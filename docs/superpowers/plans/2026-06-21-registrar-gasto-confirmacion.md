# Registrar gasto con confirmación — Plan de implementación (Fase 2b, acción 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que el chat del dashboard registre un gasto (`cuentas_por_pagar`) desde lenguaje natural mediante el patrón *propose / confirm / execute*: el agente solo propone una tarjeta, el usuario confirma con un botón, y un endpoint determinista escribe.

**Architecture:** El agente llama a una herramienta MCP `proponer_gasto` que NO escribe en la BD: valida con la capa determinista y publica un `Artifact(tipo="accion")` en el `Collector`. El frontend dibuja ese artefacto como tarjeta con botones Confirmar/Cancelar. Confirmar hace `POST /api/registrar-gasto`, y un handler determinista valida de nuevo y ejecuta el `INSERT`. El agente nunca escribe; el único camino de escritura es el endpoint.

**Tech Stack:** Python 3.x, psycopg2 (RealDictCursor), `claude_agent_sdk` (`create_sdk_mcp_server`, `tool`), `http.server` (dashboard), HTML/JS vanilla + Chart.js. Tests con pytest y cursor falso (sin BD).

## Global Constraints

- Responder/comentar/commitear en español; código en inglés camelCase para variables nuevas si las hubiera (el módulo sigue el estilo existente de `app/negocio/`).
- El agente NO escribe en la BD. Su única herramienta nueva (`proponer_gasto`) solo publica un artefacto. NO cambiar `permission_mode` en el orquestador.
- Toda escritura usa parámetros psycopg2 (`%s`), nunca string-format de SQL.
- El conector del dashboard `get_conn()` devuelve `RealDictCursor`: `cur.fetchone()` retorna `dict`, así que leer el id con `["id"]` (no `[0]`).
- Formato de monto chileno: `validar_gasto` acepta `"185.000"`, `"185000"` o número; los puntos son separadores de miles, la coma es decimal (igual que `agregar_gasto.py`).
- TDD estricto: test que falla → implementación mínima → test verde → commit. Un commit por tarea.
- Comando de tests del proyecto: `python -m pytest <ruta> -q`.

---

### Task 1: Habilitar el tipo de artefacto `accion`

El diseño asumía que no había que tocar `artifacts.py`, pero `Artifact.__post_init__` valida `tipo` contra `ARTIFACT_TYPES` y lanza `ValueError` para cualquier valor fuera del set. Sin este cambio, publicar un artefacto `accion` explota. Esta tarea lo habilita.

**Files:**
- Modify: `app/canvas/artifacts.py:7` (constante `ARTIFACT_TYPES`)
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Produces: `Artifact(tipo="accion", titulo=..., payload=...)` deja de lanzar `ValueError`. El set `ARTIFACT_TYPES` pasa a incluir `"accion"`.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `tests/test_artifacts.py`:

```python
def test_permite_tipo_accion():
    from app.canvas.artifacts import Artifact
    art = Artifact(tipo="accion", titulo="Confirmar gasto", payload={"x": 1})
    assert art.tipo == "accion"
    assert art.titulo == "Confirmar gasto"


def test_rechaza_tipo_desconocido():
    import pytest
    from app.canvas.artifacts import Artifact
    with pytest.raises(ValueError):
        Artifact(tipo="inventado", titulo="x", payload={})
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python -m pytest tests/test_artifacts.py::test_permite_tipo_accion -v`
Expected: FAIL con `ValueError: tipo de artefacto inválido: accion`

- [ ] **Step 3: Implementar el cambio mínimo**

En `app/canvas/artifacts.py` línea 7, reemplazar:

```python
ARTIFACT_TYPES = {"kpi", "grafico", "tabla", "informe"}
```

por:

```python
ARTIFACT_TYPES = {"kpi", "grafico", "tabla", "informe", "accion"}
```

- [ ] **Step 4: Correr los tests del archivo y verificar verde**

Run: `python -m pytest tests/test_artifacts.py -v`
Expected: PASS (incluye los dos nuevos)

- [ ] **Step 5: Commit**

