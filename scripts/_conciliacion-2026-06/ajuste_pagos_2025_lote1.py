#!/usr/bin/env python3
"""
ajuste_pagos_2025_lote1.py - Zigurat ERP

Revision manual de deudores 2025 - lote 1 (decisiones del dueno, 2026-06-15):

  - MONTESPINO TRANSPORTES (77945613-7): cliente de una sola vez, pago a 30 dias.
  - NIGEL GALLAGHER       (21752946-8): fuera del negocio pero dejo todo pagado.
  - NYD BIER              (76938534-7): pagado.
    -> Sus facturas pendientes se marcan PAGADAS a 30 dias (emision + 30).
    -> Nigel y NYD estaban como 'incobrable' por esas facturas; al quedar
       pagadas dejan de ser mala deuda -> estado vuelve a 'activo'.

  - GALPON ALONSO (77615028-2): quebro, deuda incobrable. Ya estaba marcado
    'incobrable' y su deuda 2025 se deja tal cual (no se toca). Se omite aqui.

Reversible: guarda en logs/ajuste_pagos_2025_lote1_<ts>.json los folios
afectados (estaban en fecha_pago IS NULL) y el estado anterior de cada cliente.

NO crea filas en conciliaciones (es inferencia, no evidencia bancaria).
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: falta psycopg2. pip install psycopg2-binary")
    sys.exit(1)

DIAS = 30

# rut -> (nombre, reactivar_a_activo)
PAGADAS_30D = {
    "77945613-7": ("Montespino Transportes", False),  # ya estaba activo
    "21752946-8": ("Nigel Gallagher",        True),
    "76938534-7": ("NYD Bier",               True),
}


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
    print("=" * 64)
    print("  ZIGURAT - Ajuste deudores 2025 (lote 1)")
    print("=" * 64)

    try:
        conn = psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: no se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    undo = {"folios_marcados_pagados": [], "estado_anterior": {}}
    total_marcadas = 0
    try:
        with conn:
            with conn.cursor() as cur:
                for rut, (nombre, reactivar) in PAGADAS_30D.items():
                    # Estado anterior (para revertir)
                    cur.execute("SELECT estado FROM clientes WHERE rut_cliente = %s", (rut,))
                    row = cur.fetchone()
                    undo["estado_anterior"][rut] = row["estado"] if row else None

                    # Facturas pendientes positivas del cliente
                    cur.execute("""
                        SELECT folio, fecha
                        FROM ventas
                        WHERE rut_cliente = %s AND fecha_pago IS NULL
                          AND tipo_documento::text != '61'
                          AND COALESCE(monto_total_ajustado, monto_total) > 0
                    """, (rut,))
                    pend = cur.fetchall()
                    for r in pend:
                        fecha_pago = r["fecha"] + timedelta(days=DIAS)
                        cur.execute("""
                            UPDATE ventas SET fecha_pago = %s, dias_pago = %s
                            WHERE folio = %s AND tipo_documento::text != '61'
                        """, (fecha_pago, DIAS, r["folio"]))
                        undo["folios_marcados_pagados"].append(r["folio"])
                        total_marcadas += 1

                    if reactivar:
                        cur.execute(
                            "UPDATE clientes SET estado = 'activo' WHERE rut_cliente = %s", (rut,))

                    print(f"  {nombre:24s}: {len(pend)} factura(s) pagada(s)"
                          + ("  + estado -> activo" if reactivar else ""))

    finally:
        conn.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"ajuste_pagos_2025_lote1_{ts}.json"
    log_file.write_text(json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 64)
    print(f"  Total facturas marcadas pagadas: {total_marcadas}")
    print(f"  Log reversible: {log_file}")
    print("=" * 64)


if __name__ == "__main__":
    main()
