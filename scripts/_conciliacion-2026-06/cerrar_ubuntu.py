#!/usr/bin/env python3
"""
cerrar_ubuntu.py - Zigurat ERP

Cierra Ubuntu Patagonia SPA (77650861-6) tras cruzar con la cartola del
Banco Santander. Ambas facturas estaban PENDIENTES y resultaron pagadas:

  - 4211 ($1.033.176, 28-ene-2025): $1.000.000 (30-ene) + $33.076 (06-feb)
    = $1.033.076 (dif $100 redondeo). fecha_pago = 2025-02-06.
  - 4607 ($575.811, 10-mar-2026): transf $575.811 exacto. fecha_pago = 2026-03-26.

Reversible: logs/cerrar_ubuntu_<ts>.json con los folios afectados.
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, date

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: falta psycopg2.")
    sys.exit(1)

PAGOS = {
    4211: "2025-02-06",
    4607: "2026-03-26",
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
    conn = psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)
    undo = {"folios": list(PAGOS.keys())}
    try:
        with conn:
            with conn.cursor() as cur:
                for folio, fp_str in PAGOS.items():
                    cur.execute("""SELECT fecha FROM ventas
                                   WHERE folio=%s AND tipo_documento::text!='61'
                                     AND fecha_pago IS NULL""", (folio,))
                    row = cur.fetchone()
                    if not row:
                        print(f"  [!] Folio {folio}: ya pagado o inexistente, se omite.")
                        continue
                    fp = date.fromisoformat(fp_str)
                    dias = (fp - row["fecha"]).days
                    cur.execute("""UPDATE ventas SET fecha_pago=%s, dias_pago=%s
                                   WHERE folio=%s AND tipo_documento::text!='61'""",
                                (fp, dias, folio))
                    print(f"  Folio {folio}: pagada {fp_str} ({dias} dias)")
                cur.execute("""SELECT COUNT(*) AS n FROM ventas
                               WHERE rut_cliente='77650861-6' AND fecha_pago IS NULL
                                 AND tipo_documento::text!='61'
                                 AND COALESCE(monto_total_ajustado,monto_total)>0""")
                resto = cur.fetchone()["n"]
    finally:
        conn.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"cerrar_ubuntu_{ts}.json"
    log_file.write_text(json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Saldo restante Ubuntu: {resto} factura(s).  Log: {log_file.name}")


if __name__ == "__main__":
    main()
