import re
import unicodedata

# Debe coincidir con los nombres reales de carpeta en dataset/textos/ (= metadata "scenario").
PROCEDURE_KEYWORDS: dict[str, list[str]] = {
    "Appendicitis": ["apendic", "apendice", "apéndice"],
    "cholecystitis": ["vesicula", "vesícula", "colecistectomia", "colecistitis", "biliar"],
    "colorectal cancer": [
        "colon", "recto", "colectomia", "colorrectal", "colorectal",
        "cancer de colon", "cáncer de colon",
    ],
    "total joint replacement": [
        "cadera", "rodilla", "artroplastia", "reemplazo articular",
        "protesis", "prótesis", "reemplazo de cadera", "reemplazo de rodilla",
    ],
    "breast_cancer": [
        "mastectomia", "mastectomía", "seno", "mama", "cancer de mama",
        "cáncer de mama", "cuello uterino", "cervix", "cérvix",
    ],
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


_NORMALIZED_KEYWORDS = {
    scenario: [_normalize(kw) for kw in keywords]
    for scenario, keywords in PROCEDURE_KEYWORDS.items()
}


def detect_scenario(texts: list[str]) -> str | None:
    """Busca menciones del procedimiento en el texto acumulado de la llamada.

    Escanea todo el historial (no solo el último turno): el paciente suele
    nombrar su cirugía una sola vez, al inicio.
    """
    combined = _normalize(" ".join(t for t in texts if t))
    if not combined:
        return None
    for scenario, keywords in _NORMALIZED_KEYWORDS.items():
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}", combined):
                return scenario
    return None
