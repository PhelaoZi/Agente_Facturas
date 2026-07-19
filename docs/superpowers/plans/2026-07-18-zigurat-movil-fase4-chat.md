# Zigurat Móvil — Fase 4: Chat de consultas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chat en español en la PWA del celular que responde consultas de negocio (deuda, ventas, flujo, gastos) vía un tool-use loop con la Messages API de Anthropic, corriendo en una edge function de InsForge — solo lectura, con historial persistente y tope de gasto diario.

**Architecture:** Una edge function `POST /chat` verifica el JWT, corre un loop de tool-use (máx. 8 iteraciones) contra la Messages API con 10 herramientas que solo hacen `SELECT` sobre las views canónicas de la réplica, persiste el historial en `chat_sesiones` y el costo por consulta en `chat_uso` (base del tope diario). La PWA agrega una 5ª pestaña "Chat". El código fuente es modular (`functions/_shared/`, testeable con `deno test`); el deploy usa `deno bundle` para producir un solo archivo (verificado: `deno bundle` en Deno 2.9.3 empaqueta imports `npm:` sin problema).

**Tech Stack:** Deno 2.9.3 (edge functions InsForge), `npm:postgres@3.4.5`, `npm:jose@5`, Messages API vía `fetch` directo (sin SDK — menos dependencias en el runtime edge), React 19 + Vite (PWA existente), psycopg2 (scripts locales), pytest + `deno test`.

## Global Constraints

- Reglas canónicas de negocio SOLO en las views SQL (`v_pendientes`, `v_ventas_reales`, `v_ventas_producto`, `v_flujo_pendientes`, `v_dias_pago_cliente`) — las tools del chat NUNCA reimplementan `COALESCE`/exclusión de NC/filtro Logistica-PET, solo consultan views. Única tabla base permitida en tools: `cuentas_por_pagar` (con `WHERE pagado = FALSE`, igual que `functions/flujo.ts`).
- El chat es SOLO LECTURA: ninguna tool hace INSERT/UPDATE/DELETE sobre datos de negocio. Las únicas escrituras de la nube son `chat_sesiones` y `chat_uso`.
- `chat_sesiones` y `chat_uso` NUNCA se agregan a `TABLAS_ORDEN` de `scripts/sync_nube.py` (el sync las truncaría y borraría el historial).
- El modelo NUNCA genera SQL libre: no existe tool de SQL; solo las 10 tools con queries fijas.
- Tool-use loop: máximo 8 iteraciones, `max_tokens: 1024` por llamada, historial recortado a los últimos 20 mensajes al llamar la API.
- Modelo por defecto `claude-sonnet-5`, siempre overrideable con el secret `CHAT_MODELO` (si el ID cambia, se corrige con un env var, sin redeploy de código).
- Secrets solo como env vars de InsForge / `.env` local: `ANTHROPIC_API_KEY`, `INSFORGE_DB_URL`, `INSFORGE_JWT_SECRET`, `JWT_PUBLIC_KEY`, opcionales `CHAT_MODELO`, `CHAT_LIMITE_DIARIO_USD`, `CHAT_PRECIO_IN_USD_MTOK`, `CHAT_PRECIO_OUT_USD_MTOK`. Nada de claves en git ni en el frontend.
- Pin de dependencias igual al código existente: `npm:postgres@3.4.5`, `npm:jose@5`, `jsr:@std/assert@1`.
- Idioma: UI y textos al usuario en español; identificadores siguiendo el estilo ya establecido del repo (`proyectarFlujo`, `ejecutarTool`, `aplicar_esquema`).
- Todo en una rama nueva desde `master` (ej: `fase-4-chat-nube`); commits chicos en español.

## Estado actual (contexto para el implementador)

- Las 4 edge functions desplegadas (`functions/{kpis,pendientes,ventas,flujo}.ts`) son **auto-contenidas** (inlinean `corsHeaders`, `db()`, `requireUser()`) porque el deploy de archivo único de InsForge no resuelve imports relativos. `functions/_shared/` conserva las versiones modulares + `flujo_test.ts`.
- `requireUser` actual acepta RS256 (tokens reales de InsForge Auth, verifica con `JWT_PUBLIC_KEY`) y HS256 (secret propio `INSFORGE_JWT_SECRET`, usado por `scripts/test_paridad_nube.py`). Tiene dos debilidades a endurecer (Task 1).
- `scripts/sync_nube.py` replica 6 tablas (`TABLAS_ORDEN`) y con `--init` hace bootstrap del esquema + `scripts/migrate_nube_views.sql`. `--init` es de una sola vez (falla si las tablas ya existen) — por eso la migración del chat se aplica en CADA corrida (idempotente), no en `--init`.
- La PWA (`nube/pwa/`) es React+Vite con 4 pestañas en `App.tsx` (591 líneas), cliente API en `src/api.ts` (`invocarEdgeFunction` solo hace GET hoy), login con `@insforge/sdk`.
- El test de aceptación `scripts/test_paridad_nube.py` firma un JWT HS256 con `INSFORGE_JWT_SECRET` (`--solo-token` lo imprime).

**Desviaciones acordadas respecto de la spec** (sección 4.4 de `docs/superpowers/specs/2026-07-14-zigurat-movil-nube-design.md`):
1. El log de uso va en una tabla propia `chat_uso` (no dentro de `chat_sesiones`): permite sumar el gasto del día con un índice, que es la base del tope diario.
2. Se agrega un **tope de gasto diario en el servidor** (`CHAT_LIMITE_DIARIO_USD`, default US$1/día) además del límite de iteraciones — la spec solo pedía "límite de gasto en console.anthropic.com", esto es la red de seguridad dentro de la app.
3. El historial persiste solo pares `{role, content}` de texto (pregunta del usuario + respuesta final), NO los intercambios de tools — mantiene bajos los tokens de entrada; el modelo re-consulta tools si necesita el dato de nuevo.
4. De las tools de la spec, `flujo_caja` no recibe `saldo_inicial` (usa siempre el saldo de `sync_meta`, igual que el endpoint `/flujo`).

**Prerequisitos de ejecución (manuales, de Christian):**
1. API key de console.anthropic.com creada y con saldo (US$5 alcanza) + límite de gasto mensual configurado allá (recomendado US$10).
2. Para los pasos de deploy (Task 6): el MCP de InsForge autorizado en la sesión (`/mcp`) **o** acceso al dashboard de InsForge para pegar el código a mano.

---

### Task 1: Endurecer `requireUser` (JWT) en `_shared` y en las 4 functions desplegadas

Dos debilidades del código actual: (a) `jwtVerify` se llama sin fijar `algorithms`, confiando en el `alg` del header del token; (b) si `INSFORGE_JWT_SECRET`/`JWT_SECRET` no están seteados, verifica HS256 con clave vacía (`""`) en vez de fallar como error de configuración. Con el chat gastando dólares reales por request, la auth debe ser estricta.

**Files:**
- Modify: `functions/_shared/auth.ts`
- Create: `functions/_shared/auth_test.ts`
- Modify: `functions/kpis.ts`, `functions/pendientes.ts`, `functions/ventas.ts`, `functions/flujo.ts` (cada una tiene su copia inline de `requireUser`)

**Interfaces:**
- Consumes: nada (task independiente).
- Produces: `requireUser(req: Request): Promise<Response | null>` endurecido en `functions/_shared/auth.ts` — misma firma que hoy; Task 5 lo importa tal cual.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `functions/_shared/auth_test.ts`:

```ts
// functions/_shared/auth_test.ts
// La auth debe fijar el algoritmo por rama y tratar un secret HS256 ausente
// como error de configuracion (nunca verificar con clave vacia).
import { assertEquals } from "jsr:@std/assert@1";
import { generateKeyPair, exportSPKI, SignJWT } from "npm:jose@5";
import { requireUser } from "./auth.ts";

function reqCon(token: string | null): Request {
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return new Request("http://local/api", { headers });
}

async function status(token: string | null): Promise<number | null> {
  const r = await requireUser(reqCon(token));
  return r === null ? null : r.status;
}

Deno.test("RS256 valido firmado con la clave privada pasa", async () => {
  const { publicKey, privateKey } = await generateKeyPair("RS256", { extractable: true });
  Deno.env.set("JWT_PUBLIC_KEY", await exportSPKI(publicKey));
  const token = await new SignJWT({ sub: "u1" })
    .setProtectedHeader({ alg: "RS256" }).setExpirationTime("5m").sign(privateKey);
  assertEquals(await status(token), null);
});

Deno.test("HS256 valido con el secret del servidor pasa", async () => {
  Deno.env.set("INSFORGE_JWT_SECRET", "secreto-de-prueba");
  const token = await new SignJWT({ sub: "u1" }).setProtectedHeader({ alg: "HS256" })
    .setExpirationTime("5m").sign(new TextEncoder().encode("secreto-de-prueba"));
  assertEquals(await status(token), null);
});

Deno.test("HS256 sin secret configurado es error de configuracion", async () => {
  Deno.env.delete("INSFORGE_JWT_SECRET");
  Deno.env.delete("JWT_SECRET");
  const token = await new SignJWT({ sub: "u1" }).setProtectedHeader({ alg: "HS256" })
    .setExpirationTime("5m").sign(new TextEncoder().encode("cualquier-cosa"));
  const r = await requireUser(reqCon(token));
  assertEquals(r?.status, 401);
  const body = JSON.parse(await r!.text());
  assertEquals(body.detalle, "error de configuracion en servidor");
});

Deno.test("alg none rechazado", async () => {
  const header = btoa(JSON.stringify({ alg: "none" })).replace(/=+$/, "");
  const payload = btoa(JSON.stringify({ sub: "u1" })).replace(/=+$/, "");
  assertEquals(await status(`${header}.${payload}.x`), 401);
});

Deno.test("sin token 401", async () => {
  assertEquals(await status(null), 401);
});
```

- [ ] **Step 2: Correr los tests y verificar que falla el de configuración**

Run: `deno test -A functions/_shared/auth_test.ts`
Expected: FAIL en "HS256 sin secret configurado es error de configuracion" (hoy devuelve `detalle: "token invalido"`). Los demás pasan.

