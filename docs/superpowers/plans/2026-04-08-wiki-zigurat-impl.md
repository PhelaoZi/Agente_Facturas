# Wiki Zigurat — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar una wiki persistente de perfiles de clientes en Markdown, mantenida automáticamente por los agentes tras cada sync/conciliación, consultable con `/perfil-cliente` y navegable en Obsidian.

**Architecture:** Script Python `wiki_update.py` consulta PostgreSQL y genera/actualiza fichas `.md` por cliente en `wiki/clientes/`. Los skills existentes (`sync-facturas`, `sync-nc`, `conciliar-banco`, `monitoreo-facturas`) agregan un paso final no-bloqueante que invoca `wiki_update.py`. Tres skills nuevos: `/wiki-init`, `/perfil-cliente`, `/wiki-lint`.

**Tech Stack:** Python 3.x, psycopg2-binary, PostgreSQL, Markdown, Obsidian (visualización)

**Spec:** `docs/superpowers/specs/2026-04-08-wiki-zigurat-design.md`

---

## Estructura de archivos

| Acción | Archivo | Responsabilidad |
|--------|---------|-----------------|
| Crear | `scripts/wiki_update.py` | Script principal: consulta BD, genera/actualiza fichas .md |
| Crear | `wiki/index.md` | Catálogo maestro de clientes |
| Crear | `wiki/log.md` | Registro cronológico de operaciones |
| Crear | `wiki/clientes/` | Carpeta para fichas de clientes |
| Crear | `.claude/skills/wiki-init/SKILL.md` | Skill de inicialización |
| Crear | `.claude/skills/perfil-cliente/SKILL.md` | Skill de consulta de perfil |
| Crear | `.claude/skills/wiki-lint/SKILL.md` | Skill de auditoría |
| Crear | `scripts/wiki_lint.py` | Script de auditoría wiki vs BD |
| Modificar | `.claude/skills/sync-facturas/SKILL.md` | Agregar paso final wiki_update |
| Modificar | `.claude/skills/sync-nc/SKILL.md` | Agregar paso final wiki_update |
| Modificar | `.claude/skills/conciliar-banco/SKILL.md` | Agregar paso final wiki_update |
| Modificar | `.claude/skills/monitoreo-facturas/SKILL.md` | Agregar paso final wiki_update |

---

## Task 1: Estructura de carpetas y script base wiki_update.py

**Files:**
- Crear: `scripts/wiki_update.py`
- Crear: `wiki/.gitkeep` (temporal, se reemplaza con index.md)

- [ ] **Step 1: Crear estructura de carpetas**

```bash
mkdir -p wiki/clientes
```

Verificar que existen:
```bash
ls wiki/
ls wiki/clientes/
```

- [ ] **Step 2: Crear wiki_update.py con boilerplate de conexión BD**

Crear `scripts/wiki_update.py` con la carga de `.env`, conexión a BD, y parsing de argumentos. Seguir el mismo patrón que `scripts/sync_db.py` para `_load_env()` y `DB_CONFIG`.

```python
#!/usr/bin/env python3
"""
wiki_update.py — Zigurat ERP
Genera y actualiza fichas wiki de clientes desde PostgreSQL.

Uso:
    python scripts/wiki_update.py --todos              # Regenera todas las fichas
    python scripts/wiki_update.py --ruts 76123456-7,77890123-4  # Clientes específicos
    python scripts/wiki_update.py --cliente 76123456-7  # Un solo cliente
"""

import argparse
import os
import re
import sys
from datetime import datetime, date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
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

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

WIKI_DIR = Path(__file__).parent.parent / "wiki"
CLIENTES_DIR = WIKI_DIR / "clientes"
INDEX_PATH = WIKI_DIR / "index.md"
LOG_PATH = WIKI_DIR / "log.md"


# ─── Conexión ─────────────────────────────────────────────────────────────────
def conectar():
    """Establece conexión a PostgreSQL."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except psycopg2.Error as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)


# ─── Utilidades ───────────────────────────────────────────────────────────────
def slugify(razon_social):
    """Convierte razón social a nombre de archivo kebab-case."""
    # Minúsculas
    slug = razon_social.lower()
    # Reemplazar tildes y ñ
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ñ": "n", "ü": "u",
    }
    for original, reemplazo in replacements.items():
        slug = slug.replace(original, reemplazo)
    # Solo alfanuméricos y espacios → guiones
    slug = re.sub(r"[^a-z0-9\s]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    # Limpiar guiones consecutivos
    slug = re.sub(r"-+", "-", slug)
    return slug


def fmt_monto(n):
    """Formatea monto como $1.234.567"""
    if n is None or n == 0:
        return "$0"
    return f"${int(n):,}".replace(",", ".")


def fmt_fecha(d):
    """Formatea date como YYYY-MM-DD"""
    if d is None:
        return "—"
    if isinstance(d, str):
        return d
    return d.strftime("%Y-%m-%d")


def parse_args():
    parser = argparse.ArgumentParser(description="Actualiza wiki de clientes desde BD")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--todos", action="store_true", help="Regenera todas las fichas")
    group.add_argument("--ruts", type=str, help="RUTs separados por coma")
    group.add_argument("--cliente", type=str, help="RUT de un solo cliente")
    parser.add_argument("--origen", type=str, default="manual",
                        help="Origen de la operación para el log (ej: sync-facturas)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print("=" * 60)
    print("ZIGURAT ERP — Wiki Update")
    print("=" * 60)
    print()
    print(f"  Modo: {'todos' if args.todos else 'selectivo'}")
    print(f"  Wiki: {WIKI_DIR}")
    print()
```

