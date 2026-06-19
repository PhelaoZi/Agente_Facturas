# Brief Diario Automático — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generar cada mañana un brief de negocio (deuda, cobranzas, morosos, inactivos, ventas recientes) de forma automática y confiable, sin intervención.

**Architecture:** Capa de datos de **solo lectura** (`app/briefing/data.py`) que reutiliza las queries canónicas del proyecto y devuelve estructuras ya agregadas; una capa de render puro a Markdown (`app/briefing/render.py`); un script de entrada (`scripts/generar_brief.py`) que conecta a Postgres, junta las secciones y guarda el brief en `briefs/YYYY-MM-DD.md`; y una Tarea Programada de Windows que lo corre a las 08:00 (mismo patrón que el backup de las 23:00). **No hay LLM ni escritura en BD en esta fase** — es la base segura sobre la que se montarán las fases 2 (chat con acciones confirmadas) y 3 (alertas proactivas).

**Tech Stack:** Python 3.x, psycopg2 (`RealDictCursor`), pytest, PowerShell (Scheduled Task). Sin dependencias nuevas.

**Decisiones de diseño (el "por qué"):**
- **Determinista, sin LLM.** El brief debe ser confiable y de costo cero. La "inteligencia" en esta fase es priorización determinista (orden por monto, buckets de antigüedad, umbrales de alerta). La narración con el agente se difiere a la fase 2, cuando exista la capa de herramientas MCP — así no agregamos un segundo camino de llamadas a Claude antes de tiempo.
- **`app/briefing/data.py` es la inversión reutilizable.** Esas mismas funciones de lectura se exponen como herramientas MCP en la fase 2 (chat capaz). No es código desechable.
- **Reutiliza reglas canónicas** del proyecto (CLAUDE.md): monto real = `COALESCE(monto_total_ajustado, monto_total)`; excluir NC con `tipo_documento != 61`; estado de cobro por `fecha_pago IS NULL`; excluir clientes `estado = 'incobrable'` de los totales de deuda.
- **Funciones testeables con cursor falso**, igual que el patrón ya usado en `tests/test_orchestrator.py` (sin BD real en los tests).

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `app/briefing/__init__.py` | Marca el paquete (vacío, patrón del proyecto). |
| `app/briefing/data.py` | Funciones de **solo lectura**; reciben un cursor, devuelven dicts/listas ya agregados. |
| `app/briefing/render.py` | Función pura `render_markdown(brief)` → string Markdown. Sin BD. |
| `scripts/generar_brief.py` | Punto de entrada: conecta, recolecta, renderiza, guarda en `briefs/`. |
| `scripts/instalar_tarea_brief.ps1` | Crea/actualiza la Tarea Programada "Zigurat - Brief Diario" (idempotente). |
| `tests/test_briefing_data.py` | Tests de la capa de datos con cursor falso. |
| `tests/test_briefing_render.py` | Tests del render a Markdown. |
| `briefs/` | Carpeta de salida (se crea en runtime; los `.md` son el historial del negocio). |

---

### Task 1: Paquete `app/briefing` + `data.resumen_cobranza`

**Files:**
- Create: `app/briefing/__init__.py`
- Create: `app/briefing/data.py`
- Test: `tests/test_briefing_data.py`

- [ ] **Step 1: Crear el `__init__.py` del paquete**

Create `app/briefing/__init__.py` con una sola línea de docstring (igual que los otros `__init__.py` del proyecto, que tienen 40 bytes):

```python
"""Capa del brief diario de Zigurat (solo lectura)."""
```

- [ ] **Step 2: Escribir el test que falla**

Create `tests/test_briefing_data.py`:

