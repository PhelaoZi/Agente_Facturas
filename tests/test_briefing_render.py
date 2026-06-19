# tests/test_briefing_render.py
from datetime import date
from app.briefing import render


def _brief_ejemplo():
    return {
        "umbral_vencidas": 30,
        "umbral_reciente": 7,
        "umbral_inactivos": 60,
        "cobranza": {
            "total": 200000,
            "n_facturas": 4,
            "buckets": {"al_dia": 20000, "d1_30": 100000, "d31_60": 50000, "d60_mas": 30000},
        },
        "top_deudores": [{"cliente": "Bar Uno", "deuda": 500000, "n": 3}],
        "vencidas": [{"folio": 1234, "cliente": "Bar Uno", "total": 80000, "dias": 78}],
        "cobrado_reciente": {"n": 2, "total": 100000, "facturas": []},
        "ventas_periodo": {"n": 5, "total": 350000},
        "inactivos": [{"cliente": "Bar Frio", "ultima_venta": "2026-03-01", "dias": 109}],
    }


def test_render_incluye_titulo_con_fecha():
    md = render.render_markdown(_brief_ejemplo(), hoy=date(2026, 6, 18))
    assert "# Brief diario Zigurat — 18/06/2026" in md


def test_render_formatea_pesos_chilenos():
    md = render.render_markdown(_brief_ejemplo(), hoy=date(2026, 6, 18))
    assert "$200.000" in md   # deuda total
    assert "$500.000" in md   # top deudor
    assert "Bar Uno" in md
    assert "Bar Frio" in md


def test_render_sin_deuda_muestra_mensaje_amable():
    brief = _brief_ejemplo()
    brief["top_deudores"] = []
    brief["vencidas"] = []
    md = render.render_markdown(brief, hoy=date(2026, 6, 18))
    assert "Sin deuda pendiente" in md
    assert "Ninguna factura vencida" in md
