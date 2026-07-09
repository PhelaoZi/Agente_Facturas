# Factibilidad: Zigurat ERP en el ecosistema Anthropic

- **Fecha:** 2026-07-06
- **Autor del análisis:** Claude Opus 4.8 (vía Claude Code)
- **Método:** `context7` (documentación oficial vigente del Claude Agent SDK), skill `claude-api` (referencia oficial de modelos/precios/superficies del Claude Developer Platform), y auditoría de código con el agente oficial `agent-sdk-dev:agent-sdk-verifier-py`.
- **Objetivo:** Evaluar la factibilidad y conveniencia de llevar/escalar todo el proyecto *Agente Facturas* dentro del ecosistema Anthropic, en tres horizontes:
  1. **H1** — uso personal actual (mono-usuario, Zigurat).
  2. **H2** — escalar a una cervecería/empresa más grande (multiusuario).
  3. **H3** — asesorar la implementación de agentes en otras empresas.

> Este documento es el insumo para una **revisión independiente por Claude Fable 5**, encargada de contrastar estas conclusiones con su propia mirada.

---

## 0) Reencuadre — no es una migración desde cero

El proyecto **ya vive ~a mitad de camino dentro del ecosistema**. Está construido con sus piezas canónicas:

- `app/agent/` corre sobre el **Claude Agent SDK** (`create_sdk_mcp_server` + `@tool` + `ClaudeAgentOptions` + `query()`).
- Usa **MCP** real: 1 servidor postgres + 3 servidores in-process (lienzo / negocio / acciones).
- El tooling operativo son **skills + hooks + plugins de Claude Code** (12+ skills).
- El patrón *propose/confirm/execute* de escritura segura es, en esencia, el modelo de permisos que Anthropic formalizó en su capa gestionada.

La pregunta real no es *"¿me muevo?"* sino **"¿hasta qué capa del stack subo, y para qué horizonte?"**.

**Lo que el ecosistema NO resuelve:** toda la capa de dominio chileno (parse DTE, ILA 20,5%, XML del SII, conciliación Itaú) es y seguirá siendo *bespoke*. El ecosistema acelera la capa *agente*, no la capa *tributaria*.

---

## Parte 1 — Panorama del ecosistema + costos

### 1a) Superficies disponibles

| Superficie | Qué es | Control / infra | ¿La usa Zigurat hoy? |
|---|---|---|---|
| **Claude API** (`/v1/messages`) | El ladrillo: llamadas sueltas, tool use, batch, archivos | Tú orquestas | Indirecto (vía SDK) |
| **Claude Agent SDK** | El *loop* del agente corre en TU máquina; tools MCP in-process | Tú hospedas | ✅ **Aquí vive** (`app/agent/`) |
| **Managed Agents** (beta) | Anthropic hospeda el loop + un contenedor por sesión; multi-tenant con *vaults*; *deployments* por cron; *outcomes* con rúbrica | Anthropic corre la infra | ❌ (candidato H2/H3) |
| **Agent Skills** | Instrucciones empaquetadas, portables a la API (`container.skills`) | Neutral | ✅ En Claude Code |
| **MCP** | Protocolo **abierto** para conectar datos/herramientas (anti-lock-in) | Neutral | ✅ Postgres + in-process |
| **Claude Code + plugins** | Capa dev/ops (skills, hooks, marketplace) | Tu máquina | ✅ A fondo |
| **claude.ai** (Projects / Connectors / Cowork) | Capa **no-code** para usuarios de negocio que no tocan código | Anthropic | ❌ (clave para H3) |
| **Claude en AWS / Bedrock / Vertex / Foundry** | Hosting enterprise con IAM/billing del cloud | Cloud provider | ❌ (solo si un cliente grande lo exige) |

### 1b) Costos — dos modelos de cobro distintos

**① Suscripción** (Claude Pro / Max / Team / Enterprise): tarifa plana. Cubre **Claude Code** y **claude.ai**. Es lo que Zigurat ya paga (Pro). El Agent SDK incluso puede correr con el login de suscripción (`ant auth login`) para uso personal/dev.

**② API (pago por token):** necesario para **producción / multiusuario / clientes**. Es **aparte** de la suscripción.

Precios de referencia API (por 1M tokens, dev platform, **cacheado 2026-06-24 — verificar en el pricing oficial antes de comprometer plata**):

| Modelo | Input | Output | Uso |
|---|---|---|---|
| Haiku 4.5 | $1 | $5 | Clasificar, tareas simples/rápidas |
| Sonnet 5 | $3 ($2 intro) | $15 ($10 intro) | Caballo de batalla |
| Opus 4.8 | $5 | $25 | Razonamiento duro, agéntico largo |
| Fable 5 | $10 | $50 | Lo más capaz |

Palancas de ahorro: **Batch API −50%**, **prompt caching** (lecturas ~0,1×), **ruteo de modelo por tarea**.

**Estimación para la escala actual (H1):** una consulta del chat ≈ 15–30k tokens de entrada (tools + contexto) + ~1–2k de salida → en Sonnet ≈ **$0,07–0,12 por consulta**, menos con caching. ~50 consultas/mes ≈ **unos pocos USD**. Los syncs DTE semanales son Python determinista → **$0 de LLM**.

➡️ **A esta escala, el costo es un no-problema.** La decisión es de *arquitectura* y *ambición*, no de plata.