```bash
git add app/canvas/artifacts.py tests/test_artifacts.py
git commit -m "Permite el tipo de artefacto accion en el lienzo"
```

---

### Task 2: Capa determinista de gastos (`app/negocio/gastos.py`)

Lógica pura de validación + el `INSERT`. Es el gatekeeper y la única pieza que toca la BD. Replica el SQL y la normalización de monto de `.claude/skills/agregar-gasto/scripts/agregar_gasto.py`.

**Files:**
- Create: `app/negocio/gastos.py`
- Test: `tests/test_negocio_gastos.py`

**Interfaces:**
- Produces:
  - `validar_gasto(descripcion, monto, fecha, proveedor=None, categoria=None) -> dict` con claves `{"descripcion": str, "monto": float, "fecha": str(YYYY-MM-DD), "proveedor": str|None, "categoria": str|None}`. Lanza `ValueError` con mensaje claro si algo es inválido.
  - `registrar_gasto(cur, descripcion, monto, fecha, proveedor, categoria) -> int` (ejecuta el INSERT con el cursor recibido y devuelve el id). Espera un cursor cuyo `fetchone()` devuelva un dict con clave `"id"` (RealDictCursor).

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_negocio_gastos.py`:

```python
# tests/test_negocio_gastos.py
import pytest
from app.negocio import gastos


class FakeCursor:
    """Cursor falso estilo RealDictCursor: fetchone devuelve un dict."""

    def __init__(self, row):
        self._row = row
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row


def test_validar_gasto_normaliza_monto_chileno():
    r = gastos.validar_gasto("Luz", "185.000", "2026-06-30")
    assert r == {"descripcion": "Luz", "monto": 185000.0,
                 "fecha": "2026-06-30", "proveedor": None, "categoria": None}


def test_validar_gasto_acepta_monto_numerico_y_campos_opcionales():
    r = gastos.validar_gasto("Arriendo", 850000, "2026-07-05",
                             proveedor="Prop SA", categoria="arriendo")
    assert r["monto"] == 850000.0
    assert r["proveedor"] == "Prop SA"
    assert r["categoria"] == "arriendo"


def test_validar_gasto_rechaza_descripcion_vacia():
    with pytest.raises(ValueError):
        gastos.validar_gasto("   ", "1000", "2026-06-30")


def test_validar_gasto_rechaza_monto_no_numerico():
    with pytest.raises(ValueError):
        gastos.validar_gasto("Luz", "abc", "2026-06-30")


def test_validar_gasto_rechaza_monto_cero_o_negativo():
    with pytest.raises(ValueError):
        gastos.validar_gasto("Luz", "0", "2026-06-30")


def test_validar_gasto_rechaza_fecha_mala():
    with pytest.raises(ValueError):
        gastos.validar_gasto("Luz", "1000", "30/06/2026")


def test_registrar_gasto_devuelve_id_y_usa_parametros():
    cur = FakeCursor({"id": 42})
    new_id = gastos.registrar_gasto(cur, "Luz", 185000.0, "2026-06-30", None, "servicios")
    assert new_id == 42
    # El INSERT va parametrizado, en el orden de columnas de cuentas_por_pagar
    assert cur.params == ("Luz", None, 185000.0, "2026-06-30", "servicios")
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_negocio_gastos.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.negocio.gastos'`

- [ ] **Step 3: Implementar `app/negocio/gastos.py`**