- [ ] **Step 3: Verificar que el script se ejecuta sin errores**

```bash
python scripts/wiki_update.py --todos
```

Esperado: imprime el header y el modo, luego termina (aún no hace nada más).

- [ ] **Step 4: Commit**

```bash
git add scripts/wiki_update.py wiki/
git commit -m "Agrega estructura wiki/ y boilerplate de wiki_update.py"
```

---

## Task 2: Queries de datos de cliente

**Files:**
- Modificar: `scripts/wiki_update.py`

- [ ] **Step 1: Agregar función obtener_datos_cliente()**

Agregar después de `parse_args()` y antes de `if __name__`:

```python
# ─── Queries de datos ─────────────────────────────────────────────────────────

def obtener_datos_cliente(cur, rut):
    """Ejecuta las 6 queries y retorna diccionario con todos los datos del cliente."""

    # 1. Datos maestros
    cur.execute(
        "SELECT razon_social, estado, direccion, comuna "
        "FROM clientes WHERE rut_cliente = %s",
        (rut,)
    )
    row = cur.fetchone()
    if not row:
        return None
    datos = {
        "rut": rut,
        "razon_social": row[0],
        "estado": row[1] or "activo",
        "direccion": row[2],
        "comuna": row[3],
    }

    # 2. Total vendido y facturas emitidas
    cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(COALESCE(monto_total_ajustado, monto_total)), 0) "
        "FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61'",
        (rut,)
    )
    row = cur.fetchone()
    datos["facturas_emitidas"] = row[0]
    datos["total_vendido"] = row[1]

    # 3. Facturas pendientes y deuda
    cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(COALESCE(monto_total_ajustado, monto_total)), 0) "
        "FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61' AND fecha_pago IS NULL",
        (rut,)
    )
    row = cur.fetchone()
    datos["facturas_pendientes"] = row[0]
    datos["deuda_pendiente"] = row[1]

    # 4. Promedio días de pago y último pago
    cur.execute(
        "SELECT AVG(dias_pago), MAX(fecha_pago) "
        "FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61' AND fecha_pago IS NOT NULL",
        (rut,)
    )
    row = cur.fetchone()
    datos["promedio_dias_pago"] = round(row[0]) if row[0] else None
    datos["ultimo_pago"] = row[1]

    # 5. Top 3 productos
    cur.execute(
        "SELECT p.nombre_producto, SUM(p.cantidad) as total_cantidad "
        "FROM productos p "
        "JOIN ventas v ON v.folio::text = p.folio::text AND v.tipo_documento = p.tipo_documento "
        "WHERE v.rut_cliente = %s AND v.tipo_documento != '61' "
        "GROUP BY p.nombre_producto "
        "ORDER BY total_cantidad DESC LIMIT 3",
        (rut,)
    )
    datos["top_productos"] = [
        {"nombre": row[0], "cantidad": row[1]}
        for row in cur.fetchall()
    ]

    # 6. Cliente desde cuándo
    cur.execute(
        "SELECT MIN(fecha) FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61'",
        (rut,)
    )
    row = cur.fetchone()
    datos["cliente_desde"] = row[0] if row else None

    return datos


def obtener_ruts_todos(cur):
    """Retorna lista de todos los RUTs en tabla clientes."""
    cur.execute("SELECT rut_cliente FROM clientes ORDER BY razon_social")
    return [row[0] for row in cur.fetchall()]
```

- [ ] **Step 2: Verificar que las queries funcionan contra la BD**

Agregar temporalmente al bloque `__main__` para probar:

```python
if __name__ == "__main__":
    args = parse_args()
    print("=" * 60)
    print("ZIGURAT ERP — Wiki Update")
    print("=" * 60)
    print()

    conn = conectar()
    cur = conn.cursor()

    if args.todos:
        ruts = obtener_ruts_todos(cur)
    elif args.ruts:
        ruts = [r.strip() for r in args.ruts.split(",")]
    else:
        ruts = [args.cliente]

    print(f"  Clientes a procesar: {len(ruts)}")
    print()

    for rut in ruts:
        datos = obtener_datos_cliente(cur, rut)
        if datos:
            print(f"  ✓ {datos['razon_social']} | "
                  f"vendido: {fmt_monto(datos['total_vendido'])} | "
                  f"deuda: {fmt_monto(datos['deuda_pendiente'])} | "
                  f"prom pago: {datos['promedio_dias_pago'] or '—'} días")
        else:
            print(f"  ✗ RUT {rut}: no encontrado en BD")

    conn.close()
```

```bash
python scripts/wiki_update.py --todos
```

Esperado: lista de todos los clientes con sus métricas.

- [ ] **Step 3: Commit**

```bash
git add scripts/wiki_update.py
git commit -m "wiki_update.py: agrega queries de datos de cliente desde PostgreSQL"
```

---

## Task 3: Generación de fichas Markdown

**Files:**
- Modificar: `scripts/wiki_update.py`

- [ ] **Step 1: Agregar función generar_ficha()**

Agregar después de `obtener_ruts_todos()`:

```python
# ─── Generación de fichas ─────────────────────────────────────────────────────

def generar_ficha(datos):
    """Genera el contenido Markdown de una ficha de cliente."""
    hoy = date.today().strftime("%Y-%m-%d")

    # Métricas
    prom_pago = f"{datos['promedio_dias_pago']} días" if datos["promedio_dias_pago"] else "—"
    ultimo_pago = fmt_fecha(datos["ultimo_pago"])

    # Facturas pendientes descripción
    if datos["facturas_pendientes"] > 0:
        estado_cuenta = f"- {datos['facturas_pendientes']} factura(s) pendiente(s) por {fmt_monto(datos['deuda_pendiente'])}"
    else:
        estado_cuenta = "- Sin facturas pendientes"

    # Facturas vencidas (>30 días)
    # Se calcula en detectar_eventos() — aquí solo ponemos placeholder
    vencidas_texto = ""

    # Patrón de comportamiento
    patron = generar_patron(datos)

    # Producto principal
    producto_principal = datos["top_productos"][0]["nombre"] if datos["top_productos"] else "—"

    ficha = f"""---
rut: "{datos['rut']}"
razon_social: "{datos['razon_social']}"
estado: {datos['estado']}
ultima_actualizacion: {hoy}
---

# {datos['razon_social']}

## Métricas clave
| Métrica | Valor |
|---------|-------|
| Total vendido | {fmt_monto(datos['total_vendido'])} |
| Facturas emitidas | {datos['facturas_emitidas']} |
| Facturas pendientes | {datos['facturas_pendientes']} ({fmt_monto(datos['deuda_pendiente'])}) |
| Promedio días de pago | {prom_pago} |
| Último pago | {ultimo_pago} |

## Estado de cuenta
{estado_cuenta}

## Patrón de comportamiento
{patron}

## Notas del agente
"""
    return ficha


def generar_patron(datos):
    """Genera descripción del patrón de comportamiento del cliente."""
    partes = []

    # Desde cuándo es cliente
    if datos["cliente_desde"]:
        partes.append(f"- Cliente desde {fmt_fecha(datos['cliente_desde'])}.")

    # Frecuencia de compra (aproximada)
    if datos["cliente_desde"] and datos["facturas_emitidas"] > 1:
        desde = datos["cliente_desde"]
        if isinstance(desde, str):
            from datetime import datetime as dt
            desde = dt.strptime(desde, "%Y-%m-%d").date()
        dias_activo = (date.today() - desde).days
        if dias_activo > 0:
            frecuencia = dias_activo / datos["facturas_emitidas"]
            if frecuencia <= 10:
                partes.append("- Compra con alta frecuencia (varias veces al mes).")
            elif frecuencia <= 35:
                partes.append("- Compra aproximadamente una vez al mes.")
            elif frecuencia <= 65:
                partes.append("- Compra cada 1-2 meses.")
            else:
                partes.append("- Compra de forma esporádica.")

    # Velocidad de pago
    if datos["promedio_dias_pago"] is not None:
        prom = datos["promedio_dias_pago"]
        if prom <= 7:
            partes.append(f"- Paga muy rápido (promedio {prom} días).")
        elif prom <= 20:
            partes.append(f"- Buen pagador (promedio {prom} días).")
        elif prom <= 35:
            partes.append(f"- Paga dentro de plazos normales (promedio {prom} días).")
        else:
            partes.append(f"- Pago lento (promedio {prom} días).")

    # Producto principal
    if datos["top_productos"]:
        prod = datos["top_productos"][0]
        partes.append(f"- Producto principal: {prod['nombre']}.")

    # Estado
    if datos["estado"] == "incobrable":
        partes.append("- **INCOBRABLE** — empresa marcada como incobrable.")

    return "\n".join(partes) if partes else "- Sin datos suficientes para determinar patrón."
```

- [ ] **Step 2: Agregar función escribir_ficha() que preserva notas existentes**

```python
def escribir_ficha(datos):
    """Escribe o actualiza la ficha .md de un cliente. Preserva notas existentes."""
    slug = slugify(datos["razon_social"])
    filepath = CLIENTES_DIR / f"{slug}.md"

    # Si la ficha ya existe, preservar la sección "Notas del agente"
    notas_existentes = ""
    if filepath.exists():
        contenido_actual = filepath.read_text(encoding="utf-8")
        # Extraer todo después de "## Notas del agente"
        match = re.search(r"## Notas del agente\n(.*)", contenido_actual, re.DOTALL)
        if match:
            notas_existentes = match.group(1).strip()

    ficha = generar_ficha(datos)

    # Agregar notas existentes o dejar vacío
    if notas_existentes:
        ficha += notas_existentes + "\n"

    # Asegurar que el directorio existe
    CLIENTES_DIR.mkdir(parents=True, exist_ok=True)

    filepath.write_text(ficha, encoding="utf-8")
    return filepath, slug
```

- [ ] **Step 3: Actualizar __main__ para generar fichas**

Reemplazar el bloque `__main__` completo:

```python
if __name__ == "__main__":
    args = parse_args()
    print("=" * 60)
    print("ZIGURAT ERP — Wiki Update")
    print("=" * 60)
    print()

    conn = conectar()
    cur = conn.cursor()

    if args.todos:
        ruts = obtener_ruts_todos(cur)
    elif args.ruts:
        ruts = [r.strip() for r in args.ruts.split(",")]
    else:
        ruts = [args.cliente]

    print(f"  Clientes a procesar: {len(ruts)}")
    print()

    actualizados = []

    for rut in ruts:
        datos = obtener_datos_cliente(cur, rut)
        if datos:
            filepath, slug = escribir_ficha(datos)
            actualizados.append(datos)
            print(f"  ✓ {datos['razon_social']} — "
                  f"deuda: {fmt_monto(datos['deuda_pendiente'])}")
        else:
            print(f"  ✗ RUT {rut}: no encontrado en BD")

    conn.close()

    print()
    print(f"Wiki actualizada: {len(actualizados)} cliente(s)")
    print("=" * 60)
```