```python
# tests/test_briefing_data.py
from app.briefing import data


class FakeCursor:
    """Cursor falso al estilo RealDictCursor: fetchall/fetchone devuelven dicts.
    Ignora el SQL; solo entrega las filas precargadas (patrón de test del proyecto)."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_resumen_cobranza_clasifica_por_antiguedad():
    rows = [
        {"dias": 0, "total": 20000},    # al día
        {"dias": 5, "total": 100000},   # 1-30
        {"dias": 45, "total": 50000},   # 31-60
        {"dias": 90, "total": 30000},   # +60
    ]
    r = data.resumen_cobranza(FakeCursor(rows))
    assert r["total"] == 200000
    assert r["n_facturas"] == 4
    assert r["buckets"]["al_dia"] == 20000
    assert r["buckets"]["d1_30"] == 100000
    assert r["buckets"]["d31_60"] == 50000
    assert r["buckets"]["d60_mas"] == 30000


def test_resumen_cobranza_sin_deuda():
    r = data.resumen_cobranza(FakeCursor([]))
    assert r["total"] == 0
    assert r["n_facturas"] == 0
    assert r["buckets"] == {"al_dia": 0, "d1_30": 0, "d31_60": 0, "d60_mas": 0}
```

- [ ] **Step 3: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_briefing_data.py -v`
Expected: FAIL con `AttributeError: module 'app.briefing.data' has no attribute 'resumen_cobranza'` (o ImportError si el módulo aún no existe).

- [ ] **Step 4: Implementar `data.py` con `resumen_cobranza`**

Create `app/briefing/data.py`:

```python
"""Capa de datos de solo lectura para el brief diario.

Cada función recibe un cursor (RealDictCursor) y devuelve estructuras Python
simples y ya agregadas, listas para renderizar. Reglas canónicas del proyecto:
- Monto real = COALESCE(monto_total_ajustado, monto_total)
- Excluir Notas de Crédito: tipo_documento != 61
- Estado de cobro: fecha_pago IS NULL = pendiente
- Excluir clientes 'incobrable' de los totales de deuda
"""


def resumen_cobranza(cur):
    """Deuda total pendiente y su desglose por antigüedad (aging buckets)."""
    cur.execute("""
        SELECT (CURRENT_DATE - v.fecha) AS dias,
               COALESCE(v.monto_total_ajustado, v.monto_total) AS total
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
          AND v.fecha_pago IS NULL
          AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
          AND COALESCE(c.estado, '') <> 'incobrable'
    """)
    buckets = {"al_dia": 0, "d1_30": 0, "d31_60": 0, "d60_mas": 0}
    total = 0
    filas = cur.fetchall()
    for f in filas:
        dias = int(f["dias"])
        monto = float(f["total"])
        total += monto
        if dias <= 0:
            buckets["al_dia"] += monto
        elif dias <= 30:
            buckets["d1_30"] += monto
        elif dias <= 60:
            buckets["d31_60"] += monto
        else:
            buckets["d60_mas"] += monto
    return {"total": total, "n_facturas": len(filas), "buckets": buckets}
```

- [ ] **Step 5: Correr el test y verificar que pasa**

Run: `python -m pytest tests/test_briefing_data.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add app/briefing/__init__.py app/briefing/data.py tests/test_briefing_data.py
git commit -m "Agrega capa de datos del brief: resumen de cobranza por antiguedad"
```

---

### Task 2: `data.top_deudores` + `data.facturas_vencidas`

**Files:**
- Modify: `app/briefing/data.py`
- Test: `tests/test_briefing_data.py`

- [ ] **Step 1: Agregar los tests que fallan**

Agregar al final de `tests/test_briefing_data.py`:

```python
def test_top_deudores_mapea_y_preserva_orden():
    rows = [
        {"razon_social": "Bar Uno", "deuda": 500000, "n": 3},
        {"razon_social": "Bar Dos", "deuda": 200000, "n": 1},
    ]
    r = data.top_deudores(FakeCursor(rows), limite=5)
    assert r == [
        {"cliente": "Bar Uno", "deuda": 500000.0, "n": 3},
        {"cliente": "Bar Dos", "deuda": 200000.0, "n": 1},
    ]


