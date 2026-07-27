# tests/test_negocio_precios.py
from app.negocio import precios_venta as pv

RECETAS = ["Cream Ale", "Scotch Ale", "Stout Café/Cacao", "Wee Heavy Pistacho"]


# --- Clase de la linea ---

def test_la_logistica_se_reconoce_en_todas_sus_variantes():
    # El productor la escribe de 23 formas distintas en el historico.
    for nombre in ["logistica", "logistica cream ale", "logistic",
                   "logistica 30l", "logistica barril 25l"]:
        assert pv._clase(nombre) == "logistica", nombre


def test_el_envase_pet_es_pass_through():
    # Es el costo del envase desechable traspasado al cliente, sin margen.
    for nombre in ["barril pet 30l", "pet 20l", "barriles pet 30l"]:
        assert pv._clase(nombre) == "pass_through", nombre


def test_el_co2_es_pass_through():
    # La schopera y el cilindro son de Zigurat: la recarga se compra en Clean
    # Ice y se cobra al cliente a costo. No es venta de cerveza.
    for nombre in ["9 kg co2", "carga co2", "recarga co2 9 kg", "co2 9kg"]:
        assert pv._clase(nombre) == "pass_through", nombre


def test_un_barril_de_cerveza_es_cerveza():
    assert pv._clase("barril 30l cream ale") == "cerveza"


# --- Familia y capacidad ---

def test_capacidad_de_barril_y_botella():
    assert pv._familia_y_capacidad("barril 30l cream ale") == ("barril", 30000)
    assert pv._familia_y_capacidad("barril 25l cream ale") == ("barril", 25000)
    assert pv._familia_y_capacidad("botella 330cc cream ale") == ("botella", 330)
    assert pv._familia_y_capacidad("lata 470 cc sour berries") == ("lata", 470)


def test_tolera_la_errata_baril():
    # "Baril 30L Stout Cafe" aparece tal cual en los folios 4286 y 4518.
    assert pv._familia_y_capacidad("baril 30l stout cafe") == ("barril", 30000)


def test_tolera_la_errata_330c():
    # "Botella 330c Cream Ale", folio 4732.
    assert pv._familia_y_capacidad("botella 330c cream ale") == ("botella", 330)


def test_barril_sin_capacidad_escrita_se_asume_de_30l():
    assert pv._familia_y_capacidad("barril cream ale") == ("barril", 30000)


def test_linea_sin_familia_reconocible():
    assert pv._familia_y_capacidad("carga co2") == (None, None)


# --- Cerveza ---

def test_detecta_la_cerveza_por_nombre_completo():
    assert pv._detectar_cerveza("barril 30l cream ale", RECETAS) == "Cream Ale"
    assert pv._detectar_cerveza("botella 330cc scotch ale", RECETAS) == "Scotch Ale"


def test_detecta_la_cerveza_con_el_nombre_a_medias():
    # La factura casi nunca escribe el nombre completo de la receta.
    assert pv._detectar_cerveza("barril 30l stout cafe/ca", RECETAS) == "Stout Café/Cacao"
    assert pv._detectar_cerveza("barril 30l wee heavy", RECETAS) == "Wee Heavy Pistacho"


def test_gana_la_receta_que_calza_en_mas_palabras():
    # "ale" esta en Cream Ale y en Scotch Ale: debe ganar la que ademas
    # aporta "cream".
    assert pv._detectar_cerveza("barril 30l cream ale", RECETAS) == "Cream Ale"


def test_sin_cerveza_identificable_devuelve_none():
    # Empate: "ale" calza igual con Cream Ale y Scotch Ale.
    assert pv._detectar_cerveza("barril 30l ale", RECETAS) is None
    # RIS no esta en el catalogo de recetas.
    assert pv._detectar_cerveza("barril 30l ris", RECETAS) is None


# --- Clave de formato ---