### 1c) Veredicto por horizonte

| Horizonte | Mejor encaje | Veredicto |
|---|---|---|
| **H1 — hoy (mono-usuario)** | **Agent SDK local** (lo que ya hay) | ✅ Ya está. **No sobre-migrar.** Managed Agents aquí = costo + riesgo beta por cero beneficio. Mejora: ruteo de modelo + caching. |
| **H2 — empresa más grande** | **Agent SDK en servidor chico** (simple) *o* **Managed Agents** (si Anthropic corre la infra + aísla por sesión) | ✅ Factible. Para *una* empresa, self-hosting del SDK es más barato/simple. MA gana con aislamiento por cliente, corridas autónomas programadas, o no querer administrar servidores. |
| **H3 — asesor para terceros** | **Managed Agents** (multi-tenant, vaults por cliente, deployments) + **Agent Skills/plugins** (IP reutilizable) + **claude.ai Connectors/Cowork** (clientes sin código) | ✅ La tecnología está lista y calza. Lo difícil **no es técnico**: el activo reutilizable es la *arquitectura de agente* (propose/confirm/execute, wiki-como-cerebro, MCP-sobre-Postgres), NO la lógica del SII. Validar demanda con **un** negocio antes de montar infra. |

### 1d) Riesgos honestos

- **Churn de beta:** Managed Agents, compaction, etc. son beta; la API se mueve rápido (deprecaciones de modelo cada pocos meses, reformas de parámetros v1→v2). Construir algo crítico sobre beta = aceptar mantención constante.
- **Re-apuntar modelos periódicamente** (existe una guía de migración entera por esto).
- **Lock-in dosificado:** Agent SDK + MCP es portable (MCP es abierto); Managed Agents + Connectors de claude.ai es más pegajoso. Decidirlo a propósito.
- **Variabilidad de costo:** el pago por token puede dispararse con Opus intensivo; se acota con ruteo, caching, batch, *task budgets*.

---

## Parte 2 — Auditoría del agente (`app/agent/`)

**Veredicto: `PASS CON ADVERTENCIAS`.** SDK bien usado (`claude-agent-sdk==0.2.93`, al día). El invariante de seguridad *"el agente nunca escribe en la BD"* **se cumple en el código real, no solo en el CLAUDE.md.** Los problemas son de configuración y manejo de errores, no de arquitectura.

### Verificado en código (no en promesas)
- Las 6 tools `proponer_*` solo publican un `Artifact`; ninguna abre conexión de escritura ni ejecuta `INSERT/UPDATE/DELETE`. La escritura vive exclusivamente en `POST /api/ejecutar-accion` (separa `ValueError`→400 de error BD→500, cierra conexión en `finally`).
- `permission_mode="bypassPermissions"` es **coherente**: ninguna tool registrada escribe.
- 3 servidores MCP in-process correctos, `allowed_tools` con formato `mcp__server__tool`, `max_turns=20`, credenciales fuera del repo (`.env` y `.mcp.json` en `.gitignore`), manejo de error de alto nivel en `run_agent()`.

### 🔴 Críticos (2) — aislamiento, ~1 línea cada uno
1. **`strict_mcp_config` no seteado** (`orchestrator.py:51`). El repo tiene un `.mcp.json` con un **segundo** servidor `postgres` de credenciales hardcodeadas y **distintas** (`postgres:<clave>@...`). Sin `strict`, el SDK puede combinarlo/chocarlo con el que arma desde `.env`. No determinista desde el código. → `strict_mcp_config=True`.
2. **`setting_sources` no seteado** (`orchestrator.py:51`). Con default `None`, el agente carga **el CLAUDE.md completo** en cada turno, encima del `SYSTEM_PROMPT` de 90 líneas. Infla tokens, puede confundir al modelo, no queda documentado en código. → `setting_sources=[]`. **(Este hallazgo se cruza con el costo: fijarlo abarata cada consulta y hace el agente 100% reproducible.)**

### 🟡 Advertencias (5)
3. Las 18 tools de `tools_negocio.py` + `publish_tools.py` no envuelven BD en try/except (solo `tools_acciones.py` lo hace). Un Postgres caído aborta todo el turno en vez de degradar. *(Media)*
4. **Decisión de producto:** el chat es *stateless* (`query()` sin memoria); no recuerda preguntas anteriores del mismo chat. Ya documentado como decisión en `dashboard_ui.html:712`. ¿Aceptable, o migrar a `ClaudeSDKClient` + `resume`? *(Media — del usuario)*
5. Falta `.env.example`. *(Baja)*
6. No hay `README.md` de onboarding humano. *(Baja)*
7. `cli_path` de `shutil.which("claude")` no se loguea. *(Baja)*

---

## Síntesis

- **Factibilidad técnica: alta.** El proyecto ya está dentro del ecosistema y el código pasa una auditoría oficial. Lo que falta son *quick wins* de hardening (2 líneas para los críticos), no reescrituras.
- **H1:** aplicar los 2 críticos + try/except en tools → agente más barato, reproducible y robusto.
- **H3:** la arquitectura de escritura-segura es el activo vendible. Empaquetarla como Skill/plugin cuando se decida validar.
- **El costo no es la barrera; la ambición y el modelo de negocio (para H3) sí lo son.**
