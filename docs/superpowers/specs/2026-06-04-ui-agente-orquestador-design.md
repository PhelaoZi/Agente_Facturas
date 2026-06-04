# Especificación de diseño — UI con agente orquestador (Zigurat ERP)

- **Fecha:** 2026-06-04
- **Autor:** Christian de la Fuente (con Claude Code)
- **Estado:** Aprobado para planificación
- **Proyecto:** Zigurat ERP — Agente Facturas

---

## 1. Contexto y objetivo

El proyecto ya automatiza la sincronización de DTEs del SII a PostgreSQL y expone
12 skills de Claude Code (ventas, flujo de caja, costos, conciliación, wiki de
clientes) más un MCP de Postgres. Hoy todo se opera desde la terminal con slash
commands.

El objetivo es **darle una interfaz gráfica local** a esa capacidad: una app que
corre solo en el notebook del dueño, donde puede **conversar con un agente
orquestador** que interpreta preguntas de negocio, despacha las skills/subagentes
necesarios, consulta la base de datos y devuelve **texto, gráficos, tablas,
informes, proyecciones y recomendaciones**. Los resultados deben poder
**exportarse a archivos** para compartirlos con el socio.

La idea de fondo: el "agente orquestador con subagentes" ya existe (Claude + las
skills + el MCP). Este proyecto le pone una UI encima, no reconstruye el cerebro.

---

## 2. Alcance

### v1 — incluye

- App **Streamlit** local (un usuario, sin login, corre con `streamlit run`).
- Disposición **chat + lienzo** (Opción B): chat a la izquierda, lienzo de
  resultados persistente a la derecha.
- **Agente orquestador** vía Claude Agent SDK, autenticado con la suscripción
  de Claude Code (sin API key), reutilizando skills existentes + MCP de Postgres.
- **Artefactos** en el lienzo: KPI, gráfico, tabla, informe (texto).
- **Exportar**: por artefacto (gráfico → PNG; tabla → Excel/CSV) y lienzo
  completo → informe **HTML autocontenido**.
- **Persistencia de sesión** del lienzo y del historial de chat mientras la app
  está abierta (`st.session_state`).

### v1 — explícitamente fuera (diferido)

- UI para **cargar datos** (subir XMLs / Excel). La ingesta sigue por slash
  commands por ahora.
- **Envío por correo** automático al socio (se comparte exportando archivos).
- **Subagentes en paralelo** avanzados (arquitecturado, no activado de entrada).
- **PDF nativo** del informe (se usa HTML; PDF queda como mejora futura).
- Guardar historial/sesiones en disco, multiusuario, autenticación.

---

## 3. Decisiones clave y su justificación

| Decisión | Elección | Por qué |
|----------|----------|---------|
| Framework UI | **Streamlit** | Un solo lenguaje (Python, stack actual). Widgets nativos de tablas, métricas y gráficos ideales para "informes". Rápido de levantar. Local de un usuario, que es justo el caso. |
| Cerebro del agente | **Claude Agent SDK** (Python) | Soporta de fábrica subagentes, skills, MCP y herramientas custom. Reutiliza todo lo existente. |
| Autenticación / costo | **Suscripción Claude Code** | Sin facturación extra de API. Hereda la sesión del CLI ya instalado. Sujeto a límites del plan (ver riesgos). |
| Disposición | **Chat + lienzo (B)** | Permite armar un informe a partir de varias preguntas; los artefactos persisten en vez de perderse en el scroll del chat. |
| Compartir | **Exportar archivos** (PNG / Excel / CSV / HTML) | Robusto, sin infraestructura ni configuración. El usuario envía el archivo por WhatsApp/correo. |
| Formato del informe completo | **HTML autocontenido** | PDF en Windows arrastra dependencias pesadas. HTML se abre en cualquier navegador y se imprime a PDF si hace falta. |

---

## 4. Arquitectura

```
┌──────────────────────────── Notebook (local, 1 usuario) ────────────────────────────┐
│                                                                                      │
│   Navegador  ──►  Streamlit app  (streamlit_app.py)                                  │
│                     │   panel chat (izq)        lienzo (der)                          │
│                     │                                                                 │
│                     ▼                                                                 │
│                  orchestrator.py ──► Claude Agent SDK ──► (suscripción Claude Code)   │
│                     │                     │                                           │
│                     │                     ├─ skills de .claude/skills/                │
│                     │                     ├─ MCP Postgres (.mcp.json, solo lectura)   │
│                     │                     └─ publish_tools (MCP in-process)           │
│                     │                            │                                    │
│                     ▼                            ▼                                    │
│                  st.session_state  ◄──── artefactos (KPI/gráfico/tabla/informe)       │
│                     │                                                                 │
│                     ▼                                                                 │
│                  canvas/render.py + canvas/export.py ──► archivos (PNG/Excel/CSV/HTML)│
│                                                                                      │
│                          PostgreSQL local · dte_facturas_chile                       │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### Componentes (módulos Python, `snake_case` como el código actual)

```
app/
  streamlit_app.py        # Entrada. Layout chat+lienzo. Orquesta UI y estado de sesión.
  agent/
    orchestrator.py       # Configura ClaudeAgentOptions y ejecuta la consulta.
    publish_tools.py      # Herramientas MCP in-process: publicar_kpi/grafico/tabla/informe.
    system_prompt.py      # Texto del system prompt del orquestador (incl. reglas SQL).
  canvas/
    artifacts.py          # Modelo de artefacto (dataclass) + helpers de session_state.
    render.py             # Renderiza cada tipo de artefacto en el lienzo.
    export.py             # Exporta artefacto y lienzo completo a archivo.
  charts/
    builder.py            # datos + spec → figura Plotly.
  config.py               # Carga .env (patrón _load_env) y constantes.
