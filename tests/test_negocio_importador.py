# tests/test_negocio_importador.py
"""Importación de XMLs DTE desde el dashboard.

El test que más importa es `test_xml_invalido_no_ejecuta_ningun_insert`: fija el
invariante del pipeline —nunca escribir sin validar— que en la consola protege
el flag `.changes_validated` y aquí protege el orden dentro de `importar_venta`.
"""
import pytest

from app.negocio import importador
from scripts import parse_dte, sync_compras, sync_db


# ─── Cursor falso ─────────────────────────────────────────────────────────────

class _FakeConn:
    """psycopg2.extras.execute_values consulta cur.connection.encoding."""
    encoding = "UTF8"


class FakeCursor:
    """Cursor mínimo: registra el SQL ejecutado y simula los folios ya en la BD."""

    def __init__(self, folios_existentes=(), rowcount=1):
        self.folios_existentes = list(folios_existentes)   # [(folio, tipo)]
        self.rowcount = rowcount
        self.ejecutados = []                               # [(sql, params)]
        self.connection = _FakeConn()
        self._ultimo = ""

    def execute(self, sql, params=None):
        if isinstance(sql, bytes):        # execute_values arma bytes
            sql = sql.decode("utf-8", "replace")
        self._ultimo = sql
        self.ejecutados.append((sql, params))

    def mogrify(self, template, args=None):
        # execute_values junta los mogrify y llama execute una sola vez.
        return repr(args).encode("utf-8")

    def fetchall(self):
        if "SELECT folio, tipo_documento" in self._ultimo:
            return list(self.folios_existentes)
        return []          # sin notas de crédito pendientes

    def fetchone(self):
        return None

    def inserts(self, tabla):
        return [e for e in self.ejecutados if f"INSERT INTO {tabla}" in e[0]]


# ─── XMLs de ejemplo ──────────────────────────────────────────────────────────

def xml_venta(tipo="33", folio=4743, rut_emisor=importador.RUT_EMISOR_PROPIO,
              rut_recep="77126823-4", neto=55370, iva=10520, total=65890):
    """Factura de barril con su línea de logística (estructura real de Zigurat)."""
    return f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<EnvioDTE><SetDTE><DTE><Documento ID="F{folio}T{tipo}">
<Encabezado>
<IdDoc><TipoDTE>{tipo}</TipoDTE><Folio>{folio}</Folio>
<FchEmis>2026-07-22</FchEmis><FmaPago>2</FmaPago></IdDoc>
<Emisor><RUTEmisor>{rut_emisor}</RUTEmisor>
<RznSoc>ELABORADORA Y COMERCIALIZADORA VINTAGE SPA</RznSoc></Emisor>
<Receptor><RUTRecep>{rut_recep}</RUTRecep><RznSocRecep>CLIENTE DE PRUEBA SPA</RznSocRecep>
<DirRecep>Av Prueba 742</DirRecep><CmnaRecep>Nunoa</CmnaRecep></Receptor>
<Totales><MntNeto>{neto}</MntNeto><TasaIVA>19.00</TasaIVA><IVA>{iva}</IVA>
<MntTotal>{total}</MntTotal></Totales>
</Encabezado>
<Detalle><NmbItem>Barril 30L Cream Ale</NmbItem><QtyItem>1</QtyItem>
<PrcItem>20000</PrcItem><MontoItem>20000</MontoItem></Detalle>
<Detalle><NmbItem>Logistica</NmbItem><QtyItem>1</QtyItem>
<PrcItem>35370</PrcItem><MontoItem>35370</MontoItem></Detalle>
</Documento></DTE></SetDTE></EnvioDTE>"""


def xml_nota_credito(folio=910, folio_ref=4743):
    return f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<EnvioDTE><SetDTE><DTE><Documento ID="F{folio}T61">
<Encabezado>
<IdDoc><TipoDTE>61</TipoDTE><Folio>{folio}</Folio><FchEmis>2026-07-23</FchEmis></IdDoc>
<Emisor><RUTEmisor>{importador.RUT_EMISOR_PROPIO}</RUTEmisor><RznSoc>VINTAGE SPA</RznSoc></Emisor>
<Receptor><RUTRecep>77126823-4</RUTRecep><RznSocRecep>CLIENTE DE PRUEBA SPA</RznSocRecep>
<DirRecep>Av Prueba 742</DirRecep><CmnaRecep>Nunoa</CmnaRecep></Receptor>
<Totales><MntNeto>55370</MntNeto><TasaIVA>19.00</TasaIVA><IVA>10520</IVA>
<MntTotal>65890</MntTotal></Totales>
</Encabezado>
<Referencia><TpoDocRef>33</TpoDocRef><FolioRef>{folio_ref}</FolioRef>
<RazonRef>Anula factura</RazonRef></Referencia>
<Detalle><NmbItem>Barril 30L Cream Ale</NmbItem><QtyItem>1</QtyItem>
<PrcItem>20000</PrcItem><MontoItem>20000</MontoItem></Detalle>
</Documento></DTE></SetDTE></EnvioDTE>"""


