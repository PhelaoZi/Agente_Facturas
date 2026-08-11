# tests/test_sync_evidencia_dte.py
"""El sync debe escribir la evidencia completa del DTE, no solo lo que se usa.

Segunda mitad del paso 2 (docs/debate-arquitectura/10-...): `parse_dte.py` ya
conserva las líneas completas, los descuentos globales, los impuestos con su
tasa y el código de impuesto por línea. Acá se comprueba que eso llegue a la
base y no se quede en el `changes.json`.

Reutiliza el `FakeCursor` de test_negocio_importador: registra el SQL sin tocar
PostgreSQL.
"""
import ast
import hashlib

from scripts import parse_dte, sync_db
from tests.test_negocio_importador import FakeCursor, xml_venta
from tests.test_parse_dte_evidencia import xml_folio_4746


def _documentos_4746():
    return parse_dte.parsear_contenido(xml_folio_4746())


def _sincronizar(documentos, **kwargs):
    cur = FakeCursor()
    sync_db.sincronizar_en_cursor(cur, documentos, **kwargs)
    return cur


def _filas(cur, tabla):
    """Devuelve las tuplas insertadas en una tabla.

    `execute_values` no pasa parámetros: arma el SQL con los valores adentro
    usando `mogrify`, que en el FakeCursor es `repr(args)`. Así que las filas se
    recuperan leyendo el texto después de VALUES.
    """
    inserts = cur.inserts(tabla)
    if not inserts:
        return []
    sql = inserts[0][0]
    valores = sql.split("VALUES", 1)[1].split("ON CONFLICT", 1)[0].strip().rstrip(";")
    return ast.literal_eval(f"[{valores}]")


# ─── Las líneas completas ─────────────────────────────────────────────────────

def test_escribe_todas_las_lineas_incluida_la_logistica():
    """`productos` recibe 1 línea (sin logística) y `dte_lineas` recibe las 2.
    Esa diferencia es exactamente el ingreso que se venía perdiendo."""
    cur = _sincronizar(_documentos_4746())

    assert len(cur.inserts("productos")) == 1
    assert len(cur.inserts("dte_lineas")) == 1        # un execute_values

    filas = _filas(cur, "dte_lineas")
    assert len(filas) == 2
    assert [f[3] for f in filas] == ["Barril 30L Wee Heavy", "Logistica"]


def test_las_lineas_llevan_numero_y_codigo_de_impuesto():
    """Sin nro_linea no hay identidad de línea; sin cod_imp_adic hay que
    adivinar cuál es cerveza por el nombre."""
    filas = _filas(_sincronizar(_documentos_4746()), "dte_lineas")

    #   (tipo, folio, nro_linea, nombre, descripcion, cant, precio, total, cod)
    assert filas[0][2] == 1 and filas[0][8] == 26     # cerveza: lleva el ILA
    assert filas[1][2] == 2 and filas[1][8] is None   # logística: no lo lleva


# ─── El descuento global ──────────────────────────────────────────────────────

def test_escribe_el_descuento_global():
    filas = _filas(_sincronizar(_documentos_4746()), "dte_ajustes_globales")

    assert len(filas) == 1
    tipo, folio, nro, movimiento, glosa, tipo_valor, valor, exento = filas[0]
    assert (movimiento, tipo_valor, valor) == ("D", "$", 9000.0)
    assert glosa == "DESCUENTO GLOBAL"


def test_un_documento_sin_descuentos_no_escribe_nada_en_ajustes():
    """No insertar filas vacías: una tabla de evidencia con ruido es peor que
    una vacía."""
    documentos = parse_dte.parsear_contenido(xml_venta())

    assert _sincronizar(documentos).inserts("dte_ajustes_globales") == []


# ─── Los impuestos ────────────────────────────────────────────────────────────

def test_escribe_los_impuestos_con_su_tasa():
    """`tipo_documento` viaja como texto hasta Postgres, que lo castea a la
    columna INTEGER — igual que en `productos` desde siempre."""
    filas = _filas(_sincronizar(_documentos_4746()), "dte_impuestos")

    assert filas == [("33", 4746, 26, 20.5, 6458)]


# ─── El archivo XML de origen ─────────────────────────────────────────────────

def test_registra_el_xml_de_origen_con_su_hash():
    """Sin el XML archivado, un documento no se puede volver a auditar. Es
    justamente lo que dejó el histórico irrecuperable."""
    hash_xml = hashlib.sha256(xml_folio_4746().encode("latin-1")).hexdigest()
    archivo = {
        "nombre": "DTE_DOWN763080122026-08-02.xml",
        "hash_sha256": hash_xml,
        "ruta": "dte-archivo/DTE_DOWN763080122026-08-02.xml",
    }

    cur = _sincronizar(_documentos_4746(), archivo=archivo)
    filas = _filas(cur, "dte_archivos")

    assert filas == [("33", 4746, hash_xml,
                      "DTE_DOWN763080122026-08-02.xml",
                      "dte-archivo/DTE_DOWN763080122026-08-02.xml")]


def test_sin_archivo_el_sync_igual_funciona():
    """El importador del dashboard y los tests llaman sin archivo. La evidencia
    del detalle no puede quedar condicionada a eso."""
    cur = _sincronizar(_documentos_4746())

    assert cur.inserts("dte_archivos") == []
    assert len(cur.inserts("dte_lineas")) == 1


# ─── Compatibilidad ───────────────────────────────────────────────────────────

def test_los_folios_duplicados_no_reescriben_la_evidencia():
    """La evidencia se escribe una vez. Si el folio ya está en la base, el
    documento se omite entero: no se puede "corregir" un DTE ya emitido."""
    cur = FakeCursor(folios_existentes=[(4746, 33)])
    sync_db.sincronizar_en_cursor(cur, _documentos_4746())

    assert cur.inserts("dte_lineas") == []
    assert cur.inserts("dte_ajustes_globales") == []
    assert cur.inserts("dte_impuestos") == []
