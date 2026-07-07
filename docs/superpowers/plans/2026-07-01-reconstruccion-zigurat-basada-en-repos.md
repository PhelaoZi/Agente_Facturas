# Plan: Reconstruir Zigurat ERP basándose en agency-agents y claude-howto

**Fecha:** 2026-07-01
**Estado:** Propuesta (no iniciado)

---

## 1. Hallazgo clave antes de empezar

Ninguno de los dos repositorios en `github-repository/` es una aplicación:

| Repo | Qué es realmente | Qué aporta |
|------|-----------------|------------|
| `agency-agents` | Biblioteca de ~300 **subagentes** (personas especializadas en Markdown) organizados en 16 divisiones (finanzas, ventas, soporte, ingeniería...). Se instalan en `.claude/agents/`. | Plantillas de agentes especialistas con identidad, reglas críticas, entregables y métricas de éxito. |
| `claude-howto` | **Tutorial** de las 10 características de Claude Code: slash commands, memoria, skills, subagentes, MCP, hooks, plugins, checkpoints, features avanzadas y CLI. Incluye plantillas copy-paste y 3 plugins completos de ejemplo. | Patrones de arquitectura para apps nativas de Claude Code, en especial el **empaquetado como plugin** (`commands/ + agents/ + hooks/ + mcp/ + scripts/`). |

**Consecuencia:** "construir la misma aplicación con base en los repos" no significa
reescribir el código Python — significa **reconstruir la capa de agentes** del
proyecto usando los patrones de ambos repos. El pipeline Python
(parse → validate → sync, conciliación, costos, wiki, dashboard) es el activo
real y se porta intacto.

## 2. Diagnóstico: qué usa hoy el proyecto vs. qué proponen los repos

| Característica Claude Code | Hoy en Agente_Facturas | Según los repos |
|---------------------------|------------------------|-----------------|
| Skills / slash commands | ✅ 16 skills maduras | Igual — ya está al nivel del tutorial |
| Memoria (CLAUDE.md) | ✅ Muy completa (reglas SQL canónicas, invariantes) | Igual — es un ejemplo del patrón |
| MCP | ✅ postgres read-only | Igual |
| Hooks | ⚠️ Solo 1 (PreToolUse protege changes.json) | claude-howto módulo 06: SessionStart, PostToolUse, Stop, etc. |
| **Subagentes** (`.claude/agents/`) | ❌ No existe | agency-agents: el corazón del repo |
| **Plugin empaquetado** | ❌ Todo suelto en `.claude/` | claude-howto módulo 07: bundle instalable y portable |
| CLI headless (`claude -p`) | ❌ Tareas programadas usan Python puro | claude-howto módulo 10 |
| Orquestación multi-agente | ⚠️ Solo el agente del dashboard (Agent SDK) | agency-agents: Agents Orchestrator + equipos por escenario |

## 3. Plan por fases

### Fase 0 — Fundaciones (portar el núcleo)

Crear la nueva estructura (repo nuevo `zigurat-erp` o reorganización in-place)
con el layout de plugin de `claude-howto/07-plugins`:

```
zigurat-erp/
  .claude-plugin/plugin.json      # manifiesto del plugin
  commands/                        # los 16 slash commands actuales
  agents/                          # NUEVO — equipo de subagentes (Fase 1)
  skills/                          # skills con scripts (sync-facturas, etc.)
  hooks/                           # hooks ampliados (Fase 3)
  mcp/postgres-config.json         # config MCP actual
  scripts/                         # pipeline Python intacto
  app/                             # dashboard + Agent SDK intacto
  tests/                           # suite pytest intacta
```

- **No se reescribe ni una línea de lógica de negocio.** `scripts/`, `app/`,
  `tests/` se copian tal cual y se verifica `python -m pytest -q` en verde.
- CLAUDE.md se conserva (ya sigue el patrón del módulo 02-memory).
- Criterio de salida: pipeline completo corre igual que hoy en la estructura nueva.

### Fase 1 — Equipo de subagentes (adaptados de agency-agents)

Crear `.claude/agents/` con 5–6 especialistas, **traducidos al negocio y en
español**, usando como base los agentes del repo:

| Agente nuevo | Base en agency-agents | Responsabilidad en Zigurat |
|--------------|----------------------|---------------------------|
| `contador-conciliador` | `finance/finance-bookkeeper-controller.md` (Dana) | Conciliación bancaria, cierre mensual, custodio del invariante `conciliaciones ⟹ fecha_pago` |
| `analista-financiero` | `finance/finance-financial-analyst.md` + `support/support-finance-tracker.md` | Flujo de caja, márgenes por SKU, análisis de variaciones |
| `extractor-datos` | `specialized/sales-data-extraction-agent.md` | Import de cartolas Itaú y XMLs DTE: mapeo flexible de columnas, log de cada import, nunca corromper datos existentes |
| `gestor-cobranza` | `finance/finance-bookkeeper-controller.md` (sección AR) + wiki de clientes | Seguimiento de morosos, aging, propuestas de recordatorio según patrón de pago del cliente |
| `reportero-ejecutivo` | `support/support-executive-summary-generator.md` + `support/support-analytics-reporter.md` | Brief diario, reporte semanal, resúmenes para decisiones |
| `db-guardian` (opcional) | `engineering/engineering-database-optimizer.md` | Mantenimiento del esquema, índices, revisión de queries nuevas |