def doc_compra(rut_emisor="77755083-7", razon="BUCAREST SPA", item="MALTA UMA - PILSEN",
               precio=32000, folio=8801, fecha="2026-07-20", items=None,
               neto=320000, total=380800):
    """Un <Documento> de proveedor, sin envoltorio (para armar descargas masivas).

    `items` acepta una lista de (nombre, qty, precio, monto) para las facturas
    que mezclan varias líneas; si no viene, arma una sola con `item`/`precio`.
    """
    lineas = items if items is not None else [(item, 10, precio, neto)]
    detalle = "".join(
        f"<Detalle><NmbItem>{n}</NmbItem><QtyItem>{q}</QtyItem>"
        f"<PrcItem>{p}</PrcItem><MontoItem>{m}</MontoItem></Detalle>"
        for n, q, p, m in lineas)
    return f"""<Documento ID="F{folio}T33">
<Encabezado>
<IdDoc><TipoDTE>33</TipoDTE><Folio>{folio}</Folio><FchEmis>{fecha}</FchEmis></IdDoc>
<Emisor><RUTEmisor>{rut_emisor}</RUTEmisor><RznSoc>{razon}</RznSoc></Emisor>
<Receptor><RUTRecep>{importador.RUT_EMISOR_PROPIO}</RUTRecep></Receptor>
<Totales><MntNeto>{neto}</MntNeto><IVA>{total - neto}</IVA><MntTotal>{total}</MntTotal></Totales>
</Encabezado>
{detalle}
</Documento>"""


def envio_dte(*documentos):
    """Envuelve uno o varios <Documento> en un EnvioDTE, como lo entrega el SII."""
    return ('<?xml version="1.0" encoding="ISO-8859-1"?>\n<EnvioDTE><SetDTE>'
            + "".join(f"<DTE>{d}</DTE>" for d in documentos)
            + "</SetDTE></EnvioDTE>")


def xml_compra(**kw):
    return envio_dte(doc_compra(**kw))


def xml_compras_masivo():
    """Descarga masiva de documentos recibidos: varios proveedores en un archivo.

    Es el formato real y único en que el SII entrega las compras del período.
    """
    return envio_dte(
        doc_compra(folio=8801),                                        # Bucarest, insumo
        doc_compra(rut_emisor="76052927-3", razon="AUTOPISTA VESPUCIO SUR",
                   item="PEAJE", folio=5511),                          # gasto
        doc_compra(rut_emisor="76045387-0", razon="MUNDO CERVECERO",
                   item="MALTA CHOCOLATE", precio=2400, folio=7702),   # insumo
    )


# ─── nombre_seguro ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("malicioso", [
    "../../.env",
    "..\\..\\config.xml",
    "C:\\Windows\\System32\\algo.xml",
    "carpeta/archivo.xml",
    ".oculto.xml",
])
def test_nombre_seguro_rechaza_rutas(malicioso):
    with pytest.raises(ValueError):
        importador.nombre_seguro(malicioso)


@pytest.mark.parametrize("invalido", ["", "   ", None, "sin_extension", "factura.pdf"])
def test_nombre_seguro_rechaza_no_xml(invalido):
    with pytest.raises(ValueError):
        importador.nombre_seguro(invalido)