- [ ] **Step 4: Probar generación de fichas**

```bash
python scripts/wiki_update.py --todos
```

Esperado: genera un `.md` por cada cliente en `wiki/clientes/`. Verificar:

```bash
ls wiki/clientes/
```

Abrir una ficha y revisar formato:

```bash
cat wiki/clientes/*.md | head -40
```

- [ ] **Step 5: Commit**

```bash
git add scripts/wiki_update.py wiki/clientes/
git commit -m "wiki_update.py: genera fichas Markdown de clientes desde PostgreSQL"
```

---

## Task 4: index.md y log.md

**Files:**
- Modificar: `scripts/wiki_update.py`

- [ ] **Step 1: Agregar función actualizar_index()**

Agregar después de `escribir_ficha()`:

```python
# ─── Index y Log ──────────────────────────────────────────────────────────────

def actualizar_index(cur):
    """Regenera index.md completo desde BD."""
    # Obtener datos de todos los clientes para el índice
    cur.execute(
        "SELECT c.rut_cliente, c.razon_social, c.estado, "
        "COUNT(CASE WHEN v.tipo_documento != '61' AND v.fecha_pago IS NULL THEN 1 END) as pendientes, "
        "COALESCE(SUM(CASE WHEN v.tipo_documento != '61' AND v.fecha_pago IS NULL "
        "    THEN COALESCE(v.monto_total_ajustado, v.monto_total) ELSE 0 END), 0) as deuda "
        "FROM clientes c "
        "LEFT JOIN ventas v ON v.rut_cliente = c.rut_cliente "
        "GROUP BY c.rut_cliente, c.razon_social, c.estado "
        "ORDER BY c.razon_social"
    )
    rows = cur.fetchall()

    hoy = date.today().strftime("%Y-%m-%d")
    activos = [r for r in rows if (r[2] or "activo") != "incobrable"]
    incobrables = [r for r in rows if (r[2] or "activo") == "incobrable"]

    lines = [
        "# Wiki Zigurat — Índice de Clientes\n",
        f"> Última actualización: {hoy} | Total clientes: {len(rows)} "
        f"({len(activos)} activos, {len(incobrables)} incobrables)\n",
    ]

    # Activos
    lines.append("## Clientes activos\n")
    lines.append("| Cliente | RUT | Deuda pendiente | Última actualización |")
    lines.append("|---------|-----|-----------------|---------------------|")
    for rut, razon, estado, pendientes, deuda in activos:
        slug = slugify(razon)
        lines.append(
            f"| [[{razon}]] | {rut} | {fmt_monto(deuda)} | {hoy} |"
        )

    # Incobrables
    if incobrables:
        lines.append("\n## Clientes incobrables\n")
        lines.append("| Cliente | RUT | Deuda histórica | Última actualización |")
        lines.append("|---------|-----|-----------------|---------------------|")
        for rut, razon, estado, pendientes, deuda in incobrables:
            slug = slugify(razon)
            lines.append(
                f"| [[{razon}]] | {rut} | {fmt_monto(deuda)} | {hoy} |"
            )

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Index actualizado: {INDEX_PATH}")
```

- [ ] **Step 2: Agregar función actualizar_log()**

```python
def actualizar_log(actualizados, origen="manual"):
    """Agrega entrada al log.md con los clientes actualizados."""
    hoy = date.today().strftime("%Y-%m-%d")
    ahora = datetime.now().strftime("%H:%M")

    # Construir entrada
    nombres = [d["razon_social"] for d in actualizados]
    if len(nombres) <= 5:
        lista = ", ".join(nombres)
    else:
        lista = ", ".join(nombres[:5]) + f" y {len(nombres) - 5} más"

    entrada = f"- **{origen}** ({ahora}): Actualizadas {len(actualizados)} ficha(s): {lista}."

    # Leer log existente o crear nuevo
    if LOG_PATH.exists():
        contenido = LOG_PATH.read_text(encoding="utf-8")
    else:
        contenido = "# Wiki Zigurat — Log de Operaciones\n"

    # Buscar si ya hay una sección para hoy
    marcador = f"## {hoy}"
    if marcador in contenido:
        # Agregar entrada bajo la sección existente
        contenido = contenido.replace(marcador, f"{marcador}\n{entrada}", 1)
    else:
        # Crear nueva sección después del título
        pos = contenido.find("\n", contenido.find("# Wiki Zigurat"))
        if pos == -1:
            pos = len(contenido)
        contenido = contenido[:pos + 1] + f"\n{marcador}\n{entrada}\n" + contenido[pos + 1:]

    LOG_PATH.write_text(contenido, encoding="utf-8")
    print(f"  Log actualizado: {LOG_PATH}")
```

- [ ] **Step 3: Integrar index y log en __main__**

Reemplazar el bloque `__main__`:

