# tests/test_atribucion_ingreso.py
"""Motor de atribución de ingreso por producto.

Núcleo del paso 3 (docs/debate-arquitectura/10-...). Responde la pregunta que
el sistema no sabía contestar: **cuánta plata dejó cada cerveza**.

Las cuatro reglas que ordenan todo:

1. El ingreso de una cerveza es su línea MÁS la logística que le toca. La
   logística es cerca de la mitad del precio del barril; ignorarla fue lo que
   hizo que el ranking de Cream Ale saliera $3,5M cuando eran $10,8M.
2. Lo que se atribuye tiene que sumar el neto del documento. Si no cuadra, el
   documento entero queda sin atribuir: nunca se publica un pedazo.
3. Cada cifra dice cómo se calculó. `deterministica` = una sola cerveza, no hay
   nada que repartir. `estimada` = se repartió entre varias y no hay forma de
   verificarlo.
4. Las notas de crédito restan, con el signo derivado del tipo de documento y
   nunca del signo guardado (en la base hay 40 NC con ILA positivo y 12 con
   negativo, y las líneas siempre positivas).
"""
from datetime import date

import pytest

from app.negocio import atribucion_ingreso as ai


def _linea(nombre, total, cantidad=1, id_linea=None):
    return {"id": id_linea, "nombre_producto": nombre,
            "cantidad": cantidad, "total_linea": total}


def _documento(lineas, neto, ila=0, tipo=33, folio=1000, fecha=date(2026, 6, 1)):
    return {"tipo_documento": tipo, "folio": folio, "fecha": fecha,
            "monto_neto": neto, "impuesto_adicional": ila, "lineas": lineas}


# ─── El caso normal: una cerveza y su logística ──────────────────────────────

def test_una_cerveza_se_lleva_toda_su_logistica():
    """Cream Ale: $20.000 de producto + $35.370 de logística = $55.370. Ese es
    el precio real del barril, y es la cifra que el sistema no daba."""
    doc = _documento([
        _linea("Barril 30L Cream Ale", 20_000),
        _linea("Logistica", 35_370),
    ], neto=55_370, ila=4_100)

    resultado = ai.atribuir(doc)

    assert resultado["estado"] == "atribuido"
    assert len(resultado["lineas"]) == 1
    linea = resultado["lineas"][0]
    assert linea["cerveza"] == "Cream Ale"
    assert linea["ingreso_neto_atribuido"] == 55_370
    assert linea["calidad"] == "deterministica"


def test_lo_atribuido_mas_el_pass_through_suma_el_neto_del_documento():
    """La invariante que hace verificable todo lo demás."""
    doc = _documento([
        _linea("Barril 30L Scotch Ale", 20_000),
        _linea("Logistica", 35_370),
        _linea("Barril Pet 30L", 16_000),
    ], neto=71_370, ila=4_100)

    r = ai.atribuir(doc)

    assert r["monto_atribuido"] + r["monto_pass_through"] + r["monto_sin_atribuir"] \
           == doc["monto_neto"] == 71_370
    assert r["monto_pass_through"] == 16_000       # el envase no es venta de cerveza


# ─── Varias cervezas: se reparte, y se dice que se repartió ──────────────────

def test_con_varias_cervezas_la_logistica_se_reparte_por_litros():
    """Dos barriles de 30L: la logística va mitad y mitad. Es una regla de
    negocio razonable, pero sigue siendo un reparto que nadie puede verificar
    contra el documento — por eso queda marcada `estimada`."""
    doc = _documento([
        _linea("Barril 30L Cream Ale", 20_000),
        _linea("Barril 30L Scotch Ale", 20_000),
        _linea("Logistica", 70_740),
    ], neto=110_740, ila=8_200)

    r = ai.atribuir(doc)

    por_cerveza = {l["cerveza"]: l for l in r["lineas"]}
    assert por_cerveza["Cream Ale"]["ingreso_neto_atribuido"] == 55_370
    assert por_cerveza["Scotch Ale"]["ingreso_neto_atribuido"] == 55_370
    assert all(l["calidad"] == "estimada" for l in r["lineas"])


def test_barriles_de_distinto_tamano_reparten_la_logistica_a_prorrata_de_litros():
    """Un barril de 20L no puede costar la misma logística que uno de 30L: es el
    mismo barril con menos adentro, y el precio escala con los litros."""
    doc = _documento([
        _linea("Barril 30L Cream Ale", 20_000),
        _linea("Barril 20L Cream Ale", 13_333),
        _linea("Logistica", 50_000),
    ], neto=83_333, ila=6_833)

    r = ai.atribuir(doc)
    logisticas = sorted(l["logistica_atribuida"] for l in r["lineas"])

    assert logisticas == [20_000, 30_000]          # 20/50 y 30/50 de los litros


