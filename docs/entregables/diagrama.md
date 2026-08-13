# Entregable 02 — Diagrama de arquitectura y flujo de decisión

Cada elemento de estos diagramas corresponde a un archivo real del repositorio
(indicado entre paréntesis) — el jurado puede tomar cualquier caja y encontrarla
en el código.

## 1. Arquitectura de la solución

```mermaid
flowchart TB
    subgraph paciente["Paciente"]
        MIC["Navegador + micrófono<br/>(app/web/templates/call.html · call.js)<br/>o frontend Lovable"]
    end

    subgraph voz["Capa de voz — ElevenLabs Agents"]
        EL["Agente 'VALAI'<br/>STT · detección de turnos · interrupciones · TTS<br/>voz Marcela (acento colombiano, eleven_flash_v2_5)<br/>pausas &lt;break/&gt; antes de preguntar"]
    end

    subgraph backend["Backend — FastAPI en AWS EC2 (HTTPS)"]
        CHAT["POST /v1/chat/completions — SSE<br/>contrato Custom LLM de ElevenLabs<br/>(app/routers/chat.py)"]

        subgraph agente["Agente por turno (app/graph/)"]
            PRE["Prefetch RAG (modo rápido)<br/>búsqueda híbrida sin ayudantes LLM<br/>(agent.py:_prefetch_context)"]
            LLM["Una invocación del modelo en el camino nominal<br/>(dos si el modelo decide usar la herramienta)<br/>cascada: Gemini 3.6 Flash → Llama 3.3 70B → Llama 3.1 8B<br/>circuit breakers por cuota (agent.py + circuit_breaker.py)"]
            TOOL["Herramienta opcional:<br/>buscar_conocimiento_clinico<br/>(tools.py — modo completo con reescritura+calificación)"]
            CTRL["Bloque &lt;control&gt; al final del texto:<br/>criticidad · red flags · síntomas estructurados<br/>— se separa, NUNCA llega al TTS<br/>(agent.py:_run_turn · chat.py)"]
            ML["Ensemble de triaje post-streaming<br/>Random Forest (data/triage_model.pkl)<br/>solo puede SUBIR la criticidad (agent.py)"]
            ACUM["Guardrail determinista de acumulación:<br/>herida alterada + apetito muy disminuido +<br/>sueño muy alterado ⇒ rojo<br/>(agent.py:_acumulacion_es_rojo)"]
        end

        subgraph conocimiento["Conocimiento vivo"]
            CHROMA[("ChromaDB local<br/>3.249 chunks, fuente+página<br/>(app/rag/store.py)")]
            BM25["Índice BM25 léxico<br/>reconstruido en cada búsqueda<br/>(app/graph/retrieval.py)"]
            DOCS["/api/documents<br/>subir → indexado en caliente<br/>eliminar → olvidado<br/>(app/routers/documents.py)"]
        end

        subgraph registro["Trazabilidad y decisión"]
            TURNS[("data/turns.jsonl<br/>por turno: fuentes citadas, latencia,<br/>tokens, modelo real usado, decisión")]
            ALERTS[("data/alerts.jsonl<br/>alertas persistentes al escalar")]
            SUMM["Resumen estructurado por llamada<br/>escribe la alerta al persistirse<br/>(app/agent/summary.py:persist_summary)"]
        end

        ADMIN["Consola /admin + API REST para Lovable<br/>(web.py: consola · documents.py · calls.py ·<br/>metrics.py · CORS en app/main.py)"]
        METRICS["GET /api/metrics<br/>P50/P95, tokens, costo<br/>(routers/metrics.py + app/metrics.py)"]
        MLF["MLflow Tracing: 1 traza por turno,<br/>sub-spans por proveedor y por búsqueda RAG<br/>(app/graph/agent.py + mlflow.langchain.autolog)<br/>opcional — timeout corto si no hay servidor"]
    end

    subgraph datos["Datos del reto"]
        WH[("data/warehouse.db<br/>ETL: 160 casos, 3.991 turnos<br/>(etl/)")]
        PKL[("Modelo ML entrenado<br/>(scripts/train_triage.py)")]
    end

    MIC <--> EL
    EL <-->|"historial completo · SSE"| CHAT
    CHAT --> PRE --> LLM
    LLM -.->|"si el contexto no alcanza"| TOOL
    TOOL --> CHROMA
    TOOL --> BM25
    PRE --> CHROMA
    PRE --> BM25
    LLM --> CTRL --> ACUM --> ML
    ML --> TURNS
    TURNS --> SUMM
    SUMM -->|"al persistir un caso escalado"| ALERTS
    DOCS --> CHROMA
    ADMIN --> DOCS
    ADMIN --> SUMM
    WH --> PKL --> ML
    PRE -.->|"traza"| MLF
    LLM -.->|"traza"| MLF
```

