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
