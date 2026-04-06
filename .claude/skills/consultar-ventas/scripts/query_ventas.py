#!/usr/bin/env python3
"""Consultas predefinidas sobre ventas — Zigurat ERP.

Uso: python query_ventas.py <tipo> [opciones]

Tipos:
  ranking   [--limit N]                              Top N clientes
  cliente   --nombre NOMBRE                          Ventas de un cliente
  periodo   --desde YYYY-MM-DD --hasta YYYY-MM-DD    Ventas por rango
  listado   --desde YYYY-MM-DD --hasta YYYY-MM-DD    Facturas individuales por rango
  facturas  --nombre NOMBRE                          Facturas de un cliente
  total                                              Total global vendido
  producto  --nombre NOMBRE                          Buscar por producto
  detalle   --folio FOLIO                            Detalle de una factura
  resumen                                            Estadisticas generales
"""
import argparse
import sys

import psycopg2

sys.stdout.reconfigure(encoding="utf-8")

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "dte_facturas_chile",
    "user": "postgres",
    "password": "zigurat",
}

TIPO_DOC = {
    33: "Fact.Afecta",
    34: "Fact.Exenta",
    39: "Boleta",
    41: "Boleta Exenta",
    52: "Guia Despacho",
    61: "Nota de Credito",
}


def connect():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        print(f"ERROR: No se pudo conectar a la BD: {e}", file=sys.stderr)
        sys.exit(1)


def fmt(n):
    """Formato peso chileno: $1.234.567"""
    if n is None:
        return "$0"
    sign = "-" if n < 0 else ""
    s = f"{abs(int(n)):,}".replace(",", ".")
    return f"{sign}${s}"


def table(headers, rows, mcols=None):
    """Imprime tabla Markdown. mcols = indices de columnas monetarias."""
    mcols = mcols or []
    frows = []
    for row in rows:
        frow = []
        for i, v in enumerate(row):
            if i in mcols:
                frow.append(fmt(v))
            elif v is None:
                frow.append("-")
            else:
                frow.append(str(v))
        frows.append(frow)
    widths = [len(h) for h in headers]
    for row in frows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))
    print("| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |")
    print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in frows:
        print("| " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(row)) + " |")


# === QUERIES ===

