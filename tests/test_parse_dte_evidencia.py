# tests/test_parse_dte_evidencia.py
"""El parser debe conservar TODA la evidencia del DTE, no solo lo que se usa hoy.

Contexto (debate cerrado en docs/debate-arquitectura/10-...):
`parse_dte.py` venía descartando en silencio cuatro cosas que el XML sí trae:

1. las líneas llamadas "Logistica" a secas (ITEMS_NO_CATALOGO), que son cerca de
   la mitad del precio del barril;
2. los descuentos globales `<DscRcgGlobal>`, que hacen que el monto de una línea
   NO sea su neto;
3. el código de impuesto por línea `<CodImpAdic>`, que es el SII declarando cuál
   línea es cerveza — mejor que cualquier match por nombre;
4. los `<ImptoReten>` más allá del primero, y su tipo y tasa.

Sin eso, la tabla `productos` no permite reconstruir el ingreso por producto, y
el histórico ya no se puede recuperar: quedan 2 XML de 876 documentos.

El caso de oro es el folio 4746, real, tal como lo emitió el SII. Es el mismo
contraejemplo que tumbó dos propuestas de reparación seguidas.

`productos` NO cambia: sigue excluyendo "Logistica" y con las mismas columnas,
porque de esa tabla dependen la vista local, el sync a la nube y sus filtros.
La evidencia nueva viaja en claves aparte.
"""
from scripts import parse_dte


# ─── Caso de oro: folio 4746, copiado del XML real ────────────────────────────

def xml_folio_4746():
    """Factura con descuento global, ILA y una línea de logística.

    Neto $81.000 = ($35.000 cerveza + $55.000 logística) − $9.000 de descuento.
    El ILA de $6.458 corresponde a una base de ~$31.500, o sea la cerveza YA
    descontada: por eso $35.000 no es el neto de esa línea.
    """
    return """<?xml version="1.0" encoding="ISO-8859-1"?>
<EnvioDTE><SetDTE><DTE><Documento ID="MiPE76308012-32527">
<Encabezado>
<IdDoc><TipoDTE>33</TipoDTE><Folio>4746</Folio>
<FchEmis>2026-07-29</FchEmis><FmaPago>2</FmaPago></IdDoc>
<Emisor><RUTEmisor>76308012-9</RUTEmisor>
<RznSoc>ELABORADORA Y COMERCIALIZADORA VINTAGE SPA</RznSoc></Emisor>
<Receptor><RUTRecep>76990452-2</RUTRecep><RznSocRecep>INVERSIONES FDL SPA</RznSocRecep>
<DirRecep>CHILOE 3575</DirRecep><CmnaRecep>SAN MIGUEL</CmnaRecep></Receptor>
<Totales><MntNeto>81000</MntNeto><TasaIVA>19.00</TasaIVA><IVA>15390</IVA>
<ImptoReten><TipoImp>26</TipoImp><TasaImp>20.50</TasaImp><MontoImp>6458</MontoImp></ImptoReten>
<MntTotal>102848</MntTotal></Totales>
</Encabezado>
<Detalle><NroLinDet>1</NroLinDet><NmbItem>Barril 30L Wee Heavy</NmbItem>
<DscItem>* Impuesto Adic.: Cervezas y Otras bebidas alcoholicas 20,5%</DscItem>
<QtyItem>1.00</QtyItem><PrcItem>35000.00</PrcItem>
<CodImpAdic>26</CodImpAdic><MontoItem>35000</MontoItem></Detalle>
<Detalle><NroLinDet>2</NroLinDet><NmbItem>Logistica</NmbItem>
<QtyItem>1.00</QtyItem><PrcItem>55000.00</PrcItem><MontoItem>55000</MontoItem></Detalle>
<DscRcgGlobal><NroLinDR>1</NroLinDR><TpoMov>D</TpoMov>
<GlosaDR>DESCUENTO GLOBAL</GlosaDR><TpoValor>$</TpoValor><ValorDR>9000</ValorDR></DscRcgGlobal>
</Documento></DTE></SetDTE></EnvioDTE>"""


def _doc():
    return parse_dte.parsear_contenido(xml_folio_4746())[0]


# ─── 1. Las líneas completas, incluida la logística ───────────────────────────

def test_conserva_todas_las_lineas_incluida_la_logistica():
    """`productos` descarta la logística a propósito; `lineas` no descarta nada.
    Perder esa línea es lo que hacía que el ingreso por producto saliera a la
    mitad."""
    lineas = _doc()["lineas"]

    assert [l["nombre_producto"] for l in lineas] == [
        "Barril 30L Wee Heavy",
        "Logistica",
    ]
    assert sum(l["total_linea"] for l in lineas) == 90_000


def test_productos_sigue_sin_la_logistica():
    """Compatibilidad: de `productos` dependen la vista local, el sync a la nube
    y todos los filtros ya escritos. La evidencia nueva va aparte, no encima."""
    productos = _doc()["productos"]

    assert [p["nombre_producto"] for p in productos] == ["Barril 30L Wee Heavy"]


