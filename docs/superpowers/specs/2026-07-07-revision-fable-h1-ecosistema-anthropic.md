# Revisión independiente (Fable 5): H1 — Zigurat ERP en el ecosistema Anthropic

- **Fecha:** 2026-07-07
- **Autor:** Claude Fable 5 (revisión independiente del spec de Opus 4.8 del 2026-07-06)
- **Alcance:** SOLO H1 (uso personal actual, mono-usuario). H2/H3 quedan fuera por instrucción de Christian.
- **Método:** lectura directa del código (`app/agent/`, `.mcp.json`, system prompt), documentación
  vigente del Claude Agent SDK vía context7 (`/anthropics/claude-agent-sdk-python`), referencia
  oficial `claude-api`, verificación en el entorno real (versión instalada del SDK, git).

---

## 0) Veredicto general sobre el trabajo de Opus

**El análisis de Opus es correcto y de buena calidad.** Verifiqué sus afirmaciones contra el
código y la documentación vigente en vez de tomarlas por ciertas, y las principales se sostienen:

| Afirmación de Opus | Mi verificación |
|---|---|
| El proyecto ya vive dentro del ecosistema (Agent SDK + MCP + skills) | ✅ Confirmado en `orchestrator.py`, `.mcp.json`, 12+ skills |
| "H1: no sobre-migrar; Managed Agents aquí = costo + riesgo beta por cero beneficio" | ✅ De acuerdo. Managed Agents resuelve multi-tenancy, aislamiento por sesión y hosting — nada de eso es un problema tuyo hoy |
| Crítico 1: `strict_mcp_config` no seteado | ✅ CONFIRMADO (default `False` en docs vigentes; `.mcp.json` tiene un 2º server postgres con credencial hardcodeada) |
| Crítico 2: `setting_sources` no seteado → carga CLAUDE.md completo por consulta | ✅ CONFIRMADO ("When None, all sources are loaded... Must include 'project' to load CLAUDE.md") |
| Tools de negocio sin try/except de BD | ✅ CONFIRMADO en `tools_negocio.py` (solo `tools_acciones.py` maneja errores) |
| SDK `claude-agent-sdk==0.2.93` al día | ✅ Confirmado instalado 0.2.93 |
| Credenciales fuera del repo | ✅ `.env` y `.mcp.json` ambos en `.gitignore`, sin historia en git |
| Invariante "el agente nunca escribe" se cumple en código | ✅ Las tools `proponer_*` solo construyen `Artifact`; la escritura vive en el endpoint |

**Donde matizo o agrego cosas que Opus no vio: secciones 2 y 3.**

---

## 1) Los dos críticos, confirmados con detalle

### 🔴 Crítico 1 — `strict_mcp_config=True` falta (`orchestrator.py:51`)
Sin este flag, el SDK puede combinar los servers MCP definidos en código con los de `.mcp.json`
del proyecto. Hoy ambos apuntan a la misma BD, así que no hay bug visible — pero es
no-determinismo latente: el server que usa el agente no queda definido por el código.
**Fix: 1 línea.**

### 🔴 Crítico 2 — `setting_sources=[]` falta (`orchestrator.py:51`)
Cada consulta del chat carga el CLAUDE.md completo (~600 líneas) encima del `SYSTEM_PROMPT`
(90 líneas). Infla tokens, duplica instrucciones y hace el comportamiento dependiente de un
archivo que cambia seguido. **Fix: 1 línea.**

Matiz importante: el `SYSTEM_PROMPT` ya contiene las reglas canónicas (COALESCE, exclusión de
NC, `fecha_pago` como fuente de verdad), así que aislar el agente **no le quita conocimiento
crítico**. Pero hay una discrepancia a corregir al mismo tiempo: el system prompt dice
"`tipo_documento` es texto ('33','61')" mientras el CLAUDE.md (sección estado de pago) dice que
en la BD real `tipo_documento` y `folio` son **integer**. Hoy el agente funciona porque
Postgres castea, y/o porque el CLAUDE.md corregía la instrucción. Al aislar con
`setting_sources=[]`, verificar el tipo real contra la BD y dejar el system prompt correcto.

---

## 2) Hallazgos propios (no estaban en el spec de Opus)