def test_facturas_vencidas_mapea_dias_y_total():
    rows = [
        {"folio": 1234, "fecha": "2026-04-01", "razon_social": "Bar Uno",
         "total": 80000, "dias_vencida": 78},
    ]
    r = data.facturas_vencidas(FakeCursor(rows), dias=30)
    assert r == [{"folio": 1234, "cliente": "Bar Uno", "total": 80000.0, "dias": 78}]
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_briefing_data.py -v`
Expected: FAIL con `AttributeError` en `top_deudores` / `facturas_vencidas`.

- [ ] **Step 3: Implementar las dos funciones**

Agregar al final de `app/briefing/data.py`:

```python
def top_deudores(cur, limite=5):
    """Top N clientes por deuda pendiente (suma de facturas sin pago)."""
    cur.execute("""
        SELECT c.razon_social,
               SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS deuda,
               COUNT(*) AS n
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
          AND v.fecha_pago IS NULL
          AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
          AND COALESCE(c.estado, '') <> 'incobrable'
        GROUP BY c.razon_social
        ORDER BY deuda DESC
        LIMIT %s
    """, (limite,))
    return [
        {"cliente": f["razon_social"], "deuda": float(f["deuda"]), "n": int(f["n"])}
        for f in cur.fetchall()
    ]


def facturas_vencidas(cur, dias=30):
    """Facturas pendientes con más de `dias` de antigüedad (morosos)."""
    cur.execute("""
        SELECT v.folio, v.fecha, c.razon_social,
               COALESCE(v.monto_total_ajustado, v.monto_total) AS total,
               (CURRENT_DATE - v.fecha) AS dias_vencida
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
          AND v.fecha_pago IS NULL
          AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
          AND COALESCE(c.estado, '') <> 'incobrable'
          AND (CURRENT_DATE - v.fecha) > %s
        ORDER BY dias_vencida DESC
    """, (dias,))
    return [
        {"folio": f["folio"], "cliente": f["razon_social"],
         "total": float(f["total"]), "dias": int(f["dias_vencida"])}
        for f in cur.fetchall()
    ]
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `python -m pytest tests/test_briefing_data.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/briefing/data.py tests/test_briefing_data.py
git commit -m "Agrega top deudores y facturas vencidas al brief"
```

---

### Task 3: `cobrado_reciente` + `ventas_periodo` + `clientes_inactivos`

**Files:**
- Modify: `app/briefing/data.py`
- Test: `tests/test_briefing_data.py`

- [ ] **Step 1: Agregar los tests que fallan**

Agregar al final de `tests/test_briefing_data.py`:

```python
def test_cobrado_reciente_suma_y_cuenta():
    rows = [
        {"folio": 1, "fecha_pago": "2026-06-17", "razon_social": "Bar Uno", "total": 70000},
        {"folio": 2, "fecha_pago": "2026-06-16", "razon_social": "Bar Dos", "total": 30000},
    ]
    r = data.cobrado_reciente(FakeCursor(rows), dias=7)
    assert r["n"] == 2
    assert r["total"] == 100000.0
    assert r["facturas"][0]["cliente"] == "Bar Uno"


def test_ventas_periodo_devuelve_n_y_total():
    r = data.ventas_periodo(FakeCursor([{"n": 5, "total": 350000}]), dias=7)
    assert r == {"n": 5, "total": 350000.0}


def test_clientes_inactivos_mapea_dias():
    rows = [
        {"razon_social": "Bar Frio", "ultima_venta": "2026-03-01", "dias_inactivo": 109},
    ]
    r = data.clientes_inactivos(FakeCursor(rows), dias=60)
    assert r == [{"cliente": "Bar Frio", "ultima_venta": "2026-03-01", "dias": 109}]
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_briefing_data.py -v`
Expected: FAIL con `AttributeError` en las tres funciones nuevas.

- [ ] **Step 3: Implementar las tres funciones**

Agregar al final de `app/briefing/data.py`:

