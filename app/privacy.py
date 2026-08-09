"""Minimización de datos personales — ver docs/gobernanza-datos.md.

Principio aplicado: cada superficie del sistema expone solo el dato personal
que necesita para su propósito concreto. La consola de administración/demo
(listado general de pacientes) no necesita identidad completa; el resumen de
una llamada específica sí (el equipo clínico debe poder contactar al
paciente real que está siendo escalado) — por eso este módulo minimiza en el
primer caso y deja intacto el segundo, en vez de enmascarar todo por igual.
"""


def first_name(full_name: str | None) -> str:
    """Nombre de pila únicamente — para listados generales, nunca para el
    resumen de una llamada puntual que el equipo clínico deba poder contactar."""
    if not full_name:
        return ""
    return full_name.strip().split()[0]


def mask_documento(documento: str | int | None) -> str:
    """Cédula: solo los últimos 3 dígitos visibles."""
    if documento is None:
        return ""
    text = str(documento)
    if len(text) <= 3:
        return "*" * len(text)
    return "*" * (len(text) - 3) + text[-3:]
