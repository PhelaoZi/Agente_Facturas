# tests/test_calcular_atribucion.py
"""Materialización de la atribución de ingreso.

El motor (`app/negocio/atribucion_ingreso.py`) ya está probado documento por
documento. Acá se prueba lo que rodea a esa cuenta: que el lote entero cuadre
antes de escribir nada, y que la capa se pueda recalcular de cero sin miedo.

La regla de fondo es la misma que dentro del motor, subida un nivel: **si no
cuadra, no se publica**. Un documento a medias no se publica, y un recálculo
que no cuadra contra `ventas` tampoco.
"""
import pytest

from scripts import calcular_atribucion as ca


def _doc(folio, neto, ila, lineas, tipo=33, fecha="2026-06-01"):
    from datetime import date
    y, m, d = (int(x) for x in fecha.split("-"))
    return {"tipo_documento": tipo, "folio": folio, "fecha": date(y, m, d),
            "monto_neto": neto, "impuesto_adicional": ila, "lineas": lineas}


def _linea(nombre, total, id_linea=1, cantidad=1):
    return {"id": id_linea, "nombre_producto": nombre,
            "cantidad": cantidad, "total_linea": total}


CREAM = [_linea("Barril 30L Cream Ale", 20_000)]


# ─── Lo que produce ───────────────────────────────────────────────────────────

def test_arma_una_fila_por_cerveza_y_una_por_documento():
    lote = ca.calcular([_doc(1, 55_370, 4_100, CREAM)])

    assert len(lote["documentos"]) == 1
    assert len(lote["lineas"]) == 1
    assert lote["lineas"][0]["cerveza"] == "Cream Ale"
    assert lote["lineas"][0]["ingreso_neto_atribuido"] == 55_370


def test_un_documento_no_atribuido_igual_deja_su_fila_con_el_motivo():
    """Sin la fila, un documento que no se pudo atribuir es indistinguible de
    uno que nunca se procesó. La cobertura se calcula sobre esas filas."""
    doc = _doc(4746, 81_000, 6_458, [_linea("Barril 30L Wee Heavy", 35_000)])

    lote = ca.calcular([doc])

    assert lote["lineas"] == []
    assert lote["documentos"][0]["estado"] == "no_atribuido"
    assert lote["documentos"][0]["motivo"] == "descuento_global"


# ─── La cuadratura del lote entero ───────────────────────────────────────────

def test_el_lote_cuadra_contra_el_neto_de_los_documentos():
    lote = ca.calcular([
        _doc(1, 55_370, 4_100, CREAM),
        _doc(2, 55_370, 4_100, CREAM),
        _doc(4746, 81_000, 6_458, [_linea("Barril 30L Wee Heavy", 35_000)]),
    ])

    assert lote["cuadra"] is True
    assert lote["neto_total"] == 191_740
    assert lote["monto_atribuido"] + lote["monto_pass_through"] \
           + lote["monto_sin_atribuir"] == lote["neto_total"]


def test_la_cobertura_se_reporta_en_documentos_y_en_monto():
    """Una cobertura del 97% en documentos puede ser del 60% en plata. Se
    informan las dos o no se informa ninguna."""
    lote = ca.calcular([
        _doc(1, 55_370, 4_100, CREAM),
        _doc(4746, 81_000, 6_458, [_linea("Barril 30L Wee Heavy", 35_000)]),
    ])

    assert lote["documentos_atribuidos"] == 1
    assert lote["documentos_totales"] == 2
    assert lote["monto_atribuido"] == 55_370
    assert lote["monto_sin_atribuir"] == 81_000


def test_las_notas_de_credito_restan_una_sola_vez():
    """Modelo de eventos: la factura suma y la NC resta. No se mezcla con los
    montos ajustados, que ya descuentan la misma NC."""
    lote = ca.calcular([
        _doc(1, 55_370, 4_100, CREAM),
        _doc(910, -55_370, 4_100, CREAM, tipo=61),
    ])

    assert lote["monto_atribuido"] == 0
    assert lote["cuadra"] is True


# ─── La escritura ────────────────────────────────────────────────────────────

def test_no_escribe_nada_si_el_lote_no_cuadra():
    """Última barrera antes de la base. Si acá algo no cuadra es un error del
    motor, y publicarlo a medias sería peor que no publicar."""
    class CursorQueDelata:
        def execute(self, *a, **k):
            raise AssertionError("no debió escribir")

    lote = {"cuadra": False, "documentos": [], "lineas": []}

    with pytest.raises(ValueError, match="no cuadra"):
        ca.materializar(CursorQueDelata(), lote)


def test_recalcular_borra_lo_anterior_antes_de_escribir():
    """La capa es derivada y se recalcula entera. Si no se borrara primero,
    cada corrida duplicaría el ingreso de cada cerveza."""
    ejecutados = []

    class CursorFalso:
        def execute(self, sql, params=None):
            ejecutados.append(" ".join(str(sql).split()))
        def mogrify(self, template, args=None):
            return repr(args).encode("utf-8")
        connection = type("C", (), {"encoding": "UTF8"})()

    lote = ca.calcular([_doc(1, 55_370, 4_100, CREAM)])
    ca.materializar(CursorFalso(), lote)

    borrados = [i for i, sql in enumerate(ejecutados) if sql.startswith("DELETE")]
    insertados = [i for i, sql in enumerate(ejecutados) if "INSERT INTO" in sql]
    assert borrados, "debe borrar la versión anterior"
    assert insertados, "debe insertar la nueva"
    assert max(borrados) < min(insertados), "borrar va antes de insertar"
