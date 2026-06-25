from app.negocio import clientes


class FakeCursor:
    """Cursor falso estilo RealDictCursor: ignora el SQL y devuelve las filas
    precargadas (patrón de test del proyecto)."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows


def _fila(**kw):
    """Fila base 'sana' (sin señales). Cada test sobreescribe lo que necesita."""
    base = {
        "rut_cliente": "1-1", "razon_social": "Bar X", "n_facturas": 5,
        "total_historico": 1_000_000, "ultima_venta": "2026-06-01",
        "dias_desde_ultima": 10, "ventas_ult_60": 100_000, "ventas_prev_60": 100_000,
        "brecha_historica_dias": 7, "brecha_reciente_dias": 7,
    }
    base.update(kw)
    return base


def test_cliente_sano_no_aparece():
    assert clientes.salud_clientes(FakeCursor([_fila()])) == []


def test_dormido_dispara_y_omite_las_otras_senales():
    r = clientes.salud_clientes(FakeCursor([_fila(dias_desde_ultima=90)]))
    assert r[0]["senales"] == ["dormido"]


def test_caida_consumo_sobre_umbral():
    r = clientes.salud_clientes(FakeCursor([
        _fila(ventas_prev_60=100_000, ventas_ult_60=40_000)]))  # -60%
    assert "caida_consumo" in r[0]["senales"]


def test_caida_consumo_bajo_umbral_no_dispara():
    r = clientes.salud_clientes(FakeCursor([
        _fila(ventas_prev_60=100_000, ventas_ult_60=80_000)]))  # -20%
    assert r == []


def test_bajo_frecuencia():
    r = clientes.salud_clientes(FakeCursor([
        _fila(brecha_historica_dias=7, brecha_reciente_dias=20)]))  # 20 > 1.5*7
    assert "bajo_frecuencia" in r[0]["senales"]


def test_nuevo_sin_recompra():
    r = clientes.salud_clientes(FakeCursor([
        _fila(n_facturas=1, dias_desde_ultima=30, ventas_prev_60=0,
              brecha_historica_dias=None, brecha_reciente_dias=None)]))
    assert r[0]["senales"] == ["nuevo_sin_recompra"]


def test_prioridad_alta_solo_para_top10():
    # 12 clientes dormidos con facturación creciente; solo los 10 mayores = alta
    filas = [_fila(rut_cliente=f"{i}-0", total_historico=i * 1000, dias_desde_ultima=90)
             for i in range(1, 13)]
    r = clientes.salud_clientes(FakeCursor(filas))
    assert sum(1 for c in r if c["prioridad"] == "alta") == 10


def test_motivo_no_vacio():
    r = clientes.salud_clientes(FakeCursor([_fila(dias_desde_ultima=90)]))
    assert r[0]["motivo"]