```python
"""Capa determinista de gastos (cuentas por pagar).

`validar_gasto` es una función pura (gatekeeper) que normaliza y valida los
datos antes de cualquier escritura. `registrar_gasto` ejecuta el INSERT con un
cursor que recibe (la conexión y el commit los maneja quien llama). Replica el
SQL y la normalización de monto de la skill agregar-gasto.
"""
from datetime import datetime


def _normalizar_monto(monto):
    """Convierte un monto en float. Acepta número o string en formato chileno
    ('185.000' = miles con punto, coma decimal). Devuelve None si no es válido."""
    if monto is None:
        return None
    if isinstance(monto, (int, float)):
        return float(monto)
    s = str(monto).strip()
    if not s:
        return None
    # Quita símbolo/espacios; punto = separador de miles, coma = decimal.
    s = s.replace("$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def validar_gasto(descripcion, monto, fecha, proveedor=None, categoria=None):
    """Valida y normaliza los datos de un gasto. Lanza ValueError si algo falla."""
    desc = (descripcion or "").strip()
    if not desc:
        raise ValueError("La descripción del gasto no puede estar vacía.")

    monto_limpio = _normalizar_monto(monto)
    if monto_limpio is None or monto_limpio <= 0:
        raise ValueError(f"Monto inválido: {monto!r}. Debe ser un número mayor que 0.")

    try:
        fecha_d = datetime.strptime(str(fecha).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValueError(f"Fecha inválida: {fecha!r}. Formato esperado: YYYY-MM-DD.")

    return {
        "descripcion": desc,
        "monto": monto_limpio,
        "fecha": fecha_d.isoformat(),
        "proveedor": (proveedor or "").strip() or None,
        "categoria": (categoria or "").strip() or None,
    }


def registrar_gasto(cur, descripcion, monto, fecha, proveedor, categoria):
    """Inserta el gasto en cuentas_por_pagar y devuelve el id nuevo.

    Recibe un cursor (RealDictCursor); el commit lo hace quien llama.
    Mismo SQL que la skill agregar-gasto.
    """
    cur.execute(
        """
        INSERT INTO cuentas_por_pagar
            (descripcion, proveedor, monto, fecha_vencimiento, categoria)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (descripcion, proveedor, monto, fecha, categoria),
    )
    return cur.fetchone()["id"]
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_negocio_gastos.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/negocio/gastos.py tests/test_negocio_gastos.py
git commit -m "Agrega capa determinista de gastos (validar + registrar)"
```

---

### Task 3: Herramienta de propuesta (`app/agent/tools_acciones.py`)

Servidor MCP in-process `acciones` con la herramienta `proponer_gasto`, que valida con la capa de la Task 2 y publica un `Artifact(tipo="accion")` en el collector. No escribe en la BD. Sigue el patrón exacto de `app/agent/publish_tools.py::build_lienzo_server`.

**Files:**
- Create: `app/agent/tools_acciones.py`
- Test: `tests/test_tools_acciones.py`

**Interfaces:**
- Consumes: `app.negocio.gastos.validar_gasto` (Task 2); `app.canvas.artifacts.Artifact`, `Collector`; `Artifact(tipo="accion")` habilitado (Task 1).
- Produces:
  - `accion_gasto_artifact(params: dict) -> Artifact` (builder puro; `params` ya validado).
  - `build_acciones_server(collector) -> (server, ["mcp__acciones__proponer_gasto"])`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_tools_acciones.py`:

```python
# tests/test_tools_acciones.py
from app.agent import tools_acciones
from app.canvas.artifacts import Collector


def test_accion_gasto_artifact_arma_payload():
    params = {"descripcion": "Luz", "monto": 185000.0, "fecha": "2026-06-30",
              "proveedor": None, "categoria": "servicios"}
    art = tools_acciones.accion_gasto_artifact(params)
    assert art.tipo == "accion"
    assert art.titulo == "Confirmar gasto"
    assert art.payload["tipo_accion"] == "registrar_gasto"
    assert art.payload["params"] == params
    assert "185.000" in art.payload["resumen"]
    assert "Luz" in art.payload["resumen"]


def test_build_acciones_server_lista_un_tool():
    server, tool_names = tools_acciones.build_acciones_server(Collector())
    assert tool_names == ["mcp__acciones__proponer_gasto"]
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_tools_acciones.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.agent.tools_acciones'`

- [ ] **Step 3: Implementar `app/agent/tools_acciones.py`**

