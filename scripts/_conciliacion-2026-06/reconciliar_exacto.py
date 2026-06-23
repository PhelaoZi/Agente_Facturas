#!/usr/bin/env python3
"""
reconciliar_exacto.py - Zigurat ERP

Reconciliacion por MATCH EXACTO transferencia <-> factura.

Para cada factura pendiente (salvo clientes incobrables), busca una transferencia
del MISMO cliente (RUT normalizado) por el MISMO monto, emitida en o despues de
la factura (con 7 dias de gracia). Si la encuentra, marca la factura pagada con
la fecha de esa transferencia y CONSUME la transferencia (no se reutiliza).

El consumo es clave: si un cliente tiene 2 facturas iguales pero una sola
transferencia, solo una queda pagada (la mas antigua). Asi no se borra deuda
real por doble conteo.

Es ADITIVO: solo marca pagadas pendientes con respaldo bancario; no toca nada ya
pagado ni a los incobrables.

Reversible: snapshot completo en logs/snapshot_fecha_pago_<ts>.json.
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: falta psycopg2.")
    sys.exit(1)

GRACIA_DIAS = 7  # la transferencia puede ser hasta 7 dias antes de la emision


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
    matches = []
    try:
        with conn:
            with conn.cursor() as cur:
                # Snapshot reversible
                cur.execute("""SELECT folio, to_char(fecha_pago,'YYYY-MM-DD') AS fp, dias_pago
                               FROM ventas WHERE tipo_documento::text!='61'""")
                snap = {str(r["folio"]): {"fp": r["fp"], "dias": r["dias_pago"]} for r in cur.fetchall()}
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_dir = Path(__file__).parent.parent / "logs"
                log_dir.mkdir(exist_ok=True)
                snap_file = log_dir / f"snapshot_fecha_pago_{ts}.json"
                snap_file.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")

                # Facturas pendientes (excluye incobrables), por cliente y fecha
                cur.execute("""
                    SELECT v.folio, v.rut_cliente, v.fecha,
                           COALESCE(v.monto_total_ajustado, v.monto_total) AS monto,
                           regexp_replace(upper(v.rut_cliente),'[^0-9K]','','g') AS rk,
                           c.razon_social
                    FROM ventas v JOIN clientes c ON c.rut_cliente=v.rut_cliente
                    WHERE v.tipo_documento::text!='61' AND v.fecha_pago IS NULL
                      AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
                      AND c.estado != 'incobrable'
                    ORDER BY v.rut_cliente, v.fecha, v.folio
                """)
                pendientes = cur.fetchall()

                # Cache de transferencias por RUT (con flag de consumo)
                transf_cache = {}

                def transfers_de(rk):
                    if rk not in transf_cache:
                        cur.execute("""SELECT id, fecha, monto_abono AS monto FROM movimientos_banco
                                       WHERE regexp_replace(upper(COALESCE(rut_emisor,'')),'[^0-9K]','','g')=%s
                                       ORDER BY fecha, id""", (rk,))
                        transf_cache[rk] = [dict(r, used=False) for r in cur.fetchall()]
                    return transf_cache[rk]

                for inv in pendientes:
                    cand = None
                    for t in transfers_de(inv["rk"]):
                        if t["used"]:
                            continue
                        if float(t["monto"]) == float(inv["monto"]) and \
                           (t["fecha"] - inv["fecha"]).days >= -GRACIA_DIAS:
                            cand = t
                            break
                    if cand:
                        cand["used"] = True
                        fp = cand["fecha"] if cand["fecha"] >= inv["fecha"] else inv["fecha"]
                        dias = (fp - inv["fecha"]).days
                        cur.execute("""UPDATE ventas SET fecha_pago=%s, dias_pago=%s
                                       WHERE folio=%s AND tipo_documento::text!='61'""",
                                    (fp, dias, inv["folio"]))
                        matches.append((inv["folio"], inv["razon_social"], int(inv["monto"]), str(fp)))

                cur.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(monto_total_ajustado,monto_total)),0)::bigint AS m
                               FROM ventas WHERE tipo_documento::text!='61' AND fecha_pago IS NULL
                                 AND COALESCE(monto_total_ajustado,monto_total)>0""")
                resto = cur.fetchone()
    finally:
        conn.close()

    print("=" * 64)
    print(f"  Facturas pagadas por match exacto con banco: {len(matches)}")
    for folio, nombre, monto, fp in matches:
        print(f"   {folio}  {nombre[:30]:30s} ${monto:>9,}".replace(",", ".") + f"  {fp}")
    print("-" * 64)
    print(f"  Snapshot reversible: {snap_file.name}")
    print(f"  Pendiente ahora: {resto['n']} facturas / ${resto['m']:,}".replace(",", "."))
    print("=" * 64)


if __name__ == "__main__":
    main()