```python
def cobrado_reciente(cur, dias=7):
    """Facturas cobradas en los últimos `dias` (fecha_pago reciente)."""
    cur.execute("""
        SELECT v.folio, v.fecha_pago, c.razon_social,
               COALESCE(v.monto_total_ajustado, v.monto_total) AS total
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
          AND v.fecha_pago >= CURRENT_DATE - %s
        ORDER BY v.fecha_pago DESC
    """, (dias,))
    facturas = [
        {"folio": f["folio"], "cliente": f["razon_social"],
         "fecha_pago": f["fecha_pago"], "total": float(f["total"])}
        for f in cur.fetchall()
    ]
    return {
        "n": len(facturas),
        "total": sum(x["total"] for x in facturas),
        "facturas": facturas,
    }


def ventas_periodo(cur, dias=7):
    """Ventas emitidas (netas de NC) en los últimos `dias`."""
    cur.execute("""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(COALESCE(v.monto_total_ajustado, v.monto_total)), 0) AS total
        FROM ventas v
        WHERE v.tipo_documento != 61
          AND v.fecha >= CURRENT_DATE - %s
    """, (dias,))
    f = cur.fetchone()
    return {"n": int(f["n"]), "total": float(f["total"])}


def clientes_inactivos(cur, dias=60, limite=10):
    """Clientes cuya última venta fue hace más de `dias` (posible churn).

    Orden ascendente por días: primero los que recién cruzaron el umbral,
    que son los más recuperables.
    """
    cur.execute("""
        SELECT c.razon_social, MAX(v.fecha) AS ultima_venta,
               (CURRENT_DATE - MAX(v.fecha)) AS dias_inactivo
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
          AND COALESCE(c.estado, '') <> 'incobrable'
        GROUP BY c.razon_social
        HAVING (CURRENT_DATE - MAX(v.fecha)) > %s
        ORDER BY dias_inactivo ASC
        LIMIT %s
    """, (dias, limite))
    return [
        {"cliente": f["razon_social"], "ultima_venta": f["ultima_venta"],
         "dias": int(f["dias_inactivo"])}
        for f in cur.fetchall()
    ]
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `python -m pytest tests/test_briefing_data.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/briefing/data.py tests/test_briefing_data.py
git commit -m "Agrega cobrado reciente, ventas del periodo e inactivos al brief"
```

---

### Task 4: `render.render_markdown`

**Files:**
- Create: `app/briefing/render.py`
- Test: `tests/test_briefing_render.py`

- [ ] **Step 1: Escribir el test que falla**

Create `tests/test_briefing_render.py`:

```python
# tests/test_briefing_render.py
from datetime import date
from app.briefing import render


def _brief_ejemplo():
    return {
        "umbral_vencidas": 30,
        "umbral_reciente": 7,
        "umbral_inactivos": 60,
        "cobranza": {
            "total": 200000,
            "n_facturas": 4,
            "buckets": {"al_dia": 20000, "d1_30": 100000, "d31_60": 50000, "d60_mas": 30000},
        },
        "top_deudores": [{"cliente": "Bar Uno", "deuda": 500000, "n": 3}],
        "vencidas": [{"folio": 1234, "cliente": "Bar Uno", "total": 80000, "dias": 78}],
        "cobrado_reciente": {"n": 2, "total": 100000, "facturas": []},
        "ventas_periodo": {"n": 5, "total": 350000},
        "inactivos": [{"cliente": "Bar Frio", "ultima_venta": "2026-03-01", "dias": 109}],
    }


def test_render_incluye_titulo_con_fecha():
    md = render.render_markdown(_brief_ejemplo(), hoy=date(2026, 6, 18))
    assert "# Brief diario Zigurat — 18/06/2026" in md


def test_render_formatea_pesos_chilenos():
    md = render.render_markdown(_brief_ejemplo(), hoy=date(2026, 6, 18))
    assert "$200.000" in md   # deuda total
    assert "$500.000" in md   # top deudor
    assert "Bar Uno" in md
    assert "Bar Frio" in md


def test_render_sin_deuda_muestra_mensaje_amable():
    brief = _brief_ejemplo()
    brief["top_deudores"] = []
    brief["vencidas"] = []
    md = render.render_markdown(brief, hoy=date(2026, 6, 18))
    assert "Sin deuda pendiente" in md
    assert "Ninguna factura vencida" in md
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_briefing_render.py -v`
Expected: FAIL con ImportError (módulo `render` aún no existe).

- [ ] **Step 3: Implementar `render.py`**

Create `app/briefing/render.py`:

```python
"""Renderiza el dict del brief a Markdown. Función pura, sin BD."""
from datetime import date


def _pesos(n):
    """Formato peso chileno: $1.234.567."""
    if n is None:
        return "$0"
    signo = "-" if n < 0 else ""
    return f"{signo}${abs(int(round(n))):,}".replace(",", ".")