### 🔴 Nuevo — El orquestador no fija `model` (`orchestrator.py`)
`ClaudeAgentOptions` no pasa `model`, así que el chat del dashboard usa **el modelo por defecto
del CLI de Claude Code**. Christian acaba de cambiar su default a **Fable 5** (el modelo más
caro y de turnos más largos). Consecuencias:
- Cada pregunta simple del dashboard ("¿cuánto debe VDT?") correría en el modelo más pesado,
  consumiendo límites de la suscripción Pro mucho más rápido.
- El comportamiento del agente cambia silenciosamente cada vez que cambias el modelo del CLI.

**Fix: fijar `model="claude-sonnet-4-5"` (o el Sonnet vigente) explícitamente en
`_build_options()`.** Para consultas de negocio con tools bien definidas, Sonnet sobra; el
system prompt y las tools hacen el trabajo pesado. Esto hace el agente reproducible Y barato.

### 🟡 Nuevo — Costo H1 real: hoy el chat corre con la suscripción, no con API
Opus estimó "$0,07–0,12 por consulta" como si pagaras por token. Pero el orquestador usa
`cli_path=shutil.which("claude")` → corre sobre el CLI de Claude Code → **usa el login de tu
suscripción Pro**. Costo marginal por consulta: $0 (dentro de los límites del plan). La
estimación de Opus aplica solo si algún día pasas a API key. Esto refuerza su conclusión (el
costo no es problema en H1), pero cambia el porqué: hoy no pagas nada extra, solo consumes
cuota — otra razón para fijar un modelo liviano en el chat.

### 🟡 Nuevo — Credencial hardcodeada en `.mcp.json`
Aunque está en `.gitignore` (bien), la contraseña `zigurat` vive en texto plano en un archivo
que cualquier herramienta del ecosistema lee. Con `strict_mcp_config=True` el agente del
dashboard deja de usarlo, pero el MCP de Claude Code (para queries ad-hoc en sesiones como
esta) lo sigue usando. Riesgo bajo (BD local, equipo mono-usuario), pero anotar: si algún día
la BD se expone a red, rotar esa contraseña primero.

### 🟢 Observación — La arquitectura por capas es el activo, cuidarla
`app/negocio` (funciones puras con cursor) → tools MCP finas → agente → frontend con
confirm. Esta separación es exactamente lo que la documentación oficial de agent-design
recomienda ("promote actions to dedicated tools when you need to gate, render, audit"). El
patrón propose/confirm/execute es equivalente casero del `permission_policy: always_ask` +
`tool_confirmation` de Managed Agents. Cuando Anthropic estabilice esas superficies, la
migración sería mecánica — pero hoy no aporta nada en mono-usuario. **No tocar.**

---

## 3) Sobre las advertencias de Opus — priorización mía

| # | Advertencia | Mi opinión | Prioridad |
|---|---|---|---|
| 3 | try/except en las 18 tools de lectura | De acuerdo. Un Postgres caído hoy aborta el turno completo con un error feo. Envolver `_con_cursor` (un solo lugar, no 18) con try/except que devuelva `_texto("Error consultando la BD: ...")` — el agente puede entonces explicarle al usuario qué pasó. | **Media — hacer** |
| 4 | Chat stateless (sin memoria entre preguntas) | Discrepo en parte con tratarlo como "decisión pendiente". Para un chat de negocio, no recordar la pregunta anterior ("¿y el mes pasado?" falla) es fricción real de uso diario. El SDK lo resuelve con `ClaudeSDKClient` + `resume`/`session_id` sin cambiar la arquitectura de seguridad. Recomiendo hacerlo, pero DESPUÉS del hardening — es una feature, no un fix. | **Media — siguiente iteración** |
| 5 | Falta `.env.example` | Trivial y útil (tu compu se apaga sola; si un día reinstalas, agradeces el ejemplo). | Baja — hacer al pasar |
| 6 | Falta README de onboarding | Útil para tu objetivo de aprendizaje/portafolio (H3 futuro), no para H1. | Baja |
| 7 | Loguear `cli_path` | Marginal. Solo si aparece un bug de "no encuentra claude". | Baja — opcional |

---

## 4) Conclusiones para Christian (H1)

1. **La decisión estratégica ya está tomada y es correcta: quedarte donde estás.** El proyecto
   ya usa las piezas canónicas del ecosistema (Agent SDK, MCP, skills, hooks). Para
   mono-usuario, subir a Managed Agents u otra capa gestionada sería pagar infra y riesgo beta
   para resolver problemas que no tienes. Opus y yo coincidimos plenamente aquí.

