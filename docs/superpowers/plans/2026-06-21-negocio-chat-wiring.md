# Conectar las calculadoras al chat (Fase 2a — Parte 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exponer las funciones de datos de `app/negocio/` y `app/briefing/data.py` como herramientas MCP que el agente del chat usa con preferencia sobre el SQL crudo, para responder deuda/ventas/flujo/costos con números exactos.

**Architecture:** Un segundo servidor MCP in-process ("negocio") con 11 herramientas que envuelven las funciones de datos ya probadas (mismo patrón que `app/agent/publish_tools.py`). Se registra en `orchestrator.py` junto al lienzo y postgres, y el system prompt instruye al agente a usar estas herramientas para los temas de negocio. Solo lectura; se mantiene `mcp__postgres__query` para ad-hoc.

**Tech Stack:** Python 3.x, claude-agent-sdk (`create_sdk_mcp_server`, `tool`), psycopg2, pytest.

**Diseño de referencia:** `docs/superpowers/specs/2026-06-20-chat-analisis-confiable-design.md`
**Depende de:** la Parte 1 (capa de datos `app/negocio/` + `deuda_cliente`), ya construida en esta misma rama.

---

## File Structure

| Archivo | Responsabilidad | Nuevo/Modificado |
|---|---|---|
| `app/agent/tools_negocio.py` | Servidor MCP "negocio": envuelve las funciones de datos como `@tool`. | Nuevo |
| `tests/test_tools_negocio.py` | Verifica que el servidor registra los 11 nombres de tools. | Nuevo |
| `app/agent/orchestrator.py` | Registra el servidor "negocio" y sus tools en `allowed_tools`. | Modificado |
| `tests/test_orchestrator.py` | Verifica que los tools de negocio quedan permitidos. | Modificado |
| `app/agent/system_prompt.py` | Agrega la "regla de oro" (preferir herramientas de negocio). | Modificado |
| `tests/test_system_prompt.py` | Verifica que el prompt menciona las herramientas. | Modificado |

**Convenciones:** pytest con `python -m pytest`. Commits en español. `git add` solo con las rutas indicadas. Patrón de referencia para el servidor MCP: `app/agent/publish_tools.py` (`build_lienzo_server`).

---

### Task 1: Servidor MCP `app/agent/tools_negocio.py`

**Files:**
- Create: `app/agent/tools_negocio.py`
- Test: `tests/test_tools_negocio.py`

- [ ] **Step 1: Crear `tests/test_tools_negocio.py`**

```python
# tests/test_tools_negocio.py
from app.agent.tools_negocio import build_negocio_server


def test_negocio_server_registra_los_tools():
    server, names = build_negocio_server()
    assert server is not None
    assert len(names) == 11
    for esperado in [
        "mcp__negocio__deuda_total",
        "mcp__negocio__deuda_cliente",
        "mcp__negocio__ranking_deudores",
        "mcp__negocio__facturas_vencidas",
        "mcp__negocio__ventas_total",
        "mcp__negocio__ranking_clientes",
        "mcp__negocio__ventas_cliente",
        "mcp__negocio__ventas_producto",
        "mcp__negocio__flujo_caja",
        "mcp__negocio__costos_sku",
        "mcp__negocio__margenes",
    ]:
        assert esperado in names
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_tools_negocio.py -v`
Expected: FAIL con ImportError (módulo no existe).

- [ ] **Step 3: Crear `app/agent/tools_negocio.py`**