```python
if __name__ == "__main__":
    args = parse_args()
    print("=" * 60)
    print("ZIGURAT ERP — Wiki Update")
    print("=" * 60)
    print()

    conn = conectar()
    cur = conn.cursor()

    if args.todos:
        ruts = obtener_ruts_todos(cur)
    elif args.ruts:
        ruts = [r.strip() for r in args.ruts.split(",")]
    else:
        ruts = [args.cliente]

    print(f"  Clientes a procesar: {len(ruts)}")
    print()

    actualizados = []

    for rut in ruts:
        datos = obtener_datos_cliente(cur, rut)
        if datos:
            filepath, slug = escribir_ficha(datos)
            actualizados.append(datos)
            print(f"  ✓ {datos['razon_social']} — "
                  f"deuda: {fmt_monto(datos['deuda_pendiente'])}")
        else:
            print(f"  ✗ RUT {rut}: no encontrado en BD")

    if actualizados:
        print()
        actualizar_index(cur)
        actualizar_log(actualizados, origen=args.origen)

    conn.close()

    print()
    print(f"Wiki actualizada: {len(actualizados)} cliente(s)")
    print("=" * 60)
```

- [ ] **Step 4: Probar generación completa**

```bash
python scripts/wiki_update.py --todos --origen "wiki-init"
```

Esperado: genera fichas + `index.md` + `log.md`. Verificar:

```bash
cat wiki/index.md
cat wiki/log.md
```

- [ ] **Step 5: Commit**

```bash
git add scripts/wiki_update.py wiki/
git commit -m "wiki_update.py: agrega generación de index.md y log.md"
```

---

## Task 5: Detección de eventos notables

**Files:**
- Modificar: `scripts/wiki_update.py`

- [ ] **Step 1: Agregar función detectar_eventos()**

Agregar después de `generar_patron()`:

```python
def detectar_eventos(cur, datos):
    """Detecta eventos notables para agregar a las notas del agente."""
    eventos = []
    hoy = date.today()
    hoy_str = hoy.strftime("%Y-%m-%d")

    # 1. Facturas vencidas (>30 días sin pago)
    cur.execute(
        "SELECT folio, fecha, COALESCE(monto_total_ajustado, monto_total) "
        "FROM ventas "
        "WHERE rut_cliente = %s AND tipo_documento != '61' AND fecha_pago IS NULL "
        "AND fecha < CURRENT_DATE - INTERVAL '30 days' "
        "ORDER BY fecha",
        (datos["rut"],)
    )
    vencidas = cur.fetchall()
    if vencidas:
        folios = ", ".join([f"#{r[0]}" for r in vencidas])
        total = sum(r[2] for r in vencidas)
        eventos.append(
            f"- {hoy_str}: ⚠️ {len(vencidas)} factura(s) vencida(s) (>30 días): "
            f"{folios} por {fmt_monto(total)}."
        )

    # 2. Pago múltiple (>1 factura pagada en la misma fecha, últimos 7 días)
    cur.execute(
        "SELECT fecha_pago, COUNT(*) "
        "FROM ventas "
        "WHERE rut_cliente = %s AND tipo_documento != '61' AND fecha_pago IS NOT NULL "
        "AND fecha_pago >= CURRENT_DATE - INTERVAL '7 days' "
        "GROUP BY fecha_pago "
        "HAVING COUNT(*) > 1",
        (datos["rut"],)
    )
    pagos_multiples = cur.fetchall()
    for fecha_pago, count in pagos_multiples:
        eventos.append(
            f"- {hoy_str}: Pagó {count} facturas juntas el {fmt_fecha(fecha_pago)}."
        )

    # 3. Cliente inactivo (>60 días sin nueva factura)
    cur.execute(
        "SELECT MAX(fecha) FROM ventas "
        "WHERE rut_cliente = %s AND tipo_documento != '61'",
        (datos["rut"],)
    )
    row = cur.fetchone()
    if row and row[0]:
        ultima_factura = row[0]
        if isinstance(ultima_factura, str):
            from datetime import datetime as dt
            ultima_factura = dt.strptime(ultima_factura, "%Y-%m-%d").date()
        dias_inactivo = (hoy - ultima_factura).days
        if dias_inactivo > 60 and datos["estado"] != "incobrable":
            eventos.append(
                f"- {hoy_str}: ⚠️ Cliente inactivo — {dias_inactivo} días sin nueva factura "
                f"(última: {fmt_fecha(ultima_factura)})."
            )

    return eventos
```

- [ ] **Step 2: Integrar detección de eventos en escribir_ficha()**

Modificar `escribir_ficha()` para que reciba `cur` y agregue eventos nuevos:

```python
def escribir_ficha(datos, cur=None):
    """Escribe o actualiza la ficha .md de un cliente. Preserva notas existentes."""
    slug = slugify(datos["razon_social"])
    filepath = CLIENTES_DIR / f"{slug}.md"

    # Si la ficha ya existe, preservar la sección "Notas del agente"
    notas_existentes = ""
    if filepath.exists():
        contenido_actual = filepath.read_text(encoding="utf-8")
        match = re.search(r"## Notas del agente\n(.*)", contenido_actual, re.DOTALL)
        if match:
            notas_existentes = match.group(1).strip()

    ficha = generar_ficha(datos)

    # Detectar eventos nuevos
    eventos_nuevos = []
    if cur:
        eventos_nuevos = detectar_eventos(cur, datos)

    # Agregar eventos nuevos (solo si no están ya en las notas)
    if eventos_nuevos and notas_existentes:
        for evento in eventos_nuevos:
            # Evitar duplicados: verificar que el texto del evento no existe
            texto_evento = evento.split(": ", 1)[1] if ": " in evento else evento
            if texto_evento not in notas_existentes:
                notas_existentes = evento + "\n" + notas_existentes
    elif eventos_nuevos:
        notas_existentes = "\n".join(eventos_nuevos)

    if notas_existentes:
        ficha += notas_existentes + "\n"

    CLIENTES_DIR.mkdir(parents=True, exist_ok=True)
    filepath.write_text(ficha, encoding="utf-8")
    return filepath, slug
```

