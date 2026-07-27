#!/usr/bin/env python3
"""
wiki_update.py - Zigurat ERP
Genera fichas Markdown por cliente en wiki/clientes/ a partir de PostgreSQL.

Uso:
    python wiki_update.py --todos              # Todos los clientes
    python wiki_update.py --ruts 12345678-9    # Uno o más RUTs separados por coma
    python wiki_update.py --cliente "NOMBRE"   # Busca por razón social (parcial)
"""

import argparse
import io
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

from _console import force_utf8

force_utf8()

# Forzar salida UTF-8 en consola Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta la librería psycopg2.")
    print("Instala con: pip install psycopg2-binary")
    sys.exit(1)


# ─── Carga de variables de entorno desde .env ─────────────────────────────────
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


# ─── Configuración de conexión ────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


# ─── Rutas del proyecto ──────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
WIKI_DIR = BASE_DIR / "wiki"
CLIENTES_DIR = WIKI_DIR / "clientes"
CONCEPTOS_DIR = WIKI_DIR / "conceptos"
PRODUCTOS_DIR = CONCEPTOS_DIR / "productos"
INDICES_DIR = WIKI_DIR / "indices"
INDEX_PATH = WIKI_DIR / "index.md"
LOG_PATH = WIKI_DIR / "log.md"

# Capa "raw" del patrón Karpathy: fuente de verdad histórica (snapshots).
# Los JSON aquí son sobrescribibles desde código pero NUNCA se editan a mano.
# Commiteables a git para obtener `git diff` del estado del negocio entre ingestas.
RAW_DIR = BASE_DIR / "raw"
RAW_CLIENTES_DIR = RAW_DIR / "clientes"


# ─── Conexión ─────────────────────────────────────────────────────────────────

