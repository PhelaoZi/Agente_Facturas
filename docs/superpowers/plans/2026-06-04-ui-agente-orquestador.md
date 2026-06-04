# UI con agente orquestador — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir una app Streamlit local (chat + lienzo) donde un agente Claude responde preguntas de negocio consultando PostgreSQL y publica KPIs, gráficos, tablas e informes exportables.

**Architecture:** Streamlit es la UI. `orchestrator.py` ejecuta el Claude Agent SDK (autenticado con la suscripción Claude Code) con dos servidores MCP: el de Postgres (solo lectura) y uno in-process (`lienzo`) cuyas herramientas `publicar_*` recolectan artefactos. Tras cada consulta, los artefactos se vuelcan a `st.session_state` y se renderizan en el lienzo con botones de exportar.

**Tech Stack:** Python, Streamlit, claude-agent-sdk, Plotly + kaleido, pandas, openpyxl, pytest.

**Nota de alcance v1:** el agente NO ejecuta scripts de skills (no se le da Bash). Responde consultando Postgres vía MCP con las reglas SQL embebidas en el system prompt. `allowed_tools` actúa como lista blanca, así que `permission_mode="bypassPermissions"` es seguro: el agente solo puede usar las 5 herramientas MCP permitidas. Reutilizar skills/subagentes con Bash queda como mejora futura.

**Requisito de entorno:** Claude Code instalado y con sesión iniciada (`claude` en el PATH). La app hereda esa autenticación. No definir `ANTHROPIC_API_KEY` para que use la suscripción.

---

### Task 1: Esqueleto del paquete, dependencias y configuración de tests

**Files:**
- Create: `app/__init__.py`
- Create: `app/agent/__init__.py`
- Create: `app/canvas/__init__.py`
- Create: `app/charts/__init__.py`
- Create: `app/requirements.txt`
- Create: `pytest.ini`
- Create: `app/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Crear los `__init__.py` vacíos del paquete**

Crea cuatro archivos vacíos:
`app/__init__.py`, `app/agent/__init__.py`, `app/canvas/__init__.py`, `app/charts/__init__.py` (contenido: una sola línea de comentario).

```python
# Paquete de la app del agente Zigurat.
```

- [ ] **Step 2: Crear `app/requirements.txt`**

```text
streamlit>=1.40
claude-agent-sdk
plotly>=5.24
kaleido==0.2.1
pandas
openpyxl
pytest
```

- [ ] **Step 3: Crear `pytest.ini` en la raíz** (para que `import app...` funcione)

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 4: Instalar dependencias**

Run: `pip install -r app/requirements.txt`
Expected: instalación sin errores (kaleido puede tardar).

- [ ] **Step 5: Escribir el test de `config.py`**

```python
# tests/test_config.py
from app import config


def test_project_root_apunta_a_la_raiz():
    # La raíz del proyecto debe contener la carpeta scripts/ ya existente.
    assert (config.PROJECT_ROOT / "scripts").exists()


def test_db_url_apunta_a_la_base_correcta():
    assert config.DB_URL.startswith("postgresql://")
    assert "dte_facturas_chile" in config.DB_URL
```

- [ ] **Step 6: Correr el test y verificar que falla**

Run: `pytest tests/test_config.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 7: Implementar `app/config.py`**

```python
"""Carga de entorno y constantes de la app (patrón _load_env del proyecto)."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Carga .env de la raíz sin depender de python-dotenv."""
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

DB_URL = "postgresql://{user}:{pwd}@{host}:{port}/{db}".format(
    user=os.environ.get("DB_USER", "postgres"),
    pwd=os.environ.get("DB_PASSWORD", "postgres"),
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
    db=os.environ.get("DB_NAME", "dte_facturas_chile"),
)
```

- [ ] **Step 8: Correr el test y verificar que pasa**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 9: Commit**

```bash
git add app/__init__.py app/agent/__init__.py app/canvas/__init__.py app/charts/__init__.py app/requirements.txt pytest.ini app/config.py tests/test_config.py
git commit -m "Agrega esqueleto del paquete app, dependencias y config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Modelo de artefactos (`canvas/artifacts.py`)

**Files:**
- Create: `app/canvas/artifacts.py`
- Test: `tests/test_artifacts.py`

- [ ] **Step 1: Escribir los tests**

```python
# tests/test_artifacts.py
import pytest
from app.canvas.artifacts import Artifact, Collector, merge_artifacts