def test_nombre_seguro_acepta_el_nombre_del_sii():
    assert importador.nombre_seguro("DTE_DOWN763080122026-07-26 (1).xml") == \
        "DTE_DOWN763080122026-07-26 (1).xml"


def test_nombre_seguro_sanea_caracteres_raros():
    assert importador.nombre_seguro('fact*ura?.xml') == "fact_ura_.xml"


# ─── clasificar ───────────────────────────────────────────────────────────────

def test_clasifica_factura_propia_como_venta():
    r = importador.clasificar(xml_venta())
    assert r["clase"] == "venta"
    assert r["rut_emisor"] == importador.RUT_EMISOR_PROPIO


def test_clasifica_documento_61_propio_como_nota_credito():
    assert importador.clasificar(xml_nota_credito())["clase"] == "nota_credito"


def test_clasifica_emisor_ajeno_como_compra():
    r = importador.clasificar(xml_compra())
    assert r["clase"] == "compra"
    assert r["rut_emisor"] == "77755083-7"


def test_archivo_con_factura_y_nc_propias_es_venta():
    # Basta un documento que no sea 61 para que el archivo vaya al pipeline de ventas.
    mezcla = xml_venta() + xml_nota_credito()
    assert importador.clasificar(mezcla)["clase"] == "venta"


def test_clasifica_desconocido_sin_documentos():
    r = importador.clasificar("<html>esto no es un DTE</html>")
    assert r["clase"] == "desconocido"
    assert "<Documento>" in r["motivo"]


def test_clasifica_descarga_masiva_de_compras_como_compra():
    # El SII entrega los documentos RECIBIDOS de todo el período en un solo
    # archivo, con un emisor distinto por proveedor. Varios emisores ajenos es
    # la forma normal de una compra, no un archivo mal armado.
    r = importador.clasificar(xml_compras_masivo())
    assert r["clase"] == "compra"
    assert r["rut_emisor"] is None                      # no hay uno solo
    assert set(r["ruts_emisores"]) == {"77755083-7", "76052927-3", "76045387-0"}


def test_clasifica_desconocido_si_mezcla_documentos_propios_con_ajenos():
    # Esto sí es ambiguo de verdad: ventas y compras van a pipelines distintos.
    r = importador.clasificar(xml_venta() + xml_compra())
    assert r["clase"] == "desconocido"
    assert "mezcla" in r["motivo"]


# ─── importar_venta ───────────────────────────────────────────────────────────

def test_importar_venta_inserta_factura_cliente_y_productos():
    cur = FakeCursor()
    res = importador.importar_venta(cur, xml_venta(), "DTE_test.xml")

    assert res["estado"] == "ok"
    assert res["facturas"] == 1 and res["notas_credito"] == 0
    assert res["productos"] == 1          # "Logistica" no es producto del catálogo
    assert res["ruts"] == ["77126823-4"]
    assert not res["errores"]
    assert len(cur.inserts("ventas")) == 1
    assert len(cur.inserts("clientes")) == 1
    assert len(cur.inserts("productos")) == 1


def test_importar_nota_credito_se_cuenta_aparte():
    cur = FakeCursor()
    res = importador.importar_venta(cur, xml_nota_credito(), "NC_test.xml", "nota_credito")

    assert res["estado"] == "ok"
    assert res["notas_credito"] == 1 and res["facturas"] == 0
    # Debe ajustar la factura referenciada, no solo insertarse.
    assert any("monto_total_ajustado" in sql for sql, _ in cur.ejecutados)


def test_xml_invalido_no_ejecuta_ningun_insert():
    # Totales que no cuadran: la validación falla y no debe escribirse nada.
    cur = FakeCursor()
    res = importador.importar_venta(cur, xml_venta(total=999999), "DTE_malo.xml")

    assert res["estado"] == "error"
    assert any("Totales no cuadran" in e for e in res["errores"])
    assert cur.ejecutados == []


