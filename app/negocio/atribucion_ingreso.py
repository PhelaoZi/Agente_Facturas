# -*- coding: utf-8 -*-
"""Atribuye el ingreso neto de un documento a cada cerveza.

Responde la pregunta que el sistema no sabía contestar: **cuánta plata dejó cada
cerveza**. Hasta el 2026-08-10 el ranking de Cream Ale 30L daba $3.575.105
cuando el ingreso real era $10.867.242 — un tercio— porque nadie sumaba la
línea de logística, que es cerca de la mitad del precio del barril.

Las cuatro reglas
-----------------
1. **El ingreso de una cerveza es su línea MÁS la logística que le toca.** El
   doble renglón existe para pagar menos ILA (el impuesto grava solo la
   cerveza), no porque la logística sea un servicio aparte.
2. **Lo atribuido tiene que sumar el neto del documento.** Si no cuadra, el
   documento entero queda sin atribuir: nunca se publica un pedazo.
3. **Cada cifra dice cómo se calculó.** `deterministica` cuando no hubo nada que
   repartir; `estimada` cuando se repartió entre varias cervezas y no hay forma
   de verificarlo contra el documento.
4. **El signo sale del tipo de documento.** En la base hay 40 notas de crédito
   con el ILA positivo y 12 con negativo, y las líneas siempre positivas:
   confiar en el signo guardado produce dobles conteos.

Esta capa es DERIVADA: se puede borrar y recalcular entera desde `productos` y
`ventas`, que no se tocan. Por eso tiene que ser determinista.
"""
from decimal import Decimal, ROUND_HALF_UP

from app.negocio import clasificacion_lineas as cl

# 1.1 (2026-08-14): el redondeo del reparto conserva el total. Antes cada línea
# redondeaba por su cuenta y la suma se pasaba en un peso, lo que hacía fallar la
# invariante y botaba el documento entero: 9 documentos por $1.732.185.
VERSION_ALGORITMO = "1.1"

CALIDADES = frozenset({
    "deterministica",   # no hubo nada que repartir
    "estimada",         # se repartió entre varias cervezas, no verificable
})

METODOS = frozenset({
    "cerveza_unica",        # una sola cerveza: toda la logística es suya
    "logistica_nombrada",   # el productor desglosó la logística por estilo
    "reparto_litros",       # varios barriles: a prorrata de litros
    "reparto_unidades",     # varias botellas o latas: a prorrata de unidades
})

# El impuesto declarado sirve de verificación independiente, pero está redondeado
# al peso: se tolera esa diferencia y nada más.
TOLERANCIA_ILA = Decimal("1")

TIPO_NOTA_CREDITO = 61
CLASES_PASS_THROUGH = ("envase", "co2")

# Largo mínimo de la abreviatura con que el productor nombra una logística
# ("Stout", "Scotch"). Más corto que esto calzaría con demasiadas cervezas.
MINIMO_ABREVIATURA = 3