def test_todos_los_barriles_comparten_clave():
    # Un "Barril 25L" es el barril de 30L con menos cerveza adentro, no otro
    # formato: el precio se normaliza y la clave es la misma.
    assert pv.clave_formato("barril", 30000) == "barril 30L"
    assert pv.clave_formato("barril", 25000) == "barril 30L"


def test_clave_de_botella_y_lata_lleva_su_capacidad():
    assert pv.clave_formato("botella", 330) == "botella 330"
    assert pv.clave_formato("lata", 470) == "lata 470"


def test_clave_desde_el_nombre_de_formato_del_catalogo():
    # Los nombres vienen de la tabla `formatos` y los usa margenes().
    assert pv.clave_formato_desde_nombre("Barril 30L acero") == "barril 30L"
    assert pv.clave_formato_desde_nombre("Barril 30L PET") == "barril 30L"
    assert pv.clave_formato_desde_nombre("Botella 330ml") == "botella 330"


# ─── Atribucion de logistica y precio ────────────────────────────────────────
from datetime import date


class FakeCursor:
    """Devuelve una lista de filas distinta por cada execute(), en orden.
    precios_por_formato hace exactamente dos consultas: recetas y luego lineas."""

    def __init__(self, *respuestas):
        self._respuestas = list(respuestas)
        self._actual = []
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._actual = self._respuestas.pop(0) if self._respuestas else []

    def fetchall(self):
        return self._actual


def _linea(folio, neto, nombre, cantidad, total, fecha=date(2026, 7, 1)):
    return {"folio": folio, "fecha": fecha, "monto_neto": neto,
            "nombre_producto": nombre, "cantidad": cantidad, "total_linea": total}


def _cursor(lineas, recetas=RECETAS):
    return FakeCursor([{"nombre_cerveza": r} for r in recetas], lineas)


def _precio(resultado, cerveza, formato):
    for fila in resultado["precios"]:
        if fila["cerveza"] == cerveza and fila["formato"] == formato:
            return fila["precio_ultimo"]
    raise AssertionError(
        f"no hay precio para {cerveza} / {formato}: {resultado['precios']}")


def test_logistica_nombrada_se_cruza_por_cantidad():
    """Folio 4694: la logistica nombrada lleva la misma cantidad que su barril,
    asi que la atribucion es exacta. 20.000 + 35.370 = 55.370, el precio
    confirmado por el productor."""
    r = pv.precios_por_formato(_cursor([
        _linea(4694, 110740, "Barril 30L Cream Ale", 2, 40000),
        _linea(4694, 110740, "Logistica Cream Ale", 2, 70740),
    ]))
    assert _precio(r, "Cream Ale", "barril 30L") == 55370.0


def test_residual_se_reparte_y_el_co2_no_recibe_nada():
    """Folio 4736: la linea "Logistica" a secas no queda guardada en productos,
    asi que aparece como residual (neto menos las lineas). La carga de CO2 es
    pass-through y NO entra en la base de reparto: si entrara, el barril daria
    menos de 55.370."""
    r = pv.precios_por_formato(_cursor([
        _linea(4736, 181110, "Barril 30L Cream Ale", 3, 60000),
        _linea(4736, 181110, "Carga CO2", 1, 15000),
    ]))
    assert _precio(r, "Cream Ale", "barril 30L") == 55370.0


def test_barril_parcial_se_normaliza_a_30l():
    """Folio 4672: un barril de 25L a 46.141 es el mismo precio que uno de 30L
    a 55.370, solo que con menos cerveza adentro."""
    r = pv.precios_por_formato(_cursor([
        _linea(4672, 61141, "Barril 25L Cream Ale", 1, 16666),
        _linea(4672, 61141, "Recarga CO2 9 kg", 1, 15000),
    ]))
    assert abs(_precio(r, "Cream Ale", "barril 30L") - 55370.0) < 2.0


