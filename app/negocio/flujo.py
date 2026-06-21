"""Proyección de flujo de caja a N semanas (lógica reutilizable, solo lectura).

Extraída de scripts/flujo_caja.py para que el CLI y las herramientas del agente
usen la misma fuente. Las funciones reciben un cursor RealDictCursor.
"""
from datetime import date, timedelta
from calendar import monthrange
from collections import defaultdict

SEMANAS = 4
AVG_DIAS_GLOBAL = 30
MIN_FACTURAS_PARA_AVG = 3


def obtener_saldo_banco(cur):
    """Último saldo_diario registrado en movimientos_banco -> (saldo, fecha)."""
    cur.execute("""
        SELECT saldo_diario, fecha
        FROM movimientos_banco
        WHERE saldo_diario IS NOT NULL
        ORDER BY fecha DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        return float(row["saldo_diario"]), row["fecha"]
    return None, None


def obtener_avg_dias_por_cliente(cur):
    """Promedio de días de pago por cliente (últimas 10 facturas pagadas)."""
    cur.execute("""
        SELECT rut_cliente, AVG(dias_pago) AS avg_dias
        FROM (
            SELECT rut_cliente, dias_pago,
                   ROW_NUMBER() OVER (PARTITION BY rut_cliente ORDER BY fecha DESC) AS rn
            FROM ventas
            WHERE fecha_pago IS NOT NULL AND dias_pago IS NOT NULL
              AND dias_pago > 0 AND tipo_documento != '61'
        ) t
        WHERE rn <= 10
        GROUP BY rut_cliente
        HAVING COUNT(*) >= %s
    """, (MIN_FACTURAS_PARA_AVG,))
    return {row["rut_cliente"]: float(row["avg_dias"]) for row in cur.fetchall()}


def obtener_facturas_pendientes(cur):
    """Facturas sin fecha_pago (cuentas por cobrar)."""
    cur.execute("""
        SELECT folio, fecha, rut_cliente, razon_social_receptor,
               COALESCE(monto_total_ajustado, monto_total) AS monto
        FROM ventas
        WHERE fecha_pago IS NULL AND tipo_documento != '61'
        ORDER BY fecha
    """)
    return cur.fetchall()


def obtener_gastos_pendientes(cur, hoy, horizonte):
    """Gastos a pagar dentro del horizonte: puntuales + recurrentes proyectados."""
    cur.execute("""
        SELECT id, descripcion, proveedor, monto, fecha_vencimiento, categoria
        FROM cuentas_por_pagar
        WHERE pagado = FALSE
          AND (recurrente = FALSE OR recurrente IS NULL)
          AND fecha_vencimiento BETWEEN %s AND %s
        ORDER BY fecha_vencimiento
    """, (hoy, horizonte))
    gastos = list(cur.fetchall())

    cur.execute("""
        SELECT id, descripcion, proveedor, monto, fecha_vencimiento, categoria
        FROM cuentas_por_pagar
        WHERE recurrente = TRUE AND periodicidad = 'mensual'
    """)
    for row in cur.fetchall():
        dia_mes = row["fecha_vencimiento"].day
        for delta_m in range(3):
            mes_abs = hoy.month + delta_m
            anio = hoy.year + (mes_abs - 1) // 12
            mes = (mes_abs - 1) % 12 + 1
            dia = min(dia_mes, monthrange(anio, mes)[1])
            fecha_proj = date(anio, mes, dia)
            if hoy <= fecha_proj <= horizonte:
                ocurrencia = dict(row)
                ocurrencia["fecha_vencimiento"] = fecha_proj
                gastos.append(ocurrencia)

    gastos.sort(key=lambda x: x["fecha_vencimiento"])
    return gastos


def semana_de(d, inicio_periodo):
    """Número de semana (0-based) de una fecha respecto al inicio."""
    return (d - inicio_periodo).days // 7


def proyectar_flujo(cur, saldo_inicial=None, semanas=SEMANAS, hoy=None):
    """Proyecta el flujo de caja. Devuelve un dict estructurado (no imprime).

    saldo_inicial: si es None, se toma el último saldo bancario de la BD.
    hoy: inyectable para tests; por defecto date.today().
    """
    hoy = hoy or date.today()
    horizonte = hoy + timedelta(weeks=semanas)

    if saldo_inicial is None:
        saldo_inicial, saldo_fecha = obtener_saldo_banco(cur)
        if saldo_inicial is None:
            saldo_inicial, saldo_fecha = 0.0, None
    else:
        saldo_fecha = hoy

    avg_dias = obtener_avg_dias_por_cliente(cur)
    facturas = obtener_facturas_pendientes(cur)
    gastos = obtener_gastos_pendientes(cur, hoy, horizonte)

    ingresos_semana = defaultdict(list)
    ingresos_fuera = []
    for f in facturas:
        avg = avg_dias.get(f["rut_cliente"], AVG_DIAS_GLOBAL)
        proyectada = f["fecha"] + timedelta(days=int(avg))
        if proyectada < hoy:
            proyectada = hoy
        if proyectada <= horizonte:
            sem = max(0, min(semana_de(proyectada, hoy), semanas - 1))
            ingresos_semana[sem].append({
                "folio": f["folio"], "cliente": f["razon_social_receptor"],
                "monto": float(f["monto"]), "fecha_proyectada": proyectada,
                "avg_dias": int(avg),
            })
        else:
            ingresos_fuera.append({
                "folio": f["folio"], "cliente": f["razon_social_receptor"],
                "monto": float(f["monto"]),
            })

    gastos_semana = defaultdict(list)
    for g in gastos:
        sem = max(0, min(semana_de(g["fecha_vencimiento"], hoy), semanas - 1))
        gastos_semana[sem].append({
            "descripcion": g["descripcion"], "proveedor": g["proveedor"],
            "monto": float(g["monto"]), "fecha_vencimiento": g["fecha_vencimiento"],
            "categoria": g["categoria"],
        })

    saldo_acum = float(saldo_inicial)
    total_ingresos = total_egresos = 0.0
    semanas_out = []
    for sem in range(semanas):
        inicio = hoy + timedelta(weeks=sem)
        fin = inicio + timedelta(days=6)
        ingresos = sum(i["monto"] for i in ingresos_semana.get(sem, []))
        egresos = sum(g["monto"] for g in gastos_semana.get(sem, []))
        saldo_acum += ingresos - egresos
        total_ingresos += ingresos
        total_egresos += egresos
        semanas_out.append({
            "semana": sem + 1,
            "label": f"{inicio.strftime('%d/%m')}-{fin.strftime('%d/%m')}",
            "ingresos": ingresos, "egresos": egresos,
            "saldo_acumulado": saldo_acum, "riesgo": saldo_acum < 0,
            "detalle_ingresos": ingresos_semana.get(sem, []),
            "detalle_egresos": gastos_semana.get(sem, []),
        })

    return {
        "saldo_inicial": float(saldo_inicial), "saldo_fecha": saldo_fecha,
        "hoy": hoy, "horizonte": horizonte, "semanas": semanas_out,
        "total_ingresos": total_ingresos, "total_egresos": total_egresos,
        "ingresos_fuera": ingresos_fuera,
    }