- [ ] **Step 3: Endurecer `functions/_shared/auth.ts`**

Reemplazar el contenido completo por:

```ts
// functions/_shared/auth.ts
// Verifica el JWT: RS256 (InsForge Auth, clave publica) o HS256 (secret propio,
// tests de paridad). Devuelve null si es valido, o una Response 401 lista.
// Endurecido: cada rama fija su algoritmo en jwtVerify (`algorithms`) y un
// secret HS256 ausente es error de configuracion, nunca clave vacia.
import { jwtVerify, importSPKI } from "npm:jose@5";
import { corsHeaders } from "./cors.ts";

export async function requireUser(req: Request): Promise<Response | null> {
  const header = req.headers.get("Authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) return sin401("falta token");
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return sin401("token malformado");
    const headerObj = JSON.parse(atob(parts[0]));
    const alg = headerObj.alg;

    if (alg === "RS256") {
      const publicKeyPEM = Deno.env.get("JWT_PUBLIC_KEY");
      if (!publicKeyPEM) {
        console.error("requireUser: falta JWT_PUBLIC_KEY en el servidor");
        return sin401("error de configuracion en servidor");
      }
      const publicKey = await importSPKI(publicKeyPEM, "RS256");
      await jwtVerify(token, publicKey, { algorithms: ["RS256"] });
    } else if (alg === "HS256") {
      const secretStr = Deno.env.get("INSFORGE_JWT_SECRET") ?? Deno.env.get("JWT_SECRET") ?? "";
      if (!secretStr) {
        console.error("requireUser: falta INSFORGE_JWT_SECRET en el servidor");
        return sin401("error de configuracion en servidor");
      }
      await jwtVerify(token, new TextEncoder().encode(secretStr), { algorithms: ["HS256"] });
    } else {
      return sin401("algoritmo no soportado");
    }
    return null;
  } catch (err) {
    console.error("requireUser: token invalido:", (err as Error)?.message ?? err);
    return sin401("token invalido");
  }
}

function sin401(detalle: string): Response {
  return new Response(JSON.stringify({ error: "no autorizado", detalle }), {
    status: 401,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}
```

- [ ] **Step 4: Correr los tests y verificar que pasan todos**

Run: `deno test -A functions/_shared/auth_test.ts`
Expected: 5 passed.

- [ ] **Step 5: Replicar el endurecimiento en las 4 functions auto-contenidas**

En cada uno de `functions/kpis.ts`, `functions/pendientes.ts`, `functions/ventas.ts` y `functions/flujo.ts`: reemplazar **la función `requireUser` completa** (la copia inline, del `async function requireUser` a su llave de cierre) por el cuerpo de arriba **sin** la línea de import de `cors.ts` (cada archivo ya tiene `corsHeaders` y `sin401` inline — conservar su `sin401` local). Es decir, los dos cambios concretos por archivo:
  1. `await jwtVerify(token, publicKey)` → `await jwtVerify(token, publicKey, { algorithms: ["RS256"] })`
  2. la rama HS256 pasa de usar `secretStr` posiblemente vacío a:
```ts
    } else if (alg === "HS256") {
      const secretStr = Deno.env.get("INSFORGE_JWT_SECRET") ?? Deno.env.get("JWT_SECRET") ?? "";
      if (!secretStr) {
        console.error("requireUser: falta INSFORGE_JWT_SECRET en el servidor");
        return sin401("error de configuracion en servidor");
      }
      await jwtVerify(token, new TextEncoder().encode(secretStr), { algorithms: ["HS256"] });
    } else {
```

- [ ] **Step 6: Typecheck de las 4 functions**

Run: `deno check functions/kpis.ts functions/pendientes.ts functions/ventas.ts functions/flujo.ts functions/_shared/auth.ts`
Expected: sin errores.

- [ ] **Step 7: Commit**

```bash
git add functions/_shared/auth.ts functions/_shared/auth_test.ts functions/kpis.ts functions/pendientes.ts functions/ventas.ts functions/flujo.ts
git commit -m "Endurece requireUser: fija algoritmo por rama y rechaza secret HS256 ausente"
```

> El redeploy de estas 4 functions ocurre en Task 6, junto con el deploy del chat.

---

### Task 2: Migración `chat_sesiones` + `chat_uso` aplicada por `sync_nube.py`

**Files:**
- Create: `scripts/migrate_nube_chat.sql`
- Modify: `scripts/sync_nube.py` (constante `SQL_CHAT` junto a `SQL_VIEWS` en la línea 34; función nueva después de `aplicar_esquema`; llamada en `main` después del bloque `--init`)
- Modify: `tests/test_sync_nube.py` (agregar al final)

**Interfaces:**
- Consumes: `sync_nube.py` existente (`TABLAS_ORDEN`, `main(argv=None)`, patrón `with conn:`).
- Produces: tablas `chat_sesiones(id, mensajes jsonb, creado, actualizado)` y `chat_uso(id, sesion_id, modelo, input_tokens, output_tokens, n_llamadas_tools, costo_usd, creado)` existentes en la réplica tras cualquier corrida de `python scripts/sync_nube.py`. Task 5 les hace SELECT/INSERT/UPDATE.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `tests/test_sync_nube.py` (el archivo ya importa `sync_nube`; usar ese import existente):

```python
# --- Fase 4: migracion de las tablas del chat ---
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent


class _FakeCursorChat:
    def __init__(self, registro):
        self.registro = registro

    def execute(self, sql, params=None):
        self.registro.append(sql)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConnChat:
    def __init__(self):
        self.ejecutado = []

    def cursor(self):
        return _FakeCursorChat(self.ejecutado)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_migracion_chat_es_idempotente():
    sql = (_RAIZ / "scripts" / "migrate_nube_chat.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS chat_sesiones" in sql
    assert "CREATE TABLE IF NOT EXISTS chat_uso" in sql


def test_tablas_chat_fuera_del_sync():
    # Si entraran a TABLAS_ORDEN, el sync las truncaria y borraria el historial.
    for tabla in ("chat_sesiones", "chat_uso"):
        assert tabla not in sync_nube.TABLAS_ORDEN


def test_aplicar_migraciones_chat_ejecuta_el_sql():
    conn = _FakeConnChat()
    sync_nube.aplicar_migraciones_chat(conn)
    assert any("chat_sesiones" in s for s in conn.ejecutado)
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_sync_nube.py -q`
Expected: FAIL — `FileNotFoundError` (migrate_nube_chat.sql no existe) y `AttributeError` (aplicar_migraciones_chat no existe).

- [ ] **Step 3: Crear `scripts/migrate_nube_chat.sql`**

```sql
-- migrate_nube_chat.sql — Zigurat Movil, Fase 4 (chat de consultas)
-- Tablas PROPIAS de la nube: el unico lugar donde la nube escribe.
-- Idempotente: sync_nube.py la aplica en CADA corrida (autoreparacion si la
-- replica se recrea). NUNCA agregar estas tablas a TABLAS_ORDEN.

CREATE TABLE IF NOT EXISTS chat_sesiones (
    id          BIGSERIAL PRIMARY KEY,
    mensajes    JSONB NOT NULL DEFAULT '[]'::jsonb,
    creado      TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Log de uso/costo por consulta: base del tope de gasto diario y auditoria.
CREATE TABLE IF NOT EXISTS chat_uso (
    id               BIGSERIAL PRIMARY KEY,
    sesion_id        BIGINT REFERENCES chat_sesiones(id),
    modelo           TEXT NOT NULL,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    n_llamadas_tools INTEGER NOT NULL DEFAULT 0,
    costo_usd        NUMERIC(10,6) NOT NULL DEFAULT 0,
    creado           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_uso_creado ON chat_uso (creado);
```

- [ ] **Step 4: Modificar `scripts/sync_nube.py`**

Junto a `SQL_VIEWS` (línea 34) agregar:

```python
SQL_CHAT = PROJECT_ROOT / "scripts" / "migrate_nube_chat.sql"
```

Después de `aplicar_esquema` agregar:

```python
def aplicar_migraciones_chat(conn_nube):
    """Tablas propias de la nube (chat). Idempotente (IF NOT EXISTS): se aplica
    en CADA corrida para que la replica se autorepare si se recrea. Estas
    tablas nunca van en TABLAS_ORDEN (el sync las truncaria)."""
    with conn_nube:
        with conn_nube.cursor() as cur:
            cur.execute(SQL_CHAT.read_text(encoding="utf-8"))
```

En `main`, dentro del `try` interior, después del bloque `if args.init:` y antes de `total = sync(...)`:

```python
            if args.init:
                aplicar_esquema(conn_nube)
            aplicar_migraciones_chat(conn_nube)
            total = sync(conn_local, conn_nube)
```

- [ ] **Step 5: Correr los tests y verificar que pasan (suite completa)**

Run: `python -m pytest -q`
Expected: todos pasan (los 3 nuevos incluidos, ninguna regresión).

- [ ] **Step 6: Commit**

```bash
git add scripts/migrate_nube_chat.sql scripts/sync_nube.py tests/test_sync_nube.py
git commit -m "Agrega tablas chat_sesiones y chat_uso, aplicadas idempotentemente por sync_nube"
```

---

### Task 3: Tools del chat (`functions/_shared/chat_tools.ts`)

Las 10 herramientas de solo lectura que el modelo puede llamar. Cada una: query fija sobre views + formateador puro (testeable sin BD). El texto que devuelven es lo que el modelo cita — formato de pesos chilenos `$1.234.567`.

**Files:**
- Create: `functions/_shared/chat_tools.ts`
- Test: `functions/_shared/chat_tools_test.ts`

**Interfaces:**
- Consumes: `proyectarFlujo`, tipos `FacturaPendiente`, `Gasto` de `./flujo.ts` (Task existente, ya en el repo).
- Produces (Task 4 y 5 los usan con estos nombres exactos):
  - `type SqlCliente` — tagged template `(strings, ...vals) => Promise<Record<string, unknown>[]>` (compatible con el cliente `postgres`).
  - `const TOOLS: unknown[]` — schemas de tools formato Messages API (`{name, description, input_schema}`).
  - `ejecutarTool(sql: SqlCliente, nombre: string, input: Record<string, unknown>, hoy: Date): Promise<string>`.
  - `formatearPesos(n: number | string | null | undefined): string`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `functions/_shared/chat_tools_test.ts`:

