# Spec: Zigurat Móvil — dashboard en la nube con chat de consultas

**Fecha:** 2026-07-14
**Estado:** diseño aprobado (Opción A para el chat)
**Contexto previo:** `docs/informe-insforge-2026-07-14.md`

---

## 1. Objetivo

Construir una versión en la nube del Centro de Comando, accesible desde el
celular, con dos capacidades:

1. **Vistas de lectura** móviles: KPIs, cobranza, ventas, flujo de caja.
2. **Chat de consultas** en español (solo lectura) contra los datos del negocio.

Objetivo secundario explícito: **aprender a construir una aplicación cloud
"de verdad"** sobre una plataforma agent-native (InsForge), usando Claude Code
como constructor.

## 2. Decisiones ya tomadas

| Decisión | Elección | Alternativa descartada |
|----------|----------|------------------------|
| Alcance v1 | Vistas de lectura + chat de consultas | Réplica completa con acciones de escritura |
| Plataforma | InsForge cloud (plan gratis), stack TS/React | Subir el dashboard Python a un PaaS |
| Datos | Réplica de solo lectura; el Postgres local sigue siendo fuente de verdad | Migrar la BD a la nube |
| Chat | **Opción A**: tool-use loop con la Messages API de Anthropic en una edge function | Opción B: portar el orquestador Agent SDK a un contenedor Compute |

## 3. Arquitectura

```
[PC local — pipeline intacto]
  parse_dte → validate → sync_db → Postgres local (fuente de verdad)
                                         ↓
                               scripts/sync_nube.py  (paso nuevo, no fatal)
                                         ↓ truncate+copy transaccional
[InsForge cloud]
  Postgres réplica ← views canónicas (reglas de negocio en SQL)
  Edge Functions (Deno/TS): /api/kpis /api/pendientes /api/ventas /api/flujo /api/chat
  Auth (email/clave, un usuario)
  Sites: PWA React
                                         ↓ HTTPS + JWT
[Celular]  PWA instalable: 4 vistas + chat
```

## 4. Componentes

### 4.1 Réplica de datos — `scripts/sync_nube.py` (Python, corre local)

- **Tablas replicadas (v1):** `ventas`, `clientes`, `productos`,
  `movimientos_banco`, `conciliaciones`, `cuentas_por_pagar`. (Las tablas de
  costos quedan fuera — las vistas v1 no las necesitan.)
  > Ajuste 2026-07-18: `movimientos_banco` originalmente quedaba fuera, pero
  > la FK `conciliaciones.movimiento_banco_id → movimientos_banco(id)` obliga
  > a replicarla (descubierto en la primera réplica real).
- **Método:** por cada tabla, `TRUNCATE` + `COPY`/insert masivo dentro de **una
  transacción** (la BD es chica; el refresh completo toma segundos y evita
  lógica de diffs). Orden de carga respetando FKs.
- **Conexión:** string de conexión al Postgres de InsForge en `.env`
  (`INSFORGE_DB_URL`), cargado con `_load_env()` como el resto de scripts.
- **Disparadores:**
  - La skill `/sync-facturas` (y `/sync-nc`, `/conciliar-banco`) agregan un
    paso final que ejecuta `sync_nube.py`. **No fatal:** si no hay internet,
    warning y el pipeline local termina OK. `sync_db.py` NO se modifica.
  - Tarea programada de Windows diaria (red de seguridad), patrón de
    `backup_db.py`.
- Si el PC está apagado, la nube muestra datos del último sync. Aceptable:
  es una app de consulta.

### 4.2 Reglas de negocio como views en la BD réplica

Creadas por un script de migración idempotente (`scripts/migrate_nube_views.sql`
aplicado por `sync_nube.py --init`). Las reglas críticas viven UNA vez, en SQL:

- `v_ventas_reales` — una fila por factura con
  `COALESCE(monto_total_ajustado, monto_total)` y neto equivalente,
  excluyendo `tipo_documento = 61`.
- `v_pendientes` — facturas por cobrar (`fecha_pago IS NULL`, total ajustado
  > 0) con días de atraso y datos del cliente.
- `v_dias_pago_cliente` — promedio de días de pago histórico por cliente
  (insumo del flujo).
- `v_ventas_producto` — líneas de `productos` con el filtro canónico
  anti-Logistica/PET (`NOT ILIKE '%logist%'`, regex PET del CLAUDE.md raíz).

Las edge functions solo hacen `SELECT` sobre views — nunca reimplementan
reglas.

### 4.3 Edge functions de consulta (Deno/TS)

- `GET /api/kpis` — ventas del mes, por cobrar total, facturas vencidas, caja
  proyectada de la semana.
- `GET /api/pendientes` — lista de `v_pendientes` ordenada por fecha.
- `GET /api/ventas?periodo=` — serie mensual + ranking de clientes y productos.
- `GET /api/flujo` — proyección 4 semanas: cobros esperados
  (`v_pendientes` × `v_dias_pago_cliente`) menos `cuentas_por_pagar`,
  replicando la lógica de `flujo_caja.py`.
