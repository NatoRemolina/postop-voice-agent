"""Genera el informe final en PDF con la identidad visual de VALAI.ORG.

Dos pasos, porque Chrome headless no sabe hacer cabeceras corridas:

1. Markdown → HTML con la paleta y la tipografía de la marca → PDF con Chrome.
   Incluye una portada a página completa con el logotipo sobre petróleo.
2. Estampado con reportlab de la cabecera de marca y el número de página en
   cada hoja de contenido (`position: fixed` de CSS solo se pinta en la primera
   página en Chrome — comprobado).

Fuentes e imágenes van embebidas: el PDF se ve igual en cualquier equipo.

    .venv/bin/python -m scripts.informe_pdf
"""

import base64
import io
import mimetypes
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = RAIZ / "docs/entregables/informe-final.md"
SALIDA = RAIZ / "docs/entregables/informe-final.pdf"
MARCA = RAIZ / "app/web/static/marca"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Paleta oficial, muestreada de los archivos originales del logotipo.
PETROLEO, TURQUESA, LIMA, MENTA = "#01383F", "#10B89F", "#9DFB65", "#DBFBFA"
A4_ANCHO, A4_ALTO = 595.28, 841.89  # puntos


def _data_uri(ruta: Path) -> str:
    tipo = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
    return f"data:{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode()}"


def _fuente(nombre: str, peso: int) -> str:
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
        _fuente("FunnelDisplay-Regular.ttf", 400),
        _fuente("FunnelDisplay-SemiBold.ttf", 600),
        _fuente("FunnelDisplay-Bold.ttf", 700),
    ])
    return f"""{fuentes}
@page {{ size: A4; margin: 24mm 17mm 20mm; }}
@page :first {{ margin: 0; }}
* {{ box-sizing: border-box; }}
body {{
  font-family: 'Funnel Display', system-ui, sans-serif;
  color: {PETROLEO}; font-size: 10.5pt; line-height: 1.55; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}}

/* ── Portada ─────────────────────────────────────────────── */
.portada {{
  background: {PETROLEO}; color: {MENTA};
  height: 297mm; padding: 34mm 24mm 24mm;
  display: flex; flex-direction: column;
  page-break-after: always; position: relative;
}}
/* El borde de la regla general de <img> no debe tocar el logotipo. */
.portada .logo {{
  height: 52px; width: auto; margin-bottom: auto;
  border: none; border-radius: 0;
}}
.portada .kicker {{
  font-size: 9.5pt; letter-spacing: .16em; text-transform: uppercase;
  color: {TURQUESA}; margin-bottom: .9em;
}}
.portada h1 {{
  font-size: 34pt; font-weight: 700; line-height: 1.12; letter-spacing: -.025em;
  margin: 0 0 .35em; color: #fff; border: none; padding: 0;
}}
.portada .sub {{
  font-size: 13pt; font-weight: 400; color: {MENTA}; opacity: .85;
  margin: 0 0 2.2em; max-width: 78%;
}}
.portada .regla {{ height: 4px; width: 84px; background: {LIMA}; margin-bottom: 1.6em; }}
.portada .meta {{ font-size: 10pt; line-height: 1.85; color: {MENTA}; opacity: .9; }}
.portada .meta b {{ color: #fff; font-weight: 600; }}
.portada .pie {{
  margin-top: 2.4em; padding-top: 1em; font-size: 8.8pt;
  border-top: 1px solid rgba(219,251,250,.25); color: {MENTA}; opacity: .7;
}}

/* ── Contenido ───────────────────────────────────────────── */
h1 {{
  font-size: 20pt; font-weight: 700; letter-spacing: -.02em;
  border-bottom: 3px solid {TURQUESA}; padding-bottom: .3em; margin: 0 0 .7em;
}}
h2 {{
  font-size: 14pt; font-weight: 700; margin: 1.7em 0 .55em;
  padding-left: .55em; border-left: 4px solid {TURQUESA};
  page-break-after: avoid;
}}
h3 {{
  font-size: 11.5pt; font-weight: 600; margin: 1.25em 0 .4em;
  color: #0a6b60; page-break-after: avoid;
}}
p, li {{ orphans: 3; widows: 3; }}
a {{ color: #0a7d6e; text-decoration: none; }}
code {{
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 8.6pt;
  background: {MENTA}; padding: .1em .35em; border-radius: 3px;
}}
pre {{
  background: {PETROLEO}; color: {MENTA}; padding: .85em 1em; border-radius: 6px;
  font-size: 8pt; line-height: 1.45; white-space: pre-wrap; word-break: break-word;
  page-break-inside: avoid; border-left: 3px solid {TURQUESA};
}}
pre code {{ background: none; color: inherit; padding: 0; }}
table {{
  width: 100%; border-collapse: collapse; margin: .95em 0; font-size: 8.8pt;
  page-break-inside: avoid;
}}
th {{
  background: {MENTA}; color: {PETROLEO}; text-align: left; font-weight: 600;
  padding: .45em .6em; border-bottom: 2px solid {TURQUESA};
}}
td {{ padding: .45em .6em; border-bottom: 1px solid #dcebe9; vertical-align: top; }}
tr:nth-child(even) td {{ background: #f7fcfb; }}
blockquote {{
  margin: .95em 0; padding: .65em 1.05em; background: #f2fbfa;
  border-left: 4px solid {LIMA};
}}
blockquote p {{ margin: .3em 0; }}
img {{
  max-width: 100%; border: 1px solid #cfe6e4; border-radius: 6px;
  page-break-inside: avoid; display: block; margin: .9em 0 .5em;
}}
/* Pie de figura: el párrafo que sigue a una captura. */
main p > img + em {{ font-size: 8.6pt; color: #5b7d7a; }}
hr {{ border: none; border-top: 1px solid #cfe6e4; margin: 1.7em 0; }}
ul, ol {{ padding-left: 1.3em; }}
li {{ margin: .25em 0; }}
"""