def test_rut_de_cliente_invalido_bloquea_la_escritura():
    cur = FakeCursor()
    res = importador.importar_venta(cur, xml_venta(rut_recep="ABC"), "DTE_malo.xml")

    assert res["estado"] == "error"
    assert cur.ejecutados == []


def test_archivo_sin_documentos_es_error_sin_escribir():
    cur = FakeCursor()
    res = importador.importar_venta(cur, "<html>nada</html>", "raro.xml")

    assert res["estado"] == "error"
    assert cur.ejecutados == []


def test_folio_ya_existente_se_omite_y_se_reporta():
    cur = FakeCursor(folios_existentes=[(4743, "33")])
    res = importador.importar_venta(cur, xml_venta(folio=4743), "DTE_repetido.xml")

    assert res["estado"] == "omitido"
    assert res["facturas"] == 0
    assert res["duplicados"] == [{"folio": 4743, "tipo": "33"}]
    assert cur.inserts("ventas") == []


# ─── importar_compra ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def registro_procesados(tmp_path, monkeypatch):
    """Apunta .procesados.json a un archivo temporal vacío.

    Aísla los tests del registro real del proyecto (si no, que un test pase
    dependería de qué XMLs se hayan importado en el equipo) y deja correr la
    función de verdad en vez de parchearla. Devuelve la ruta para los tests que
    necesitan sembrar un archivo ya procesado.
    """
    registro = tmp_path / ".procesados.json"
    monkeypatch.setattr(sync_compras, "LOG_PROCESADOS", registro)
    return registro


def test_importar_compra_de_insumos_actualiza_precios():
    cur = FakeCursor()
    res = importador.importar_compra(cur, xml_compra().encode("latin-1"), "compra.xml")

    assert res["estado"] == "ok"
    # Bucarest vende la malta en bolsas de 25 kg: $32.000 / 25 = $1.280 por kg.
    assert res["precios_actualizados"] == ["Malta Pilsen -> $1280.0000/unidad"]
    assert res["procesado_completo"] is True


def test_importar_compra_de_gasto_inserta_gasto_operativo():
    cur = FakeCursor()
    xml = xml_compra(rut_emisor="76052927-3", razon="AUTOPISTA VESPUCIO SUR", item="PEAJE")
    res = importador.importar_compra(cur, xml.encode("latin-1"), "peaje.xml")

    assert res["estado"] == "ok"
    assert res["gastos_insertados"] == 1
    assert len(cur.inserts("gastos_operativos")) == 1


def test_compra_ya_procesada_no_se_vuelve_a_cargar(registro_procesados):
    # La lista .procesados.json es lo único que evita que una factura antigua
    # de insumos pise el precio vigente con un UPDATE.
    registro_procesados.write_text('["compra.xml"]', encoding="utf-8")

    cur = FakeCursor()
    res = importador.importar_compra(cur, xml_compra().encode("latin-1"), "compra.xml")

    assert res["estado"] == "omitido"
    assert res["ya_procesada"] is True
    assert res["procesado_completo"] is False
    assert res["precios_actualizados"] == []
    assert cur.ejecutados == []


def test_marcar_una_compra_la_deja_registrada_para_la_proxima():
    assert importador.compra_ya_procesada("compra.xml") is False

    importador.marcar_compra_procesada("compra.xml")
    assert importador.compra_ya_procesada("compra.xml") is True

    # Marcar dos veces no duplica ni rompe el archivo.
    importador.marcar_compra_procesada("compra.xml")
    assert sync_compras._load_procesados() == {"compra.xml"}


def test_importar_compra_procesa_todos_los_proveedores_del_archivo():
    cur = FakeCursor()
    res = importador.importar_compra(cur, xml_compras_masivo().encode("latin-1"), "masivo.xml")

    assert res["estado"] == "ok"
    assert res["gastos_insertados"] == 1                         # el peaje
    assert len(res["precios_actualizados"]) == 2                 # Bucarest + Mundo Cervecero
    assert res["procesado_completo"] is True


