# Arquitectura del agente model-driven (LangGraph)

Este documento describe el agente que razona detrás de `POST /v1/chat/completions`
—el "Custom LLM" que ElevenLabs invoca en cada turno de la llamada de voz—, construido
sobre LangChain/LangGraph. La superficie de voz (ElevenLabs: STT, turn-taking, TTS) no
cambia; todo lo de este documento ocurre **detrás** de ese contrato.

**Interruptor de seguridad**: `settings.agentic_rag_enabled` (en `app/config.py`,
`AGENTIC_RAG_ENABLED` en `.env`). En `True` (default) corre el agente descrito abajo;
en `False`, el sistema vuelve exactamente al pipeline fijo original (RAG pre-inyectado +
Gemini/Groq directo, `app/agent/llm.py`) sin ningún otro cambio de comportamiento. Es el
mecanismo de "si el grafo entero fallara, se degrada al pipeline que ya funciona".

## 1. Flujo por turno de voz

```mermaid
sequenceDiagram
    participant P as Paciente
    participant EL as ElevenLabs (voz)
    participant CH as chat.py (/v1/chat/completions)
    participant AG as create_agent (LangGraph)
    participant T as Herramientas
    participant LLM as Gemini 3.6 Flash / Groq Llama

    P->>EL: habla
    EL->>CH: POST SSE, historial completo
    CH->>AG: stream_agentic_response(history, patient_context, scenario)
    AG->>LLM: mensajes + tools disponibles
    LLM-->>AG: tool_call: buscar_conocimiento_clinico("...")
    AG->>T: buscar_conocimiento_clinico
    T-->>AG: pasajes con fuente + página
    AG->>LLM: resultado de la tool
    LLM-->>AG: tool_call: registrar_evaluacion(...)
    AG->>T: registrar_evaluacion (+ ensemble con modelo ML)
    LLM-->>AG: texto hablado (streaming)
    AG-->>CH: chunks de texto
    CH-->>EL: SSE (texto)
    EL-->>P: TTS
    AG-->>CH: bloque &lt;control&gt; sintetizado desde turn_state
    CH->>CH: separa control del habla; nunca llega al TTS
    CH->>CH: registra turno en turns.jsonl
```

### 1.1 Por qué "model-driven" y no un pipeline fijo

El modelo decide qué herramienta usar y cuándo — no hay un paso de "RAG obligatorio en
cada turno" cableado a mano. Un turno trivial ("gracias", "listo") no dispara búsqueda;
un turno con una duda clínica sí. La ingeniería está en las herramientas que se le
entregan y en los guardrails que acotan sus decisiones:

- `buscar_conocimiento_clinico(consulta)` — retrieval híbrido completo (§2), con su
  propio sub-pipeline de ingeniería que el modelo no ve, solo usa.
- `registrar_evaluacion(sintomas, criticidad, red_flags, dimensiones_cubiertas)` —
  aplica el ensemble con el modelo de ML de triaje (§3): solo puede **subir** la
  criticidad reportada por el LLM, nunca bajarla.
- `escalar_a_equipo_clinico(motivo, red_flags)` — **exenta del presupuesto de
  herramientas a propósito** (bug real encontrado en pruebas: quedaba silenciada si
  competía por presupuesto con otras 3 tools en el mismo turno; ver §5).
- `finalizar_llamada(resumen_corto)` — señaliza cierre natural de la conversación.

**Guardrails deterministas** (fuera del alcance de decisión del modelo):
1. Presupuesto de 2 llamadas a herramienta por turno (excepto escalamiento, ver arriba)
   — protege el presupuesto de latencia de voz.
2. `_control_payload` en `app/graph/agent.py`: si la criticidad final es "rojo",
   `escalar` se fuerza a `true` pase lo que pase con el estado crudo del turno — nunca
   se sub-reporta silenciosamente un caso rojo.
3. El bloque `<control>` es sintetizado por código a partir de `turn_state`, no escrito
   libremente por el modelo en texto — reduce el riesgo de que un formato inválido
   rompa el parseo aguas abajo.

## 2. Retrieval híbrido (`app/graph/retrieval.py`)