def conectar():
    """Establece conexión a PostgreSQL con encoding UTF-8."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        # Forzar encoding UTF-8 para que los caracteres con tilde se lean correctamente
        conn.set_client_encoding('UTF8')
        return conn
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL:")
        print(f"  {e}")
        print()
        print("Verifica que PostgreSQL esté corriendo y que los datos de")
        print("conexión en .env sean correctos.")
        sys.exit(1)


# ─── Utilidades ───────────────────────────────────────────────────────────────

def slugify(razon_social):
    """Convierte razón social a slug para nombre de archivo.

    Ejemplo: 'CERVECERÍA MARINA SPA' → 'cerveceria-marina-spa'
    """
    # Normalizar unicode: quitar acentos y caracteres especiales
    texto = unicodedata.normalize("NFKD", razon_social)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    # Minúsculas, reemplazar espacios y caracteres no alfanuméricos por guiones
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    # Limpiar guiones duplicados y en los extremos
    texto = re.sub(r"-+", "-", texto).strip("-")
    return texto


def fmt_monto(n):
    """Formatea un número como monto chileno.

    Ejemplo: 1234567 → '$1.234.567'
    """
    if n is None:
        return "$0"
    # Convertir a entero y formatear con separador de miles
    entero = int(round(n))
    signo = "-" if entero < 0 else ""
    entero = abs(entero)
    formateado = f"{entero:,}".replace(",", ".")
    return f"{signo}${formateado}"


def fmt_fecha(d):
    """Formatea una fecha como 'YYYY-MM-DD' o '—' si es None."""
    if d is None:
        return "—"
    if isinstance(d, str):
        return d
    return d.strftime("%Y-%m-%d")


# ─── Queries de datos de cliente ──────────────────────────────────────────────

def obtener_datos_cliente(cur, rut):
    """Ejecuta 6 queries y retorna un dict con toda la info del cliente."""

    # 1. Datos maestros
    cur.execute(
        "SELECT razon_social, estado, direccion, comuna "
        "FROM clientes WHERE rut_cliente = %s",
        (rut,)
    )
    row = cur.fetchone()
    if row is None:
        return None
    razon_social, estado, direccion, comuna = row

    # 2. Total vendido (facturas, excluyendo NC)
    cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(COALESCE(monto_total_ajustado, monto_total)), 0) "
        "FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61'",
        (rut,)
    )
    facturas_emitidas, total_vendido = cur.fetchone()

    # 3. Facturas pendientes (sin fecha_pago)
    cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(COALESCE(monto_total_ajustado, monto_total)), 0) "
        "FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61' AND fecha_pago IS NULL",
        (rut,)
    )
    facturas_pendientes, deuda_pendiente = cur.fetchone()

    # 4. Promedio días pago y último pago
    cur.execute(
        "SELECT AVG(dias_pago), MAX(fecha_pago) "
        "FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61' AND fecha_pago IS NOT NULL",
        (rut,)
    )
    promedio_dias_pago, ultimo_pago = cur.fetchone()

    # 5. Top 3 productos más comprados
    cur.execute(
        "SELECT p.nombre_producto, SUM(p.cantidad) "
        "FROM productos p "
        "JOIN ventas v ON v.folio::text = p.folio::text AND v.tipo_documento = p.tipo_documento "
        "WHERE v.rut_cliente = %s AND v.tipo_documento != '61' "
        # Excluir lineas que no son producto (ver CLAUDE.md): Logistica, envase
        # PET y carga de CO2
        "AND p.nombre_producto NOT ILIKE '%%logist%%' "
        "AND p.nombre_producto !~* '^(barril(es)?\\s+)?pet\\y' "
        "AND p.nombre_producto NOT ILIKE '%%co2%%' "
        "GROUP BY p.nombre_producto "
        "ORDER BY SUM(cantidad) DESC LIMIT 3",
        (rut,)
    )
    top_productos = [
        {"nombre": row[0], "cantidad": float(row[1]) if row[1] else 0}
        for row in cur.fetchall()
    ]

    # 6. Cliente desde (primera factura)
    cur.execute(
        "SELECT MIN(fecha) FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61'",
        (rut,)
    )
    cliente_desde = cur.fetchone()[0]

    return {
        "rut": rut,
        "razon_social": razon_social,
        "estado": estado,
        "direccion": direccion,
        "comuna": comuna,
        "facturas_emitidas": facturas_emitidas or 0,
        "total_vendido": total_vendido or 0,
        "facturas_pendientes": facturas_pendientes or 0,
        "deuda_pendiente": deuda_pendiente or 0,
        "promedio_dias_pago": round(promedio_dias_pago) if promedio_dias_pago else None,
        "ultimo_pago": ultimo_pago,
        "top_productos": top_productos,
        "cliente_desde": cliente_desde,
    }


def obtener_ruts_todos(cur):
    """Retorna lista de todos los RUTs de la tabla clientes."""
    cur.execute("SELECT rut_cliente FROM clientes ORDER BY razon_social")
    return [row[0] for row in cur.fetchall()]


# ─── Snapshots raw/ (capa inmutable del patrón Karpathy) ──────────────────────

def _serializar_datos(datos):
    """Convierte dict de datos a JSON-safe (date/datetime → ISO, Decimal → float)."""
    from decimal import Decimal

    def conv(v):
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, list):
            return [
                {k: conv(vv) for k, vv in item.items()} if isinstance(item, dict) else conv(item)
                for item in v
            ]
        return v
    return {k: conv(v) for k, v in datos.items()}


def cargar_snapshot(rut):
    """Lee snapshot previo de raw/clientes/{rut}.json. Retorna dict o None."""
    path = RAW_CLIENTES_DIR / f"{rut}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def guardar_snapshot(datos):
    """Escribe snapshot JSON en raw/clientes/{rut}.json (sobrescribe)."""
    RAW_CLIENTES_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_CLIENTES_DIR / f"{datos['rut']}.json"
    serializado = _serializar_datos(datos)
    serializado["_snapshot_fecha"] = date.today().isoformat()
    path.write_text(
        json.dumps(serializado, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def detectar_cambios_snapshot(previo, datos):
    """Compara snapshot anterior con datos actuales y retorna eventos notables.

    Umbrales:
      - Cambio de estado (activo/incobrable): siempre
      - Facturas nuevas: siempre que diff > 0
      - Caída en total_vendido: diff > $1 (debería ser monotónico)
      - Aumento de deuda_pendiente: diff > $100.000
    """
    if previo is None:
        return []
    hoy = date.today().isoformat()
    eventos = []

    # Cambio de estado
    estado_prev = previo.get("estado") or "activo"
    estado_act = datos.get("estado") or "activo"
    if estado_prev != estado_act:
        eventos.append(
            f"- {hoy}: 🔄 Cambio de estado: '{estado_prev}' → '{estado_act}'."
        )

    # Nuevas facturas desde el snapshot previo
    fact_prev = previo.get("facturas_emitidas", 0) or 0
    fact_act = datos.get("facturas_emitidas", 0) or 0
    diff_fact = fact_act - fact_prev
    if diff_fact > 0:
        fecha_ref = previo.get("_snapshot_fecha", "snapshot previo")
        eventos.append(
            f"- {hoy}: 📄 {diff_fact} factura(s) nueva(s) desde {fecha_ref}."
        )

    # Caída en ventas totales (posible NC o ajuste manual)
    tot_prev = float(previo.get("total_vendido", 0) or 0)
    tot_act = float(datos.get("total_vendido", 0) or 0)
    if tot_act < tot_prev - 1:
        eventos.append(
            f"- {hoy}: ⚠️ Total vendido bajó en {fmt_monto(tot_prev - tot_act)} "
            f"(posible NC o ajuste)."
        )

    # Deuda pendiente creció significativamente
    deuda_prev = float(previo.get("deuda_pendiente", 0) or 0)
    deuda_act = float(datos.get("deuda_pendiente", 0) or 0)
    if deuda_act - deuda_prev > 100000:
        eventos.append(
            f"- {hoy}: 📈 Deuda pendiente aumentó en "
            f"{fmt_monto(deuda_act - deuda_prev)}."
        )

    return eventos


# ─── Generación de fichas Markdown ───────────────────────────────────────────

def detectar_eventos(cur, datos):
    """Detecta eventos notables para un cliente y retorna lista de strings.

    Cada string tiene formato: '- YYYY-MM-DD: [emoji] Descripción.'
    Eventos detectados:
      1. Facturas vencidas (>30 días sin pago)
      2. Pago múltiple (>1 factura pagada el mismo día, últimos 7 días)
      3. Cliente inactivo (>60 días sin factura nueva)
    """
    hoy = date.today()
    eventos = []
    rut = datos["rut"]

    # 1. Facturas vencidas (>30 días sin pago)
    cur.execute(
        "SELECT folio, COALESCE(monto_total_ajustado, monto_total) "
        "FROM ventas "
        "WHERE rut_cliente = %s AND tipo_documento != '61' "
        "  AND fecha_pago IS NULL "
        "  AND fecha < CURRENT_DATE - INTERVAL '30 days'",
        (rut,)
    )
    vencidas = cur.fetchall()
    if vencidas:
        folios = ", ".join(f"#{row[0]}" for row in vencidas)
        total = sum(row[1] for row in vencidas if row[1])
        eventos.append(
            f"- {hoy.isoformat()}: ⚠️ {len(vencidas)} factura(s) vencida(s) "
            f"(>30 días): {folios} por {fmt_monto(total)}."
        )

    # 2. Pago múltiple (>1 factura pagada el mismo día, últimos 7 días)
    cur.execute(
        "SELECT fecha_pago, COUNT(*) "
        "FROM ventas "
        "WHERE rut_cliente = %s AND tipo_documento != '61' "
        "  AND fecha_pago IS NOT NULL "
        "  AND fecha_pago >= CURRENT_DATE - INTERVAL '7 days' "
        "GROUP BY fecha_pago "
        "HAVING COUNT(*) > 1",
        (rut,)
    )
    pagos_multiples = cur.fetchall()
    for fecha_pago, cantidad in pagos_multiples:
        eventos.append(
            f"- {hoy.isoformat()}: 💰 Pagó {cantidad} facturas juntas "
            f"el {fmt_fecha(fecha_pago)}."
        )

    # 3. Cliente inactivo (>60 días sin factura nueva)
    cur.execute(
        "SELECT MAX(fecha) FROM ventas "
        "WHERE rut_cliente = %s AND tipo_documento != '61'",
        (rut,)
    )
    row = cur.fetchone()
    ultima_factura = row[0] if row else None
    if ultima_factura and datos.get("estado") != "incobrable":
        dias_inactivo = (hoy - ultima_factura).days
        if dias_inactivo > 60:
            eventos.append(
                f"- {hoy.isoformat()}: ⚠️ Cliente inactivo — {dias_inactivo} días "
                f"sin nueva factura (última: {fmt_fecha(ultima_factura)})."
            )

    return eventos


def generar_patron(datos):
    """Genera bullets de patrón de comportamiento del cliente."""
    lineas = []

    # Desde cuándo es cliente
    if datos["cliente_desde"]:
        lineas.append(f"- Cliente desde **{fmt_fecha(datos['cliente_desde'])}**")

    # Frecuencia de compra
    if datos["cliente_desde"] and datos["facturas_emitidas"] > 1:
        dias_activo = (date.today() - datos["cliente_desde"]).days
        if dias_activo > 0:
            frecuencia = dias_activo / datos["facturas_emitidas"]
            lineas.append(f"- Frecuencia de compra: ~1 factura cada **{int(round(frecuencia))} días**")
    elif datos["facturas_emitidas"] == 1:
        lineas.append("- Solo 1 compra registrada")

    # Velocidad de pago
    dias = datos["promedio_dias_pago"]
    if dias is not None:
        if dias <= 15:
            velocidad = "rápido"
        elif dias <= 30:
            velocidad = "bueno"
        elif dias <= 45:
            velocidad = "normal"
        else:
            velocidad = "lento"
        lineas.append(f"- Comportamiento de pago: **{velocidad}** ({dias} días promedio)")

    # Producto principal
    if datos["top_productos"]:
        prod = datos["top_productos"][0]
        lineas.append(f"- Producto principal: **{prod['nombre']}**")

    # Estado incobrable
    if datos["estado"] == "incobrable":
        lineas.append("- ⚠ **Cliente marcado como incobrable**")

    return "\n".join(lineas) if lineas else "- Sin datos suficientes para generar patrón"


# ─── Relacionados (Mejora 2: wikilinks entre clientes) ───────────────────────

def obtener_relacionados(cur, datos):
    """Retorna lista de hasta 5 clientes que comparten el producto principal.

    Usa el top_producto ya calculado en `datos` para evitar una query extra.
    """
    if not datos.get("top_productos"):
        return []
    producto_principal = datos["top_productos"][0]["nombre"]
    cur.execute(
        "SELECT c.razon_social, c.rut_cliente, SUM(p.cantidad) AS total "
        "FROM productos p "
        "JOIN ventas v ON v.folio::text = p.folio::text "
        "  AND v.tipo_documento = p.tipo_documento "
        "JOIN clientes c ON c.rut_cliente = v.rut_cliente "
        "WHERE p.nombre_producto = %s "
        "  AND v.rut_cliente != %s "
        "  AND v.tipo_documento != '61' "
        "GROUP BY c.razon_social, c.rut_cliente "
        "ORDER BY total DESC LIMIT 5",
        (producto_principal, datos["rut"]),
    )
    return [
        {"razon_social": r[0], "rut": r[1], "producto": producto_principal}
        for r in cur.fetchall()
    ]


# ─── Inconsistencias (Mejora 3: contra-argumentos vs cámara de eco) ──────────

def detectar_inconsistencias(cur, datos, notas_existentes=""):
    """Detecta contradicciones entre datos duros (BD) y observaciones blandas.

    Retorna lista de strings (una por inconsistencia). Vacía si no hay.
    """
    hoy = date.today()
    rut = datos["rut"]
    resultados = []

    # 1. Marcado incobrable pero con facturas recientes (últimos 60 días)
    if datos.get("estado") == "incobrable":
        cur.execute(
            "SELECT COUNT(*) FROM ventas "
            "WHERE rut_cliente = %s AND tipo_documento != '61' "
            "  AND fecha > CURRENT_DATE - INTERVAL '60 days'",
            (rut,),
        )
        recientes = cur.fetchone()[0]
        if recientes and recientes > 0:
            resultados.append(
                f"- Estado en BD = **incobrable**, pero hay {recientes} "
                f"factura(s) emitida(s) en los últimos 60 días."
            )

    # 2. Notas mencionan 'incobrable' pero BD dice activo
    if notas_existentes and "incobrable" in notas_existentes.lower():
        if datos.get("estado") != "incobrable":
            resultados.append(
                "- Notas del agente mencionan 'incobrable' pero estado en BD "
                "no es 'incobrable'. Revisar manualmente."
            )

    # 3. Top producto (global) diferente al producto de las últimas 3 facturas
    if datos.get("top_productos"):
        top_global = datos["top_productos"][0]["nombre"]
        cur.execute(
            "SELECT p.nombre_producto, SUM(p.cantidad) "
            "FROM productos p "
            "JOIN ventas v ON v.folio::text = p.folio::text "
            "  AND v.tipo_documento = p.tipo_documento "
            "WHERE v.rut_cliente = %s AND v.tipo_documento != '61' "
            "  AND v.folio IN ( "
            "    SELECT folio FROM ventas "
            "    WHERE rut_cliente = %s AND tipo_documento != '61' "
            "    ORDER BY fecha DESC LIMIT 3 "
            "  ) "
            "  AND p.nombre_producto NOT ILIKE '%%logist%%' "
            "  AND p.nombre_producto !~* '^(barril(es)?\\s+)?pet\\y' "
            "  AND p.nombre_producto NOT ILIKE '%%co2%%' "
            "GROUP BY p.nombre_producto "
            "ORDER BY SUM(p.cantidad) DESC LIMIT 1",
            (rut, rut),
        )
        row = cur.fetchone()
        if row and row[0] and row[0] != top_global:
            resultados.append(
                f"- Producto principal histórico = **{top_global}**, pero "
                f"en las últimas 3 facturas predomina **{row[0]}**. "
                f"Posible cambio de patrón de compra."
            )

    # 4. Sin pagos nunca y deuda pendiente > 0 y cliente con >6 meses de antigüedad
    if (
        datos.get("deuda_pendiente", 0)
        and datos["deuda_pendiente"] > 0
        and datos.get("ultimo_pago") is None
        and datos.get("cliente_desde")
    ):
        dias_antiguo = (hoy - datos["cliente_desde"]).days
        if dias_antiguo > 180:
            resultados.append(
                f"- Cliente desde hace {dias_antiguo} días (>{180}), sin "
                f"ningún pago registrado y con deuda de "
                f"{fmt_monto(datos['deuda_pendiente'])}. Revisar si falta "
                f"conciliar o es cliente moroso crónico."
            )

    return resultados


def generar_ficha(datos, relacionados=None, inconsistencias=None):
    """Genera contenido Markdown completo para la ficha de un cliente."""
    hoy = date.today().isoformat()

    # YAML frontmatter
    lineas = [
        "---",
        f"rut: {datos['rut']}",
        f"razon_social: \"{datos['razon_social']}\"",
        f"estado: {datos['estado'] or 'activo'}",
        f"ultima_actualizacion: {hoy}",
        "---",
        "",
        f"# {datos['razon_social']}",
        "",
        "## Métricas clave",
        "",
        "| Indicador | Valor |",
        "| --- | --- |",
        f"| Total vendido | {fmt_monto(datos['total_vendido'])} |",
        f"| Facturas emitidas | {datos['facturas_emitidas']} |",
        f"| Facturas pendientes | {datos['facturas_pendientes']} ({fmt_monto(datos['deuda_pendiente'])}) |",
        f"| Promedio días de pago | {datos['promedio_dias_pago'] or '—'} |",
        f"| Último pago | {fmt_fecha(datos['ultimo_pago'])} |",
        "",
    ]

    # Estado de cuenta
    lineas.append("## Estado de cuenta")
    lineas.append("")
    if datos["facturas_pendientes"] > 0:
        lineas.append(f"- {datos['facturas_pendientes']} factura(s) pendiente(s) por {fmt_monto(datos['deuda_pendiente'])}")
        if datos["ultimo_pago"]:
            lineas.append(f"- Último pago registrado: {fmt_fecha(datos['ultimo_pago'])}")
        else:
            lineas.append("- Sin pagos registrados")
    else:
        lineas.append("- Sin facturas pendientes de pago")
    lineas.append("")

    # Patrón de comportamiento
    lineas.append("## Patrón de comportamiento")
    lineas.append("")
    lineas.append(generar_patron(datos))
    lineas.append("")

    # Relacionados (Mejora 2: wikilinks entre clientes con mismo producto)
    lineas.append("## Relacionados")
    lineas.append("")
    if relacionados:
        prod = relacionados[0]["producto"]
        lineas.append(f"Clientes que también compran **{prod}**:")
        lineas.append("")
        for r in relacionados:
            lineas.append(f"- [[{r['razon_social']}]] ({r['rut']})")
    else:
        lineas.append("- Sin clientes relacionados detectados")
    lineas.append("")

    # Inconsistencias (Mejora 3: contra-argumentos entre SQL y notas)
    lineas.append("## Inconsistencias")
    lineas.append("")
    if inconsistencias:
        lineas.extend(inconsistencias)
    else:
        lineas.append("- Ninguna detectada")
    lineas.append("")

    # Notas del agente (sección vacía para llenado posterior)
    lineas.append("## Notas del agente")
    lineas.append("")

    return "\n".join(lineas)


def escribir_ficha(datos, cur=None):
    """Escribe ficha .md del cliente. Preserva 'Notas del agente' si ya existe.

    Si se pasa cur (cursor de BD), detecta eventos notables y los agrega
    como notas del agente, evitando duplicados.
    """
    slug = slugify(datos["razon_social"])
    filepath = CLIENTES_DIR / f"{slug}.md"

    # Si ya existe, preservar la sección 'Notas del agente'
    notas_existentes = ""
    if filepath.exists():
        contenido_actual = filepath.read_text(encoding="utf-8")
        # Buscar todo después del heading '## Notas del agente'
        match = re.search(r"## Notas del agente\n(.*)", contenido_actual, re.DOTALL)
        if match:
            notas_existentes = match.group(1)

    # Cargar snapshot previo (capa raw/) para detectar cambios temporales
    snapshot_previo = cargar_snapshot(datos["rut"])

    # Calcular relacionados e inconsistencias (Mejoras 2 y 3)
    relacionados = []
    inconsistencias = []
    if cur is not None:
        relacionados = obtener_relacionados(cur, datos)
        inconsistencias = detectar_inconsistencias(cur, datos, notas_existentes)

    # Detectar eventos notables si hay cursor disponible
    if cur is not None:
        nuevos_eventos = detectar_eventos(cur, datos)
        # Agregar eventos derivados de comparar con snapshot anterior
        nuevos_eventos.extend(detectar_cambios_snapshot(snapshot_previo, datos))
        if nuevos_eventos:
            # Filtrar duplicados: solo agregar eventos cuyo texto no exista ya
            eventos_a_agregar = [
                ev for ev in nuevos_eventos
                if ev.strip() not in notas_existentes
            ]
            if eventos_a_agregar:
                # Prepend nuevos eventos (más recientes arriba)
                bloque_nuevo = "\n".join(eventos_a_agregar)
                if notas_existentes.strip():
                    notas_existentes = "\n" + bloque_nuevo + "\n" + notas_existentes.lstrip("\n")
                else:
                    notas_existentes = "\n" + bloque_nuevo + "\n"

    # Generar ficha nueva
    contenido = generar_ficha(
        datos, relacionados=relacionados, inconsistencias=inconsistencias
    )

    # Reemplazar la sección de notas con la preservada (si había)
    if notas_existentes.strip():
        contenido = contenido.rstrip("\n") + "\n"
        # Reemplazar desde '## Notas del agente' en adelante
        contenido = re.sub(
            r"## Notas del agente\n.*",
            f"## Notas del agente\n{notas_existentes}",
            contenido,
            flags=re.DOTALL,
        )

    # Asegurar que el directorio existe
    CLIENTES_DIR.mkdir(parents=True, exist_ok=True)
    filepath.write_text(contenido, encoding="utf-8")

    # Guardar snapshot en raw/ (fuente histórica inmutable desde código)
    guardar_snapshot(datos)

    return str(filepath), slug


# ─── index.md y log.md ──────────────────────────────────────────────────────

def _tabla_clientes(rows, col_deuda="Deuda pendiente"):
    """Convierte lista de rows (razon_social, rut, deuda) en tabla Markdown."""
    hoy = date.today().isoformat()
    lineas = [
        f"| Cliente | RUT | {col_deuda} | Última actualización |",
        "| --- | --- | --- | --- |",
    ]
    for e in rows:
        lineas.append(
            f"| [[{e['razon_social']}]] | {e['rut']} | {fmt_monto(e['deuda'])} | {hoy} |"
        )
    return lineas


def actualizar_index(cur):
    """Regenera wiki/index.md (resumen) + sub-índices en wiki/indices/ (Mejora 4)."""
    hoy = date.today().isoformat()

    # Query: todos los clientes con su deuda pendiente en una sola consulta
    cur.execute(
        "SELECT c.razon_social, c.rut_cliente, c.estado, "
        "  COALESCE(SUM(CASE WHEN v.tipo_documento != '61' AND v.fecha_pago IS NULL "
        "    THEN COALESCE(v.monto_total_ajustado, v.monto_total) ELSE 0 END), 0) AS deuda "
        "FROM clientes c "
        "LEFT JOIN ventas v ON v.rut_cliente = c.rut_cliente "
        "GROUP BY c.rut_cliente, c.razon_social, c.estado "
        "ORDER BY c.razon_social"
    )
    rows = cur.fetchall()

    activos = []
    incobrables = []
    morosos = []
    for razon_social, rut, estado, deuda in rows:
        entry = {"razon_social": razon_social, "rut": rut, "deuda": deuda}
        if estado == "incobrable":
            incobrables.append(entry)
        else:
            activos.append(entry)
            if deuda and deuda > 0:
                morosos.append(entry)

    total = len(activos) + len(incobrables)

    # Asegurar directorios
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    INDICES_DIR.mkdir(parents=True, exist_ok=True)

    # Sub-índice: activos
    sub_activos = [
        "# Clientes activos",
        "",
        f"Actualizado: {hoy} | {len(activos)} clientes",
        "",
    ] + _tabla_clientes(activos) + [""]
    (INDICES_DIR / "activos.md").write_text("\n".join(sub_activos), encoding="utf-8")

    # Sub-índice: morosos (activos con deuda > 0)
    morosos.sort(key=lambda e: -e["deuda"])
    sub_morosos = [
        "# Clientes morosos",
        "",
        f"Actualizado: {hoy} | {len(morosos)} clientes con deuda pendiente",
        "",
    ] + (_tabla_clientes(morosos) if morosos else ["- Ninguno"]) + [""]
    (INDICES_DIR / "morosos.md").write_text("\n".join(sub_morosos), encoding="utf-8")

    # Sub-índice: incobrables
    sub_inc = [
        "# Clientes incobrables",
        "",
        f"Actualizado: {hoy} | {len(incobrables)} clientes",
        "",
    ] + (_tabla_clientes(incobrables, col_deuda="Deuda histórica") if incobrables else ["- Ninguno"]) + [""]
    (INDICES_DIR / "incobrables.md").write_text("\n".join(sub_inc), encoding="utf-8")

    # Índice maestro: corto, enlaza a sub-índices y conceptos
    lineas = [
        "# Wiki Zigurat — Índice de Clientes",
        "",
        f"Actualizado: {hoy} | **{total}** clientes "
        f"({len(activos)} activos, {len(morosos)} morosos, {len(incobrables)} incobrables)",
        "",
        "## Sub-índices por estado",
        "",
        f"- [[indices/activos|Clientes activos]] ({len(activos)})",
        f"- [[indices/morosos|Clientes morosos]] ({len(morosos)})",
        f"- [[indices/incobrables|Clientes incobrables]] ({len(incobrables)})",
        "",
        "## Conceptos",
        "",
        "- [[conceptos/clientes-top|Top 10 por ventas]]",
        "- [[conceptos/clientes-morosos|Morosos (>30 días)]]",
        "- [[conceptos/clientes-inactivos|Inactivos (>60 días)]]",
        "- Ver también `wiki/conceptos/productos/` — un concepto por producto principal",
        "",
    ]
    INDEX_PATH.write_text("\n".join(lineas), encoding="utf-8")
    return total


def actualizar_conceptos(cur):
    """Regenera páginas de conceptos en wiki/conceptos/ (Mejora 2).

    Genera:
      - clientes-top.md       Top 10 por ventas totales
      - clientes-morosos.md   Con facturas vencidas >30 días
      - clientes-inactivos.md Sin compras en los últimos 60 días
      - productos/<slug>.md   Un archivo por producto principal
    """
    hoy = date.today().isoformat()
    CONCEPTOS_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCTOS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Top 10 por ventas
    cur.execute(
        "SELECT c.razon_social, v.rut_cliente, "
        "  SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS total "
        "FROM ventas v JOIN clientes c ON c.rut_cliente = v.rut_cliente "
        "WHERE v.tipo_documento != '61' "
        "GROUP BY v.rut_cliente, c.razon_social "
        "ORDER BY total DESC LIMIT 10"
    )
    top = cur.fetchall()
    lineas = [
        "# Top 10 clientes por ventas",
        "",
        f"Actualizado: {hoy}",
        "",
        "| # | Cliente | RUT | Total vendido |",
        "| --- | --- | --- | --- |",
    ]
    for i, (razon, rut, total) in enumerate(top, 1):
        lineas.append(f"| {i} | [[{razon}]] | {rut} | {fmt_monto(total)} |")
    lineas.append("")
    (CONCEPTOS_DIR / "clientes-top.md").write_text("\n".join(lineas), encoding="utf-8")

    # 2. Morosos (facturas vencidas >30 días)
    cur.execute(
        "SELECT c.razon_social, v.rut_cliente, "
        "  COUNT(*) AS vencidas, "
        "  SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS deuda "
        "FROM ventas v JOIN clientes c ON c.rut_cliente = v.rut_cliente "
        "WHERE v.tipo_documento != '61' AND v.fecha_pago IS NULL "
        "  AND v.fecha < CURRENT_DATE - INTERVAL '30 days' "
        "GROUP BY v.rut_cliente, c.razon_social "
        "ORDER BY deuda DESC"
    )
    morosos = cur.fetchall()
    lineas = [
        "# Clientes morosos (>30 días vencidos)",
        "",
        f"Actualizado: {hoy} | {len(morosos)} clientes con deuda vencida",
        "",
    ]
    if morosos:
        lineas.append("| Cliente | RUT | Facturas vencidas | Deuda |")
        lineas.append("| --- | --- | --- | --- |")
        for razon, rut, nv, deuda in morosos:
            lineas.append(f"| [[{razon}]] | {rut} | {nv} | {fmt_monto(deuda)} |")
    else:
        lineas.append("- Sin morosos actualmente")
    lineas.append("")
    (CONCEPTOS_DIR / "clientes-morosos.md").write_text("\n".join(lineas), encoding="utf-8")

    # 3. Inactivos (>60 días sin factura nueva, excluyendo incobrables)
    cur.execute(
        "SELECT c.razon_social, v.rut_cliente, MAX(v.fecha) AS ultima "
        "FROM ventas v JOIN clientes c ON c.rut_cliente = v.rut_cliente "
        "WHERE v.tipo_documento != '61' "
        "  AND (c.estado IS NULL OR c.estado != 'incobrable') "
        "GROUP BY v.rut_cliente, c.razon_social "
        "HAVING MAX(v.fecha) < CURRENT_DATE - INTERVAL '60 days' "
        "ORDER BY ultima ASC"
    )
    inactivos = cur.fetchall()
    lineas = [
        "# Clientes inactivos (>60 días sin compra)",
        "",
        f"Actualizado: {hoy} | {len(inactivos)} clientes inactivos",
        "",
    ]
    if inactivos:
        lineas.append("| Cliente | RUT | Última factura |")
        lineas.append("| --- | --- | --- |")
        for razon, rut, ultima in inactivos:
            lineas.append(f"| [[{razon}]] | {rut} | {fmt_fecha(ultima)} |")
    else:
        lineas.append("- Sin inactivos")
    lineas.append("")
    (CONCEPTOS_DIR / "clientes-inactivos.md").write_text("\n".join(lineas), encoding="utf-8")

    # 4. Productos: un archivo por producto con top 10 clientes que lo compran
    cur.execute(
        "SELECT p.nombre_producto, SUM(p.cantidad) AS cant "
        "FROM productos p "
        "JOIN ventas v ON v.folio::text = p.folio::text "
        "  AND v.tipo_documento = p.tipo_documento "
        "WHERE v.tipo_documento != '61' "
        # Excluir lineas que no son producto (ver CLAUDE.md). Sin parametros %s:
        # aqui el % va simple, no doblado.
        "AND p.nombre_producto NOT ILIKE '%logist%' "
        "AND p.nombre_producto !~* '^(barril(es)?\\s+)?pet\\y' "
        "AND p.nombre_producto NOT ILIKE '%co2%' "
        "GROUP BY p.nombre_producto "
        "ORDER BY cant DESC LIMIT 15"
    )
    productos = [r[0] for r in cur.fetchall()]

    # Limpiar productos viejos (regeneramos desde cero)
    for f in PRODUCTOS_DIR.glob("*.md"):
        f.unlink()

    for nombre in productos:
        cur.execute(
            "SELECT c.razon_social, v.rut_cliente, SUM(p.cantidad) AS cant "
            "FROM productos p "
            "JOIN ventas v ON v.folio::text = p.folio::text "
            "  AND v.tipo_documento = p.tipo_documento "
            "JOIN clientes c ON c.rut_cliente = v.rut_cliente "
            "WHERE p.nombre_producto = %s AND v.tipo_documento != '61' "
            "GROUP BY c.razon_social, v.rut_cliente "
            "ORDER BY cant DESC LIMIT 10",
            (nombre,),
        )
        compradores = cur.fetchall()
        slug = slugify(nombre) or "producto"
        lineas = [
            f"# Producto: {nombre}",
            "",
            f"Actualizado: {hoy}",
            "",
            "## Clientes que lo compran",
            "",
            "| Cliente | RUT | Cantidad total |",
            "| --- | --- | --- |",
        ]
        for razon, rut, cant in compradores:
            cant_fmt = f"{float(cant):.0f}" if cant else "0"
            lineas.append(f"| [[{razon}]] | {rut} | {cant_fmt} |")
        lineas.append("")
        (PRODUCTOS_DIR / f"{slug}.md").write_text("\n".join(lineas), encoding="utf-8")

    return {
        "top": len(top),
        "morosos": len(morosos),
        "inactivos": len(inactivos),
        "productos": len(productos),
    }


def actualizar_log(actualizados, origen):
    """Agrega entrada al log de operaciones wiki/log.md.

    actualizados: lista de dicts con 'razon_social'
    origen: etiqueta de origen (ej: 'test', 'sync-facturas')
    """
    hoy = date.today().isoformat()
    hora = datetime.now().strftime("%H:%M")
    origen = origen or "manual"

    # Nombres de clientes actualizados
    nombres = [d["razon_social"] for d in actualizados]
    n = len(nombres)
    if n == 0:
        return

    # Si son más de 5, mostrar solo los primeros 5
    if n > 5:
        lista_texto = ", ".join(nombres[:5]) + f" y {n - 5} más"
    else:
        lista_texto = ", ".join(nombres)

    entrada = f"- **{origen}** ({hora}): Actualizadas {n} ficha(s): {lista_texto}"

    # Usar constante LOG_PATH para consistencia con el resto del módulo
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Leer contenido existente o crear header
    if LOG_PATH.exists():
        contenido = LOG_PATH.read_text(encoding="utf-8")
    else:
        contenido = "# Wiki Zigurat — Log de Operaciones\n"

    # Buscar si ya existe la sección del día
    heading_dia = f"## {hoy}"
    if heading_dia in contenido:
        # Agregar la entrada después del heading del día
        contenido = contenido.replace(
            heading_dia + "\n",
            heading_dia + "\n" + entrada + "\n",
        )
    else:
        # Agregar nueva sección al final
        contenido = contenido.rstrip("\n") + "\n\n" + heading_dia + "\n" + entrada + "\n"

    LOG_PATH.write_text(contenido, encoding="utf-8")


# ─── Argumentos CLI ───────────────────────────────────────────────────────────

def parse_args():
    """Parsea argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Genera fichas wiki de clientes desde PostgreSQL"
    )

    # Grupo mutuamente exclusivo: selección de clientes
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument(
        "--todos", action="store_true",
        help="Procesar todos los clientes"
    )
    grupo.add_argument(
        "--ruts", type=str,
        help="RUTs separados por coma (ej: 12345678-9,98765432-1)"
    )
    grupo.add_argument(
        "--cliente", type=str,
        help="Buscar por razón social (coincidencia parcial)"
    )

    parser.add_argument(
        "--origen", type=str, default=None,
        help="Etiqueta de origen para logging"
    )

    return parser.parse_args()


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_args()

    print("=" * 60)
    print("  WIKI UPDATE — Zigurat ERP")
    print("=" * 60)

    # Determinar modo de ejecución
    if args.todos:
        modo = "todos los clientes"
    elif args.ruts:
        modo = f"RUTs: {args.ruts}"
    elif args.cliente:
        modo = f"busqueda: '{args.cliente}'"
    print(f"  Modo: {modo}")
    if args.origen:
        print(f"  Origen: {args.origen}")
    print()

    # Conectar a la base de datos
    conn = conectar()
    cur = conn.cursor()
    print("[OK] Conectado a PostgreSQL")

    # Obtener lista de RUTs según el modo
    if args.todos:
        ruts = obtener_ruts_todos(cur)
    elif args.ruts:
        ruts = [r.strip() for r in args.ruts.split(",")]
    elif args.cliente:
        # Buscar por razón social parcial (case-insensitive)
        cur.execute(
            "SELECT rut_cliente FROM clientes "
            "WHERE UPPER(razon_social) LIKE UPPER(%s) "
            "ORDER BY razon_social",
            (f"%{args.cliente}%",)
        )
        ruts = [row[0] for row in cur.fetchall()]
        if not ruts:
            print(f"  No se encontraron clientes con '{args.cliente}'")
            cur.close()
            conn.close()
            sys.exit(0)

    print(f"  Clientes a procesar: {len(ruts)}")
    print("-" * 60)

    # Procesar cada cliente: generar ficha Markdown
    actualizados = []
    errores = 0

    for rut in ruts:
        datos = obtener_datos_cliente(cur, rut)
        if datos is None:
            print(f"  [{rut}] — no encontrado en tabla clientes")
            errores += 1
            continue

        # Escribir ficha .md (con detección de eventos si hay cursor)
        filepath, slug = escribir_ficha(datos, cur=cur)
        actualizados.append(datos)

        # Imprimir resumen del cliente
        estado_tag = f" [{datos['estado']}]" if datos['estado'] else ""
        pendiente_tag = f" | Pendiente: {fmt_monto(datos['deuda_pendiente'])}" if datos['facturas_pendientes'] > 0 else ""
        print(
            f"  ✓ {datos['razon_social']}{estado_tag} | "
            f"{datos['facturas_emitidas']} fact. | "
            f"Total: {fmt_monto(datos['total_vendido'])}"
            f"{pendiente_tag}"
        )

    print("-" * 60)
    print(f"  Fichas generadas: {len(actualizados)}")
    if errores > 0:
        print(f"  Errores: {errores}")

    # Actualizar index.md + sub-índices
    total_index = actualizar_index(cur)
    print(f"  [OK] index.md + sub-índices actualizados ({total_index} clientes)")

    # Actualizar páginas de conceptos (Mejora 2)
    stats = actualizar_conceptos(cur)
    print(
        f"  [OK] conceptos actualizados: top={stats['top']}, "
        f"morosos={stats['morosos']}, inactivos={stats['inactivos']}, "
        f"productos={stats['productos']}"
    )

    # Actualizar log.md
    if actualizados:
        actualizar_log(actualizados, args.origen)
        print(f"  [OK] log.md actualizado")

    print()
    cur.close()
    conn.close()