@pytest.mark.parametrize("rut, razon, item, categoria", [
    ("76747198-K", "CENTRAL GAS SPA",   "CARGA 15 KL",          "servicios"),
    ("76676921-7", "QUEZADA Y CIA",     "Amortiguadores",       "transporte"),
    ("77983419-0", "COMERCIALIZADORA M.I.F.", "poleron",        "marketing"),
    ("76568660-1", "EASY RETAIL S.A.",  "CINTA EMBALAJE PROF",  "otros"),
])
def test_proveedores_de_gasto_van_a_su_categoria(rut, razon, item, categoria):
    cur = FakeCursor()
    xml = xml_compra(rut_emisor=rut, razon=razon, item=item)
    res = importador.importar_compra(cur, xml.encode("latin-1"), "gasto.xml")

    assert res["estado"] == "ok"
    assert res["gastos_insertados"] == 1
    _, params = cur.inserts("gastos_operativos")[0]
    assert params[5] == item            # descripción
    assert params[8] == categoria


@pytest.mark.parametrize("item, precio, esperado", [
    ("Café grano Bolivia 1kg",  18000, "Café -> $18000.0000/unidad"),
    ("Café grano Bolivia 500g",  9300, "Café -> $18600.0000/unidad"),   # /0,5 → precio por kilo
])
def test_compra_de_cafe_actualiza_el_insumo_de_la_stout(item, precio, esperado):
    # Lúdico es proveedor de insumos, no un gasto: el café entra a la receta
    # de la Stout Café/Cacao y su precio alimenta el costo del SKU.
    cur = FakeCursor()
    xml = xml_compra(rut_emisor="77380521-0", razon="LUDICO SPA", item=item, precio=precio)
    res = importador.importar_compra(cur, xml.encode("latin-1"), "cafe.xml")

    assert res["estado"] == "ok"
    assert res["precios_actualizados"] == [esperado]
    assert res["gastos_insertados"] == 0


@pytest.mark.parametrize("rut, item, precio, esperado", [
    # El nombre del proveedor trae la marca y el formato; el precio se guarda
    # siempre por unidad del maestro (kg), dividiendo por lo que trae el envase.
    ("76518077-5", "ROASTED BARLEY WEYERMANN 1 KILO", 2437,
     "Cebada Tostada -> $2437.0000/unidad"),
    ("76518077-5", "MALTA CARA 50 EBC CR CASTLE MALTING 10 KILOS", 21008,
     "Malta Cara 50 / Ruby -> $2100.8000/unidad"),
    ("76045387-0", "Malta Cara Ruby 50 Castle Malting", 2437,
     "Malta Cara 50 / Ruby -> $2437.0000/unidad"),
])
def test_maltas_con_otra_marca_mapean_al_mismo_insumo(rut, item, precio, esperado):
    cur = FakeCursor()
    xml = xml_compra(rut_emisor=rut, item=item, precio=precio)
    res = importador.importar_compra(cur, xml.encode("latin-1"), "malta.xml")

    assert res["precios_actualizados"] == [esperado]
    assert res["sin_mapeo"] == []


@pytest.mark.parametrize("item, precio, esperado", [
    # El maestro guarda el fosfórico por ml: cada formato divide por lo suyo.
    # El caso del frasco de 250 ml es el que protege el orden de ITEM_MAP —
    # con las claves al revés caería en "acido fosforico" y quedaría a $2,90.
    ("Acido Fosforico",       5462, "Fosfórico -> $5.4620/unidad"),
    ("Acido Fosforico 250ml", 2900, "Fosfórico -> $11.6000/unidad"),
    ("Desinfectante 5 kg",   22269, "Desinfectante Peracetico -> $4453.8000/unidad"),
    # Adjunto de la Wee Heavy: pote de 1 kg, el precio ya viene por kilo.
    ("AROMA EN PASTA DE PISTACHO 1KG. SEVAROME", 28500, "Pistacho -> $28500.0000/unidad"),
])
def test_limpieza_usa_el_formato_de_cada_envase(item, precio, esperado):
    cur = FakeCursor()
    xml = xml_compra(rut_emisor="76045387-0", razon="MUNDO CERVECERO",
                     item=item, precio=precio)
    res = importador.importar_compra(cur, xml.encode("latin-1"), "limpieza.xml")

    assert res["precios_actualizados"] == [esperado]
    assert res["sin_mapeo"] == []


