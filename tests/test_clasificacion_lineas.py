# tests/test_clasificacion_lineas.py
"""Clasificación de las líneas de factura por reconocimiento POSITIVO.

Paso 3 del cierre (docs/debate-arquitectura/10-...). La regla que ordena todo
este archivo:

    lo que no se reconoce queda `desconocida`, y `desconocida` NUNCA es cerveza.

El clasificador anterior (`precios_venta.py`) funcionaba por descarte: "si no es
logística, PET ni CO2, entonces es cerveza". Con eso, `Arriendo maquina
schopera` ($59.000) y `Malta.Boortmalt.Pilsen 25` ($162.918) entraban como venta
de cerveza. Los detectó la auditoría externa y se verificaron en la base.

En `productos` hay 123 descripciones distintas para ~20 cervezas, con erratas
del productor al escribir la factura a mano (`Baril`, `Balck IPA`, `Scoth Ale`,
`Sctout Cafe`, `Barril 30L Scotch Ale Ale`). Por eso el reconocimiento va contra
un mapa explícito de alias y no contra una expresión regular ingeniosa: un
nombre nuevo tiene que caer en `desconocida` y esperar, no colarse.
"""
from datetime import date

import pytest

from app.negocio import clasificacion_lineas as cl
from app.negocio.clasificacion_lineas import LITROS_BARRIL_ESTANDAR


def _clase(nombre):
    return cl.clasificar(nombre)["clase"]


# ─── Lo que NO puede volver a pasar ───────────────────────────────────────────

@pytest.mark.parametrize("nombre, clase_esperada", [
    ("Arriendo maquina schopera", "servicio"),      # $59.000, folio 4354
    ("Malta.Boortmalt.Pilsen 25", "insumo"),        # $162.918, folio 4447
])
def test_los_dos_casos_que_encontro_la_auditoria_no_son_cerveza(nombre, clase_esperada):
    """Ambos son exactamente las dos únicas facturas con ILA = 0, así que el
    control del impuesto ya los delataba. Ahora además se clasifican bien."""
    assert _clase(nombre) == clase_esperada


def test_la_logistica_con_nombre_de_cerveza_sigue_siendo_logistica():
    """'Logistica Cream Ale' contiene 'Cream Ale'. Si el orden de las reglas se
    equivoca, media factura se cuenta dos veces como cerveza."""
    for nombre in ("Logistica Cream Ale", "Logistica Scotch Ale",
                   "Logistica Stout Cafe", "Logistic", "Logistica 30L"):
        assert _clase(nombre) == "logistica", nombre


# ─── Cerveza: reconocimiento positivo, con las erratas reales ─────────────────

@pytest.mark.parametrize("nombre, cerveza, litros", [
    ("Barril 30L Cream Ale",      "Cream Ale",       30),
    ("Barril  30L Cream Ale",     "Cream Ale",       30),   # doble espacio
    ("Barril 30L Cream  Ale",     "Cream Ale",       30),
    ("Baril 30L Scotch Ale",      "Scotch Ale",      30),   # "Baril"
    ("Barril 30L Scoth Ale",      "Scotch Ale",      30),   # "Scoth"
    ("Barril 30L Scotch Ale Ale", "Scotch Ale",      30),   # "Ale" repetido
    ("Barril 30L Scotch",         "Scotch Ale",      30),
    ("Barril 30L Balck IPA",      "Black IPA",       30),   # "Balck"
    ("Barril 30L Black Ipa",      "Black IPA",       30),
    ("Barril 30L Sctout Cafe",    "Stout Café",      30),   # "Sctout"
    ("Barril 30L Sout Cafe",      "Stout Café",      30),   # "Sout"
    ("Barril 30LStout Cafe",      "Stout Café",      30),   # sin espacio
    ("Barril 30L Wee Heavy",      "Wee Heavy",       30),
    ("Barril 20L Cream Ale",      "Cream Ale",       20),   # lote incompleto
    ("Barril 25L W.C IPA",        "West Coast IPA",  25),
    ("Barril 30L IPA W.C",        "West Coast IPA",  30),   # orden invertido
    ("Barril 30L West Coast IPA", "West Coast IPA",  30),
    ("30L Sour Berries",          "Sour Berries",    30),   # sin "Barril"
])
def test_reconoce_las_cervezas_pese_a_las_erratas(nombre, cerveza, litros):
    resultado = cl.clasificar(nombre)

    assert resultado["clase"] == "cerveza"
    assert resultado["cerveza"] == cerveza
    assert resultado["litros"] == litros
    assert resultado["formato"] == "barril"


def test_reconoce_botellas_y_latas():
    botella = cl.clasificar("Botella 330cc Cream Ale")
    assert (botella["clase"], botella["cerveza"], botella["formato"]) == \
           ("cerveza", "Cream Ale", "botella")

    lata = cl.clasificar("Lata 470cc Scotch Ale")
    assert (lata["clase"], lata["cerveza"], lata["formato"]) == \
           ("cerveza", "Scotch Ale", "lata")


def test_botella_33cc_es_una_errata_de_330cc():
    """No existe una botella de 33cc. El precio ($9.600 por 12) coincide con la
    de 330cc."""
    assert cl.clasificar("Botella 33cc Scotch Ale")["formato"] == "botella"


# ─── Pass-through: parte del monto facturado, no venta de cerveza ────────────

