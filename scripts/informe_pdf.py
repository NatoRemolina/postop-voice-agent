"""Genera el informe final en PDF con la identidad visual de VALAI.ORG.

Convierte `docs/entregables/informe-final.md` a HTML con la paleta y la
tipografía de la marca y lo imprime con Chrome en modo headless (no hace falta
LaTeX ni pandoc). Las imágenes y las fuentes se embeben como data URI para que
el PDF sea autocontenido y se vea igual en cualquier equipo.

    .venv/bin/python -m scripts.informe_pdf
"""

import base64
import mimetypes
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = RAIZ / "docs/entregables/informe-final.md"
SALIDA = RAIZ / "docs/entregables/informe-final.pdf"
MARCA = RAIZ / "app/web/static/marca"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Paleta oficial, muestreada de los archivos originales del logotipo.
PETROLEO, TURQUESA, LIMA, MENTA = "#01383F", "#10B89F", "#9DFB65", "#DBFBFA"


def _data_uri(ruta: Path) -> str:
    tipo = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
    return f"data:{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode()}"


def _fuente_embebida(nombre: str, peso: int) -> str:
    ruta = MARCA / nombre
    if not ruta.exists():
        return ""
    return (
        "@font-face{font-family:'Funnel Display';"
        f"src:url('{_data_uri(ruta)}') format('truetype');"
        f"font-weight:{peso};font-style:normal;}}"
    )


def _css() -> str:
    fuentes = "".join([
        _fuente_embebida("FunnelDisplay-Regular.ttf", 400),
        _fuente_embebida("FunnelDisplay-SemiBold.ttf", 600),
        _fuente_embebida("FunnelDisplay-Bold.ttf", 700),
    ])
    return f"""{fuentes}
@page {{ size: A4; margin: 18mm 16mm 20mm; }}
* {{ box-sizing: border-box; }}
body {{
  font-family: 'Funnel Display', system-ui, sans-serif;
  color: {PETROLEO}; font-size: 10.5pt; line-height: 1.55; margin: 0;
}}
h1 {{
  font-size: 22pt; font-weight: 700; letter-spacing: -.02em;
  border-bottom: 3px solid {TURQUESA}; padding-bottom: .3em; margin: 0 0 .6em;
}}
h2 {{
  font-size: 14pt; font-weight: 700; color: {PETROLEO}; margin: 1.6em 0 .5em;
  padding-left: .5em; border-left: 4px solid {TURQUESA};
  page-break-after: avoid;
}}
h3 {{ font-size: 11.5pt; font-weight: 600; margin: 1.2em 0 .4em; page-break-after: avoid; }}
p, li {{ orphans: 3; widows: 3; }}
a {{ color: #0a7d6e; text-decoration: none; }}
code {{
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 8.8pt;
  background: {MENTA}; padding: .1em .35em; border-radius: 3px;
}}
pre {{
  background: {PETROLEO}; color: {MENTA}; padding: .8em 1em; border-radius: 6px;
  font-size: 8.2pt; line-height: 1.45; overflow-x: hidden; white-space: pre-wrap;
  word-break: break-word; page-break-inside: avoid;
}}
pre code {{ background: none; color: inherit; padding: 0; }}
table {{
  width: 100%; border-collapse: collapse; margin: .9em 0; font-size: 9pt;
  page-break-inside: avoid;
}}
th {{
  background: {MENTA}; color: {PETROLEO}; text-align: left; font-weight: 600;
  padding: .45em .6em; border-bottom: 2px solid {TURQUESA};
}}
td {{ padding: .45em .6em; border-bottom: 1px solid #d9e8e6; vertical-align: top; }}
tr:nth-child(even) td {{ background: #f6fcfb; }}
blockquote {{
  margin: .9em 0; padding: .6em 1em; background: #f2fbfa;
  border-left: 4px solid {LIMA}; font-style: normal;
}}
blockquote p {{ margin: .3em 0; }}
img {{ max-width: 100%; border: 1px solid #cfe6e4; border-radius: 6px; page-break-inside: avoid; }}
img[alt="VALAI.ORG"] {{ border: none; height: 42px; width: auto; margin-bottom: .4em; }}
hr {{ border: none; border-top: 1px solid #cfe6e4; margin: 1.6em 0; }}
strong {{ font-weight: 700; }}
ul, ol {{ padding-left: 1.3em; }}
li {{ margin: .25em 0; }}
.pie {{
  margin-top: 2.5em; padding-top: .8em; border-top: 1px solid #cfe6e4;
  font-size: 8.5pt; color: #5b7d7a;
}}
"""


def construir() -> Path:
    import markdown

    texto = FUENTE.read_text(encoding="utf-8")

    html_cuerpo = markdown.markdown(
        texto,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
    )

    # Las imágenes se embeben: el PDF debe verse igual sin acceso al repo.
    def _embeber(m: re.Match) -> str:
        src = m.group(1)
        ruta = (FUENTE.parent / src).resolve()
        return f'src="{_data_uri(ruta)}"' if ruta.exists() else m.group(0)

    html_cuerpo = re.sub(r'src="([^"]+)"', _embeber, html_cuerpo)

    html = (
        "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
        f"<title>Informe final — VALAI</title><style>{_css()}</style></head>"
        f"<body>{html_cuerpo}"
        "<p class='pie'>VALAI.ORG · Agente de atención para seguimiento "
        "postoperatorio · Tech Sphere Challenge 2026</p>"
        "</body></html>"
    )

    tmp = RAIZ / "docs/entregables/.informe-tmp.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={SALIDA}", f"file://{tmp}"],
            check=True, capture_output=True, timeout=180,
        )
    finally:
        tmp.unlink(missing_ok=True)
    return SALIDA


if __name__ == "__main__":
    salida = construir()
    if not salida.exists():
        print("no se generó el PDF", file=sys.stderr)
        raise SystemExit(1)
    print(f"{salida.relative_to(RAIZ)} — {salida.stat().st_size // 1024} KB")