def render_markdown(brief, hoy=None):
    """Convierte el dict del brief en un documento Markdown legible."""
    hoy = hoy or date.today()
    L = [f"# Brief diario Zigurat — {hoy.strftime('%d/%m/%Y')}", ""]

    cob = brief["cobranza"]
    b = cob["buckets"]
    L += [
        "## Cobranza",
        f"- **Deuda total pendiente:** {_pesos(cob['total'])} en {cob['n_facturas']} facturas",
        (f"- Al día: {_pesos(b['al_dia'])} · 1–30 d: {_pesos(b['d1_30'])} · "
         f"31–60 d: {_pesos(b['d31_60'])} · +60 d: {_pesos(b['d60_mas'])}"),
        "",
    ]

    L.append("## Top deudores")
    if brief["top_deudores"]:
        L += ["| Cliente | Deuda | Facturas |", "|---|---:|---:|"]
        for d in brief["top_deudores"]:
            L.append(f"| {d['cliente']} | {_pesos(d['deuda'])} | {d['n']} |")
    else:
        L.append("Sin deuda pendiente. 🎉")
    L.append("")

    L.append(f"## Facturas vencidas (+{brief['umbral_vencidas']} días)")
    if brief["vencidas"]:
        L += ["| Folio | Cliente | Total | Días |", "|---|---|---:|---:|"]
        for f in brief["vencidas"]:
            L.append(f"| {f['folio']} | {f['cliente']} | {_pesos(f['total'])} | {f['dias']} |")
    else:
        L.append("Ninguna factura vencida sobre el umbral. 👍")
    L.append("")

    cr = brief["cobrado_reciente"]
    vp = brief["ventas_periodo"]
    L += [
        f"## Cobrado últimos {brief['umbral_reciente']} días",
        f"- {cr['n']} facturas · {_pesos(cr['total'])}",
        "",
        f"## Ventas últimos {brief['umbral_reciente']} días",
        f"- {vp['n']} facturas · {_pesos(vp['total'])}",
        "",
    ]

    L.append(f"## Clientes inactivos (+{brief['umbral_inactivos']} días)")
    if brief["inactivos"]:
        L += ["| Cliente | Última venta | Días |", "|---|---|---:|"]
        for c in brief["inactivos"]:
            L.append(f"| {c['cliente']} | {c['ultima_venta']} | {c['dias']} |")
    else:
        L.append("Ningún cliente inactivo sobre el umbral. 👍")
    L.append("")

    return "\n".join(L)
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_briefing_render.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Correr toda la suite (no romper nada existente)**

Run: `python -m pytest -q`
Expected: PASS — los tests nuevos del brief más los ya existentes (`test_config`, `test_orchestrator`, etc.).

- [ ] **Step 6: Commit**

```bash
git add app/briefing/render.py tests/test_briefing_render.py
git commit -m "Agrega render del brief diario a Markdown"
```

---

### Task 5: Script de entrada `scripts/generar_brief.py`

**Files:**
- Create: `scripts/generar_brief.py`

> Este script es "pegamento" (conecta a la BD real y orquesta las funciones ya
> testeadas). No lleva test unitario; se verifica ejecutándolo contra la BD.

- [ ] **Step 1: Crear el script**

Create `scripts/generar_brief.py`:

```python
#!/usr/bin/env python3
"""Genera el brief diario de Zigurat (solo lectura) y lo guarda en briefs/.

Uso:
    python scripts/generar_brief.py
"""
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)

# Permite importar app.* al ejecutar como script suelto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_URL, PROJECT_ROOT  # noqa: E402
from app.briefing import data, render          # noqa: E402


def _recolectar(cur):
    """Junta todas las secciones del brief en un dict."""
    return {
        "umbral_vencidas": 30,
        "umbral_reciente": 7,
        "umbral_inactivos": 60,
        "cobranza": data.resumen_cobranza(cur),
        "top_deudores": data.top_deudores(cur, limite=5),
        "vencidas": data.facturas_vencidas(cur, dias=30),
        "cobrado_reciente": data.cobrado_reciente(cur, dias=7),
        "ventas_periodo": data.ventas_periodo(cur, dias=7),
        "inactivos": data.clientes_inactivos(cur, dias=60),
    }


def main():
    try:
        conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: no se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            brief = _recolectar(cur)
    finally:
        conn.close()

    md = render.render_markdown(brief)

    destino_dir = PROJECT_ROOT / "briefs"
    destino_dir.mkdir(exist_ok=True)
    destino = destino_dir / f"{date.today().isoformat()}.md"
    destino.write_text(md, encoding="utf-8")

    print(md)
    print(f"\nBrief guardado en: {destino}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Ejecutar contra la BD real y verificar**

Run: `python scripts/generar_brief.py`
Expected: imprime el brief en Markdown y la línea `Brief guardado en: ...\briefs\YYYY-MM-DD.md`. Abrir el `.md` generado y verificar que las cifras de deuda total coinciden con `/consultar-ventas → pendientes` (deben cuadrar, porque usan la misma regla de `fecha_pago IS NULL`).

- [ ] **Step 3: Commit**

```bash
git add scripts/generar_brief.py
git commit -m "Agrega script generador del brief diario"
```

---

### Task 6: Tarea Programada de Windows `scripts/instalar_tarea_brief.ps1`

**Files:**
- Create: `scripts/instalar_tarea_brief.ps1`

> Mismo patrón exacto que `scripts/instalar_tarea_backup.ps1` (idempotente,
> `StartWhenAvailable`, ruta absoluta de python). Cambia: nombre de tarea,
> script destino, hora 08:00.

- [ ] **Step 1: Crear el instalador**

Create `scripts/instalar_tarea_brief.ps1`:

```powershell
# instalar_tarea_brief.ps1 - Zigurat ERP
# Crea/actualiza la Tarea Programada "Zigurat - Brief Diario" (idempotente:
# re-ejecutar este script actualiza la tarea).
#
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea_brief.ps1

$ErrorActionPreference = "Stop"

$proyecto = Split-Path -Parent $PSScriptRoot
$script = Join-Path $proyecto "scripts\generar_brief.py"
if (-not (Test-Path $script)) {
    throw "No se encontró $script. Ejecuta este instalador desde el repo del proyecto."
}

# Ruta absoluta de python.exe: la tarea no depende del PATH del momento de ejecución.
$python = (Get-Command python -ErrorAction Stop).Source

$accion = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $proyecto
$trigger = New-ScheduledTaskTrigger -Daily -At "08:00"
# StartWhenAvailable: si el notebook estaba apagado a las 08:00, corre al encenderlo.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "Zigurat - Brief Diario" -Action $accion -Trigger $trigger `
    -Settings $settings -Description "Brief diario de cobranza/ventas de Zigurat (proyecto Agente_Facturas)" `
    -Force | Out-Null

Write-Host "Tarea 'Zigurat - Brief Diario' instalada: diaria 08:00, StartWhenAvailable."
Write-Host "Python: $python"
Write-Host "Script: $script"

# Ejecución de prueba inmediata para validar la instalación.
Write-Host ""
Write-Host "Ejecutando la tarea ahora como prueba..."
Start-ScheduledTask -TaskName "Zigurat - Brief Diario"
Start-Sleep -Seconds 15
$info = Get-ScheduledTaskInfo -TaskName "Zigurat - Brief Diario"
Write-Host "Ultima ejecucion: $($info.LastRunTime) | Resultado: $($info.LastTaskResult) (0 = OK)"
```

- [ ] **Step 2: Ejecutar el instalador y verificar**

Run: `powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea_brief.ps1`
Expected: imprime "Tarea 'Zigurat - Brief Diario' instalada" y `Resultado: 0 (0 = OK)`. Verificar que se generó el `briefs/YYYY-MM-DD.md` de hoy con la corrida de prueba.

- [ ] **Step 3: Commit**

```bash
git add scripts/instalar_tarea_brief.ps1
git commit -m "Agrega instalador de tarea programada del brief diario 08:00"
```

---

### Task 7: Documentar en CLAUDE.md

**Files:**
- Modify: `.claude/CLAUDE.md`

- [ ] **Step 1: Agregar la sección de documentación**

