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