2. **"Establecer bien el ecosistema" en H1 = hardening del agente, no migración.** Son ~5
   cambios chicos (sección 5) que hacen el agente del dashboard determinista, más barato,
   robusto ante BD caída y con memoria de conversación. Después de eso, el proyecto "toma
   forma": la base agéntica queda sólida y el trabajo siguiente vuelve a ser de negocio
   (acciones nuevas: marcar factura pagada, conciliar desde el chat — ya en tu roadmap).

3. **Para tu objetivo de aprendizaje:** los conceptos que este hardening te hace tocar
   (aislamiento de configuración, determinismo de agentes, ruteo de modelo por tarea, manejo
   de errores en tools, sesiones/resume) son exactamente el vocabulario de "ingeniería de
   agentes" que después vas a usar como asesor. Este proyecto ya es un caso de estudio
   presentable: agente con lectura segura + escritura con confirmación humana sobre un ERP
   real.

4. **Riesgo a vigilar (heredado del análisis de Opus, sigue vigente):** el ecosistema se mueve
   rápido. Tu única dependencia sensible es `claude-agent-sdk` — congelar la versión en
   `requirements.txt` (`claude-agent-sdk==0.2.93`) y actualizarla a propósito, no por accidente.

---

## 5) Plan de acción H1 (orden propuesto)

### Fase A — Hardening del orquestador ✅ COMPLETADA (2026-07-07)
Resultado: `strict_mcp_config=True`, `setting_sources=[]` y `model="sonnet"` en
`orchestrator.py`; system prompt corregido (`tipo_documento`/`folio` son INTEGER,
verificado contra information_schema); decorador `_tool_seguro` aplicado a las 14
tools de negocio (error de BD → resultado is_error legible, no aborta el turno);
3 tests nuevos en `tests/test_tools_negocio_errores.py`. Verificación: 149 tests
verdes + consulta real end-to-end por el orquestador (respondió y publicó KPI).

Plan original:
En `app/agent/orchestrator.py`, `_build_options()`:
1. `strict_mcp_config=True`
2. `setting_sources=[]`
3. `model="claude-sonnet-4-5"` (fijar explícito; verificar alias vigente al implementar)
4. Corregir en `system_prompt.py` la línea de `tipo_documento` (verificar tipo real en BD:
   `SELECT data_type FROM information_schema.columns WHERE table_name='ventas' AND column_name IN ('tipo_documento','folio')`)
5. En `tools_negocio.py`, envolver `_con_cursor` en try/except (`psycopg2.Error`) que devuelva
   un `_texto()` de error legible.
6. Verificar: levantar el dashboard, hacer 3 preguntas (deuda, ventas, una acción de gasto) y
   una con Postgres detenido. `python -m pytest -q` verde.

### Fase B — Memoria de conversación en el chat (1 sesión)
- Migrar `query()` → `ClaudeSDKClient` con sesión persistente por pestaña del dashboard
  (botón "Limpiar" ya existente = nueva sesión). Sin cambios en tools ni en el patrón de
  escritura. Diseñar spec corto antes (superpowers:brainstorming).

### Fase C — Al pasar
- `.env.example` (sin valores reales), pin de versión del SDK en requirements,
  nota en CLAUDE.md de que el agente corre aislado (`setting_sources=[]`) y por qué.

### Explícitamente NO hacer en H1
- Managed Agents / Vaults / Deployments (H2/H3).
- Pasar el chat a API key (la suscripción Pro cubre el uso actual).
- Reescrituras de arquitectura: la separación negocio/acciones/lienzo/agente está bien.

---

## Anexo — Fuentes verificadas
- `ClaudeAgentOptions` (docs vigentes, context7 `/anthropics/claude-agent-sdk-python`):
  `strict_mcp_config: bool = False`; `setting_sources: None` = carga todo, `[]` = aislamiento SDK.
- Código: `app/agent/orchestrator.py`, `app/agent/system_prompt.py`, `app/agent/tools_negocio.py`,
  `app/agent/tools_acciones.py`, `.mcp.json`.
- Entorno: `claude-agent-sdk 0.2.93` instalado; `.env`/`.mcp.json` ignorados por git.
