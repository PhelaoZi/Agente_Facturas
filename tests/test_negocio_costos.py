# tests/test_negocio_costos.py
from app.negocio import costos


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_costos_sku_mapea():
    rows = [
        {"codigo": "CREAM-B30", "nombre_cerveza": "Cream Ale", "formato": "Barril 30L acero",
         "costo_liquido_unitario": 18000, "costo_envasado_unitario": 0,
         "costo_total_unitario": 18000},
    ]
    r = costos.costos_sku(FakeCursor(rows))
    assert r[0]["codigo"] == "CREAM-B30"
    assert r[0]["costo_total"] == 18000.0


def test_margenes_calcula_para_barril_con_precio():
    rows = [
        {"codigo": "CREAM-B30", "nombre_cerveza": "Cream Ale", "formato": "Barril 30L acero",
         "costo_liquido_unitario": 18000, "costo_envasado_unitario": 0,
         "costo_total_unitario": 18000},
    ]
    r = costos.margenes(FakeCursor(rows))
    assert r[0]["precio_venta"] == 55370.0
    assert r[0]["margen"] == 55370.0 - 18000.0


def test_margenes_botella_sin_precio_queda_none():
    rows = [
        {"codigo": "CREAM-330", "nombre_cerveza": "Cream Ale", "formato": "Botella 330ml",
         "costo_liquido_unitario": 600, "costo_envasado_unitario": 300,
         "costo_total_unitario": 900},
    ]
    r = costos.margenes(FakeCursor(rows))
    assert r[0]["precio_venta"] is None
    assert r[0]["margen"] is None
