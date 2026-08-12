# tests/test_wiki_productos_canonicos.py
"""La wiki agrupa por cerveza, no por cómo se escribió el ítem en la factura.

El productor escribe el nombre a mano en cada factura, así que la misma cerveza
aparece con 123 descripciones distintas. Agrupando por el texto crudo:

- `Barril 30L Stout Cafe` (38 unidades) y `Barril 30L Sout Cafe` (29) generan
  DOS páginas de producto en la wiki, para la misma cerveza;
- `Botella 330cc Cream Ale` (340) y `Botella 330c Cream Ale` (24), lo mismo;
- el top de un cliente se reparte entre variantes: medido, 1 de 68 clientes
  tiene un producto #1 distinto al real.

La fuente canónica es `v_ingreso_producto`, que ya trae el nombre normalizado.
Si la atribución todavía no está calculada, la wiki cae al camino anterior: es
preferible una agrupación imperfecta a no generar la wiki.
"""
from scripts import wiki_update


class FakeCursor:
    """Cursor de tuplas (el estilo que usa wiki_update). Un lote por query."""

    def __init__(self, *lotes):
        self._lotes = list(lotes)
        self._actual = []
        self.sql_ejecutado = []

    def execute(self, sql, params=None):
        self.sql_ejecutado.append(" ".join(str(sql).split()))
        self._actual = self._lotes.pop(0) if self._lotes else []

    def fetchall(self):
        return self._actual

    def fetchone(self):
        return self._actual[0] if self._actual else None


# ─── Detección de la capa de atribución ──────────────────────────────────────

def test_usa_la_vista_canonica_cuando_existe():
    cur = FakeCursor([(1,)])

    assert wiki_update.hay_atribucion(cur) is True
    assert "v_ingreso_producto" in cur.sql_ejecutado[0]


def test_sin_atribucion_calculada_no_se_cae():
    """La wiki tiene que poder generarse igual: es la memoria del negocio."""
    assert wiki_update.hay_atribucion(FakeCursor([(0,)])) is False


def test_si_la_vista_no_existe_tampoco_se_cae():
    class CursorQueFalla:
        def execute(self, *a, **k):
            raise Exception("relation does not exist")

    assert wiki_update.hay_atribucion(CursorQueFalla()) is False


# ─── Agrupación por cerveza ──────────────────────────────────────────────────

def test_top_productos_de_un_cliente_agrupa_por_cerveza():
    cur = FakeCursor([(1,)], [("Cream Ale", 30.0), ("Scotch Ale", 12.0)])

    r = wiki_update.top_productos_cliente(cur, "77.126.823-4", limite=3)

    assert r == [{"nombre": "Cream Ale", "cantidad": 30.0},
                 {"nombre": "Scotch Ale", "cantidad": 12.0}]
    assert "v_ingreso_producto" in cur.sql_ejecutado[1]


def test_sin_atribucion_cae_al_nombre_crudo():
    cur = FakeCursor([(0,)], [("Barril 30L Cream Ale", 30.0)])

    r = wiki_update.top_productos_cliente(cur, "77.126.823-4", limite=3)

    assert r == [{"nombre": "Barril 30L Cream Ale", "cantidad": 30.0}]
    assert "productos" in cur.sql_ejecutado[1]
    assert "v_ingreso_producto" not in cur.sql_ejecutado[1]


def test_las_paginas_de_producto_salen_de_las_cervezas_canonicas():
    """Una página por cerveza, no una por errata."""
    cur = FakeCursor([(1,)], [("Cream Ale", 1068.0), ("Stout Café", 99.0)])

    assert wiki_update.productos_destacados(cur, limite=15) == \
           ["Cream Ale", "Stout Café"]


def test_los_compradores_de_una_cerveza_incluyen_todas_sus_variantes():
    """`Sout Cafe` y `Stout Cafe` son la misma cerveza: quien compró cualquiera
    de las dos tiene que aparecer una sola vez, con el total sumado."""
    cur = FakeCursor([(1,)], [("BAR UNO", "77-1", 40.0)])

    r = wiki_update.compradores_de(cur, "Stout Café", limite=10)

    assert r == [("BAR UNO", "77-1", 40.0)]
    assert "v_ingreso_producto" in cur.sql_ejecutado[1]
