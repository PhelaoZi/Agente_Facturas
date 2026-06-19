# tests/test_briefing_data.py
from app.briefing import data


class FakeCursor:
    """Cursor falso al estilo RealDictCursor: fetchall/fetchone devuelven dicts.
    Ignora el SQL; solo entrega las filas precargadas (patrón de test del proyecto)."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_resumen_cobranza_clasifica_por_antiguedad():
    rows = [
        {"dias": 0, "total": 20000},    # al día
        {"dias": 5, "total": 100000},   # 1-30
        {"dias": 45, "total": 50000},   # 31-60
        {"dias": 90, "total": 30000},   # +60
    ]
    r = data.resumen_cobranza(FakeCursor(rows))
    assert r["total"] == 200000
    assert r["n_facturas"] == 4
    assert r["buckets"]["al_dia"] == 20000
    assert r["buckets"]["d1_30"] == 100000
    assert r["buckets"]["d31_60"] == 50000
    assert r["buckets"]["d60_mas"] == 30000


def test_resumen_cobranza_sin_deuda():
    r = data.resumen_cobranza(FakeCursor([]))
    assert r["total"] == 0
    assert r["n_facturas"] == 0
    assert r["buckets"] == {"al_dia": 0, "d1_30": 0, "d31_60": 0, "d60_mas": 0}


def test_top_deudores_mapea_y_preserva_orden():
    rows = [
        {"razon_social": "Bar Uno", "deuda": 500000, "n": 3},
        {"razon_social": "Bar Dos", "deuda": 200000, "n": 1},
    ]
    r = data.top_deudores(FakeCursor(rows), limite=5)
    assert r == [
        {"cliente": "Bar Uno", "deuda": 500000.0, "n": 3},
        {"cliente": "Bar Dos", "deuda": 200000.0, "n": 1},
    ]


def test_facturas_vencidas_mapea_dias_y_total():
    rows = [
        {"folio": 1234, "fecha": "2026-04-01", "razon_social": "Bar Uno",
         "total": 80000, "dias_vencida": 78},
    ]
    r = data.facturas_vencidas(FakeCursor(rows), dias=30)
    assert r == [{"folio": 1234, "cliente": "Bar Uno", "total": 80000.0, "dias": 78}]


def test_cobrado_reciente_suma_y_cuenta():
    rows = [
        {"folio": 1, "fecha_pago": "2026-06-17", "razon_social": "Bar Uno", "total": 70000},
        {"folio": 2, "fecha_pago": "2026-06-16", "razon_social": "Bar Dos", "total": 30000},
    ]
    r = data.cobrado_reciente(FakeCursor(rows), dias=7)
    assert r["n"] == 2
    assert r["total"] == 100000.0
    assert r["facturas"][0]["cliente"] == "Bar Uno"


def test_ventas_periodo_devuelve_n_y_total():
    r = data.ventas_periodo(FakeCursor([{"n": 5, "total": 350000}]), dias=7)
    assert r == {"n": 5, "total": 350000.0}


def test_clientes_inactivos_mapea_dias():
    rows = [
        {"razon_social": "Bar Frio", "ultima_venta": "2026-03-01", "dias_inactivo": 109},
    ]
    r = data.clientes_inactivos(FakeCursor(rows), dias=60)
    assert r == [{"cliente": "Bar Frio", "ultima_venta": "2026-03-01", "dias": 109}]