```python
"""Servidor MCP in-process 'acciones': herramientas que PROPONEN una escritura
sin ejecutarla. El agente nunca escribe en la BD; solo publica una tarjeta de
confirmación (Artifact tipo 'accion'). La escritura real la hace el endpoint
determinista del dashboard al apretar Confirmar.

Mismo patrón que app/agent/publish_tools.py (build_lienzo_server).
"""
from app.canvas.artifacts import Artifact, Collector
from app.negocio.gastos import validar_gasto


def _pesos(n):
    try:
        return "$" + f"{int(round(float(n))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "$0"


def _resumen_gasto(params: dict) -> str:
    desc = params.get("descripcion", "")
    extra = f" · {params['proveedor']}" if params.get("proveedor") else ""
    return f"Gasto: {desc} · {_pesos(params.get('monto'))} · vence {params.get('fecha', '')}{extra}"


def accion_gasto_artifact(params: dict) -> Artifact:
    """Construye el artefacto de acción 'registrar_gasto' a partir de params ya validados."""
    return Artifact(
        tipo="accion",
        titulo="Confirmar gasto",
        payload={
            "tipo_accion": "registrar_gasto",
            "params": params,
            "resumen": _resumen_gasto(params),
        },
    )


def build_acciones_server(collector: Collector):
    """Construye el servidor MCP 'acciones' ligado a un collector.

    Devuelve (server, lista_de_nombres_de_tools).
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool(
        "proponer_gasto",
        "Propone registrar un gasto (cuenta por pagar) para que el usuario lo "
        "confirme con un botón. NO escribe en la base de datos: solo publica una "
        "tarjeta de confirmación. Úsala cuando el usuario pida anotar/registrar un gasto.",
        {"descripcion": str, "monto": str, "fecha": str, "proveedor": str, "categoria": str},
    )
    async def proponer_gasto(args):
        try:
            limpio = validar_gasto(
                args.get("descripcion"), args.get("monto"), args.get("fecha"),
                args.get("proveedor"), args.get("categoria"))
        except ValueError as e:
            return {"content": [{"type": "text",
                    "text": f"No puedo proponer el gasto: {e} Pídele al usuario el dato que falta."}]}
        collector.add(accion_gasto_artifact(limpio))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — {_resumen_gasto(limpio)}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar para que se registre. "
                        "NO afirmes que el gasto ya quedó registrado."}]}

    server = create_sdk_mcp_server(name="acciones", version="1.0.0", tools=[proponer_gasto])
    tool_names = ["mcp__acciones__proponer_gasto"]
    return server, tool_names
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_tools_acciones.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agent/tools_acciones.py tests/test_tools_acciones.py
git commit -m "Agrega servidor MCP acciones con proponer_gasto (no escribe)"
```

---

### Task 4: Registrar el servidor `acciones` en el orquestador

Conectar `build_acciones_server(collector)` en `_build_options`, agregándolo a `mcp_servers` y a `allowed_tools`. No se cambia `permission_mode`.

**Files:**
- Modify: `app/agent/orchestrator.py:11-13` (imports), `:45-61` (`_build_options`)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `build_acciones_server` (Task 3).
- Produces: `_build_options(collector).allowed_tools` incluye `"mcp__acciones__proponer_gasto"`.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `tests/test_orchestrator.py`:

```python
def test_build_options_incluye_tool_de_accion():
    options = orchestrator._build_options(Collector())
    assert "mcp__acciones__proponer_gasto" in options.allowed_tools
    # No rompe lo anterior:
    assert "mcp__negocio__deuda_total" in options.allowed_tools
    assert "mcp__lienzo__publicar_kpi" in options.allowed_tools
    assert "mcp__postgres__query" in options.allowed_tools


def test_build_options_no_cambia_permission_mode():
    options = orchestrator._build_options(Collector())
    assert options.permission_mode == "bypassPermissions"
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python -m pytest tests/test_orchestrator.py::test_build_options_incluye_tool_de_accion -v`
Expected: FAIL (`mcp__acciones__proponer_gasto` no está en allowed_tools)

- [ ] **Step 3: Implementar los cambios**

En `app/agent/orchestrator.py`, tras la línea 13 (`from app.agent.tools_negocio import build_negocio_server`), agregar:

```python
from app.agent.tools_acciones import build_acciones_server
```

En `_build_options` (línea ~46), tras `negocio_server, negocio_tools = build_negocio_server()`, agregar:

```python
    acciones_server, acciones_tools = build_acciones_server(collector)
```

