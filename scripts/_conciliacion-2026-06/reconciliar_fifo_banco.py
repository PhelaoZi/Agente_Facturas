#!/usr/bin/env python3
"""
reconciliar_fifo_banco.py - Zigurat ERP

Reconciliacion determinista factura-por-factura contra el banco (FIFO real).

Para cada cliente (salvo excepciones), toma TODAS sus transferencias del banco
(cruzando por RUT normalizado) y TODAS sus facturas, ambas en orden cronologico,
y asigna pagos FIFO: una factura queda PAGADA cuando el acumulado de
transferencias cubre el acumulado de facturas hasta ella. fecha_pago = fecha de
la transferencia que la cubrio (nunca antes de la emision); dias_pago = dias
entre emision y pago.

Esto RE-DERIVA fecha_pago desde la verdad del banco, reemplazando el trabajo
manual previo (que quedo inconsistente). Es determinista y reproducible.

EXCEPCIONES (no se tocan: pagan desde otro RUT / casos resueltos a mano /
incobrables): se listan abajo.

Reversible: snapshot COMPLETO del estado previo en
logs/snapshot_fecha_pago_<ts>.json (folio -> fecha_pago, dias_pago) de TODAS
las ventas. Para revertir, se restaura ese snapshot.
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

# RUTs que NO se re-derivan por banco (pagan de otro RUT / a mano / incobrables)
EXCEPCIONES = {
    "77166721-K",  # Amadeus (paga de otro RUT; debe solo la 4685)
    "77113872-1",  # Cuatro y Medio (al dia, otro RUT)
    "78130985-0",  # Los Putamadre (al dia, otro RUT)
    "3830475-5",   # Maria Ester (otro RUT / efectivo)
    "77245148-2",  # Brotherwood (anomalo, al dia)
    "76730367-K",  # Gastronomica Sur (fuera del negocio; se marca aparte)
    "77615028-2",  # Galpon (incobrable)
    "77594422-6",  # Barveda (incobrable)
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


def snapshot(cur) -> dict:
    cur.execute("""SELECT folio, to_char(fecha_pago,'YYYY-MM-DD') AS fp, dias_pago
                   FROM ventas WHERE tipo_documento::text!='61'""")
    return {str(r["folio"]): {"fp": r["fp"], "dias": r["dias_pago"]} for r in cur.fetchall()}


def main() -> None:
    conn = psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)
    cambios = []
    try:
        with conn:
            with conn.cursor() as cur:
                # Snapshot reversible ANTES de tocar nada
                snap = snapshot(cur)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_dir = Path(__file__).parent.parent / "logs"
                log_dir.mkdir(exist_ok=True)
                snap_file = log_dir / f"snapshot_fecha_pago_{ts}.json"
                snap_file.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")

                # Clientes a reconciliar (todos menos excepciones)
                cur.execute("SELECT DISTINCT rut_cliente FROM ventas WHERE tipo_documento::text!='61'")
                ruts = [r["rut_cliente"] for r in cur.fetchall() if r["rut_cliente"] not in EXCEPCIONES]

                for rut in ruts:
                    rk = "".join(ch for ch in rut.upper() if ch.isdigit() or ch == "K")

                    cur.execute("""SELECT folio, fecha, fecha_pago,
                                          COALESCE(monto_total_ajustado,monto_total) AS monto
                                   FROM ventas WHERE rut_cliente=%s AND tipo_documento::text!='61'
                                     AND COALESCE(monto_total_ajustado,monto_total)>0
                                   ORDER BY fecha, folio""", (rut,))
                    facturas = cur.fetchall()
                    if not facturas:
                        continue

                    cur.execute("""SELECT fecha, monto_abono AS monto FROM movimientos_banco
                                   WHERE regexp_replace(upper(COALESCE(rut_emisor,'')),'[^0-9K]','','g')=%s
                                   ORDER BY fecha, id""", (rk,))
                    transfs = cur.fetchall()

                    cum_inv = 0.0
                    cum_tr = 0.0
                    ti = 0
                    last_tr_date = None
                    for inv in facturas:
                        cum_inv += float(inv["monto"])
                        while ti < len(transfs) and cum_tr < cum_inv:
                            cum_tr += float(transfs[ti]["monto"])
                            last_tr_date = transfs[ti]["fecha"]
                            ti += 1
                        pagada = cum_tr >= cum_inv
                        # ADITIVO: solo apaga fantasma (pendiente que el banco cubre).
                        # Nunca des-paga lo ya marcado (efectivo/otro RUT/decisiones manuales).
                        if pagada and inv["fecha_pago"] is None:
                            fp = last_tr_date if last_tr_date and last_tr_date >= inv["fecha"] else inv["fecha"]
                            dias = (fp - inv["fecha"]).days
                            cur.execute("""UPDATE ventas SET fecha_pago=%s, dias_pago=%s
                                           WHERE folio=%s AND tipo_documento::text!='61'""",
                                        (fp, dias, inv["folio"]))
                            cambios.append(inv["folio"])

                # Resumen
                cur.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(monto_total_ajustado,monto_total)),0)::bigint AS m
                               FROM ventas WHERE tipo_documento::text!='61' AND fecha_pago IS NULL
                                 AND COALESCE(monto_total_ajustado,monto_total)>0""")
                resto = cur.fetchone()
    finally:
        conn.close()

    print(f"  Snapshot reversible: {snap_file.name}")
    print(f"  Fantasma apagado (facturas marcadas pagadas por banco): {len(cambios)}")
    print(f"  Pendiente tras reconciliacion FIFO real: {resto['n']} facturas / ${resto['m']:,}".replace(",", "."))


if __name__ == "__main__":
    main()