def test_artifact_valida_tipo():
    with pytest.raises(ValueError):
        Artifact(tipo="zzz", titulo="x", payload={})


def test_artifact_requiere_titulo():
    with pytest.raises(ValueError):
        Artifact(tipo="kpi", titulo="", payload={})


def test_artifact_genera_id_y_fecha():
    a = Artifact(tipo="kpi", titulo="Ventas", payload={"valor": "10"})
    assert a.id
    assert a.creado_en


def test_collector_acumula_en_orden():
    c = Collector()
    c.add(Artifact(tipo="kpi", titulo="A", payload={}))
    c.add(Artifact(tipo="kpi", titulo="B", payload={}))
    assert [a.titulo for a in c.items] == ["A", "B"]


def test_merge_evita_duplicados_por_id():
    a = Artifact(tipo="kpi", titulo="A", payload={})
    canvas = [a]
    assert len(merge_artifacts(canvas, [a])) == 1  # mismo id, no duplica
    b = Artifact(tipo="kpi", titulo="B", payload={})
    assert len(merge_artifacts(canvas, [b])) == 2
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `pytest tests/test_artifacts.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.canvas.artifacts'`.

- [ ] **Step 3: Implementar `app/canvas/artifacts.py`**

```python
"""Modelo de artefactos del lienzo y recolector usado por el agente."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

ARTIFACT_TYPES = {"kpi", "grafico", "tabla", "informe"}


@dataclass
class Artifact:
    tipo: str
    titulo: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    creado_en: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def __post_init__(self) -> None:
        if self.tipo not in ARTIFACT_TYPES:
            raise ValueError(f"tipo de artefacto inválido: {self.tipo}")
        if not self.titulo:
            raise ValueError("el artefacto requiere un título")


class Collector:
    """Acumula los artefactos que el agente publica durante una consulta."""

    def __init__(self) -> None:
        self._items: list[Artifact] = []

    def add(self, art: Artifact) -> None:
        self._items.append(art)

    @property
    def items(self) -> list[Artifact]:
        return list(self._items)


def merge_artifacts(canvas: list[Artifact], nuevos: list[Artifact]) -> list[Artifact]:
    """Anexa artefactos nuevos al lienzo evitando duplicar por id."""
    existentes = {a.id for a in canvas}
    return canvas + [a for a in nuevos if a.id not in existentes]
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `pytest tests/test_artifacts.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/canvas/artifacts.py tests/test_artifacts.py
git commit -m "Agrega modelo de artefactos y recolector del lienzo

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Constructor de gráficos (`charts/builder.py`)

**Files:**
- Create: `app/charts/builder.py`
- Test: `tests/test_charts_builder.py`

- [ ] **Step 1: Escribir los tests**

```python
# tests/test_charts_builder.py
import plotly.graph_objects as go
import pytest
from app.charts.builder import build_figure


def test_build_bar_con_titulo():
    fig = build_figure({"chart_type": "bar", "titulo": "Ventas", "x": ["a", "b"], "y": [1, 2]})
    assert isinstance(fig, go.Figure)
    assert fig.layout.title.text == "Ventas"


def test_build_line():
    fig = build_figure({"chart_type": "line", "x": [1, 2], "y": [3, 4]})
    assert isinstance(fig, go.Figure)


def test_build_pie():
    fig = build_figure({"chart_type": "pie", "x": ["a", "b"], "y": [10, 5]})
    assert isinstance(fig, go.Figure)


def test_chart_type_invalido():
    with pytest.raises(ValueError):
        build_figure({"chart_type": "donut", "x": [], "y": []})
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `pytest tests/test_charts_builder.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.charts.builder'`.

- [ ] **Step 3: Implementar `app/charts/builder.py`**