En el `return ClaudeAgentOptions(...)`, actualizar `mcp_servers` y `allowed_tools`:

```python
        mcp_servers={
            "lienzo": lienzo_server,
            "negocio": negocio_server,
            "acciones": acciones_server,
            "postgres": _postgres_server(),
        },
        allowed_tools=lienzo_tools + negocio_tools + acciones_tools + ["mcp__postgres__query"],
```

- [ ] **Step 4: Correr los tests del orquestador y verificar verde**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (incluye los dos nuevos y los existentes)

- [ ] **Step 5: Commit**

```bash
git add app/agent/orchestrator.py tests/test_orchestrator.py
git commit -m "Registra el servidor de acciones en el orquestador"
```

---

### Task 5: Regla de gastos en el system prompt

Instruir al agente: para gastos, usar `proponer_gasto`; pedir datos faltantes; nunca afirmar que el gasto quedó registrado (solo queda propuesto hasta que el usuario confirme).

**Files:**
- Modify: `app/agent/system_prompt.py` (agregar un bloque antes del cierre `"""`)
- Test: `tests/test_system_prompt.py`

**Interfaces:**
- Produces: `SYSTEM_PROMPT` contiene `"proponer_gasto"` y la regla de no afirmar registro.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `tests/test_system_prompt.py`:

```python
def test_system_prompt_incluye_regla_de_gastos():
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "proponer_gasto" in SYSTEM_PROMPT
    # Debe dejar claro que NO afirme que quedó registrado:
    assert "registrad" in SYSTEM_PROMPT.lower()
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python -m pytest tests/test_system_prompt.py::test_system_prompt_incluye_regla_de_gastos -v`
Expected: FAIL (`proponer_gasto` no está en el prompt)

- [ ] **Step 3: Implementar el bloque**

En `app/agent/system_prompt.py`, insertar este bloque justo antes de la línea final que cierra el string (la línea con solo `"""`), después del párrafo de proyecciones:

```python

REGISTRAR GASTOS (acción con confirmación): si el usuario pide anotar o registrar
un gasto / cuenta por pagar, usa la herramienta mcp__acciones__proponer_gasto con
descripción, monto y fecha de vencimiento (proveedor y categoría son opcionales).
Si falta la descripción, el monto o la fecha, pídeselos antes de proponer. Esta
herramienta NO registra el gasto: solo deja una tarjeta para que el usuario apriete
Confirmar. Por eso NUNCA digas que el gasto "quedó registrado" o "ya está guardado";
di que dejaste la propuesta lista para confirmar.
```

- [ ] **Step 4: Correr los tests del prompt y verificar verde**

Run: `python -m pytest tests/test_system_prompt.py -v`
Expected: PASS (incluye el nuevo y los existentes)

- [ ] **Step 5: Commit**

```bash
git add app/agent/system_prompt.py tests/test_system_prompt.py
git commit -m "Agrega regla de gastos (proponer, no afirmar registro) al prompt"
```

---

### Task 6: Endpoint determinista `POST /api/registrar-gasto`

Único camino de escritura. Lee JSON, valida con `gastos.validar_gasto`, ejecuta `gastos.registrar_gasto` con `get_conn()` y commit, y responde `{ok, id, mensaje}`. Maneja errores con 400 (validación) y 500 (BD) sin fingir éxito. Se verifica con una prueba HTTP real (los handlers HTTP no se testean con pytest en este proyecto).

**Files:**
- Modify: `app/dashboard.py:1046-1061` (método `do_POST` de `Handler`)

**Interfaces:**
- Consumes: `app.negocio.gastos.validar_gasto`, `registrar_gasto` (Task 2); `get_conn()` (ya existe en dashboard.py).

- [ ] **Step 1: Implementar el nuevo branch en `do_POST`**

En `app/dashboard.py`, dentro de `Handler.do_POST`, justo antes de la rama `else:` final (línea ~1060), insertar:

```python
        elif path == "/api/registrar-gasto":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except Exception:
                self._send(400, json.dumps({"ok": False, "error": "JSON inválido"}))
                return
            try:
                from app.negocio import gastos
            except Exception as e:  # pragma: no cover
                self._send(500, json.dumps({"ok": False, "error": "módulo de gastos no disponible",
                                            "detalle": str(e)}))
                return
            try:
                limpio = gastos.validar_gasto(
                    body.get("descripcion"), body.get("monto"), body.get("fecha"),
                    body.get("proveedor"), body.get("categoria"))
            except ValueError as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
                return
            try:
                conn = get_conn()
                with conn:
                    with conn.cursor() as cur:
                        new_id = gastos.registrar_gasto(cur, **limpio)
                conn.close()
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "error": "error al escribir en la base",
                                            "detalle": str(e)}, ensure_ascii=False))
                return
            monto_fmt = "$" + f"{int(round(float(limpio['monto']))):,}".replace(",", ".")
            self._send(200, json.dumps(
                {"ok": True, "id": new_id,
                 "mensaje": f"Gasto registrado (id {new_id}): {limpio['descripcion']} · {monto_fmt}"},
                ensure_ascii=False))
```

- [ ] **Step 2: Verificar el endpoint con una prueba HTTP real**

Levantar el dashboard en segundo plano y probar caso válido, caso inválido y limpieza. En PowerShell:

```powershell
# Levantar el dashboard (otra ventana o background) y esperar a que responda
Start-Process -WindowStyle Hidden python "app/dashboard.py"
Start-Sleep -Seconds 3

# Caso válido -> 200 ok:true con id
$ok = Invoke-RestMethod -Uri http://localhost:8777/api/registrar-gasto -Method Post `
  -ContentType 'application/json' `
  -Body '{"descripcion":"PRUEBA plan fase2b","monto":"1.234","fecha":"2026-12-31","categoria":"test"}'
$ok    # debe mostrar ok=True, id=<n>, mensaje=...

# Caso inválido (fecha mala) -> 400 ok:false
try {
  Invoke-RestMethod -Uri http://localhost:8777/api/registrar-gasto -Method Post `
    -ContentType 'application/json' -Body '{"descripcion":"x","monto":"100","fecha":"31/12/2026"}'
} catch { $_.Exception.Response.StatusCode.value__ }   # debe imprimir 400
```

Expected: el caso válido devuelve `ok=True` y un `id`; el inválido devuelve `400`.

- [ ] **Step 3: Borrar la fila de prueba y detener el dashboard**

```powershell
python -c "import app.dashboard as d; c=d.get_conn();
import contextlib;
cur=c.cursor(); cur.execute(\"DELETE FROM cuentas_por_pagar WHERE descripcion='PRUEBA plan fase2b'\"); c.commit(); c.close(); print('fila de prueba borrada')"
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
```

Expected: imprime "fila de prueba borrada".

- [ ] **Step 4: Commit**

```bash
git add app/dashboard.py
git commit -m "Agrega endpoint determinista POST /api/registrar-gasto"
```

---

### Task 7: Tarjeta de confirmación en el frontend

Agregar el caso `a.tipo === 'accion'` en `renderArtefactos`: dibuja una tarjeta con el resumen y botones Confirmar/Cancelar. Confirmar hace `POST /api/registrar-gasto` con `payload.params` y muestra el resultado; Cancelar descarta la tarjeta.

**Files:**
- Modify: `app/dashboard_ui.html:703-719` (función `renderArtefactos`)

**Interfaces:**
- Consumes: artefacto `{tipo:'accion', titulo, payload:{tipo_accion, params, resumen}}` que llega vía `/api/ask` (Task 3 + `run_agent`); endpoint `/api/registrar-gasto` (Task 6); helpers existentes `esc`, `artSeq`.

- [ ] **Step 1: Agregar la rama `accion` en `renderArtefactos`**

En `app/dashboard_ui.html`, dentro de `renderArtefactos`, después del bloque `else if(a.tipo==='informe'){ ... }` (línea ~716) y antes del `}` que cierra el `forEach` (línea 717), insertar:

```javascript
    } else if(a.tipo==='accion'){
      const id='acc'+(++artSeq);
      const params=p.params||{};
      cont.insertAdjacentHTML('beforeend',`<div id="${id}" style="border:1px solid var(--line,#3b3220);border-radius:10px;padding:12px 14px;margin-top:10px;background:rgba(249,115,22,.06)">
        <div style="font-weight:700;font-size:12.5px;margin-bottom:4px">${esc(a.titulo||'Confirmar acción')}</div>
        <div style="font-size:13px;margin-bottom:10px">${esc(p.resumen||'')}</div>
        <button class="acc-ok" style="background:#f97316;color:#fff;border:none;padding:8px 16px;border-radius:7px;font-size:12.5px;cursor:pointer;font-weight:600">Confirmar</button>
        <button class="acc-no" style="background:transparent;color:var(--muted,#a8a29e);border:1px solid var(--line,#3b3220);padding:8px 16px;border-radius:7px;font-size:12.5px;cursor:pointer;margin-left:6px">Cancelar</button>
        <div class="acc-msg" style="margin-top:8px;font-size:12px"></div></div>`);
      const card=document.getElementById(id);
      const msg=card.querySelector('.acc-msg');
      const ok=card.querySelector('.acc-ok'), no=card.querySelector('.acc-no');
      no.addEventListener('click',()=>card.remove());
      ok.addEventListener('click',async()=>{
        ok.disabled=true; ok.textContent='Registrando…';
        try{
          const r=await fetch('/api/registrar-gasto',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(params)});
          const dd=await r.json();
          if(dd.ok){ msg.innerHTML='<span style="color:var(--green,#16a34a)">✓ '+esc(dd.mensaje||('Registrado (id '+dd.id+')'))+'</span>'; ok.textContent='Registrado'; no.disabled=true; }
          else { msg.innerHTML='<span style="color:#dc2626">'+esc(dd.error||'No se pudo registrar')+'</span>'; ok.disabled=false; ok.textContent='Confirmar'; }
        }catch(e){ msg.innerHTML='<span style="color:#dc2626">Error de conexión: '+esc(e.message)+'</span>'; ok.disabled=false; ok.textContent='Confirmar'; }
      });