## 2. Flujo de decisión del agente

```mermaid
flowchart TB
    START(["Turno del paciente"]) --> RAG["Recuperar contexto clínico<br/>del corpus (con fuente y página)"]
    RAG --> HABLA["Responder YA por voz<br/>(1-3 frases, una pregunta)"]
    HABLA --> DIM{"¿Cubiertas las 6 dimensiones?<br/>dolor · fiebre · movilidad ·<br/>herida · apetito · sueño"}
    DIM -->|"No"| INDAGA["Indagar la siguiente,<br/>adaptándose a lo que cuente"]
    DIM -->|"Ambigüedad<br/>('me siento destemplada')"| ACLARA["PREGUNTAR antes de decidir<br/>— nunca asumir"]
    INDAGA --> EVAL
    ACLARA --> EVAL
    DIM -->|"Sí"| EVAL["Evaluar criticidad en el bloque<br/>de control (no hablado)"]

    EVAL --> ACUM2["Guardrail de acumulación (código):<br/>herida alterada + apetito muy disminuido +<br/>sueño muy alterado ⇒ rojo<br/>calibrado: 12/12 rojos, 0 verdes del dataset"]
    ACUM2 --> ENS["Ensemble: max(criticidad LLM,<br/>criticidad Random Forest)<br/>el ML nunca la baja"]
    ENS --> NIVEL{"¿Nivel?"}

    NIVEL -->|"VERDE<br/>evolución esperada"| VERDE["Cerrar con recomendaciones<br/>básicas de las guías"]
    NIVEL -->|"AMARILLO<br/>algo que vigilar"| AMAR["Explicar qué vigilar;<br/>el equipo clínico revisará el reporte"]
    NIVEL -->|"ROJO<br/>signo de alarma"| ROJO["Avisar con calma: el caso pasa YA<br/>al equipo clínico; si hay riesgo vital,<br/>indicar urgencias"]

    ROJO --> ALERTA[("Alerta persistente<br/>data/alerts.jsonl")]
    NIVEL -.->|"duda entre dos niveles"| ALTO["Elegir SIEMPRE el más alto<br/>(el falso negativo es la falla<br/>catastrófica — asimetría clínica)"]
    ALTO --> NIVEL

    VERDE --> FIN["Resumen estructurado de la llamada:<br/>paciente · síntomas · decisión ·<br/>referencias citadas · próximos pasos"]
    AMAR --> FIN
    ALERTA --> FIN

    GUARD["Redes deterministas EN CÓDIGO<br/>(fuera del alcance del modelo):<br/>rojo ⇒ escalar siempre ·<br/>escalar ⇒ nunca queda en verde<br/>(agent.py:_control_payload)"] -.-> NIVEL
    POLIT["Reglas del system prompt<br/>(agent_prompt.py):<br/>sin dosis ni medicamentos inventados ·<br/>inyección de prompt ignorada ·<br/>anti-minimización del paciente"] -.-> EVAL
```

## 3. Cascada de modelos y degradación (todo en nivel gratuito)

```mermaid
flowchart LR
    T["Turno"] --> G{"¿Gemini 3.6 Flash<br/>disponible?"}
    G -->|"sí"| OK1["Genera<br/>(modelo primario)"]
    G -->|"429 / error → circuit breaker<br/>10 min (cuota diaria) · 65 s (por minuto)"| L70{"¿Llama 3.3 70B<br/>(Groq) disponible?"}
    L70 -->|"sí"| OK2["Genera<br/>(respaldo 1)"]
    L70 -->|"cuota agotada"| L8{"¿Llama 3.1 8B<br/>(Groq) disponible?"}
    L8 -->|"sí"| OK3["Genera<br/>(respaldo 2)"]
    L8 -->|"no"| DISC["Si falla ANTES de hablar: disculpa hablada<br/>Si falla a mitad de turno: corte con gracia<br/>(no se retracta lo ya dicho)"]
    OK1 & OK2 & OK3 --> REG["turns.jsonl registra QUÉ modelo<br/>respondió realmente cada turno"]
```