```python
"""Convierte una especificación de datos en una figura Plotly."""
import plotly.graph_objects as go

CHART_TYPES = {"bar", "line", "pie"}


def build_figure(spec: dict) -> go.Figure:
    chart_type = spec.get("chart_type")
    if chart_type not in CHART_TYPES:
        raise ValueError(f"chart_type inválido: {chart_type}")

    titulo = spec.get("titulo", "")
    x = spec.get("x", [])
    y = spec.get("y", [])

    if chart_type == "bar":
        fig = go.Figure(go.Bar(x=x, y=y))
    elif chart_type == "line":
        fig = go.Figure(go.Scatter(x=x, y=y, mode="lines+markers"))
    else:  # pie
        fig = go.Figure(go.Pie(labels=x, values=y))

    fig.update_layout(title=titulo, margin=dict(l=20, r=20, t=40, b=20))
    return fig
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `pytest tests/test_charts_builder.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/charts/builder.py tests/test_charts_builder.py
git commit -m "Agrega constructor de figuras Plotly desde spec de datos

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Exportación de artefactos (`canvas/export.py`)

**Files:**
- Create: `app/canvas/export.py`
- Test: `tests/test_export.py`

- [ ] **Step 1: Escribir los tests**

```python
# tests/test_export.py
import pytest
from app.canvas.artifacts import Artifact
from app.canvas import export


def test_tabla_csv_incluye_datos():
    art = Artifact(tipo="tabla", titulo="T", payload={"columnas": ["c"], "filas": [[1], [2]]})
    out = export.tabla_to_csv(art)
    assert b"c" in out and b"1" in out and b"2" in out


def test_tabla_excel_no_vacio():
    art = Artifact(tipo="tabla", titulo="T", payload={"columnas": ["c"], "filas": [[1]]})
    out = export.tabla_to_excel(art)
    assert len(out) > 0


def test_lienzo_html_incluye_titulos_y_estructura():
    canvas = [
        Artifact(tipo="kpi", titulo="Facturado",
                 payload={"etiqueta": "Facturado", "valor": "$1M", "delta": ""}),
        Artifact(tipo="informe", titulo="Resumen", payload={"markdown": "Hola socio"}),
    ]
    html = export.lienzo_to_html(canvas)
    assert "<html" in html.lower()
    assert "Facturado" in html
    assert "Resumen" in html
    assert "Hola socio" in html


def test_grafico_png_si_kaleido_disponible():
    pytest.importorskip("kaleido")
    art = Artifact(tipo="grafico", titulo="G",
                   payload={"chart_type": "bar", "x": ["a"], "y": [1]})
    out = export.grafico_to_png(art)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"  # firma de archivo PNG
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `pytest tests/test_export.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.canvas.export'`.

- [ ] **Step 3: Implementar `app/canvas/export.py`**

```python
"""Exporta artefactos y el lienzo completo a archivos descargables."""
import base64
import io
from html import escape

import pandas as pd

from app.canvas.artifacts import Artifact
from app.charts.builder import build_figure


def _tabla_df(art: Artifact) -> pd.DataFrame:
    return pd.DataFrame(art.payload["filas"], columns=art.payload["columnas"])


def tabla_to_csv(art: Artifact) -> bytes:
    return _tabla_df(art).to_csv(index=False).encode("utf-8")


def tabla_to_excel(art: Artifact) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _tabla_df(art).to_excel(writer, index=False, sheet_name="Datos")
    return buf.getvalue()


def grafico_to_png(art: Artifact) -> bytes:
    """Requiere kaleido instalado."""
    return build_figure(art.payload).to_image(format="png")


def _artifact_to_html(art: Artifact) -> str:
    titulo = escape(art.titulo)
    if art.tipo == "kpi":
        valor = escape(str(art.payload.get("valor", "")))
        delta = escape(str(art.payload.get("delta", "")))
        return (
            f'<div class="kpi"><div class="k-label">{titulo}</div>'
            f'<div class="k-val">{valor}</div><div class="k-delta">{delta}</div></div>'
        )
    if art.tipo == "grafico":
        png_b64 = base64.b64encode(grafico_to_png(art)).decode("ascii")
        return f'<h3>{titulo}</h3><img src="data:image/png;base64,{png_b64}" style="max-width:100%">'
    if art.tipo == "tabla":
        tabla_html = _tabla_df(art).to_html(index=False, border=0)
        return f"<h3>{titulo}</h3>{tabla_html}"
    # informe
    cuerpo = escape(art.payload.get("markdown", "")).replace("\n", "<br>")
    return f"<h3>{titulo}</h3><p>{cuerpo}</p>"


def lienzo_to_html(canvas: list[Artifact]) -> str:
    bloques = "\n".join(_artifact_to_html(a) for a in canvas)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Informe Zigurat</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;}}
 .kpi{{display:inline-block;border:1px solid #ddd;border-radius:8px;padding:0.6rem 1rem;margin:0.3rem;}}
 .k-label{{color:#666;font-size:0.8rem;}} .k-val{{font-size:1.4rem;font-weight:700;}}
 .k-delta{{color:#2a9d4a;font-size:0.85rem;}}
 table{{border-collapse:collapse;width:100%;}} th,td{{border-bottom:1px solid #eee;padding:0.4rem;text-align:left;}}
</style></head><body>
<h1>Informe Zigurat</h1>
{bloques}
</body></html>"""
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `pytest tests/test_export.py -v`
Expected: PASS (3 passed, 1 passed o skipped según kaleido).

- [ ] **Step 5: Commit**

```bash
git add app/canvas/export.py tests/test_export.py
git commit -m "Agrega exportación de artefactos a PNG, Excel, CSV e informe HTML

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Herramientas del agente (`agent/publish_tools.py`)

