# Especificación del frontend (Lovable)

El frontend de demo se construye en Lovable y consume la API REST de este
backend. Las páginas integradas (`/admin`, `/call`) quedan como respaldo
funcional: si el frontend externo falla, la demo sigue en pie.

**URL base del backend (en producción, ya desplegada):**

```
https://52-207-194-196.sslip.io
```

CORS está abierto a cualquier origen — **verificado** con un preflight real
desde un origen `*.lovable.app`: responde `access-control-allow-origin: *` y
permite `GET, POST, DELETE, PATCH, PUT, OPTIONS` con cabecera `content-type`.
No hay autenticación: no se envían claves desde el navegador.

Todos los contratos de abajo están **verificados contra el servidor en
producción**, no copiados del código.

---

## Prompt para Lovable

> Crea una aplicación web en español para supervisar un agente de voz de
> seguimiento postoperatorio, con tres vistas y una barra de navegación:
> **Consola**, **Llamada** y **Observabilidad**.
>
> Usa `const API = "https://52-207-194-196.sslip.io"` como constante de
> configuración al inicio. No hay autenticación.
>
> Estilo: limpio y clínico, tipo panel hospitalario. Badges de criticidad:
> verde = evolución esperada, amarillo = vigilar, rojo = escalado. Si hay
> alertas activas, un banner rojo fijo arriba. Todo el texto en español.
>
> Para la vista de Llamada instala el paquete npm `@elevenlabs/client` y usa
> `Conversation.startSession({ signedUrl, ...callbacks })`. La voz ya está
> fijada en el servidor (Marcela, acento colombiano): no ofrezcas selector.

---

## Contrato de la API (verificado en producción)

### 1. Conocimiento vivo — vista Consola

- **`GET /api/documents`** → `{documents: [...], count}`
  - `count` actual: **106**
  - Cada elemento: `{doc_id, source, scenario, uploaded, n_chunks}`
  - Ejemplo real:
    ```json
    {"doc_id": "appendicitis_acute_appendicitis_evidence_based_medicine_guideline_pdf_ad5f61d6",
     "source": "Acute Appendicitis Evidence Based Medicine Guideline.pdf",
     "scenario": "Appendicitis", "uploaded": false, "n_chunks": 15}
    ```
  - `uploaded: true` marca los subidos en vivo desde la consola (chip distinto).
  - Todo lo listado está indexado y disponible para el agente.

- **`POST /api/documents?scenario=<texto>`** — multipart, campo `file` (PDF).
  - 200: `{status: "procesado y disponible", doc_id, source, scenario, n_chunks}`
  - Error: `{detail: "<mensaje en español>"}` — mostrarlo tal cual. Caso real:
    un PDF escaneado sin capa de texto responde
    `"No se pudo extraer texto del PDF: sin capa de texto extraible (PDF escaneado?) — omitido"`.
  - Subir el mismo nombre de archivo y escenario **reemplaza** la versión
    anterior (no se duplica ni contamina).

- **`DELETE /api/documents/{doc_id}`** → `{status: "eliminado", doc_id, chunks_removed}`
  - Tras borrar, el agente deja de conocer ese contenido de inmediato.

### 2. Llamadas y alertas — vista Consola

- **`GET /api/calls`** → `{calls: [...], alerts: [...]}`

  `calls[]` (campos reales, confirmados en el servidor):
  ```
  conversation_id · ts · started_ts · ended_ts · n_turnos
  paciente · procedimiento
  criticidad_final: "verde"|"amarillo"|"rojo" · escalar: bool
  red_flags: [str] · sintomas_reportados: [str] · dimensiones_cubiertas: [str]
  resumen_narrativo · proximos_pasos
  referencias: [{source, scenario, page}]
  tokens: {input, output} · model_calls · rag_queries · generated_by
  ```

  `alerts[]`: `{conversation_id, ts, criticidad, red_flags, paciente, resumen_narrativo}`
  — una por conversación, la más reciente. Si el arreglo no está vacío, mostrar
  el banner de alertas.

