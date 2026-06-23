#!/usr/bin/env python3
"""
limpieza_fifo.py - Zigurat ERP

Limpieza de cobranza con respaldo bancario (FIFO).

Con TODO el banco cargado (movimientos_banco al dia), el saldo real de cada
cliente es:  saldo = total_facturado - total_transferido  (cruzando por RUT
normalizado). El resto de lo "pendiente" son pagos no registrados.

Para cada cliente (salvo los excluidos) aplica FIFO sobre sus facturas
pendientes: deja PENDIENTES las mas recientes que suman su saldo real y marca
PAGADAS las mas viejas (ya cubiertas por transferencias). fecha_pago = emision
+ promedio de dias del cliente (12 meses), topado a hoy.

Es conservador: nunca borra deuda real (mantiene las facturas recientes hasta
cubrir el saldo); si acaso deja de mas.

Excluidos (se revisan aparte):
  - 77166721-K Amadeus     -> ya cerrado a mano (paga de otro RUT).
  - 77615028-2 Galpon      -> incobrable (quebro); su deuda se deja intacta.
  - 77245148-2 Brotherwood -> saldo -2,2M anomalo, hay que entenderlo.
  - 3830475-5  Maria Ester -> transferido $0 (¿paga de otro RUT / efectivo?).

Reversible: logs/limpieza_fifo_<ts>.json con los folios marcados pagados.
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

EXCLUIR = {"77166721-K", "77615028-2", "77245148-2", "3830475-5"}
DIAS_DEFECTO = 30
TOL = 10000  # saldo bajo este umbral = ruido de redondeo -> cliente al dia


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


def saldos_reales(cur) -> dict:
    """rut_cliente -> saldo real (facturado - transferido), cruzando por RUT normalizado."""
    cur.execute("""
        WITH fact AS (
          SELECT rut_cliente,
                 regexp_replace(upper(rut_cliente),'[^0-9K]','','g') AS rk,
                 SUM(COALESCE(monto_total_ajustado, monto_total)) AS facturado
          FROM ventas WHERE tipo_documento::text!='61'
            AND COALESCE(monto_total_ajustado, monto_total) > 0
          GROUP BY rut_cliente
        ),
        transf AS (
          SELECT regexp_replace(upper(rut_emisor),'[^0-9K]','','g') AS rk,
                 SUM(monto_abono) AS transferido
          FROM movimientos_banco GROUP BY 1
        )
        SELECT f.rut_cliente, (f.facturado - COALESCE(t.transferido,0)) AS saldo
        FROM fact f LEFT JOIN transf t ON t.rk = f.rk
    """)
    return {r["rut_cliente"]: float(r["saldo"]) for r in cur.fetchall()}


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
    tot_marcadas = 0
    tot_queda = 0.0
    print("=" * 78)
    print(f"  {'Cliente':32s} {'saldo real':>12} {'marcadas':>9} {'queda pend':>12}")
    print("-" * 78)
    try:
        with conn:
            with conn.cursor() as cur:
                saldos = saldos_reales(cur)

                cur.execute("""
                    SELECT DISTINCT v.rut_cliente, c.razon_social
                    FROM ventas v JOIN clientes c ON c.rut_cliente=v.rut_cliente
                    WHERE v.tipo_documento::text!='61' AND v.fecha_pago IS NULL
                      AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
                """)
                clientes = [(r["rut_cliente"], r["razon_social"]) for r in cur.fetchall()]

                for rut, nombre in sorted(clientes, key=lambda x: -saldos.get(x[0], 0)):
                    if rut in EXCLUIR:
                        continue
                    saldo_pos = max(saldos.get(rut, 0.0), 0.0)
                    if saldo_pos < TOL:   # ruido de redondeo: cliente al dia
                        saldo_pos = 0.0
                    dias = avg_dias(cur, rut)

                    cur.execute("""
                        SELECT folio, fecha, COALESCE(monto_total_ajustado, monto_total) AS monto
                        FROM ventas WHERE rut_cliente=%s AND fecha_pago IS NULL
                          AND tipo_documento::text!='61'
                          AND COALESCE(monto_total_ajustado, monto_total) > 0
                        ORDER BY fecha DESC
                    """, (rut,))
                    pend = cur.fetchall()

                    acc = 0.0
                    marcadas = 0
                    queda = 0.0
                    keeping = True
                    for inv in pend:  # de la mas nueva a la mas vieja
                        monto = float(inv["monto"])
                        # Mantener pendientes las mas nuevas hasta cubrir el saldo
                        # adeudado; el resto (mas viejas) ya las cubrio el banco.
                        if keeping and acc < saldo_pos:
                            acc += monto
                            queda += monto
                        else:
                            keeping = False
                            fp = inv["fecha"] + timedelta(days=dias)
                            if fp > hoy:
                                fp = hoy
                            d = (fp - inv["fecha"]).days
                            cur.execute("""UPDATE ventas SET fecha_pago=%s, dias_pago=%s
                                           WHERE folio=%s AND tipo_documento::text!='61'""",
                                        (fp, d, inv["folio"]))
                            undo["folios"].append(inv["folio"])
                            marcadas += 1
                            tot_marcadas += 1
                    tot_queda += queda
                    if marcadas:
                        print(f"  {nombre[:32]:32s} {saldos.get(rut,0):>12,.0f} {marcadas:>9d} {queda:>12,.0f}")
    finally:
        conn.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"limpieza_fifo_{ts}.json"
    log_file.write_text(json.dumps(undo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 78)
    print(f"  TOTAL facturas marcadas pagadas: {tot_marcadas}")
    print(f"  TOTAL que queda pendiente (clientes limpiados): ${tot_queda:,.0f}".replace(",", "."))
    print(f"  Log reversible: {log_file.name}")
    print("=" * 78)


if __name__ == "__main__":
    main()