```python
"""Servidor MCP in-process 'negocio': herramientas de datos de SOLO LECTURA que
el agente usa para responder con números exactos (deuda, ventas, flujo, costos).

Cada herramienta abre su propia conexión de solo lectura y reutiliza las
funciones ya probadas de app/briefing/data.py y app/negocio/. Mismo patrón que
app/agent/publish_tools.py (build_lienzo_server).
"""
import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import DB_URL
from app.briefing import data as deuda_data
from app.negocio import ventas as ventas_data
from app.negocio import costos as costos_data
from app.negocio import flujo as flujo_data


def _con_cursor(fn, *args, **kwargs):
    """Abre conexión RealDictCursor, ejecuta fn(cur, ...), cierra y devuelve."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return fn(cur, *args, **kwargs)
    finally:
        conn.close()


def _pesos(n):
    if n is None:
        return "$0"
    return f"${int(round(float(n))):,}".replace(",", ".")


def _texto(s):
    return {"content": [{"type": "text", "text": s}]}


def build_negocio_server():
    """Construye el servidor MCP 'negocio'. Devuelve (server, lista_de_tool_names)."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("deuda_total", "Deuda total pendiente de cobro con desglose por antigüedad.", {})
    async def deuda_total(args):
        r = _con_cursor(deuda_data.resumen_cobranza)
        b = r["buckets"]
        return _texto(
            f"Deuda total pendiente: {_pesos(r['total'])} en {r['n_facturas']} facturas. "
            f"Al día {_pesos(b['al_dia'])}, 1-30d {_pesos(b['d1_30'])}, "
            f"31-60d {_pesos(b['d31_60'])}, +60d {_pesos(b['d60_mas'])}."
        )

    @tool("deuda_cliente", "Deuda pendiente de un cliente, por nombre o RUT.", {"nombre": str})
    async def deuda_cliente(args):
        r = _con_cursor(deuda_data.deuda_cliente, args["nombre"])
        if r["n_facturas"] == 0:
            return _texto(f"{args['nombre']}: sin deuda pendiente.")
        lineas = [f"- Folio {f['folio']} ({f['fecha']}): {_pesos(f['total'])}, {f['dias']}d"
                  for f in r["facturas"]]
        return _texto(f"{args['nombre']}: {_pesos(r['total'])} en {r['n_facturas']} facturas.\n"
                      + "\n".join(lineas))

    @tool("ranking_deudores", "Top N clientes por deuda pendiente.", {"limite": int})
    async def ranking_deudores(args):
        r = _con_cursor(deuda_data.top_deudores, args.get("limite", 5))
        if not r:
            return _texto("Sin deuda pendiente.")
        return _texto("\n".join(
            f"{i+1}. {d['cliente']}: {_pesos(d['deuda'])} ({d['n']} facturas)"
            for i, d in enumerate(r)))

    @tool("facturas_vencidas", "Facturas pendientes con más de N días (morosos).", {"dias": int})
    async def facturas_vencidas(args):
        r = _con_cursor(deuda_data.facturas_vencidas, args.get("dias", 30))
        if not r:
            return _texto("Ninguna factura vencida sobre el umbral.")
        return _texto("\n".join(
            f"- Folio {f['folio']} {f['cliente']}: {_pesos(f['total'])}, {f['dias']}d"
            for f in r))

    @tool("ventas_total", "Total vendido. Opcional: rango desde/hasta (YYYY-MM-DD).",
          {"desde": str, "hasta": str})
    async def ventas_total(args):
        r = _con_cursor(ventas_data.total, args.get("desde"), args.get("hasta"))
        periodo = f" entre {r['desde']} y {r['hasta']}" if r["desde"] and r["hasta"] else ""
        return _texto(f"Ventas{periodo}: {_pesos(r['total'])} en {r['n']} facturas.")

    @tool("ranking_clientes", "Top N clientes por ventas.", {"limite": int})
    async def ranking_clientes(args):
        r = _con_cursor(ventas_data.ranking, args.get("limite", 10))
        if not r:
            return _texto("Sin ventas.")
        return _texto("\n".join(f"{i+1}. {c['cliente']}: {_pesos(c['total'])}"
                                for i, c in enumerate(r)))

    @tool("ventas_cliente", "Ventas de un cliente, por nombre.", {"nombre": str})
    async def ventas_cliente(args):
        r = _con_cursor(ventas_data.por_cliente, args["nombre"])
        return _texto(f"{args['nombre']}: {_pesos(r['total_real'])} en {r['n_facturas']} "
                      f"facturas ({r['n_notas_credito']} notas de crédito).")

    @tool("ventas_producto", "Buscar ventas por nombre de producto.", {"nombre": str})
    async def ventas_producto(args):
        r = _con_cursor(ventas_data.por_producto, args["nombre"])
        if not r:
            return _texto(f"Sin ventas que coincidan con '{args['nombre']}'.")
        unidades = sum((x["cantidad"] or 0) for x in r)
        return _texto(f"'{args['nombre']}': {len(r)} líneas de venta, {unidades} unidades en total.")

    @tool("flujo_caja", "Proyección de caja a 4 semanas (ingresos esperados − gastos). "
                        "Opcional: saldo_inicial.", {"saldo_inicial": float})
    async def flujo_caja(args):
        r = _con_cursor(flujo_data.proyectar_flujo, args.get("saldo_inicial"))
        lineas = [
            f"- {s['label']}: ingresos {_pesos(s['ingresos'])}, egresos {_pesos(s['egresos'])}, "
            f"saldo {_pesos(s['saldo_acumulado'])}" + (" [RIESGO]" if s["riesgo"] else "")
            for s in r["semanas"]
        ]
        return _texto(
            f"Flujo de caja 4 semanas (saldo inicial {_pesos(r['saldo_inicial'])}):\n"
            + "\n".join(lineas)
            + f"\nTotales: ingresos {_pesos(r['total_ingresos'])}, "
              f"egresos {_pesos(r['total_egresos'])}.")

    @tool("costos_sku", "Costo unitario por SKU. Opcional: filtrar por receta.", {"receta": str})
    async def costos_sku(args):
        r = _con_cursor(costos_data.costos_sku, args.get("receta"))
        if not r:
            return _texto("Sin SKUs cargados.")
        return _texto("\n".join(
            f"- {s['codigo']} {s['cerveza']} {s['formato']}: costo {_pesos(s['costo_total'])}"
            for s in r))

    @tool("margenes", "Margen por cerveza/formato (precio venta − costo; solo barriles). "
                      "Opcional: filtrar por receta.", {"receta": str})
    async def margenes(args):
        r = _con_cursor(costos_data.margenes, args.get("receta"))
        if not r:
            return _texto("Sin SKUs cargados.")
        lineas = []
        for m in r:
            if m["margen"] is None:
                lineas.append(f"- {m['cerveza']} {m['formato']}: costo {_pesos(m['costo_total'])} "
                              f"(sin precio de venta confirmado)")
            else:
                lineas.append(f"- {m['cerveza']} {m['formato']}: precio {_pesos(m['precio_venta'])} "
                              f"− costo {_pesos(m['costo_total'])} = margen {_pesos(m['margen'])} "
                              f"({m['margen_pct']}%)")
        return _texto("\n".join(lineas))

    server = create_sdk_mcp_server(name="negocio", version="1.0.0", tools=[
        deuda_total, deuda_cliente, ranking_deudores, facturas_vencidas,
        ventas_total, ranking_clientes, ventas_cliente, ventas_producto,
        flujo_caja, costos_sku, margenes,
    ])
    tool_names = [
        "mcp__negocio__deuda_total", "mcp__negocio__deuda_cliente",
        "mcp__negocio__ranking_deudores", "mcp__negocio__facturas_vencidas",
        "mcp__negocio__ventas_total", "mcp__negocio__ranking_clientes",
        "mcp__negocio__ventas_cliente", "mcp__negocio__ventas_producto",
        "mcp__negocio__flujo_caja", "mcp__negocio__costos_sku", "mcp__negocio__margenes",
    ]
    return server, tool_names
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_tools_negocio.py -v`
Expected: PASS (1 test). Si falla al construir el servidor por un esquema `{}` vacío en `deuda_total`, reportarlo (no cambiar la lógica por cuenta propia).