def _factura_mixta(folio=7702, fecha="2026-07-20"):
    """Factura de proveedor de insumos que mezcla insumos con lo que no lo es."""
    return envio_dte(doc_compra(
        rut_emisor="76045387-0", razon="MUNDO CERVECERO", folio=folio, fecha=fecha,
        neto=30000, total=35700,
        items=[
            ("Malta Chocolate",                2, 2400, 24000),
            ("Llave paso plastica John Guest", 2, 2000,  4000),
            ("Servicio Molienda",              8,  250,  2000),
        ]))


def test_lo_que_no_es_insumo_se_registra_como_gasto():
    # Mundo Cervecero factura maltas junto con fittings y servicios que no son
    # insumo de receta. Antes esas líneas se botaban: no actualizaban ningún
    # precio y tampoco entraban a gastos, así que la plata desaparecía.
    cur = FakeCursor()
    res = importador.importar_compra(cur, _factura_mixta().encode("latin-1"), "mixta.xml")

    assert res["precios_actualizados"] == ["Malta Chocolate -> $2400.0000/unidad"]
    assert res["gastos_insertados"] == 1

    _, params = cur.inserts("gastos_operativos")[0]
    assert "John Guest" in params[5] and "Molienda" in params[5]
    assert params[6] == 6000        # solo las líneas sueltas, no el total de la factura
    assert params[7] == 7140        # total prorrateado (35.700 × 6.000/30.000)
    assert params[8] == "insumos varios"


def test_actualizar_un_precio_deja_el_sello_de_fecha_al_dia():
    # Si el UPDATE no toca actualizado_el, la columna queda marcando la última
    # edición manual y no la factura que de verdad cambió el precio.
    cur = FakeCursor()
    importador.importar_compra(cur, xml_compra().encode("latin-1"), "compra.xml")

    updates = [sql for sql, _ in cur.ejecutados if "UPDATE maestro_insumos" in sql]
    assert updates and all("actualizado_el" in sql for sql in updates)


def test_una_factura_solo_de_insumos_no_genera_gasto():
    cur = FakeCursor()
    res = importador.importar_compra(cur, xml_compra().encode("latin-1"), "solo_insumos.xml")

    assert res["gastos_insertados"] == 0
    assert cur.inserts("gastos_operativos") == []


def test_el_precio_que_queda_es_el_de_la_factura_mas_nueva():
    # El UPDATE de precios no tiene condición: manda el último documento
    # procesado. Con los documentos desordenados dentro del archivo (así los
    # entrega el SII), el insumo debe quedar con el precio más reciente.
    xml = envio_dte(
        doc_compra(precio=35000, folio=9001, fecha="2026-07-22"),   # el nuevo, primero
        doc_compra(precio=32000, folio=8801, fecha="2026-06-02"),   # el viejo, después
    )
    cur = FakeCursor()
    res = importador.importar_compra(cur, xml.encode("latin-1"), "desordenado.xml")

    assert res["precios_actualizados"] == [
        "Malta Pilsen -> $1280.0000/unidad",     # junio, primero
        "Malta Pilsen -> $1400.0000/unidad",     # julio, gana
    ]
    updates = [p for sql, p in cur.ejecutados if "UPDATE maestro_insumos" in sql]
    assert updates[-1] == (1400.0, "Malta Pilsen")


@pytest.mark.parametrize("contenido, esperado", [
    (envio_dte(doc_compra(fecha="2026-06-02"), doc_compra(fecha="2026-07-22")), "2026-07-22"),
    (envio_dte(doc_compra(fecha="2026-06-02")), "2026-06-02"),
    ("<html>sin fechas</html>", ""),
])
def test_fecha_mas_reciente_ordena_la_carga(contenido, esperado):
    assert importador.fecha_mas_reciente(contenido) == esperado


