#!/usr/bin/env python3
"""
conciliar_banco.py - Zigurat ERP
Concilia movimientos bancarios (transferencias recibidas) con facturas emitidas.

Algoritmo:
  1. Match exacto:   rut_emisor == rut_cliente AND monto_abono == monto_total_pendiente
  2. Match multiple: rut_emisor == rut_cliente AND monto_abono == SUMA de N facturas
  3. Sin match:      monto no coincide con nada del RUT -> reportar para revision manual

Modo interactivo: muestra reporte completo y pide confirmacion antes de guardar.

Uso:
    python scripts/conciliar_banco.py
    python scripts/conciliar_banco.py --auto    (no pide confirmacion, util para tests)
"""

import os
import sys
from pathlib import Path
from itertools import combinations

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)


def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

TOLERANCIA = 200  # pesos de diferencia permitida por redondeos


def normalizar_rut(rut):
    """Normaliza RUT para comparacion sin puntos, con guion."""
    if not rut:
        return None
    return str(rut).strip().upper().replace(".", "")


def monto_pendiente(factura):
    """Retorna el monto pendiente real de cobro (usando ajuste si existe)."""
    ajustado = factura.get('monto_total_ajustado')
    if ajustado is not None:
        return float(ajustado)
    return float(factura['monto_total'])


def encontrar_match(monto_transferencia, facturas_cliente):
    """
    Intenta encontrar un subconjunto de facturas cuya suma == monto_transferencia.
    Tolera diferencia <= TOLERANCIA.
    Retorna lista de facturas que hacen match, o None si no encuentra.
    Limita busqueda a combinaciones de hasta 6 facturas.
    """
    MAX_COMBO = 6

    for n in range(1, min(len(facturas_cliente) + 1, MAX_COMBO + 1)):
        for combo in combinations(facturas_cliente, n):
            suma = sum(monto_pendiente(f) for f in combo)
            if abs(suma - monto_transferencia) <= TOLERANCIA:
                return list(combo)

    return None


def obtener_movimientos_pendientes(cur):
    """Retorna movimientos bancarios no conciliados con monto_abono > 0."""
    cur.execute("""
        SELECT id, fecha, rut_emisor, nombre_emisor, monto_abono, codigo_transferencia
        FROM movimientos_banco
        WHERE conciliado = FALSE
          AND monto_abono > 0
        ORDER BY fecha, rut_emisor
    """)
    return cur.fetchall()


def obtener_facturas_pendientes(cur):
    """
    Retorna facturas sin fecha_pago agrupadas por rut_cliente normalizado.
    """
    cur.execute("""
        SELECT
            folio,
            fecha,
            rut_cliente,
            razon_social_receptor,
            COALESCE(monto_total_ajustado, monto_total) AS monto_total_ajustado,
            monto_total
        FROM ventas
        WHERE fecha_pago IS NULL
          AND tipo_documento != '61'
        ORDER BY rut_cliente, fecha
    """)
    rows = cur.fetchall()

    por_rut = {}
    for row in rows:
        rut = normalizar_rut(row['rut_cliente'])
        if rut not in por_rut:
            por_rut[rut] = []
        por_rut[rut].append(dict(row))

    return por_rut


def analizar(movimientos, facturas_por_rut):
    """
    Clasifica cada movimiento en exactos, sin_match, o sin_cliente.
    """
    exactos = []
    sin_match = []
    sin_cliente = []

    for mov in movimientos:
        rut = normalizar_rut(mov['rut_emisor'])
        facturas = facturas_por_rut.get(rut, [])

        if not facturas:
            sin_cliente.append(mov)
            continue

        match = encontrar_match(float(mov['monto_abono']), facturas)
        if match:
            exactos.append((dict(mov), match))
        else:
            sin_match.append((dict(mov), facturas))

    return exactos, sin_match, sin_cliente


def fmt_pesos(n):
    """Formatea numero como pesos chilenos."""
    return "$" + "{:,.0f}".format(float(n)).replace(",", ".")


