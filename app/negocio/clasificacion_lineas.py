# -*- coding: utf-8 -*-
"""Clasifica una línea de factura por reconocimiento positivo.

Regla de oro
------------
    Lo que no se reconoce queda `desconocida`, y `desconocida` NUNCA es cerveza.

Por qué no se clasifica por descarte
------------------------------------
`precios_venta.py` usaba "si no es logística, PET ni CO2, entonces es cerveza".
Con esa regla `Arriendo maquina schopera` ($59.000) y `Malta.Boortmalt.Pilsen 25`
($162.918) entraban como venta de cerveza. Lo encontró la auditoría externa
(docs/debate-arquitectura/09-...) y se verificó en la base.

El problema de fondo es que el productor escribe el nombre del ítem a mano en
cada factura: hay 123 descripciones distintas para ~20 cervezas, con erratas
(`Baril`, `Balck IPA`, `Scoth Ale`, `Sctout Cafe`, `Barril 30L Scotch Ale Ale`).
Por eso el reconocimiento va contra un mapa explícito de alias: un nombre nuevo
tiene que caer en `desconocida` y esperar a que alguien lo mapee, no colarse.

Para los DTE nuevos esto importa menos: desde el 2026-08-10 se guarda el
`<CodImpAdic>` de cada línea, y el 26 es el propio SII declarando que la línea
es cerveza. Este mapa es sobre todo para el histórico, donde ese dato no existe.
"""
import re
import unicodedata

CLASES = frozenset({
    "cerveza",      # venta de cerveza: es lo único que genera ingreso de producto
    "logistica",    # parte del precio de la cerveza, desglosada para reducir ILA
    "envase",       # barril PET desechable: costo traspasado sin margen
    "co2",          # recarga del cilindro: costo traspasado sin margen
    "servicio",     # arriendos y similares
    "insumo",       # venta de materia prima a terceros
    "desconocida",  # no se reconoce: no se atribuye
})

# ─── Cervezas conocidas, con las erratas tal como aparecen en las facturas ────
# La clave es el nombre normalizado (sin tildes, minúsculas, espacios colapsados).
# El valor es el nombre canónico. Agregar acá cuando aparezca una cerveza nueva.
CERVEZAS = {
    "cream ale": "Cream Ale",
    "cream": "Cream Ale",

    "scotch ale": "Scotch Ale",
    "scoth ale": "Scotch Ale",
    "scotch": "Scotch Ale",
    "scotch ale ale": "Scotch Ale",
    "imp scotch": "Imperial Scotch",
    "imp. scotch": "Imperial Scotch",

    "stout cafe": "Stout Café",
    "sout cafe": "Stout Café",
    "sctout cafe": "Stout Café",
    "stout cafe/ca": "Stout Café/Cacao",
    "stout cafe/cacao": "Stout Café/Cacao",
    "sout caf/ca": "Stout Café/Cacao",
    "stout caf/ca": "Stout Café/Cacao",

    "ris": "RIS",
    "ris cafe": "RIS Café",
    "ris cafe/cacao": "RIS Café/Cacao",
    "ris cacao/cafe": "RIS Café/Cacao",
    "ris cafe/caco": "RIS Café/Cacao",

    "black ipa": "Black IPA",
    "balck ipa": "Black IPA",
    "hazy black ipa": "Hazy Black IPA",

    "w.c ipa": "West Coast IPA",
    "wc ipa": "West Coast IPA",
    "ipa w.c": "West Coast IPA",
    "west coast ipa": "West Coast IPA",
    "session ipa": "Session IPA",
    "brut ipa": "Brut IPA",
    "milkshake ipa": "Milkshake IPA",

    "wee heavy": "Wee Heavy",
    "paint it black": "Paint it Black",
    "apa": "APA",
    "mincay": "Mincay",
    "redhouse": "RedHouse",

    "barley wine": "Barley Wine",
    "barley w murta": "Barley Wine Murta",

    "sour berries": "Sour Berries",
    "sour guayaba": "Sour Guayaba",
    "sour pina": "Sour Piña",
    "sour lima/pina": "Sour Lima/Piña",
    "sour p/l": "Sour Piña/Lima",
    "sour f/l": "Sour Frambuesa/Lima",
    "sour fl": "Sour Frambuesa/Lima",
    # OJO: "sour" a secas NO está mapeado a propósito. Hay seis sours distintos
    # y la factura no dice cuál: atribuirlo a una sería inventar.
}

# ─── Servicios e insumos: no son productos del catálogo ───────────────────────
SERVICIOS = ("arriendo",)
INSUMOS = ("malta", "lupulo", "levadura")

# ─── Formatos ─────────────────────────────────────────────────────────────────
# Todos los barriles son de 30L. Cuando el fermentador no alcanza a llenar uno,
# se despacha con 20 o 25 litros y se factura como "Barril 25L": el precio y la
# logística escalan con los litros, así que hay que conservarlos.
# Sin límite de palabra tras la "l": el productor escribe "Barril 30LStout Cafe"
# pegado. El límite va antes de los dígitos, para no morder el "30" de "330cc".
RE_BARRIL = re.compile(r'\b(?:bar+il(?:es)?\s*)?(\d{2})\s*l', re.IGNORECASE)
RE_BOTELLA = re.compile(r'\bbotella\b', re.IGNORECASE)
RE_LATA = re.compile(r'\blata\b', re.IGNORECASE)