def q_ranking(cur, limit=10):
    cur.execute("""
        SELECT c.razon_social, v.rut_cliente,
               SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS total_real
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
        GROUP BY v.rut_cliente, c.razon_social
        ORDER BY total_real DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    if not rows:
        print("No se encontraron registros.")
        return
    print(f"Top {limit} clientes por venta real:\n")
    table(["Cliente", "RUT", "Total Real"], rows, mcols=[2])
    print(f"\nTotal mostrado: {fmt(sum(r[2] for r in rows))}")


def q_cliente(cur, nombre):
    cur.execute("""
        SELECT v.folio, v.tipo_documento, v.fecha,
               CASE WHEN v.tipo_documento = 61 THEN v.monto_total
                    ELSE COALESCE(v.monto_total_ajustado, v.monto_total)
               END AS monto,
               v.folio_referencia
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE c.razon_social ILIKE %s
        ORDER BY v.fecha DESC
    """, (f"%{nombre}%",))
    rows = cur.fetchall()
    if not rows:
        print(f"No se encontraron ventas para cliente '{nombre}'.")
        return
    # Separate invoices and NCs
    facturas = [r for r in rows if r[1] != 61]
    ncs = [r for r in rows if r[1] == 61]
    # Display with tipo label and ref folio for NCs
    display = []
    for r in rows:
        tipo = TIPO_DOC.get(r[1], str(r[1]))
        ref = f" (ref:{r[4]})" if r[4] else ""
        display.append((r[0], tipo + ref, r[2], r[3]))
    print(f"Documentos de '{nombre}':\n")
    table(["Folio", "Tipo", "Fecha", "Monto"], display, mcols=[3])
    total_real = sum(r[3] for r in facturas)
    print(f"\nFacturas: {len(facturas)} | Notas de Credito: {len(ncs)}")
    print(f"Total real (ajustado): {fmt(total_real)}")


def q_periodo(cur, desde, hasta):
    cur.execute("""
        SELECT c.razon_social, v.rut_cliente,
               SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS total_real
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61 AND v.fecha BETWEEN %s AND %s
        GROUP BY v.rut_cliente, c.razon_social
        ORDER BY total_real DESC
    """, (desde, hasta))
    rows = cur.fetchall()
    if not rows:
        print(f"No se encontraron ventas entre {desde} y {hasta}.")
        return
    print(f"Ventas por cliente ({desde} a {hasta}):\n")
    table(["Cliente", "RUT", "Total Real"], rows, mcols=[2])
    print(f"\nTotal periodo: {fmt(sum(r[2] for r in rows))}")


def q_listado(cur, desde, hasta):
    """Lista todas las facturas individuales en un rango de fechas."""
    cur.execute("""
        SELECT v.folio, v.fecha, c.razon_social,
               COALESCE(v.monto_total_ajustado, v.monto_total) AS total_real
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61 AND v.fecha BETWEEN %s AND %s
        ORDER BY v.fecha, v.folio::integer
    """, (desde, hasta))
    rows = cur.fetchall()
    if not rows:
        print(f"No se encontraron facturas entre {desde} y {hasta}.")
        return
    print(f"Facturas emitidas ({desde} a {hasta}):\n")
    table(["Folio", "Fecha", "Cliente", "Total Real"], rows, mcols=[3])
    print(f"\nTotal: {len(rows)} facturas | {fmt(sum(r[3] for r in rows))}")


def q_facturas(cur, nombre):
    cur.execute("""
        SELECT v.folio, v.fecha, v.monto_neto, v.iva, v.monto_total,
               COALESCE(v.monto_total_ajustado, v.monto_total) AS total_ajust
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE c.razon_social ILIKE %s AND v.tipo_documento != 61
        ORDER BY v.fecha DESC
    """, (f"%{nombre}%",))
    rows = cur.fetchall()
    if not rows:
        print(f"No se encontraron facturas para '{nombre}'.")
        return
    print(f"Facturas de '{nombre}':\n")
    table(["Folio", "Fecha", "Neto", "IVA", "Total", "Total Ajust."], rows, mcols=[2, 3, 4, 5])


def q_total(cur):
    cur.execute("""
        SELECT COUNT(*),
               SUM(COALESCE(v.monto_total_ajustado, v.monto_total))
        FROM ventas v
        WHERE v.tipo_documento != 61
    """)
    row = cur.fetchone()
    print(f"Total vendido (real): {fmt(row[1])}")
    print(f"Facturas emitidas: {row[0]}")


def q_producto(cur, nombre):
    cur.execute("""
        SELECT p.folio, v.fecha, c.razon_social, p.descripcion,
               p.cantidad, p.precio_unitario
        FROM productos p
        JOIN ventas v ON v.folio = p.folio
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE p.descripcion ILIKE %s AND v.tipo_documento != 61
        ORDER BY v.fecha DESC
    """, (f"%{nombre}%",))
    rows = cur.fetchall()
    if not rows:
        print(f"No se encontraron productos con '{nombre}'.")
        return
    print(f"Productos que coinciden con '{nombre}':\n")
    table(["Folio", "Fecha", "Cliente", "Producto", "Cant.", "P.Unit."], rows, mcols=[5])


def q_detalle(cur, folio):
    cur.execute("""
        SELECT v.folio, v.tipo_documento, v.fecha, c.razon_social, v.rut_cliente,
               v.monto_neto, v.iva, v.impuesto_adicional, v.monto_total,
               v.monto_neto_ajustado, v.monto_total_ajustado, v.folio_referencia
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.folio = %s
    """, (folio,))
    row = cur.fetchone()
    if not row:
        print(f"No se encontro factura con folio {folio}.")
        return
    print(f"Detalle factura #{row[0]}:\n")
    print(f"  Tipo:        {TIPO_DOC.get(row[1], row[1])}")
    print(f"  Fecha:       {row[2]}")
    print(f"  Cliente:     {row[3]} ({row[4]})")
    print(f"  Neto:        {fmt(row[5])}")
    print(f"  IVA:         {fmt(row[6])}")
    if row[7]:
        print(f"  Imp.Adic.:   {fmt(row[7])}")
    print(f"  Total:       {fmt(row[8])}")
    if row[9] is not None:
        print(f"  Neto Ajust.: {fmt(row[9])}")
    if row[10] is not None:
        print(f"  Total Ajust.:{fmt(row[10])}")
    if row[11]:
        print(f"  Ref. Folio:  {row[11]}")
    cur.execute("SELECT descripcion, cantidad, precio_unitario FROM productos WHERE folio = %s", (folio,))
    prods = cur.fetchall()
    if prods:
        print(f"\nProductos ({len(prods)}):\n")
        table(["Descripcion", "Cantidad", "P.Unitario"], prods, mcols=[2])


def q_resumen(cur):
    cur.execute("""
        SELECT COUNT(*),
               SUM(COALESCE(v.monto_total_ajustado, v.monto_total)),
               AVG(COALESCE(v.monto_total_ajustado, v.monto_total)),
               MIN(v.fecha), MAX(v.fecha),
               COUNT(DISTINCT v.rut_cliente)
        FROM ventas v
        WHERE v.tipo_documento != 61
    """)
    r = cur.fetchone()
    print("Resumen general de ventas:\n")
    print(f"  Facturas emitidas: {r[0]}")
    print(f"  Total real:        {fmt(r[1])}")
    print(f"  Promedio/factura:  {fmt(r[2])}")
    print(f"  Primera factura:   {r[3]}")
    print(f"  Ultima factura:    {r[4]}")
    print(f"  Clientes unicos:   {r[5]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("tipo", choices=["ranking", "cliente", "periodo", "listado",
                                     "facturas", "total", "producto", "detalle",
                                     "resumen"])
    p.add_argument("--nombre", "-n")
    p.add_argument("--limit", "-l", type=int, default=10)
    p.add_argument("--desde")
    p.add_argument("--hasta")
    p.add_argument("--folio", "-f", type=int)
    args = p.parse_args()

    conn = connect()
    cur = conn.cursor()
    try:
        if args.tipo == "ranking":
            q_ranking(cur, args.limit)
        elif args.tipo == "cliente":
            if not args.nombre:
                print("ERROR: --nombre requerido", file=sys.stderr); sys.exit(1)
            q_cliente(cur, args.nombre)
        elif args.tipo == "periodo":
            if not args.desde or not args.hasta:
                print("ERROR: --desde y --hasta requeridos", file=sys.stderr); sys.exit(1)
            q_periodo(cur, args.desde, args.hasta)
        elif args.tipo == "listado":
            if not args.desde or not args.hasta:
                print("ERROR: --desde y --hasta requeridos", file=sys.stderr); sys.exit(1)
            q_listado(cur, args.desde, args.hasta)
        elif args.tipo == "facturas":
            if not args.nombre:
                print("ERROR: --nombre requerido", file=sys.stderr); sys.exit(1)
            q_facturas(cur, args.nombre)
        elif args.tipo == "total":
            q_total(cur)
        elif args.tipo == "producto":
            if not args.nombre:
                print("ERROR: --nombre requerido", file=sys.stderr); sys.exit(1)
            q_producto(cur, args.nombre)
        elif args.tipo == "detalle":
            if not args.folio:
                print("ERROR: --folio requerido", file=sys.stderr); sys.exit(1)
            q_detalle(cur, args.folio)
        elif args.tipo == "resumen":
            q_resumen(cur)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