```

> **Convención:** aunque el CLAUDE.md global pide kebab-case para archivos, el
> proyecto usa `snake_case` en todos sus `.py` (`flujo_caja.py`, `parse_dte.py`).
> Se respeta la convención real del proyecto para que los imports sean limpios.

---

## 5. Flujo de datos

1. El usuario escribe una pregunta en el chat (panel izquierdo).
2. `streamlit_app.py` agrega el mensaje al historial y llama a
   `orchestrator.run(pregunta, collector)`.
3. `orchestrator.py` ejecuta el Agent SDK con:
   - `cwd` = raíz del proyecto y carga de settings del proyecto, para que el
     agente vea `.claude/skills/` y `.mcp.json`.
   - `system_prompt` del orquestador (contexto del negocio + reglas SQL críticas).
   - Servidor MCP in-process con las `publish_tools`.
4. El agente interpreta, despacha skills/subagentes, consulta la BD por el MCP de
   Postgres y arma resultados.
5. Cuando produce un resultado visual, **llama a una `publicar_*`**, que agrega el
   artefacto al `collector` (lista en memoria compartida con la app).
6. Al terminar (o en streaming), `orchestrator.py` devuelve el texto de respuesta
   y los artefactos recolectados.
7. `streamlit_app.py` guarda el texto en el historial del chat y los artefactos en
   `st.session_state["canvas"]`. El lienzo se re-renderiza con `render.py`.
8. El usuario exporta un artefacto o todo el lienzo con `export.py` → descarga.

### Puente herramienta → lienzo (punto técnico delicado)

Las `publish_tools` corren **dentro** de la llamada del Agent SDK. Para evitar
problemas de hilos/estado, cada tool **anexa un dict de artefacto a un `collector`**
(objeto pasado por closure al construir el servidor MCP in-process en cada
consulta). La app lee el `collector` **después** de que la corrida termina y vuelca
los artefactos a `st.session_state`. Así no se escribe a `session_state` desde
dentro del SDK.

---

## 6. Modelo de artefactos

`canvas/artifacts.py` define un `Artifact` (dataclass) con:

- `id`: identificador único (para botones de export y de-duplicación).
- `tipo`: `"kpi" | "grafico" | "tabla" | "informe"`.
- `titulo`: encabezado mostrado en el lienzo.
- `payload`: datos del artefacto según el tipo:
  - `kpi`: `{etiqueta, valor, delta?}`.
  - `grafico`: `{chart_type, data, x, y, ...}` (spec para `charts/builder.py`).
  - `tabla`: filas/columnas (se materializa como `pandas.DataFrame`).
  - `informe`: markdown/texto.
- `creado_en`: timestamp.

El lienzo es una **lista ordenada de artefactos** en `st.session_state["canvas"]`.

---

## 7. Herramientas del agente (`publish_tools.py`)

Expuestas como servidor MCP in-process del Agent SDK (decorador `@tool`):

- `publicar_kpi(etiqueta, valor, delta?)`
- `publicar_grafico(titulo, chart_type, data, x, y, ...)`
- `publicar_tabla(titulo, columnas, filas)`
- `publicar_informe(titulo, markdown)`

Cada una valida sus argumentos (dato externo → validar siempre) y anexa el
artefacto al `collector`. El system prompt instruye al agente a **usar estas
herramientas** para todo resultado que deba quedar en el lienzo, en vez de volcar
tablas crudas en el texto del chat.

---

## 8. System prompt del orquestador (`system_prompt.py`)

Debe incluir, como mínimo:

- Rol: orquestador analista de Zigurat Brewery; responde en español.
- **Reglas SQL críticas** (copiadas del CLAUDE.md): usar
  `COALESCE(monto_*_ajustado, monto_*)`, excluir `tipo_documento = '61'` en sumas,
  `tipo_documento` y `folio` son texto, `COUNT(DISTINCT rut_cliente)`, etc.
- **Estructura de facturación de doble línea** (cerveza + logística) y precios
  reales por barril, para no malinterpretar `precio_unitario`.
- Preferir las **skills existentes** para consultas frecuentes; MCP para ad-hoc.
- Instrucción de **publicar artefactos** con las `publish_tools`.
- Tono: directo y conciso; explicar el "por qué"; advertir riesgos.

---

## 9. Subagentes (diferido, pero arquitecturado)

El Agent SDK soporta definiciones de subagentes. En v1 el orquestador usa las
skills directamente (menor consumo de la suscripción). El enganche futuro: definir
subagentes por dominio (`analista-ventas`, `analista-finanzas`, `analista-costos`)
y dejar que el orquestador los despache en paralelo cuando una pregunta cruce
varios dominios (ej: "compara ventas, costos y flujo de un cliente"). No se
implementa en v1; se documenta el punto de extensión en `orchestrator.py`.

---

## 10. Exportar / compartir (`canvas/export.py`)

- **Gráfico → PNG**: `plotly` + `kaleido` (`fig.write_image`). También permite
  bajar el HTML interactivo del gráfico.
- **Tabla → Excel / CSV**: `pandas` (`to_excel` con `openpyxl` / `to_csv`).
- **Lienzo completo → informe HTML autocontenido**: plantilla HTML que embebe los
  KPIs, los gráficos (PNG en base64) y los textos, en un único `.html` que se abre
  en cualquier navegador y se puede imprimir a PDF.

Todos los export usan `st.download_button` (descarga directa, sin servidor extra).

---

## 11. Manejo de errores y riesgos

| Riesgo | Mitigación |
|--------|-----------|
| **Streamlit re-ejecuta el script en cada interacción.** | Guardar historial y artefactos en `st.session_state`; transmitir la respuesta del agente con `st.write_stream`; recolectar artefactos tras la corrida (no escribir a `session_state` desde el SDK). |
| **Límites de uso de la suscripción.** | v1 mayormente mono-agente. Capturar el error de límite del SDK y mostrar un mensaje claro en el chat ("alcanzaste el límite de tu plan, intenta más tarde"). |
| **Claude Code no instalado / sin sesión.** | `config.py` verifica al arranque y muestra instrucción de `claude login` si falta. |
| **El agente genera SQL incorrecto** (totales inflados). | Reglas SQL embebidas en el system prompt + MCP de Postgres en **solo lectura**. Preferir skills probadas. |
| **PDF en Windows.** | Informe en HTML autocontenido; PDF queda como mejora futura. |
| **Llamadas largas del agente.** | Indicador de progreso (`st.status`/spinner) mientras corre; manejo de excepciones con `try/except` que devuelve mensaje claro al chat, nunca un catch vacío. |

Toda operación async/externa va envuelta en `try/except` con logging de contexto
(coherente con las preferencias del proyecto). Los errores nunca se silencian.

---

## 12. Estrategia de testing

- **TDD en piezas puras:**
  - `charts/builder.py`: spec de datos → figura Plotly esperada.
  - `canvas/export.py`: artefacto → bytes de archivo (PNG no vacío, CSV con
    contenido correcto, HTML con los títulos esperados).
  - `canvas/artifacts.py`: alta/orden/de-duplicación de artefactos.
- **Agente con stub:** `orchestrator.py` testeado con un doble del SDK que
  devuelve llamadas a `publish_tools` predefinidas; se verifica que el `collector`
  termina con los artefactos esperados.
- **App completa:** validación manual/exploratoria (chat real, exportar, compartir).

---

## 13. Dependencias nuevas

```
streamlit
claude-agent-sdk
plotly
kaleido          # export PNG de gráficos Plotly
pandas           # ya presente
openpyxl         # ya presente
```

Requisito de entorno: **Claude Code instalado y con sesión iniciada** en el
notebook (la app hereda esa autenticación).

---

## 14. Convenciones del proyecto a respetar

- Carga de `.env` con el patrón `_load_env()` (no python-dotenv).
- Archivos `.py`, variables y funciones en `snake_case` (PEP 8 y estilo actual del
  repo). El camelCase que pide el CLAUDE.md global se reserva para proyectos JS/TS.
- Comentarios y textos de UI en español.
- Organizar por funcionalidad (carpetas `agent/`, `canvas/`, `charts/`).
- No tocar el pipeline DTE ni los scripts existentes.

---

## 15. Criterios de aceptación (v1)

1. `streamlit run app/streamlit_app.py` abre la app con chat (izq) + lienzo (der).
2. Una pregunta como "¿cómo van las ventas de mayo?" produce: respuesta de texto
   en el chat + al menos un artefacto (KPI o gráfico) en el lienzo, con datos
   reales de la BD.
3. El lienzo persiste entre preguntas sucesivas en la misma sesión.
4. Cada gráfico se exporta a PNG y cada tabla a Excel/CSV.
5. "Exportar informe" genera un HTML autocontenido con los artefactos del lienzo.
6. Los totales de ventas respetan las reglas SQL críticas (ajustados, excluir NC).
7. Si Claude Code no tiene sesión, la app lo informa con instrucción clara.

---

## 16. Futuro (post-v1)

- UI de carga de datos (XMLs del SII, Excel del banco) sobre el pipeline existente.
- Envío del informe por correo al socio.
- Subagentes por dominio en paralelo.
- PDF nativo del informe.
- Persistencia de sesiones/conversaciones en disco.
