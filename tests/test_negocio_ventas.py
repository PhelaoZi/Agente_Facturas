# tests/test_negocio_ventas.py
from app.negocio import ventas


class FakeCursor:
    """Cursor falso estilo RealDictCursor (fetchall/fetchone devuelven dicts)."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_total_global():
    r = ventas.total(FakeCursor([{"n": 120, "total": 5000000}]))
    assert r["n"] == 120
    assert r["total"] == 5000000.0
    assert r["desde"] is None and r["hasta"] is None


def test_total_con_rango():
    r = ventas.total(FakeCursor([{"n": 6, "total": 756409}]),
                     desde="2026-06-01", hasta="2026-06-30")
    assert r["n"] == 6
    assert r["total"] == 756409.0
    assert r["desde"] == "2026-06-01"


def test_ranking_mapea_filas():
    rows = [
        {"razon_social": "Bar Uno", "rut_cliente": "11-1", "total_real": 900000},
        {"razon_social": "Bar Dos", "rut_cliente": "22-2", "total_real": 400000},
    ]
    r = ventas.ranking(FakeCursor(rows), limite=2)
    assert r[0] == {"cliente": "Bar Uno", "rut": "11-1", "total": 900000.0}
    assert len(r) == 2


def test_por_cliente_separa_facturas_y_nc():
    rows = [
        {"folio": 10, "tipo_documento": 33, "fecha": "2026-06-01", "monto": 100000},
        {"folio": 11, "tipo_documento": 61, "fecha": "2026-06-02", "monto": 20000},
    ]
    r = ventas.por_cliente(FakeCursor(rows), "Bar Uno")
    assert r["n_facturas"] == 1
    assert r["n_notas_credito"] == 1
    assert r["total_real"] == 100000.0
    assert len(r["documentos"]) == 2


def test_por_producto_mapea():
    # La columna real de la tabla productos es `nombre_producto` (no `descripcion`).
    rows = [
        {"folio": 10, "fecha": "2026-06-01", "razon_social": "Bar Uno",
         "nombre_producto": "Barril 30L Cream Ale", "cantidad": 2, "precio_unitario": 20000},
    ]
    r = ventas.por_producto(FakeCursor(rows), "Cream")
    assert r[0]["producto"] == "Barril 30L Cream Ale"
    assert r[0]["cantidad"] == 2
    assert r[0]["precio_unitario"] == 20000.0