Reglas de adaptación (críticas):

1. Los agentes del repo hablan de GAAP/QuickBooks/1099 — se reescriben para la
   realidad chilena: SII, DTE, IVA 19%, ILA 20,5%, estructura de dos líneas
   (producto + logística).
2. **Cada agente lleva embebidas las reglas SQL canónicas** (COALESCE de montos
   ajustados, `tipo_documento != 61`, `fecha_pago` como única fuente de verdad).
   Los subagentes tienen contexto propio: si las reglas no van en su prompt,
   repetirán los errores históricos que motivaron esas reglas.
3. Se conserva el formato del repo (frontmatter + identidad + misión + reglas
   críticas + entregables + métricas) porque funciona bien como prompt.
4. **Todos los agentes son de solo lectura sobre la BD.** El invariante del
   dashboard (el agente propone, un endpoint determinista ejecuta) se mantiene
   intocable.

### Fase 2 — Empaquetado como plugin

Siguiendo `claude-howto/07-plugins` (ejemplos `pr-review`, `devops-automation`):

- Manifiesto `plugin.json` que agrupa commands + agents + skills + hooks + mcp.
- Beneficios concretos: instalar el sistema completo en otro notebook con un
  comando, versionar la capa de agentes junto al código, y reutilizar el plugin
  si algún día se replica para otro negocio.
- Las 16 skills actuales se mantienen como skills (tienen scripts propios);
  los comandos simples pueden migrar a `commands/*.md`.

### Fase 3 — Hooks ampliados (módulo 06 de claude-howto)

Conservar el hook actual (PreToolUse bloquea edición de `changes.json`) y agregar:

| Hook | Evento | Función |
|------|--------|---------|
| `session-start.py` | SessionStart | Mostrar estado al abrir sesión: XMLs pendientes en `facturas-ventas/`, último backup OK, brief del día |
| `post-sync.py` | PostToolUse (tras sync_db) | Recordar/disparar `wiki_update.py --ruts` si el pipeline no lo hizo |
| `stop-push-reminder.py` | Stop | Recordar push a GitHub al cerrar sesión (resuelve el atraso crónico de commits locales registrado en memoria) |
| `pre-commit-tests.sh` | PreToolUse (git commit) | Correr `python -m pytest -q` antes de commitear |

### Fase 4 — CLI headless para automatización (módulo 10)

- `claude -p "/monitoreo-facturas"` como tarea programada: detección y sync
  automático de XMLs pendientes sin abrir sesión interactiva.
- **Advertencia de costo/robustez:** lo determinista (backup, brief diario) ya
  funciona con Python puro y tareas de Windows — se queda así. El modo headless
  se reserva para lo que realmente necesita juicio del agente (clasificar
  facturas de compra ambiguas, detectar anomalías).

### Fase 5 — Orquestación multi-agente (patrón "escenarios" de agency-agents)

Comandos que coordinan al equipo, imitando los "Real-World Use Cases" del README
de agency-agents:

- `/cierre-mes`: `contador-conciliador` (reconciliar banco completo) →
  `analista-financiero` (variaciones mes a mes, márgenes) →
  `reportero-ejecutivo` (informe de cierre en `briefs/`).
- `/campaña-cobranza`: `gestor-cobranza` (aging + priorización con la wiki) →
  propuestas de recordatorio por cliente → confirmación humana antes de enviar.

## 4. Qué NO hacer

- **No reescribir el pipeline Python.** Los repos no aportan código de
  aplicación; aportan capa de agentes. Reescribir parse/validate/sync sería
  riesgo puro sin beneficio.
- **No instalar los ~300 agentes del repo.** Solo los 5–6 adaptados; el resto
  es ruido de contexto.
- **No copiar agentes tal cual:** están en inglés y con supuestos contables de
  EE.UU. La adaptación al contexto chileno es la mitad del trabajo.
- **No darle permisos de escritura a ningún subagente sobre la BD.** El patrón
  propose/confirm/execute del dashboard es la referencia de seguridad.

## 5. Orden sugerido y esfuerzo estimado

| Fase | Esfuerzo | Valor | Prioridad |
|------|----------|-------|-----------|
| 0 — Fundaciones | 1 sesión | Base necesaria | 1º |
| 1 — Subagentes | 2–3 sesiones (la adaptación es lo caro) | Alto: especialistas reutilizables | 2º |
| 3 — Hooks | 1 sesión | Alto: automatiza dolores conocidos (push, wiki) | 3º |
| 5 — Orquestación | 1–2 sesiones | Alto: cierre de mes de punta a punta | 4º |
| 2 — Plugin | 1 sesión | Medio: portabilidad | 5º |
| 4 — Headless | 0,5 sesión | Medio: solo para casos con juicio | 6º |

Las fases 1, 3 y 5 son las que cambian el día a día del negocio; 2 y 4 son
infraestructura que puede esperar.