```
consulta del paciente (coloquial, regional)
  → REESCRITURA (Groq llama-3.1-8b-instant, structured output)
      "me siento destemplada" → "fiebre escalofríos malestar postoperatorio"
      + hasta 2 sub-consultas
  → BÚSQUEDA HÍBRIDA por cada consulta (máx 3):
      · DENSA: Chroma vía langchain-chroma, mismo cliente/colección que usa
        app/rag/store.py — nunca se duplica el índice
      · LÉXICA: BM25Retriever reconstruido FRESCO en cada llamada desde
        collection.get() (nunca cacheado) — refleja altas/bajas en vivo de la
        consola (compuerta G5) sin invalidación de caché
      · fusión: EnsembleRetriever (pesos 0.6 denso / 0.4 léxico)
      · filtro: {"$or": [{"scenario": <procedimiento del paciente>},
                          {"uploaded": true}]}  — documentos subidos en vivo
        siempre elegibles, sin importar el escenario detectado
  → CALIFICACIÓN DE RELEVANCIA (Groq 8B, UNA llamada batch, máx 10 candidatos
    por llamada — más que eso degeneraba el modelo en pruebas reales, ver §5)
  → si CERO calificaron relevante: UN reintento con consulta original,
    sin filtro de escenario (búsqueda amplia)
  → top-k deduplicado por (fuente, página, texto[:80])
```

Devuelve `{"text", "source", "scenario", "page", "score"}` por pasaje — la unidad de
citación que sostiene la trazabilidad (fuente + página) exigida por el criterio de RAG
de la rúbrica.

## 3. Modelo de ML de triaje (ensemble, `app/agent/triage_model.py`)

Interfaz lista (`predict(features: dict) -> dict | None`), con degradación explícita:
sin `data/triage_model.pkl` presente, devuelve `None` de inmediato y el LLM decide
solo — es el estado actual (el entrenamiento del modelo es una tarea separada). Cuando
exista el `.pkl`, el ensemble en `registrar_evaluacion` compara la criticidad del LLM
contra la del modelo y usa la **más alta** de las dos — nunca la más baja, coherente con
la asimetría clínica que exige la rúbrica (el falso negativo es la falla catastrófica).

**Nota de entorno**: `pandas`/`scikit-learn` no están instalados en el `.venv` de
runtime todavía (se instalarán junto con el `.pkl` real); hasta entonces `predict()`
degrada a `None` con gracia, sin excepciones.

## 4. Resumen post-llamada

**Activo en producción**: `app/agent/summary.py`. Al cerrar la llamada
(`POST /api/calls/{id}/summarize` o el webhook de ElevenLabs) genera el resumen
estructurado con Gemini y, si el modelo no está disponible, cae a un resumen
determinista construido desde los bloques de control de cada turno — nunca se pierde una
llamada sin cierre. Al persistirse un caso escalado, escribe además la alerta en
`data/alerts.jsonl`.

**Escrito pero NO conectado**: `app/agent/deep_summary.py` es un agente verificador
post-llamada (herramientas `leer_turnos`, `buscar_en_corpus` para confirmar que las citas
dichas están realmente sustentadas, y `consultar_triaje`). Está implementado con el mismo
contrato de salida que `summary.py`, pero en pruebas su ciclo de herramientas supera los
200 s contra los proveedores gratuitos, así que **no se cableó a ninguna ruta**: activarlo
sin resolver esa latencia degradaría el cierre de llamada. Queda como trabajo pendiente
documentado, no como funcionalidad en uso.

## 5. Fallos reales encontrados y corregidos en esta sesión

Documentado porque es la evidencia más honesta de qué tan sólida quedó la integración
(y es material directo para la Pregunta 2 del video — decisión técnica, riesgos, qué se
descartó):

- **`.with_fallbacks()` de LangChain no es confiable dentro del ciclo de tool-calling
  de `create_agent`.** Verificado en vivo, reproducido de forma independiente: en una
  fracción de las corridas, la excepción de Gemini se escapaba sin que el fallback a
  Groq se activara, dejando al paciente en silencio total. Se reemplazó por un
  reintento manual de **turno completo** (Gemini → si falla antes de decir algo, se
  reintenta el turno entero desde cero con Groq — nunca a medio turno, porque una vez
  que se le habló algo al paciente no se puede "retractar"), replicando el patrón ya
  probado de `app/agent/llm.py`. Verificado con pruebas repetidas: reintento
  determinista, y cuando ambos proveedores fallan de verdad, la excepción ahora se
  propaga correctamente hasta `chat.py`, que sí tiene el mensaje de disculpa hablado
  probado en producción (antes quedaba absorbida en silencio).
- **Presupuesto de herramientas compartido silenciaba escalamientos.** El contador de
  presupuesto (2 tool calls/turno) era compartido entre las 4 herramientas; una llamada
  real a `escalar_a_equipo_clinico` podía perder la carrera contra el presupuesto y
  devolver "presupuesto agotado" sin aplicar el escalamiento. Corregido eximiendo a esa
  herramienta del presupuesto (es la acción de seguridad clínica del turno, nunca puede
  perderse) — más el guardrail redundante de `_control_payload` (rojo ⇒ escalar=true
  siempre, sin importar el estado crudo).
