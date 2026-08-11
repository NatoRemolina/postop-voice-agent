# Clara — Agente de voz para seguimiento postoperatorio

**Tech Sphere Challenge 2026.** Un agente de voz en español que llama al paciente
recién operado, conversa en su lenguaje (regionalismos colombianos incluidos),
fundamenta cada respuesta clínica en un corpus de guías reales con cita de fuente
y página, decide cuándo escalar a un humano, y deja un resumen estructurado de
cada llamada. El conocimiento es vivo: se sube un documento por la consola y el
agente lo aprende; se elimina y lo olvida.

- **Demo en vivo**: https://52-207-194-196.sslip.io/call (llamada de voz) ·
  https://52-207-194-196.sslip.io/admin (consola de administración)
- **Modelo razonador**: Google **Gemini 3.6 Flash** (nivel gratuito), con
  respaldo automático en **Meta Llama 3.3 70B y 3.1 8B vía Groq** (nivel
  gratuito) — todas familias permitidas por el reto. Ver la declaración
  completa en [docs/entregables/informe-final.md](docs/entregables/informe-final.md).
- **Diagrama de arquitectura y flujo de decisión**:
  [docs/entregables/diagrama.md](docs/entregables/diagrama.md)

---

## Levantamiento en ≤15 minutos

Requisitos: Python 3.12+ y `git`. Sin Docker, sin base de datos externa, sin
cuentas nuevas: todo el estado (vector store, warehouse, modelo de ML) viene
versionado o se regenera con un script.

```bash
# 1. Clonar e instalar (~3 min)
git clone https://github.com/NatoRemolina/postop-voice-agent.git
cd postop-voice-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Credenciales (~1 min)
cp .env.example .env
# editar .env: GEMINI_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, GROQ_API_KEY

# 3. Indexar el corpus clínico (~2 min, una sola vez)
.venv/bin/python -m scripts.ingest_corpus

# 4. Arrancar
.venv/bin/uvicorn app.main:app --port 8600
```

**Sobre las credenciales**: las claves de esta entrega se envían junto con el
repositorio en el formulario del reto. Si no se tienen a mano, el sistema
**arranca igual con valores cualquiera**: la consola `/admin`, el corpus, las
métricas y todas las APIs son navegables; solo la conversación con el modelo
responde "tuve un problema técnico" (es la degradación diseñada ante un
proveedor no disponible, no una falla del levantamiento). Las claves propias se
crean gratis en Google AI Studio (Gemini), console.groq.com (Llama) y
elevenlabs.io (voz).

**Notas del levantamiento** (medidas en un clon limpio: **2 min 22 s** en total):
- La instalación descarga ~150-200 MB de dependencias; con caché de pip fría y
  red lenta puede tardar bastante más que los ~50 s medidos aquí.
- Si el puerto 8600 está ocupado, usar `--port <otro>` y sustituirlo en las URLs.
- Probado con Python 3.12 y 3.14.

Abrir `http://localhost:8600/admin` (consola) y `http://localhost:8600/call`
(llamada de voz). **Nota sobre la voz en local**: el agente de ElevenLabs invoca
al backend por HTTPS público; para probar la voz contra una instancia local hay
que exponerla (p. ej. `cloudflared tunnel --url http://localhost:8600`) y
actualizar la URL del Custom LLM del agente de ElevenLabs. Por eso la vía
recomendada para evaluar es la **instancia ya desplegada** (arriba), que está
apuntada de forma permanente.

### Verificación rápida

```bash
curl -s http://localhost:8600/health                 # → {"status":"ok"}
curl -s http://localhost:8600/api/documents | head   # → corpus indexado (106 docs, ver nota)
```

### Observabilidad con MLflow Tracing (opcional, no cuenta en los 15 minutos)

El backend intenta enviar trazas (una por turno, con sub-spans por proveedor
de modelo y por búsqueda RAG) a un `mlflow server` local. **Si ese servidor no
está corriendo, el sistema arranca y responde exactamente igual** — el intento
falla en ~2 s y queda solo un WARNING en el log; nunca bloquea un turno de voz
(verificado: una conexión colgada al tracking server puede tardar más de 60 s
con la configuración por defecto, así que se fuerza un timeout corto vía
variables de entorno y, además, la inicialización corre en un hilo con un
límite duro de 5 s). Para verlo en acción:

```bash
python3 -m venv .venv-mlflow-ui
.venv-mlflow-ui/bin/pip install -r requirements-mlflow-ui.txt
.venv-mlflow-ui/bin/mlflow server --backend-store-uri sqlite:///mlflow_data/mlflow.db \
    --host 127.0.0.1 --port 5605
```

Con eso corriendo (antes o después de arrancar el backend, en cualquier
orden), abrir `http://localhost:5605` muestra cada llamada como una traza
navegable: el turno completo, qué proveedor respondió (Gemini o el respaldo
Groq), las búsquedas RAG con sus resultados, y la criticidad/escalamiento
decididos. Va en un **venv aparte** (`requirements-mlflow-ui.txt`) porque el
paquete `mlflow` completo fija `pandas<3`, y el proyecto usa `pandas==3.0.5`
para el ETL/EDA — el backend en sí solo instala el cliente ligero
(`mlflow-tracing`, ya incluido en `requirements.txt`), que habla con este
servidor por HTTP y no tiene ese conflicto.

---

## Arquitectura (resumen)

