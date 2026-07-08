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


# --- Tests para acciones de seguimiento comercial ---

def test_validar_agregar_seguimiento_enruta():
    clean = acciones.validar("agregar_seguimiento",
                             {"rut_cliente": "77-1", "motivo": "Se enfrió"})
    assert clean["rut_cliente"] == "77-1"
    assert clean["prioridad"] == "media"


def test_ejecutar_agregar_seguimiento_incluye_id_y_mensaje():
    class Cur(FakeCursor):
        def __init__(self):
            super().__init__()
            self._calls = 0

        def fetchone(self):
            self._calls += 1
            return None if self._calls == 1 else {"id": 9}

    r = acciones.ejecutar(Cur(), "agregar_seguimiento",
                          {"rut_cliente": "77-1", "motivo": "Se enfrió",
                           "prioridad": "alta", "senales": None,
                           "fecha_objetivo": None, "notas": None})
    assert r["id"] == 9
    assert "Seguimiento creado" in r["mensaje"]


def test_validar_marcar_seguimiento_enruta():
    clean = acciones.validar("marcar_seguimiento", {"id": "5", "estado": "contactado"})
    assert clean == {"id": 5, "estado": "contactado",
                     "fecha_contacto": clean["fecha_contacto"]}


def test_ejecutar_marcar_seguimiento_devuelve_mensaje():
    cur = FakeCursor(row={"rut_cliente": "77-1", "motivo": "Se enfrió"})
    r = acciones.ejecutar(cur, "marcar_seguimiento",
                          {"id": 5, "estado": "contactado", "fecha_contacto": "2026-06-24"})
    assert "mensaje" in r


# --- Tests para acciones de cobranza (marcar factura pagada) ---

def test_validar_marcar_factura_pagada_enruta():
    clean = acciones.validar("marcar_factura_pagada",
                             {"folio": "4664", "fecha_pago": "2026-07-03"})
    assert clean == {"folio": 4664, "fecha_pago": "2026-07-03"}


def test_ejecutar_marcar_factura_pagada_devuelve_mensaje():
    factura = {"folio": 4664, "fecha": "2026-06-15", "fecha_pago": None,
               "rut_cliente": "77-1", "razon_social": "BOTILLERIA X", "total": 69990}
    cur = FakeCursor(row=factura)
    r = acciones.ejecutar(cur, "marcar_factura_pagada",
                          {"folio": 4664, "fecha_pago": "2026-07-03"})
    assert r["folio"] == 4664
    assert "pagada" in r["mensaje"]


def test_validar_corregir_fecha_pago_enruta():
    clean = acciones.validar("corregir_fecha_pago",
                             {"folio": "4686", "fecha_pago": "2026-06-30"})
    assert clean == {"folio": 4686, "fecha_pago": "2026-06-30"}


def test_ejecutar_corregir_fecha_pago_devuelve_mensaje():
    factura = {"folio": 4686, "fecha": "2026-05-15", "fecha_pago": "2026-06-09",
               "rut_cliente": "77-2", "razon_social": "VDT SPA", "total": 100000}
    cur = FakeCursor(row=factura)
    r = acciones.ejecutar(cur, "corregir_fecha_pago",
                          {"folio": 4686, "fecha_pago": "2026-06-30"})
    assert r["fecha_anterior"] == "2026-06-09"
    assert "corregida" in r["mensaje"]