**Files:**
- Create: `app/agent/publish_tools.py`
- Test: `tests/test_publish_tools.py`

- [ ] **Step 1: Escribir los tests**

```python
# tests/test_publish_tools.py
from app.agent import publish_tools
from app.canvas.artifacts import Collector


def test_kpi_artifact_builder():
    art = publish_tools.kpi_artifact({"etiqueta": "Ventas", "valor": "$1M", "delta": "+5%"})
    assert art.tipo == "kpi"
    assert art.titulo == "Ventas"
    assert art.payload["valor"] == "$1M"


def test_grafico_artifact_builder():
    art = publish_tools.grafico_artifact(
        {"titulo": "G", "chart_type": "bar", "x": ["a"], "y": [1]}
    )
    assert art.tipo == "grafico"
    assert art.payload["chart_type"] == "bar"


def test_tabla_artifact_builder():
    art = publish_tools.tabla_artifact({"titulo": "T", "columnas": ["c"], "filas": [[1]]})
    assert art.tipo == "tabla"
    assert art.payload["columnas"] == ["c"]


def test_informe_artifact_builder():
    art = publish_tools.informe_artifact({"titulo": "I", "markdown": "x"})
    assert art.tipo == "informe"
    assert art.payload["markdown"] == "x"


def test_build_lienzo_server_lista_cuatro_tools():
    server, tool_names = publish_tools.build_lienzo_server(Collector())
    assert len(tool_names) == 4
    assert "mcp__lienzo__publicar_kpi" in tool_names
    assert "mcp__lienzo__publicar_grafico" in tool_names
    assert "mcp__lienzo__publicar_tabla" in tool_names
    assert "mcp__lienzo__publicar_informe" in tool_names
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `pytest tests/test_publish_tools.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.agent.publish_tools'`.

- [ ] **Step 3: Implementar `app/agent/publish_tools.py`**

Los builders son funciones puras (testeables sin el SDK). El SDK se importa de forma diferida dentro de `build_lienzo_server`.

