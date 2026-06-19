"""Exporta artefactos y el lienzo completo a archivos descargables."""
import base64
import io
from html import escape

import pandas as pd

from app.canvas.artifacts import Artifact
from app.charts.builder import build_figure


def _tabla_df(art: Artifact) -> pd.DataFrame:
    return pd.DataFrame(art.payload["filas"], columns=art.payload["columnas"])


def tabla_to_csv(art: Artifact) -> bytes:
    return _tabla_df(art).to_csv(index=False).encode("utf-8")


def tabla_to_excel(art: Artifact) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _tabla_df(art).to_excel(writer, index=False, sheet_name="Datos")
    return buf.getvalue()


def grafico_to_png(art: Artifact) -> bytes:
    """Requiere kaleido instalado."""
    return build_figure(art.payload).to_image(format="png")


def _artifact_to_html(art: Artifact) -> str:
    titulo = escape(art.titulo)
    if art.tipo == "kpi":
        valor = escape(str(art.payload.get("valor", "")))
        delta = escape(str(art.payload.get("delta", "")))
        return (
            f'<div class="kpi"><div class="k-label">{titulo}</div>'
            f'<div class="k-val">{valor}</div><div class="k-delta">{delta}</div></div>'
        )
    if art.tipo == "grafico":
        png_b64 = base64.b64encode(grafico_to_png(art)).decode("ascii")
        return f'<h3>{titulo}</h3><img src="data:image/png;base64,{png_b64}" style="max-width:100%">'
    if art.tipo == "tabla":
        tabla_html = _tabla_df(art).to_html(index=False, border=0)
        return f"<h3>{titulo}</h3>{tabla_html}"
    # informe
    cuerpo = escape(art.payload.get("markdown", "")).replace("\n", "<br>")
    return f"<h3>{titulo}</h3><p>{cuerpo}</p>"


def lienzo_to_html(canvas: list[Artifact]) -> str:
    bloques = "\n".join(_artifact_to_html(a) for a in canvas)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Informe Zigurat</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;}}
 .kpi{{display:inline-block;border:1px solid #ddd;border-radius:8px;padding:0.6rem 1rem;margin:0.3rem;}}
 .k-label{{color:#666;font-size:0.8rem;}} .k-val{{font-size:1.4rem;font-weight:700;}}
 .k-delta{{color:#2a9d4a;font-size:0.85rem;}}
 table{{border-collapse:collapse;width:100%;}} th,td{{border-bottom:1px solid #eee;padding:0.4rem;text-align:left;}}
</style></head><body>
<h1>Informe Zigurat</h1>
{bloques}
</body></html>"""