def _portada(titulo: str, subtitulo: str) -> str:
    logo = MARCA / "logotipo-valai.png"  # versión clara, para fondo oscuro
    img = f'<img class="logo" src="{_data_uri(logo)}" alt="VALAI.ORG">' if logo.exists() else ""
    hoy = date.today().strftime("%d/%m/%Y")
    return f"""<section class="portada">
  {img}
  <div class="kicker">Entregable 03 · Informe final</div>
  <h1>{titulo}</h1>
  <p class="sub">{subtitulo}</p>
  <div class="regla"></div>
  <div class="meta">
    <b>Autora</b> · Natalia Patricia Remolina Rodríguez<br>
    <b>Repositorio</b> · github.com/NatoRemolina/postop-voice-agent<br>
    <b>Demo en vivo</b> · 52-207-194-196.sslip.io/call<br>
    <b>Consola</b> · valai-agente-atencion.lovable.app<br>
    <b>Fecha</b> · {hoy}
  </div>
  <div class="pie">Tech Sphere Challenge 2026 · VALAI.ORG</div>
</section>"""


def _estampar(pdf_bytes: bytes) -> bytes:
    """Cabecera de marca y numeración en cada página de contenido."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    lector = PdfReader(io.BytesIO(pdf_bytes))
    total = len(lector.pages)
    escritor = PdfWriter()
    logo = MARCA / "logotipo-valai-oscuro.png"  # versión oscura, sobre blanco
    marca = ImageReader(str(logo)) if logo.exists() else None

    for i, pagina in enumerate(lector.pages):
        if i > 0:  # la portada no se estampa
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(A4_ANCHO, A4_ALTO))
            if marca:
                alto = 13
                ancho = alto * (marca.getSize()[0] / marca.getSize()[1])
                c.drawImage(marca, 48, A4_ALTO - 46, width=ancho, height=alto,
                            mask="auto")
            c.setStrokeColor(HexColor(TURQUESA))
            c.setLineWidth(0.8)
            c.line(48, A4_ALTO - 56, A4_ANCHO - 48, A4_ALTO - 56)
            c.setFont("Helvetica", 7.5)
            c.setFillColor(HexColor("#5b7d7a"))
            c.drawRightString(A4_ANCHO - 48, A4_ALTO - 43,
                              "Informe final · Agente de atención postoperatoria")
            c.drawCentredString(A4_ANCHO / 2, 32, f"{i} / {total - 1}")
            c.save()
            buf.seek(0)
            pagina.merge_page(PdfReader(buf).pages[0])
        escritor.add_page(pagina)

    salida = io.BytesIO()
    escritor.write(salida)
    return salida.getvalue()


def construir() -> Path:
    import markdown

    texto = FUENTE.read_text(encoding="utf-8")

    # El título y el logotipo del markdown pasan a la portada.
    texto = re.sub(r"^# .*\n", "", texto, count=1)
    texto = re.sub(r"!\[VALAI\.ORG\]\([^)]+\)\n?", "", texto, count=1)
    # Los datos de cabecera también están en la portada: se evita repetirlos.
    texto = re.sub(r"^Proyecto:.*\nAutora:.*\nRepositorio:.*\n", "", texto,
                   count=1, flags=re.MULTILINE)

    cuerpo = markdown.markdown(
        texto, extensions=["tables", "fenced_code", "sane_lists", "attr_list"]
    )

    def _embeber(m: re.Match) -> str:
        ruta = (FUENTE.parent / m.group(1)).resolve()
        return f'src="{_data_uri(ruta)}"' if ruta.exists() else m.group(0)

    cuerpo = re.sub(r'src="([^"]+)"', _embeber, cuerpo)

    html = (
        "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
        f"<title>Informe final — VALAI</title><style>{_css()}</style></head><body>"
        + _portada("Agente de atención para seguimiento postoperatorio",
                   "Un agente de voz que llama al paciente recién operado, fundamenta "
                   "cada respuesta clínica en guías reales con cita verificable y "
                   "decide cuándo escalar a un equipo humano.")
        + f"<main>{cuerpo}</main></body></html>"
    )

    tmp_html = RAIZ / "docs/entregables/.informe-tmp.html"
    tmp_pdf = RAIZ / "docs/entregables/.informe-tmp.pdf"
    tmp_html.write_text(html, encoding="utf-8")
    try:
        subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={tmp_pdf}", f"file://{tmp_html}"],
            check=True, capture_output=True, timeout=240,
        )
        SALIDA.write_bytes(_estampar(tmp_pdf.read_bytes()))
    finally:
        tmp_html.unlink(missing_ok=True)
        tmp_pdf.unlink(missing_ok=True)
    return SALIDA


if __name__ == "__main__":
    salida = construir()
    if not salida.exists():
        print("no se generó el PDF", file=sys.stderr)
        raise SystemExit(1)
    print(f"{salida.relative_to(RAIZ)} — {salida.stat().st_size // 1024} KB")
