# tests/test_linea_canonica.py
"""La traducción nombre-escrito → cerveza, disponible desde SQL.

Christian escribe el nombre a mano en cada factura. Medido el 2026-08-16: **125
formas distintas de escribir**, de las cuales 84 son cerveza y colapsan en 27
cervezas reales. `Barril 30L Cream Ale` (360 veces), `Barril 30L Cream  Ale`
(doble espacio, 11), `Barril  30L Cream Ale` (1), `Botella 330c`, `Botella 33cc`,
`Sout Cafe`, `Baril`, `Balck IPA`, `Stcotch Ale`…

`clasificacion_lineas.py` ya resuelve todo eso, pero vive en Python: cualquier
consulta SQL vuelve al nombre crudo. Por eso el chat mostró `Barril 30L APA` dos
veces en la misma tabla — una de las dos tiene doble espacio.

Esta capa materializa esa traducción para que **cualquier** consulta agrupe bien
sin acordarse de nada. No corrige `productos`: esa tabla es lo que dice el
documento tributario, y reescribirla sería falsificar la evidencia. Se traduce
al consultar, no se altera el original.
"""
from datetime import date

import pytest

from scripts import calcular_atribucion as ca


def _doc(folio, neto, ila, lineas, tipo=33, fecha=date(2026, 6, 1)):
    return {"tipo_documento": tipo, "folio": folio, "fecha": fecha,
            "monto_neto": neto, "impuesto_adicional": ila, "lineas": lineas}


def _linea(nombre, total, id_linea=1, cantidad=1):
    return {"id": id_linea, "nombre_producto": nombre,
            "cantidad": cantidad, "total_linea": total}


def _por_id(lote):
    return {f["linea_id"]: f for f in lote["canonicos"]}


# ─── Lo que produce ──────────────────────────────────────────────────────────

def test_las_erratas_de_una_misma_cerveza_dan_el_mismo_nombre():
    """Las tres formas de escribir Cream Ale en barril tienen que colapsar."""
    lote = ca.calcular([_doc(1, 166_110, 12_300, [
        _linea("Barril 30L Cream Ale", 20_000, id_linea=1),
        _linea("Barril 30L Cream  Ale", 20_000, id_linea=2),
        _linea("Barril  30L Cream Ale", 20_000, id_linea=3),
    ])])

    filas = _por_id(lote)
    assert {f["cerveza"] for f in filas.values()} == {"Cream Ale"}
    assert {f["formato"] for f in filas.values()} == {"barril"}
    # El nombre escrito NO se toca: es lo que dice el documento tributario.
    assert filas[2]["nombre_producto"] == "Barril 30L Cream  Ale"


def test_cada_linea_declara_su_clase():
    """`clase` es lo que reemplaza a los filtros con ILIKE '%logist%' repartidos
    por todo el código: una columna, no una expresión que hay que recordar."""
    lote = ca.calcular([_doc(2, 100_000, 4_100, [
        _linea("Barril 30L Cream Ale", 20_000, id_linea=10),
        _linea("Logistica", 35_370, id_linea=11),
        _linea("Barril Pet 30L", 16_000, id_linea=12),
        _linea("Carga CO2 9 kg", 12_000, id_linea=13),
    ])])

    clases = {f["linea_id"]: f["clase"] for f in lote["canonicos"]}
    assert clases == {10: "cerveza", 11: "logistica", 12: "envase", 13: "co2"}


def test_cubre_TODAS_las_lineas_aunque_el_documento_no_se_atribuya():
    """La atribución rechaza documentos enteros; la traducción de nombres no
    tiene por qué perderse con ellos. Si se perdiera, las unidades del documento
    rechazado tampoco se podrían contar."""
    lineas = [_linea("Barril 20L Black IPA", 60_000, id_linea=20),
              _linea("Lata 470cc Mincay", 9_000, id_linea=21)]
    lote = ca.calcular([_doc(4019, 214_528, 15_646, lineas)])

    assert lote["documentos"][0]["estado"] == "no_atribuido"
    assert lote["lineas"] == []                      # no se atribuyó nada
    assert len(lote["canonicos"]) == 2               # pero los nombres sí están


def test_una_linea_que_no_se_reconoce_se_marca_y_no_se_inventa():
    """El aviso que habría cazado "Stcotch Ale" el día que se facturó, en vez de
    cuatro días después persiguiendo otra cosa."""
    lote = ca.calcular([_doc(3, 55_370, 4_100,
                             [_linea("Barril 30L Cerveza Nueva XYZ", 20_000, id_linea=30)])])

    fila = _por_id(lote)[30]
    assert fila["clase"] == "desconocida"
    assert fila["cerveza"] is None, "nunca se adivina una cerveza"


def test_la_sour_ambigua_se_resuelve_con_la_fecha_del_documento():
    """`Barril 20L Sour` a secas solo se puede resolver por cuándo se vendió: la
    clasificación tiene que recibir la fecha, no solo el texto."""
    lote = ca.calcular([_doc(4232, 55_370, 4_100,
                             [_linea("Barril 20L Sour", 20_000, id_linea=40)],
                             fecha=date(2025, 2, 19))])

    assert _por_id(lote)[40]["cerveza"] == "Sour Frambuesa/Lima"


def test_el_informe_avisa_los_nombres_que_no_reconoce():
    """`Barril 30L Stcotch Ale` se facturó el 12-ago y lo descubrimos el 16,
    de casualidad, persiguiendo otra cosa. Mientras tanto ese documento entero
    quedó fuera del dinero por cerveza, en silencio.

    El aviso va en el informe que corre en cada importación: es el único momento
    en que alguien está mirando.
    """
    lote = ca.calcular([_doc(3, 55_370, 4_100, [
        _linea("Barril 30L Cream Ale", 20_000, id_linea=1),
        _linea("Barril 30L Cerveza Nueva XYZ", 20_000, id_linea=2),
    ])])

    texto = ca.informe(lote)

    assert "Cerveza Nueva XYZ" in texto
    assert "Cream Ale" not in texto, "solo se reportan las que NO se reconocen"


def test_sin_nombres_nuevos_el_informe_no_dice_nada():
    """El caso normal es que no haya ninguno: si aparece siempre una sección
    vacía, se vuelve ruido y se deja de leer."""
    lote = ca.calcular([_doc(1, 55_370, 4_100,
                             [_linea("Barril 30L Cream Ale", 20_000)])])

    assert "no reconoc" not in ca.informe(lote).lower()


# ─── La escritura ────────────────────────────────────────────────────────────

class CursorFalso:
    def __init__(self):
        self.ejecutado = []

    def execute(self, sql, params=None):
        self.ejecutado.append(" ".join(str(sql).split()))

    def fetchone(self):
        return [0]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_materializar_borra_lo_anterior_antes_de_escribir(monkeypatch):
    """Es una capa derivada: se recalcula entera, no se actualiza de a pedazos.
    Un DELETE que no corra deja filas de cervezas que ya no existen."""
    cur = CursorFalso()
    monkeypatch.setattr(ca, "execute_values", lambda c, sql, filas, **k: None)

    lote = ca.calcular([_doc(1, 55_370, 4_100,
                             [_linea("Barril 30L Cream Ale", 20_000)])])
    ca.materializar(cur, lote)

    borrados = [s for s in cur.ejecutado if s.startswith("DELETE")]
    assert any("linea_canonica" in s for s in borrados)