- [ ] **Step 3: Actualizar la llamada a escribir_ficha() en __main__**

Cambiar la línea en el loop:

```python
            filepath, slug = escribir_ficha(datos, cur=cur)
```

- [ ] **Step 4: Probar detección de eventos**

```bash
python scripts/wiki_update.py --todos --origen "test-eventos"
```

Verificar que las fichas de clientes con facturas vencidas o inactivos tienen notas:

```bash
grep -l "vencida\|inactivo" wiki/clientes/*.md
```

- [ ] **Step 5: Commit**

```bash
git add scripts/wiki_update.py wiki/
git commit -m "wiki_update.py: agrega detección de eventos notables en notas del agente"
```

---

## Task 6: Skill /wiki-init

**Files:**
- Crear: `.claude/skills/wiki-init/SKILL.md`

- [ ] **Step 1: Crear el skill**

```markdown
---
name: wiki-init
description: >
  Inicializa la wiki de Zigurat generando fichas de todos los clientes desde PostgreSQL.
  Usar la primera vez para crear la wiki completa, o para regenerar todo desde cero.
  Ejemplos: "inicializa la wiki", "crea la wiki de clientes", "regenera las fichas".
context: fork
disable-model-invocation: true
allowed-tools: Bash(python *), Bash(mkdir *)
---

# Wiki Init — Zigurat ERP

Inicializa la wiki completa generando una ficha por cada cliente en la BD.

## Reglas

- NUNCA pedir confirmación antes de ejecutar
- Si la wiki ya existe, se regenera (las notas del agente existentes se preservan)

## Paso 1 — Crear estructura

```bash
mkdir -p wiki/clientes
```

## Paso 2 — Generar todas las fichas

```bash
python scripts/wiki_update.py --todos --origen "wiki-init"
```

Si falla: reportar error y detener.

## Paso 3 — Resumen final

Reportar al usuario:
- Total de fichas generadas
- Clientes activos vs incobrables
- Ruta de la wiki: `wiki/`
- Sugerir abrir `wiki/` como vault en Obsidian para ver graph view
```

- [ ] **Step 2: Probar el skill**

Ejecutar manualmente lo que haría el skill:

```bash
mkdir -p wiki/clientes && python scripts/wiki_update.py --todos --origen "wiki-init"
```

Esperado: genera todas las fichas, index y log.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/wiki-init/
git commit -m "Agrega skill /wiki-init para inicializar la wiki de clientes"
```

---

## Task 7: Skill /perfil-cliente

**Files:**
- Crear: `.claude/skills/perfil-cliente/SKILL.md`

- [ ] **Step 1: Crear el skill**

```markdown
---
name: perfil-cliente
description: >
  Muestra el perfil completo de un cliente consultando la wiki y la base de datos.
  Usar cuando el usuario quiera saber sobre un cliente específico, su estado de cuenta,
  patrón de pago, o historial. Ejemplos: "cómo va Marina?", "perfil de Distribuidora XYZ",
  "qué onda con el cliente tal?", "muéstrame la ficha de...".
argument-hint: "[nombre del cliente]"
context: conversation
allowed-tools: Read, Glob, Bash(python *)
---

# Perfil Cliente — Zigurat ERP

Consulta la wiki de clientes y complementa con datos en tiempo real de la BD.

## Reglas

- SIEMPRE buscar primero en la wiki (`wiki/clientes/`)
- Si la ficha no existe, sugerir ejecutar `/wiki-init`
- Presentar la información de forma narrativa, no solo copiar el Markdown
- Destacar alertas: facturas vencidas, clientes inactivos, cambios de comportamiento

## Paso 1 — Buscar ficha del cliente

Buscar en `wiki/clientes/` el archivo que coincida con el nombre proporcionado en `$ARGUMENTS`:
1. Hacer glob en `wiki/clientes/*.md`
2. Buscar coincidencia parcial en el nombre del archivo (case-insensitive)
3. Si hay múltiples matches, mostrarlos y pedir que el usuario elija
4. Si no hay match, buscar dentro de los archivos por `razon_social` en el frontmatter

## Paso 2 — Leer la ficha

Leer el archivo `.md` completo del cliente encontrado.

## Paso 3 — Presentar al usuario

Presentar la información de forma narrativa y clara:
- Nombre y estado del cliente
- Métricas clave con contexto (ej: "paga rápido", "tiene deuda pendiente")
- Patrón de comportamiento
- Notas del agente relevantes
- Si hay alertas (facturas vencidas, inactividad), destacarlas al inicio

No copiar el Markdown tal cual — interpretar y presentar con insights.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/perfil-cliente/
git commit -m "Agrega skill /perfil-cliente para consultar wiki de clientes"
```

---

## Task 8: Skill /wiki-lint y script wiki_lint.py

**Files:**
- Crear: `scripts/wiki_lint.py`
- Crear: `.claude/skills/wiki-lint/SKILL.md`

- [ ] **Step 1: Crear script wiki_lint.py**

```python
#!/usr/bin/env python3
"""
wiki_lint.py — Zigurat ERP
Audita la consistencia entre la wiki y la base de datos.

Uso:
    python scripts/wiki_lint.py
"""

import os
import re
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
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

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

