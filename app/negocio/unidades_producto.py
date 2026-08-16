# -*- coding: utf-8 -*-
"""Unidades vendidas por cerveza, desde `v_lineas_producto`.

Existe porque el agente no tenía herramienta para esta pregunta: `ingreso_producto`
es de dinero y `ventas_producto` es el detalle de un producto. Ante *"cuántas
unidades vendí por producto en julio vs junio"* el modelo escribía SQL a mano
sobre `productos` y agrupaba por `nombre_producto`, que es el bug: devolvía
`Botella 330cc Cream Ale` (96) y `Botella 330c Cream Ale` (24) como productos
distintos.

**Prohibirlo en el prompt no alcanza.** Mientras una pregunta frecuente no tenga
herramienta, el modelo improvisa SQL. La forma de que no agrupe mal es que no
tenga que escribir la consulta.

Para UNIDADES la fuente es `v_lineas_producto` y NO `v_ingreso_producto`: hay que
contar también las líneas de los documentos que la atribución rechazó. El folio
4019 tiene 2 barriles y 24 latas que se vendieron de verdad, aunque su plata no
se pueda repartir entre formatos.

Sigue el contrato de `app/negocio/`: recibe un cursor, no maneja conexión.
"""
from app.negocio import clasificacion_lineas as cl

# Las notas de crédito devuelven mercadería: restan unidades. Sumarlas dejaría
# el volumen inflado con lo que volvió a la bodega.
TIPO_NOTA_CREDITO = 61


def _alcance(desde, hasta, cerveza):
    """Frase de alcance. La arma Python con los filtros que de verdad llegaron,
    nunca el modelo, que puede olvidarlos."""
    if desde and hasta:
        periodo = f"del {desde} al {hasta}"
    elif desde:
        periodo = f"desde el {desde}"
    elif hasta:
        periodo = f"hasta el {hasta}"
    else:
        periodo = "todo el histórico, sin filtro de fecha"
    que = f"Unidades de {cerveza}" if cerveza else "Unidades por cerveza"
    return f"{que} ({periodo})"


def ranking(cur, desde=None, hasta=None, cerveza=None, limite=50):
    """Unidades por cerveza y formato, de mayor a menor.

    Agrupa por el nombre CANÓNICO: el productor escribe el nombre a mano y hay
    84 formas de escribir 27 cervezas.
    """
    condiciones = [
        "clase = 'cerveza'",
        f"tipo_documento != {TIPO_NOTA_CREDITO}",
    ]
    # Los litros de un barril vienen en su nombre; los de una botella o una lata
    # salen del formato. Van parametrizados y desde `clasificacion_lineas`, que
    # es donde vive el conocimiento de formatos.
    params = [cl.LITROS_POR_UNIDAD["botella"], cl.LITROS_POR_UNIDAD["lata"]]
    if desde:
        condiciones.append("fecha >= %s")
        params.append(desde)
    if hasta:
        condiciones.append("fecha <= %s")
        params.append(hasta)
    if cerveza:
        condiciones.append("cerveza ILIKE %s")
        params.append(f"%{cerveza}%")

    cur.execute(f"""
        SELECT cerveza,
               formato,
               SUM(cantidad)         AS unidades,
               SUM(cantidad * COALESCE(litros,
                     CASE formato WHEN 'botella' THEN %s
                                  WHEN 'lata'    THEN %s
                                  ELSE 0 END))  AS litros,
               COUNT(DISTINCT folio) AS documentos,
               MAX(fecha)            AS ultima
        FROM v_lineas_producto
        WHERE {' AND '.join(condiciones)}
        GROUP BY cerveza, formato
        ORDER BY litros DESC
        LIMIT %s
    """, params + [limite])

    productos = [
        {"cerveza": f["cerveza"],
         "formato": f["formato"],
         "unidades": float(f["unidades"] or 0),
         "litros": round(float(f["litros"] or 0), 1),
         "documentos": int(f["documentos"] or 0),
         "ultima": f["ultima"]}
        for f in cur.fetchall()
    ]

    return {
        "productos": productos,
        "desde": desde,
        "hasta": hasta,
        "alcance": _alcance(desde, hasta, cerveza),
        "total_unidades": sum(p["unidades"] for p in productos),
        "total_litros": round(sum(p["litros"] for p in productos), 1),
    }