def test_la_logistica_con_nombre_va_a_su_cerveza_y_no_a_prorrata():
    """Cuando los costos difieren, el productor desglosa la logística por estilo
    ("Logistica Scotch" + "Logistica Stout"). Ese desglose es evidencia suya:
    repartir a prorrata encima sería descartar lo que él mismo declaró."""
    doc = _documento([
        _linea("Barril 30L Scotch Ale", 20_000),
        _linea("Barril 30L Stout Cafe", 25_000),
        _linea("Logistica Scotch", 35_370),
        _linea("Logistica Stout", 50_000),
    ], neto=130_370, ila=9_225)

    r = ai.atribuir(doc)
    por_cerveza = {l["cerveza"]: l for l in r["lineas"]}

    assert por_cerveza["Scotch Ale"]["ingreso_neto_atribuido"] == 55_370
    assert por_cerveza["Stout Café"]["ingreso_neto_atribuido"] == 75_000
    assert all(l["calidad"] == "deterministica" for l in r["lineas"])


# ─── Lo que NO se atribuye ───────────────────────────────────────────────────

def test_un_documento_con_descuento_global_queda_sin_atribuir():
    """El folio 4746 real: el ILA declarado ($6.458) no calza con el bruto de la
    línea de cerveza ($35.000 → $7.175). La diferencia es un descuento global, y
    sin saber a qué línea se le aplicó no se puede repartir."""
    doc = _documento([
        _linea("Barril 30L Wee Heavy", 35_000),
        _linea("Logistica", 55_000),
    ], neto=81_000, ila=6_458, folio=4746)

    r = ai.atribuir(doc)

    assert r["estado"] == "no_atribuido"
    assert r["motivo"] == "descuento_global"
    assert r["lineas"] == []
    assert r["monto_sin_atribuir"] == 81_000


def test_una_linea_desconocida_deja_el_documento_entero_sin_atribuir():
    """No se descarga el residual sobre la cerveza. Preferimos una cifra
    faltante, que se nota, a una inventada, que no."""
    doc = _documento([
        _linea("Barril 30L Cream Ale", 20_000),
        _linea("Cerveza nueva sin mapear", 30_000),
        _linea("Logistica", 35_370),
    ], neto=85_370, ila=4_100)

    r = ai.atribuir(doc)

    assert r["estado"] == "no_atribuido"
    assert r["motivo"] == "linea_desconocida"


def test_un_documento_sin_cerveza_se_explica_entero_sin_inventar_ingreso():
    """El arriendo de la schopera ($59.000, folio 4354). No es venta de cerveza
    y no hay nada que atribuir, pero el documento igual cuadra."""
    doc = _documento([_linea("Arriendo maquina schopera", 59_000)],
                     neto=59_000, ila=0, folio=4354)

    r = ai.atribuir(doc)

    assert r["lineas"] == []
    assert r["monto_atribuido"] == 0
    assert r["monto_sin_atribuir"] == 59_000
    assert r["monto_atribuido"] + r["monto_pass_through"] + r["monto_sin_atribuir"] \
           == 59_000


def test_si_las_lineas_suman_mas_que_el_neto_no_se_publica_nada():
    """Cualquier descuadre hacia arriba es señal de un ajuste no declarado."""
    doc = _documento([
        _linea("Barril 30L Cream Ale", 90_000),
        _linea("Logistica", 35_370),
    ], neto=99_999, ila=18_450)

    r = ai.atribuir(doc)

    assert r["estado"] == "no_atribuido"


# ─── El histórico: la logística que el parser tiró ───────────────────────────

def test_reconstruye_la_logistica_que_falta_en_el_historico():
    """Hasta el 2026-08-10 el parser descartaba las líneas llamadas "Logistica"
    a secas, así que en el histórico las líneas NUNCA suman el neto: faltan
    $35.370 de $55.370. Sin esta regla el motor rechaza 828 de 876 documentos.

    Lo que falta se deduce de la cabecera: `MntNeto − líneas guardadas`. Esto es
    justo lo que la auditoría rechazó en su momento, y con razón — pero por dos
    motivos que ya no aplican: se estaba escribiendo como si fuera una línea del
    DTE (acá es atribución declarada como derivada), y un descuento global podía
    corromper el residual (acá el control del ILA descarta esos documentos antes
    de llegar hasta aquí).
    """
    doc = _documento([_linea("Barril 30L Cream Ale", 20_000)],
                     neto=55_370, ila=4_100)

    r = ai.atribuir(doc)

    assert r["estado"] == "atribuido"
    linea = r["lineas"][0]
    assert linea["ingreso_neto_atribuido"] == 55_370
    assert linea["logistica_atribuida"] == 35_370
    assert linea["fuente"] == "residual_cabecera"