```python
"""Herramientas MCP in-process que el agente usa para publicar artefactos."""
from app.canvas.artifacts import Artifact, Collector


def kpi_artifact(args: dict) -> Artifact:
    return Artifact(
        tipo="kpi",
        titulo=args["etiqueta"],
        payload={
            "etiqueta": args["etiqueta"],
            "valor": args["valor"],
            "delta": args.get("delta", ""),
        },
    )


def grafico_artifact(args: dict) -> Artifact:
    return Artifact(
        tipo="grafico",
        titulo=args["titulo"],
        payload={
            "titulo": args["titulo"],
            "chart_type": args["chart_type"],
            "x": args["x"],
            "y": args["y"],
        },
    )


def tabla_artifact(args: dict) -> Artifact:
    return Artifact(
        tipo="tabla",
        titulo=args["titulo"],
        payload={"columnas": args["columnas"], "filas": args["filas"]},
    )


def informe_artifact(args: dict) -> Artifact:
    return Artifact(
        tipo="informe",
        titulo=args["titulo"],
        payload={"markdown": args["markdown"]},
    )


def build_lienzo_server(collector: Collector):
    """Construye el servidor MCP in-process 'lienzo' ligado a un collector.

    Devuelve (server, lista_de_nombres_de_tools).
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("publicar_kpi", "Publica un indicador (KPI) en el lienzo.",
          {"etiqueta": str, "valor": str, "delta": str})
    async def publicar_kpi(args):
        collector.add(kpi_artifact(args))
        return {"content": [{"type": "text", "text": f"KPI '{args['etiqueta']}' publicado."}]}

    @tool("publicar_grafico", "Publica un gráfico (chart_type: bar|line|pie) en el lienzo.",
          {"titulo": str, "chart_type": str, "x": list, "y": list})
    async def publicar_grafico(args):
        collector.add(grafico_artifact(args))
        return {"content": [{"type": "text", "text": f"Gráfico '{args['titulo']}' publicado."}]}

    @tool("publicar_tabla", "Publica una tabla (columnas + filas) en el lienzo.",
          {"titulo": str, "columnas": list, "filas": list})
    async def publicar_tabla(args):
        collector.add(tabla_artifact(args))
        return {"content": [{"type": "text", "text": f"Tabla '{args['titulo']}' publicada."}]}

    @tool("publicar_informe", "Publica un informe de texto (markdown) en el lienzo.",
          {"titulo": str, "markdown": str})
    async def publicar_informe(args):
        collector.add(informe_artifact(args))
        return {"content": [{"type": "text", "text": f"Informe '{args['titulo']}' publicado."}]}

    server = create_sdk_mcp_server(
        name="lienzo",
        version="1.0.0",
        tools=[publicar_kpi, publicar_grafico, publicar_tabla, publicar_informe],
    )
    tool_names = [
        "mcp__lienzo__publicar_kpi",
        "mcp__lienzo__publicar_grafico",
        "mcp__lienzo__publicar_tabla",
        "mcp__lienzo__publicar_informe",
    ]
    return server, tool_names
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `pytest tests/test_publish_tools.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/agent/publish_tools.py tests/test_publish_tools.py
git commit -m "Agrega herramientas publicar_* del lienzo (servidor MCP in-process)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: System prompt del orquestador (`agent/system_prompt.py`)

**Files:**
- Create: `app/agent/system_prompt.py`
- Test: `tests/test_system_prompt.py`

- [ ] **Step 1: Escribir el test**

```python
# tests/test_system_prompt.py
from app.agent.system_prompt import SYSTEM_PROMPT


def test_incluye_reglas_sql_criticas():
    assert "COALESCE" in SYSTEM_PROMPT
    assert "'61'" in SYSTEM_PROMPT  # excluir notas de crédito en sumas


def test_instruye_publicar_artefactos():
    assert "publicar_grafico" in SYSTEM_PROMPT
    assert "publicar_kpi" in SYSTEM_PROMPT


def test_responde_en_espanol():
    assert "español" in SYSTEM_PROMPT.lower()
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `pytest tests/test_system_prompt.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.agent.system_prompt'`.

- [ ] **Step 3: Implementar `app/agent/system_prompt.py`**

