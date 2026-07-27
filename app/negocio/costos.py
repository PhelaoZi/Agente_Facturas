"""Costos por SKU y márgenes (solo lectura).

Costos: misma consulta a vista_costo_sku que scripts/costo_sku.py.
Márgenes: cruza el costo total con el precio de venta REAL, deducido de las
facturas por app/negocio/precios_venta.py — cubre todos los formatos, no solo
los barriles. PRECIOS_VENTA_NETO quedó como respaldo para un SKU que todavía
no se ha vendido nunca.
"""
import unicodedata

from app.negocio import precios_venta

# Precios de venta netos confirmados por barril 30L (desde CLAUDE.md).
# Lista de (patrón, precio): el patrón se busca como SUBCADENA en el nombre
# normalizado, así "Stout Café/Cacao" (norm: "stout cafe/cacao") casa con
# "stout cafe". El primer patrón que calza gana (orden = prioridad).
PRECIOS_VENTA_NETO = [
    ("cream ale", 55370),
    ("scotch ale", 55370),
    ("stout cafe", 75000),
    ("stout cacao", 75000),
    ("stout", 75000),           # "Stout Café/Cacao" y variantes de escritura
    ("paint it black", 98000),
]


def _norm(s):
    """Normaliza para comparar nombres: minúsculas, sin tildes, espacios simples."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _precio_venta(cerveza, formato):
    """Precio de venta confirmado de un SKU: solo barriles 30L (acero y PET
    comparten precio; difieren en costo). None si no aplica."""
    if "barril" not in _norm(formato):
        return None
    nombre = _norm(cerveza)
    for patron, precio in PRECIOS_VENTA_NETO:
        if patron in nombre:
            return precio
    return None


def costos_sku(cur, receta=None, sku=None):
    """Costo unitario por SKU desde vista_costo_sku. Filtros opcionales."""
    where, params = [], []
    if sku:
        where.append("codigo = %s")
        params.append(sku)
    if receta:
        where.append("nombre_cerveza ILIKE %s")
        params.append(f"%{receta}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    cur.execute(f"""
        SELECT codigo, nombre_cerveza, formato,
               costo_liquido_unitario, costo_envasado_unitario, costo_total_unitario
        FROM vista_costo_sku
        {where_sql}
        ORDER BY nombre_cerveza, formato, codigo
    """, params)
    return [
        {"codigo": r["codigo"], "cerveza": r["nombre_cerveza"], "formato": r["formato"],
         "costo_liquido": (float(r["costo_liquido_unitario"])
                           if r["costo_liquido_unitario"] is not None else None),
         "costo_envasado": (float(r["costo_envasado_unitario"])
                            if r["costo_envasado_unitario"] is not None else None),
         "costo_total": (float(r["costo_total_unitario"])
                         if r["costo_total_unitario"] is not None else None)}
        for r in cur.fetchall()
    ]


# Ventana del promedio: mas atras el precio ya no es comparable (cambian los
# costos y la lista). El precio ULTIMO no usa ventana.
DIAS_PROMEDIO = 180


def _envase_es_pass_through(formato):
    """¿El envase de este formato se le cobra al cliente en línea aparte?

    Solo el barril PET. La factura trae su propia línea ("Barril Pet 30L") por
    el costo exacto del envase, así que el cliente ya lo pagó: descontarlo del
    margen sería cobrarlo dos veces y arroja una pérdida falsa. El envase de la
    botella (botella, tapa, etiqueta, caja) NO va en línea aparte: ese sí es
    costo propio y entra en el margen.
    """
    return "pet" in _norm(formato).split()


SQL_NETO_PERIODO = """
    SELECT COALESCE(SUM(COALESCE(monto_neto_ajustado, monto_neto)), 0) AS neto,
           COUNT(*) AS n
    FROM ventas
    WHERE tipo_documento != 61
      AND fecha BETWEEN %s AND %s
