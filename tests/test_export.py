# tests/test_export.py
import pytest
from app.canvas.artifacts import Artifact
from app.canvas import export


def test_tabla_csv_incluye_datos():
    art = Artifact(tipo="tabla", titulo="T", payload={"columnas": ["c"], "filas": [[1], [2]]})
    out = export.tabla_to_csv(art)
    assert b"c" in out and b"1" in out and b"2" in out


def test_tabla_excel_no_vacio():
    art = Artifact(tipo="tabla", titulo="T", payload={"columnas": ["c"], "filas": [[1]]})
    out = export.tabla_to_excel(art)
    assert len(out) > 0


def test_lienzo_html_incluye_titulos_y_estructura():
    canvas = [
        Artifact(tipo="kpi", titulo="Facturado",
                 payload={"etiqueta": "Facturado", "valor": "$1M", "delta": ""}),
        Artifact(tipo="informe", titulo="Resumen", payload={"markdown": "Hola socio"}),
    ]
    html = export.lienzo_to_html(canvas)
    assert "<html" in html.lower()
    assert "Facturado" in html
    assert "Resumen" in html
    assert "Hola socio" in html


def test_grafico_png_si_kaleido_disponible():
    pytest.importorskip("kaleido")
    art = Artifact(tipo="grafico", titulo="G",
                   payload={"chart_type": "bar", "x": ["a"], "y": [1]})
    out = export.grafico_to_png(art)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"  # firma de archivo PNG
