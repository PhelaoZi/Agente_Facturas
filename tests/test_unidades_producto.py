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


# ─── Litros: unidades de formatos distintos no se suman ──────────────────────
# Medido el 2026-08-16: el agente respondió "Cream Ale 156 unidades (120
# botellas + 36 barriles)" y "Scotch Ale 94". Las cifras eran correctas y la
# conclusión no: en litros Stout Café (394) le gana a Scotch Ale (327), o sea el
# ranking se da vuelta. Sumar botellas con barriles es aritmética válida sobre
# cosas que no son comparables.

def test_trae_los_litros_ademas_de_las_unidades():
    cur = FakeCursor([
        {"cerveza": "Cream Ale", "formato": "barril", "unidades": 36,
         "litros": 1080.0, "documentos": 16, "ultima": "2026-07-31"},
        {"cerveza": "Cream Ale", "formato": "botella", "unidades": 120,
         "litros": 39.6, "documentos": 4, "ultima": "2026-07-28"},
    ])

    r = up.ranking(cur)

    assert r["productos"][0]["litros"] == 1080.0
    assert r["productos"][1]["litros"] == 39.6
    assert r["total_litros"] == 1119.6


def test_suma_litros_derecho_porque_la_vista_ya_trae_el_total():
    """`v_lineas_producto.litros` es el total de la LINEA (cantidad x volumen),
    no el tamano del envase. Ese cambio existe porque el modelo escribio
    `SUM(litros)` con la definicion anterior y le dio 480 L donde eran 1.080:
    ignoraba la cantidad. La consulta ingenua tiene que ser la correcta.

    Si esto vuelve a `SUM(cantidad * litros)`, el volumen se multiplica al
    cuadrado."""
    cur = FakeCursor()
    up.ranking(cur)

    assert "SUM(litros)" in cur.sql
    assert "cantidad * litros" not in cur.sql


# ─── Lo que devuelve ─────────────────────────────────────────────────────────

def test_junta_las_erratas_en_una_sola_fila():
    """Las 96 + 24 unidades que el chat mostraba en dos filas son 120."""
    cur = FakeCursor([
        {"cerveza": "Cream Ale", "formato": "botella", "unidades": 120,
         "litros": 39.6, "documentos": 8, "ultima": "2026-07-28"},
        {"cerveza": "Cream Ale", "formato": "barril", "unidades": 36,
         "litros": 1080.0, "documentos": 30, "ultima": "2026-07-31"},
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


# ─── Corte mensual ───────────────────────────────────────────────────────────
# Existe porque sin él, ante "informe por producto por cada mes del 2026", el
# modelo se fue a escribir SQL — y ahí sumó `litros` sin multiplicar por la
# cantidad. Cada pregunta frecuente sin herramienta es una consulta improvisada.

def test_por_mes_abre_una_fila_por_mes():
    cur = FakeCursor()
    up.ranking(cur, por_mes=True)

    assert "to_char(fecha, 'YYYY-MM') AS mes" in cur.sql
    assert "GROUP BY to_char(fecha, 'YYYY-MM'), cerveza, formato" in cur.sql


def test_sin_por_mes_no_aparece_la_columna():
    """El caso normal es el agregado; el mes es lo excepcional."""
    cur = FakeCursor()
    up.ranking(cur)

    assert "AS mes" not in cur.sql


def test_por_mes_ordena_cronologicamente_y_devuelve_el_mes():
    cur = FakeCursor([
        {"mes": "2026-06", "cerveza": "Cream Ale", "formato": "barril",
         "unidades": 27, "litros": 810.0, "documentos": 13, "ultima": "2026-06-30"},
        {"mes": "2026-07", "cerveza": "Cream Ale", "formato": "barril",
         "unidades": 36, "litros": 1080.0, "documentos": 16, "ultima": "2026-07-31"},
    ])

    r = up.ranking(cur, por_mes=True)

    assert [p["mes"] for p in r["productos"]] == ["2026-06", "2026-07"]
    assert r["total_litros"] == 1890.0


@pytest.mark.parametrize("valor", [None, 0, ""])
def test_sin_filtros_no_agrega_condiciones_de_mas(valor):
    """Un filtro vacío no puede convertirse en `WHERE cerveza ILIKE '%%'`
    silencioso ni en un rango de fechas inventado."""
    cur = FakeCursor()
    up.ranking(cur, desde=valor, hasta=valor, cerveza=valor)

    assert "fecha >=" not in cur.sql and "fecha <=" not in cur.sql
    assert "cerveza ILIKE" not in cur.sql