```python
"""System prompt del agente orquestador de Zigurat."""

SYSTEM_PROMPT = """\
Eres el analista de negocio de Zigurat Brewery (Elaboradora y Comercializadora
Vintage SPA). Respondes SIEMPRE en español, de forma directa y concisa, explicando
el "por qué" cuando aporta. Tienes acceso de SOLO LECTURA a la base PostgreSQL
`dte_facturas_chile` mediante la herramienta `mcp__postgres__query`.

REGLAS SQL CRÍTICAS (obligatorias en cada consulta de ventas):
- Usa COALESCE(monto_total_ajustado, monto_total) y
  COALESCE(monto_neto_ajustado, monto_neto). Nunca el campo sin ajustar.
- Excluye las notas de crédito en las sumas: WHERE tipo_documento != '61'
  (ya están descontadas en los campos ajustados; incluirlas = doble conteo).
- `tipo_documento` es texto ('33', '61'): compara siempre con comillas.
- `folio` se guarda como texto; usa folio::integer si necesitas ordenarlo.
- Clientes únicos: COUNT(DISTINCT rut_cliente), no COUNT(*).
- `impuesto_adicional` (ILA) puede ser 0; no es obligatorio que sea > 0.

ESTRUCTURA DE FACTURACIÓN (doble línea): cada barril se factura en dos líneas
(producto + "Logistica"). El precio real del barril es la SUMA de ambas. Nunca
uses `precio_unitario` de la tabla productos para estimar el precio de venta; usa
COALESCE(monto_neto_ajustado, monto_neto) de la tabla ventas.

Tablas principales: ventas (folio+tipo_documento), clientes (rut_cliente),
productos (líneas de detalle), movimientos_banco, conciliaciones, cuentas_por_pagar,
maestro_insumos, recetas, sku, vista_costo_sku.

PUBLICAR RESULTADOS: cuando un resultado deba quedar visible para el usuario,
publícalo en el lienzo con las herramientas, además de resumirlo en texto:
- publicar_kpi para una métrica clave (etiqueta, valor, delta opcional).
- publicar_grafico para tendencias o comparaciones (chart_type: bar|line|pie,
  con listas x e y).
- publicar_tabla para rankings o detalles (columnas + filas).
- publicar_informe para conclusiones, recomendaciones o proyecciones en texto.
Prefiere publicar artefactos antes que volcar tablas largas en el chat.

Si una pregunta requiere proyecciones o recomendaciones, básate en los datos reales
de la BD y explica los supuestos. Si algo puede estar incompleto o ser riesgoso,
adviértelo.
"""
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `pytest tests/test_system_prompt.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/agent/system_prompt.py tests/test_system_prompt.py
git commit -m "Agrega system prompt del orquestador con reglas SQL del negocio

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Orquestador del Agent SDK (`agent/orchestrator.py`)

**Files:**
- Create: `app/agent/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Escribir los tests**

```python
# tests/test_orchestrator.py
import types
from app.agent import orchestrator
from app.canvas.artifacts import Collector


def test_extract_text_concatena_solo_bloques_con_texto():
    msgs = [
        types.SimpleNamespace(content=[
            types.SimpleNamespace(text="Hola"),
            types.SimpleNamespace(text="mundo"),
        ]),
        types.SimpleNamespace(content="no-es-lista"),
        types.SimpleNamespace(otra_cosa=1),
    ]
    assert orchestrator._extract_text(msgs) == "Hola\nmundo"


def test_postgres_server_usa_npx_y_server_postgres():
    s = orchestrator._postgres_server()
    assert s["command"] == "npx"
    assert any("server-postgres" in a for a in s["args"])


def test_build_options_incluye_tools_permitidos():
    options = orchestrator._build_options(Collector())
    assert "mcp__postgres__query" in options.allowed_tools
    assert "mcp__lienzo__publicar_kpi" in options.allowed_tools


def test_run_agrega_texto_de_la_respuesta(monkeypatch):
    async def fake_query(prompt, options):
        yield types.SimpleNamespace(
            content=[types.SimpleNamespace(text="Respuesta del agente")]
        )

    monkeypatch.setattr(orchestrator, "query", fake_query)
    out = orchestrator.run("¿ventas?", Collector())
    assert out == "Respuesta del agente"
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `pytest tests/test_orchestrator.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.agent.orchestrator'`.

- [ ] **Step 3: Implementar `app/agent/orchestrator.py`**

