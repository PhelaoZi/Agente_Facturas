# Informe: InsForge (insforge.dev) y su utilidad para Agente Facturas

**Fecha:** 2026-07-14
**Fuentes:** sitio oficial, docs.insforge.dev, documentación técnica vía context7 (repo GitHub insforge/insforge), Y Combinator, prensa especializada.

---

## 1. Qué es InsForge

InsForge es una plataforma de **backend-as-a-service "agent-native"**: un
Supabase alternativo diseñado para que quien opere la infraestructura no sea
un humano con dashboard, sino un **agente de código** (Claude Code, Cursor,
Codex) a través de CLI y MCP. Su lema: "el AWS nativo para agentes".

La idea central: en vez de que tú configures base de datos, auth y hosting
clickeando en paneles, tu agente lo hace directo desde el editor — lee
esquemas, corre queries, crea tablas, deploya funciones — porque la
plataforma expone todo como herramientas MCP y comandos CLI.

## 2. La empresa

| Dato | Valor |
|------|-------|
| Fundación | 2025, San Francisco |
| Equipo | ~6 personas |
| Fundadores | Hang Huang (CEO, ex-PM de Amazon) y Tony Chang (CTO, ex-Databricks) |
| Financiamiento | Y Combinator (batch invierno 2026) + 1984 Ventures + MindWorks; ~USD $4M levantados |
| Licencia | Open source Apache 2.0, self-hosteable con Docker Compose |

**Lectura de riesgo:** empresa de ~1 año y 6 personas. Producto serio y bien
financiado, pero sin historial de permanencia. La licencia Apache 2.0 mitiga
el riesgo de desaparición (siempre puedes self-hostear tu instancia), pero
migrar datos tributarios de la empresa a la nube de una startup así exige
tener estrategia de salida (backups propios, que ya tienes como práctica).

## 3. Servicios que ofrece

| Servicio | Detalle |
|----------|---------|
| **Postgres administrado** | Con RLS, migraciones, pgvector (embeddings), y preview branches (probar cambios de esquema en rama aislada antes de producción) |
| **Auth** | Usuarios, sesiones, OAuth, JWT |
| **Storage** | Compatible S3, con políticas de acceso |
| **Edge Functions** | TypeScript sobre Deno; se invocan on-demand, por triggers de BD, o **por cron** (expresiones de 5 campos, con retry ante fallo) |
| **Realtime** | Suscripciones a cambios de BD, pub/sub, presencia |
| **Sites** | Hosting de frontends (respaldado por Vercel) |
| **AI Gateway** | API compatible OpenAI que rutea a múltiples LLMs con una sola clave y cuotas por proyecto |
| **Compute** | Contenedores de larga duración (lo más nuevo/verde de la plataforma) |

**Precios:** plan gratis (500 MB de BD, 1 GB storage, 5 GB ancho de banda,
50.000 MAU, $1 en créditos de IA) y plan Pro a **USD $25/mes** (8 GB BD, 100
GB storage, $10 créditos IA). Enterprise con SOC2/HIPAA.

**Integración con Claude Code:** MCP server HTTP (`https://mcp.insforge.dev/mcp`
en cloud, `localhost:7130` self-hosted) + CLI (`npx @insforge/cli link
--project-id ...`). Con eso el agente lee esquemas, ejecuta queries y deploya
funciones sin salir del editor — el mismo patrón que ya usas con el MCP de
postgres local, pero contra un backend cloud completo.

## 4. Utilidad para Agente Facturas — análisis honesto

### Estado actual del proyecto

Todo vive en tu PC: Postgres local (5432), scripts Python, dashboard en
localhost:8777, tareas programadas de Windows (backup 23:00, briefs). Un solo
usuario. **Y funciona.**

### Qué dolores reales resolvería

1. **Dependencia de tu PC.** El cron de edge functions correría el brief, el
   reporte semanal y los backups en la nube, con retry automático, aunque tu
   computador esté apagado. Es exactamente la limitación que encontramos al
   evaluar las tareas programadas de Cowork (no pueden ver tu BD local).
2. **Conexión con Cowork.** Si la BD viviera en InsForge, su MCP server HTTP
   podría agregarse como conector custom en claude.ai — y en principio las
   tareas programadas remotas de Cowork podrían consultar el ERP
   directamente, sin el puente a Drive. (Verificar: los conectores custom
   están disponibles en plan Pro, pero habría que probar que las tareas
   programadas los usan.)
3. **Acceso remoto.** Dashboard hosteado = verlo desde el celular o desde la
   cervecería. Auth/RLS = darle acceso de solo lectura al socio o al contador.
4. **Storage** para los XML DTE en vez de carpetas locales/OneDrive.

### Qué NO calza (los peros importantes)

1. **Tu stack es Python; las edge functions son TypeScript/Deno.** El
   pipeline (parse_dte, validate, sync) y el dashboard no se migran — se
   **reescriben**. Los contenedores Compute podrían correr Python, pero es la
   parte más inmadura de la plataforma.
2. **Migrar un sistema que funciona es un proyecto grande con riesgo real**,
   para una app que hoy usa una persona. El costo/beneficio no da si el único
   dolor es la automatización remota — eso se resuelve con el puente a Drive
   por una fracción del esfuerzo.
3. **Datos tributarios en la nube de una startup de 1 año.** Mitigable
   (self-host, backups), pero es una decisión de confianza, no solo técnica.
4. **Self-host elimina ese riesgo pero te convierte en sysadmin** de un VPS
   con Docker Compose — cambias un problema por otro.

### El escenario donde InsForge sí brilla para ti

Tu visión registrada es escalar esto y asesorar a otros en agentes. Si algún
día **productizas el ERP para otras cervecerías** (multi-cliente), InsForge es
candidato natural: auth multi-tenant, RLS por cervecería, hosting, y el flujo
agent-native calza con cómo ya trabajas (Claude Code construyendo todo). Para
ese producto nuevo — que nacería sin código legado Python-local — la
plataforma tiene mucho sentido.

## 5. Recomendación

- **Hoy: no migrar.** El sistema local funciona; el dolor de automatización
  remota tiene solución más barata (export a Drive + Cowork).
- **Experimento de bajo costo si quieres conocerla:** crea un proyecto gratis
  en InsForge y restaura ahí un **backup** de `dte_facturas_chile` (tu BD cabe
  de sobra en los 500 MB gratis). Como los scripts leen la conexión desde
  `.env`, apuntar una copia del proyecto a la BD cloud es cambio de
  configuración, no de código. Úsalo para probar el MCP remoto y el cron.
  **Nunca con la BD viva** — y ojo: el clon zigurat-erp comparte la BD con el
  original, así que el experimento debe usar una copia restaurada.
- **Reevaluar en serio solo si** decides productizar el ERP para terceros o
  si la dependencia del PC se vuelve un dolor diario.

## Fuentes

- https://insforge.dev/ y https://insforge.dev/pricing
- https://docs.insforge.dev/
- Documentación técnica (context7): github.com/insforge/insforge — mcp-setup.mdx, functions/schedules.mdx, deployment/
- https://www.ycombinator.com/companies/insforge
- https://www.ycombinator.com/launches/QP6-insforge-the-backend-platform-for-ai-native-developers