Agregar una nueva sección en `.claude/CLAUDE.md` (después de la sección "Backup de la base de datos", siguiendo el mismo estilo):

```markdown
## Brief diario automático

Reporte de negocio generado cada mañana (Tarea Programada de Windows
"Zigurat - Brief Diario", 08:00, `StartWhenAvailable` igual que el backup):

- **Qué incluye:** deuda total con desglose por antigüedad, top 5 deudores,
  facturas vencidas (+30 días), cobrado y ventas de los últimos 7 días,
  clientes inactivos (+60 días).
- **Solo lectura:** no modifica la BD. Reutiliza las reglas canónicas de
  cobranza (`fecha_pago IS NULL`, excluye NC e `incobrable`).
- **Capa de datos:** `app/briefing/data.py` (funciones reutilizables, testeadas
  con cursor falso en `tests/test_briefing_data.py`). Render en
  `app/briefing/render.py`.
- **Salida:** `briefs/YYYY-MM-DD.md` (historial committeable del negocio).
- **Generar manualmente:** `python scripts/generar_brief.py`
- **Reinstalar la tarea:**
  `powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea_brief.ps1`
```

- [ ] **Step 2: Commit**

```bash
git add .claude/CLAUDE.md
git commit -m "Documenta el brief diario en CLAUDE.md"
```

---

## Self-Review

**1. Cobertura del spec (lo que pidió el usuario para la prioridad #1 — "quién te debe, qué entró al banco, alertas de morosos/inactivos"):**
- "Quién te debe" → `resumen_cobranza` + `top_deudores` (Tasks 1–2). ✅
- "Qué entró" → `cobrado_reciente` (facturas marcadas pagadas en 7 días; usa `fecha_pago`, fuente de verdad del proyecto, en vez de la tabla `movimientos_banco` cuyo esquema de montos no fue verificado). ✅ con nota: si se quiere el detalle de transferencias bancarias, es una mejora futura que requiere leer el esquema real de `movimientos_banco`.
- "Morosos" → `facturas_vencidas(dias=30)` (Task 2). ✅
- "Inactivos" → `clientes_inactivos(dias=60)` (Task 3). ✅
- Automatización diaria → Task 6 (Tarea Programada 08:00). ✅

**2. Sin placeholders:** todo el código (data, render, script, ps1, tests) está completo y literal. Sin "TODO" ni "implementar después". ✅

**3. Consistencia de tipos/nombres:** las claves de los dicts que produce `data.py` (`total`, `n_facturas`, `buckets`, `cliente`, `deuda`, `n`, `folio`, `dias`, `facturas`, `ultima_venta`) coinciden exactamente con las que consume `render.py` y arma `_recolectar` en `generar_brief.py`. Columnas SQL (`razon_social`, `rut_cliente`, `fecha_pago`, `monto_total_ajustado`, `monto_total`, `tipo_documento`, `estado`) verificadas contra `query_ventas.py` y `flujo_caja.py`. ✅

**Riesgo conocido / supuesto a validar en Task 5:** `CURRENT_DATE - %s` con parámetro entero asume resta date−int en Postgres (válida). Si la columna `ventas.fecha` fuese `timestamp` en vez de `date`, el cast de `dias` a `int` sigue funcionando porque `CURRENT_DATE - fecha` igual da entero de días. La corrida real de Task 5 lo confirma.

---

## Fuera de alcance (fases siguientes, NO en este plan)

- **Fase 2 — Chat capaz con acciones confirmadas:** exponer `app/briefing/data.py` (y `flujo_caja`, `costos`) como herramientas MCP del agente, y agregar herramientas de **escritura con confirmación** (conciliar, registrar gasto). Requiere diseño de guardarraíles (revertir `bypassPermissions`, gates de confirmación, auditoría).
- **Fase 3 — Alertas proactivas:** comparar el brief de hoy contra el de ayer (`briefs/` ya da el historial) y notificar solo los cambios relevantes (nueva factura vencida, caída de ventas, insumo que subió) por Telegram/WhatsApp/correo.
- **Narración con LLM:** resumen ejecutivo en lenguaje natural arriba del brief, una vez exista la capa de herramientas MCP de la Fase 2.