```
Paciente (navegador, micrófono)
   └─ ElevenLabs Agents ─ STT · turn-taking · TTS  (la voz)
        └─ POST /v1/chat/completions (SSE)          (este backend en FastAPI)
             ├─ Prefetch RAG: búsqueda híbrida (ChromaDB denso + BM25 léxico)
             │    filtrada por procedimiento, con fuente y página por pasaje
             ├─ Agente LangGraph (una invocación por turno):
             │    Gemini 3.6 Flash → Llama 3.3 70B → Llama 3.1 8B (cascada con
             │    circuit breakers por cuota; herramienta de búsqueda adicional
             │    disponible si el contexto pre-cargado no alcanza)
             ├─ Bloque <control>: criticidad, red flags, síntomas estructurados
             │    (nunca llega al TTS; se separa del texto hablado)
             ├─ Ensemble de triaje: Random Forest entrenado con los 160 casos
             │    etiquetados del reto — solo puede SUBIR la criticidad
             └─ Registro por turno: latencia, tokens, fuentes citadas, decisión
Consola /admin ── conocimiento vivo: subir PDF → indexado en caliente → eliminar → olvidado
Resumen por llamada ── paciente, síntomas, decisión, referencias, próximos pasos
```

Detalle completo: [docs/arquitectura/agentic-rag.md](docs/arquitectura/agentic-rag.md) ·
[docs/arquitectura/etl.md](docs/arquitectura/etl.md) ·
[docs/analisis/dataset-eda.md](docs/analisis/dataset-eda.md) ·
[docs/analisis/modelo-triaje.md](docs/analisis/modelo-triaje.md) ·
[docs/gobernanza-datos.md](docs/gobernanza-datos.md)

---

## Métricas (obligatorias — §5 de la rúbrica)

Calculadas sobre los registros reales de `data/turns.jsonl` del servidor
desplegado (**218 turnos acumulados** al 10 de agosto, incluidas las pruebas de
desarrollo y evaluación). **Verificables en vivo por el jurado en cualquier
momento**, sin credenciales:

```bash
curl -s https://52-207-194-196.sslip.io/api/metrics
```

Ese endpoint las recalcula sobre el log real, así que lo que aparece abajo
coincide por construcción con lo que el jurado vea en la sesión.

### Latencia (medida en el servidor: de recibir la petición de ElevenLabs al primer token / respuesta completa)

| Métrica | P50 | P95 | Media |
|---|---|---|---|
| Primer token | **1.412 ms** | 5.458 ms | 2.260 ms |
| Respuesta completa | 1.999 ms | 5.936 ms | 2.879 ms |

**Cómo leer estos números, con honestidad**: el histórico acumula todas las
iteraciones de desarrollo, incluida una versión intermedia que ejecutaba
herramientas *antes* de hablar (4–7 s por turno) y las corridas del arnés de
evaluación con la cuota gratuita agotada, que disparan la cola alta. Medido solo
sobre los **50 turnos más recientes** —ya con el diseño final— el primer token
baja a **P50 992 ms / P95 3.601 ms**. La latencia que percibe el paciente añade
el ASR y el TTS de ElevenLabs, fuera de nuestra medición del servidor.

### Consumo

| Métrica | P50 | Media |
|---|---|---|
| Tokens de entrada por turno | 3.161 | 3.725 |
| Tokens de salida por turno | 114 | 120 |
| Invocaciones al modelo por turno | — | 6,93* |
| Consultas RAG por llamada | — | 1,58 |

*\*El generador se invoca **una vez por turno** en el camino nominal. La media
incluye los reintentos completos de la cascada cuando un proveedor agota su
cuota gratuita (23 de 218 turnos usaron respaldo) y los ayudantes de reescritura
y calificación de pasajes, que corren fuera del camino crítico de la voz.*

**Qué modelo respondió realmente** (registrado turno a turno, no declarado):
`gemini-3.6-flash` 205 turnos · `llama-3.3-70b-versatile` 8 · `llama-3.1-8b-instant` 5.

### Costo estimado por llamada

| Métrica | Valor |
|---|---|
| Costo por llamada (P50) | **USD 0,0065** |
| Costo por llamada (media) | USD 0,0102 |

Supuestos: precios de lista de `gemini-3.6-flash` (USD 1,50 entrada / USD 7,50
salida por millón de tokens); el reto corre en el nivel gratuito y el costo se
extrapola a precios de producción; no incluye la plataforma de voz de ElevenLabs.

---

## Estructura del repositorio

| Ruta | Qué es |
|---|---|
| `app/` | Backend FastAPI: endpoint custom-LLM (SSE), agente LangGraph, RAG híbrido, consola, llamadas, métricas, privacidad |
| `app/graph/` | Agente model-driven: prompt, herramientas, retrieval híbrido, cascada de modelos |
| `etl/` + `data/warehouse.db` | ETL del dataset del reto → warehouse SQLite (160 casos, 3.991 turnos) |
| `data/triage_model.pkl` | Modelo de triaje (Random Forest) entrenado con los casos etiquetados |
| `scripts/` | Ingesta del corpus, ETL, entrenamiento del modelo, reporte de métricas, EDA |
| `dataset/` | Los datos del reto tal como se entregaron. El corpus trae **107 PDFs**; uno de `Appendicitis/` es un escaneo sin capa de texto extraíble (el propio reto lo advierte) y la ingesta lo omite registrándolo en el log → **106 documentos indexados** |
| `docs/` | Arquitectura, análisis, gobernanza de datos y entregables |

## Licencia

MIT (ver [LICENSE](LICENSE)). Los PDFs de `dataset/textos/` conservan los
derechos de sus autores y se incluyen solo como material del reto. Los datos
son sintéticos y no tienen validez clínica.