def test_logistica_que_nombra_la_capacidad_y_no_la_cerveza():
    """Folio 4572: "Logistica Barril 25L" no nombra cerveza, pero identifica sin
    ambiguedad al unico barril de 25L. El resto de la logistica (residual) se
    reparte entre los cuatro barriles llenos. Los tres precios convergen al
    mismo valor: es un cliente con descuento."""
    r = pv.precios_por_formato(_cursor([
        _linea(4572, 231209, "Barril 30L Cream Ale", 2, 30002),
        _linea(4572, 231209, "Barril 30L Scotch Ale", 2, 30000),
        _linea(4572, 231209, "Barril 25L Cream Ale", 1, 12500),
        _linea(4572, 231209, "Logistica Barril 25L", 1, 27363),
    ]))
    assert abs(_precio(r, "Cream Ale", "barril 30L") - 47836.0) < 2.0
    assert abs(_precio(r, "Scotch Ale", "barril 30L") - 47836.0) < 2.0


def test_botellas_reparten_el_residual_por_unidad():
    """Folio 4743: dos cervezas distintas a distinto precio de producto, pero la
    logistica es la misma ($900 por botella) y por eso el productor puso una
    sola linea. Scotch 400+900 y Stout 600+900."""
    r = pv.precios_por_formato(_cursor([
        _linea(4743, 33600, "Botella 330cc Scotch Ale", 12, 4800),
        _linea(4743, 33600, "Botella 330cc Stout Cafe", 12, 7200),
    ]))
    assert _precio(r, "Scotch Ale", "botella 330") == 1300.0
    assert _precio(r, "Stout Café/Cacao", "botella 330") == 1500.0


def test_el_envase_pet_no_recibe_logistica():
    """El PET es pass-through: la logistica del barril no se diluye en el."""
    r = pv.precios_por_formato(_cursor([
        _linea(4664, 70697, "Barril 30L Cream Ale", 1, 20000),
        _linea(4664, 70697, "Barril Pet 30L", 1, 15327),
        _linea(4664, 70697, "Logistica Cream Ale", 1, 35370),
    ]))
    assert _precio(r, "Cream Ale", "barril 30L") == 55370.0


def test_factura_de_familia_mixta_se_descarta():
    """Barriles y botellas con una sola logistica sin nombrar: no hay forma de
    saber cuanto le toca a cada uno. Preferimos no responder antes que inventar
    un margen. Hoy no ocurre en ninguna factura del historico."""
    r = pv.precios_por_formato(_cursor([
        _linea(4999, 100000, "Barril 30L Cream Ale", 1, 20000),
        _linea(4999, 100000, "Botella 330cc Cream Ale", 12, 4800),
    ]))
    assert r["precios"] == []
    assert r["descartadas"]["familia_mixta"] == 1


def test_la_consulta_excluye_las_facturas_con_nota_de_credito():
    """Una factura anulada por NC no dice a cuanto se vende, y una NC parcial
    rebajaria el neto sin tocar las lineas de productos (el residual saldria
    corto). Se filtran en el SQL."""
    cur = _cursor([])
    pv.precios_por_formato(cur)
    sql_lineas = cur.sql[1]
    assert "monto_neto_ajustado IS NULL" in sql_lineas
    assert "tipo_documento != 61" in sql_lineas


def test_precio_ultimo_y_promedio_se_calculan_por_separado():
    """El ultimo es el precio vigente; el promedio revela los descuentos."""
    r = pv.precios_por_formato(_cursor([
        _linea(4691, 95672, "Barril 30L Cream Ale", 2, 30000, date(2026, 5, 27)),
        _linea(4691, 95672, "Logistica Cream Ale", 2, 65672, date(2026, 5, 27)),
        _linea(4736, 166110, "Barril 30L Cream Ale", 3, 60000, date(2026, 7, 15)),
    ]))
    fila = r["precios"][0]
    assert fila["precio_ultimo"] == 55370.0
    assert fila["folio_ultimo"] == 4736
    assert fila["n_facturas"] == 2
    assert 47836.0 < fila["precio_promedio"] < 55370.0