- **`GET /api/calls/{conversation_id}`** → `{summary, turns: [...]}`
  - `turns[]`: `{user_text, spoken_text, control, rag_sources: [{source,
    scenario, page, score}], latency_first_token_ms, model, ...}`
  - `control` por turno: `{criticidad, confianza, red_flags, sintomas,
    escalar, fin_llamada, dimensiones_cubiertas}`
  - Vista de detalle sugerida: ficha del resumen arriba, transcript turno a
    turno abajo con la criticidad y las fuentes citadas de cada turno.

- **`POST /api/calls/{conversation_id}/summarize`** → devuelve el resumen.
  - **No es obligatorio llamarlo**: el backend ya genera el resumen solo cuando
    el agente se despide. Úsalo como botón "Regenerar resumen".

- **`DELETE /api/calls/{conversation_id}`** → `{status: "eliminado",
  conversation_id, registros_borrados: {...}}`
  - Derecho de supresión (Ley 1581 de 2012): borra turnos, resumen y alertas.
  - Ponerlo tras una confirmación explícita; es irreversible.

### 3. Pacientes (contexto de la demo)

- **`GET /api/patients`** → **arreglo plano** (ojo: no viene envuelto en un objeto)
  ```json
  [{"paciente_id": "pac_42_00000", "nombre": "Mauricio", "ciudad": "Soacha",
    "procedimiento": "Apendicectomía", "dia_postop": 1,
    "caso_id": "caso_tray_pac_42_00000_1", "edad": 34, "genero": "F"}]
  ```
  - 160 pacientes del dataset del reto. Útil para un selector de "a quién llamar".

### 4. Voz — vista Llamada

- **`GET /api/voice/signed-url`** → `{signed_url}` · errores: `{detail}`
- Flujo:
  1. `navigator.mediaDevices.getUserMedia({audio: true})` (permiso de micrófono)
  2. `GET /api/voice/signed-url`
  3. `Conversation.startSession({signedUrl, onConnect, onDisconnect, onError,
     onModeChange, onMessage})`
- Callbacks:
  - `onModeChange({mode})`: `"speaking"` → "El agente habla"; si no → "El agente escucha"
  - `onMessage({message, source})`: `source === "ai"` → burbuja de Clara;
    `"user"` → burbuja del paciente (transcript en vivo)
- Controles que debe tener la vista:
  - **Colgar** → `conversation.endSession()`
  - **Silenciar** → `conversation.setVolume({volume: 0})` / `{volume: 1}` para
    reactivar. Silencia solo la salida: el micrófono sigue abierto y la llamada
    no se corta. Deshabilitado mientras no haya llamada; se restablece al colgar.
  - **No incluir selector de voz**: está fijada en el servidor.

### 5. Observabilidad — vista Observabilidad

- **`GET /api/metrics`** →
  ```
  n_turns · n_calls
  latency: {first_token_ms: {p50,p95,mean}, total_ms: {p50,p95,mean}}
  tokens_per_turn: {input: {p50,mean}, output: {p50,mean}}
  tokens_per_call: {...}
  model_calls_per_turn · rag_queries_per_call
  cost_per_call_usd: {p50, mean, assumptions: [str]}
  ```
  - Mostrar `assumptions` como nota al pie (son los supuestos del cálculo de costo).
  - Valores reales al 10 de agosto: 218 turnos, 138 llamadas, primer token
    P50 1.412 ms, costo por llamada P50 USD 0,0065.

---

## Orden sugerido para armarlo

1. **Consola primero**: lista de documentos + subir + eliminar. Es la vista que
   demuestra el conocimiento vivo, y se puede probar de inmediato subiendo un PDF.
2. **Llamadas**: lista con badges y ficha de detalle con transcript.
3. **Llamada de voz**: es la que necesita el paquete npm y permisos del navegador.
4. **Observabilidad**: la más simple, son tarjetas con números.