"""


def margen_periodo(cur, desde, hasta):
    """Margen realizado de un período: lo que se vendió menos lo que costó.

    Es la pregunta de gerente ("¿cuánto gané en junio?") y no se puede sacar de
    `margenes`, que da el margen unitario de catálogo: aquí hay que multiplicar
    por lo efectivamente vendido, factura por factura, al precio real de cada
    una (que cambia según el cliente).

    Declara siempre qué NO pudo costear. Varias cervezas que se venden (RIS,
    APA, Sour…) no tienen receta cargada, así que un total que las ignore en
    silencio se ve preciso y está mal. `cobertura_pct` dice sobre qué fracción
    de la venta del período se calculó el margen.
    """
    costos_por_clave = {}
    for sku in costos_sku(cur):
        clave = precios_venta.clave_formato_desde_nombre(sku["formato"])
        if not clave or sku["costo_total"] is None:
            continue
        # El envase PET va facturado aparte a costo: no se descuenta del margen.
        costo = sku["costo_total"]
        if _envase_es_pass_through(sku["formato"]) and sku["costo_liquido"] is not None:
            costo = sku["costo_liquido"]
        # Acero y PET comparten clave; el primero que llega fija el costo del
        # liquido, que es el que corresponde comparar en ambos.
        costos_por_clave.setdefault((_norm(sku["cerveza"]), clave),
                                    {"formato": sku["formato"], "costo": costo})

    muestras, _descartadas = precios_venta.recolectar_muestras(cur, desde=desde, hasta=hasta)

    por_producto, sin_costo = {}, {}
    for m in muestras:
        ingreso = m["precio"] * m["unidades"]
        ref = (costos_por_clave.get((_norm(m["cerveza"]), m["formato"]))
               if m["cerveza"] and m["formato"] else None)
        if ref is None:
            acum = sin_costo.setdefault(m["nombre"], {"producto": m["nombre"],
                                                      "ingreso": 0.0, "unidades": 0.0})
            acum["ingreso"] += ingreso
            acum["unidades"] += m["unidades"]
            continue
        clave = (m["cerveza"], ref["formato"])
        acum = por_producto.setdefault(clave, {
            "cerveza": m["cerveza"], "formato": ref["formato"],
            "unidades": 0.0, "ingreso": 0.0, "costo": 0.0})
        acum["unidades"] += m["unidades"]
        acum["ingreso"] += ingreso
        acum["costo"] += ref["costo"] * m["unidades"]

    filas = []
    for acum in por_producto.values():
        acum["margen"] = acum["ingreso"] - acum["costo"]
        acum["margen_pct"] = (round(100 * acum["margen"] / acum["ingreso"], 1)
                              if acum["ingreso"] else None)
        filas.append(acum)
    filas.sort(key=lambda x: x["margen"], reverse=True)

    ingreso = sum(f["ingreso"] for f in filas)
    costo = sum(f["costo"] for f in filas)

    cur.execute(SQL_NETO_PERIODO, (desde, hasta))
    fila = cur.fetchone() or {}
    ventas_netas = float(fila.get("neto") or 0)

    return {
        "desde": desde, "hasta": hasta,
        "ventas_netas": ventas_netas,
        "n_facturas": int(fila.get("n") or 0),
        "ingreso_costeado": ingreso,
        "costo": costo,
        "margen": ingreso - costo,
        "margen_pct": round(100 * (ingreso - costo) / ingreso, 1) if ingreso else None,
        "cobertura_pct": round(100 * ingreso / ventas_netas, 1) if ventas_netas else None,
        "por_producto": filas,
        "sin_costo": sorted(sin_costo.values(), key=lambda x: x["ingreso"], reverse=True),
    }


def margen_cliente(cur, cliente, receta=None):
    """Margen de cada SKU al precio que paga UN cliente, contra el general.

    Existe porque los descuentos son reales y grandes: A & C paga la Scotch a
    $47.836 en vez de $55.370, así que su margen es 12,5% y no 24,4%. Sin esta
    función el agente tenía que reconstruir el precio con SQL a mano sobre
    `productos` — justo donde la doble línea lo engaña — y se quedaba sin pasos.

    Devuelve solo los SKU que ese cliente compró. Lista vacía = no le hemos
    vendido (o el nombre no calza con ningún cliente).
    """
    generales = {(_norm(m["cerveza"]), m["formato"]): m for m in margenes(cur, receta=receta)}
    del_cliente = precios_venta.precios_por_formato(cur, cliente=cliente)["precios"]

    salida = []
    for p in del_cliente:
        clave_sku = None
        for (cerveza_norm, formato_sku), m in generales.items():
            if cerveza_norm != _norm(p["cerveza"]):
                continue
            if precios_venta.clave_formato_desde_nombre(formato_sku) == p["formato"]:
                clave_sku = m
                break
        if clave_sku is None or clave_sku["costo_comparable"] is None:
            continue          # ese formato no tiene SKU con costo cargado

        costo = clave_sku["costo_comparable"]
        precio = p["precio_ultimo"]
        margen = float(precio) - costo
        salida.append({
            "cerveza": p["cerveza"], "formato": clave_sku["formato"],
            "costo": costo,
            "precio_cliente": precio,
            "precio_general": clave_sku["precio_venta"],
            "margen": margen,
            "margen_pct": round(100 * margen / precio, 1) if precio else None,
            "margen_pct_general": clave_sku["margen_pct"],
            "n_facturas": p["n_facturas"],
            "fecha_ultimo": p["fecha_ultimo"],
        })
    salida.sort(key=lambda x: (x["cerveza"], x["formato"]))
    return salida


def margenes(cur, receta=None):
    """Margen por SKU = precio de venta − costo total.

    El precio sale de las facturas (fuente principal, refleja lo que realmente
    se cobró, descuentos incluidos). Si el SKU no se ha vendido nunca, cae al
    precio confirmado por el productor. Sin ninguno de los dos, el margen queda
    en None: nunca se inventa.
    """
    skus = costos_sku(cur, receta=receta)
    deducidos = precios_venta.precios_por_formato(cur, dias=DIAS_PROMEDIO)["precios"]
    por_clave = {(_norm(p["cerveza"]), p["formato"]): p for p in deducidos}

    salida = []
    for sku in skus:
        clave = precios_venta.clave_formato_desde_nombre(sku["formato"])
        ref = por_clave.get((_norm(sku["cerveza"]), clave)) if clave else None

        if ref:
            precio, origen = ref["precio_ultimo"], "facturas"
        else:
            precio = _precio_venta(sku["cerveza"], sku["formato"])
            origen = "lista" if precio is not None else None

        # El precio deducido excluye la línea del envase PET (pass-through), así
        # que el costo contra el que se compara también debe excluirlo. Si no,
        # se descuenta un envase que el cliente ya pagó aparte y el barril PET
        # aparece con pérdida.
        pass_through = _envase_es_pass_through(sku["formato"])
        costo_comparable = sku["costo_total"]
        if pass_through and sku["costo_liquido"] is not None:
            costo_comparable = sku["costo_liquido"]

        margen = margen_pct = None
        if precio is not None and costo_comparable is not None:
            margen = float(precio) - costo_comparable
            margen_pct = round(100 * margen / precio, 1) if precio else None

        salida.append({
            "codigo": sku["codigo"], "cerveza": sku["cerveza"], "formato": sku["formato"],
            "costo_total": sku["costo_total"],
            "costo_comparable": costo_comparable,
            "envase_pass_through": pass_through,
            "precio_venta": float(precio) if precio is not None else None,
            "margen": margen, "margen_pct": margen_pct,
            "origen": origen,
            "precio_promedio": ref["precio_promedio"] if ref else None,
            "n_facturas": ref["n_facturas"] if ref else None,
        })
    return salida