WIKI_DIR = Path(__file__).parent.parent / "wiki"
CLIENTES_DIR = WIKI_DIR / "clientes"


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Wiki Lint")
    print("=" * 60)
    print()

    if not CLIENTES_DIR.exists():
        print("ERROR: No existe wiki/clientes/. Ejecuta /wiki-init primero.")
        sys.exit(1)

    # Conectar a BD
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
    except psycopg2.Error as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    problemas = []

    # 1. Clientes en BD sin ficha
    cur.execute("SELECT rut_cliente, razon_social FROM clientes ORDER BY razon_social")
    clientes_bd = cur.fetchall()

    fichas_existentes = {f.stem for f in CLIENTES_DIR.glob("*.md")}

    # Importar slugify de wiki_update
    sys.path.insert(0, str(Path(__file__).parent))
    from wiki_update import slugify

    for rut, razon in clientes_bd:
        slug = slugify(razon)
        if slug not in fichas_existentes:
            problemas.append(f"  [SIN FICHA] {razon} ({rut}) — no tiene ficha en wiki/clientes/")

    # 2. Fichas sin cliente en BD (huérfanas)
    ruts_bd = {r[0] for r in clientes_bd}
    slugs_bd = {slugify(r[1]) for r in clientes_bd}

    for ficha in CLIENTES_DIR.glob("*.md"):
        if ficha.stem not in slugs_bd:
            problemas.append(f"  [HUÉRFANA] {ficha.name} — no tiene cliente en BD")

    # 3. Fichas desactualizadas
    hoy = date.today()
    for ficha in CLIENTES_DIR.glob("*.md"):
        contenido = ficha.read_text(encoding="utf-8")
        match = re.search(r"ultima_actualizacion:\s*(\d{4}-\d{2}-\d{2})", contenido)
        if match:
            from datetime import datetime as dt
            ultima = dt.strptime(match.group(1), "%Y-%m-%d").date()
            dias = (hoy - ultima).days
            if dias > 7:
                # Verificar si hay movimientos recientes
                rut_match = re.search(r'rut:\s*"([^"]+)"', contenido)
                if rut_match:
                    rut = rut_match.group(1)
                    cur.execute(
                        "SELECT COUNT(*) FROM ventas "
                        "WHERE rut_cliente = %s AND fecha > %s",
                        (rut, match.group(1))
                    )
                    count = cur.fetchone()[0]
                    if count > 0:
                        problemas.append(
                            f"  [DESACTUALIZADA] {ficha.name} — última actualización: "
                            f"{match.group(1)} ({dias} días) con {count} movimiento(s) nuevo(s)"
                        )

    conn.close()

    # Reporte
    if problemas:
        print(f"  Se encontraron {len(problemas)} problema(s):\n")
        for p in problemas:
            print(p)
        print()
        print("  Sugerencia: ejecutar 'python scripts/wiki_update.py --todos' para corregir.")
    else:
        print("  ✅ Wiki consistente — no se encontraron problemas.")

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Crear el skill wiki-lint**

```markdown
---
name: wiki-lint
description: >
  Audita la consistencia entre la wiki de clientes y la base de datos.
  Detecta fichas faltantes, huérfanas o desactualizadas.
  Usar cuando el usuario quiera verificar la salud de la wiki.
  Ejemplos: "revisa la wiki", "hay fichas desactualizadas?", "lint wiki",
  "la wiki está bien?".
context: fork
disable-model-invocation: true
allowed-tools: Bash(python *)
---

# Wiki Lint — Zigurat ERP

Audita la wiki de clientes para encontrar inconsistencias con la BD.

## Reglas

- NUNCA modificar archivos, solo reportar
- Si hay problemas, sugerir `/wiki-init` para regenerar todo o `wiki_update.py --ruts` para corregir selectivamente

## Paso 1 — Ejecutar auditoría

```bash
python scripts/wiki_lint.py
```

## Paso 2 — Interpretar resultados

Mostrar al usuario los problemas encontrados con contexto:
- **SIN FICHA**: cliente en BD que no tiene wiki → sugerir wiki_update
- **HUÉRFANA**: ficha sin cliente en BD → puede ser un cliente eliminado
- **DESACTUALIZADA**: ficha vieja con movimientos nuevos → sugerir wiki_update
```

- [ ] **Step 3: Probar el lint**

```bash
python scripts/wiki_lint.py
```

Esperado: si se ejecutó `wiki_update.py --todos` antes, debería mostrar "Wiki consistente".

- [ ] **Step 4: Commit**

```bash
git add scripts/wiki_lint.py .claude/skills/wiki-lint/
git commit -m "Agrega skill /wiki-lint y script de auditoría wiki vs BD"
```

---

## Task 9: Integrar wiki_update en skills existentes

**Files:**
- Modificar: `.claude/skills/sync-facturas/SKILL.md`
- Modificar: `.claude/skills/sync-nc/SKILL.md`
- Modificar: `.claude/skills/conciliar-banco/SKILL.md`
- Modificar: `.claude/skills/monitoreo-facturas/SKILL.md`

- [ ] **Step 1: Modificar /sync-facturas**

Agregar al final de `.claude/skills/sync-facturas/SKILL.md`, después del Paso 4:

```markdown
## Paso 5 — Actualizar wiki (no-bloqueante)

Parsear los RUTs de clientes del output del Paso 3 (líneas con "✓ Folio ... | NOMBRE_CLIENTE |").
Extraer los RUTs únicos del output de sync_db.py. Si no se pueden parsear, usar `--todos`.

```bash
python scripts/wiki_update.py --ruts RUT1,RUT2,RUT3 --origen "sync-facturas"
```

Si falla: mostrar warning "⚠️ No se pudo actualizar la wiki" pero NO fallar el proceso.
La sincronización de facturas ya se completó exitosamente.
```

