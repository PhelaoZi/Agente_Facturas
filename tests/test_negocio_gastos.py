# tests/test_negocio_gastos.py
import pytest
from app.negocio import gastos


class FakeCursor:
    """Cursor falso estilo RealDictCursor: fetchone devuelve un dict."""

    def __init__(self, row):
        self._row = row
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row


def test_validar_gasto_normaliza_monto_chileno():
    r = gastos.validar_gasto("Luz", "185.000", "2026-06-30")
    assert r == {"descripcion": "Luz", "monto": 185000.0,
                 "fecha": "2026-06-30", "proveedor": None, "categoria": None}


def test_validar_gasto_acepta_monto_numerico_y_campos_opcionales():
    r = gastos.validar_gasto("Arriendo", 850000, "2026-07-05",
                             proveedor="Prop SA", categoria="arriendo")
    assert r["monto"] == 850000.0
    assert r["proveedor"] == "Prop SA"
    assert r["categoria"] == "arriendo"


def test_validar_gasto_rechaza_descripcion_vacia():
    with pytest.raises(ValueError):
        gastos.validar_gasto("   ", "1000", "2026-06-30")


def test_validar_gasto_rechaza_monto_no_numerico():
    with pytest.raises(ValueError):
        gastos.validar_gasto("Luz", "abc", "2026-06-30")


def test_validar_gasto_rechaza_monto_cero_o_negativo():
    with pytest.raises(ValueError):
        gastos.validar_gasto("Luz", "0", "2026-06-30")


def test_validar_gasto_rechaza_fecha_mala():
    with pytest.raises(ValueError):
        gastos.validar_gasto("Luz", "1000", "30/06/2026")


def test_registrar_gasto_devuelve_id_y_usa_parametros():
    cur = FakeCursor({"id": 42})
    new_id = gastos.registrar_gasto(cur, "Luz", 185000.0, "2026-06-30", None, "servicios")
    assert new_id == 42
    # El INSERT va parametrizado, en el orden de columnas de cuentas_por_pagar
    assert cur.params == ("Luz", None, 185000.0, "2026-06-30", "servicios")
    assert "cuentas_por_pagar" in cur.sql
    assert "RETURNING id" in cur.sql
