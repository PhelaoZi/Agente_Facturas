# tests/test_negocio_seguimiento.py
import pytest
from datetime import date
from app.negocio import seguimiento


class FakeCursor:
    """Cursor falso estilo RealDictCursor. Captura sql/params; fetchone devuelve
    `row`, fetchall devuelve `rows`."""

    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def test_validar_agregar_normaliza_y_valida():
    r = seguimiento.validar_agregar(
        {"rut_cliente": "77-1", "motivo": "Se enfrió", "prioridad": "ALTA",
         "senales": "caida_consumo"})
    assert r["rut_cliente"] == "77-1"
    assert r["motivo"] == "Se enfrió"
    assert r["prioridad"] == "alta"
    assert r["senales"] == "caida_consumo"
    assert r["fecha_objetivo"] is None


def test_validar_agregar_rechaza_sin_rut():
    with pytest.raises(ValueError):
        seguimiento.validar_agregar({"motivo": "x"})


def test_validar_agregar_rechaza_sin_motivo():
    with pytest.raises(ValueError):
        seguimiento.validar_agregar({"rut_cliente": "77-1", "motivo": "  "})


def test_validar_agregar_rechaza_prioridad_invalida():
    with pytest.raises(ValueError):
        seguimiento.validar_agregar(
            {"rut_cliente": "77-1", "motivo": "x", "prioridad": "urgentisima"})


def test_agregar_inserta_y_devuelve_id():
    # fetchone se llama dos veces: guard de pendiente (None) y RETURNING id.
    class Cur(FakeCursor):
        def __init__(self):
            super().__init__()
            self._calls = 0

        def fetchone(self):
            self._calls += 1
            return None if self._calls == 1 else {"id": 7}

    cur = Cur()
    r = seguimiento.agregar(cur, "77-1", "Se enfrió", "alta", "caida_consumo", None, None)
    assert r["id"] == 7
    assert "seguimiento_comercial" in cur.sql
    assert "RETURNING id" in cur.sql


def test_agregar_rechaza_si_ya_hay_pendiente():
    cur = FakeCursor(row={"id": 3})  # hay un pendiente
    with pytest.raises(ValueError):
        seguimiento.agregar(cur, "77-1", "x", "media", None, None, None)


def test_validar_marcar_estado_y_fecha_por_defecto():
    r = seguimiento.validar_marcar({"id": 5, "estado": "contactado"})
    assert r["id"] == 5
    assert r["estado"] == "contactado"
    assert r["fecha_contacto"] == date.today().isoformat()


def test_validar_marcar_rechaza_estado_malo():
    with pytest.raises(ValueError):
        seguimiento.validar_marcar({"id": 5, "estado": "pendiente"})


def test_marcar_actualiza_y_devuelve_mensaje():
    cur = FakeCursor(row={"rut_cliente": "77-1", "motivo": "Se enfrió"})
    r = seguimiento.marcar(cur, 5, "contactado", "2026-06-24")
    assert "contactado" in r["mensaje"]
    assert "UPDATE" in cur.sql and "seguimiento_comercial" in cur.sql
    assert cur.params == ("contactado", "2026-06-24", 5)


def test_marcar_inexistente_lanza_valueerror():
    with pytest.raises(ValueError):
        seguimiento.marcar(FakeCursor(row=None), 999, "contactado", "2026-06-24")


def test_listar_filtra_por_estado():
    filas = [{"id": 1, "rut_cliente": "77-1", "razon_social": "Bar X",
              "motivo": "x", "prioridad": "alta", "estado": "pendiente",
              "senales": None, "fecha_creacion": "2026-06-24",
              "fecha_objetivo": None, "fecha_contacto": None}]
    cur = FakeCursor(rows=filas)
    r = seguimiento.listar(cur, estado="pendiente")
    assert len(r) == 1 and r[0]["razon_social"] == "Bar X"
