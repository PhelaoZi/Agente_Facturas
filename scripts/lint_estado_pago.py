#!/usr/bin/env python3
"""
lint_estado_pago.py - Zigurat ERP

Audita la invariante de estado de pago:

    Toda factura con conciliacion bancaria DEBE tener fecha_pago.
    (conciliaciones  =>  fecha_pago)

`ventas.fecha_pago IS NOT NULL` es la fuente de verdad unica del estado de
cobro. `conciliaciones` es solo evidencia de respaldo. Si aparecen facturas
con conciliacion pero sin `fecha_pago`, el estado de cobro quedo inconsistente
(distintas consultas daran deudas distintas).

Sale con codigo 1 si hay inconsistencias (util para hooks/CI), 0 si todo OK.

Uso:
    python scripts/lint_estado_pago.py
"""

import os
import sys
from pathlib import Path

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(2)


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

# Violaciones de la invariante: conciliacion presente pero fecha_pago NULL.
QUERY_VIOLACIONES = """
    SELECT v.folio, v.rut_cliente, v.fecha,
           COALESCE(v.monto_total_ajustado, v.monto_total) AS total
    FROM ventas v
    WHERE v.tipo_documento != 61
      AND v.fecha_pago IS NULL
      AND EXISTS (SELECT 1 FROM conciliaciones c WHERE c.folio_venta = v.folio)
    ORDER BY v.folio
"""


def main():
    print("=" * 60)
    print("LINT estado de pago — invariante conciliaciones => fecha_pago")
    print("=" * 60)

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(2)

    try:
        with conn.cursor() as cur:
            cur.execute(QUERY_VIOLACIONES)
            violaciones = cur.fetchall()
    finally:
        conn.close()

    if not violaciones:
        print("  [OK] 0 inconsistencias. fecha_pago es la fuente de verdad unica.")
        sys.exit(0)

    print(f"  [!] {len(violaciones)} factura(s) con conciliacion pero SIN fecha_pago:")
    print()
    for v in violaciones[:30]:
        print(f"    folio {v['folio']}  {v['rut_cliente']}  {v['fecha']}  ${v['total']}")
    if len(violaciones) > 30:
        print(f"    ... y {len(violaciones) - 30} mas")
    print()
    print("  Corrige con: python scripts/migrate_backfill_fecha_pago.py")
    sys.exit(1)


if __name__ == "__main__":
    main()
