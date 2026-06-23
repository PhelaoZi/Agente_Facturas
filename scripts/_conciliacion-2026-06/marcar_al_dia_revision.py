#!/usr/bin/env python3
"""
marcar_al_dia_revision.py - Zigurat ERP

Cierra los 3 clientes que quedaban "a revisar", confirmados al dia por el dueno
(2026-06-15). Pagan desde otro RUT / efectivo, por lo que el cruce por RUT no los
veia, pero estan al dia:

  - 77245148-2 Brotherwood       (banco confirma: transfirio $2,2M de mas)
  - 77113872-1 Cuatro y Medio
  - 78130985-0 Los Putamadre

Marca TODAS sus facturas pendientes como pagadas: fecha = emision + promedio de
dias del cliente (12 meses), 30 si no hay, topado a hoy.

Reversible: logs/marcar_al_dia_revision_<ts>.json con los folios afectados.
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

RUTS = {
    "77245148-2": "Brotherwood",
    "77113872-1": "Cuatro y Medio",
    "78130985-0": "Los Putamadre",
}
DIAS_DEFECTO = 30


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


def avg_dias(cur, rut) -> int:
    cur.execute("""
        SELECT ROUND(AVG(dias_pago))::int AS p FROM ventas
        WHERE rut_cliente=%s AND fecha >= (CURRENT_DATE - INTERVAL '12 months')
          AND fecha_pago IS NOT NULL AND dias_pago>0 AND tipo_documento::text!='61'
    """, (rut,))
    r = cur.fetchone()
    return r["p"] if r and r["p"] and r["p"] > 0 else DIAS_DEFECTO


def main() -> None:
    hoy = date.today()
    conn = psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)
    undo = {"folios": []}
    try:
        with conn:
            with conn.cursor() as cur:
                for rut, nombre in RUTS.items():
                    dias = avg_dias(cur, rut)
                    cur.execute("""
                        SELECT folio, fecha FROM ventas
                        WHERE rut_cliente=%s AND fecha_pago IS NULL
                          AND tipo_documento::text!='61'
                          AND COALESCE(monto_total_ajustado, monto_total) > 0
                    """, (rut,))
                    pend = cur.fetchall()
                    for r in pend:
                        fp = r["fecha"] + timedelta(days=dias)
                        if fp > hoy:
                            fp = hoy
                        d = (fp - r["fecha"]).days
                        cur.execute("""UPDATE ventas SET fecha_pago=%s, dias_pago=%s
                                       WHERE folio=%s AND tipo_documento::text!='61'""",
                                    (fp, d, r["folio"]))
                        undo["folios"].append(r["folio"])
                    print(f"  {nombre:16s}: {len(pend)} factura(s) marcada(s) pagada(s) (prom {dias} dias)")
    finally:
        conn.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"marcar_al_dia_revision_{ts}.json"
    log_file.write_text(json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Total: {len(undo['folios'])} facturas. Log: {log_file.name}")


if __name__ == "__main__":
    main()
