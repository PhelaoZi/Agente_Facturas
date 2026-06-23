#!/usr/bin/env python3
"""
cerrar_amadeus.py - Zigurat ERP

Amadeus (77166721-K) paga desde otro RUT, por eso el cruce por RUT solo vio
$138k. Segun el dueno, Amadeus debe UNA sola factura: la que vence hoy
(folio 4685, emitida 15-may-2026). El resto son pagos no registrados.

Marca como PAGADAS todas las pendientes de Amadeus EXCEPTO la 4685, con
fecha = emision + 30 dias (su plazo). dias_pago = 30. La 4685 queda pendiente.

Reversible: logs/cerrar_amadeus_<ts>.json con los folios afectados.
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
    print("ERROR: falta psycopg2.")
    sys.exit(1)

RUT = "77166721-K"
DIAS = 30
MANTENER_PENDIENTE = {4685}  # la que vence hoy


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
    hoy = date.today()
    conn = psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)
    undo = {"folios": []}
    total = 0
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT folio, fecha FROM ventas
                    WHERE rut_cliente=%s AND fecha_pago IS NULL
                      AND tipo_documento::text!='61'
                      AND COALESCE(monto_total_ajustado, monto_total) > 0
                    ORDER BY fecha
                """, (RUT,))
                for r in cur.fetchall():
                    if r["folio"] in MANTENER_PENDIENTE:
                        continue
                    fp = r["fecha"] + timedelta(days=DIAS)
                    if fp > hoy:
                        fp = hoy
                    d = (fp - r["fecha"]).days
                    cur.execute("""UPDATE ventas SET fecha_pago=%s, dias_pago=%s
                                   WHERE folio=%s AND tipo_documento::text!='61'""",
                                (fp, d, r["folio"]))
                    undo["folios"].append(r["folio"])
                    total += 1

                cur.execute("""SELECT folio, COALESCE(monto_total_ajustado,monto_total)::bigint AS m
                               FROM ventas WHERE rut_cliente=%s AND fecha_pago IS NULL
                                 AND tipo_documento::text!='61'
                                 AND COALESCE(monto_total_ajustado,monto_total)>0""", (RUT,))
                resto = cur.fetchall()
    finally:
        conn.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"cerrar_amadeus_{ts}.json"
    log_file.write_text(json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  Facturas marcadas pagadas: {total}")
    print(f"  Pendiente que queda: {[(r['folio'], r['m']) for r in resto]}")
    print(f"  Log reversible: {log_file.name}")


if __name__ == "__main__":
    main()
