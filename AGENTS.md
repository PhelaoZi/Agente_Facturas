# AGENTS.md

## Trabajo en paralelo — protocolo de coordinación (LEER PRIMERO)

En este repo trabajan **dos agentes distintos**, alternándose según los créditos
disponibles: **Claude Code** y **Antigravity**. Nunca asumas que el estado del
repositorio es solo obra tuya: puede haber trabajo ajeno, incluso sin commitear.

**No hay reparto de carpetas: los dos agentes pueden trabajar en cualquier
parte del proyecto** (app de escritorio, app del teléfono, scripts, lo que sea).
Christian decide quién hace qué según los créditos que tenga disponibles. Por eso
la coordinación depende de git, no de territorios.

### Las dos reglas

**1. Antes de empezar, mira qué dejó el otro.**

```bash
git status        # ¿hay trabajo ajeno sin commitear?
git log --oneline -5
```

Si encuentras cambios que tú no hiciste: **no los toques, no los reviertas, no
los mezcles con los tuyos**. Son trabajo del otro agente. Commitea solo tus
archivos (`git add <archivos>`, nunca `git add .` ni `git add -A`).

Si necesitas modificar un archivo que el otro dejó a medias, primero commitea su
versión tal cual (atribuida a él) y recién entonces haz tus cambios encima, en un
commit aparte. Así queda claro qué hizo cada uno y se puede deshacer por separado.

**2. Al terminar, commitea. Siempre.**

Un cambio sin commitear no tiene autor, ni fecha, ni forma de deshacerse, y el
otro agente puede destruirlo sin saberlo. Terminar una tarea = guardarla en git.
No lo dejes "para después".

### Cuándo usar una rama

Trabajar directo en `master` está bien para lo cotidiano. Usa una rama
(`git checkout -b <nombre>`) cuando: el cambio sea **grande o riesgoso**
(migraciones, cambiar el motor del chat, tocar el pipeline DTE), estés
**retomando o corrigiendo trabajo del otro agente**, o Christian quiera
revisarlo antes de que entre.

Antes de mergear a `master`: `python -m pytest -q` en verde (y
`deno test functions/ --allow-env --allow-net` si tocaste la nube).

### Invariantes que no se rompen

- **El agente del chat NUNCA escribe en la BD.** Las lecturas SQL van en sesión
  `readonly`; las escrituras solo por propose/confirm/execute (ver `app/CLAUDE.md`).
- **El pipeline DTE es secuencial:** `parse_dte` → `validate_changes` → `sync_db`.
  Nunca correr `sync_db.py` sin validar antes.
- **Credenciales solo en `.env`** (nunca en el código ni en commits).
- **La BD local es la fuente de verdad**; la nube es una réplica de solo lectura.

<!-- INSFORGE:START -->
## InsForge backend

This project uses [InsForge](https://insforge.dev): an all-in-one, open-source Postgres-based backend (BaaS) that gives this app a database, authentication, file storage, edge functions, realtime, an AI model gateway, and payments through one platform.

- **Project:** **zigurat-movil** (API base `https://z86cmn8g.us-west.insforge.app`)
- **Skills:** these InsForge skills are installed for supported coding agents. Reach for them before implementing any InsForge feature instead of guessing the API:
  - `insforge`: app code with the `@insforge/sdk` client (database CRUD, auth, storage, edge functions, realtime, AI, email, and Stripe payments).
  - `insforge-cli`: backend and infrastructure via the `insforge` CLI (projects, SQL, migrations, RLS policies, storage buckets, functions, secrets, payment setup, schedules, deploys).
  - `insforge-debug`: diagnosing failures (SDK/HTTP errors, RLS denials, auth and OAuth issues) and running security or performance audits.
  - `insforge-integrations`: wiring external auth providers (Clerk, Auth0, WorkOS, Better Auth, etc.) for JWT-based RLS, or the OKX x402 payment facilitator.
  - `find-skills`: discovering additional skills on demand.
- **Credentials:** app code reads keys from `.env.local`; the CLI reads `.insforge/project.json`. Never hardcode or commit keys.

Key patterns:

- Database inserts take an array: `insert([{ ... }])`.
- Reference users with `auth.users(id)`; use `auth.uid()` in RLS policies.
- For storage uploads, persist both the returned `url` and `key`.
<!-- INSFORGE:END -->