# Pass-through
RE_PET = re.compile(r'\bpet\b', re.IGNORECASE)
RE_CO2 = re.compile(r'\bco\s*2\b', re.IGNORECASE)
RE_LOGISTICA = re.compile(r'^\s*log[ií]stic', re.IGNORECASE)

# Carácter de reemplazo de Unicode: marca dónde había una tilde que se perdió al
# cargar el dato. Ver _alias_que_calzan.
REEMPLAZO = "�"

# Mínimo de caracteres legibles para arriesgar un reconocimiento sobre un nombre
# corrupto. Bajo esto, la línea queda `desconocida`.
MINIMO_LEGIBLE = 4


def _normalizar(texto):
    """Minúsculas, sin tildes y con los espacios colapsados."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_tildes).strip().lower()


def _resultado(clase, cerveza=None, formato=None, litros=None):
    return {"clase": clase, "cerveza": cerveza, "formato": formato, "litros": litros}


def _alias_que_calzan(limpio):
    """Alias contenidos en el texto, tolerando caracteres ilegibles.

    En la base hay nombres con U+FFFD, el carácter de reemplazo de Unicode: la
    ñ de 'Sour Piña' se perdió al ingresar el dato (3 filas en `productos`, 22
    en `ventas`). Ese carácter significa "acá había algo que no se pudo leer",
    así que calza con cualquier carácter — pero con UNO solo, para que un
    nombre corrupto no termine atribuido a la cerveza más parecida.
    """
    if REEMPLAZO not in limpio:
        return [alias for alias in CERVEZAS if alias in limpio]

    # Con caracteres ilegibles la comparación se vuelve exacta en vez de por
    # subcadena, y se exige un mínimo de texto legible. Si no, "????" calzaría
    # con cualquier alias de cuatro letras.
    legibles = limpio.replace(REEMPLAZO, "").strip()
    if len(legibles) < MINIMO_LEGIBLE:
        return []

    patron = re.compile(".".join(
        re.escape(parte) for parte in limpio.split(REEMPLAZO)
    ))
    return [alias for alias in CERVEZAS if patron.fullmatch(alias)]


def _buscar_cerveza(resto):
    """Busca el nombre de la cerveza en lo que queda tras sacar el formato."""
    limpio = resto.strip(" .-–")
    if limpio in CERVEZAS:
        return CERVEZAS[limpio]

    # El nombre puede venir con ruido alrededor ("Cream Ale x2"). Se toma el
    # alias más largo que calce, para que "stout cafe/cacao" gane sobre "stout
    # cafe" y no al revés.
    candidatos = _alias_que_calzan(limpio)
    if not candidatos:
        return None
    return CERVEZAS[max(candidatos, key=len)]


def clasificar(nombre):
    """Clasifica el nombre de una línea de factura.

    Devuelve {"clase", "cerveza", "formato", "litros"}. `clase` siempre es una
    de CLASES; el resto puede ser None.
    """
    if not nombre or not str(nombre).strip():
        return _resultado("desconocida")

    texto = _normalizar(str(nombre))

    # El orden importa: "Logistica Cream Ale" contiene "Cream Ale". Si la
    # logística no se descarta primero, media factura se cuenta dos veces.
    if RE_LOGISTICA.search(texto):
        return _resultado("logistica")
    if RE_CO2.search(texto):
        return _resultado("co2")
    if RE_PET.search(texto):
        return _resultado("envase")
    if any(p in texto for p in SERVICIOS):
        return _resultado("servicio")
    if any(p in texto for p in INSUMOS):
        return _resultado("insumo")

    # ── Formato ──────────────────────────────────────────────────────────────
    formato = litros = None
    resto = texto

    barril = RE_BARRIL.search(texto)
    if barril:
        formato, litros = "barril", int(barril.group(1))
        resto = texto[barril.end():]
    elif RE_BOTELLA.search(texto):
        formato = "botella"
        # "330cc", "330c" y la errata "33cc": no existe una botella de 33cc.
        resto = re.sub(r'\bbotella\b\s*\d{2,3}\s*c+\b', "", texto)
    elif RE_LATA.search(texto):
        formato = "lata"
        resto = re.sub(r'\blata\b\s*\d{3}\s*c*c\b', "", texto)

    cerveza = _buscar_cerveza(resto)

    # Cerveza reconocida Y formato reconocido: recién ahí es una venta de
    # cerveza atribuible. Con uno solo de los dos no alcanza — suponer los
    # litros propaga el error al precio, a la logística y al margen.
    if cerveza and formato:
        return _resultado("cerveza", cerveza, formato, litros)

    # Se informa lo que sí se pudo reconocer, sin ascender la línea a cerveza.
    return _resultado("desconocida", cerveza, formato, litros)
