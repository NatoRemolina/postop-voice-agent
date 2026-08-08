# Especificación del frontend (Lovable)

El frontend de demo se construye en Lovable y consume la API REST de este backend.
El backend ya acepta CORS desde cualquier origen. Las páginas integradas (`/admin`,
`/call`) quedan como respaldo funcional.

**URL del backend**: `https://PENDIENTE-URL-PUBLICA` (se define al desplegar; en
desarrollo local: `http://localhost:8600`).

---

## Prompt sugerido para Lovable

> Crea una aplicación en español con dos vistas para un agente de voz de
> seguimiento postoperatorio: (1) "Consola de administración" y (2) "Llamada".
> Usa la API REST descrita abajo con la URL base configurable en una constante.
> Estilo: limpio, clínico, profesional; badges de criticidad verde/amarillo/rojo.
> Para la vista de Llamada instala el paquete npm `@elevenlabs/client` y usa
> `Conversation.startSession({ signedUrl, ...callbacks })`.

## Contrato de la API

### Conocimiento vivo

- `GET {BASE}/api/documents` → `{documents: [{doc_id, source, scenario, uploaded, n_chunks}], count}`
  - Todo documento listado está "procesado y disponible" (mostrar chip verde).
- `POST {BASE}/api/documents?scenario=<texto>` — multipart, campo `file` (PDF).
  - 200: `{status: "procesado y disponible", doc_id, source, scenario, n_chunks}`
  - 4xx/422: `{detail: <mensaje en español>}` (mostrar tal cual).
- `DELETE {BASE}/api/documents/{doc_id}` → `{status: "eliminado", doc_id, chunks_removed}`

### Llamadas y alertas

- `GET {BASE}/api/calls` → `{calls: [...], alerts: [...]}`
  - `calls[]`: `{conversation_id, ts, started_ts, ended_ts, n_turnos, paciente,
    criticidad_final: "verde"|"amarillo"|"rojo", escalar: bool, red_flags: [str],
    resumen_narrativo, referencias: [{source, scenario, page}], proximos_pasos,
    tokens: {input, output}, generated_by}`
  - `alerts[]`: `{conversation_id, ts, criticidad, red_flags, paciente, resumen_narrativo}`
    — si no está vacío, mostrar banner de alertas activas.
- `GET {BASE}/api/calls/{conversation_id}` → `{summary, turns: [{user_text,
  spoken_text, control, rag_sources: [{source, scenario, page, score}], ...}]}`
  — vista de detalle con transcript y referencias por turno.
- `POST {BASE}/api/calls/{conversation_id}/summarize` → genera y devuelve el
  resumen. Llamarlo al terminar cada llamada (en `onDisconnect`).

### Voz (vista Llamada)

- `GET {BASE}/api/voice/signed-url` → `{signed_url}`
  - 503/502: `{detail}` — mostrar el mensaje.
- Flujo: pedir permiso de micrófono → obtener `signed_url` →
  `Conversation.startSession({signedUrl, onConnect, onDisconnect, onError,
  onModeChange, onMessage})`.
  - `onModeChange({mode})`: `"speaking"` → "El agente habla"; si no → "El agente escucha".
  - `onMessage({message, source})`: `source === "ai"` → burbuja del agente
    ("Clara"); `"user"` → burbuja del paciente. Transcript en vivo.
  - Botón Colgar → `conversation.endSession()`; luego `POST .../summarize` con
    `conversation.getId()`.

### Métricas (opcional, para una vista "Observabilidad")

- `GET {BASE}/api/metrics` → `{n_turns, n_calls, latency: {first_token_ms:
  {p50,p95,mean}, total_ms: {...}}, tokens_per_turn, tokens_per_call,
  model_calls_per_turn, rag_queries_per_call, cost_per_call_usd: {p50, mean,
  assumptions: [str]}}`
