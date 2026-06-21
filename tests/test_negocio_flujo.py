# tests/test_negocio_flujo.py
from datetime import date
from app.negocio import flujo


class FakeCursorSecuencial:
    """Devuelve un result-set por cada execute(), en orden. proyectar_flujo hace
    estas consultas en este orden: saldo banco, avg días, facturas pendientes,
    gastos puntuales, gastos recurrentes."""

    def __init__(self, resultados):
        self._resultados = list(resultados)
        self._actual = []

    def execute(self, sql, params=None):
        self._actual = self._resultados.pop(0) if self._resultados else []

    def fetchall(self):
        return self._actual

    def fetchone(self):
        return self._actual[0] if self._actual else None


def test_proyectar_flujo_estructura_y_ingreso_en_ventana():
    hoy = date(2026, 6, 20)
    resultados = [
        [{"saldo_diario": 1000000, "fecha": hoy}],          # saldo banco
        [{"rut_cliente": "11-1", "avg_dias": 30}],          # avg días
        [{"folio": 1, "fecha": date(2026, 5, 31), "rut_cliente": "11-1",
          "razon_social_receptor": "Bar Uno", "monto": 200000}],  # facturas
        [],                                                  # gastos puntuales
        [],                                                  # gastos recurrentes
    ]
    r = flujo.proyectar_flujo(FakeCursorSecuencial(resultados), hoy=hoy)
    assert r["saldo_inicial"] == 1000000.0
    assert len(r["semanas"]) == 4
    # factura del 31/05 + 30 días = 30/06, dentro del horizonte (hoy+28d) -> cuenta
    assert r["total_ingresos"] == 200000.0


def test_proyectar_flujo_saldo_manual():
    hoy = date(2026, 6, 20)
    resultados = [
        [],   # avg días (no consulta saldo banco porque saldo_inicial viene dado)
        [],   # facturas
        [],   # gastos puntuales
        [],   # gastos recurrentes
    ]
    r = flujo.proyectar_flujo(FakeCursorSecuencial(resultados),
                              saldo_inicial=500000, hoy=hoy)
    assert r["saldo_inicial"] == 500000.0
    assert r["total_ingresos"] == 0.0
    assert r["total_egresos"] == 0.0