- Todas verifican el JWT de InsForge Auth; sin token → 401.

### 4.4 Chat — Opción A: tool-use loop con la Messages API

- Edge function `POST /api/chat` que corre un loop de tool-use con la API de
  Anthropic (modelo Sonnet por defecto, configurable vía secret).
- **Herramientas (espejo de las tools canónicas del dashboard local, solo
  lectura):** `deuda_total`, `deuda_cliente`, `ranking_deudores`,
  `facturas_vencidas`, `ventas_total`, `ranking_clientes`, `ventas_cliente`,
  `ventas_producto`, `flujo_caja`, `listar_gastos`. Cada una es una función TS
  que consulta las views. **El modelo nunca genera SQL libre.**
- **System prompt:** adaptación del de `app/agent/system_prompt.py` (reglas de
  negocio, estructura de facturación, tono), recortado a consultas de lectura.
- **Historial:** tabla `chat_sesiones(id, mensajes jsonb, actualizado)` en la
  BD réplica — única tabla donde la nube escribe. Botón "limpiar" en la UI.
- **v1 sin streaming:** respuesta completa con indicador de "pensando"
  (las consultas toman 5–20 s). Streaming SSE queda como mejora futura.
- **Control de costo:** límite de iteraciones del loop (máx. 8 llamadas a
  tools), `max_tokens` acotado, y log de uso por consulta en
  `chat_sesiones`.

### 4.5 Frontend — PWA React

- Vite + React, mobile-first, hosteada en InsForge Sites. Manifest + service
  worker mínimo → instalable con ícono en el celular.
- Pantallas: **Inicio** (KPIs), **Cobranza** (pendientes con días de atraso),
  **Ventas** (gráfico mensual + rankings), **Flujo** (4 semanas), **Chat**.
- Gráficos con librería liviana (Recharts); aplicar la skill `dataviz` al
  implementarlos.
- Login con InsForge Auth (email/clave); sesión persistente en el dispositivo.

## 5. Seguridad

- Todo endpoint y toda vista requieren usuario autenticado; un solo usuario
  registrado (Christian). Registro público deshabilitado.
- RLS: denegar todo al rol anónimo; las edge functions acceden con service
  role tras verificar el JWT.
- La BD réplica solo recibe escrituras desde `sync_nube.py` (credencial
  directa de Postgres) y la tabla `chat_sesiones` desde `/api/chat`.
- `ANTHROPIC_API_KEY` e `INSFORGE_DB_URL` como secrets (edge functions y
  `.env` local respectivamente). Nada de claves en el frontend ni en git.
- Datos tributarios en nube de startup joven: mitigado por backups locales
  existentes + réplica descartable (se puede borrar el proyecto InsForge sin
  perder nada).

## 6. Costos

- InsForge plan gratis (500 MB BD ≫ tamaño real).
- API de Anthropic para el chat: requiere key de console.anthropic.com con
  facturación por uso (el agente local usa la suscripción; esto no).
  Estimación: $0,01–0,05 USD por consulta con Sonnet.

## 7. Fases de entrega

| Fase | Entregable verificable | Estimación |
|------|------------------------|------------|
| 0 | Proyecto InsForge creado, backup restaurado, MCP de InsForge conectado a Claude Code | ½ sesión |
| 1 | `sync_nube.py` + views + tarea programada; cifras de views = cifras locales | 1 sesión |
| 2 | Edge functions de consulta con tests; respuestas correctas vía curl | 1–2 sesiones |
| 3 | PWA con auth y 4 vistas, instalada y funcionando en el celular | 2–3 sesiones |
| 4 | Chat operativo con tools canónicas e historial | 1–2 sesiones |

Cada fase deja valor usable; desde la fase 3 el dashboard ya sirve en el
celular sin chat.

## 8. Criterios de éxito

1. Abrir la PWA en el celular, hacer login y ver las 4 vistas.
2. **Paridad de cifras:** los totales de la nube coinciden exactamente con
   `/consultar-ventas` local al momento del último sync (test de aceptación
   comparando ambos lados).
3. El chat responde consultas de negocio usando solo tools (verificable en
   logs) y rechaza pedidos de escritura.
4. Un sync completo local→nube tarda < 60 s y no rompe el pipeline si falla.

## 9. Fuera de alcance (v1)

- Acciones de escritura (marcar pagos, gastos) y el patrón propose/confirm.
- Memoria de largo plazo del agente (memoria-agente/).
- Vistas de costos/márgenes (las tablas de costos no se replican).
- Streaming del chat, multi-usuario, notificaciones push.

## 10. Riesgos

| Riesgo | Mitigación |
|--------|-----------|
| InsForge (startup ~1 año) cambia o desaparece | Réplica descartable; open source self-hosteable; datos maestros siempre locales |
| Datos desactualizados en la nube | Timestamp de último sync visible en la UI |
| Costo del chat se descontrola | Límite de iteraciones, log de uso, key con límite de gasto en console.anthropic.com |
| Divergencia de reglas de negocio entre local y nube | Reglas solo en views SQL + test de paridad de cifras (criterio 2) |
