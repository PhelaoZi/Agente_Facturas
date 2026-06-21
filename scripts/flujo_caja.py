#!/usr/bin/env python3
"""
flujo_caja.py - Zigurat ERP
Proyecta el flujo de caja de las próximas 4 semanas (CLI).

La lógica vive en app/negocio/flujo.py; este script solo conecta a la BD,
llama a proyectar_flujo() e imprime el resultado.

Uso:
    python scripts/flujo_caja.py
    python scripts/flujo_caja.py --saldo-inicial 5000000
"""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.negocio import flujo  # noqa: E402


def _load_env():
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
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", 5432)),
    "dbname": os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


def parsear_saldo_arg():
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--saldo-inicial" and i < len(sys.argv):
            try:
                return float(sys.argv[i + 1].replace(".", "").replace(",", "."))
            except (ValueError, IndexError):
                pass
    return None


def fmt_pesos(n):
    return "$" + "{:,.0f}".format(float(n)).replace(",", ".")


def main():
    saldo_arg = parsear_saldo_arg()

    print("=" * 70)
    print("ZIGURAT ERP - Proyeccion de Flujo de Caja")
    print("=" * 70)
    print()

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    with conn:
        with conn.cursor() as cur:
            r = flujo.proyectar_flujo(cur, saldo_inicial=saldo_arg)
    conn.close()

    if saldo_arg is not None:
        print(f"  Saldo inicial (manual): {fmt_pesos(r['saldo_inicial'])}")
    elif r["saldo_fecha"] is not None:
        dias_viejo = (r["hoy"] - r["saldo_fecha"]).days
        if dias_viejo > 7:
            print(f"  [!] El ultimo saldo en BD es de hace {dias_viejo} dias ({r['saldo_fecha']})")
            print("      Para mayor precision usa: python scripts/flujo_caja.py --saldo-inicial MONTO")
        print(f"  Saldo inicial (BD {r['saldo_fecha']}): {fmt_pesos(r['saldo_inicial'])}")
    else:
        print("  [!] No hay saldo bancario en la BD.")
        print("      Usa: python scripts/flujo_caja.py --saldo-inicial MONTO")
        print(f"  Asumiendo saldo inicial: {fmt_pesos(r['saldo_inicial'])}")

    print()
    print(f"  Horizonte: {r['hoy'].strftime('%d/%m/%Y')} -> {r['horizonte'].strftime('%d/%m/%Y')}")
    en_ventana = sum(len(s["detalle_ingresos"]) for s in r["semanas"])
    print(f"  Facturas por cobrar en ventana: {en_ventana}")
    print(f"  Facturas fuera de ventana:      {len(r['ingresos_fuera'])}")
    print()

    sep = "=" * 70
    print(sep)
    print(f"  {'SEMANA':<18} {'INGRESOS':>14} {'EGRESOS':>14} {'SALDO':>14}")
    print("-" * 70)
    for s in r["semanas"]:
        alerta = " <--RIESGO" if s["riesgo"] else ""
        print(f"  {s['label']:<18} {fmt_pesos(s['ingresos']):>14} "
              f"{fmt_pesos(s['egresos']):>14} {fmt_pesos(s['saldo_acumulado']):>14}{alerta}")
    print("-" * 70)
    print(f"  {'TOTAL':<18} {fmt_pesos(r['total_ingresos']):>14} {fmt_pesos(r['total_egresos']):>14}")
    print(sep)
    print()

    print("DETALLE INGRESOS PROYECTADOS")
    print("-" * 70)
    for s in r["semanas"]:
        if s["detalle_ingresos"]:
            print(f"  Semana {s['semana']} ({s['label']}):")
            for i in s["detalle_ingresos"]:
                print(f"    Folio {i['folio']:>5} | {str(i['cliente'])[:35]:<35} | "
                      f"{fmt_pesos(i['monto']):>12} | ~{i['fecha_proyectada'].strftime('%d/%m')} "
                      f"(avg {int(i['avg_dias'])}d)")
    if r["ingresos_fuera"]:
        print()
        print(f"  Fuera de las 4 semanas ({len(r['ingresos_fuera'])} facturas):")
        for f in r["ingresos_fuera"][:5]:
            print(f"    Folio {f['folio']:>5} | {str(f['cliente'])[:35]:<35} | {fmt_pesos(f['monto']):>12}")
        if len(r["ingresos_fuera"]) > 5:
            print(f"    ... y {len(r['ingresos_fuera']) - 5} mas")
    print()

    if any(s["detalle_egresos"] for s in r["semanas"]):
        print("DETALLE EGRESOS PROYECTADOS")
        print("-" * 70)
        for s in r["semanas"]:
            if s["detalle_egresos"]:
                print(f"  Semana {s['semana']} ({s['label']}):")
                for g in s["detalle_egresos"]:
                    cat = f"[{g['categoria']}]" if g["categoria"] else ""
                    prov = str(g["proveedor"] or "")
                    print(f"    {str(g['descripcion'])[:35]:<35} {prov[:20]:<20} "
                          f"{fmt_pesos(g['monto']):>12} vence "
                          f"{g['fecha_vencimiento'].strftime('%d/%m')} {cat}")
        print()
    else:
        print("Sin gastos registrados en la ventana de 4 semanas.")
        print("Usa /agregar-gasto para registrar cuentas por pagar.")
        print()

    print(sep)


if __name__ == "__main__":
    main()
