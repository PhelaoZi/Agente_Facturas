#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zigurat ERP - Centro de Comando (Dashboard)
============================================
Panel de control oscuro que lee tu PostgreSQL local en vivo y muestra
la panoramica completa del negocio: ventas, clientes, cobranza y costos.

USO (la forma facil):
    Doble clic en  iniciar_dashboard.bat

USO (manual):
    python app/dashboard.py
    -> abre http://localhost:8777 en tu navegador

No requiere instalar nada nuevo: usa psycopg2 (ya presente en el proyecto)
y la libreria estandar de Python. Todas las consultas respetan las reglas
del proyecto (COALESCE de montos ajustados, exclusion de Notas de Credito,
tipo_documento como texto, etc.).
"""
import sys
import json
import os
import webbrowser
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception as e:  # pragma: no cover
    print("ERROR: falta psycopg2. Instala con:  pip install psycopg2-binary")
    print(f"Detalle: {e}")
    sys.exit(1)

# --------------------------------------------------------------------------- #
# Configuracion / entorno
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("ZIGURAT_DASH_PORT", "8777"))

# Al lanzar "python app/dashboard.py", sys.path[0] es la carpeta app/, no la
# raiz del proyecto: sin esto, "from app.negocio import acciones" (accion) y
# "from app.agent import orchestrator" (chat) fallan con ModuleNotFoundError.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_env() -> None:
    """Carga .env de la raiz sin depender de python-dotenv (patron del proyecto)."""
    env_file = PROJECT_ROOT / ".env"
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
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user": os.environ.get("DB_USER", "postgres"),
    # La clave vive SOLO en el .env (nunca hardcodeada como fallback).
    "password": os.environ.get("DB_PASSWORD", ""),
}


def get_conn():
    return psycopg2.connect(cursor_factory=RealDictCursor, **DB_CONFIG)


# --------------------------------------------------------------------------- #
# Introspeccion de esquema -> consultas robustas
# --------------------------------------------------------------------------- #
def introspect(cur):
    """Devuelve {tabla: set(columnas)} y un set de vistas/tablas existentes."""
    cur.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
    """)
    cols = {}
    for r in cur.fetchall():
        cols.setdefault(r["table_name"], set()).add(r["column_name"])
    cur.execute("""
        SELECT table_name FROM information_schema.tables WHERE table_schema='public'
        UNION
        SELECT table_name FROM information_schema.views WHERE table_schema='public'
    """)
    objs = {r["table_name"] for r in cur.fetchall()}
    return cols, objs


def money(cols, base):
    """Expresion monetaria que usa el campo ajustado si existe."""
    aj = base + "_ajustado"
    if aj in cols.get("ventas", set()):
        return f"COALESCE(v.{aj}, v.{base})"
    return f"v.{base}"


# Filtro estandar: excluir Notas de Credito. Cast a texto para tolerar int o text.
NOT_NC = "v.tipo_documento::text <> '61'"


# --------------------------------------------------------------------------- #
# Bloques de datos (cada uno tolera fallos por su cuenta)
# --------------------------------------------------------------------------- #
def q_resumen(cur, cols, objs):
    mt = money(cols, "monto_total")
    vcols = cols.get("ventas", set())
    ccols = cols.get("clientes", set())
    has_pago = "fecha_pago" in vcols
    out = {}

    cur.execute(f"""
        SELECT
          COALESCE(SUM({mt}) FILTER (WHERE v.fecha >= date_trunc('month', CURRENT_DATE)), 0) AS mes,
          COALESCE(SUM({mt}) FILTER (WHERE v.fecha >= date_trunc('month', CURRENT_DATE - interval '1 month')
                                       AND v.fecha <  date_trunc('month', CURRENT_DATE)), 0) AS mes_prev,
          COALESCE(SUM({mt}) FILTER (WHERE v.fecha >= date_trunc('year', CURRENT_DATE)), 0) AS ytd,
          COALESCE(SUM({mt}), 0) AS total,
          COUNT(*) FILTER (WHERE v.fecha >= date_trunc('month', CURRENT_DATE)) AS facturas_mes,
          COUNT(*) AS facturas_total,
          COALESCE(AVG({mt}), 0) AS ticket,
          MIN(v.fecha) AS primera, MAX(v.fecha) AS ultima
        FROM ventas v
        WHERE {NOT_NC}
    """)
    out.update(cur.fetchone())

    if has_pago:
        # "Por cobrar" = credito COBRABLE: excluye clientes incobrables (castigados)
        # para que no inflen el total de credito que el dueno quiere ver.
        cur.execute(f"""
            SELECT COALESCE(SUM({mt}),0) AS por_cobrar,
                   COUNT(*) AS por_cobrar_n,
                   COALESCE(SUM({mt}) FILTER (WHERE (CURRENT_DATE - v.fecha) > 30),0) AS vencido,
                   COUNT(*) FILTER (WHERE (CURRENT_DATE - v.fecha) > 30) AS vencido_n
            FROM ventas v
            LEFT JOIN clientes c ON c.rut_cliente = v.rut_cliente
            WHERE {NOT_NC} AND v.fecha_pago IS NULL
              AND COALESCE(c.estado, '') <> 'incobrable'
        """)
        out.update(cur.fetchone())
        # Incobrable (castigado) por separado: visible pero FUERA del credito cobrable.
        cur.execute(f"""
            SELECT COALESCE(SUM({mt}),0) AS incobrable,
                   COUNT(*) AS incobrable_n
            FROM ventas v
            JOIN clientes c ON c.rut_cliente = v.rut_cliente
            WHERE {NOT_NC} AND v.fecha_pago IS NULL AND c.estado = 'incobrable'
        """)
        out.update(cur.fetchone())
    else:
        out.update({"por_cobrar": None, "por_cobrar_n": None, "vencido": None,
                    "vencido_n": None, "incobrable": None, "incobrable_n": None})

    # Clientes
    if "estado" in ccols:
        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE estado = 'activo') AS activos,
                   COUNT(*) FILTER (WHERE estado = 'incobrable') AS incobrables
            FROM clientes
        """)
    else:
        cur.execute("SELECT COUNT(*) AS total, NULL AS activos, NULL AS incobrables FROM clientes")
    cli = cur.fetchone()
    out["clientes_total"] = cli["total"]
    out["clientes_activos"] = cli["activos"]
    out["clientes_incobrables"] = cli["incobrables"]

    # Saldo banco
    out["saldo_banco"] = None
    if "movimientos_banco" in objs and "saldo_diario" in cols.get("movimientos_banco", set()):
        try:
            cur.execute("""
                SELECT saldo_diario FROM movimientos_banco
                WHERE saldo_diario IS NOT NULL ORDER BY fecha DESC, id DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                out["saldo_banco"] = row["saldo_diario"]
        except Exception:
            pass
    return out


