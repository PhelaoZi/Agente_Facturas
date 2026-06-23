#!/usr/bin/env python3
"""
revertir_faciles_2026.py - Zigurat ERP (correccion)

marcar_faciles_2025.py marco por error tambien facturas 2026 de los 5 clientes
"faciles" (incluyendo recientes aun no vencidas). Esto las devuelve a PENDIENTE
para que se resuelvan con la cartola del banco, como el resto de 2026.

Solo toca los folios listados (los 14 de 2026 que ese script habia marcado) y
solo si la factura es del 2026. Las de 2025 quedan pagadas (correctas).
"""
import os
import sys
from pathlib import Path

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: falta psycopg2.")
    sys.exit(1)

FOLIOS_2026 = [4569, 4684, 4554, 4651, 4616, 4617, 4631, 4630,
               4642, 4665, 4670, 4673, 4686, 4694]


def _load_env() -> None:
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


def main() -> None:
    conn = psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ventas SET fecha_pago = NULL, dias_pago = NULL
                    WHERE folio = ANY(%s) AND fecha >= '2026-01-01'
                      AND tipo_documento::text != '61'
                """, (FOLIOS_2026,))
                n = cur.rowcount
    finally:
        conn.close()
    print(f"Revertidas a pendiente: {n} facturas 2026.")


if __name__ == "__main__":
    main()