- [ ] **Step 5: Commit**

```bash
git add app/agent/tools_negocio.py tests/test_tools_negocio.py
git commit -m "Agrega servidor MCP de negocio con 11 herramientas de datos"
```

---

### Task 2: Registrar el servidor en `orchestrator.py`

**Files:**
- Modify: `app/agent/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Agregar el test al final de `tests/test_orchestrator.py`**

```python
def test_build_options_incluye_tools_de_negocio():
    options = orchestrator._build_options(Collector())
    assert "mcp__negocio__deuda_total" in options.allowed_tools
    assert "mcp__negocio__flujo_caja" in options.allowed_tools
    # No se rompe lo anterior:
    assert "mcp__postgres__query" in options.allowed_tools
    assert "mcp__lienzo__publicar_kpi" in options.allowed_tools
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL en `test_build_options_incluye_tools_de_negocio` (los tools de negocio aún no están en allowed_tools).

- [ ] **Step 3: Modificar `app/agent/orchestrator.py`**

Agregar el import después de la línea `from app.agent.system_prompt import SYSTEM_PROMPT`:

```python
from app.agent.tools_negocio import build_negocio_server
```

Reemplazar la función `_build_options` completa por:

```python
def _build_options(collector: Collector) -> ClaudeAgentOptions:
    lienzo_server, lienzo_tools = build_lienzo_server(collector)
    negocio_server, negocio_tools = build_negocio_server()
    claude_path = shutil.which("claude")
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        cwd=str(PROJECT_ROOT),
        mcp_servers={
            "lienzo": lienzo_server,
            "negocio": negocio_server,
            "postgres": _postgres_server(),
        },
        allowed_tools=lienzo_tools + negocio_tools + ["mcp__postgres__query"],
        permission_mode="bypassPermissions",
        max_turns=MAX_TURNS,
        cli_path=claude_path,
    )
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (los previos + el nuevo).

- [ ] **Step 5: Commit**

```bash
git add app/agent/orchestrator.py tests/test_orchestrator.py
git commit -m "Registra el servidor de negocio en el orquestador del agente"
```

---

### Task 3: La "regla de oro" en `system_prompt.py`

**Files:**
- Modify: `app/agent/system_prompt.py`
- Test: `tests/test_system_prompt.py`

- [ ] **Step 1: Agregar el test al final de `tests/test_system_prompt.py`**

```python
def test_system_prompt_menciona_herramientas_de_negocio():
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "mcp__negocio__" in SYSTEM_PROMPT
    assert "deuda_total" in SYSTEM_PROMPT
    assert "flujo_caja" in SYSTEM_PROMPT
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_system_prompt.py -v`
Expected: FAIL en el test nuevo (el prompt aún no menciona las herramientas).

- [ ] **Step 3: Modificar `app/agent/system_prompt.py`**

En el string `SYSTEM_PROMPT`, insertar el siguiente bloque justo ANTES de la línea
que empieza con `PUBLICAR RESULTADOS:` (es decir, entre la sección de estructura
de facturación/tablas y la de publicar resultados):

```
HERRAMIENTAS DE NEGOCIO (úsalas SIEMPRE para estos temas; no improvises SQL):
Para deuda, cobranza, ventas, flujo de caja y costos, usa la herramienta
mcp__negocio__* correspondiente en lugar de escribir SQL a mano:
- Deuda: mcp__negocio__deuda_total, mcp__negocio__deuda_cliente,
  mcp__negocio__ranking_deudores, mcp__negocio__facturas_vencidas.
