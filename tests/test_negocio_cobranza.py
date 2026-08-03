# tests/test_negocio_cobranza.py
from datetime import date, timedelta

import pytest

from app.negocio import cobranza


class FakeCursor:
    """Cursor falso estilo RealDictCursor. Igual que en test_negocio_gastos,
    pero con una cola de filas para funciones que hacen varios execute
    (marcar_factura_pagada: SELECT y luego UPDATE)."""

    def __init__(self, row=None, rows_queue=None, filas=None):
        # rows_queue: lista de resultados de fetchone, uno por execute
        self._queue = list(rows_queue) if rows_queue is not None else [row]
        self._row = None
        self._filas = filas or []
        self.sqls = []
        self.params_list = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)
        self.params_list.append(params)
        self._row = self._queue.pop(0) if self._queue else None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._filas


FACTURA = {"folio": 4664, "fecha": "2026-06-15", "razon_social": "BOTILLERIA X",
           "rut_cliente": "77126823-4", "total": 69990.0, "fecha_pago": None}


# --- validar_marcar_pagada (pura) ---

def test_validar_fecha_por_defecto_hoy():
    r = cobranza.validar_marcar_pagada({"folio": 4664})
    assert r == {"folio": 4664, "fecha_pago": date.today().isoformat()}


def test_validar_acepta_folio_string_y_fecha_explicita():
    r = cobranza.validar_marcar_pagada({"folio": "4664", "fecha_pago": "2026-07-03"})
    assert r == {"folio": 4664, "fecha_pago": "2026-07-03"}


def test_validar_rechaza_folio_malo():
    for malo in ({"folio": "abc"}, {"folio": 0}, {"folio": -3}, {}):
        with pytest.raises(ValueError):
            cobranza.validar_marcar_pagada(malo)


def test_validar_rechaza_fecha_mala():
    with pytest.raises(ValueError):
        cobranza.validar_marcar_pagada({"folio": 4664, "fecha_pago": "03/07/2026"})


def test_validar_rechaza_fecha_futura():
    manana = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        cobranza.validar_marcar_pagada({"folio": 4664, "fecha_pago": manana})


# --- obtener_factura ---

def test_obtener_factura_devuelve_dict_y_excluye_nc():
    cur = FakeCursor(row=FACTURA)
    r = cobranza.obtener_factura(cur, 4664)
    assert r["razon_social"] == "BOTILLERIA X"
    assert cur.params_list[0] == (4664,)
    sql = cur.sqls[0]
    assert "!= 61" in sql                       # nunca marcar una NC
    assert "COALESCE" in sql                    # total real (montos ajustados)


def test_obtener_factura_inexistente_devuelve_none():
    assert cobranza.obtener_factura(FakeCursor(row=None), 9999) is None


# --- marcar_factura_pagada ---

def test_marcar_factura_pagada_actualiza_fecha_pago():
    cur = FakeCursor(rows_queue=[FACTURA, {"folio": 4664}])
    r = cobranza.marcar_factura_pagada(cur, 4664, "2026-07-03")
    assert r["folio"] == 4664
    assert r["cliente"] == "BOTILLERIA X"
    assert "pagada" in r["mensaje"]
    update_sql = cur.sqls[1]
    assert "UPDATE ventas" in update_sql and "fecha_pago" in update_sql
    assert "!= 61" in update_sql
    assert cur.params_list[1] == ("2026-07-03", 4664)


def test_marcar_factura_inexistente_lanza_valueerror():
    with pytest.raises(ValueError, match="4664"):
        cobranza.marcar_factura_pagada(FakeCursor(row=None), 4664, "2026-07-03")


def test_marcar_factura_ya_pagada_lanza_valueerror_con_fecha():
    pagada = {**FACTURA, "fecha_pago": "2026-06-20"}
    cur = FakeCursor(rows_queue=[pagada])
    with pytest.raises(ValueError, match="2026-06-20"):
        cobranza.marcar_factura_pagada(cur, 4664, "2026-07-03")
    # No debe haber intentado el UPDATE
    assert len(cur.sqls) == 1


# --- validar_corregir_fecha_pago (pura) ---

def test_validar_corregir_exige_fecha_explicita():
    # Una corrección sin fecha no tiene sentido: no hay default a hoy
    with pytest.raises(ValueError):
        cobranza.validar_corregir_fecha_pago({"folio": 4686})


def test_validar_corregir_acepta_folio_y_fecha():
    r = cobranza.validar_corregir_fecha_pago({"folio": "4686", "fecha_pago": "2026-06-30"})
    assert r == {"folio": 4686, "fecha_pago": "2026-06-30"}


def test_validar_corregir_rechaza_fecha_futura_o_mala():
    manana = (date.today() + timedelta(days=1)).isoformat()
    for mala in (manana, "30/06/2026"):
        with pytest.raises(ValueError):
            cobranza.validar_corregir_fecha_pago({"folio": 4686, "fecha_pago": mala})


# --- corregir_fecha_pago ---

def test_corregir_fecha_pago_actualiza_y_muestra_anterior():
    pagada = {**FACTURA, "fecha_pago": "2026-06-09"}
    cur = FakeCursor(rows_queue=[pagada, None])
    r = cobranza.corregir_fecha_pago(cur, 4664, "2026-06-30")
    assert r["folio"] == 4664
    assert r["fecha_anterior"] == "2026-06-09"
    assert "2026-06-09" in r["mensaje"] and "2026-06-30" in r["mensaje"]
    update_sql = cur.sqls[1]
    assert "UPDATE ventas" in update_sql and "!= 61" in update_sql
    assert cur.params_list[1] == ("2026-06-30", 4664)