- [ ] **Step 2: Modificar /sync-nc**

Agregar al final de los Pasos A3 y E3 de `.claude/skills/sync-nc/SKILL.md`:

```markdown
### Paso final — Actualizar wiki (no-bloqueante)

Parsear los RUTs de los clientes afectados del output de sync_db.py.

```bash
python scripts/wiki_update.py --ruts RUT1,RUT2,RUT3 --origen "sync-nc"
```

Si falla: mostrar warning pero NO fallar el proceso.
```

- [ ] **Step 3: Modificar /conciliar-banco**

Agregar al final de `.claude/skills/conciliar-banco/SKILL.md`, después del Paso 2:

```markdown
## Paso 3 — Actualizar wiki (no-bloqueante)

Si la conciliación fue exitosa (se guardaron cambios en BD), parsear los RUTs
de los clientes conciliados del output del script.

```bash
python scripts/wiki_update.py --ruts RUT1,RUT2,RUT3 --origen "conciliar-banco"
```

Si falla: mostrar warning pero NO fallar el proceso.
```

- [ ] **Step 4: Modificar /monitoreo-facturas**

Agregar al final de `.claude/skills/monitoreo-facturas/SKILL.md`, después del Paso 3:

```markdown
## Paso 4 — Actualizar wiki (no-bloqueante)

Si se procesaron archivos exitosamente, ejecutar wiki_update para los clientes afectados.
Si no se pueden determinar los RUTs específicos, usar `--todos`.

```bash
python scripts/wiki_update.py --todos --origen "monitoreo-facturas"
```

Si falla: mostrar warning pero NO fallar el proceso.
```

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/sync-facturas/SKILL.md .claude/skills/sync-nc/SKILL.md .claude/skills/conciliar-banco/SKILL.md .claude/skills/monitoreo-facturas/SKILL.md
git commit -m "Integra wiki_update como paso final no-bloqueante en skills de sync y conciliación"
```

---

## Task 10: Actualizar CLAUDE.md y prueba end-to-end

**Files:**
- Modificar: `.claude/CLAUDE.md`

- [ ] **Step 1: Agregar documentación de la wiki a CLAUDE.md**

Agregar una nueva sección después de "## Workflow de conciliación bancaria" en `.claude/CLAUDE.md`:

```markdown
---

## Wiki de clientes (LLM Wiki)

Base de conocimiento persistente en Markdown, mantenida automáticamente por los agentes.

### Estructura

```
wiki/
├── index.md          # Catálogo maestro de clientes
├── log.md            # Registro cronológico de operaciones
└── clientes/         # Una ficha .md por cliente
```

### Comandos

```bash
/wiki-init                    # Inicializar wiki completa (primera vez)
/perfil-cliente [nombre]      # Consultar perfil de un cliente
/wiki-lint                    # Auditar consistencia wiki vs BD
```

### Actualización automática

Los skills `/sync-facturas`, `/sync-nc`, `/conciliar-banco` y `/monitoreo-facturas` actualizan automáticamente las fichas de los clientes afectados al finalizar.

### Obsidian

La carpeta `wiki/` se puede abrir como vault en Obsidian para navegar con graph view y backlinks. Los wikilinks `[[Nombre]]` en index.md se resuelven automáticamente.
```

- [ ] **Step 2: Agregar wiki a la estructura de archivos en CLAUDE.md**

En la sección "### Estructura de archivos", agregar:

```markdown
wiki/                       # Wiki de clientes (LLM Wiki pattern)
  index.md                  # Catálogo maestro de clientes
  log.md                    # Registro de operaciones
  clientes/                 # Fichas .md por cliente
```

- [ ] **Step 3: Agregar comandos wiki a la sección "## Comandos frecuentes"**

```markdown
# Wiki de clientes
/wiki-init                        # Inicializar wiki (primera vez)
/perfil-cliente Marina            # Ver perfil de un cliente
/wiki-lint                        # Auditar consistencia wiki vs BD
```

- [ ] **Step 4: Prueba end-to-end**

Ejecutar la secuencia completa:

```bash
# 1. Inicializar wiki
python scripts/wiki_update.py --todos --origen "wiki-init"

# 2. Verificar fichas generadas
ls wiki/clientes/ | head -10

# 3. Verificar index
cat wiki/index.md | head -20

# 4. Verificar log
cat wiki/log.md

# 5. Lint
python scripts/wiki_lint.py

# 6. Actualizar un cliente específico (simula post-sync)
python scripts/wiki_update.py --ruts <RUT_DE_PRUEBA> --origen "test"

# 7. Lint de nuevo
python scripts/wiki_lint.py
```

Criterios de éxito:
- Fichas generadas para todos los clientes
- Index con tabla de clientes activos e incobrables
- Log con entradas de operaciones
- Lint sin problemas después de init
- Actualización selectiva funciona sin afectar otras fichas
- Notas del agente se preservan entre actualizaciones

- [ ] **Step 5: Commit**

```bash
git add .claude/CLAUDE.md
git commit -m "Documenta wiki de clientes en CLAUDE.md: comandos, estructura y workflow"
```

- [ ] **Step 6: Commit final con toda la wiki generada**

```bash
git add wiki/
git commit -m "Wiki de clientes inicializada: fichas, index y log desde PostgreSQL"
```
