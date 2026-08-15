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
    compara con el declarado. Acá NUNCA se invierte el impuesto — está redondeado
    al peso y varias bases dan el mismo. Invertirlo solo se hace después, en
    `_factor_descuento`, cuando esta verificación ya falló: ahí no se afirma una
    base, se deduce una proporción y el residual absorbe la imprecisión.

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


def _factor_descuento(documento, cerveza_lineas):
    """Cuánto quedó de las líneas escritas, deducido del impuesto declarado.

    El ILA se calcula sobre la base YA descontada, así que dice qué proporción
    del bruto sobrevivió al descuento global. Se usa SOLO cuando la verificación
    directa falló, y el resultado nunca se afirma exacto: el redondeo del propio
    impuesto (2 pesos en el folio 4746) cae en el residual, que ya viaja como
    `estimada`, y la invariante sigue exigiendo que todo sume el neto.

    Solo se acepta un descuento. Un impuesto MAYOR que el de las líneas no es un
    recargo conocido: es señal de que algo no entendemos, y ahí corresponde
    dejar el hueco honesto.
    """
    ila = abs(Decimal(str(documento.get("impuesto_adicional") or 0)))
    bruto = sum(Decimal(str(l["total_linea"])) for l, _ in cerveza_lineas)
    if not ila or not bruto:
        return None
    tasa = Decimal(str(documento.get("tasa_ila") or "0.205"))
    factor = (ila / tasa) / bruto
    return factor if 0 < factor <= 1 else None


def _escalar(grupos, factor):
    """Aplica el descuento global a cada línea escrita.

    Con descuento, lo que se cobró por una línea no es lo que dice la línea. El
    `monto_linea_evidencia` pasa a ser el monto ya descontado: es lo que de
    verdad entró por esa cerveza, y es lo único que permite que la suma cuadre.
    """
    if factor == 1:
        return grupos
    return {clase: [({**linea,
                      "total_linea": Decimal(str(linea["total_linea"])) * factor},
                     info)
                    for linea, info in lineas]
            for clase, lineas in grupos.items()}


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
    factor = None
    if confirmacion is False:
        # No confirma porque el impuesto se calculó sobre la base ya descontada.
        # El mismo impuesto dice cuánto se descontó: se escalan las líneas y el
        # residual absorbe el resto.
        factor = _factor_descuento(documento, cervezas)
        if factor is None:
            return _resultado("no_atribuido", "descuento_global",
                              sin_atribuir=signo * _redondear(neto), signo=signo)
        grupos = _escalar(grupos, factor)
        cervezas = grupos.get("cerveza", [])
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

    atribuidas = _repartir(grupos, cervezas, signo, residual,
                           descontado=factor is not None)
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


def _cervezas_por_formato(nombre_logistica, cervezas):
    """Las cervezas del documento cuyo FORMATO nombra esta logística.

    El productor desglosa la logística por estilo ("Logistica Scotch") cuando el
    costo difiere por estilo, y por formato ("Logistica Barril", "Logistica
    Latas") cuando difiere por formato. Las dos son evidencia suya.

    Se compara en singular porque escribe "Latas" y el formato es "lata".
    """
    resto = cl.RE_LOGISTICA_PALABRA.sub("", cl.normalizar(nombre_logistica)).strip(" .-–")
    if len(resto) < MINIMO_ABREVIATURA:
        return []
    singular = resto.rstrip("s")
    return [(linea, info) for linea, info in cervezas
            if info["formato"] and info["formato"].rstrip("s") == singular]


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


def _repartir(grupos, cervezas, signo, residual=Decimal(0), descontado=False):
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
        monto = Decimal(str(linea["total_linea"]))
        cerveza = info["cerveza"] or _cerveza_por_contexto(
            linea["nombre_producto"], del_documento)
        if cerveza in del_documento:
            nombradas[cerveza] = nombradas.get(cerveza, Decimal(0)) + monto
            continue

        # "Logistica Barril" / "Logistica Latas": nombró el FORMATO en vez del
        # estilo. Es evidencia suya igual que el nombre, y sin leerla no había
        # cómo prorratear entre un barril y una lata: el documento se caía.
        del_formato = _cervezas_por_formato(linea["nombre_producto"], cervezas)
        if del_formato:
            base = sum(Decimal(str(l["total_linea"])) for l, _ in del_formato)
            for l, i in del_formato:
                parte = (monto * Decimal(str(l["total_linea"])) / base if base
                         else monto / len(del_formato))
                nombradas[i["cerveza"]] = nombradas.get(i["cerveza"], Decimal(0)) + parte
            continue

        sin_nombrar += monto

    # El residual de la cabecera se comporta como logística sin nombrar: no dice
    # a qué cerveza corresponde, así que se reparte con el mismo criterio.
    a_repartir = sin_nombrar + residual

    pesos = metodo_reparto = None
    if a_repartir:
        pesos, metodo_reparto = _base_reparto(cervezas)
        if pesos is None or not sum(pesos):
            return None

    # Primera pasada, sin redondear nada: el monto de la línea y la logística que
    # le toca. Con un descuento global el monto tampoco es entero, así que lo que
    # hay que conservar al redondear es el INGRESO —que es lo que la invariante
    # compara contra el neto— y no cada parte por su lado.
    montos_exactos, logisticas_exactas, metodos = [], [], []
    for indice, (linea, info) in enumerate(cervezas):
        logistica = nombradas.get(info["cerveza"], Decimal(0))
        metodo = "logistica_nombrada" if logistica else "cerveza_unica"
        if a_repartir:
            logistica += a_repartir * pesos[indice] / sum(pesos)
            metodo = metodo_reparto if len(cervezas) > 1 else "cerveza_unica"
        montos_exactos.append(Decimal(str(linea["total_linea"])))
        logisticas_exactas.append(logistica)
        metodos.append(metodo)

    ingresos = _redondear_conservando_total(
        [m + l for m, l in zip(montos_exactos, logisticas_exactas)])
    montos = _redondear_conservando_total(montos_exactos)

    # Un descuento deducido del impuesto NO es determinístico aunque haya una
    # sola cerveza: el factor se infirió, no venía escrito en el documento.
    calidad = ("estimada" if descontado or (a_repartir and len(cervezas) > 1)
               else "deterministica")
    fuente = "residual_cabecera" if residual else "linea_dte"

    resultado = []
    for indice, (linea, info) in enumerate(cervezas):
        resultado.append({
            "linea_id": linea.get("id"),
            "cerveza": info["cerveza"],
            "formato": info["formato"],
            "litros": info["litros"],
            "unidades": linea.get("cantidad"),
            "monto_linea_evidencia": signo * montos[indice],
            # Se deriva del ingreso para que las tres cifras sean consistentes:
            # evidencia + logística == ingreso, siempre.
            "logistica_atribuida": signo * (ingresos[indice] - montos[indice]),
            "ingreso_neto_atribuido": signo * ingresos[indice],
            "metodo": metodos[indice],
            "calidad": calidad,
            "fuente": fuente,
            "version_algoritmo": VERSION_ALGORITMO,
        })
    return resultado
