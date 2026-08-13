# Gobernanza de datos y marco legal

Este documento cubre dos capas, tal como las trata el reto: el **marco legal**
aplicable a un agente de este tipo en producción real, y lo que el sistema
**implementa hoy** — con la distinción explícita entre ambas, porque el
dataset del reto es sintético y no representa datos reales de pacientes.

## 1. Marco legal (Colombia)

Un agente de seguimiento postoperatorio real, hablando con pacientes reales,
procesa **datos sensibles** bajo la legislación colombiana:

- **Ley 1581 de 2012** (Habeas Data) — art. 5 y 6: los datos de salud son
  categoría de "datos sensibles". Su tratamiento requiere **autorización
  previa, expresa e informada** del titular; no puede inferirse ni presumirse.
- **Decreto 1377 de 2013** — reglamenta la ley: obliga a informar al titular
  la finalidad del tratamiento, quién lo realiza, y sus derechos (conocer,
  actualizar, rectificar y **suprimir** el dato, y revocar la autorización).

### Cómo se traduce a una llamada de voz con IA

- **Consentimiento y transparencia**: el paciente debe saber, desde el primer
  segundo, que habla con un agente asistido por IA y para qué se usa la
  información. El `first_message` del agente "VALAI" ya se presenta como el
  programa de seguimiento de la clínica; en producción real se agregaría una
  línea explícita de consentimiento grabado antes de indagar síntomas.
- **Finalidad**: los datos se usan única y exclusivamente para el seguimiento
  postoperatorio de ese paciente — no para publicidad, no para venderlos a
  terceros, no para entrenar modelos con datos reales sin autorización nueva.
- **Minimización**: no se pide ni se expone más dato del necesario para esa
  finalidad (ver §2).
- **Circulación restringida**: qué sale del sistema hacia terceros y qué no.

  | Dato | ¿Viaja a un tercero? | Tercero |
  |---|---|---|
  | Audio de la voz del paciente | Sí | ElevenLabs (STT/TTS) |
  | Texto de la conversación | Sí | Google (Gemini) / Groq (Llama), como parte del razonamiento del modelo |
  | Nombre mencionado en la llamada | Sí, si el paciente lo dice | Los mismos proveedores de modelo, como parte del texto |
  | Cédula, dirección exacta | **No** | Nunca se envía a los proveedores de modelo ni se expone en las APIs de listado (ver §2) |

- **Seguridad**: las claves de los proveedores viven en `.env` (nunca en el
  repositorio, ver `.gitignore`); el servidor corre sobre HTTPS.
- **Retención y supresión**: el titular tiene derecho a pedir que se borre su
  información. Implementado como `DELETE /api/calls/{conversation_id}` (§3).

### El dataset de este reto es sintético

Ningún nombre, cédula, dirección o EPS del dataset (`dataset/perfiles_pacientes_co.xlsx`)
corresponde a una persona real — lo declara el propio repositorio base del
reto. Este documento describe el marco que **aplicaría en producción real**;
el sistema lo implementa igual sobre los datos sintéticos, precisamente para
demostrar el diseño "privado por defecto" independientemente de si el dato de
turno es sintético o real.

## 2. Minimización implementada — `app/privacy.py`

- `first_name(nombre_completo)`: usado en `GET /api/patients` (listado
  general de demo) — expone solo el nombre de pila, nunca el nombre completo,
  la cédula ni la dirección exacta (que sí existen en `data/warehouse.db`
  pero nunca se sirven por esa ruta).
- `mask_documento(cc)`: últimos 3 dígitos visibles, el resto enmascarado —
  disponible para cualquier vista futura que necesite referenciar una cédula
  sin exponerla completa.
- **Excepción deliberada**: el resumen de una llamada específica
  (`GET/POST /api/calls/{id}`) sí puede incluir el nombre que el paciente dio
  durante ESA llamada — porque el equipo clínico necesita poder contactar al
  paciente real que está siendo escalado. Minimizar ahí sería contraproducente
  para la finalidad (seguridad del paciente). La minimización aplica al
  listado general/administrativo, no al expediente de un caso activo.

## 3. Derecho de supresión — `DELETE /api/calls/{conversation_id}`

Borra, de forma irreversible, todos los registros de esa conversación en
`turns.jsonl`, `call_summaries.jsonl` y `alerts.jsonl` (`app/storage.py:delete_matching`).
Devuelve cuántos registros se eliminaron por archivo; `404` si no había nada
que borrar. Es el mecanismo con el que un paciente (o el equipo clínico en su
nombre) ejerce el derecho de supresión de la Ley 1581.

## 4. Pendiente para producción real (fuera del alcance del reto)

- Consentimiento grabado explícito al inicio de cada llamada (hoy el
  `first_message` informa el propósito, pero no pide confirmación grabada).
- Cifrado en reposo de `data/*.jsonl` y `data/warehouse.db` (hoy quedan en
  disco plano en el servidor, protegidos solo por el acceso SSH/IAM).
- Política de retención con expiración automática (hoy los datos persisten
  indefinidamente hasta que alguien pida `DELETE` explícitamente).
- Registro de auditoría de quién accedió a qué expediente y cuándo.