def test_corregir_factura_no_pagada_lanza_valueerror():
    cur = FakeCursor(rows_queue=[FACTURA])  # fecha_pago None
    with pytest.raises(ValueError, match="no está marcada como pagada"):
        cobranza.corregir_fecha_pago(cur, 4664, "2026-06-30")
    assert len(cur.sqls) == 1  # sin UPDATE


def test_corregir_con_la_misma_fecha_lanza_valueerror():
    pagada = {**FACTURA, "fecha_pago": "2026-06-30"}
    cur = FakeCursor(rows_queue=[pagada])
    with pytest.raises(ValueError, match="ya tiene"):
        cobranza.corregir_fecha_pago(cur, 4664, "2026-06-30")


def test_corregir_factura_inexistente_lanza_valueerror():
    with pytest.raises(ValueError, match="9999"):
        cobranza.corregir_fecha_pago(FakeCursor(row=None), 9999, "2026-06-30")


# ── Castigo de deuda incobrable (cliente quebrado) ────────────────────────────
# `clientes.estado = 'incobrable'` ya saca la deuda del "por cobrar" en el
# dashboard, el brief, la wiki y la nube. Lo que faltaba era poder ESCRIBIRLO
# sin entrar a SQL a mano. Ojo: castigar NO es cobrar — `ventas.fecha_pago`
# queda intacta en NULL, porque la factura de verdad nunca se pagó.

CLIENTE = {"rut_cliente": "76861668-K", "razon_social": "BIER BAR SPA",
           "estado": "activo", "deuda": 188750.0, "n_facturas": 1}


# --- validar_rut_cliente (pura) ---

def test_validar_rut_normaliza_puntos_espacios_y_minusculas():
    r = cobranza.validar_rut_cliente({"rut_cliente": " 76.861.668-k "})
    assert r == {"rut_cliente": "76861668-K"}


def test_validar_rut_rechaza_vacio_o_con_formato_invalido():
    for malo in ({}, {"rut_cliente": ""}, {"rut_cliente": "   "},
                 {"rut_cliente": "BIER BAR"}, {"rut_cliente": "76861668"}):
        with pytest.raises(ValueError):
            cobranza.validar_rut_cliente(malo)


# --- buscar_clientes (para resolver un nombre a un RUT) ---

def test_buscar_clientes_busca_por_nombre_o_rut():
    cur = FakeCursor(filas=[CLIENTE])
    r = cobranza.buscar_clientes(cur, "bier bar")
    assert r == [CLIENTE]
    assert cur.params_list[0] == ("%bier bar%", "%bier bar%")


def test_buscar_clientes_sin_coincidencias_devuelve_lista_vacia():
    assert cobranza.buscar_clientes(FakeCursor(filas=[]), "nadie") == []


# --- obtener_cliente ---

def test_obtener_cliente_trae_estado_y_deuda_pendiente():
    cur = FakeCursor(row=CLIENTE)
    r = cobranza.obtener_cliente(cur, "76861668-K")
    assert r["razon_social"] == "BIER BAR SPA"
    assert r["deuda"] == 188750.0
    assert cur.params_list[0] == ("76861668-K",)
    sql = cur.sqls[0]
    assert "!= 61" in sql                      # la deuda no cuenta NC
    assert "fecha_pago IS NULL" in sql         # solo lo que sigue pendiente


def test_obtener_cliente_inexistente_devuelve_none():
    assert cobranza.obtener_cliente(FakeCursor(row=None), "99999999-9") is None


# --- marcar_cliente_incobrable ---

def test_marcar_incobrable_actualiza_estado_y_no_toca_las_facturas():
    cur = FakeCursor(rows_queue=[CLIENTE, None])
    r = cobranza.marcar_cliente_incobrable(cur, "76861668-K")

    assert r["cliente"] == "BIER BAR SPA"
    assert r["deuda_castigada"] == 188750.0
    assert r["n_facturas"] == 1
    assert "UPDATE clientes" in cur.sqls[1]
    assert cur.params_list[1] == ("incobrable", "76861668-K")
    # La factura NO se marca pagada: castigar no es cobrar.
    assert not any(s.strip().startswith("UPDATE ventas") for s in cur.sqls)


def test_marcar_incobrable_cliente_inexistente_lanza_valueerror():
    with pytest.raises(ValueError, match="99999999-9"):
        cobranza.marcar_cliente_incobrable(FakeCursor(row=None), "99999999-9")


def test_marcar_incobrable_dos_veces_lanza_valueerror():
    ya = {**CLIENTE, "estado": "incobrable"}
    cur = FakeCursor(rows_queue=[ya])
    with pytest.raises(ValueError, match="ya está marcado como incobrable"):
        cobranza.marcar_cliente_incobrable(cur, "76861668-K")
    assert len(cur.sqls) == 1  # sin UPDATE


# --- reactivar_cliente (deshacer) ---

def test_reactivar_cliente_vuelve_a_activo():
    ya = {**CLIENTE, "estado": "incobrable"}
    cur = FakeCursor(rows_queue=[ya, None])
    r = cobranza.reactivar_cliente(cur, "76861668-K")

    assert r["cliente"] == "BIER BAR SPA"
    assert "UPDATE clientes" in cur.sqls[1]
    assert cur.params_list[1] == ("activo", "76861668-K")


def test_reactivar_cliente_que_ya_estaba_activo_lanza_valueerror():
    cur = FakeCursor(rows_queue=[CLIENTE])
    with pytest.raises(ValueError, match="no está marcado como incobrable"):
        cobranza.reactivar_cliente(cur, "76861668-K")
    assert len(cur.sqls) == 1


def test_reactivar_cliente_inexistente_lanza_valueerror():
    with pytest.raises(ValueError, match="99999999-9"):
        cobranza.reactivar_cliente(FakeCursor(row=None), "99999999-9")