- Ventas: mcp__negocio__ventas_total, mcp__negocio__ranking_clientes,
  mcp__negocio__ventas_cliente, mcp__negocio__ventas_producto.
- Flujo de caja a 4 semanas: mcp__negocio__flujo_caja.
- Costos y márgenes por SKU: mcp__negocio__costos_sku, mcp__negocio__margenes.
Estas herramientas ya aplican las reglas canónicas (montos ajustados, exclusión
de notas de crédito, estado de pago por fecha_pago), así que son la fuente
confiable. Reserva mcp__postgres__query SOLO para preguntas ad-hoc que ninguna
herramienta de negocio cubra.

```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_system_prompt.py -v`
Expected: PASS (los previos + el nuevo).

- [ ] **Step 5: Correr toda la suite**

Run: `python -m pytest -q`
Expected: PASS — todos los tests del proyecto (incluyendo los 64 previos de la Parte 1).

- [ ] **Step 6: Commit**

```bash
git add app/agent/system_prompt.py tests/test_system_prompt.py
git commit -m "Agrega la regla de oro: preferir herramientas de negocio sobre SQL"
```

---

### Task 4: Verificación de integración con el agente real

**Files:** (ninguno — verificación)

Esta tarea comprueba que el agente realmente usa las herramientas y da el número
correcto. Llama al agente real (usa el CLI `claude` del proyecto), así que puede
tardar y requiere conexión.

