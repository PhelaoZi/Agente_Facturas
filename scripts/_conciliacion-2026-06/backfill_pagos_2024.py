#!/usr/bin/env python3
"""
backfill_pagos_2024.py - Zigurat ERP

Marca como PAGADAS todas las facturas de 2024 que quedaron sin fecha_pago.

Contexto (decision del dueno, 2026-06-15): el banco se importo solo hasta
2025-12-29, por lo que muchas facturas pagadas por transferencia siguen como
pendientes. Para el 2024 se asume que toda la cartera esta saldada y debe
quedar sin deuda.

Fecha de pago inferida (no es evidencia bancaria, es supuesto):
  - Cliente CON historial de dias_pago > 0  -> fecha_emision + promedio del cliente.
  - Cliente SIN historial                   -> fecha_emision + 30 dias.

Reversible: antes de tocar nada, guarda en logs/backfill_pagos_2024_<ts>.txt
todos los folios afectados (estaban en fecha_pago IS NULL). Para deshacer:
  UPDATE ventas SET fecha_pago=NULL, dias_pago=NULL WHERE folio IN (<esos folios>);

NO crea filas en conciliaciones (no hay respaldo bancario; es inferencia).

Uso:
    python scripts/backfill_pagos_2024.py
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: falta psycopg2. pip install psycopg2-binary")
    sys.exit(1)

DIAS_DEFECTO = 30  # supuesto para clientes sin historial de pago


def _load_env() -> None:
    """Carga .env de la raiz sin depender de python-dotenv (patron del proyecto)."""
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


def promedios_dias_pago(cur) -> dict:
    """Promedio de dias_pago (>0) por cliente, redondeado, desde facturas ya pagadas."""
    cur.execute("""
        SELECT rut_cliente, ROUND(AVG(dias_pago))::int AS prom
        FROM ventas
        WHERE fecha_pago IS NOT NULL
          AND dias_pago > 0
          AND tipo_documento::text != '61'
        GROUP BY rut_cliente
    """)
    return {r["rut_cliente"]: r["prom"] for r in cur.fetchall()}


def facturas_objetivo(cur) -> list:
    """Facturas 2024 sin fecha_pago (las que hay que marcar pagadas)."""
    cur.execute("""
        SELECT folio, rut_cliente, fecha,
               COALESCE(monto_total_ajustado, monto_total) AS monto
        FROM ventas
        WHERE date_part('year', fecha) = 2024
          AND tipo_documento::text != '61'
          AND fecha_pago IS NULL
        ORDER BY folio
    """)
    return cur.fetchall()


def main() -> None:
    print("=" * 64)
    print("  ZIGURAT - Backfill de pagos 2024 (marcar 2024 sin deuda)")
    print("=" * 64)

    try:
        conn = psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: no se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    afectados = []  # (folio, fecha_pago, dias, con_historial)
    try:
        with conn:
            with conn.cursor() as cur:
                prom = promedios_dias_pago(cur)
                objetivo = facturas_objetivo(cur)

                if not objetivo:
                    print("  No hay facturas 2024 pendientes. Nada que hacer.")
                    return

                # Guardar folios para poder revertir ANTES de modificar.
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_dir = Path(__file__).parent.parent / "logs"
                log_dir.mkdir(exist_ok=True)
                log_file = log_dir / f"backfill_pagos_2024_{ts}.txt"
                folios = [str(r["folio"]) for r in objetivo]
                log_file.write_text(",".join(folios), encoding="utf-8")

                for r in objetivo:
                    rut = r["rut_cliente"]
                    dias = prom.get(rut)
                    con_hist = dias is not None and dias > 0
                    if not con_hist:
                        dias = DIAS_DEFECTO
                    fecha_pago = r["fecha"] + timedelta(days=int(dias))
                    cur.execute("""
                        UPDATE ventas
                        SET fecha_pago = %s, dias_pago = %s
                        WHERE folio = %s AND tipo_documento::text != '61'
                    """, (fecha_pago, int(dias), r["folio"]))
                    afectados.append((r["folio"], fecha_pago, int(dias), con_hist))

                # Verificacion dentro de la misma transaccion.
                cur.execute("""
                    SELECT COUNT(*) AS n,
                           COALESCE(SUM(COALESCE(monto_total_ajustado, monto_total)),0)::bigint AS monto
                    FROM ventas
                    WHERE date_part('year', fecha) = 2024
                      AND tipo_documento::text != '61'
                      AND fecha_pago IS NULL
                      AND COALESCE(monto_total_ajustado, monto_total) > 0
                """)
                resto = cur.fetchone()
    finally:
        conn.close()

    con_h = sum(1 for a in afectados if a[3])
    sin_h = len(afectados) - con_h
    print(f"  Facturas 2024 marcadas pagadas : {len(afectados)}")
    print(f"    - con historial (promedio)   : {con_h}")
    print(f"    - sin historial (30 dias)    : {sin_h}")
    print(f"  Log reversible                 : {log_file}")
    print(f"  Deuda 2024 restante (monto>0)  : {resto['n']} facturas / ${resto['monto']:,}".replace(",", "."))
    print("=" * 64)


if __name__ == "__main__":
    main()