def q_ventas_mensuales(cur, cols):
    mt = money(cols, "monto_total")
    cur.execute(f"""
        SELECT to_char(date_trunc('month', v.fecha), 'YYYY-MM') AS mes,
               SUM({mt}) AS total,
               COUNT(*) AS facturas
        FROM ventas v
        WHERE {NOT_NC} AND v.fecha IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    return cur.fetchall()


def q_top_clientes(cur, cols, limit=10):
    mt = money(cols, "monto_total")
    cur.execute(f"""
        SELECT c.razon_social, v.rut_cliente,
               SUM({mt}) AS total,
               COUNT(*) AS facturas
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE {NOT_NC}
        GROUP BY v.rut_cliente, c.razon_social
        ORDER BY total DESC
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


def q_top_productos(cur, cols, limit=12):
    """Productos mas vendidos por unidades (barriles/botellas).

    Excluye lineas que no son producto (regla documentada en CLAUDE.md):
    - 'Logistica *': desglose tributario del precio del barril (no es servicio).
    - 'Barril Pet *' / 'Pet *': envase desechable traspasado al cliente
      (pass-through sin margen, no es venta de cerveza).
    """
    if "productos" not in cols:
        return []
    pcols = cols.get("productos", set())
    name_col = "nombre_producto" if "nombre_producto" in pcols else (
        "descripcion" if "descripcion" in pcols else None)
    if not name_col:
        return []
    monto_expr = "SUM(p.total_linea)" if "total_linea" in pcols else "NULL"
    cur.execute(f"""
        SELECT p.{name_col} AS producto,
               SUM(p.cantidad) AS unidades,
               {monto_expr} AS monto
        FROM productos p
        JOIN ventas v ON v.folio::text = p.folio::text
                     AND v.tipo_documento::text = p.tipo_documento::text
        WHERE {NOT_NC}
          AND p.{name_col} NOT ILIKE '%%logist%%'
          AND p.{name_col} !~* '^(barril(es)?\\s+)?pet\\y'
        GROUP BY p.{name_col}
        ORDER BY unidades DESC NULLS LAST
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


def q_morosos(cur, cols, limit=15):
    if "fecha_pago" not in cols.get("ventas", set()):
        return []
    mt = money(cols, "monto_total")
    cur.execute(f"""
        SELECT c.razon_social, v.rut_cliente,
               SUM({mt}) AS deuda,
               COUNT(*) AS facturas,
               MAX(CURRENT_DATE - v.fecha) AS dias_max
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE {NOT_NC} AND v.fecha_pago IS NULL
          AND (CURRENT_DATE - v.fecha) > 30
          AND COALESCE(c.estado, '') <> 'incobrable'
        GROUP BY v.rut_cliente, c.razon_social
        ORDER BY deuda DESC
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


def q_inactivos(cur, cols, limit=15):
    mt = money(cols, "monto_total")
    estado_sel = "c.estado" if "estado" in cols.get("clientes", set()) else "NULL AS estado"
    cur.execute(f"""
        SELECT c.razon_social, v.rut_cliente,
               {estado_sel},
               MAX(v.fecha) AS ultima_compra,
               (CURRENT_DATE - MAX(v.fecha)) AS dias_sin_comprar,
               SUM({mt}) AS total_historico
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE {NOT_NC}
        GROUP BY v.rut_cliente, c.razon_social{(', c.estado' if 'estado' in cols.get('clientes', set()) else '')}
        HAVING (CURRENT_DATE - MAX(v.fecha)) > 60
        ORDER BY total_historico DESC
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


def q_estado_clientes(cur, cols):
    if "estado" not in cols.get("clientes", set()):
        return []
    cur.execute("SELECT estado, COUNT(*) AS n FROM clientes GROUP BY estado ORDER BY n DESC")
    return cur.fetchall()


def q_por_cobrar(cur, cols, limit=40):
    if "fecha_pago" not in cols.get("ventas", set()):
        return []
    mt = money(cols, "monto_total")
    cur.execute(f"""
        SELECT v.folio, v.fecha,
               COALESCE(c.razon_social, v.razon_social_receptor) AS cliente,
               v.rut_cliente,
               {mt} AS monto,
               (CURRENT_DATE - v.fecha) AS dias
        FROM ventas v
        LEFT JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE {NOT_NC} AND v.fecha_pago IS NULL
          AND COALESCE(c.estado, '') <> 'incobrable'
        ORDER BY v.fecha ASC
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


def q_por_pagar(cur, cols, objs, limit=40):
    if "cuentas_por_pagar" not in objs:
        return []
    cp = cols.get("cuentas_por_pagar", set())
    cond = []
    if "pagado" in cp:
        cond.append("(pagado = FALSE OR pagado IS NULL)")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    sel_prov = "proveedor" if "proveedor" in cp else "NULL AS proveedor"
    sel_cat = "categoria" if "categoria" in cp else "NULL AS categoria"
    cur.execute(f"""
        SELECT descripcion, {sel_prov}, monto, fecha_vencimiento, {sel_cat}
        FROM cuentas_por_pagar
        {where}
        ORDER BY fecha_vencimiento ASC NULLS LAST
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


def q_costos_sku(cur, objs):
    if "vista_costo_sku" not in objs:
        return []
    cur.execute("""
        SELECT codigo, nombre_cerveza, formato,
               costo_liquido_unitario, costo_envasado_unitario, costo_total_unitario
        FROM vista_costo_sku
        ORDER BY nombre_cerveza, formato
    """)
    return cur.fetchall()


# Precios de venta confirmados (neto, por barril 30L) - desde CLAUDE.md
PRECIOS_VENTA_NETO = {
    "cream ale": 55370,
    "scotch ale": 55370,
    "stout cafe": 75000,
    "stout cacao": 75000,
    "paint it black": 98000,
}

import datetime as _dt
from calendar import monthrange as _monthrange


def _fmt_clp(n):
    try:
        return "$" + f"{int(round(float(n))):,}".replace(",", ".")
    except Exception:
        return "$0"


def _next_month(ym):
    y, m = map(int, ym.split("-"))
    m += 1
    if m > 12:
        y += 1; m = 1
    return f"{y:04d}-{m:02d}"


# ── Predicción de ventas (pura, sobre el histórico ya consultado) ──────────────
def _months_between(a, b):
    ay, am = map(int, a.split("-")); by, bm = map(int, b.split("-"))
    return (by - ay) * 12 + (bm - am)


def predecir_ventas(ventas_mensuales):
    data = {r["mes"]: float(r.get("total") or 0)
            for r in (ventas_mensuales or []) if r.get("mes")}
    if not data:
        return {"disponible": False, "motivo": "Aún no hay datos de ventas."}
    cur_m = _dt.date.today().strftime("%Y-%m")
    completos = sorted(m for m in data if m < cur_m)   # meses ya cerrados
    if len(completos) < 3:
        return {"disponible": False,
                "motivo": "Se necesitan al menos 3 meses cerrados para proyectar.",
                "historico": [{"mes": m, "total": data[m]} for m in sorted(data)]}
    # Serie contigua: rellena meses faltantes con 0 (primero → último cerrado)
    first, last = completos[0], completos[-1]
    serie = []; mm = first
    while True:
        serie.append((mm, data.get(mm, 0.0)))
        if mm == last:
            break
        mm = _next_month(mm)
    serie = serie[-12:]
    n = len(serie)
    base_off = _months_between(first, serie[0][0])      # por si recortamos a 12
    xs = list(range(n)); ys = [t for _, t in serie]
    mx = sum(xs) / n; my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs) or 1
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / den
    inter = my - slope * mx
    resid = [ys[i] - (inter + slope * xs[i]) for i in range(n)]
    sd = (sum(r * r for r in resid) / n) ** 0.5
    # Pronóstico: mes en curso y los 2 siguientes (índices según calendario)
    idx_cur = _months_between(serie[0][0], cur_m)
    fc = []; fm = cur_m
    for k in range(3):
        val = inter + slope * (idx_cur + k)
        fc.append({"mes": fm, "total": max(0, val),
                   "min": max(0, val - sd), "max": val + sd})
        fm = _next_month(fm)
    return {"disponible": True,
            "historico": [{"mes": m, "total": data[m]} for m in completos[-12:]],
            "pronostico": fc, "promedio": my,
            "tendencia": "subiendo" if slope > 5000 else ("bajando" if slope < -5000 else "estable"),
            "metodo": f"Regresión lineal sobre {n} meses cerrados."}


# ── Recomendaciones (puras, derivadas del payload ya armado) ───────────────────
def construir_recomendaciones(payload):
    recs = []
    mor = payload.get("morosos") or []
    if mor:
        deuda = sum(float(x.get("deuda") or 0) for x in mor)
        t = mor[0]
        recs.append({"prioridad": "alta", "icono": "cash",
            "titulo": f"Cobra {_fmt_clp(deuda)} en {len(mor)} clientes morosos",
            "detalle": f"El más urgente: {t.get('razon_social')} debe {_fmt_clp(t.get('deuda'))} "
                       f"(vencido hace {t.get('dias_max')} días)."})
    ina = payload.get("inactivos") or []
    if ina:
        t = ina[0]
        recs.append({"prioridad": "media", "icono": "refresh",
            "titulo": f"Reactiva {len(ina)} clientes inactivos",
            "detalle": f"{t.get('razon_social')} no compra hace {t.get('dias_sin_comprar')} días "
                       f"(histórico {_fmt_clp(t.get('total_historico'))})."})
    tc = payload.get("top_clientes") or []
    tot = sum(float(x.get("total") or 0) for x in tc)
    if tc and tot > 0:
        share = float(tc[0].get("total") or 0) / tot * 100
        if share >= 30:
            recs.append({"prioridad": "media", "icono": "alert-triangle",
                "titulo": f"Riesgo de concentración: {tc[0].get('razon_social')} es el {share:.0f}% de tus ventas",
                "detalle": "Depender tanto de un solo cliente es riesgoso; conviene diversificar la cartera."})
    costos = payload.get("costos_sku") or []
    precios = payload.get("precios_venta") or {}
    bajos = []
    for x in costos:
        f = (x.get("formato") or "").lower()
        if not ("30" in f or "barril" in f):
            continue
        key = next((k for k in precios if k in (x.get("nombre_cerveza") or "").lower()), None)
        if not key:
            continue
        venta = precios[key]; costo = float(x.get("costo_total_unitario") or 0)
        if venta:
            m = (venta - costo) / venta * 100
            if m < 25:
                bajos.append((x.get("nombre_cerveza"), m))
    if bajos:
        nm = ", ".join(f"{b[0]} ({b[1]:.0f}%)" for b in bajos)
        recs.append({"prioridad": "media", "icono": "chart-bar",
            "titulo": f"Margen bajo en {len(bajos)} producto(s)",
            "detalle": f"Revisa precio o costo de: {nm}."})
    pv = payload.get("prediccion_ventas") or {}
    if pv.get("disponible") and pv.get("tendencia") == "bajando":
        recs.append({"prioridad": "alta", "icono": "trending-down",
            "titulo": "Tus ventas vienen a la baja",
            "detalle": "La proyección de los próximos meses es decreciente. Considera una campaña o promoción."})
    if not recs:
        recs.append({"prioridad": "baja", "icono": "check",
            "titulo": "Todo en orden", "detalle": "No detecté acciones urgentes. ¡Buen trabajo!"})
    return recs


# ── Flujo de caja 4 semanas (replica scripts/flujo_caja.py) ────────────────────
def q_flujo_caja(cur, cols, objs):
    SEM, AVG_G, MINF = 4, 30, 3
    vcols = cols.get("ventas", set())
    has_pago = "fecha_pago" in vcols
    has_dias = "dias_pago" in vcols
    mt = money(cols, "monto_total")
    hoy = _dt.date.today(); horizonte = hoy + _dt.timedelta(weeks=SEM)

    saldo = 0.0; saldo_fecha = None
    if "movimientos_banco" in objs and "saldo_diario" in cols.get("movimientos_banco", set()):
        cur.execute("SELECT saldo_diario, fecha FROM movimientos_banco "
                    "WHERE saldo_diario IS NOT NULL ORDER BY fecha DESC, id DESC LIMIT 1")
        r = cur.fetchone()
        if r and r.get("saldo_diario") is not None:
            saldo = float(r["saldo_diario"]); saldo_fecha = r.get("fecha")

    avg = {}
    if has_pago and has_dias:
        cur.execute(f"""
            SELECT rut_cliente, AVG(dias_pago) AS a FROM (
              SELECT rut_cliente, dias_pago,
                     ROW_NUMBER() OVER (PARTITION BY rut_cliente ORDER BY fecha DESC) rn
              FROM ventas
              WHERE fecha_pago IS NOT NULL AND dias_pago IS NOT NULL AND dias_pago > 0
                AND tipo_documento::text <> '61') t
            WHERE rn <= 10 GROUP BY rut_cliente HAVING COUNT(*) >= %s
        """, (MINF,))
        avg = {r["rut_cliente"]: float(r["a"]) for r in cur.fetchall()}

    facturas = []
    if has_pago:
        cur.execute(f"""SELECT folio, fecha, rut_cliente, razon_social_receptor, {mt} AS monto
                        FROM ventas v WHERE {NOT_NC} AND v.fecha_pago IS NULL ORDER BY fecha""")
        facturas = cur.fetchall()

    gastos = []
    ccp = "cuentas_por_pagar" in objs
    if ccp:
        cp = cols.get("cuentas_por_pagar", set())
        pag = "AND (pagado = FALSE OR pagado IS NULL)" if "pagado" in cp else ""
        cat = ", categoria" if "categoria" in cp else ""
        no_rec = "AND (recurrente = FALSE OR recurrente IS NULL)" if "recurrente" in cp else ""
        cur.execute(f"""SELECT descripcion, monto, fecha_vencimiento {cat}
                        FROM cuentas_por_pagar
                        WHERE fecha_vencimiento BETWEEN %s AND %s {pag} {no_rec}""",
                    (hoy, horizonte))
        gastos = [dict(r) for r in cur.fetchall()]
        if "recurrente" in cp and "periodicidad" in cp:
            cur.execute(f"""SELECT descripcion, monto, fecha_vencimiento {cat}
                            FROM cuentas_por_pagar
                            WHERE recurrente = TRUE AND periodicidad = 'mensual' {pag}""")
            for r in cur.fetchall():
                base = r.get("fecha_vencimiento")
                if not base:
                    continue
                dia = base.day
                for dm in range(3):
                    ma = hoy.month + dm; y = hoy.year + (ma - 1) // 12; mo = (ma - 1) % 12 + 1
                    d = min(dia, _monthrange(y, mo)[1]); fp = _dt.date(y, mo, d)
                    if hoy <= fp <= horizonte:
                        rr = dict(r); rr["fecha_vencimiento"] = fp; gastos.append(rr)

    def semana(d):
        return max(0, min((d - hoy).days // 7, SEM - 1))

    ing = [0.0] * SEM; egr = [0.0] * SEM
    det_ing = [[] for _ in range(SEM)]; det_egr = [[] for _ in range(SEM)]
    for f in facturas:
        a = avg.get(f["rut_cliente"], AVG_G)
        fp = f["fecha"] + _dt.timedelta(days=int(a))
        if fp < hoy:
            fp = hoy
        if fp <= horizonte:
            s = semana(fp); m = float(f.get("monto") or 0); ing[s] += m
            det_ing[s].append({"folio": f["folio"], "cliente": f.get("razon_social_receptor"),
                               "monto": m, "fecha": str(fp)})
    for g in gastos:
        fv = g.get("fecha_vencimiento")
        if fv and hoy <= fv <= horizonte:
            s = semana(fv); m = float(g.get("monto") or 0); egr[s] += m
            det_egr[s].append({"desc": g.get("descripcion"), "monto": m, "fecha": str(fv)})

    semanas = []; acum = saldo
    for s in range(SEM):
        ini = hoy + _dt.timedelta(weeks=s); fin = ini + _dt.timedelta(days=6)
        acum += ing[s] - egr[s]
        semanas.append({"label": f"{ini.strftime('%d/%m')}–{fin.strftime('%d/%m')}",
                        "ingresos": ing[s], "egresos": egr[s], "saldo": acum,
                        "riesgo": acum < 0, "det_ing": det_ing[s], "det_egr": det_egr[s]})
    nota = None
    if not has_pago:
        nota = "Sin columna fecha_pago: no puedo proyectar ingresos."
    elif not ccp:
        nota = "Sin tabla cuentas_por_pagar: no puedo proyectar egresos."
    return {"disponible": True, "saldo_inicial": saldo,
            "saldo_fecha": str(saldo_fecha) if saldo_fecha else None,
            "semanas": semanas, "total_ingresos": sum(ing), "total_egresos": sum(egr),
            "horizonte": str(horizonte), "nota": nota}


# ── Cobranza inteligente ───────────────────────────────────────────────────────
def _cargar_contactos():
    """Lee contactos.csv (rut,email,telefono) de la raíz si existe."""
    import csv
    out = {}
    path = PROJECT_ROOT / "contactos.csv"
    if path.exists():
        try:
            with open(path, encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    low = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                    rut = low.get("rut") or low.get("rut_cliente") or ""
                    if not rut:
                        continue
                    out[rut.strip()] = {
                        "email": low.get("email") or low.get("correo") or low.get("mail") or "",
                        "telefono": low.get("telefono") or low.get("fono") or low.get("celular") or "",
                    }
        except Exception:
            pass
    return out


def _plantilla_cobranza(categoria, nombre, monto, n, folios, dias):
    """Devuelve (asunto, cuerpo) de correo de cobranza según el tipo de pagador."""
    nombre = (nombre or "estimado cliente").title()
    montos = _fmt_clp(monto)
    fol = ", ".join(f"N°{x}" for x in folios[:6]) + ("…" if len(folios) > 6 else "")
    plural = "las facturas" if n > 1 else "la factura"
    firma = "Equipo Zigurat Brewery\nElaboradora y Comercializadora Vintage SPA"
    if categoria == "bueno":
        asunto = f"Recordatorio amistoso de pago · {montos} · Zigurat"
        cuerpo = (f"Hola {nombre}, ¿cómo estás?\n\n"
                  f"Te escribo solo para recordarte de forma amable que tienes pendiente {plural} {fol} "
                  f"por un total de {montos}. Nada urgente, pero quería dejártelo presente.\n\n"
                  f"Si ya realizaste el pago, ignora este mensaje y avísame para registrarlo. "
                  f"Cualquier cosa, quedo atento.\n\n"
                  f"¡Gracias por tu preferencia y por apoyar la cerveza artesanal!\n\n{firma}")
    elif categoria == "cronico":
        asunto = f"Pago pendiente {montos} · {plural.capitalize()} con {dias} días de atraso"
        cuerpo = (f"Estimado/a {nombre}:\n\n"
                  f"Le escribo respecto al pago pendiente de {plural} {fol}, por un total de {montos}, "
                  f"con un atraso de {dias} días.\n\n"
                  f"Le agradeceré regularizar el pago a la brevedad. Si ya lo efectuó, por favor "
                  f"envíeme el comprobante para registrarlo. Si necesita coordinar un acuerdo de pago, "
                  f"escríbame y lo vemos.\n\nQuedo atento a su respuesta.\n\n{firma}")
    elif categoria == "incobrable":
        asunto = f"Regularización de deuda pendiente · {montos}"
        cuerpo = (f"Estimado/a {nombre}:\n\n"
                  f"Tenemos registrada una deuda pendiente de {montos} correspondiente a {plural} {fol}, "
                  f"con {dias} días de atraso.\n\n"
                  f"Le solicitamos contactarse para regularizar su situación o acordar una forma de pago. "
                  f"De no recibir respuesta, deberemos evaluar los pasos a seguir.\n\n"
                  f"Quedamos atentos.\n\n{firma}")
    else:  # medio
        asunto = f"Recordatorio de pago · {montos} · Zigurat"
        cuerpo = (f"Hola {nombre}, espero que estés muy bien.\n\n"
                  f"Te recuerdo que tienes pendiente el pago de {plural} {fol} por un total de {montos} "
                  f"({dias} días desde la emisión).\n\n"
                  f"Te agradecería coordinar el pago cuando puedas. Si ya lo hiciste, avísame y lo registro.\n\n"
                  f"¡Gracias por tu preferencia!\n\n{firma}")
    return asunto, cuerpo


def q_cobranza(cur, cols, objs):
    """Facturas por cobrar priorizadas, con tono y correo redactado por cliente."""
    vcols = cols.get("ventas", set())
    if "fecha_pago" not in vcols:
        return []
    ccols = cols.get("clientes", set())
    mt = money(cols, "monto_total")
    email_col = next((c for c in ["email", "correo", "correo_electronico", "mail", "e_mail"]
                      if c in ccols), None)
    sel_email = f"c.{email_col} AS email" if email_col else "NULL AS email"
    sel_estado = "c.estado" if "estado" in ccols else "NULL AS estado"
    group = ["c.razon_social", "v.rut_cliente"]
    if email_col:
        group.append(f"c.{email_col}")
    if "estado" in ccols:
        group.append("c.estado")
    cur.execute(f"""
        SELECT c.razon_social, v.rut_cliente, {sel_email}, {sel_estado},
               SUM({mt}) AS deuda, COUNT(*) AS n,
               MAX(CURRENT_DATE - v.fecha) AS dias_max,
               array_agg(v.folio ORDER BY v.fecha) AS folios
        FROM ventas v JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE {NOT_NC} AND v.fecha_pago IS NULL
        GROUP BY {", ".join(group)}
    """)
    pendientes = cur.fetchall()

    # Historial de comportamiento de pago por cliente
    avg = {}
    if "dias_pago" in vcols:
        try:
            cur.execute(f"""
                SELECT rut_cliente, AVG(dias_pago) AS a, COUNT(*) AS cnt FROM (
                  SELECT rut_cliente, dias_pago,
                         ROW_NUMBER() OVER (PARTITION BY rut_cliente ORDER BY fecha DESC) rn
                  FROM ventas
                  WHERE fecha_pago IS NOT NULL AND dias_pago IS NOT NULL AND dias_pago > 0
                    AND tipo_documento::text <> '61') t
                WHERE rn <= 10 GROUP BY rut_cliente
            """)
            avg = {r["rut_cliente"]: (float(r["a"]), int(r["cnt"])) for r in cur.fetchall()}
        except Exception:
            avg = {}

    contactos = _cargar_contactos()

    def peso(dias):
        if dias is None:
            return 1.0
        if dias > 90: return 3.0
        if dias > 60: return 2.4
        if dias > 30: return 1.8
        if dias > 15: return 1.2
        return 1.0

    items = []
    for p in pendientes:
        rut = p["rut_cliente"]
        dias = int(p["dias_max"] or 0)
        estado = p.get("estado")
        prom_dias, cnt = avg.get(rut, (None, 0))
        if estado == "incobrable":
            categoria = "incobrable"
        elif prom_dias is not None and prom_dias <= 32 and dias < 60:
            categoria = "bueno"
        elif dias > 75 or (prom_dias is not None and prom_dias > 60):
            categoria = "cronico"
        else:
            categoria = "medio"
        folios = [str(x) for x in (p.get("folios") or [])]
        deuda = float(p["deuda"] or 0)
        asunto, cuerpo = _plantilla_cobranza(categoria, p["razon_social"], deuda,
                                             int(p["n"] or 0), folios, dias)
        email = p.get("email") or contactos.get(rut, {}).get("email") or ""
        items.append({
            "razon_social": p["razon_social"], "rut_cliente": rut,
            "email": email, "estado": estado,
            "deuda": deuda, "n": int(p["n"] or 0), "dias_max": dias,
            "folios": folios, "categoria": categoria,
            "prom_dias_pago": round(prom_dias) if prom_dias is not None else None,
            "vencida": dias > 30,
            "score": deuda * peso(dias),
            "asunto": asunto, "cuerpo": cuerpo,
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def construir_brief(payload):
    """Resumen accionable del día a partir de cobranza, flujo y recomendaciones."""
    r = payload.get("resumen") or {}
    cob = payload.get("cobranza") or []
    fj = payload.get("flujo_caja") or {}
    recs = payload.get("recomendaciones") or []
    por_pagar = payload.get("por_pagar") or []
    hoy = _dt.date.today()

    vence = []
    for g in por_pagar:
        fv = g.get("fecha_vencimiento")
        try:
            d = _dt.date.fromisoformat(str(fv)[:10]) if fv else None
        except Exception:
            d = None
        if d and 0 <= (d - hoy).days <= 7:
            vence.append({"desc": g.get("descripcion"), "monto": g.get("monto"), "fecha": str(d)})

    cobrar_hoy = [c for c in cob if c.get("vencida") and c.get("categoria") != "incobrable"][:3]

    riesgo_sem = None
    for s in (fj.get("semanas") or []):
        if s.get("riesgo"):
            riesgo_sem = s
            break

    if riesgo_sem:
        principal = (f"Cuidado con la caja: queda en rojo la semana {riesgo_sem['label']} "
                     f"({_fmt_clp(riesgo_sem['saldo'])}). Adelanta cobranzas o posterga un gasto.")
    elif cobrar_hoy:
        t = cobrar_hoy[0]
        principal = (f"Parte el día cobrando: {t['razon_social']} debe {_fmt_clp(t['deuda'])} "
                     f"(hace {t['dias_max']} días).")
    elif r.get("vencido"):
        principal = f"Tienes {_fmt_clp(r['vencido'])} en facturas vencidas por cobrar."
    else:
        principal = "Sin urgencias hoy. Buen momento para planificar la próxima producción."

    alertas = [x["titulo"] for x in recs if x.get("prioridad") == "alta"][:2]
    return {"fecha": str(hoy), "principal": principal, "cobrar_hoy": cobrar_hoy,
            "vence_pronto": vence[:4], "alertas": alertas,
            "por_cobrar": r.get("por_cobrar"), "vencido": r.get("vencido"),
            "saldo_banco": r.get("saldo_banco")}


def build_payload():
    """Arma el JSON completo. Cada bloque es tolerante a fallos."""
    payload = {"ok": True, "errores": [], "bloques_vacios": []}
    try:
        conn = get_conn()
    except Exception as e:
        return {"ok": False, "error_conexion": str(e)}

    try:
        with conn:
            with conn.cursor() as cur:
                cols, objs = introspect(cur)

                def run(nombre, fn, *a):
                    try:
                        data = fn(cur, *a)
                        if not data:
                            payload["bloques_vacios"].append(nombre)
                        return data
                    except Exception as e:
                        payload["errores"].append(f"{nombre}: {e}")
                        # rollback parcial para no arrastrar el error a la siguiente query
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        return [] if nombre != "resumen" else {}

                payload["resumen"] = run("resumen", q_resumen, cols, objs)
                payload["ventas_mensuales"] = run("ventas_mensuales", q_ventas_mensuales, cols)
                payload["top_clientes"] = run("top_clientes", q_top_clientes, cols)
                payload["top_productos"] = run("top_productos", q_top_productos, cols)
                payload["morosos"] = run("morosos", q_morosos, cols)
                payload["inactivos"] = run("inactivos", q_inactivos, cols)
                payload["estado_clientes"] = run("estado_clientes", q_estado_clientes, cols)
                payload["por_cobrar"] = run("por_cobrar", q_por_cobrar, cols)
                payload["por_pagar"] = run("por_pagar", q_por_pagar, cols, objs)
                payload["costos_sku"] = run("costos_sku", q_costos_sku, objs)
                payload["precios_venta"] = PRECIOS_VENTA_NETO
                payload["flujo_caja"] = run("flujo_caja", q_flujo_caja, cols, objs)
                payload["cobranza"] = run("cobranza", q_cobranza, cols, objs)
    finally:
        conn.close()

    # Cálculos puros (no tocan la BD): predicción, recomendaciones y brief
    try:
        payload["prediccion_ventas"] = predecir_ventas(payload.get("ventas_mensuales"))
    except Exception as e:
        payload["errores"].append(f"prediccion_ventas: {e}")
        payload["prediccion_ventas"] = {"disponible": False, "motivo": "Error al calcular."}
    try:
        payload["recomendaciones"] = construir_recomendaciones(payload)
    except Exception as e:
        payload["errores"].append(f"recomendaciones: {e}")
        payload["recomendaciones"] = []
    try:
        payload["brief"] = construir_brief(payload)
    except Exception as e:
        payload["errores"].append(f"brief: {e}")
        payload["brief"] = {}

    return payload


# --------------------------------------------------------------------------- #
# Servidor HTTP
# --------------------------------------------------------------------------- #
def _json_default(o):
    # fechas, Decimals, etc.
    try:
        import decimal
        if isinstance(o, decimal.Decimal):
            return float(o)
    except Exception:
        pass
    return str(o)


def _log_agente_error(pregunta: str, detalle: str, trace: str) -> None:
    """Registra en logs/agente_chat.log el fallo del agente para diagnosticarlo despues.

    El error que ve el usuario ('Claude Code returned an error result: ...') es un
    mensaje generico de la SDK cuando el subproceso del CLI sale con codigo != 0; la
    causa real (rate limit, crash, etc.) solo queda aqui. Nunca debe romper la
    respuesta, asi que va envuelto en su propio try/except.
    """
    try:
        from datetime import datetime
        log_path = PROJECT_ROOT / "logs" / "agente_chat.log"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {ts} =====\n")
            f.write(f"PREGUNTA: {pregunta}\n")
            f.write(f"DETALLE: {detalle}\n")
            f.write(f"TRACE:\n{trace}\n")
    except Exception:
        pass  # nunca bloquear la respuesta por un fallo al loguear


# Sesion de chat vigente (servidor mono-usuario): permite que el agente recuerde
# la conversacion entre preguntas. El boton "Limpiar" la resetea via /api/chat-reset.
CHAT_SESSION = {"id": None}

# Modelos ofrecidos en el selector del chat (ids validados contra el catalogo
# de OpenRouter el 2026-07-20; todos soportan tool calling, que el agente
# necesita). Deben coincidir con las <option> de dashboard_ui.html.
# GLM 5.2 es el default: ~5x mas barato que Sonnet y solido encadenando tools.
MODELO_CHAT_DEFAULT = "z-ai/glm-5.2"
MODELOS_CHAT_PERMITIDOS = {
    "z-ai/glm-5.2",
    "google/gemini-3.1-flash-lite",
    "anthropic/claude-sonnet-5",
    "openai/gpt-5.6-luna",
}


# ── Protección anti-CSRF de los endpoints POST ────────────────────────────────
# El servidor escucha solo en 127.0.0.1, pero cualquier página abierta en el
# navegador puede hacer POST a http://localhost:8777 (borrar gastos, marcar
# facturas pagadas). Se exige Host local (bloquea DNS rebinding) y, si el
# navegador manda Origin (siempre lo hace en POST cross-origin), que sea el
# del propio dashboard. Sin Origin (curl, scripts locales) se permite.
HOSTS_PERMITIDOS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
ORIGENES_PERMITIDOS = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}


def origen_permitido(host, origin):
    """True si el request viene del propio dashboard (o de un cliente local
    sin Origin). False para POST cross-origin o Host ajeno."""
    if (host or "") not in HOSTS_PERMITIDOS:
        return False
    if not origin:
        return True
    return origin in ORIGENES_PERMITIDOS


def run_agent(pregunta: str, model: str = MODELO_CHAT_DEFAULT) -> dict:
    """Reutiliza el agente (loop propio sobre OpenRouter). Degrada con gracia."""
    try:
        from app.agent import orchestrator
        from app.canvas.artifacts import Collector
    except Exception as e:
        return {"ok": False, "error": "asistente_no_disponible", "detalle": str(e)}
    try:
        col = Collector()
        texto, session_id = orchestrator.run(pregunta, col, CHAT_SESSION["id"], model=model)
        CHAT_SESSION["id"] = session_id
        arts = [{"tipo": a.tipo, "titulo": a.titulo, "payload": a.payload} for a in col.items]
        return {"ok": True, "texto": texto, "artefactos": arts}
    except Exception as e:
        import traceback
        trace = traceback.format_exc()
        _log_agente_error(pregunta, str(e), trace)
        return {"ok": False, "error": "error_agente", "detalle": str(e),
                "trace": trace[-1200:]}


def _md_to_html(s: str) -> str:
    import re
    s = (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return s.replace("\n", "<br>")


def build_informe_html(payload: dict) -> str:
    """Informe mensual imprimible (tema claro, listo para 'Guardar como PDF')."""
    r = payload.get("resumen", {}) or {}
    f = _fmt_clp
    hoy = _dt.date.today()
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    mes_nombre = f"{meses[hoy.month - 1]} {hoy.year}"

    def filas_top(rows, n=8):
        out = ""
        for i, x in enumerate(rows[:n], 1):
            out += (f"<tr><td>{i}</td><td>{x.get('razon_social','')}</td>"
                    f"<td class='r'>{f(x.get('total'))}</td>"
                    f"<td class='r'>{int(x.get('facturas') or 0)}</td></tr>")
        return out or "<tr><td colspan=4 class='muted'>Sin datos</td></tr>"

    def filas_mor(rows, n=8):
        out = ""
        for x in rows[:n]:
            out += (f"<tr><td>{x.get('razon_social','')}</td>"
                    f"<td class='r'>{int(x.get('dias_max') or 0)} d</td>"
                    f"<td class='r'>{f(x.get('deuda'))}</td></tr>")
        return out or "<tr><td colspan=3 class='muted'>Sin deudas vencidas</td></tr>"

    def items_rec(rows):
        out = ""
        col = {"alta": "#c2410c", "media": "#a16207", "baja": "#15803d"}
        for x in rows:
            c = col.get(x.get("prioridad"), "#555")
            out += (f"<li><b style='color:{c}'>{x.get('titulo','')}</b><br>"
                    f"<span class='muted'>{x.get('detalle','')}</span></li>")
        return out or "<li class='muted'>Sin recomendaciones</li>"

    pv = payload.get("prediccion_ventas") or {}
    pv_txt = ""
    if pv.get("disponible"):
        prox = pv.get("pronostico", [])
        if prox:
            pv_txt = ("Proyección próximos 3 meses: " +
                      " · ".join(f"{p['mes']}: {f(p['total'])}" for p in prox) +
                      f" (tendencia {pv.get('tendencia')}).")
    else:
        pv_txt = pv.get("motivo", "")

    delta = ""
    if r.get("mes_prev"):
        try:
            p = (float(r["mes"]) - float(r["mes_prev"])) / abs(float(r["mes_prev"])) * 100
            delta = f"{'+' if p>=0 else ''}{p:.0f}% vs mes anterior"
        except Exception:
            pass

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>Informe mensual · Zigurat · {mes_nombre}</title>
<style>
  *{{box-sizing:border-box}} body{{font-family:'Segoe UI',system-ui,sans-serif;color:#1c1917;margin:0;padding:38px 46px;background:#fff}}
  .hd{{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #f97316;padding-bottom:14px;margin-bottom:22px}}
  .hd h1{{font-size:22px;margin:0;color:#9a3412}} .hd .s{{font-size:13px;color:#78716c;margin-top:4px}}
  .logo{{font-size:30px}}
  h2{{font-size:14px;text-transform:uppercase;letter-spacing:.5px;color:#9a3412;margin:24px 0 10px;border-bottom:1px solid #e7e5e4;padding-bottom:5px}}
  .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
  .k{{border:1px solid #e7e5e4;border-radius:9px;padding:12px 14px}}
  .k .l{{font-size:11px;color:#78716c}} .k .v{{font-size:20px;font-weight:700;margin-top:4px;color:#1c1917}}
  .k .d{{font-size:11px;color:#16a34a;margin-top:3px}}
  table{{width:100%;border-collapse:collapse;font-size:12.5px}}
  th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #eee}}
  th{{color:#78716c;font-size:10.5px;text-transform:uppercase;letter-spacing:.4px}}
  td.r,th.r{{text-align:right}} .muted{{color:#a8a29e}}
  ul{{margin:0;padding-left:18px}} li{{margin-bottom:9px;font-size:12.5px}}
  .cols{{display:grid;grid-template-columns:1fr 1fr;gap:26px}}
  .ft{{margin-top:28px;font-size:10.5px;color:#a8a29e;border-top:1px solid #e7e5e4;padding-top:10px}}
  @media print{{body{{padding:0}} .noprint{{display:none}}}}
  .btn{{background:#f97316;color:#fff;border:none;padding:10px 18px;border-radius:8px;font-size:13px;cursor:pointer;font-weight:600}}
</style></head><body>
<div class="noprint" style="text-align:right;margin-bottom:14px">
  <button class="btn" onclick="window.print()">🖨️ Guardar como PDF / Imprimir</button>
</div>
<div class="hd">
  <div><h1>Informe mensual de gestión</h1><div class="s">Zigurat Brewery · Vintage SPA · {mes_nombre}</div></div>
  <div class="logo">🍺</div>
</div>

<h2>Resumen ejecutivo</h2>
<div class="kpis">
  <div class="k"><div class="l">Ventas del mes</div><div class="v">{f(r.get('mes'))}</div><div class="d">{delta}</div></div>
  <div class="k"><div class="l">Ventas del año</div><div class="v">{f(r.get('ytd'))}</div></div>
  <div class="k"><div class="l">Ticket promedio</div><div class="v">{f(r.get('ticket'))}</div></div>
  <div class="k"><div class="l">Por cobrar</div><div class="v">{f(r.get('por_cobrar'))}</div><div class="d" style="color:#dc2626">{(str(int(r.get('vencido_n') or 0))+' facturas vencidas') if r.get('vencido_n') else ''}</div></div>
</div>
<p style="font-size:12.5px;color:#44403c;margin-top:14px">
  Se emitieron <b>{int(r.get('facturas_mes') or 0)}</b> facturas este mes.
  La cartera tiene <b>{int(r.get('clientes_total') or 0)}</b> clientes
  ({int(r.get('clientes_activos') or 0) if r.get('clientes_activos') is not None else '—'} activos).
  {('Saldo en banco: <b>'+f(r.get('saldo_banco'))+'</b>.') if r.get('saldo_banco') is not None else ''}
  {pv_txt}
</p>

<div class="cols">
  <div>
    <h2>Mejores clientes</h2>
    <table><tr><th>#</th><th>Cliente</th><th class="r">Venta real</th><th class="r">Facturas</th></tr>
    {filas_top(payload.get('top_clientes') or [])}</table>
  </div>
  <div>
    <h2>Deudas vencidas (+30 días)</h2>
    <table><tr><th>Cliente</th><th class="r">Atraso</th><th class="r">Deuda</th></tr>
    {filas_mor(payload.get('morosos') or [])}</table>
  </div>
</div>

<h2>Recomendaciones</h2>
<ul>{items_rec(payload.get('recomendaciones') or [])}</ul>

<div class="ft">Generado automáticamente por el Centro de Comando Zigurat el {hoy.strftime('%d/%m/%Y')}.
Cifras reales ajustadas por notas de crédito. Documento de gestión interna.</div>
<script>setTimeout(function(){{try{{window.print()}}catch(e){{}}}}, 600);</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silenciar log ruidoso
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            html_file = HERE / "dashboard_ui.html"
            try:
                self._send(200, html_file.read_text(encoding="utf-8"),
                           "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, f"<h1>Error cargando UI</h1><pre>{e}</pre>",
                           "text/html; charset=utf-8")
        elif path == "/api/data":
            try:
                payload = build_payload()
                self._send(200, json.dumps(payload, default=_json_default, ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "error": str(e)}))
        elif path == "/api/ping":
            self._send(200, json.dumps({"ok": True}))
        elif path == "/informe":
            try:
                payload = build_payload()
                self._send(200, build_informe_html(payload), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, f"<h1>Error generando informe</h1><pre>{e}</pre>",
                           "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"ok": False, "error": "no encontrado"}))

    def do_POST(self):
        if not origen_permitido(self.headers.get("Host"), self.headers.get("Origin")):
            self._send(403, json.dumps({"ok": False, "error": "origen no permitido"}))
            return
        path = self.path.split("?")[0]
        if path == "/api/ask":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b"{}"
                payload = json.loads(body or b"{}")
                q = (payload.get("q") or "").strip()
                model = (payload.get("model") or MODELO_CHAT_DEFAULT).strip()
                # Whitelist: el modelo viene del navegador; uno arbitrario
                # gastaria creditos en un modelo no elegido o fallaria feo.
                if model not in MODELOS_CHAT_PERMITIDOS:
                    model = MODELO_CHAT_DEFAULT
            except Exception:
                q = ""
                model = MODELO_CHAT_DEFAULT
            if not q:
                self._send(400, json.dumps({"ok": False, "error": "pregunta_vacia"}))
                return
            res = run_agent(q, model)
            self._send(200, json.dumps(res, default=_json_default, ensure_ascii=False))
        elif path == "/api/chat-reset":
            # "Limpiar" en la UI: descarta la sesion para partir una conversacion nueva.
            CHAT_SESSION["id"] = None
            self._send(200, json.dumps({"ok": True}))
        elif path == "/api/ejecutar-accion":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except Exception:
                self._send(400, json.dumps({"ok": False, "error": "JSON inválido"}))
                return
            try:
                from app.negocio import acciones
            except Exception as e:  # pragma: no cover
                self._send(500, json.dumps({"ok": False, "error": "módulo de acciones no disponible",
                                            "detalle": str(e)}))
                return
            tipo_accion = body.get("tipo_accion")
            params = body.get("params") or {}
            try:
                clean = acciones.validar(tipo_accion, params)
            except ValueError as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
                return
            try:
                conn = get_conn()
                try:
                    with conn:
                        with conn.cursor() as cur:
                            result = acciones.ejecutar(cur, tipo_accion, clean)
                finally:
                    conn.close()
            except ValueError as e:
                # p. ej. "El gasto N ya no existe" durante la ejecución
                self._send(400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
                return
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "error": "error al escribir en la base",
                                            "detalle": str(e)}, ensure_ascii=False))
                return
            self._send(200, json.dumps({"ok": True, **result},
                                       default=_json_default, ensure_ascii=False))
        else:
            self._send(404, json.dumps({"ok": False, "error": "no encontrado"}))


def main():
    url = f"http://localhost:{PORT}"
    print("=" * 60)
    print("  ZIGURAT - Centro de Comando")
    print("=" * 60)
    print(f"  Base de datos : {DB_CONFIG['dbname']} @ {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print(f"  Abriendo      : {url}")
    print("  Para cerrar   : Ctrl + C en esta ventana")
    print("=" * 60)
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando dashboard. Hasta pronto!")
        server.shutdown()


if __name__ == "__main__":
    main()
