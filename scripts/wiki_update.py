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
import os
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

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
INDEX_PATH = WIKI_DIR / "index.md"
LOG_PATH = BASE_DIR / "logs" / "wiki_update.log"


# ─── Conexión ─────────────────────────────────────────────────────────────────

def conectar():
    """Establece conexión a PostgreSQL."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
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


# ─── Argumentos CLI ───────────────────────────────────────────────────────────

# ─── Generación de fichas Markdown ───────────────────────────────────────────

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


def generar_ficha(datos):
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

    # Notas del agente (sección vacía para llenado posterior)
    lineas.append("## Notas del agente")
    lineas.append("")

    return "\n".join(lineas)


def escribir_ficha(datos):
    """Escribe ficha .md del cliente. Preserva 'Notas del agente' si ya existe."""
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

    # Generar ficha nueva
    contenido = generar_ficha(datos)

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

    return str(filepath), slug



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

        # Escribir ficha .md
        filepath, slug = escribir_ficha(datos)
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

    print()
    cur.close()
    conn.close()
