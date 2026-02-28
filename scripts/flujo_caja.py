#!/usr/bin/env python3
"""
flujo_caja.py - Zigurat ERP
Proyecta el flujo de caja de las proximas 4 semanas.

Ingresos proyectados: facturas emitidas sin fecha_pago, proyectadas segun
  el promedio historico de dias de pago del cliente.
Egresos proyectados: cuentas_por_pagar pendientes en el horizonte de 4 semanas.

Uso:
    python scripts/flujo_caja.py
    python scripts/flujo_caja.py --saldo-inicial 5000000
"""

import io
import os
import sys
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

# Force UTF-8 output on Windows so client names with special characters print correctly.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)


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
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

SEMANAS = 4
AVG_DIAS_GLOBAL = 30
MIN_FACTURAS_PARA_AVG = 3


def parsear_saldo_arg():
    """Retorna saldo inicial desde --saldo-inicial XXXX si fue pasado."""
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--saldo-inicial' and i < len(sys.argv):
            try:
                return float(sys.argv[i + 1].replace('.', '').replace(',', '.'))
            except (ValueError, IndexError):
                pass
    return None


def obtener_saldo_banco(cur):
    """Retorna el ultimo saldo_diario registrado en movimientos_banco."""
    cur.execute("""
        SELECT saldo_diario, fecha
        FROM movimientos_banco
        WHERE saldo_diario IS NOT NULL
        ORDER BY fecha DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        return float(row['saldo_diario']), row['fecha']
    return None, None


def obtener_avg_dias_por_cliente(cur):
    """
    Retorna dict {rut_cliente: avg_dias} basado en las ultimas 10 facturas
    pagadas de cada cliente con al menos MIN_FACTURAS_PARA_AVG facturas.
    """
    cur.execute("""
        SELECT
            rut_cliente,
            AVG(dias_pago) as avg_dias
        FROM (
            SELECT rut_cliente, dias_pago,
                   ROW_NUMBER() OVER (PARTITION BY rut_cliente ORDER BY fecha DESC) AS rn
            FROM ventas
            WHERE fecha_pago IS NOT NULL
              AND dias_pago IS NOT NULL
              AND dias_pago > 0
              AND tipo_documento != '61'
        ) t
        WHERE rn <= 10
        GROUP BY rut_cliente
        HAVING COUNT(*) >= %s
    """, (MIN_FACTURAS_PARA_AVG,))

    return {row['rut_cliente']: float(row['avg_dias']) for row in cur.fetchall()}


def obtener_facturas_pendientes(cur):
    """Retorna facturas sin fecha_pago."""
    cur.execute("""
        SELECT
            folio,
            fecha,
            rut_cliente,
            razon_social_receptor,
            COALESCE(monto_total_ajustado, monto_total) AS monto
        FROM ventas
        WHERE fecha_pago IS NULL
          AND tipo_documento != '61'
        ORDER BY fecha
    """)
    return cur.fetchall()


def obtener_gastos_pendientes(cur, hasta):
    """Retorna cuentas_por_pagar pendientes hasta la fecha indicada."""
    cur.execute("""
        SELECT id, descripcion, proveedor, monto, fecha_vencimiento, categoria
        FROM cuentas_por_pagar
        WHERE pagado = FALSE
          AND fecha_vencimiento <= %s
        ORDER BY fecha_vencimiento
    """, (hasta,))
    return cur.fetchall()


def semana_de(d, inicio_periodo):
    """Retorna el numero de semana (0-based) para una fecha dada."""
    delta = (d - inicio_periodo).days
    return delta // 7


def fmt_pesos(n):
    """Formatea numero como pesos chilenos."""
    return "$" + "{:,.0f}".format(float(n)).replace(",", ".")


def main():
    saldo_arg = parsear_saldo_arg()
    hoy = date.today()
    horizonte = hoy + timedelta(weeks=SEMANAS)

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
            # Saldo inicial
            if saldo_arg is not None:
                saldo_inicial = saldo_arg
                saldo_fecha = hoy
                print(f"  Saldo inicial (manual): {fmt_pesos(saldo_inicial)}")
            else:
                saldo_inicial, saldo_fecha = obtener_saldo_banco(cur)
                if saldo_inicial is not None:
                    dias_viejo = (hoy - saldo_fecha).days
                    if dias_viejo > 7:
                        print(f"  [!] El ultimo saldo en BD es de hace {dias_viejo} dias ({saldo_fecha})")
                        print(f"      Para mayor precision usa: python scripts/flujo_caja.py --saldo-inicial MONTO")
                    print(f"  Saldo inicial (BD {saldo_fecha}): {fmt_pesos(saldo_inicial)}")
                else:
                    print("  [!] No hay saldo bancario en la BD.")
                    print("      Usa: python scripts/flujo_caja.py --saldo-inicial MONTO")
                    saldo_inicial = 0
                    print(f"  Asumiendo saldo inicial: {fmt_pesos(saldo_inicial)}")

            print()

            avg_dias = obtener_avg_dias_por_cliente(cur)
            facturas = obtener_facturas_pendientes(cur)
            gastos = obtener_gastos_pendientes(cur, horizonte)

    conn.close()

    # Clasificar ingresos por semana
    ingresos_semana = defaultdict(list)
    ingresos_fuera = []

    for f in facturas:
        rut = f['rut_cliente']
        avg = avg_dias.get(rut, AVG_DIAS_GLOBAL)
        fecha_proyectada = f['fecha'] + timedelta(days=int(avg))

        if fecha_proyectada < hoy:
            fecha_proyectada = hoy

        if fecha_proyectada <= horizonte:
            sem = semana_de(fecha_proyectada, hoy)
            sem = max(0, min(sem, SEMANAS - 1))
            ingresos_semana[sem].append({
                'folio': f['folio'],
                'cliente': f['razon_social_receptor'],
                'monto': float(f['monto']),
                'fecha_emision': f['fecha'],
                'fecha_proyectada': fecha_proyectada,
                'avg_dias': avg,
            })
        else:
            ingresos_fuera.append(f)

    gastos_semana = defaultdict(list)
    for g in gastos:
        sem = semana_de(g['fecha_vencimiento'], hoy)
        sem = max(0, min(sem, SEMANAS - 1))
        gastos_semana[sem].append(g)

    # Resumen
    print(f"  Horizonte: {hoy.strftime('%d/%m/%Y')} -> {horizonte.strftime('%d/%m/%Y')}")
    print(f"  Facturas por cobrar en ventana: {sum(len(v) for v in ingresos_semana.values())}")
    print(f"  Facturas fuera de ventana:      {len(ingresos_fuera)}")
    print(f"  Gastos pendientes en ventana:   {len(gastos)}")
    print()

    # Tabla semanal
    sep = "=" * 70
    print(sep)
    print(f"  {'SEMANA':<18} {'INGRESOS':>14} {'EGRESOS':>14} {'SALDO':>14}")
    print("-" * 70)

    saldo_acum = saldo_inicial
    total_ingresos = 0
    total_egresos = 0
    detalles = []

    for sem in range(SEMANAS):
        inicio_sem = hoy + timedelta(weeks=sem)
        fin_sem = inicio_sem + timedelta(days=6)
        label = f"{inicio_sem.strftime('%d/%m')}-{fin_sem.strftime('%d/%m')}"

        ingresos = sum(i['monto'] for i in ingresos_semana.get(sem, []))
        egresos  = sum(float(g['monto']) for g in gastos_semana.get(sem, []))

        saldo_acum += ingresos - egresos
        total_ingresos += ingresos
        total_egresos  += egresos

        alerta = " <--RIESGO" if saldo_acum < 0 else ""
        print(f"  {label:<18} {fmt_pesos(ingresos):>14} {fmt_pesos(egresos):>14} {fmt_pesos(saldo_acum):>14}{alerta}")
        detalles.append((sem, label, ingresos_semana.get(sem, []), gastos_semana.get(sem, [])))

    print("-" * 70)
    print(f"  {'TOTAL':<18} {fmt_pesos(total_ingresos):>14} {fmt_pesos(total_egresos):>14}")
    print(sep)
    print()

    # Detalle ingresos
    print("DETALLE INGRESOS PROYECTADOS")
    print("-" * 70)
    for sem, label, ingresos_list, _ in detalles:
        if ingresos_list:
            print(f"  Semana {sem+1} ({label}):")
            for i in ingresos_list:
                print(f"    Folio {i['folio']:>5} | {str(i['cliente'])[:35]:<35} | "
                      f"{fmt_pesos(i['monto']):>12} | "
                      f"~{i['fecha_proyectada'].strftime('%d/%m')} "
                      f"(avg {int(i['avg_dias'])}d)")

    if ingresos_fuera:
        print()
        print(f"  Fuera de las 4 semanas ({len(ingresos_fuera)} facturas):")
        for f in ingresos_fuera[:5]:
            avg = avg_dias.get(f['rut_cliente'], AVG_DIAS_GLOBAL)
            proyectada = f['fecha'] + timedelta(days=int(avg))
            print(f"    Folio {f['folio']:>5} | {str(f['razon_social_receptor'])[:35]:<35} | "
                  f"{fmt_pesos(float(f['monto'])):>12} | ~{proyectada.strftime('%d/%m/%Y')}")
        if len(ingresos_fuera) > 5:
            print(f"    ... y {len(ingresos_fuera)-5} mas")

    print()

    # Detalle egresos
    if any(gastos_semana.values()):
        print("DETALLE EGRESOS PROYECTADOS")
        print("-" * 70)
        for sem, label, _, gastos_list in detalles:
            if gastos_list:
                print(f"  Semana {sem+1} ({label}):")
                for g in gastos_list:
                    cat = f"[{g['categoria']}]" if g['categoria'] else ""
                    prov = str(g['proveedor'] or "")
                    print(f"    {str(g['descripcion'])[:35]:<35} {prov[:20]:<20} "
                          f"{fmt_pesos(float(g['monto'])):>12} "
                          f"vence {g['fecha_vencimiento'].strftime('%d/%m')} {cat}")
        print()
    else:
        print("Sin gastos registrados en la ventana de 4 semanas.")
        print("Usa /agregar-gasto para registrar cuentas por pagar.")
        print()

    print(sep)


if __name__ == "__main__":
    main()
