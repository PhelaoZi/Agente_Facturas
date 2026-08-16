# -*- coding: utf-8 -*-
"""Unidades vendidas por cerveza, con el nombre ya traducido.

Existe porque el agente NO tenía herramienta para esto. `ingreso_producto` es de
dinero y `ventas_producto` es el detalle de un producto, así que ante "cuántas
unidades vendí por producto en julio vs junio" el modelo escribía SQL a mano
sobre `productos` — y agrupaba por `nombre_producto`.

Resultado medido el 2026-08-16: una tabla de 17 filas con `Botella 330cc Cream
Ale` (96) y `Botella 330c Cream Ale` (24) como productos distintos, y `Barril
30L APA` dos veces (una tiene doble espacio). Prohibirlo en el prompt no
alcanza: mientras la pregunta no tenga herramienta, el modelo va a improvisar
SQL. La forma de que no agrupe mal es que no tenga que escribir la consulta.

Sale de `v_lineas_producto` y no de `v_ingreso_producto`: para UNIDADES hay que
contar también las líneas de documentos que la atribución rechazó (el folio 4019
tiene 2 barriles y 24 latas que existen aunque su plata no se pueda repartir).
"""
import pytest

from app.negocio import unidades_producto as up


class FakeCursor:
    """Cursor estilo RealDictCursor que además guarda el SQL y los params."""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.params = params

    def fetchall(self):
        return self._rows


# ─── De dónde salen los datos ────────────────────────────────────────────────

def test_sale_de_la_vista_canonica_y_agrupa_por_cerveza():
    cur = FakeCursor()
    up.ranking(cur)

    assert "v_lineas_producto" in cur.sql
    assert "GROUP BY cerveza, formato" in cur.sql
    assert "nombre_producto" not in cur.sql, "agrupar por el nombre crudo es el bug"


def test_excluye_logistica_envases_y_co2_por_clase_no_por_ilike():
    """`clase` es una columna; los ILIKE son una expresión que hay que recordar
    escribir bien, y por eso se escribían mal."""
    cur = FakeCursor()
    up.ranking(cur)

    assert "clase = 'cerveza'" in cur.sql
    assert "ILIKE" not in cur.sql.upper()


def test_las_notas_de_credito_restan_unidades():
    """Una NC devuelve mercadería: si suma, las unidades quedan infladas."""
    cur = FakeCursor()
    up.ranking(cur)

    assert "tipo_documento" in cur.sql and "61" in cur.sql


# ─── Lo que devuelve ─────────────────────────────────────────────────────────

def test_junta_las_erratas_en_una_sola_fila():
    """Las 96 + 24 unidades que el chat mostraba en dos filas son 120."""
    cur = FakeCursor([
        {"cerveza": "Cream Ale", "formato": "botella", "unidades": 120,
         "documentos": 8, "ultima": "2026-07-28"},
        {"cerveza": "Cream Ale", "formato": "barril", "unidades": 36,
         "documentos": 30, "ultima": "2026-07-31"},
    ])

    r = up.ranking(cur, desde="2026-07-01", hasta="2026-07-31")

    assert [f["cerveza"] for f in r["productos"]] == ["Cream Ale", "Cream Ale"]
    assert r["productos"][0]["unidades"] == 120.0
    assert r["productos"][0]["formato"] == "botella"


def test_declara_el_periodo_como_todas_las_cifras_del_proyecto():
    """Una cifra sin período se lee como si fuera del mes."""
    sin = up.ranking(FakeCursor())
    assert "histórico" in sin["alcance"]

    con = up.ranking(FakeCursor(), desde="2026-07-01", hasta="2026-07-31")
    assert "2026-07-01" in con["alcance"] and "2026-07-31" in con["alcance"]


def test_el_filtro_de_fechas_va_parametrizado():
    cur = FakeCursor()
    up.ranking(cur, desde="2026-07-01", hasta="2026-07-31")

    assert "2026-07-01" in cur.params and "2026-07-31" in cur.params
    assert "2026-07-01" not in cur.sql, "la fecha nunca se interpola en el SQL"


def test_se_puede_pedir_una_sola_cerveza():
    cur = FakeCursor()
    up.ranking(cur, cerveza="Cream")

    assert "cerveza ILIKE" in cur.sql
    assert "%Cream%" in cur.params


@pytest.mark.parametrize("valor", [None, 0, ""])
def test_sin_filtros_no_agrega_condiciones_de_mas(valor):
    """Un filtro vacío no puede convertirse en `WHERE cerveza ILIKE '%%'`
    silencioso ni en un rango de fechas inventado."""
    cur = FakeCursor()
    up.ranking(cur, desde=valor, hasta=valor, cerveza=valor)

    assert "fecha >=" not in cur.sql and "fecha <=" not in cur.sql
    assert "cerveza ILIKE" not in cur.sql