def _redondear(valor):
    return int(Decimal(valor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _redondear_conservando_total(exactos):
    """Redondea una lista de montos sin que la suma se mueva.

    Redondear cada parte por su cuenta no conserva el total: $131.345 entre dos
    barriles da $65.672,5 y las dos suben a $65.673, así que la suma se pasa en
    un peso. Con la invariante estricta —correcta— eso botaba el documento
    entero: eran 9 documentos por $1.732.185 perdidos por 9 pesos.

    Repartir plata obliga a elegir dónde cae el resto; no puede quedar sin
    dueño. Va a la parte mayor, que es donde menos pesa en términos relativos.
    """
    redondeados = [_redondear(x) for x in exactos]
    diferencia = _redondear(sum(exactos)) - sum(redondeados)
    if diferencia and redondeados:
        mayor = max(range(len(exactos)), key=lambda i: exactos[i])
        redondeados[mayor] += diferencia
    return redondeados


def _resultado(estado, motivo=None, lineas=None, atribuido=0, pass_through=0,
               sin_atribuir=0, signo=1):
    return {
        "estado": estado,
        "motivo": motivo,
        "signo_evento": signo,
        "lineas": lineas or [],
        "monto_atribuido": atribuido,
        "monto_pass_through": pass_through,
        "monto_sin_atribuir": sin_atribuir,
        "version_algoritmo": VERSION_ALGORITMO,
    }


def _clasificar_lineas(documento):
    """Clasifica cada línea y la agrupa por clase."""
    fecha = documento.get("fecha")
    grupos = {}
    for linea in documento["lineas"]:
        info = cl.clasificar(linea.get("nombre_producto"), fecha=fecha)
        grupos.setdefault(info["clase"], []).append((linea, info))
    return grupos


def _base_reparto(cerveza_lineas):
    """Cuánto le toca a cada cerveza de la logística sin nombrar.

    Por litros en barriles y por unidades en botellas y latas, tal como está
    documentado en CLAUDE.md. Una factura que mezcle familias sin desglosar la
    logística no tiene forma de repartirse: eso lo detecta el llamador.
    """
    formatos = {info["formato"] for _, info in cerveza_lineas}
    if formatos == {"barril"}:
        return [Decimal(str(info["litros"])) * Decimal(str(linea.get("cantidad") or 1))
                for linea, info in cerveza_lineas], "reparto_litros"
    if formatos and "barril" not in formatos:
        return [Decimal(str(linea.get("cantidad") or 1))
                for linea, _ in cerveza_lineas], "reparto_unidades"
    return None, None          # familias mezcladas: no hay base común


def _ila_confirma(documento, cerveza_lineas):
    """Compara el ILA declarado contra el que corresponde al bruto de las líneas.

    Es una validación HACIA ADELANTE: se calcula el impuesto esperado y se
    compara con el declarado. No se invierte el impuesto para deducir una base,
    porque está redondeado y varias bases dan el mismo peso.

    Cuando no calza, el documento trae un descuento global: sobre las 822
    facturas con ILA, calza en 815 y los 7 que no son exactamente los que traen
    descuento.
    """
    ila_declarado = abs(Decimal(str(documento.get("impuesto_adicional") or 0)))
    if not ila_declarado:
        return not cerveza_lineas or None      # sin ILA no hay nada que verificar

    bruto_cerveza = sum(Decimal(str(l["total_linea"])) for l, _ in cerveza_lineas)
    if not bruto_cerveza:
        return None

    tasa = Decimal(str(documento.get("tasa_ila") or "0.205"))
    esperado = (bruto_cerveza * tasa).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return abs(esperado - ila_declarado) <= TOLERANCIA_ILA


def atribuir(documento):
    """Atribuye el neto de un documento a sus cervezas.

    `documento` necesita: tipo_documento, folio, fecha, monto_neto,
    impuesto_adicional y `lineas` (cada una con nombre_producto, cantidad,
    total_linea y opcionalmente id).
    """
    signo = -1 if int(documento["tipo_documento"]) == TIPO_NOTA_CREDITO else 1
    neto = abs(Decimal(str(documento["monto_neto"])))

    grupos = _clasificar_lineas(documento)
    cervezas = grupos.get("cerveza", [])

    # Una línea que no se reconoce deja el documento entero fuera. No se
    # descarga el residual sobre la cerveza: preferimos una cifra faltante, que
    # se nota, a una inventada, que no.
    if grupos.get("desconocida"):
        return _resultado("no_atribuido", "linea_desconocida",
                          sin_atribuir=signo * _redondear(neto), signo=signo)

    # El impuesto declarado al SII, como verificación independiente. Va ANTES
    # de mirar el residual: es lo que distingue "falta la línea de logística"
    # de "hubo un descuento global", que dejan el mismo hueco.
    confirmacion = _ila_confirma(documento, cervezas)
    if confirmacion is False:
        return _resultado("no_atribuido", "descuento_global",
                          sin_atribuir=signo * _redondear(neto), signo=signo)
    if confirmacion is None and cervezas:
        return _resultado("no_atribuido", "sin_ila",
                          sin_atribuir=signo * _redondear(neto), signo=signo)

    bruto_total = sum(Decimal(str(l["total_linea"]))
                      for lineas in grupos.values() for l, _ in lineas)

    # Lo que falta para llegar al neto es la logística que el parser descartaba
    # hasta el 2026-08-10 (las líneas llamadas "Logistica" a secas). Se deduce
    # de la cabecera, y por eso viaja marcada como `residual_cabecera` y no se
    # confunde con lo que venía escrito en el documento.
    residual = neto - bruto_total
    if residual < 0:
        return _resultado("no_atribuido", "descuento_global",
                          sin_atribuir=signo * _redondear(neto), signo=signo)
    if residual and not cervezas:
        return _resultado("no_atribuido", "residual_sin_cerveza",
                          sin_atribuir=signo * _redondear(neto), signo=signo)

    pass_through = sum(Decimal(str(l["total_linea"]))
                       for clase in CLASES_PASS_THROUGH
                       for l, _ in grupos.get(clase, []))
    otros = sum(Decimal(str(l["total_linea"]))
                for clase in ("servicio", "insumo")
                for l, _ in grupos.get(clase, []))

    if not cervezas:
        # Documento sin cerveza (arriendo, venta de insumo). Cuadra igual, pero
        # no genera ingreso de producto.
        return _resultado("atribuido", None,
                          pass_through=signo * _redondear(pass_through),
                          sin_atribuir=signo * _redondear(otros), signo=signo)

    atribuidas = _repartir(grupos, cervezas, signo, residual)
    if atribuidas is None:
        return _resultado("no_atribuido", "logistica_no_repartible",
                          sin_atribuir=signo * _redondear(neto), signo=signo)

    total_atribuido = sum(l["ingreso_neto_atribuido"] for l in atribuidas)

    # La invariante, comprobada antes de devolver nada.
    if total_atribuido + signo * _redondear(pass_through) + signo * _redondear(otros) \
            != signo * _redondear(neto):
        return _resultado("no_atribuido", "no_cuadra",
                          sin_atribuir=signo * _redondear(neto), signo=signo)

    return _resultado("atribuido", None, lineas=atribuidas,
                      atribuido=total_atribuido,
                      pass_through=signo * _redondear(pass_through),
                      sin_atribuir=signo * _redondear(otros), signo=signo)


def _cerveza_por_contexto(nombre_logistica, cervezas_del_documento):
    """A qué cerveza del documento apunta una logística abreviada.

    El productor escribe "Logistica Stout" cuando en la misma factura va un
    "Barril 30L Stout Cafe". Esa abreviatura no es reconocible por sí sola —hay
    varios stouts en el catálogo— pero sí lo es DENTRO de su documento.

    Si calza con más de una cerveza de la factura, no se elige ninguna: se
    reparte a prorrata, que es lo conservador.
    """
    resto = cl.RE_LOGISTICA_PALABRA.sub("", cl.normalizar(nombre_logistica)).strip(" .-–")
    if len(resto) < MINIMO_ABREVIATURA:
        return None

    candidatas = {
        cerveza for cerveza in cervezas_del_documento
        if resto in cl.normalizar(cerveza) or cl.normalizar(cerveza) in resto
    }
    return candidatas.pop() if len(candidatas) == 1 else None


def _repartir(grupos, cervezas, signo, residual=Decimal(0)):
    """Reparte la logística entre las cervezas. None si no hay forma de hacerlo.

    `residual` es la logística que falta en el histórico, deducida de la
    cabecera. Se reparte igual que la logística sin nombrar, pero las líneas
    quedan marcadas con otra `fuente`.
    """
    logisticas = grupos.get("logistica", [])

    # La logística que el productor nombró va a su cerveza: es evidencia suya y
    # repartirla a prorrata encima sería descartar lo que él mismo declaró.
    del_documento = [info["cerveza"] for _, info in cervezas]

    nombradas, sin_nombrar = {}, Decimal(0)
    for linea, info in logisticas:
        cerveza = info["cerveza"] or _cerveza_por_contexto(
            linea["nombre_producto"], del_documento)
        if cerveza in del_documento:
            nombradas[cerveza] = nombradas.get(cerveza, Decimal(0)) + \
                                 Decimal(str(linea["total_linea"]))
        else:
            sin_nombrar += Decimal(str(linea["total_linea"]))

    # El residual de la cabecera se comporta como logística sin nombrar: no dice
    # a qué cerveza corresponde, así que se reparte con el mismo criterio.
    a_repartir = sin_nombrar + residual

    pesos = metodo_reparto = None
    if a_repartir:
        pesos, metodo_reparto = _base_reparto(cervezas)
        if pesos is None or not sum(pesos):
            return None

    # Primera pasada: la logística exacta de cada cerveza, sin redondear. Lo que
    # se redondea es la logística y no el ingreso, porque el monto de la línea ya
    # es un entero del documento: así el ingreso queda exacto por construcción.
    exactas, metodos = [], []
    for indice, (linea, info) in enumerate(cervezas):
        logistica = nombradas.get(info["cerveza"], Decimal(0))
        metodo = "logistica_nombrada" if logistica else "cerveza_unica"
        if a_repartir:
            logistica += a_repartir * pesos[indice] / sum(pesos)
            metodo = metodo_reparto if len(cervezas) > 1 else "cerveza_unica"
        exactas.append(logistica)
        metodos.append(metodo)

    logisticas = _redondear_conservando_total(exactas)

    calidad = "estimada" if (a_repartir and len(cervezas) > 1) else "deterministica"
    fuente = "residual_cabecera" if residual else "linea_dte"

    resultado = []
    for indice, (linea, info) in enumerate(cervezas):
        monto = _redondear(Decimal(str(linea["total_linea"])))
        resultado.append({
            "linea_id": linea.get("id"),
            "cerveza": info["cerveza"],
            "formato": info["formato"],
            "litros": info["litros"],
            "unidades": linea.get("cantidad"),
            "monto_linea_evidencia": signo * monto,
            "logistica_atribuida": signo * logisticas[indice],
            "ingreso_neto_atribuido": signo * (monto + logisticas[indice]),
            "metodo": metodos[indice],
            "calidad": calidad,
            "fuente": fuente,
            "version_algoritmo": VERSION_ALGORITMO,
        })
    return resultado