```ts
// functions/_shared/chat_tools_test.ts
import { assertEquals, assertStringIncludes } from "jsr:@std/assert@1";
import { TOOLS, ejecutarTool, formatearPesos, type SqlCliente } from "./chat_tools.ts";

const HOY = new Date("2026-07-20");

/** Tagged template falso: devuelve resultados en orden, una query por llamada. */
function fakeSql(...resultados: unknown[][]): SqlCliente {
  const cola = [...resultados];
  return ((..._args: unknown[]) =>
    Promise.resolve(cola.shift() ?? [])) as unknown as SqlCliente;
}

Deno.test("formatearPesos usa puntos de miles chilenos", () => {
  assertEquals(formatearPesos(4267294), "$4.267.294");
  assertEquals(formatearPesos(0), "$0");
  assertEquals(formatearPesos(null), "$0");
  assertEquals(formatearPesos("55370.00"), "$55.370");
});

Deno.test("TOOLS: 10 tools con nombres unicos y schema de objeto", () => {
  assertEquals(TOOLS.length, 10);
  const nombres = TOOLS.map((t) => (t as { name: string }).name);
  assertEquals(new Set(nombres).size, 10);
  for (const t of TOOLS) {
    assertEquals((t as { input_schema: { type: string } }).input_schema.type, "object");
  }
});

Deno.test("deuda_total suma y separa por antiguedad", async () => {
  const sql = fakeSql([
    { total: 100000, dias_desde_emision: 10 },
    { total: 200000, dias_desde_emision: 45 },
    { total: 50000, dias_desde_emision: 120 },
  ]);
  const r = await ejecutarTool(sql, "deuda_total", {}, HOY);
  assertStringIncludes(r, "$350.000");
  assertStringIncludes(r, "3 facturas");
  assertStringIncludes(r, "$100.000");  // bucket 0-30
  assertStringIncludes(r, "$50.000");   // bucket +90
});

Deno.test("deuda_cliente sin filas responde sin deuda", async () => {
  const r = await ejecutarTool(fakeSql([]), "deuda_cliente", { nombre: "VDT" }, HOY);
  assertStringIncludes(r, "sin deuda pendiente");
});

Deno.test("ventas_total con rango", async () => {
  const sql = fakeSql([{ n: 6, total: 756409 }]);
  const r = await ejecutarTool(sql, "ventas_total",
    { desde: "2026-06-01", hasta: "2026-06-30" }, HOY);
  assertStringIncludes(r, "$756.409");
  assertStringIncludes(r, "6 facturas");
});

Deno.test("flujo_caja proyecta con las 4 queries", async () => {
  const sql = fakeSql(
    [{ folio: 1, fecha: "2026-07-10", rut_cliente: "1-9",
       razon_social_receptor: "Bar Uno", monto: 100000 }],  // v_flujo_pendientes
    [{ rut_cliente: "1-9", avg_dias: 15 }],                 // v_dias_pago_cliente
    [],                                                     // cuentas_por_pagar
    [{ valor: { saldo: 500000, fecha: "2026-07-18" } }],    // sync_meta saldo_banco
  );
  const r = await ejecutarTool(sql, "flujo_caja", {}, HOY);
  assertStringIncludes(r, "Semana 1");
  assertStringIncludes(r, "$100.000");
});

Deno.test("tool desconocida devuelve error legible", async () => {
  const r = await ejecutarTool(fakeSql([]), "borrar_todo", {}, HOY);
  assertStringIncludes(r, "Herramienta desconocida");
});
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `deno test -A functions/_shared/chat_tools_test.ts`
Expected: FAIL — módulo `./chat_tools.ts` no existe.

- [ ] **Step 3: Implementar `functions/_shared/chat_tools.ts`**

```ts
// functions/_shared/chat_tools.ts
// Las 10 herramientas de SOLO LECTURA del chat. Queries fijas sobre las views
// canonicas (las reglas de negocio viven en el SQL de las views, no aqui).
// El texto devuelto es lo que el modelo cita: pesos chilenos con puntos.
import {
  proyectarFlujo,
  type FacturaPendiente,
  type Gasto,
} from "./flujo.ts";

export type SqlCliente = (
  strings: TemplateStringsArray,
  ...vals: unknown[]
) => Promise<Record<string, unknown>[]>;