@pytest.mark.parametrize("nombre", [
    "Barril Pet 30L", "Barril PET 30L", "Barriles Pet 30L",
    "Barril Pet 30 litros", "Pet 20L", "Barril Pet 20L",
])
def test_el_envase_pet_es_pass_through(nombre):
    assert _clase(nombre) == "envase"


@pytest.mark.parametrize("nombre", [
    "9 kg CO2", "Carga CO2", "Carga CO2 9 kg", "CO2 9kg", "Recarga CO2 9 kg",
])
def test_el_co2_es_pass_through(nombre):
    assert _clase(nombre) == "co2"


# ─── La regla de fondo: lo desconocido se queda desconocido ──────────────────

@pytest.mark.parametrize("nombre", [
    "Barril 30L Sour",          # ¿cuál sour? hay Berries, Guayaba, Piña, P/L, F/L
    "Barril 20L Sour",
    "Cerveza artesanal",        # nombre nuevo cualquiera
    "",
    None,
])
def test_lo_ambiguo_o_nuevo_no_se_convierte_en_cerveza(nombre):
    """Es preferible no atribuir una línea a atribuirla mal: una cifra faltante
    se nota, una equivocada no."""
    assert _clase(nombre) == "desconocida"


# ─── Ambigüedades resueltas por el productor, acotadas en el tiempo ──────────

def test_la_sour_de_febrero_2025_se_resuelve_con_la_fecha():
    """Las 5 líneas que dicen solo "Sour" son de feb-mar 2025. En esa ventana la
    única sour vendida fue la Frambuesa/Lima, y el productor lo confirmó
    (2026-08-11).

    Se resuelve con la FECHA y no agregando "sour" al mapa de alias: eso
    asignaría a Frambuesa/Lima cualquier "Sour" futuro, que puede ser otra.
    """
    resultado = cl.clasificar("Barril 20L Sour", fecha=date(2025, 2, 19))

    assert resultado["clase"] == "cerveza"
    assert resultado["cerveza"] == "Sour Frambuesa/Lima"
    assert resultado["litros"] == 20


def test_fuera_de_esa_ventana_la_sour_sigue_siendo_ambigua():
    """Para abril de 2026 la sour del catálogo era la Guayaba. La resolución
    vale para el período confirmado, no para siempre."""
    assert _clase("Barril 30L Sour") == "desconocida"                      # sin fecha
    assert cl.clasificar("Barril 30L Sour",
                         fecha=date(2026, 4, 1))["clase"] == "desconocida"


def test_la_fecha_no_convierte_en_cerveza_un_nombre_cualquiera():
    """La resolución por fecha aplica a las ambigüedades declaradas, no es una
    puerta trasera para que cualquier texto pase."""
    assert cl.clasificar("Servicio de flete",
                         fecha=date(2025, 2, 19))["clase"] == "desconocida"


@pytest.mark.parametrize("nombre, cerveza", [
    ("Barril Wee Heavy", "Wee Heavy"),
    ("Barril W.C IPA",   "West Coast IPA"),
    ("Barril Mincay",    "Mincay"),
])
def test_un_barril_sin_litros_es_de_30l(nombre, cerveza):
    """Confirmado por el productor el 2026-08-11: todos los barriles son de 30L.
    Los de 20 o 25 litros son el mismo barril con menos adentro, cuando el
    fermentador no alcanza a llenarlo, y ESOS sí lo dicen en la factura.

    Son 3 líneas de $90.000 en total. Se resuelven acá y no suponiendo litros
    dentro del motor de atribución, donde la suposición quedaría invisible.
    """
    resultado = cl.clasificar(nombre)

    assert resultado["clase"] == "cerveza"
    assert resultado["cerveza"] == cerveza
    assert resultado["formato"] == "barril"
    assert resultado["litros"] == LITROS_BARRIL_ESTANDAR


def test_una_cerveza_sin_formato_alguno_no_se_da_por_barril():
    """La regla de arriba vale para "Barril X", no para cualquier texto: sin la
    palabra que declare el envase no hay de dónde deducirlo."""
    resultado = cl.clasificar("Wee Heavy")

    assert resultado["clase"] == "desconocida"
    assert resultado["cerveza"] == "Wee Heavy"     # lo que sí se sabe, se dice
    assert resultado["litros"] is None


def test_tolera_las_tildes_que_se_perdieron_al_cargar_los_datos():
    """En la base hay nombres con U+FFFD, el carácter de reemplazo: la ñ de
    'Sour Piña' se perdió al ingresar el dato, no al clasificarlo (3 filas en
    `productos`, 22 en `ventas`).

    Ese carácter significa "acá había algo que no se pudo leer", así que calza
    con cualquiera. Descartarlo daría 'sour pia', que no es nada.
    """
    resultado = cl.clasificar("Barril 30L Sour Pi�a")

    assert resultado["clase"] == "cerveza"
    assert resultado["cerveza"] == "Sour Piña"


def test_el_caracter_perdido_no_convierte_cualquier_cosa_en_cerveza():
    """Calza con un carácter, no con una palabra: si tolerara de más, un nombre
    corrupto cualquiera se atribuiría a la cerveza más parecida."""
    assert _clase("Barril 30L ����") == "desconocida"


def test_toda_clase_devuelta_es_una_de_las_declaradas():
    """Protege contra un typo en una rama nueva del clasificador."""
    for nombre in ("Barril 30L Cream Ale", "Logistica", "Carga CO2",
                   "Pet 30L", "Arriendo maquina schopera", "loquesea"):
        assert cl.clasificar(nombre)["clase"] in cl.CLASES
