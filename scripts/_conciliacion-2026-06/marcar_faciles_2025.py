#!/usr/bin/env python3
"""
marcar_faciles_2025.py - Zigurat ERP

Marca como PAGADAS las facturas pendientes de 2025+ de clientes "faciles":
aquellos cuyo ultimo pago registrado es POSTERIOR a su factura pendiente mas
nueva (imposible que la deban -> es pago no registrado).

Decision del dueno (2026-06-15). Clientes de este lote:
  Brothers, Inversiones Bardos, VDT, Uncle Fletch, Aramon.

Fecha de pago inferida = emision + promedio de dias_pago de los ULTIMOS 12 MESES
del cliente (criterio del dueno: el comportamiento reciente es el que vale).
Sin historial reciente -> 30 dias. Si cae en el futuro, se topa a hoy.

Reversible: logs/marcar_faciles_2025_<ts>.json con los folios afectados.
NO crea filas en conciliaciones (es inferencia; el banco lo confirmara luego).
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta, date

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: falta psycopg2. pip install psycopg2-binary")
    sys.exit(1)

DIAS_DEFECTO = 30

# rut -> nombre (solo para el log/impresion)
CLIENTES = {
    "76573828-8": "Brothers",
    "76922048-8": "Inversiones Bardos",
    "77220069-2": "VDT",
    "76296603-4": "Uncle Fletch",
    "76261485-5": "Aramon",
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


def prom_12m(cur, rut) -> int:
    """Promedio de dias_pago de los ultimos 12 meses del cliente; 30 si no hay."""
    cur.execute("""
        SELECT ROUND(AVG(dias_pago))::int AS p
        FROM ventas
        WHERE rut_cliente = %s
          AND fecha >= (CURRENT_DATE - INTERVAL '12 months')
          AND fecha_pago IS NOT NULL AND dias_pago > 0
          AND tipo_documento::text != '61'
    """, (rut,))
    row = cur.fetchone()
    return row["p"] if row and row["p"] and row["p"] > 0 else DIAS_DEFECTO


def main() -> None:
    print("=" * 60)
    print("  ZIGURAT - Marcar faciles 2025 (pago no registrado)")
    print("=" * 60)
    try:
        conn = psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: no se pudo conectar: {e}")
        sys.exit(1)

    hoy = date.today()
    undo = {"folios": []}
    total = 0
    try:
        with conn:
            with conn.cursor() as cur:
                for rut, nombre in CLIENTES.items():
                    dias = prom_12m(cur, rut)
                    cur.execute("""
                        SELECT folio, fecha FROM ventas
                        WHERE rut_cliente = %s AND fecha_pago IS NULL
                          AND tipo_documento::text != '61'
                          AND COALESCE(monto_total_ajustado, monto_total) > 0
                    """, (rut,))
                    pend = cur.fetchall()
                    for r in pend:
                        fp = r["fecha"] + timedelta(days=dias)
                        if fp > hoy:
                            fp = hoy
                        d = (fp - r["fecha"]).days
                        cur.execute("""
                            UPDATE ventas SET fecha_pago = %s, dias_pago = %s
                            WHERE folio = %s AND tipo_documento::text != '61'
                        """, (fp, d, r["folio"]))
                        undo["folios"].append(r["folio"])
                        total += 1
                    print(f"  {nombre:20s}: {len(pend)} factura(s)  (prom {dias} dias)")
    finally:
        conn.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"marcar_faciles_2025_{ts}.json"
    log_file.write_text(json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 60)
    print(f"  Total facturas marcadas pagadas: {total}")
    print(f"  Log reversible: {log_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