```python
"""Ejecuta el Claude Agent SDK como orquestador sobre Postgres + lienzo."""
import asyncio

from claude_agent_sdk import ClaudeAgentOptions, query

from app.agent.publish_tools import build_lienzo_server
from app.agent.system_prompt import SYSTEM_PROMPT
from app.canvas.artifacts import Collector
from app.config import DB_URL, PROJECT_ROOT

MAX_TURNS = 20


def _postgres_server() -> dict:
    """Servidor MCP de Postgres (solo lectura) igual al de .mcp.json."""
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres", DB_URL],
    }


def _extract_text(messages) -> str:
    """Concatena el texto de los bloques de respuesta (duck-typing, sin acoplar al SDK)."""
    partes: list[str] = []
    for m in messages:
        content = getattr(m, "content", None)
        if isinstance(content, list):
            for b in content:
                t = getattr(b, "text", None)
                if isinstance(t, str):
                    partes.append(t)
    return "\n".join(partes).strip()


def _build_options(collector: Collector) -> ClaudeAgentOptions:
    lienzo_server, lienzo_tools = build_lienzo_server(collector)
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        cwd=str(PROJECT_ROOT),
        mcp_servers={"lienzo": lienzo_server, "postgres": _postgres_server()},
        allowed_tools=lienzo_tools + ["mcp__postgres__query"],
        permission_mode="bypassPermissions",
        max_turns=MAX_TURNS,
    )


async def _run(pregunta: str, collector: Collector) -> str:
    options = _build_options(collector)
    mensajes = []
    async for message in query(prompt=pregunta, options=options):
        mensajes.append(message)
    return _extract_text(mensajes)


def run(pregunta: str, collector: Collector) -> str:
    """Punto de entrada síncrono para Streamlit. Llena `collector` con artefactos."""
    return asyncio.run(_run(pregunta, collector))
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `pytest tests/test_orchestrator.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/agent/orchestrator.py tests/test_orchestrator.py
git commit -m "Agrega orquestador del Agent SDK sobre Postgres y lienzo

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Renderizado del lienzo (`canvas/render.py`)

Capa de vista (Streamlit). No lleva test unitario; se valida manualmente en la Task 9.

**Files:**
- Create: `app/canvas/render.py`

- [ ] **Step 1: Implementar `app/canvas/render.py`**

```python
"""Renderiza los artefactos del lienzo en Streamlit, con botones de exportar."""
import pandas as pd
import streamlit as st

from app.canvas import export
from app.canvas.artifacts import Artifact
from app.charts.builder import build_figure


def render_canvas(canvas: list[Artifact]) -> None:
    if not canvas:
        st.caption("El lienzo está vacío. Hazme una pregunta y publicaré aquí los resultados.")
        return

    st.download_button(
        "⬇ Exportar informe (HTML)",
        data=export.lienzo_to_html(canvas).encode("utf-8"),
        file_name="informe-zigurat.html",
        mime="text/html",
        key="export_informe",
    )

    kpis = [a for a in canvas if a.tipo == "kpi"]
    if kpis:
        cols = st.columns(len(kpis))
        for col, a in zip(cols, kpis):
            col.metric(a.payload["etiqueta"], a.payload["valor"], a.payload.get("delta") or None)

    for a in canvas:
        if a.tipo == "grafico":
            st.subheader(a.titulo)
            st.plotly_chart(build_figure(a.payload), use_container_width=True)
            try:
                st.download_button(
                    f"⬇ PNG · {a.titulo}", data=export.grafico_to_png(a),
                    file_name=f"{a.id}.png", mime="image/png", key=f"png_{a.id}",
                )
            except Exception:
                st.caption("(Instala kaleido para exportar el gráfico a PNG.)")
        elif a.tipo == "tabla":
            st.subheader(a.titulo)
            df = pd.DataFrame(a.payload["filas"], columns=a.payload["columnas"])
            st.dataframe(df, use_container_width=True)
            c1, c2 = st.columns(2)
            c1.download_button("⬇ Excel", data=export.tabla_to_excel(a),
                               file_name=f"{a.id}.xlsx", key=f"xls_{a.id}")
            c2.download_button("⬇ CSV", data=export.tabla_to_csv(a),
                               file_name=f"{a.id}.csv", mime="text/csv", key=f"csv_{a.id}")
        elif a.tipo == "informe":
            st.subheader(a.titulo)
            st.markdown(a.payload["markdown"])
```

- [ ] **Step 2: Verificación de import (sanity check)**

Run: `python -c "import app.canvas.render"`
Expected: sin salida ni error (import OK).

- [ ] **Step 3: Commit**

