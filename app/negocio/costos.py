"""Costos por SKU y márgenes (solo lectura).

Costos: misma consulta a vista_costo_sku que scripts/costo_sku.py.
Márgenes: cruza el costo total con los precios de venta netos confirmados.
Los precios son por BARRIL 30L (confirmados por el productor, ver CLAUDE.md);
para botellas no hay precio confirmado, así que el margen queda en None.
"""
import unicodedata

# Precios de venta netos confirmados por barril 30L (desde CLAUDE.md).
PRECIOS_VENTA_NETO = {
    "cream ale": 55370,
    "scotch ale": 55370,
    "stout cafe": 75000,
    "stout cacao": 75000,
    "paint it black": 98000,
}


def _norm(s):
    """Normaliza para comparar nombres: minúsculas, sin tildes, espacios simples."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


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


def margenes(cur, receta=None):
    """Margen por SKU = precio de venta confirmado − costo total.

    Solo para formatos de barril (donde hay precio confirmado). Para botellas
    u otros, precio_venta y margen quedan en None (no se inventa un margen).
    """
    salida = []
    for sku in costos_sku(cur, receta=receta):
        precio = None
        if "barril" in _norm(sku["formato"]):
            precio = PRECIOS_VENTA_NETO.get(_norm(sku["cerveza"]))
        margen = None
        margen_pct = None
        if precio is not None and sku["costo_total"] is not None:
            margen = float(precio) - sku["costo_total"]
            margen_pct = round(100 * margen / precio, 1) if precio else None
        salida.append({
            "codigo": sku["codigo"], "cerveza": sku["cerveza"], "formato": sku["formato"],
            "costo_total": sku["costo_total"],
            "precio_venta": float(precio) if precio is not None else None,
            "margen": margen, "margen_pct": margen_pct,
        })
    return salida