def test_lo_reconstruido_se_distingue_de_lo_que_estaba_en_la_factura():
    """Quien lea la cifra tiene que poder saber si la logística venía escrita en
    el documento o se dedujo de la cabecera."""
    completo = ai.atribuir(_documento([
        _linea("Barril 30L Cream Ale", 20_000),
        _linea("Logistica", 35_370),
    ], neto=55_370, ila=4_100))

    assert completo["lineas"][0]["fuente"] == "linea_dte"


def test_no_se_reconstruye_nada_si_el_ila_no_confirma():
    """El residual solo es logística cuando no hubo descuento. El folio 4746
    real: $81.000 de neto contra $35.000 en líneas deja $46.000 de residual, y
    de esos $46.000 solo $49.500... no cuadran. El ILA lo delata antes."""
    doc = _documento([_linea("Barril 30L Wee Heavy", 35_000)],
                     neto=81_000, ila=6_458, folio=4746)

    r = ai.atribuir(doc)

    assert r["estado"] == "no_atribuido"
    assert r["motivo"] == "descuento_global"


def test_sin_ila_declarado_una_venta_de_cerveza_no_se_atribuye():
    """El ILA es la única verificación independiente que hay. Sin él no se puede
    afirmar que el residual sea logística y no otra cosa."""
    doc = _documento([_linea("Barril 30L Cream Ale", 20_000)],
                     neto=55_370, ila=0)

    r = ai.atribuir(doc)

    assert r["estado"] == "no_atribuido"
    assert r["motivo"] == "sin_ila"


# ─── Notas de crédito: restan, y el signo sale del tipo de documento ─────────

def test_una_nota_de_credito_atribuye_ingreso_negativo():
    """En la base las líneas de NC están todas positivas y el ILA a veces
    positivo y a veces negativo. Confiar en el signo guardado produce dobles
    conteos: se deriva del tipo 61 y punto."""
    doc = _documento([
        _linea("Barril 30L Cream Ale", 20_000),
        _linea("Logistica", 35_370),
    ], neto=-55_370, ila=4_100, tipo=61, folio=910)

    r = ai.atribuir(doc)

    assert r["estado"] == "atribuido"
    assert r["lineas"][0]["ingreso_neto_atribuido"] == -55_370
    assert r["signo_evento"] == -1


def test_el_signo_guardado_de_la_nota_de_credito_no_altera_el_resultado():
    """Misma NC con el ILA negativo en vez de positivo: el resultado no cambia."""
    lineas = [_linea("Barril 30L Cream Ale", 20_000), _linea("Logistica", 35_370)]

    positiva = ai.atribuir(_documento(lineas, -55_370, ila=4_100, tipo=61))
    negativa = ai.atribuir(_documento(lineas, -55_370, ila=-4_100, tipo=61))

    assert positiva["lineas"][0]["ingreso_neto_atribuido"] == \
           negativa["lineas"][0]["ingreso_neto_atribuido"] == -55_370


# ─── Reproducibilidad ────────────────────────────────────────────────────────

def test_dos_corridas_con_la_misma_entrada_dan_lo_mismo():
    """La capa es recalculable de cero: si no fuera determinista, no se podría
    borrar y volver a construir sin miedo."""
    doc = _documento([
        _linea("Barril 30L Cream Ale", 20_000),
        _linea("Barril 30L Scotch Ale", 20_000),
        _linea("Logistica", 70_740),
    ], neto=110_740, ila=8_200)

    assert ai.atribuir(doc) == ai.atribuir(doc)


def test_cada_linea_declara_como_se_calculo():
    """Sin procedencia, una cifra estimada y una evidenciada se ven iguales en
    el dashboard — que es justo como se llegó a este problema."""
    doc = _documento([
        _linea("Barril 30L Cream Ale", 20_000),
        _linea("Logistica", 35_370),
    ], neto=55_370, ila=4_100)

    linea = ai.atribuir(doc)["lineas"][0]

    assert linea["metodo"] == "cerveza_unica"
    assert linea["calidad"] in ai.CALIDADES
    assert linea["version_algoritmo"] == ai.VERSION_ALGORITMO