```

- [ ] **Step 2: Verificación manual en el navegador**

Levantar el dashboard (`python app/dashboard.py`), abrir http://localhost:8777, y en el chat escribir:
`anota un gasto de luz de 185 mil que vence el 30 de junio`

Expected:
- El agente responde hablando de "propuesta lista para confirmar" (no dice que ya se registró).
- Aparece una tarjeta con el resumen `Gasto: ... · $185.000 · vence 2026-06-30` y botones Confirmar/Cancelar.
- Al apretar **Confirmar**, la tarjeta muestra "✓ Gasto registrado (id N)…" y el botón queda deshabilitado.
- Al apretar **Cancelar** (en otra propuesta), la tarjeta desaparece.

- [ ] **Step 3: Borrar el gasto de prueba creado desde el chat**

```powershell
python -c "import app.dashboard as d; c=d.get_conn(); cur=c.cursor(); cur.execute(\"DELETE FROM cuentas_por_pagar WHERE descripcion ILIKE '%luz%' AND monto=185000\"); print('borradas', cur.rowcount); c.commit(); c.close()"
```

Expected: imprime cuántas filas borró (al menos 1).

- [ ] **Step 4: Commit**

```bash
git add app/dashboard_ui.html
git commit -m "Agrega tarjeta de confirmacion de gasto en el chat del dashboard"
```

---

### Task 8: Verificación integral del flujo completo

Confirmar que la suite completa pasa y que el ciclo proponer → confirmar → registrar → aparece en flujo de caja funciona de punta a punta.

**Files:** ninguno (solo verificación).

- [ ] **Step 1: Correr toda la suite de tests**

Run: `python -m pytest -q`
Expected: PASS sin fallos (incluye los nuevos: artifacts, negocio_gastos, tools_acciones, orchestrator, system_prompt).

- [ ] **Step 2: Prueba de extremo a extremo con el dashboard**

Levantar `python app/dashboard.py`. En el chat:
1. `registra un gasto de arriendo de 850.000 para el 2026-07-05, proveedor Propietario SA`
2. Confirmar la tarjeta.
3. Preguntar en el chat: `proyecta el flujo de caja` y verificar que el egreso de $850.000 aparece en la semana del 05/07 (si cae dentro del horizonte de 4 semanas) — o consultar la BD directamente:

```powershell
python -c "import app.dashboard as d; c=d.get_conn(); cur=c.cursor(); cur.execute(\"SELECT id,descripcion,monto,fecha_vencimiento,proveedor FROM cuentas_por_pagar WHERE descripcion ILIKE '%arriendo%' AND monto=850000\"); print(cur.fetchall()); c.close()"
```

Expected: la fila existe con los datos correctos.

- [ ] **Step 3: Limpiar el gasto de prueba**

```powershell
python -c "import app.dashboard as d; c=d.get_conn(); cur=c.cursor(); cur.execute(\"DELETE FROM cuentas_por_pagar WHERE descripcion ILIKE '%arriendo%' AND monto=850000 AND proveedor='Propietario SA'\"); print('borradas', cur.rowcount); c.commit(); c.close()"
```

Expected: imprime "borradas 1".

- [ ] **Step 4: Commit de cierre (si quedó algún ajuste de docs)**

Si no hubo cambios de código en esta tarea, no hay commit. Si se ajustó documentación (p. ej. anotar el endpoint en CLAUDE.md), commitearlo:

```bash
git add -A
git commit -m "Documenta el flujo de registrar gasto con confirmacion"
```

---

## Self-Review

**1. Cobertura del spec:**
- "agente no escribe / propone artefacto" → Task 3 (`proponer_gasto` solo publica) + Task 4 (sin cambiar `permission_mode`, test en Task 4 Step 1). ✓
- `app/negocio/gastos.py` (`validar_gasto`, `registrar_gasto`) → Task 2. ✓
- `app/agent/tools_acciones.py` (`build_acciones_server`, artefacto `accion`) → Task 3. ✓
- orquestador registra server + allowed_tools → Task 4. ✓
- system prompt regla de gastos → Task 5. ✓
- `POST /api/registrar-gasto` determinista (400/500 sin fingir éxito) → Task 6. ✓
- `dashboard_ui.html` caso `accion` con tarjeta + botones → Task 7. ✓
- Tests: negocio_gastos, tools_acciones, orchestrator, system_prompt → Tasks 2–5. Integración → Tasks 6–8. ✓
- **Corrección al spec:** el spec decía "no se modifica artifacts.py"; es incorrecto porque `ARTIFACT_TYPES` valida el tipo. Se añadió la Task 1 para habilitar `"accion"`. ✓

**2. Escaneo de placeholders:** No hay "TBD"/"añadir validación"/"similar a Task N". Todos los pasos de código muestran el código completo. ✓

**3. Consistencia de tipos:**
- `validar_gasto(...) -> dict` con claves `descripcion/monto/fecha/proveedor/categoria`; `registrar_gasto(cur, **limpio)` recibe exactamente esas claves como parámetros nombrados. ✓
- `accion_gasto_artifact(params)` consume el dict de `validar_gasto`; el payload `{tipo_accion, params, resumen}` es lo que el frontend lee como `p.params`/`p.resumen` en Task 7. ✓
- `registrar_gasto` lee `cur.fetchone()["id"]` (RealDictCursor de `get_conn()`); el `FakeCursor` del test devuelve `{"id": 42}`. ✓
- `build_acciones_server(collector)` devuelve `(server, ["mcp__acciones__proponer_gasto"])`; el orquestador suma `acciones_tools` a `allowed_tools` y el test verifica el nombre exacto. ✓

## Execution Handoff

**Plan completo y guardado en `docs/superpowers/plans/2026-06-21-registrar-gasto-confirmacion.md`. Dos opciones de ejecución:**

**1. Subagent-Driven (recomendada)** — despacho un subagente nuevo por tarea, reviso entre tareas, iteración rápida.

**2. Inline Execution** — ejecuto las tareas en esta sesión con executing-plans, por lotes con checkpoints de revisión.

**¿Cuál prefieres?**