def mostrar_reporte(exactos, sin_match, sin_cliente):
    """Imprime el reporte de conciliacion."""
    sep = "=" * 70

    print()
    print(sep)
    print("REPORTE DE CONCILIACION BANCARIA")
    print(sep)
    print()

    print(f"[OK] MATCHES ENCONTRADOS ({len(exactos)})")
    print("-" * 70)
    if exactos:
        for mov, facturas in exactos:
            folios = ", ".join(str(f['folio']) for f in facturas)
            cliente = facturas[0]['razon_social_receptor']
            suma = sum(monto_pendiente(f) for f in facturas)
            diff = abs(suma - float(mov['monto_abono']))
            print(f"  {mov['fecha']} | {mov['nombre_emisor']}")
            print(f"    Monto recibido: {fmt_pesos(mov['monto_abono'])}")
            print(f"    Cubre {len(facturas)} factura(s): folio(s) {folios}")
            print(f"    Cliente: {cliente}")
            if len(facturas) > 1 or diff > 0:
                print(f"    Suma facturas: {fmt_pesos(suma)}  (diff: {fmt_pesos(diff)})")
            print()
    else:
        print("  (ninguno)")
        print()

    print(f"[!] SIN MATCH - REVISION MANUAL ({len(sin_match)})")
    print("-" * 70)
    if sin_match:
        for mov, facturas in sin_match:
            folios = [f['folio'] for f in facturas]
            montos = [fmt_pesos(monto_pendiente(f)) for f in facturas]
            print(f"  {mov['fecha']} | {mov['nombre_emisor']}")
            print(f"    Monto recibido: {fmt_pesos(mov['monto_abono'])}")
            print(f"    Facturas pendientes: folios {folios}")
            print(f"    Montos pendientes:   {montos}")
            print(f"    -> Conciliar manualmente en la BD")
            print()
    else:
        print("  (ninguno)")
        print()

    print(f"[i] TRANSFERENCIAS SIN FACTURAS PENDIENTES ({len(sin_cliente)})")
    print("-" * 70)
    if sin_cliente:
        for mov in sin_cliente:
            print(f"  {mov['fecha']} | {mov['nombre_emisor']} | {fmt_pesos(mov['monto_abono'])}")
    else:
        print("  (ninguno)")
    print()


def confirmar():
    """Pide confirmacion al usuario. Retorna True si confirma."""
    while True:
        resp = input("Confirmar conciliacion de los matches encontrados? [s/N]: ").strip().lower()
        if resp in ('s', 'si', 'y', 'yes'):
            return True
        if resp in ('n', 'no', ''):
            return False
        print("  Responde 's' para confirmar o 'n' para cancelar.")


def aplicar_conciliacion(cur, exactos):
    """
    Aplica la conciliacion:
    - INSERT en conciliaciones
    - UPDATE ventas.fecha_pago y dias_pago
    - UPDATE movimientos_banco.conciliado = TRUE
    """
    for mov, facturas in exactos:
        for factura in facturas:
            cur.execute("""
                INSERT INTO conciliaciones
                    (folio_venta, movimiento_banco_id, monto_aplicado, fecha_conciliacion)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT DO NOTHING
            """, (factura['folio'], mov['id'], monto_pendiente(factura)))

            cur.execute("""
                UPDATE ventas
                SET fecha_pago = %s,
                    dias_pago  = %s::date - fecha
                WHERE folio = %s
                  AND tipo_documento != '61'
            """, (mov['fecha'], mov['fecha'], factura['folio']))

        cur.execute("""
            UPDATE movimientos_banco
            SET conciliado = TRUE
            WHERE id = %s
        """, (mov['id'],))


def main():
    auto = '--auto' in sys.argv

    print("=" * 70)
    print("ZIGURAT ERP - Conciliacion Bancaria")
    print("=" * 70)
    print()

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    print(f"Conectado a: {DB_CONFIG['dbname']}")

    try:
        with conn.cursor() as cur:
            movimientos = obtener_movimientos_pendientes(cur)
            facturas_por_rut = obtener_facturas_pendientes(cur)

        print(f"  Movimientos sin conciliar: {len(movimientos)}")
        print(f"  Clientes con facturas pendientes: {len(facturas_por_rut)}")

        if not movimientos:
            print()
            print("No hay movimientos pendientes de conciliar.")
            print("Usa /importar-transferencias para cargar el Excel del banco.")
            return

        exactos, sin_match, sin_cliente = analizar(movimientos, facturas_por_rut)
        mostrar_reporte(exactos, sin_match, sin_cliente)

        if not exactos:
            print("No hay matches para conciliar automaticamente.")
            return

        total_facturas = sum(len(f) for _, f in exactos)
        print(f"Se van a conciliar {len(exactos)} transferencias")
        print(f"(Facturas a marcar como pagadas: {total_facturas})")
        print()

        if auto or confirmar():
            with conn:
                with conn.cursor() as cur:
                    aplicar_conciliacion(cur, exactos)

            print()
            print("Conciliacion guardada exitosamente")
            print(f"  Transferencias conciliadas: {len(exactos)}")
            print(f"  Facturas marcadas como pagadas: {total_facturas}")
        else:
            print()
            print("Conciliacion cancelada. No se guardo nada.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