def test_conserva_el_numero_de_linea():
    """Sin NroLinDet, dos líneas del mismo producto en un documento son
    indistinguibles. Ya pasa: el folio 4344 tiene dos 'Barril 30L Cream Ale'."""
    assert [l["nro_linea"] for l in _doc()["lineas"]] == [1, 2]


def test_conserva_el_codigo_de_impuesto_de_cada_linea():
    """CodImpAdic=26 es el SII diciendo 'esta línea es cerveza'. Es evidencia
    declarada, no una heurística sobre el nombre: la cerveza lo lleva y la
    logística no."""
    lineas = _doc()["lineas"]

    assert lineas[0]["cod_imp_adic"] == 26
    assert lineas[1]["cod_imp_adic"] is None


def test_conserva_la_descripcion_de_la_linea():
    assert _doc()["lineas"][0]["descripcion"].startswith("* Impuesto Adic.")


# ─── 2. El descuento global, que fue lo que tumbó dos propuestas ──────────────

def test_conserva_el_descuento_global():
    """Sin esto, `monto de la línea` se confunde con `neto de la línea`. En este
    folio la diferencia es de $9.000 sobre $90.000."""
    ajustes = _doc()["ajustes_globales"]

    assert len(ajustes) == 1
    assert ajustes[0] == {
        "nro_linea": 1,
        "tipo_movimiento": "D",          # D = descuento, R = recargo
        "glosa": "DESCUENTO GLOBAL",
        "tipo_valor": "$",               # $ = monto fijo, % = porcentaje
        "valor": 9000.0,
        "indicador_exento": None,
    }


def test_un_documento_sin_descuentos_trae_la_lista_vacia():
    """No None: quien consuma esto no debería tener que preguntar."""
    from tests.test_negocio_importador import xml_venta

    assert parse_dte.parsear_contenido(xml_venta())[0]["ajustes_globales"] == []


# ─── 3. Los impuestos, con tipo y tasa ────────────────────────────────────────

def test_conserva_los_impuestos_con_tipo_y_tasa():
    """El parser tomaba solo <MontoImp> y daba por hecho que la tasa era 20,5%.
    La tasa hay que leerla, no suponerla."""
    impuestos = _doc()["impuestos"]

    assert impuestos == [{"tipo": 26, "tasa": 20.5, "monto": 6458}]


def test_impuesto_adicional_de_la_cabecera_no_cambia():
    """La columna `ventas.impuesto_adicional` sigue siendo la misma de siempre:
    esto agrega evidencia, no reescribe lo que ya funciona."""
    assert _doc()["venta"]["impuesto_adicional"] == 6458


# ─── 4. La cuadratura que hace verificable todo lo anterior ───────────────────

def test_las_lineas_menos_el_descuento_dan_el_neto_del_documento():
    """Esta es la comprobación que ninguna propuesta pudo hacer antes, porque
    faltaban las dos mitades: la línea de logística y el descuento."""
    doc = _doc()

    bruto = sum(l["total_linea"] for l in doc["lineas"])
    descuentos = sum(a["valor"] for a in doc["ajustes_globales"]
                     if a["tipo_movimiento"] == "D")

    assert bruto - descuentos == doc["venta"]["monto_neto"] == 81_000


def test_el_ila_declarado_confirma_el_reparto_del_descuento():
    """El pago de esta capa de evidencia, en el caso que costó dos NO-GO.

    Repartiendo el descuento en proporción al monto de cada línea, la cerveza
    queda en $31.500 y la logística en $49.500. Eso NO es una suposición: el ILA
    que Zigurat le declaró al SII ($6.458) solo cuadra con una base de $31.500,
    y el impuesto lo calculó el emisor, no nosotros.

    O sea que la atribución propone y el impuesto declarado verifica, documento
    por documento. Antes esto era imposible: sin la línea de logística ni el
    descuento no había nada contra qué contrastar.
    """
    from decimal import Decimal, ROUND_HALF_UP

    doc = _doc()
    bruto = Decimal(sum(l["total_linea"] for l in doc["lineas"]))
    descuentos = Decimal(sum(a["valor"] for a in doc["ajustes_globales"]
                             if a["tipo_movimiento"] == "D"))
    factor = (bruto - descuentos) / bruto           # 0,9 → 10% de descuento

    # CodImpAdic 26 = ILA de cervezas. Lo declara el SII línea por línea, así
    # que no hay que adivinar cuál línea es cerveza por su nombre.
    base_cerveza = sum(Decimal(l["total_linea"]) * factor
                       for l in doc["lineas"] if l["cod_imp_adic"] == 26)
    assert base_cerveza == Decimal("31500.0")

    tasa = Decimal(str(doc["impuestos"][0]["tasa"])) / 100    # leída, no supuesta
    esperado = (base_cerveza * tasa).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    assert int(esperado) == doc["venta"]["impuesto_adicional"] == 6458
