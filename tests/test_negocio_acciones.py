# tests/test_negocio_acciones.py
import pytest
from app.negocio import acciones


class FakeCursor:
    def __init__(self, row=None):
        self._row = row
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row


def test_validar_tipo_desconocido_lanza_valueerror():
    with pytest.raises(ValueError):
        acciones.validar("inventado", {})


def test_ejecutar_tipo_desconocido_lanza_valueerror():
    with pytest.raises(ValueError):
        acciones.ejecutar(FakeCursor(), "inventado", {})


def test_validar_registrar_enruta_a_validar_gasto():
    clean = acciones.validar("registrar_gasto",
                             {"descripcion": "Luz", "monto": "185000", "fecha": "2026-06-30"})
    assert clean["descripcion"] == "Luz"
    assert clean["monto"] == 185000.0


def test_validar_borrar_enruta():
    assert acciones.validar("borrar_gasto", {"id": "5"}) == {"id": 5}


def test_ejecutar_borrar_devuelve_resultado_uniforme():
    cur = FakeCursor(row={"descripcion": "Contadora"})
    r = acciones.ejecutar(cur, "borrar_gasto", {"id": 5})
    assert r["mensaje"] == "Gasto borrado: Contadora"


def test_ejecutar_registrar_incluye_id_y_mensaje():
    cur = FakeCursor(row={"id": 42})
    r = acciones.ejecutar(cur, "registrar_gasto",
                          {"descripcion": "Luz", "monto": 185000.0, "fecha": "2026-06-30",
                           "proveedor": None, "categoria": None})
    assert r["id"] == 42
    assert "Gasto registrado" in r["mensaje"]


def test_ejecutar_editar_devuelve_resultado_uniforme():
    cur = FakeCursor(row={"descripcion": "Luz actualizada"})
    r = acciones.ejecutar(cur, "editar_gasto", {"id": 3, "cambios": {"monto": 200000}})
    assert "mensaje" in r


def test_ejecutar_marcar_pagado_devuelve_resultado_uniforme():
    cur = FakeCursor(row={"descripcion": "Contadora"})
    r = acciones.ejecutar(cur, "marcar_gasto_pagado", {"id": 7, "fecha_pago": "2026-06-20"})
    assert "mensaje" in r