def test_un_proveedor_desconocido_no_bloquea_al_resto_del_archivo():
    # Casi toda descarga masiva trae algún proveedor nuevo: los conocidos deben
    # cargarse igual, y el archivo NO marcarse para poder reimportarlo después.
    xml = envio_dte(
        doc_compra(folio=8801),
        doc_compra(rut_emisor="11111111-1", razon="PROVEEDOR NUEVO SPA", folio=9902),
    )
    cur = FakeCursor()
    res = importador.importar_compra(cur, xml.encode("latin-1"), "masivo_parcial.xml")

    assert res["precios_actualizados"] == ["Malta Pilsen -> $1280.0000/unidad"]
    assert res["estado"] == "omitido"
    assert res["procesado_completo"] is False
    assert any("sin clasificar" in e for e in res["errores"])


def test_proveedor_sin_clasificar_no_se_marca_como_procesado():
    cur = FakeCursor()
    xml = xml_compra(rut_emisor="11111111-1", razon="PROVEEDOR NUEVO SPA")
    res = importador.importar_compra(cur, xml.encode("latin-1"), "nuevo.xml")

    assert res["estado"] == "omitido"
    assert res["procesado_completo"] is False
    assert any("sin clasificar" in e for e in res["errores"])
    assert cur.ejecutados == []


# ─── guardar_xml ──────────────────────────────────────────────────────────────

@pytest.fixture
def carpetas_tmp(tmp_path, monkeypatch):
    destinos = {clase: tmp_path / clase for clase in ("venta", "nota_credito", "compra")}
    monkeypatch.setattr(importador, "CARPETAS", destinos)
    return destinos


def test_guardar_xml_crea_el_archivo(carpetas_tmp):
    destino = importador.guardar_xml(b"<xml/>", "a.xml", "venta")
    assert destino == carpetas_tmp["venta"] / "a.xml"
    assert destino.read_bytes() == b"<xml/>"


def test_guardar_xml_no_reescribe_el_mismo_contenido(carpetas_tmp):
    primero = importador.guardar_xml(b"<xml/>", "a.xml", "venta")
    mtime = primero.stat().st_mtime_ns
    segundo = importador.guardar_xml(b"<xml/>", "a.xml", "venta")
    assert segundo == primero
    assert segundo.stat().st_mtime_ns == mtime


def test_guardar_xml_no_pisa_un_archivo_distinto(carpetas_tmp):
    importador.guardar_xml(b"<uno/>", "a.xml", "venta")
    otro = importador.guardar_xml(b"<dos/>", "a.xml", "venta")

    assert otro.name == "a (2).xml"
    assert (carpetas_tmp["venta"] / "a.xml").read_bytes() == b"<uno/>"
    # Reintentar el mismo contenido reutiliza el sufijo en vez de acumular copias.
    assert importador.guardar_xml(b"<dos/>", "a.xml", "venta") == otro


def test_guardar_xml_rechaza_clase_desconocida(carpetas_tmp):
    with pytest.raises(ValueError):
        importador.guardar_xml(b"<xml/>", "a.xml", "desconocido")


# ─── sync_db.sincronizar_en_cursor ────────────────────────────────────────────
# Viven aquí porque comparten el andamiaje: es el núcleo que el importador
# reutiliza de la CLI, y estos tests protegen ese refactor.

def test_sincronizar_en_cursor_resume_lo_insertado():
    documentos = parse_dte.parsear_contenido(xml_venta())
    cur = FakeCursor()
    res = sync_db.sincronizar_en_cursor(cur, documentos)

    assert res["insertados"] == 1
    assert res["productos"] == 1
    assert res["duplicados"] == []
    assert res["ruts"] == ["77126823-4"]
    assert any("Folio 4743" in linea for linea in res["detalle"])


def test_sincronizar_en_cursor_omite_folios_existentes():
    documentos = parse_dte.parsear_contenido(xml_venta(folio=4743))
    cur = FakeCursor(folios_existentes=[(4743, "33")])
    res = sync_db.sincronizar_en_cursor(cur, documentos)

    assert res["insertados"] == 0
    assert res["duplicados"] == [{"folio": 4743, "tipo": "33"}]
    assert cur.inserts("ventas") == []
    assert any("ya existen" in linea for linea in res["detalle"])