- [ ] **Step 1: Preguntarle la deuda total al agente**

Run:
```bash
python -c "from app.agent import orchestrator; from app.canvas.artifacts import Collector; print(orchestrator.run('¿Cuál es la deuda total pendiente de cobro?', Collector()))"
```
Expected: el agente responde con un monto de deuda total. Anotar el monto.

- [ ] **Step 2: Comparar con la fuente canónica**

Run:
```bash
python .claude/skills/consultar-ventas/scripts/query_ventas.py pendientes
```
Expected: la última línea muestra el total pendiente. **Debe coincidir** con el
monto que dio el agente en el Step 1 (misma regla `fecha_pago IS NULL`).

- [ ] **Step 3: Reportar resultado**

Si los montos coinciden: la integración funciona. Si el agente no está disponible
en este entorno (error de CLI/red), reportar DONE_WITH_CONCERNS con el detalle —
la verificación se hará manualmente desde el dashboard. NO modificar código para
"arreglar" esto sin confirmación.

No hay commit en esta tarea (es verificación).

---

## Self-Review

**1. Cobertura del spec (parte de integración):**
- Servidor MCP "negocio" con las 11 herramientas → Task 1. ✅
- Cableado en `orchestrator.py` (mcp_servers + allowed_tools) → Task 2. ✅
- "Regla de oro" en `system_prompt.py` → Task 3. ✅
- Verificación de integración (deuda del chat == `/consultar-ventas`) → Task 4. ✅
- Mantener `mcp__postgres__query` para ad-hoc → conservado en Task 2 y reforzado en el prompt (Task 3). ✅
- 100% solo lectura, sin cambios de permisos → no se toca `permission_mode`. ✅

**2. Sin placeholders:** todo el código está completo (servidor, edición del orquestador, bloque del prompt, tests). ✅

**3. Consistencia de tipos/nombres:** los 11 nombres `mcp__negocio__*` del servidor (Task 1) coinciden con los que verifican `test_tools_negocio.py` (Task 1), `test_orchestrator.py` (Task 2) y `test_system_prompt.py` (Task 3). Las funciones de datos invocadas (`deuda_data.resumen_cobranza`, `deuda_data.deuda_cliente`, `deuda_data.top_deudores`, `deuda_data.facturas_vencidas`, `ventas_data.total/ranking/por_cliente/por_producto`, `flujo_data.proyectar_flujo`, `costos_data.costos_sku/margenes`) existen todas en la Parte 1 con esas firmas. ✅

**Riesgo conocido (Task 1):** el esquema vacío `{}` en `deuda_total` se asume válido para `@tool`. Si el SDK lo rechaza, el test del Step 4 lo detecta al construir el servidor. **Riesgo (Task 4):** la verificación de integración depende del CLI `claude`; si no está disponible en el entorno del ejecutor, se degrada a verificación manual.

---

## Fuera de alcance (Fase 2b)

- Herramientas de escritura con confirmación (registrar gasto, marcar pago, conciliar).
- Memoria de conversación entre mensajes del chat.