```bash
git add app/canvas/render.py
git commit -m "Agrega renderizado del lienzo en Streamlit con botones de exportar

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: App Streamlit y verificación end-to-end (`streamlit_app.py`)

**Files:**
- Create: `app/streamlit_app.py`

- [ ] **Step 1: Implementar `app/streamlit_app.py`**

```python
"""App Streamlit: chat (izq) + lienzo (der). Ejecutar: streamlit run app/streamlit_app.py"""
import sys
from pathlib import Path

# Permite `import app...` al ejecutar con `streamlit run`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from app.agent import orchestrator  # noqa: E402
from app.canvas.artifacts import Collector, merge_artifacts  # noqa: E402
from app.canvas.render import render_canvas  # noqa: E402

st.set_page_config(page_title="Zigurat · Agente", layout="wide")

if "chat" not in st.session_state:
    st.session_state.chat = []
if "canvas" not in st.session_state:
    st.session_state.canvas = []

col_chat, col_lienzo = st.columns([0.38, 0.62], gap="large")

with col_chat:
    st.subheader("🍺 Agente Zigurat")
    for msg in st.session_state.chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

with col_lienzo:
    st.subheader("📌 Lienzo")
    render_canvas(st.session_state.canvas)

pregunta = st.chat_input("Pregúntame sobre ventas, flujo de caja, costos…")
if pregunta:
    st.session_state.chat.append({"role": "user", "content": pregunta})
    collector = Collector()
    try:
        with st.spinner("Analizando…"):
            respuesta = orchestrator.run(pregunta, collector)
    except Exception as e:  # nunca silenciar: mostrar el error con contexto
        respuesta = f"⚠️ Ocurrió un error al consultar al agente: {e}"
    st.session_state.chat.append({"role": "assistant", "content": respuesta})
    st.session_state.canvas = merge_artifacts(st.session_state.canvas, collector.items)
    st.rerun()
```

- [ ] **Step 2: Correr toda la suite de tests**

Run: `pytest -v`
Expected: todos PASS (el test de PNG puede salir "skipped" si no hay kaleido).

- [ ] **Step 3: Verificar sesión de Claude Code**

Run: `claude --version`
Expected: imprime una versión. Si falla, instalar/iniciar sesión de Claude Code antes de seguir.

- [ ] **Step 4: Levantar la app y probar manualmente**

Run: `streamlit run app/streamlit_app.py`
Verificar en el navegador:
1. Se ve el chat (izq) y el lienzo vacío (der).
2. Preguntar: **"¿Cuánto facturé en mayo de 2026 y quiénes fueron mis top 5 clientes?"**
3. El agente responde en el chat y publica al menos un KPI o gráfico + una tabla en el lienzo.
4. Los botones ⬇ PNG / Excel / CSV descargan archivos correctos.
5. "⬇ Exportar informe (HTML)" descarga un HTML que abre bien en el navegador.
6. Hacer una segunda pregunta y confirmar que el lienzo conserva lo anterior y agrega lo nuevo.

- [ ] **Step 5: Commit**

```bash
git add app/streamlit_app.py
git commit -m "Agrega app Streamlit chat + lienzo (entrada de la UI del agente)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Verificación final contra el spec

- [ ] Criterio 1 (app chat+lienzo): Task 9.
- [ ] Criterio 2 (pregunta → texto + artefacto con datos reales): Task 9 paso 4.
- [ ] Criterio 3 (lienzo persiste entre preguntas): Task 9 paso 4.6 + `merge_artifacts`.
- [ ] Criterio 4 (export PNG / Excel / CSV): Task 4 + Task 8.
- [ ] Criterio 5 (informe HTML del lienzo): Task 4 (`lienzo_to_html`) + Task 8.
- [ ] Criterio 6 (reglas SQL respetadas): Task 6 (system prompt) + MCP solo lectura.
- [ ] Criterio 7 (avisa si no hay sesión de Claude Code): Task 9 paso 3 + try/except en la app.

## Fuera de alcance (confirmado en el spec)

UI de carga de datos, envío por correo, subagentes en paralelo, PDF nativo,
persistencia de sesiones en disco, ejecución de scripts de skills por el agente.
