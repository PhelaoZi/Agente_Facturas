#!/usr/bin/env python3
"""
cerrar_bar_original.py - Zigurat ERP

Cierra la cobranza de Bar Cerveceria Original SPA (77042203-5) tras cruzar sus
facturas contra la cartola del Banco del Estado (feb-2025 -> may-2026).

Cada factura se marca pagada con la FECHA REAL de la transferencia que la cubre
(ver logs/transferencias_bar_original_staging.csv). dias_pago = fecha_pago - emision.

La factura 4693 (27-may-2026) NO se toca: esta al dia, aun no vencida.
El descuadre ~$39k se trata como saldo rotativo, no como deuda.

Reversible: logs/cerrar_bar_original_<ts>.json con los folios afectados
(estaban en fecha_pago IS NULL).
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
    print("ERROR: falta psycopg2. pip install psycopg2-binary")
    sys.exit(1)

# folio -> fecha de pago (transferencia que lo cubre)
PAGOS = {
    4210: "2025-03-11",  # transf $76.000
    4267: "2025-05-22",  # 02-may + 22-may ($200.000)
    4296: "2025-06-05",  # transf $119.000
    4332: "2025-07-07",  # abono jul
    4352: "2025-08-19",  # abonos jul-ago completados
    4577: "2026-03-10",  # transf $94.000
    4644: "2026-05-20",  # transf $114.275 (exacto)
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
    print("=" * 60)
    print("  ZIGURAT - Cierre Bar Cerveceria Original")
    print("=" * 60)
    try:
        conn = psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: no se pudo conectar: {e}")
        sys.exit(1)

    undo = {"folios": list(PAGOS.keys())}
    try:
        with conn:
            with conn.cursor() as cur:
                for folio, fp_str in PAGOS.items():
                    cur.execute("""
                        SELECT fecha FROM ventas
                        WHERE folio = %s AND tipo_documento::text != '61'
                          AND fecha_pago IS NULL
                    """, (folio,))
                    row = cur.fetchone()
                    if not row:
                        print(f"  [!] Folio {folio}: ya pagado o inexistente, se omite.")
                        continue
                    emision = row["fecha"]
                    fp = date.fromisoformat(fp_str)
                    dias = (fp - emision).days
                    cur.execute("""
                        UPDATE ventas SET fecha_pago = %s, dias_pago = %s
                        WHERE folio = %s AND tipo_documento::text != '61'
                    """, (fp, dias, folio))
                    print(f"  Folio {folio}: pagada {fp_str} ({dias} dias)")

                # Verificacion del saldo restante del cliente
                cur.execute("""
                    SELECT COUNT(*) AS n,
                           COALESCE(SUM(COALESCE(monto_total_ajustado, monto_total)),0)::bigint AS monto
                    FROM ventas
                    WHERE rut_cliente = '77042203-5' AND fecha_pago IS NULL
                      AND tipo_documento::text != '61'
                      AND COALESCE(monto_total_ajustado, monto_total) > 0
                """)
                resto = cur.fetchone()
    finally:
        conn.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"cerrar_bar_original_{ts}.json"
    log_file.write_text(json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 60)
    print(f"  Saldo restante Bar Original: {resto['n']} factura(s) / ${resto['monto']:,}".replace(",", "."))
    print(f"  (debe ser solo la 4693, al dia)")
    print(f"  Log reversible: {log_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