export function formatearPesos(n: number | string | null | undefined): string {
  const v = Math.round(Number(n ?? 0));
  const signo = v < 0 ? "-" : "";
  const digitos = String(Math.abs(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${signo}$${digitos}`;
}

const num = (x: unknown) => Number(x ?? 0);

export const TOOLS = [
  { name: "deuda_total",
    description: "Deuda total pendiente de cobro, con desglose por antiguedad (dias desde la emision de la factura).",
    input_schema: { type: "object", properties: {} } },
  { name: "deuda_cliente",
    description: "Deuda pendiente de un cliente especifico, por nombre (parcial) o RUT.",
    input_schema: { type: "object", properties: {
      nombre: { type: "string", description: "Nombre parcial o RUT del cliente" } },
      required: ["nombre"] } },
  { name: "ranking_deudores",
    description: "Top N clientes ordenados por deuda pendiente.",
    input_schema: { type: "object", properties: {
      limite: { type: "integer", description: "Cuantos clientes mostrar (default 5)" } } } },
  { name: "facturas_vencidas",
    description: "Facturas pendientes de pago con mas de N dias desde su emision (default 30).",
    input_schema: { type: "object", properties: {
      dias: { type: "integer", description: "Umbral de dias (default 30)" } } } },
  { name: "ventas_total",
    description: "Total vendido (neto de notas de credito). Opcional: rango desde/hasta en formato YYYY-MM-DD (ambos o ninguno).",
    input_schema: { type: "object", properties: {
      desde: { type: "string" }, hasta: { type: "string" } } } },
  { name: "ranking_clientes",
    description: "Top N clientes ordenados por ventas historicas totales.",
    input_schema: { type: "object", properties: {
      limite: { type: "integer", description: "Cuantos clientes mostrar (default 10)" } } } },
  { name: "ventas_cliente",
    description: "Ventas historicas de un cliente por nombre, con sus ultimas facturas.",
    input_schema: { type: "object", properties: {
      nombre: { type: "string" } }, required: ["nombre"] } },
  { name: "ventas_producto",
    description: "Lineas de venta que coinciden con un nombre de producto (ya excluye Logistica y envases PET).",
    input_schema: { type: "object", properties: {
      nombre: { type: "string" } }, required: ["nombre"] } },
  { name: "flujo_caja",
    description: "Proyeccion de caja a 4 semanas: cobros esperados por cliente menos gastos programados, partiendo del saldo bancario del ultimo sync.",
    input_schema: { type: "object", properties: {} } },
  { name: "listar_gastos",
    description: "Gastos pendientes de pago (cuentas por pagar) con monto y vencimiento. Opcional: filtro de texto sobre la descripcion.",
    input_schema: { type: "object", properties: {
      filtro: { type: "string" } } } },
];

interface FilaPendiente { total: unknown; dias_desde_emision: unknown }

export function resumenDeuda(filas: FilaPendiente[]): string {
  if (!filas.length) return "No hay deuda pendiente de cobro.";
  const buckets = { d0_30: 0, d31_60: 0, d61_90: 0, d90_mas: 0 };
  let total = 0;
  for (const f of filas) {
    const t = num(f.total);
    const d = num(f.dias_desde_emision);
    total += t;
    if (d <= 30) buckets.d0_30 += t;
    else if (d <= 60) buckets.d31_60 += t;
    else if (d <= 90) buckets.d61_90 += t;
    else buckets.d90_mas += t;
  }
  return `Deuda total pendiente: ${formatearPesos(total)} en ${filas.length} facturas. ` +
    `Por antiguedad: 0-30d ${formatearPesos(buckets.d0_30)}, ` +
    `31-60d ${formatearPesos(buckets.d31_60)}, ` +
    `61-90d ${formatearPesos(buckets.d61_90)}, ` +
    `+90d ${formatearPesos(buckets.d90_mas)}.`;
}

export async function ejecutarTool(
  sql: SqlCliente,
  nombre: string,
  input: Record<string, unknown>,
  hoy: Date,
): Promise<string> {
  switch (nombre) {
    case "deuda_total": {
      const filas = await sql`SELECT total, dias_desde_emision FROM v_pendientes`;
      return resumenDeuda(filas as unknown as FilaPendiente[]);
    }
    case "deuda_cliente": {
      const nombreCliente = String(input.nombre ?? "");
      const q = `%${nombreCliente}%`;
      const filas = await sql`
        SELECT folio, fecha, razon_social, total, dias_desde_emision
        FROM v_pendientes
        WHERE razon_social ILIKE ${q} OR rut_cliente = ${nombreCliente}
        ORDER BY fecha`;
      if (!filas.length) return `${nombreCliente}: sin deuda pendiente.`;
      const total = filas.reduce((s, f) => s + num(f.total), 0);
      const lineas = filas.map((f) =>
        `- Folio ${f.folio} (${String(f.fecha).slice(0, 10)}): ${formatearPesos(f.total)}, ${num(f.dias_desde_emision)}d`);
      return `${filas[0].razon_social}: ${formatearPesos(total)} en ${filas.length} facturas.\n${lineas.join("\n")}`;
    }
    case "ranking_deudores": {
      const limite = num(input.limite) || 5;
      const filas = await sql`
        SELECT razon_social, SUM(total) AS deuda, COUNT(*) AS n
        FROM v_pendientes GROUP BY razon_social
        ORDER BY deuda DESC LIMIT ${limite}`;
      if (!filas.length) return "No hay deuda pendiente.";
      return filas.map((f, i) =>
        `${i + 1}. ${f.razon_social}: ${formatearPesos(f.deuda)} (${f.n} facturas)`).join("\n");
    }
    case "facturas_vencidas": {
      const dias = num(input.dias) || 30;
      const filas = await sql`
        SELECT folio, razon_social, total, dias_desde_emision
        FROM v_pendientes WHERE dias_desde_emision > ${dias}
        ORDER BY dias_desde_emision DESC`;
      if (!filas.length) return `Ninguna factura pendiente con mas de ${dias} dias.`;
      return `${filas.length} facturas con mas de ${dias} dias:\n` + filas.map((f) =>
        `- Folio ${f.folio} ${f.razon_social}: ${formatearPesos(f.total)}, ${num(f.dias_desde_emision)}d`).join("\n");
    }
    case "ventas_total": {
      const desde = input.desde ? String(input.desde) : null;
      const hasta = input.hasta ? String(input.hasta) : null;
      const filas = (desde && hasta)
        ? await sql`SELECT COUNT(*) AS n, COALESCE(SUM(total_real), 0) AS total
                    FROM v_ventas_reales WHERE fecha BETWEEN ${desde} AND ${hasta}`
        : await sql`SELECT COUNT(*) AS n, COALESCE(SUM(total_real), 0) AS total
                    FROM v_ventas_reales`;
      const f = filas[0];
      const periodo = (desde && hasta) ? ` entre ${desde} y ${hasta}` : " historicas";
      return `Ventas${periodo}: ${formatearPesos(f.total)} en ${num(f.n)} facturas.`;
    }
    case "ranking_clientes": {
      const limite = num(input.limite) || 10;
      const filas = await sql`
        SELECT rut_cliente, MAX(razon_social_receptor) AS cliente,
               SUM(total_real) AS total
        FROM v_ventas_reales GROUP BY rut_cliente
        ORDER BY total DESC LIMIT ${limite}`;
      if (!filas.length) return "Sin ventas registradas.";
      return filas.map((f, i) =>
        `${i + 1}. ${f.cliente}: ${formatearPesos(f.total)}`).join("\n");
    }
    case "ventas_cliente": {
      const q = `%${String(input.nombre ?? "")}%`;
      const filas = await sql`
        SELECT folio, fecha, razon_social_receptor, total_real
        FROM v_ventas_reales WHERE razon_social_receptor ILIKE ${q}
        ORDER BY fecha DESC`;
      if (!filas.length) return `Sin ventas que coincidan con '${input.nombre}'.`;
      const total = filas.reduce((s, f) => s + num(f.total_real), 0);
      const ultimas = filas.slice(0, 5).map((f) =>
        `- Folio ${f.folio} (${String(f.fecha).slice(0, 10)}): ${formatearPesos(f.total_real)}`);
      return `${filas[0].razon_social_receptor}: ${formatearPesos(total)} en ${filas.length} facturas.\n` +
        `Ultimas:\n${ultimas.join("\n")}`;
    }
    case "ventas_producto": {
      const q = `%${String(input.nombre ?? "")}%`;
      const filas = await sql`
        SELECT nombre_producto, cantidad, fecha
        FROM v_ventas_producto WHERE nombre_producto ILIKE ${q}
        ORDER BY fecha DESC`;
      if (!filas.length) return `Sin ventas que coincidan con '${input.nombre}'.`;
      const unidades = filas.reduce((s, f) => s + num(f.cantidad), 0);
      return `'${input.nombre}': ${filas.length} lineas de venta, ${unidades} unidades ` +
        `(ultima el ${String(filas[0].fecha).slice(0, 10)}).`;
    }
    case "flujo_caja": {
      // Mismas queries que functions/flujo.ts (paridad con el endpoint /flujo).
      const facturas = await sql`
        SELECT folio, fecha, rut_cliente, razon_social_receptor, monto
        FROM v_flujo_pendientes ORDER BY fecha`;
      const avgs = await sql`SELECT rut_cliente, avg_dias FROM v_dias_pago_cliente`;
      const gastos = await sql`
        SELECT descripcion, proveedor, monto, fecha_vencimiento, categoria,
               recurrente, periodicidad
        FROM cuentas_por_pagar WHERE pagado = FALSE`;
      const meta = await sql`SELECT valor FROM sync_meta WHERE clave = 'saldo_banco'`;
      const avgDias = Object.fromEntries(
        avgs.map((a) => [String(a.rut_cliente), Number(a.avg_dias)]));
      const saldoInicial = num((meta[0]?.valor as { saldo?: unknown })?.saldo);
      const r = proyectarFlujo(
        facturas.map((f): FacturaPendiente => ({
          folio: num(f.folio), fecha: new Date(String(f.fecha)),
          rut_cliente: String(f.rut_cliente),
          razon_social_receptor: String(f.razon_social_receptor),
          monto: num(f.monto),
        })),
        avgDias,
        gastos.map((g): Gasto => ({
          descripcion: String(g.descripcion),
          proveedor: g.proveedor === null ? null : String(g.proveedor),
          monto: num(g.monto),
          fecha_vencimiento: new Date(String(g.fecha_vencimiento)),
          categoria: g.categoria === null ? null : String(g.categoria),
          recurrente: Boolean(g.recurrente),
          periodicidad: g.periodicidad === null ? null : String(g.periodicidad),
        })),
        saldoInicial, hoy,
      );
      const lineas = r.semanas.map((s) =>
        `- Semana ${s.semana} (${s.label}): ingresos ${formatearPesos(s.ingresos)}, ` +
        `egresos ${formatearPesos(s.egresos)}, saldo ${formatearPesos(s.saldo_acumulado)}` +
        (s.riesgo ? " [RIESGO]" : ""));
      return `Flujo de caja 4 semanas (saldo inicial ${formatearPesos(r.saldo_inicial)}):\n` +
        lineas.join("\n") +
        `\nTotales: ingresos ${formatearPesos(r.total_ingresos)}, egresos ${formatearPesos(r.total_egresos)}. ` +
        `Fuera del horizonte: ${formatearPesos(r.fuera_horizonte)}.`;
    }
    case "listar_gastos": {
      const filtro = input.filtro ? String(input.filtro) : null;
      const filas = filtro
        ? await sql`SELECT id, descripcion, proveedor, monto, fecha_vencimiento
                    FROM cuentas_por_pagar
                    WHERE pagado = FALSE AND descripcion ILIKE ${"%" + filtro + "%"}
                    ORDER BY fecha_vencimiento`
        : await sql`SELECT id, descripcion, proveedor, monto, fecha_vencimiento
                    FROM cuentas_por_pagar WHERE pagado = FALSE
                    ORDER BY fecha_vencimiento`;
      if (!filas.length) {
        return filtro
          ? `No hay gastos pendientes que coincidan con '${filtro}'.`
          : "No hay gastos pendientes.";
      }
      return filas.map((g) =>
        `- ${g.descripcion}: ${formatearPesos(g.monto)}, vence ${String(g.fecha_vencimiento).slice(0, 10)}` +
        (g.proveedor ? ` (${g.proveedor})` : "")).join("\n");
    }
    default:
      return `Herramienta desconocida: ${nombre}.`;
  }
}
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `deno test -A functions/_shared/chat_tools_test.ts`
Expected: 8 passed. Luego `deno check functions/_shared/chat_tools.ts` sin errores.

- [ ] **Step 5: Commit**

```bash
git add functions/_shared/chat_tools.ts functions/_shared/chat_tools_test.ts
git commit -m "Agrega las 10 tools de solo lectura del chat sobre las views canonicas"
```

---

### Task 4: Tool-use loop (`functions/_shared/chat_loop.ts`)

El corazón de la Fase 4: el loop que alterna modelo ↔ herramientas hasta obtener la respuesta final, con tope de iteraciones y acumulación de uso. `llamarModelo` es inyectable para testear el loop sin gastar API.

**Files:**
- Create: `functions/_shared/chat_loop.ts`
- Test: `functions/_shared/chat_loop_test.ts`

**Interfaces:**
- Consumes: nada de tasks anteriores (el loop no conoce las tools concretas — recibe `ejecutarTool` como callback).
- Produces (Task 5 los usa con estos nombres exactos):
  - `interface UsoChat { input_tokens: number; output_tokens: number; n_llamadas_tools: number }`
  - `interface MensajeAPI { role: "user" | "assistant"; content: string | BloqueContenido[] }`
  - `correrChat(opts: { system: string; mensajes: MensajeAPI[]; tools: unknown[]; llamarModelo: (body: Record<string, unknown>) => Promise<RespuestaModelo>; ejecutarTool: (nombre: string, input: Record<string, unknown>) => Promise<string>; maxIteraciones?: number; maxTokens?: number }): Promise<{ texto: string; uso: UsoChat }>`
  - `llamarModeloReal(apiKey: string, modelo: string): (body: Record<string, unknown>) => Promise<RespuestaModelo>`
  - `const MAX_ITERACIONES = 8`

- [ ] **Step 1: Escribir los tests que fallan**

Crear `functions/_shared/chat_loop_test.ts`:

```ts
// functions/_shared/chat_loop_test.ts
// El loop se testea con un modelo falso: respuestas en secuencia.
import { assertEquals, assertStringIncludes } from "jsr:@std/assert@1";
import { correrChat, MAX_ITERACIONES, type RespuestaModelo } from "./chat_loop.ts";

function modeloFalso(respuestas: RespuestaModelo[]) {
  const cola = [...respuestas];
  const llamadas: Record<string, unknown>[] = [];
  const fn = (body: Record<string, unknown>) => {
    llamadas.push(body);
    const r = cola.shift();
    if (!r) throw new Error("modelo falso sin respuestas");
    return Promise.resolve(r);
  };
  return { fn, llamadas };
}

const fin = (texto: string): RespuestaModelo => ({
  content: [{ type: "text", text: texto }],
  stop_reason: "end_turn",
  usage: { input_tokens: 100, output_tokens: 50 },
});

const pideTool = (nombre: string, input: Record<string, unknown>): RespuestaModelo => ({
  content: [{ type: "tool_use", id: "tu_1", name: nombre, input }],
  stop_reason: "tool_use",
  usage: { input_tokens: 200, output_tokens: 30 },
});

Deno.test("respuesta directa sin tools", async () => {
  const { fn } = modeloFalso([fin("Hola, soy el analista.")]);
  const r = await correrChat({
    system: "s", mensajes: [{ role: "user", content: "hola" }], tools: [],
    llamarModelo: fn, ejecutarTool: () => Promise.resolve(""),
  });
  assertEquals(r.texto, "Hola, soy el analista.");
  assertEquals(r.uso.input_tokens, 100);
  assertEquals(r.uso.n_llamadas_tools, 0);
});

Deno.test("una tool: ejecuta, enhebra tool_result y suma uso", async () => {
  const { fn, llamadas } = modeloFalso([
    pideTool("deuda_total", {}),
    fin("Te deben $350.000."),
  ]);
  const ejecutadas: string[] = [];
  const r = await correrChat({
    system: "s", mensajes: [{ role: "user", content: "cuanto me deben" }], tools: [],
    llamarModelo: fn,
    ejecutarTool: (nombre) => { ejecutadas.push(nombre); return Promise.resolve("Deuda: $350.000"); },
  });
  assertEquals(ejecutadas, ["deuda_total"]);
  assertEquals(r.texto, "Te deben $350.000.");
  assertEquals(r.uso.input_tokens, 300);
  assertEquals(r.uso.n_llamadas_tools, 1);
  // La 2a llamada al modelo lleva el tool_result enhebrado.
  const mensajes2 = llamadas[1].messages as { role: string; content: unknown }[];
  assertEquals(mensajes2.length, 3);  // user + assistant(tool_use) + user(tool_result)
  const ultimo = mensajes2[2].content as { type: string; tool_use_id: string }[];
  assertEquals(ultimo[0].type, "tool_result");
  assertEquals(ultimo[0].tool_use_id, "tu_1");
});

Deno.test("error de una tool va como tool_result is_error y el loop sigue", async () => {
  const { fn } = modeloFalso([
    pideTool("deuda_total", {}),
    fin("No pude consultar la deuda."),
  ]);
  const r = await correrChat({
    system: "s", mensajes: [{ role: "user", content: "x" }], tools: [],
    llamarModelo: fn,
    ejecutarTool: () => Promise.reject(new Error("BD caida")),
  });
  assertEquals(r.texto, "No pude consultar la deuda.");
});

Deno.test("tope de iteraciones corta el loop", async () => {
  const respuestas = Array.from({ length: MAX_ITERACIONES }, () => pideTool("deuda_total", {}));
  const { fn } = modeloFalso(respuestas);
  const r = await correrChat({
    system: "s", mensajes: [{ role: "user", content: "x" }], tools: [],
    llamarModelo: fn,
    ejecutarTool: () => Promise.resolve("dato"),
  });
  assertStringIncludes(r.texto, "tope de pasos");
  assertEquals(r.uso.n_llamadas_tools, MAX_ITERACIONES);
});
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `deno test -A functions/_shared/chat_loop_test.ts`
Expected: FAIL — módulo `./chat_loop.ts` no existe.

- [ ] **Step 3: Implementar `functions/_shared/chat_loop.ts`**

```ts
// functions/_shared/chat_loop.ts
// Tool-use loop generico sobre la Messages API de Anthropic. `llamarModelo` es
// inyectable: los tests usan un modelo falso; produccion usa llamarModeloReal
// (fetch directo, sin SDK: menos dependencias en el runtime edge).

export interface BloqueContenido {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  tool_use_id?: string;
  content?: unknown;
  is_error?: boolean;
}

export interface RespuestaModelo {
  content: BloqueContenido[];
  stop_reason: string;
  usage: { input_tokens: number; output_tokens: number };
}

export interface MensajeAPI {
  role: "user" | "assistant";
  content: string | BloqueContenido[];
}

export interface UsoChat {
  input_tokens: number;
  output_tokens: number;
  n_llamadas_tools: number;
}

export const MAX_ITERACIONES = 8;
const MAX_TOKENS = 1024;

export async function correrChat(opts: {
  system: string;
  mensajes: MensajeAPI[];
  tools: unknown[];
  llamarModelo: (body: Record<string, unknown>) => Promise<RespuestaModelo>;
  ejecutarTool: (nombre: string, input: Record<string, unknown>) => Promise<string>;
  maxIteraciones?: number;
  maxTokens?: number;
}): Promise<{ texto: string; uso: UsoChat }> {
  const mensajes = [...opts.mensajes];
  const uso: UsoChat = { input_tokens: 0, output_tokens: 0, n_llamadas_tools: 0 };
  const tope = opts.maxIteraciones ?? MAX_ITERACIONES;

  for (let i = 0; i < tope; i++) {
    const resp = await opts.llamarModelo({
      system: opts.system,
      tools: opts.tools,
      messages: mensajes,
      max_tokens: opts.maxTokens ?? MAX_TOKENS,
    });
    uso.input_tokens += resp.usage.input_tokens;
    uso.output_tokens += resp.usage.output_tokens;

    if (resp.stop_reason !== "tool_use") {
      const texto = resp.content
        .filter((b) => b.type === "text")
        .map((b) => b.text ?? "")
        .join("\n").trim();
      return { texto, uso };
    }

    mensajes.push({ role: "assistant", content: resp.content });
    const resultados: BloqueContenido[] = [];
    for (const b of resp.content) {
      if (b.type !== "tool_use") continue;
      uso.n_llamadas_tools++;
      let contenido: string;
      let esError = false;
      try {
        contenido = await opts.ejecutarTool(b.name ?? "", b.input ?? {});
      } catch (e) {
        contenido = `Error ejecutando ${b.name}: ${(e as Error).message}`;
        esError = true;
      }
      resultados.push({
        type: "tool_result",
        tool_use_id: b.id ?? "",
        content: contenido,
        ...(esError ? { is_error: true } : {}),
      });
    }
    mensajes.push({ role: "user", content: resultados });
  }

  return {
    texto: "No alcance a terminar la consulta (tope de pasos). Intenta una pregunta mas acotada.",
    uso,
  };
}

/** Cliente real de la Messages API. Reintenta 429/5xx/529 (esperas 1s y 3s). */
export function llamarModeloReal(apiKey: string, modelo: string) {
  return async (body: Record<string, unknown>): Promise<RespuestaModelo> => {
    const esperas = [0, 1000, 3000];
    let ultimo = "";
    for (const espera of esperas) {
      if (espera) await new Promise((r) => setTimeout(r, espera));
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "x-api-key": apiKey,
          "anthropic-version": "2023-06-01",
          "content-type": "application/json",
        },
        body: JSON.stringify({ model: modelo, ...body }),
      });
      if (res.ok) return await res.json() as RespuestaModelo;
      ultimo = `${res.status}: ${(await res.text()).slice(0, 300)}`;
      // 4xx distinto de 429 no se reintenta (key mala, request invalido...).
      if (res.status !== 429 && res.status < 500) break;
    }
    throw new Error(`La API de Anthropic fallo (${ultimo})`);
  };
}
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `deno test -A functions/_shared/` — pasan los 4 nuevos + los de auth, tools y flujo (suite Deno completa).
Expected: todos passed.

- [ ] **Step 5: Commit**

```bash
git add functions/_shared/chat_loop.ts functions/_shared/chat_loop_test.ts
git commit -m "Agrega el tool-use loop del chat con tope de iteraciones y cliente Messages API"
```

---

### Task 5: System prompt + endpoint `functions/chat.ts`

**Files:**
- Create: `functions/_shared/chat_prompt.ts`
- Create: `functions/chat.ts`

**Interfaces:**
- Consumes: `requireUser` (Task 1), `db` y `corsHeaders` (`functions/_shared/{db,cors}.ts` existentes), `TOOLS`/`ejecutarTool` (Task 3), `correrChat`/`llamarModeloReal` (Task 4), tablas `chat_sesiones`/`chat_uso` (Task 2).
- Produces: `POST /chat` con request `{mensaje: string, sesion_id?: number}` y response `200 {respuesta: string, sesion_id: number, uso: {input_tokens, output_tokens, n_llamadas_tools, costo_usd}}`; errores `400` (body inválido), `401` (sin token), `405` (método), `429 {error: "limite_diario"}`, `500/502 {error}`. Task 7 (PWA) y Task 8 (aceptación) consumen exactamente estas formas.

- [ ] **Step 1: Crear `functions/_shared/chat_prompt.ts`**

```ts
// functions/_shared/chat_prompt.ts
// System prompt del chat movil. Adaptacion de solo-lectura del prompt del
// Centro de Comando local (app/agent/system_prompt.py). Si alla cambia una
// regla de negocio, evaluar si aplica replicarla aqui.

export function promptChat(hoy: string, ultimoSync: string | null): string {
  const sync = ultimoSync
    ? `La replica se sincronizo por ultima vez el ${ultimoSync}.`
    : "No hay registro del ultimo sync (advierte que los datos pueden estar desactualizados).";
  return `Eres el analista de negocio de Zigurat Brewery (Elaboradora y \
Comercializadora Vintage SPA), respondiendo en el CHAT MOVIL del dueno. \
Respondes SIEMPRE en espanol, directo y conciso.

HOY es ${hoy}. Consultas una REPLICA de solo lectura de la base del negocio. \
${sync} Si te preguntan por algo posterior al ultimo sync, advierte que puede \
no estar reflejado.

FORMATO (pantalla de celular, obligatorio):
- Respuestas BREVES: la cifra primero, el detalle solo si lo piden.
- NUNCA uses headings markdown (# ## ###). Negrita (**texto**) para cifras clave.
- Guiones para listas cortas; parrafos de 1-3 lineas.

HERRAMIENTAS (obligatorio):
- TODA cifra que entregues debe salir de una herramienta ejecutada en esta \
conversacion. NUNCA inventes ni estimes numeros de memoria.
- No tienes acceso a SQL ni a otras fuentes: si ninguna herramienta cubre la \
pregunta, dilo honestamente y sugiere consultarlo en el Centro de Comando del PC.
- Las herramientas ya aplican las reglas del negocio (montos ajustados por \
notas de credito, exclusion de NC, estado de pago por fecha_pago, filtro de \
lineas Logistica/PET). No re-expliques esas reglas salvo que te pregunten.

ESTRUCTURA DE FACTURACION (contexto): cada barril se factura en dos lineas \
(producto + "Logistica"); el precio real es la SUMA de ambas. Las lineas de \
envase PET son costo traspasado, no venta de cerveza. Por eso los rankings de \
producto excluyen Logistica y PET.

SOLO LECTURA: no puedes registrar pagos, gastos ni modificar nada. Si te lo \
piden, explica amablemente que las acciones se hacen desde el Centro de \
Comando en el PC, y ofrece en cambio el dato de lectura que ayude.`;
}
```

- [ ] **Step 2: Crear `functions/chat.ts`**

```ts
// functions/chat.ts
// POST {mensaje, sesion_id?} -> {respuesta, sesion_id, uso}
// Unica function con escritura en la nube: chat_sesiones (historial) y
// chat_uso (log de costo, base del tope diario). Fuente modular: se despliega
// empaquetada con `deno bundle` (ver scripts de deploy en el plan Fase 4).
import { corsHeaders } from "./_shared/cors.ts";
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";
import { TOOLS, ejecutarTool } from "./_shared/chat_tools.ts";
import { correrChat, llamarModeloReal, type MensajeAPI } from "./_shared/chat_loop.ts";
import { promptChat } from "./_shared/chat_prompt.ts";

const MAX_LARGO_MENSAJE = 2000;
const MAX_HISTORIAL_API = 20;   // mensajes enviados a la API (el historial completo queda en BD)
const MODELO_DEFAULT = "claude-sonnet-5";

interface MensajeGuardado { role: "user" | "assistant"; content: string }

function json(cuerpo: unknown, status: number): Response {
  return new Response(JSON.stringify(cuerpo), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

export default async function handler(req: Request): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "solo POST" }, 405);

  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;

  const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
  if (!apiKey) return json({ error: "falta ANTHROPIC_API_KEY en el servidor" }, 500);

  let cuerpo: { mensaje?: string; sesion_id?: number };
  try {
    cuerpo = await req.json();
  } catch {
    return json({ error: "body invalido: se espera JSON {mensaje, sesion_id?}" }, 400);
  }
  const mensaje = (cuerpo.mensaje ?? "").trim();
  if (!mensaje) return json({ error: "mensaje vacio" }, 400);
  if (mensaje.length > MAX_LARGO_MENSAJE) return json({ error: "mensaje demasiado largo" }, 400);

  const sql = db();

  // Tope de gasto diario: red de seguridad ante loops o uso descontrolado.
  const limiteDiario = Number(Deno.env.get("CHAT_LIMITE_DIARIO_USD") ?? "1.0");
  const [gasto] = await sql`
    SELECT COALESCE(SUM(costo_usd), 0) AS hoy
    FROM chat_uso WHERE creado >= date_trunc('day', now())`;
  if (Number(gasto.hoy) >= limiteDiario) {
    return json({
      error: "limite_diario",
      detalle: `Tope diario de US$${limiteDiario} alcanzado. Vuelve manana ` +
        `o sube CHAT_LIMITE_DIARIO_USD en la configuracion de la function.`,
    }, 429);
  }

  // Sesion: cargar la existente o crear una nueva.
  let sesionId = cuerpo.sesion_id ?? null;
  let historial: MensajeGuardado[] = [];
  if (sesionId) {
    const [s] = await sql`SELECT mensajes FROM chat_sesiones WHERE id = ${sesionId}`;
    if (s) historial = s.mensajes as MensajeGuardado[];
    else sesionId = null;   // id desconocido (ej: replica recreada): sesion nueva
  }
  if (!sesionId) {
    const [s] = await sql`INSERT INTO chat_sesiones (mensajes) VALUES ('[]'::jsonb) RETURNING id`;
    sesionId = Number(s.id);
  }

  const meta = await sql`SELECT valor FROM sync_meta WHERE clave = 'ultimo_sync'`;
  const ultimoSync = (meta[0]?.valor as { momento?: string } | undefined)?.momento ?? null;
  const hoy = new Date().toISOString().slice(0, 10);
  const modelo = Deno.env.get("CHAT_MODELO") ?? MODELO_DEFAULT;

  const mensajesAPI: MensajeAPI[] = [
    ...historial.slice(-MAX_HISTORIAL_API),
    { role: "user", content: mensaje },
  ];

  let texto: string;
  let uso: { input_tokens: number; output_tokens: number; n_llamadas_tools: number };
  try {
    ({ texto, uso } = await correrChat({
      system: promptChat(hoy, ultimoSync),
      mensajes: mensajesAPI,
      tools: TOOLS,
      llamarModelo: llamarModeloReal(apiKey, modelo),
      ejecutarTool: (nombre, input) => ejecutarTool(sql, nombre, input, new Date()),
    }));
  } catch (e) {
    console.error("chat: fallo el loop:", (e as Error).message);
    return json({ error: `El chat fallo: ${(e as Error).message}` }, 502);
  }

  // Costo estimado (para el tope diario; precios por MTok configurables).
  const precioIn = Number(Deno.env.get("CHAT_PRECIO_IN_USD_MTOK") ?? "3");
  const precioOut = Number(Deno.env.get("CHAT_PRECIO_OUT_USD_MTOK") ?? "15");
  const costo = (uso.input_tokens * precioIn + uso.output_tokens * precioOut) / 1_000_000;

  // Persistir: historial completo en la sesion + fila de uso.
  const nuevoHistorial: MensajeGuardado[] = [
    ...historial,
    { role: "user", content: mensaje },
    { role: "assistant", content: texto },
  ];
  await sql`
    UPDATE chat_sesiones
    SET mensajes = ${JSON.stringify(nuevoHistorial)}::jsonb, actualizado = now()
    WHERE id = ${sesionId}`;
  await sql`
    INSERT INTO chat_uso (sesion_id, modelo, input_tokens, output_tokens,
                          n_llamadas_tools, costo_usd)
    VALUES (${sesionId}, ${modelo}, ${uso.input_tokens}, ${uso.output_tokens},
            ${uso.n_llamadas_tools}, ${costo})`;

  return json({ respuesta: texto, sesion_id: sesionId, uso: { ...uso, costo_usd: costo } }, 200);
}
```

- [ ] **Step 3: Typecheck + suite Deno + bundle de prueba**

Run:
```bash
deno check functions/chat.ts
deno test -A functions/_shared/
mkdir -p nube/dist && deno bundle -o nube/dist/chat.bundle.js functions/chat.ts
```
Expected: check sin errores; tests passed; bundle genera `nube/dist/chat.bundle.js` (~100-150 KB, "Bundled N modules").

- [ ] **Step 4: Ignorar el artefacto de bundle en git**

Agregar a `.gitignore` (sección de artefactos de build, junto a lo existente):

```
nube/dist/
```

- [ ] **Step 5: Commit**

```bash
git add functions/chat.ts functions/_shared/chat_prompt.ts .gitignore
git commit -m "Agrega el endpoint POST /chat: loop de tools, sesiones, log de uso y tope diario"
```

---

### Task 6: Deploy del chat + redeploy de las functions endurecidas + secrets

Esta task es de operación (no de código). Requiere el MCP de InsForge autorizado en la sesión (`/mcp`) **o** el dashboard de InsForge a mano. Los pasos con 🔑 los hace Christian.

**Files:**
- Ninguno nuevo (usa `nube/dist/chat.bundle.js` generado en Task 5).

**Interfaces:**
- Consumes: bundle de Task 5; tablas de Task 2; functions endurecidas de Task 1.
- Produces: `POST https://<functions-url>/chat` operativo en producción; secrets configurados. Task 7 y 8 lo llaman.

- [ ] **Step 1: Crear las tablas del chat en la réplica**

Run: `python scripts/sync_nube.py`
Expected: `Sync OK ...` (la corrida aplica `migrate_nube_chat.sql`; verificar sin errores).

- [ ] **Step 2: 🔑 Configurar secrets de la function en InsForge**

> **Ajuste 2026-07-19 — proveedor cambiado al AI Gateway de InsForge.** El
> chat ya NO usa `ANTHROPIC_API_KEY` ni `OPENROUTER_API_KEY`. El deploy que
> corre hoy en producción es el build viejo (OpenRouter): al redeployar hay
> que (1) regenerar el bundle (`deno bundle -o nube/dist/chat.bundle.js
> functions/chat.ts` — verificar que el bundle nuevo contenga
> `api/ai/chat/completion` y NO `openrouter.ai`), y (2) tener configurado el
> secret nuevo ANTES del cutover, o toda consulta responderá
> `500 "falta INSFORGE_AI_KEY"`. Después del cutover, borrar el secret
> `OPENROUTER_API_KEY` de la function, quitar la línea de `.env` local y
> cerrar/vaciar la cuenta de openrouter.ai.

En el dashboard de InsForge (proyecto `zigurat-movil`) → Functions → variables/secrets, agregar:
- `INSFORGE_AI_KEY` = la API key del proyecto InsForge (la misma `ik_...` de `.insforge/project.json`).
- (opcional) `INSFORGE_AI_URL` = host del proyecto; default `https://z86cmn8g.us-west.insforge.app`.
- (opcionales, solo si se quiere cambiar el default) `CHAT_MODELO` (default `google/gemini-2.5-flash`), `CHAT_LIMITE_DIARIO_USD`, `CHAT_PRECIO_IN_USD_MTOK` (default 0.30), `CHAT_PRECIO_OUT_USD_MTOK` (default 2.50).

Ya deben existir de fases anteriores: `INSFORGE_DB_URL`, `INSFORGE_JWT_SECRET`, `JWT_PUBLIC_KEY`.

- [ ] **Step 3: Regenerar el bundle y desplegar las 5 functions**

```bash
deno bundle -o nube/dist/chat.bundle.js functions/chat.ts
```

Luego, por cada function, subir el código vía MCP de InsForge (o dashboard → Functions → editar → pegar → deploy):
- slug `chat` ← contenido de `nube/dist/chat.bundle.js` (nueva)
- slug `kpis` ← `functions/kpis.ts` (redeploy con auth endurecida)
- slug `pendientes` ← `functions/pendientes.ts`
- slug `ventas` ← `functions/ventas.ts`
- slug `flujo` ← `functions/flujo.ts`

- [ ] **Step 4: Verificar en producción**

```bash
# 1) Sin token -> 401
curl -s -o /dev/null -w "%{http_code}" -X POST "$INSFORGE_FUNCTIONS_URL/chat" -d '{"mensaje":"hola"}'
# Expected: 401

# 2) Los endpoints de lectura siguen vivos tras el redeploy (401 sin token)
curl -s -o /dev/null -w "%{http_code}" "$INSFORGE_FUNCTIONS_URL/kpis"
# Expected: 401

# 3) Con token HS256 de paridad -> 200 con respuesta
TOKEN=$(python scripts/test_paridad_nube.py --solo-token)
curl -s -X POST "$INSFORGE_FUNCTIONS_URL/chat" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"mensaje":"hola, en una linea: quien eres?"}'
# Expected: {"respuesta":"...analista...","sesion_id":N,"uso":{...}}
```

Además, abrir la PWA del celular y verificar que las 4 vistas existentes siguen cargando (el redeploy de auth no debe romper el login RS256).

- [ ] **Step 5: Commit (si hubo ajustes) y anotar**

Si la verificación obligó a tocar código, commitear el ajuste con mensaje descriptivo. Si no, no hay commit en esta task.

---

### Task 7: Pestaña Chat en la PWA

**Files:**
- Modify: `nube/pwa/src/api.ts` (helper POST + `enviarMensajeChat` + tipos)
- Create: `nube/pwa/src/Chat.tsx`
- Modify: `nube/pwa/src/App.tsx` (5ª pestaña; líneas de referencia: tipo del tab en 65, render de vistas 280-548, nav 552-588)
- Modify: `nube/pwa/src/App.css` (estilos del chat, al final)

**Interfaces:**
- Consumes: endpoint `POST /chat` (Task 5/6): request `{mensaje, sesion_id?}`, response `{respuesta, sesion_id, uso}`, error 429 `{error: "limite_diario", detalle}`.
- Produces: componente `<Chat />` (sin props); `enviarMensajeChat(mensaje: string, sesionId: number | null): Promise<ChatRespuesta>` en `api.ts`.

- [ ] **Step 1: Generalizar el helper de `api.ts` y agregar `enviarMensajeChat`**

En `nube/pwa/src/api.ts`, reemplazar la función `invocarEdgeFunction` completa (líneas 76-109) por:

```ts
// Helper interno para llamar las edge functions de InsForge (GET o POST)
async function invocarEdgeFunction(
  slug: string,
  opciones?: { method?: 'GET' | 'POST'; body?: unknown },
): Promise<any> {
  const httpClient = insforge.getHttpClient();
  const token = await httpClient.getValidAccessToken();

  // URL directa de funciones
  const functionsUrl = 'https://z86cmn8g.function2.insforge.app';
  const url = `${functionsUrl}/${slug}`;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(url, {
    method: opciones?.method ?? 'GET',
    headers,
    body: opciones?.body !== undefined ? JSON.stringify(opciones.body) : undefined,
  });

  if (!res.ok) {
    let errorDetail = '';
    try {
      const data = await res.json();
      errorDetail = data.detalle || data.error || '';
    } catch {
      errorDetail = res.statusText;
    }
    throw new Error(`Error en API (${res.status}): ${errorDetail || 'Petición fallida'}`);
  }

  return await res.json();
}
```

Y agregar al final del archivo:

```ts
export interface ChatUso {
  input_tokens: number;
  output_tokens: number;
  n_llamadas_tools: number;
  costo_usd: number;
}

export interface ChatRespuesta {
  respuesta: string;
  sesion_id: number;
  uso: ChatUso;
}

export async function enviarMensajeChat(
  mensaje: string,
  sesionId: number | null,
): Promise<ChatRespuesta> {
  return await invocarEdgeFunction('chat', {
    method: 'POST',
    body: { mensaje, ...(sesionId ? { sesion_id: sesionId } : {}) },
  });
}
```

- [ ] **Step 2: Crear `nube/pwa/src/Chat.tsx`**

```tsx
import React, { useState, useRef, useEffect } from 'react';
import { Send, Trash2 } from 'lucide-react';
import { enviarMensajeChat } from './api';

interface Mensaje {
  rol: 'usuario' | 'asistente';
  texto: string;
}

const CLAVE_SESION = 'zigurat_chat_sesion';
const CLAVE_MENSAJES = 'zigurat_chat_mensajes';

// Pestaña Chat: conversa con el analista de negocio (edge function /chat).
// La sesion y los mensajes persisten en localStorage para sobrevivir cierres
// de la PWA; "limpiar" parte una conversacion nueva (el servidor conserva la
// antigua en chat_sesiones como respaldo).
export default function Chat() {
  const [mensajes, setMensajes] = useState<Mensaje[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(CLAVE_MENSAJES) || '[]');
    } catch {
      return [];
    }
  });
  const [borrador, setBorrador] = useState('');
  const [pensando, setPensando] = useState(false);
  const [error, setError] = useState('');
  const finRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem(CLAVE_MENSAJES, JSON.stringify(mensajes));
    finRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [mensajes, pensando]);

  const enviar = async () => {
    const texto = borrador.trim();
    if (!texto || pensando) return;
    setBorrador('');
    setError('');
    setMensajes((prev) => [...prev, { rol: 'usuario', texto }]);
    setPensando(true);
    try {
      const sesionGuardada = localStorage.getItem(CLAVE_SESION);
      const r = await enviarMensajeChat(texto, sesionGuardada ? Number(sesionGuardada) : null);
      localStorage.setItem(CLAVE_SESION, String(r.sesion_id));
      setMensajes((prev) => [...prev, { rol: 'asistente', texto: r.respuesta }]);
    } catch (err: any) {
      setError(err?.message || 'Error de conexión con el chat.');
    } finally {
      setPensando(false);
    }
  };

  const limpiar = () => {
    if (!window.confirm('¿Empezar una conversación nueva?')) return;
    localStorage.removeItem(CLAVE_SESION);
    localStorage.removeItem(CLAVE_MENSAJES);
    setMensajes([]);
    setError('');
  };

  const alTeclear = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      enviar();
    }
  };

  return (
    <section id="view-chat" className="chat-container">
      <div className="chat-mensajes">
        {mensajes.length === 0 && !pensando && (
          <div className="chat-vacio">
            <p>Pregúntame por el negocio:</p>
            <p>"¿Cuánto me deben?" · "¿Cómo va el flujo de caja?" · "Top 5 clientes"</p>
          </div>
        )}
        {mensajes.map((m, i) => (
          <div key={i} className={`chat-burbuja ${m.rol}`}>
            {m.texto}
          </div>
        ))}
        {pensando && <div className="chat-burbuja asistente chat-pensando">Consultando…</div>}
        {error && <div className="error-banner">{error}</div>}
        <div ref={finRef} />
      </div>

      <div className="chat-input-bar">
        <button
          id="btn-chat-limpiar"
          className="chat-btn-limpiar"
          onClick={limpiar}
          title="Conversación nueva"
        >
          <Trash2 size={18} />
        </button>
        <textarea
          id="chat-input"
          className="chat-input"
          rows={1}
          placeholder="Escribe tu consulta…"
          value={borrador}
          onChange={(e) => setBorrador(e.target.value)}
          onKeyDown={alTeclear}
          disabled={pensando}
        />
        <button
          id="btn-chat-enviar"
          className="chat-btn-enviar"
          onClick={enviar}
          disabled={pensando || !borrador.trim()}
        >
          <Send size={18} />
        </button>
      </div>
    </section>
  );
}
```

- [ ] **Step 3: Integrar la pestaña en `App.tsx`**

Tres ediciones:

1. Imports (junto a los de lucide, línea 2-13, agregar `MessageCircle`; y bajo el import de `./api` agregar):

```tsx
import { MessageCircle } from 'lucide-react';   // fusionar con el import lucide existente
import Chat from './Chat';
```

2. Tipo del tab (línea 65):

```tsx
const [activeTab, setActiveTab] = useState<'inicio' | 'cobros' | 'ventas' | 'flujo' | 'chat'>('inicio');
```

3. Render de la vista — dentro del fragmento de vistas (después del bloque `{activeTab === 'flujo' && ...}` que cierra en la línea ~547):

```tsx
            {/* VISTA 5: CHAT */}
            {activeTab === 'chat' && <Chat />}
```

4. Botón en la barra de navegación (antes del cierre `</nav>` en la línea 588):

```tsx
        <button
          id="tab-btn-chat"
          onClick={() => setActiveTab('chat')}
          className={`nav-item ${activeTab === 'chat' ? 'active' : ''}`}
        >
          <MessageCircle size={20} />
          <span>Chat</span>
        </button>
```

- [ ] **Step 4: Estilos — agregar al final de `nube/pwa/src/App.css`**

```css
/* ===== Chat (Fase 4) ===== */
.chat-container {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 160px); /* header + bottom nav */
}

.chat-mensajes {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 8px 2px;
}

.chat-vacio {
  margin: auto;
  text-align: center;
  opacity: 0.6;
  font-size: 0.9rem;
  line-height: 1.6;
}

.chat-burbuja {
  max-width: 85%;
  padding: 10px 14px;
  border-radius: 16px;
  font-size: 0.95rem;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-burbuja.usuario {
  align-self: flex-end;
  background: var(--primary);
  color: #fff;
  border-bottom-right-radius: 4px;
}

.chat-burbuja.asistente {
  align-self: flex-start;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-bottom-left-radius: 4px;
}

.chat-pensando {
  opacity: 0.7;
  font-style: italic;
  animation: pulse 1.2s ease-in-out infinite;
}

.chat-input-bar {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding: 8px 0 4px;
}

.chat-input {
  flex: 1;
  resize: none;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: rgba(255, 255, 255, 0.06);
  color: inherit;
  padding: 10px 12px;
  font-size: 16px; /* evita el zoom automatico de iOS/Android al enfocar */
  font-family: inherit;
}

.chat-btn-enviar,
.chat-btn-limpiar {
  border: none;
  border-radius: 12px;
  padding: 10px 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
}

.chat-btn-enviar {
  background: var(--primary);
  color: #fff;
}

.chat-btn-enviar:disabled {
  opacity: 0.4;
}

.chat-btn-limpiar {
  background: rgba(255, 255, 255, 0.08);
  color: inherit;
}
```

> Nota: si `App.css` no define `@keyframes pulse` ni `--primary`, reusar la variable/animación que sí exista en ese archivo (revisarlo al editar) — el objetivo visual es: burbujas del usuario con el color primario de la app, burbujas del asistente con el mismo estilo glass de las tarjetas.

- [ ] **Step 5: Build y lint**

Run: `cd nube/pwa && npm run build && npm run lint`
Expected: build OK (tsc + vite) y lint sin errores.

- [ ] **Step 6: Desplegar la PWA y probar en el celular**

Redeploy del build a InsForge Sites (mismo método usado en Fase 3: MCP de InsForge o dashboard → Sites, subiendo `nube/pwa/dist/`). Probar en el celular: login → pestaña Chat → "¿cuánto me deben?" → respuesta con cifra; cerrar y reabrir la app → la conversación sigue; botón basurero → conversación nueva.

- [ ] **Step 7: Commit**

```bash
git add nube/pwa/src/api.ts nube/pwa/src/Chat.tsx nube/pwa/src/App.tsx nube/pwa/src/App.css
git commit -m "Agrega la pestana Chat a la PWA con sesion persistente en localStorage"
```

---

### Task 8: Test de aceptación `scripts/test_chat_nube.py`

Criterio de éxito 3 de la spec: el chat responde con tools y rechaza escrituras. Script manual (necesita red, stack desplegado y gasta ~US$0,05) — NO es parte de `python -m pytest -q`, igual que `test_paridad_nube.py`.

**Files:**
- Create: `scripts/test_chat_nube.py`

**Interfaces:**
- Consumes: `POST /chat` (Task 6 desplegado); `token_jwt()` — se re-implementa localmente con el mismo patrón HS256 de `scripts/test_paridad_nube.py`; `.env` con `INSFORGE_FUNCTIONS_URL`, `INSFORGE_JWT_SECRET`, `INSFORGE_DB_URL` y credenciales locales.
- Produces: salida `CHAT NUBE OK` / `CHAT NUBE FALLO` con detalle.

- [ ] **Step 1: Crear `scripts/test_chat_nube.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_chat_nube.py - Zigurat ERP, Fase 4
Aceptacion del chat en la nube (criterio 3 de la spec Zigurat Movil):
1. Paridad: la deuda total que responde el chat coincide con la BD local.
2. Continuidad: una segunda pregunta en la misma sesion responde 200.
3. Solo lectura: un pedido de escritura no ejecuta nada (revision manual
   de la respuesta impresa) y aun asi queda logueado en chat_uso.
4. Auditoria: cada consulta agrega una fila a chat_uso con costo > 0.

Script manual (gasta ~US$0,05 de API): NO es parte de `python -m pytest -q`.
Correr DESPUES de `python scripts/sync_nube.py` y del deploy de /chat.

Uso:
    python scripts/test_chat_nube.py
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sync_nube import _load_env, conectar_local, conectar_nube  # noqa: E402

_load_env()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token_jwt() -> str:
    """JWT HS256 minimo firmado con el secret del proyecto (1 hora).
    Mismo patron que scripts/test_paridad_nube.py."""
    secreto = os.environ.get("INSFORGE_JWT_SECRET")
    if not secreto:
        raise RuntimeError("Falta INSFORGE_JWT_SECRET en el .env")
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    ahora = int(time.time())
    payload = _b64url(json.dumps(
        {"sub": "aceptacion-chat", "iat": ahora, "exp": ahora + 3600}
    ).encode())
    firma = _b64url(hmac.new(secreto.encode(), f"{header}.{payload}".encode(),
                             hashlib.sha256).digest())
    return f"{header}.{payload}.{firma}"


def preguntar(mensaje: str, token: str, sesion_id=None) -> dict:
    base = os.environ.get("INSFORGE_FUNCTIONS_URL")
    if not base:
        raise RuntimeError("Falta INSFORGE_FUNCTIONS_URL en el .env")
    cuerpo = {"mensaje": mensaje}
    if sesion_id:
        cuerpo["sesion_id"] = sesion_id
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat",
        data=json.dumps(cuerpo).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def formatear_pesos(n) -> str:
    """Mismo formato que formatearPesos de chat_tools.ts: $1.234.567."""
    return "$" + f"{int(round(float(n))):,}".replace(",", ".")


def deuda_local() -> tuple:
    """Query canonica de pendientes (igual que v_pendientes)."""
    conn = conectar_local()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(COALESCE(v.monto_total_ajustado, v.monto_total)), 0),
                       COUNT(*)
                FROM ventas v
                JOIN clientes c ON c.rut_cliente = v.rut_cliente
                WHERE v.tipo_documento != '61' AND v.fecha_pago IS NULL
                  AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
                  AND COALESCE(c.estado, '') <> 'incobrable'
            """)
            total, n = cur.fetchone()
            return float(total), int(n)
    finally:
        conn.close()


def filas_chat_uso() -> int:
    conn = conectar_nube()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chat_uso")
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def main() -> int:
    token = token_jwt()
    fallas = []
    uso_antes = filas_chat_uso()

    # 1. Paridad de la deuda total.
    total_local, n_local = deuda_local()
    esperado = formatear_pesos(total_local)
    r1 = preguntar(
        "Dime el monto exacto en pesos de la deuda total pendiente y el "
        "numero de facturas, tal como los entregue la herramienta.", token)
    print(f"[1] respuesta: {r1['respuesta']}\n    uso: {r1['uso']}")
    if esperado not in r1["respuesta"]:
        fallas.append(f"paridad: esperaba ver {esperado} "
                      f"(local: {n_local} facturas) en la respuesta")

    # 2. Continuidad de sesion.
    r2 = preguntar("¿Y cuantas de esas facturas tienen mas de 30 dias?",
                   token, r1["sesion_id"])
    print(f"[2] respuesta: {r2['respuesta']}")
    if r2["sesion_id"] != r1["sesion_id"]:
        fallas.append("continuidad: la sesion cambio entre preguntas")
    if not r2["respuesta"].strip():
        fallas.append("continuidad: respuesta vacia")

    # 3. Solo lectura: la respuesta se imprime para revision manual.
    r3 = preguntar("Marca la factura 4664 como pagada.", token, r1["sesion_id"])
    print(f"[3] (revisar a ojo que NO afirme haberlo hecho)\n    respuesta: {r3['respuesta']}")

    # 4. Auditoria: 3 consultas -> 3 filas nuevas en chat_uso con costo > 0.
    uso_despues = filas_chat_uso()
    if uso_despues - uso_antes != 3:
        fallas.append(f"auditoria: esperaba 3 filas nuevas en chat_uso, "
                      f"hay {uso_despues - uso_antes}")
    if r1["uso"]["costo_usd"] <= 0:
        fallas.append("auditoria: costo_usd deberia ser > 0")

    if fallas:
        print("\nCHAT NUBE FALLO:")
        for f in fallas:
            print(f"  - {f}")
        return 1
    print(f"\nCHAT NUBE OK (deuda {esperado} en {n_local} facturas; "
          f"{uso_despues - uso_antes} consultas logueadas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> Nota: `conectar_nube` ya existe en `sync_nube.py` (la usa el sync); si su cursor no es de tuplas sino RealDict, ajustar los `fetchone()` según corresponda al leerla.

- [ ] **Step 2: Correr la aceptación completa**

Run:
```bash
python scripts/sync_nube.py
python scripts/test_chat_nube.py
```
Expected: `CHAT NUBE OK (deuda $X en N facturas; 3 consultas logueadas)` y, en la salida de [3], una respuesta que rechaza la escritura y redirige al Centro de Comando.

- [ ] **Step 3: Verificar que la suite local sigue verde**

Run: `python -m pytest -q`
Expected: todos los tests pasan.

- [ ] **Step 4: Commit final**

```bash
git add scripts/test_chat_nube.py
git commit -m "Agrega test de aceptacion del chat en la nube (paridad, sesion, solo lectura, auditoria)"
```

---

## Self-review (hecho al escribir el plan)

1. **Cobertura de la spec 4.4:** tool-use loop ✔ (Task 4), modelo Sonnet configurable ✔ (env `CHAT_MODELO`), 10 tools espejo de solo lectura ✔ (Task 3 — la spec listaba las mismas 10), el modelo nunca genera SQL ✔ (no existe tool de SQL), system prompt adaptado ✔ (Task 5), historial en tabla ✔ (Task 2/5), botón limpiar ✔ (Task 7), sin streaming con indicador "pensando" ✔ (Task 7), límite de 8 iteraciones + max_tokens + log de uso ✔ (Tasks 4/5). Seguridad §5: JWT en todo endpoint ✔ + endurecimiento extra (Task 1). Criterio de éxito 3 ✔ (Task 8).
2. **Placeholders:** ninguno — todo step tiene código o comando concreto. Los dos puntos que dependen de inspección local (variables CSS de `App.css`, tipo de cursor de `conectar_nube`) están marcados explícitamente como verificación al editar, con el objetivo definido.
3. **Consistencia de tipos:** `SqlCliente`/`TOOLS`/`ejecutarTool` (Task 3) = lo que importa Task 5; `correrChat`/`llamarModeloReal`/`MensajeAPI`/`UsoChat` (Task 4) = lo que importa Task 5; respuesta `{respuesta, sesion_id, uso}` (Task 5) = lo que consumen `enviarMensajeChat` (Task 7) y `preguntar` (Task 8); `formatearPesos` TS (Task 3) = `formatear_pesos` Python (Task 8, mismo formato con puntos).