- **El modelo calificador (Groq 8B) degeneraba con lotes grandes.** Calificar más de
  ~10 pasajes en una sola llamada structured-output producía salidas corruptas y
  latencias de 20-45s. Se acotó el lote a 10 — corridas limpias bajaron a ~2.8s
  end-to-end.

## 6. Patrones de RAG aplicados y descartados

| Patrón | Aplicado | Razón |
|---|---|---|
| Query rewriting | Sí | El paciente habla en regionalismos ("destemplada"); el corpus está en lenguaje clínico |
| Metadata filtering | Sí | Filtro por escenario/procedimiento — evita mezclar guías de otro procedimiento |
| Hybrid search (denso+BM25) | Sí | Términos exactos (nombres de fármacos, siglas) que el embedding puede perder |
| Relevance grading / self-correction | Sí, acotado a 1 reintento | Presupuesto de latencia de voz no permite loops largos |
| Citation-aware generation | Sí | Cada pasaje trae fuente+página; requisito directo de la rúbrica |
| Guarded generation | Sí | System prompt + guardrails deterministas anti-inyección y anti-alucinación |
| Reranker cross-encoder | Descartado | +300-500ms en CPU, fuera del presupuesto de latencia de una llamada de voz |
| Loop agéntico sin límite de iteraciones | Descartado | Cuota diaria de Gemini (20 req/día) y presupuesto de latencia lo prohíben |

## 7. Presupuesto de cuota y latencia por proveedor

- **Gemini 3.6 Flash**: 20 solicitudes/día (nivel gratuito) — reservado para la
  generación final hablada, 1 invocación por turno (más si hay reintento de fallback).
- **Groq `llama-3.1-8b-instant`**: cuota generosa — reescritura + calificación, hasta
  2 invocaciones por turno además de las de retrieval.
- **Groq `llama-3.3-70b-versatile`**: respaldo de la generación final si Gemini falla;
  también tiene tope diario (100k tokens/día en el tier gratuito, se agota con pruebas
  intensivas — observado en esta misma sesión).
- Árbol de degradación: Groq 8B falla en reescritura → se usa la consulta original.
  Groq 8B falla en calificación → se consideran todos los candidatos relevantes.
  Chroma vacío → retrieval denso devuelve lista vacía, BM25 se omite, no rompe.
  Gemini falla antes de hablar → reintento completo con Groq 70B. Ambos fallan →
  excepción propagada a `chat.py`, que responde con el mensaje de disculpa hablado.

## 8. Prácticas de AI engineering aplicadas

Model-driven tool use (el modelo decide, no un pipeline fijo) · structured outputs
schema-validados en cada frontera (reescritura, calificación, evaluación clínica) ·
guardrails deterministas sobre decisiones del modelo (presupuesto, ensemble
solo-sube, escalamiento exento) · prompts versionados como código
(`app/graph/agent_prompt.py`) · evals offline contra el dataset etiquetado del reto
(ver `data/eval/sample_cases.json` y la evaluación de la Etapa E3) · observabilidad
por turno (telemetría de herramientas invocadas, camino tomado, fuentes citadas en
`turns.jsonl`) · fallbacks explícitos y probados en cada dependencia externa (dos
proveedores de LLM, degradación de retrieval, degradación del modelo de ML) ·
separación clara de responsabilidades: voz (ElevenLabs) / razonamiento (LangGraph +
Gemini/Groq) / conocimiento (Chroma+BM25) / decisión (ensemble LLM+ML) · gobernanza de
datos como capa aparte (pendiente de implementar, documentada en el plan del proyecto).

## 9. Estado de verificación (honesto)

Verificado con pruebas reales en esta sesión: retrieval híbrido contra el corpus real
(3.249 chunks), herramientas del agente invocadas de extremo a extremo, reintento
Gemini→Groq determinista (6/6 corridas), degradación ante fallo total de ambos
proveedores (excepción propagada correctamente), compatibilidad exacta del bloque
`<control>` con el parseo de `chat.py`. **Pendiente de una llamada de voz real de
extremo a extremo** con el agente nuevo activo (el smoke testing se hizo por texto,
directo contra `stream_agentic_response`, para no gastar la cuota ya escasa de Gemini
en pruebas repetidas) — es el último paso antes de dar la migración por cerrada.
