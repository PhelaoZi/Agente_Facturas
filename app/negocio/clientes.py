"""Análisis de salud de la cartera de clientes (solo lectura).

`salud_clientes(cur)` detecta y prioriza clientes con señales de alerta
comercial (dormido, caída de consumo, baja frecuencia, nuevo sin recompra).
Es el cerebro del "gerente comercial" en el área de crecimiento y clientes:
solo detecta y prioriza; NO escribe ni decide a quién contactar.

Reglas canónicas: solo facturas (tipo_documento != 61), monto real =
COALESCE(monto_total_ajustado, monto_total), excluye clientes 'incobrable'.
Patrón de app/briefing/data.py: SQL agregada -> clasificación en Python,
testeable con cursor falso.
"""

# Umbrales (constantes con nombre, ajustables)
UMBRAL_DORMIDO_DIAS = 60          # sin comprar hace más de esto = dormido
CAIDA_CONSUMO_PCT = 0.40          # caída > 40% vs la ventana previa de 60 días
FACTOR_FRECUENCIA = 1.5           # brecha reciente > 1.5x la histórica
NUEVO_SIN_RECOMPRA_MIN_DIAS = 21  # 1 sola compra hace al menos esto (y <= dormido)
TOP_N_PRIORIDAD = 10              # top N histórico por facturación = prioridad alta


def salud_clientes(cur):
    """Lista de clientes con al menos una señal de alerta, priorizada
    (alta primero, luego mayor facturación histórica)."""
    cur.execute(_SQL)
    filas = cur.fetchall()
    top_ruts = _top_ruts(filas)
    resultado = []
    for f in filas:
        senales = _senales(f)
        if not senales:
            continue
        prioridad = "alta" if f["rut_cliente"] in top_ruts else "media"
        resultado.append({
            "rut": f["rut_cliente"],
            "cliente": f["razon_social"],
            "senales": senales,
            "prioridad": prioridad,
            "motivo": _motivo(senales, f, prioridad),
            "dias_desde_ultima": int(f["dias_desde_ultima"]),
            "ultima_venta": f["ultima_venta"],
            "total_historico": float(f["total_historico"]),
            "n_facturas": int(f["n_facturas"]),
        })
    resultado.sort(key=lambda c: (0 if c["prioridad"] == "alta" else 1,
                                  -c["total_historico"]))
    return resultado


def _top_ruts(filas):
    """RUTs del top N histórico por facturación (= prioridad alta)."""
    ordenados = sorted(filas, key=lambda f: float(f["total_historico"]), reverse=True)
    return {f["rut_cliente"] for f in ordenados[:TOP_N_PRIORIDAD]}


def _senales(f):
    """Señales activas para una fila de cliente."""
    dias = int(f["dias_desde_ultima"])
    if dias > UMBRAL_DORMIDO_DIAS:
        return ["dormido"]  # dormido: ya no se evalúan caída/frecuencia
    senales = []
    if _caida_consumo(f):
        senales.append("caida_consumo")
    if _bajo_frecuencia(f):
        senales.append("bajo_frecuencia")
    if _nuevo_sin_recompra(f, dias):
        senales.append("nuevo_sin_recompra")
    return senales


def _caida_consumo(f):
    prev = f["ventas_prev_60"]
    if not prev or float(prev) <= 0:
        return False  # sin base previa no se evalúa (evita falsos positivos)
    ult = float(f["ventas_ult_60"] or 0)
    return (float(prev) - ult) / float(prev) > CAIDA_CONSUMO_PCT


def _bajo_frecuencia(f):
    bh, br = f["brecha_historica_dias"], f["brecha_reciente_dias"]
    if not bh or not br or float(bh) <= 0:
        return False
    return float(br) > FACTOR_FRECUENCIA * float(bh)


def _nuevo_sin_recompra(f, dias):
    return (int(f["n_facturas"]) == 1
            and NUEVO_SIN_RECOMPRA_MIN_DIAS <= dias <= UMBRAL_DORMIDO_DIAS)


def _motivo(senales, f, prioridad):
    partes = []
    if "dormido" in senales:
        partes.append(f"dormido {int(f['dias_desde_ultima'])}d sin comprar")
    if "caida_consumo" in senales:
        prev = float(f["ventas_prev_60"] or 0)
        ult = float(f["ventas_ult_60"] or 0)
        pct = int(round((prev - ult) / prev * 100)) if prev > 0 else 0
        partes.append(f"-{pct}% de consumo vs sus 2 meses previos")
    if "bajo_frecuencia" in senales:
        partes.append(f"compra cada ~{int(round(float(f['brecha_reciente_dias'])))}d "
                      f"(antes ~{int(round(float(f['brecha_historica_dias'])))}d)")
    if "nuevo_sin_recompra" in senales:
        partes.append(f"compró 1 vez hace {int(f['dias_desde_ultima'])}d y no volvió")
    prefijo = "Cliente top: " if prioridad == "alta" else ""
    return prefijo + "; ".join(partes)


_SQL = """
WITH base AS (
    SELECT v.rut_cliente, c.razon_social, v.fecha,
           COALESCE(v.monto_total_ajustado, v.monto_total) AS monto,
           ROW_NUMBER() OVER (PARTITION BY v.rut_cliente ORDER BY v.fecha DESC) AS rn
    FROM ventas v
    JOIN clientes c ON c.rut_cliente = v.rut_cliente
    WHERE v.tipo_documento != 61
      AND COALESCE(c.estado, '') <> 'incobrable'
      AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
)
SELECT rut_cliente, razon_social,
       COUNT(*) AS n_facturas,
       SUM(monto) AS total_historico,
       MAX(fecha) AS ultima_venta,
       (CURRENT_DATE - MAX(fecha)) AS dias_desde_ultima,
       SUM(monto) FILTER (WHERE fecha >= CURRENT_DATE - 60) AS ventas_ult_60,
       SUM(monto) FILTER (WHERE fecha >= CURRENT_DATE - 120
                            AND fecha < CURRENT_DATE - 60) AS ventas_prev_60,
       CASE WHEN COUNT(*) >= 3
            THEN (MAX(fecha) - MIN(fecha))::numeric / (COUNT(*) - 1)
            ELSE NULL END AS brecha_historica_dias,
       (MAX(fecha) - MAX(fecha) FILTER (WHERE rn = 2)) AS brecha_reciente_dias
FROM base
GROUP BY rut_cliente, razon_social
"""
