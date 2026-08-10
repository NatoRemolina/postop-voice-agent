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
        EL["Agente 'Clara'<br/>STT · detección de turnos · interrupciones · TTS<br/>voz configurable (41 voces es, selector de acento)"]
    end

    subgraph backend["Backend — FastAPI en AWS EC2 (HTTPS)"]
        CHAT["POST /v1/chat/completions — SSE<br/>contrato Custom LLM de ElevenLabs<br/>(app/routers/chat.py)"]

        subgraph agente["Agente por turno (app/graph/)"]
            PRE["Prefetch RAG (modo rápido)<br/>búsqueda híbrida sin ayudantes LLM<br/>(agent.py:_prefetch_context)"]
            LLM["Una invocación del modelo por turno<br/>cascada: Gemini 3.6 Flash → Llama 3.3 70B → Llama 3.1 8B<br/>circuit breakers por cuota (agent.py + circuit_breaker.py)"]
            TOOL["Herramienta opcional:<br/>buscar_conocimiento_clinico<br/>(tools.py — modo completo con reescritura+calificación)"]
            CTRL["Bloque &lt;control&gt; al final del texto:<br/>criticidad · red flags · síntomas estructurados<br/>— se separa, NUNCA llega al TTS"]
            ML["Ensemble de triaje post-streaming<br/>Random Forest (data/triage_model.pkl)<br/>solo puede SUBIR la criticidad (agent.py)"]
        end

        subgraph conocimiento["Conocimiento vivo"]
            CHROMA[("ChromaDB local<br/>3.249 chunks, fuente+página<br/>(app/rag/store.py)")]
            BM25["Índice BM25 léxico<br/>reconstruido en cada búsqueda<br/>(app/graph/retrieval.py)"]
            DOCS["/api/documents<br/>subir → indexado en caliente<br/>eliminar → olvidado<br/>(app/routers/documents.py)"]
        end

        subgraph registro["Trazabilidad y decisión"]
            TURNS[("data/turns.jsonl<br/>por turno: fuentes citadas, latencia,<br/>tokens, modelo real usado, decisión")]
            ALERTS[("data/alerts.jsonl<br/>alertas persistentes al escalar")]
            SUMM["Resumen estructurado por llamada<br/>(app/agent/summary.py + deep_summary.py)"]
        end

        ADMIN["Consola /admin + API REST para Lovable<br/>documentos · llamadas · alertas · métricas<br/>(app/routers/web.py · CORS abierto)"]
        METRICS["GET /api/metrics<br/>P50/P95, tokens, costo<br/>(app/metrics.py)"]
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
    LLM --> CTRL --> ML
    ML --> TURNS
    CTRL --> ALERTS
    TURNS --> SUMM
    DOCS --> CHROMA
    ADMIN --> DOCS
    ADMIN --> SUMM
    WH --> PKL --> ML
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

    EVAL --> ENS["Ensemble: max(criticidad LLM,<br/>criticidad Random Forest)<br/>el ML nunca la baja"]
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

    GUARD["Redes de seguridad deterministas<br/>(fuera del alcance del modelo):<br/>rojo ⇒ escalar siempre ·<br/>escalar ⇒ nunca queda en verde ·<br/>sin dosis ni medicamentos inventados ·<br/>inyección de prompt ignorada"] -.-> EVAL
    GUARD -.-> NIVEL
```

## 3. Cascada de modelos y degradación (todo en nivel gratuito)

```mermaid
flowchart LR
    T["Turno"] --> G{"¿Gemini 3.6 Flash<br/>disponible?"}
    G -->|"sí"| OK1["Genera<br/>(modelo primario)"]
    G -->|"429 / error<br/>(circuit breaker 10 min)"| L70{"¿Llama 3.3 70B<br/>(Groq) disponible?"}
    L70 -->|"sí"| OK2["Genera<br/>(respaldo 1)"]
    L70 -->|"cuota agotada"| L8{"¿Llama 3.1 8B<br/>(Groq) disponible?"}
    L8 -->|"sí"| OK3["Genera<br/>(respaldo 2)"]
    L8 -->|"no"| DISC["Mensaje de disculpa hablado<br/>(nunca silencio total)"]
    OK1 & OK2 & OK3 --> REG["turns.jsonl registra QUÉ modelo<br/>respondió realmente cada turno"]
```
