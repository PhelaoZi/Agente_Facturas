# Zigurat Móvil — Backend en la nube (Fases 0–2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réplica de solo lectura del Postgres local en InsForge, con views canónicas y edge functions de consulta (`/api/kpis`, `/api/pendientes`, `/api/ventas`, `/api/flujo`) verificadas por paridad de cifras contra la BD local.

**Architecture:** El pipeline local queda intacto; `scripts/sync_nube.py` replica 5 tablas por truncate+copy transaccional y registra metadatos en `sync_meta`. Las reglas de negocio viven UNA vez en views SQL (`migrate_nube_views.sql`). Las edge functions (Deno/TS) solo leen views y verifican JWT de InsForge Auth.

**Tech Stack:** Python 3 + psycopg2 (sync), SQL (views), Deno/TypeScript (edge functions, `npm:postgres`, `npm:jose`), InsForge CLI/MCP.

**Spec:** `docs/superpowers/specs/2026-07-14-zigurat-movil-nube-design.md`
**Fases 3 (PWA) y 4 (chat):** plan separado cuando este backend esté desplegado.

## Global Constraints

- Comentarios, mensajes de log y commits en **español**.
- Archivos de `scripts/` en snake_case (patrón del repo), no kebab-case.
- Reglas canónicas SOLO en las views: `COALESCE(monto_*_ajustado, monto_*)`, `tipo_documento != '61'`, `fecha_pago IS NULL` = pendiente, filtro anti-Logistica/PET. Ninguna función TS reimplementa reglas.
- `sync_nube.py` es **no fatal** para el pipeline: exit code 1 en error, pero quien lo invoca lo trata como warning.
- Credenciales solo en `.env` (local) y secrets de InsForge (nube). Nada en git.
- `.env` nuevo: `INSFORGE_DB_URL` (connection string Postgres del proyecto InsForge).
- Al terminar cada tarea: `python -m pytest -q` debe estar verde.
- La columna de nombre de producto es `productos.nombre_producto` (verificado contra information_schema en Task 2; la referencia `p.descripcion` de `app/negocio/ventas.py:77` es un bug preexistente de ese módulo); el estado incobrable es `clientes.estado = 'incobrable'` (verificado en `app/briefing/data.py:22`).
- Patrón `_load_env()` y `log()` copiados de `scripts/backup_db.py:49-57,187-193`.

---

### Task 1: Fase 0 — Proyecto InsForge conectado y verificado

**Files:**
- Modify: `.env` (local, NO va a git)
- Modify: `.env.example`
- Modify: `.mcp.json` (local, NO va a git)

**Interfaces:**
- Produces: variable `INSFORGE_DB_URL` en `.env` que Tasks 4 y 8 leen vía `_load_env()`; MCP de InsForge disponible en Claude Code para Tasks 6–8.

**Nota:** esta tarea mezcla acciones de Christian (cuenta, login) con verificaciones del agente. No hay TDD porque no hay código — el "test" es cada verificación.

- [ ] **Step 1 (Christian): crear cuenta y proyecto** — En https://insforge.dev crear cuenta (plan gratis) y un proyecto llamado `zigurat-movil`. Anotar el **project ID**.

- [ ] **Step 2: login y link del CLI**

```powershell
npx @insforge/cli login
npx @insforge/cli link --project-id <PROJECT_ID>
```

Expected: mensaje de éxito del CLI. Si los verbos del CLI difieren, consultar la referencia oficial: https://docs.insforge.dev (sección CLI) vía context7 (`/insforge/insforge`, query "CLI login link project").

- [ ] **Step 3: obtener el connection string de Postgres** — En el panel del proyecto InsForge (o vía CLI, sección database de las docs), copiar el connection string directo a Postgres. Agregarlo al `.env`:

```
INSFORGE_DB_URL=postgresql://usuario:clave@host:5432/base
```

Y a `.env.example` (sin valor):

```
# Réplica en la nube (proyecto InsForge zigurat-movil)
INSFORGE_DB_URL=
```

**Checkpoint:** si InsForge cloud NO expone conexión directa a Postgres, DETENERSE y reportar — el diseño de sync depende de esto y habría que pasar por su API REST.

- [ ] **Step 4: verificar conexión**

```powershell
$env:INSFORGE_URL_TMP = (Get-Content .env | Where-Object { $_ -match '^INSFORGE_DB_URL=' }) -replace '^INSFORGE_DB_URL=', ''
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" $env:INSFORGE_URL_TMP -c "SELECT 1;"
```

Expected: ` ?column? --- 1`.

- [ ] **Step 5: conectar el MCP de InsForge** — agregar al `.mcp.json` local (no committeado):

```json
"insforge": { "type": "http", "url": "https://mcp.insforge.dev/mcp" }
```

Verificar en una sesión nueva de Claude Code que las tools `mcp__insforge__*` aparecen.

- [ ] **Step 6: commit**

```bash
git add .env.example
git commit -m "Agrega INSFORGE_DB_URL a .env.example para la replica en la nube"
```

---

### Task 2: Views canónicas y esquema mínimo de la réplica

**Files:**
- Create: `scripts/migrate_nube_views.sql`

**Interfaces:**
- Produces: tabla `sync_meta(clave TEXT PK, valor JSONB, actualizado TIMESTAMPTZ)`; views `v_ventas_reales`, `v_pendientes`, `v_flujo_pendientes`, `v_dias_pago_cliente`, `v_ventas_producto`. Tasks 4, 7 y 8 consumen exactamente estos nombres y columnas.

- [ ] **Step 1: verificar columnas reales contra la BD local**

```powershell
$env:PGPASSWORD = "<DB_PASSWORD del .env>"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -U postgres -d dte_facturas_chile -c "\d productos" -c "\d ventas" -c "\d clientes"
```

Confirmar que existen: `productos.nombre_producto`, `productos.tipo_documento`, `ventas.razon_social_receptor`, `ventas.dias_pago`, `clientes.estado`. Si algún nombre difiere, ajustar el SQL del paso 2 y **reportarlo en el resumen de la tarea**.

- [ ] **Step 2: escribir el SQL (idempotente)**

```sql
-- migrate_nube_views.sql — Zigurat Movil
-- Esquema minimo de la replica InsForge: metadatos del sync + views canonicas.
-- Las reglas de negocio del CLAUDE.md viven AQUI y solo aqui (las edge
-- functions consultan views, nunca reimplementan reglas). Idempotente.

CREATE TABLE IF NOT EXISTS sync_meta (
    clave       TEXT PRIMARY KEY,
    valor       JSONB NOT NULL,
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ventas reales por factura: montos ajustados por NC, excluye las NC mismas.
CREATE OR REPLACE VIEW v_ventas_reales AS
SELECT v.folio, v.tipo_documento, v.fecha, v.rut_cliente,
       v.razon_social_receptor,
       COALESCE(v.monto_neto_ajustado,  v.monto_neto)  AS neto_real,
       COALESCE(v.monto_total_ajustado, v.monto_total) AS total_real,
       v.fecha_pago, v.dias_pago
FROM ventas v
WHERE v.tipo_documento != '61';

-- Por cobrar COBRABLE (excluye clientes castigados como incobrables).
CREATE OR REPLACE VIEW v_pendientes AS
SELECT v.folio, v.fecha, v.rut_cliente, c.razon_social,
       COALESCE(v.monto_total_ajustado, v.monto_total) AS total,
       (CURRENT_DATE - v.fecha) AS dias_desde_emision
FROM ventas v
JOIN clientes c ON c.rut_cliente = v.rut_cliente
WHERE v.tipo_documento != '61'
  AND v.fecha_pago IS NULL
  AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
  AND COALESCE(c.estado, '') <> 'incobrable';

-- Espejo EXACTO de obtener_facturas_pendientes de app/negocio/flujo.py
-- (el flujo historicamente incluye incobrables; mantener paridad).
CREATE OR REPLACE VIEW v_flujo_pendientes AS
SELECT folio, fecha, rut_cliente, razon_social_receptor,
       COALESCE(monto_total_ajustado, monto_total) AS monto
FROM ventas
WHERE fecha_pago IS NULL AND tipo_documento != '61';

-- Espejo de obtener_avg_dias_por_cliente de app/negocio/flujo.py
-- (ultimas 10 facturas pagadas, minimo 3 para promediar).
CREATE OR REPLACE VIEW v_dias_pago_cliente AS
SELECT rut_cliente, AVG(dias_pago) AS avg_dias
FROM (
    SELECT rut_cliente, dias_pago,
           ROW_NUMBER() OVER (PARTITION BY rut_cliente ORDER BY fecha DESC) AS rn
    FROM ventas
    WHERE fecha_pago IS NOT NULL AND dias_pago IS NOT NULL
      AND dias_pago > 0 AND tipo_documento != '61'
) t
WHERE rn <= 10
GROUP BY rut_cliente
HAVING COUNT(*) >= 3;

-- Lineas de producto SIN Logistica ni envases PET (filtro canonico del
-- CLAUDE.md raiz, con la columna real `nombre_producto`).
CREATE OR REPLACE VIEW v_ventas_producto AS
SELECT p.folio, v.fecha, v.rut_cliente, p.nombre_producto,
       p.cantidad, p.precio_unitario
FROM productos p
JOIN ventas v ON v.folio = p.folio AND v.tipo_documento = p.tipo_documento
WHERE v.tipo_documento != '61'
  AND p.nombre_producto NOT ILIKE '%logist%'
  AND p.nombre_producto !~* '^(barril(es)?\s+)?pet\y';
```

- [ ] **Step 3: probar el SQL contra la BD LOCAL** (las views son válidas en ambos lados; probar local no toca la nube)

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -U postgres -d dte_facturas_chile -f scripts/migrate_nube_views.sql
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -U postgres -d dte_facturas_chile -c "SELECT COUNT(*) FROM v_pendientes;" -c "SELECT COUNT(*) FROM v_ventas_producto;"
```

Expected: `CREATE TABLE`/`CREATE VIEW` sin errores; counts > 0.

- [ ] **Step 4: validar paridad de la view contra el código existente** — comparar `SELECT COALESCE(SUM(total),0) FROM v_pendientes` con el "por cobrar" del dashboard local (`python app/dashboard.py` → KPI, o la query de `app/dashboard.py:140-150`). Deben coincidir exactamente. Si difieren, revisar filtros antes de seguir.

- [ ] **Step 5: commit**

```bash
git add scripts/migrate_nube_views.sql
git commit -m "Agrega views canonicas y sync_meta para la replica InsForge"
```

---

### Task 3: Tests de sync_nube (primero, TDD)

**Files:**
- Create: `tests/test_sync_nube.py`

**Interfaces:**
- Consumes: nada (los tests definen el contrato).
- Produces: contrato que Task 4 implementa — `TABLAS_ORDEN`, `sql_insert(tabla, columnas) -> str`, `sync(conn_local, conn_nube, ahora=None) -> dict[str, int]`, `main() -> int`.

- [ ] **Step 1: escribir los tests (cursores/conexiones falsos, patrón de `tests/test_negocio_*.py`)**

```python
# -*- coding: utf-8 -*-
"""Tests de scripts/sync_nube.py: orden de tablas, SQL generado y no-fatalidad."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sync_nube


class CursorFalso:
    """Registra cada execute; fetchall devuelve lo encolado."""
    def __init__(self, respuestas=None):
        self.ejecutado = []
        self.respuestas = list(respuestas or [])
        self.description = None

    def execute(self, sql, params=None):
        self.ejecutado.append(sql.strip())

    def fetchall(self):
        return self.respuestas.pop(0) if self.respuestas else []

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConexionFalsa:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self, **kwargs):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):
        if exc_type is None:
            self.commits += 1
        return False


def test_orden_de_tablas_respeta_fks():
    orden = sync_nube.TABLAS_ORDEN
    assert orden.index("clientes") < orden.index("ventas")
    assert orden.index("ventas") < orden.index("productos")
    assert orden.index("ventas") < orden.index("conciliaciones")


def test_sql_insert_construye_columnas():
    sql = sync_nube.sql_insert("clientes", ["rut_cliente", "razon_social"])
    assert sql == "INSERT INTO clientes (rut_cliente, razon_social) VALUES %s"


def test_sync_trunca_todo_en_una_sentencia_y_replica(monkeypatch):
    cur_nube = CursorFalso()
    conn_local = ConexionFalsa(CursorFalso())
    conn_nube = ConexionFalsa(cur_nube)

    monkeypatch.setattr(sync_nube, "leer_tabla",
                        lambda cur, t: (["a"], [(1,)]))
    llamadas = []
    monkeypatch.setattr(sync_nube, "replicar_tabla",
                        lambda cur, t, cols, filas: llamadas.append(t))
    monkeypatch.setattr(sync_nube, "obtener_saldo_banco",
                        lambda cur: (1000.0, None))

    total = sync_nube.sync(conn_local, conn_nube)

    truncates = [s for s in cur_nube.ejecutado if s.startswith("TRUNCATE")]
    assert len(truncates) == 1                      # una sola sentencia
    for tabla in sync_nube.TABLAS_ORDEN:            # todas las tablas en ella
        assert tabla in truncates[0]
    assert llamadas == sync_nube.TABLAS_ORDEN       # replica en orden de FKs
    assert conn_nube.commits == 1                   # una sola transaccion
    assert total == {t: 1 for t in sync_nube.TABLAS_ORDEN}
    metas = [s for s in cur_nube.ejecutado if "sync_meta" in s]
    assert metas, "debe registrar metadatos del sync"


def test_main_es_no_fatal(monkeypatch):
    def explota():
        raise RuntimeError("sin internet")
    monkeypatch.setattr(sync_nube, "conectar_nube", explota)
    monkeypatch.setattr(sync_nube, "conectar_local", explota)
    assert sync_nube.main([]) == 1                  # informa error, no lanza
```

- [ ] **Step 2: correr y verificar que fallan**

Run: `python -m pytest tests/test_sync_nube.py -v`
Expected: FAIL/ERROR con `ModuleNotFoundError: No module named 'sync_nube'`.

- [ ] **Step 3: commit**

```bash
git add tests/test_sync_nube.py
git commit -m "Agrega tests de sync_nube (orden FK, transaccion unica, no fatal)"
```

---

### Task 4: `scripts/sync_nube.py`

**Files:**
- Create: `scripts/sync_nube.py`
- Test: `tests/test_sync_nube.py` (de Task 3)

**Interfaces:**
- Consumes: `INSFORGE_DB_URL` del `.env` (Task 1); `scripts/migrate_nube_views.sql` (Task 2).
- Produces: CLI `python scripts/sync_nube.py [--init]`; funciones `sync`, `sql_insert`, `leer_tabla`, `replicar_tabla`, `obtener_saldo_banco`, `conectar_local`, `conectar_nube`, `main`. `sync_meta` queda con claves `ultimo_sync` y `saldo_banco` que Task 7 lee.

- [ ] **Step 1: implementar**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_nube.py - Zigurat ERP
Replica de SOLO LECTURA del Postgres local hacia el proyecto InsForge
(zigurat-movil). La BD local sigue siendo la fuente de verdad.

Flujo: leer 5 tablas locales -> TRUNCATE + INSERT masivo en la nube dentro
de UNA transaccion -> registrar metadatos (ultimo_sync, saldo_banco) en
sync_meta. Con --init ademas aplica scripts/migrate_nube_views.sql y crea
las tablas (esquema copiado de la BD local con pg_dump --schema-only).

NO FATAL: siempre termina con exit code (0 ok, 1 error) y loggea; quien lo
invoque desde el pipeline debe tratar el 1 como warning, nunca abortar.

Uso:
    python scripts/sync_nube.py           # replica los datos
    python scripts/sync_nube.py --init    # primera vez: esquema + views + datos
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "logs" / "sync_nube.log"
SQL_VIEWS = PROJECT_ROOT / "scripts" / "migrate_nube_views.sql"

# Orden de carga: padres antes que hijos (FKs). El TRUNCATE va en una sola
# sentencia con todas, asi Postgres resuelve las dependencias entre ellas.
TABLAS_ORDEN = ["clientes", "ventas", "productos", "conciliaciones",
                "cuentas_por_pagar"]
LOTE = 1000
TIMEOUT_PG_DUMP = 120


def _load_env():
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def log(mensaje):
    """Imprime y anexa a logs/sync_nube.log con timestamp."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {mensaje}"
    print(linea)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def conectar_local():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "dte_facturas_chile"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ["DB_PASSWORD"],
    )


def conectar_nube():
    url = os.environ.get("INSFORGE_DB_URL")
    if not url:
        raise RuntimeError("Falta INSFORGE_DB_URL en el .env")
    return psycopg2.connect(url)


def sql_insert(tabla, columnas):
    """SQL de insert masivo para execute_values. `tabla` y `columnas` vienen
    de TABLAS_ORDEN y de cursor.description (nunca de input externo)."""
    return f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES %s"


def leer_tabla(cur_local, tabla):
    cur_local.execute(f"SELECT * FROM {tabla}")
    columnas = [d[0] for d in cur_local.description]
    return columnas, cur_local.fetchall()


def replicar_tabla(cur_nube, tabla, columnas, filas):
    execute_values(cur_nube, sql_insert(tabla, columnas), filas, page_size=LOTE)


def obtener_saldo_banco(cur_local):
    """Ultimo saldo_diario de movimientos_banco (espejo de app/negocio/flujo.py).
    La tabla NO se replica; solo viaja este valor, que el flujo de la nube usa
    como saldo inicial."""
    cur_local.execute("""
        SELECT saldo_diario, fecha FROM movimientos_banco
        WHERE saldo_diario IS NOT NULL ORDER BY fecha DESC LIMIT 1
    """)
    fila = cur_local.fetchone()
    if fila:
        return float(fila[0]), fila[1]
    return None, None


def guardar_meta(cur_nube, clave, valor):
    cur_nube.execute("""
        INSERT INTO sync_meta (clave, valor, actualizado)
        VALUES (%s, %s, now())
        ON CONFLICT (clave) DO UPDATE
        SET valor = EXCLUDED.valor, actualizado = now()
    """, (clave, json.dumps(valor, default=str)))


def sync(conn_local, conn_nube, ahora=None):
    """Replica todas las tablas en UNA transaccion en la nube.
    Devuelve {tabla: filas_copiadas}."""
    ahora = ahora or datetime.now()
    total = {}
    with conn_local.cursor() as cur_local:
        with conn_nube:  # commit al salir sin excepcion; rollback si falla
            with conn_nube.cursor() as cur_nube:
                cur_nube.execute(f"TRUNCATE {', '.join(TABLAS_ORDEN)}")
                for tabla in TABLAS_ORDEN:
                    columnas, filas = leer_tabla(cur_local, tabla)
                    if filas:
                        replicar_tabla(cur_nube, tabla, columnas, filas)
                    total[tabla] = len(filas)
                saldo, fecha_saldo = obtener_saldo_banco(cur_local)
                guardar_meta(cur_nube, "saldo_banco",
                             {"saldo": saldo, "fecha": fecha_saldo})
                guardar_meta(cur_nube, "ultimo_sync",
                             {"momento": ahora.isoformat(timespec="seconds"),
                              "filas": total})
    return total


def aplicar_esquema(conn_nube):
    """--init: copia el esquema de las 5 tablas desde la BD local (pg_dump
    --schema-only) y aplica las views. Idempotente en las views; si una tabla
    ya existe en la nube, pg_dump/psql reportara el error y seguimos."""
    from backup_db import localizar_pg_dump  # reutiliza la autodeteccion
    pg_dump = localizar_pg_dump()
    env = {**os.environ, "PGPASSWORD": os.environ.get("DB_PASSWORD", "")}
    cmd = [str(pg_dump), "--schema-only", "--no-owner", "--no-privileges",
           "-h", os.environ.get("DB_HOST", "localhost"),
           "-p", os.environ.get("DB_PORT", "5432"),
           "-U", os.environ.get("DB_USER", "postgres"),
           "-d", os.environ.get("DB_NAME", "dte_facturas_chile")]
    for tabla in TABLAS_ORDEN:
        cmd += ["-t", tabla]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                       errors="replace", timeout=TIMEOUT_PG_DUMP)
    if r.returncode != 0:
        raise RuntimeError(f"pg_dump --schema-only fallo: {r.stderr[:300]}")
    with conn_nube:
        with conn_nube.cursor() as cur:
            cur.execute(r.stdout)
            cur.execute(SQL_VIEWS.read_text(encoding="utf-8"))
    log("Esquema y views aplicados en la nube (--init)")


def main(argv=None):
    """argv inyectable para los tests (pytest contamina sys.argv)."""
    inicio = time.monotonic()
    parser = argparse.ArgumentParser(description="Replica local -> InsForge")
    parser.add_argument("--init", action="store_true",
                        help="primera vez: crea esquema y views antes de sincronizar")
    args = parser.parse_args(argv)
    try:
        conn_local = conectar_local()
        conn_nube = conectar_nube()
        try:
            if args.init:
                aplicar_esquema(conn_nube)
            total = sync(conn_local, conn_nube)
        finally:
            conn_local.close()
            conn_nube.close()
        duracion = round(time.monotonic() - inicio, 1)
        resumen = ", ".join(f"{t}={n}" for t, n in total.items())
        log(f"Sync OK en {duracion}s: {resumen}")
        return 0
    except Exception as e:
        log(f"ERROR (no fatal para el pipeline): {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: correr los tests**

Run: `python -m pytest tests/test_sync_nube.py -v`
Expected: 4 passed.

- [ ] **Step 3: suite completa**

Run: `python -m pytest -q`
Expected: todo verde (los tests existentes no se tocan).

- [ ] **Step 4: primera réplica real**

```powershell
python scripts/sync_nube.py --init
python scripts/sync_nube.py
```

Expected: `Esquema y views aplicados...` y luego `Sync OK en Xs: clientes=N, ventas=N, ...` con N > 0. Verificar en la nube:

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" $env:INSFORGE_URL_TMP -c "SELECT COUNT(*) FROM ventas;" -c "SELECT clave, actualizado FROM sync_meta;"
```

Expected: mismo COUNT que la BD local; 2 filas en sync_meta.

- [ ] **Step 5: commit**

```bash
git add scripts/sync_nube.py
git commit -m "Agrega sync_nube.py: replica de solo lectura hacia InsForge"
```

---

### Task 5: Automatización — tarea programada y paso no fatal en las skills

**Files:**
- Create: `scripts/instalar_tarea_sync_nube.ps1`
- Modify: `.claude/skills/sync-facturas/SKILL.md`
- Modify: `.claude/skills/sync-nc/SKILL.md`
- Modify: `.claude/skills/conciliar-banco/SKILL.md`

**Interfaces:**
- Consumes: `scripts/sync_nube.py` (Task 4).
- Produces: tarea de Windows "Zigurat - Sync Nube" (08:15, `StartWhenAvailable`); las 3 skills terminan invocando el sync como warning.

- [ ] **Step 1: crear el instalador copiando el patrón existente** — Leer `scripts/instalar_tarea_brief.ps1` y crear `instalar_tarea_sync_nube.ps1` idéntico salvo estos tres campos: nombre de tarea `"Zigurat - Sync Nube"`, hora `08:15`, script `scripts\sync_nube.py`. Mantener `LogonType Interactive` + `StartWhenAvailable` (mismo razonamiento que el backup: corre al iniciar sesión si el equipo estaba apagado).

- [ ] **Step 2: instalar y verificar**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea_sync_nube.ps1
schtasks /query /tn "Zigurat - Sync Nube"
```

Expected: la tarea aparece con estado Listo/Ready.

- [ ] **Step 3: agregar el paso final a las 3 skills** — En cada SKILL.md (`sync-facturas`, `sync-nc`, `conciliar-banco`), agregar al final del flujo este bloque textual:

```markdown
## Paso final: replicar a la nube (no fatal)

Tras un sync/conciliación exitoso, ejecutar:

    python scripts/sync_nube.py

Si falla (sin internet, InsForge caído), mostrar el error como WARNING y
terminar normalmente: la réplica es secundaria, el pipeline local es lo
importante. NUNCA abortar ni reintentar por este paso.
```

- [ ] **Step 4: commit**

```bash
git add scripts/instalar_tarea_sync_nube.ps1 .claude/skills/sync-facturas/SKILL.md .claude/skills/sync-nc/SKILL.md .claude/skills/conciliar-banco/SKILL.md
git commit -m "Automatiza la replica a la nube: tarea diaria y paso final en skills"
```

---

### Task 6: Edge functions — helpers compartidos y lógica de flujo con tests

**Files:**
- Create: `nube/functions/_shared/db.ts`
- Create: `nube/functions/_shared/auth.ts`
- Create: `nube/functions/_shared/flujo.ts`
- Test: `nube/functions/_shared/flujo_test.ts`

**Interfaces:**
- Consumes: secrets de InsForge `INSFORGE_DB_URL` y `INSFORGE_JWT_SECRET` (se configuran en Task 8).
- Produces: `db(): Sql` (cliente postgres), `requireUser(req: Request): Promise<Response | null>` (null = autorizado; Response = 401 listo para devolver), `proyectarFlujo(facturas, avgDias, gastos, saldoInicial, hoy): FlujoResult`. Task 7 importa estos tres.

**Requiere Deno instalado localmente** (`winget install DenoLand.Deno`) para correr los tests.

**Antes de escribir código:** consultar vía context7 (`/insforge/insforge`) la firma exacta del handler de functions y cómo acceden a la BD ("edge function database access", "function handler signature"). Si InsForge inyecta un cliente de BD propio o usa `module.exports`, adaptar estos archivos a ese template y anotarlo en el resumen de la tarea. El código siguiente asume handler `export default` y acceso directo por connection string.

- [ ] **Step 1: helper de BD**

```typescript
// nube/functions/_shared/db.ts
// Cliente Postgres compartido por todas las functions (solo lectura de views).
import postgres from "npm:postgres@3.4.5";

let sql: ReturnType<typeof postgres> | null = null;

export function db() {
  if (!sql) {
    const url = Deno.env.get("INSFORGE_DB_URL");
    if (!url) throw new Error("Falta el secret INSFORGE_DB_URL");
    sql = postgres(url, { max: 1, prepare: false });
  }
  return sql;
}
```

- [ ] **Step 2: helper de auth**

```typescript
// nube/functions/_shared/auth.ts
// Verifica el JWT de InsForge Auth. Devuelve null si el usuario es valido,
// o una Response 401 lista para retornar. Ningun endpoint responde sin esto.
import { jwtVerify } from "npm:jose@5";

export async function requireUser(req: Request): Promise<Response | null> {
  const header = req.headers.get("Authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) return sin401("falta token");
  try {
    const secreto = new TextEncoder().encode(
      Deno.env.get("INSFORGE_JWT_SECRET") ?? "",
    );
    await jwtVerify(token, secreto);
    return null;
  } catch {
    return sin401("token invalido");
  }
}

function sin401(detalle: string): Response {
  return new Response(JSON.stringify({ error: "no autorizado", detalle }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  });
}
```

- [ ] **Step 3: tests de la proyección de flujo (fallan primero)**

```typescript
// nube/functions/_shared/flujo_test.ts
// La logica debe ser espejo de app/negocio/flujo.py (paridad de cifras).
import { assertEquals } from "jsr:@std/assert@1";
import { proyectarFlujo } from "./flujo.ts";

const HOY = new Date("2026-07-20");

Deno.test("factura con promedio conocido cae en la semana correcta", () => {
  const r = proyectarFlujo(
    [{ folio: 1, fecha: new Date("2026-07-10"), rut_cliente: "1-9",
       razon_social_receptor: "X", monto: 100 }],
    { "1-9": 15 },   // proyectada = 10 jul + 15d = 25 jul -> semana 0
    [], 0, HOY,
  );
  assertEquals(r.semanas[0].ingresos, 100);
});

Deno.test("proyeccion en el pasado se mueve a hoy (semana 0)", () => {
  const r = proyectarFlujo(
    [{ folio: 2, fecha: new Date("2026-05-01"), rut_cliente: "1-9",
       razon_social_receptor: "X", monto: 50 }],
    { "1-9": 10 },   // proyectada 11 may < hoy -> hoy
    [], 0, HOY,
  );
  assertEquals(r.semanas[0].ingresos, 50);
});

Deno.test("cliente sin historial usa 30 dias globales", () => {
  const r = proyectarFlujo(
    [{ folio: 3, fecha: new Date("2026-07-15"), rut_cliente: "2-7",
       razon_social_receptor: "Y", monto: 80 }],
    {},              // sin avg -> 30d -> 14 ago -> semana 3
    [], 0, HOY,
  );
  assertEquals(r.semanas[3].ingresos, 80);
});

Deno.test("gasto recurrente mensual se proyecta dentro del horizonte", () => {
  const r = proyectarFlujo([], {},
    [{ descripcion: "arriendo", proveedor: "Z", monto: 500,
       fecha_vencimiento: new Date("2026-01-05"), categoria: "fijo",
       recurrente: true, periodicidad: "mensual" }],
    1000, HOY,
  );
  // dia 5 del mes: 5 ago cae en semana 2 del horizonte 20 jul - 17 ago
  assertEquals(r.semanas[2].egresos, 500);
  assertEquals(r.semanas[3].saldo_acumulado, 500);
});
```

- [ ] **Step 4: correr y ver fallar**

Run: `deno test nube/functions/_shared/`
Expected: FAIL — `Module not found "./flujo.ts"`.

- [ ] **Step 5: implementar `flujo.ts` (puerto fiel de `proyectar_flujo` de `app/negocio/flujo.py:99-173`)**

```typescript
// nube/functions/_shared/flujo.ts
// Puerto fiel de app/negocio/flujo.py::proyectar_flujo. Si cambia la logica
// alla, replicar aqui (el test de paridad de Task 8 detecta divergencias).

export const SEMANAS = 4;
export const AVG_DIAS_GLOBAL = 30;
const DIA_MS = 86_400_000;

export interface FacturaPendiente {
  folio: number; fecha: Date; rut_cliente: string;
  razon_social_receptor: string; monto: number;
}
export interface Gasto {
  descripcion: string; proveedor: string | null; monto: number;
  fecha_vencimiento: Date; categoria: string | null;
  recurrente?: boolean; periodicidad?: string | null;
}
export interface Semana {
  semana: number; label: string; ingresos: number; egresos: number;
  saldo_acumulado: number; riesgo: boolean;
}
export interface FlujoResult {
  saldo_inicial: number; semanas: Semana[];
  total_ingresos: number; total_egresos: number; fuera_horizonte: number;
}

const dias = (a: Date, b: Date) => Math.floor((a.getTime() - b.getTime()) / DIA_MS);
const masDias = (d: Date, n: number) => new Date(d.getTime() + n * DIA_MS);
const ddmm = (d: Date) =>
  `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}`;

/** Ocurrencias de un gasto mensual en [hoy, horizonte], mismo dia del mes
 * (recortado al ultimo dia si el mes es mas corto), 3 meses hacia adelante. */
function proyectarRecurrente(g: Gasto, hoy: Date, horizonte: Date): Gasto[] {
  const diaMes = g.fecha_vencimiento.getUTCDate();
  const out: Gasto[] = [];
  for (let dm = 0; dm < 3; dm++) {
    const anio = hoy.getUTCFullYear() + Math.floor((hoy.getUTCMonth() + dm) / 12);
    const mes = (hoy.getUTCMonth() + dm) % 12;
    const ultimo = new Date(Date.UTC(anio, mes + 1, 0)).getUTCDate();
    const fecha = new Date(Date.UTC(anio, mes, Math.min(diaMes, ultimo)));
    if (fecha >= hoy && fecha <= horizonte) {
      out.push({ ...g, fecha_vencimiento: fecha });
    }
  }
  return out;
}

export function proyectarFlujo(
  facturas: FacturaPendiente[],
  avgDias: Record<string, number>,
  gastos: Gasto[],
  saldoInicial: number,
  hoy: Date,
  semanas = SEMANAS,
): FlujoResult {
  const horizonte = masDias(hoy, semanas * 7);

  const ingresosSemana: number[] = Array(semanas).fill(0);
  let fueraHorizonte = 0;
  for (const f of facturas) {
    const avg = Math.trunc(avgDias[f.rut_cliente] ?? AVG_DIAS_GLOBAL);
    let proyectada = masDias(f.fecha, avg);
    if (proyectada < hoy) proyectada = hoy;
    if (proyectada <= horizonte) {
      const sem = Math.max(0, Math.min(Math.floor(dias(proyectada, hoy) / 7), semanas - 1));
      ingresosSemana[sem] += f.monto;
    } else {
      fueraHorizonte += f.monto;
    }
  }

  const egresosSemana: number[] = Array(semanas).fill(0);
  const puntuales = gastos.filter((g) => !g.recurrente);
  const recurrentes = gastos
    .filter((g) => g.recurrente && g.periodicidad === "mensual")
    .flatMap((g) => proyectarRecurrente(g, hoy, horizonte));
  for (const g of [...puntuales, ...recurrentes]) {
    if (g.fecha_vencimiento < hoy || g.fecha_vencimiento > horizonte) continue;
    const sem = Math.max(0, Math.min(Math.floor(dias(g.fecha_vencimiento, hoy) / 7), semanas - 1));
    egresosSemana[sem] += g.monto;
  }

  let saldo = saldoInicial;
  let totalIn = 0, totalOut = 0;
  const out: Semana[] = [];
  for (let sem = 0; sem < semanas; sem++) {
    const inicio = masDias(hoy, sem * 7);
    const fin = masDias(inicio, 6);
    saldo += ingresosSemana[sem] - egresosSemana[sem];
    totalIn += ingresosSemana[sem];
    totalOut += egresosSemana[sem];
    out.push({
      semana: sem + 1, label: `${ddmm(inicio)}-${ddmm(fin)}`,
      ingresos: ingresosSemana[sem], egresos: egresosSemana[sem],
      saldo_acumulado: saldo, riesgo: saldo < 0,
    });
  }
  return {
    saldo_inicial: saldoInicial, semanas: out,
    total_ingresos: totalIn, total_egresos: totalOut,
    fuera_horizonte: fueraHorizonte,
  };
}
```

- [ ] **Step 6: correr los tests**

Run: `deno test nube/functions/_shared/`
Expected: 4 passed.

- [ ] **Step 7: commit**

```bash
git add nube/functions/_shared/
git commit -m "Agrega helpers de edge functions: db, auth y proyeccion de flujo con tests"
```

---

### Task 7: Edge functions — endpoints de consulta

**Files:**
- Create: `nube/functions/kpis.ts`
- Create: `nube/functions/pendientes.ts`
- Create: `nube/functions/ventas.ts`
- Create: `nube/functions/flujo.ts`

**Interfaces:**
- Consumes: `db()`, `requireUser()`, `proyectarFlujo()` de Task 6; views y `sync_meta` de Task 2.
- Produces: 4 endpoints HTTP GET que la PWA (plan de Fase 3) consumirá. Formatos JSON documentados en cada función.

- [ ] **Step 1: `kpis.ts`**

```typescript
// nube/functions/kpis.ts
// GET -> { ventas_mes, por_cobrar, n_pendientes, n_vencidas, monto_vencido,
//          saldo_banco, ultimo_sync }
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";

export default async function handler(req: Request): Promise<Response> {
  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;
  const sql = db();

  const [ventasMes] = await sql`
    SELECT COALESCE(SUM(total_real), 0) AS total
    FROM v_ventas_reales
    WHERE date_trunc('month', fecha) = date_trunc('month', CURRENT_DATE)`;
  const [cobrar] = await sql`
    SELECT COALESCE(SUM(total), 0) AS total, COUNT(*) AS n,
           COUNT(*) FILTER (WHERE dias_desde_emision > 30) AS n_vencidas,
           COALESCE(SUM(total) FILTER (WHERE dias_desde_emision > 30), 0) AS monto_vencido
    FROM v_pendientes`;
  const meta = await sql`SELECT clave, valor FROM sync_meta`;
  const porClave = Object.fromEntries(meta.map((m) => [m.clave, m.valor]));

  return json({
    ventas_mes: Number(ventasMes.total),
    por_cobrar: Number(cobrar.total),
    n_pendientes: Number(cobrar.n),
    n_vencidas: Number(cobrar.n_vencidas),
    monto_vencido: Number(cobrar.monto_vencido),
    saldo_banco: porClave.saldo_banco ?? null,
    ultimo_sync: porClave.ultimo_sync ?? null,
  });
}

function json(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    headers: { "Content-Type": "application/json" },
  });
}
```

- [ ] **Step 2: `pendientes.ts`**

```typescript
// nube/functions/pendientes.ts
// GET -> { pendientes: [{folio, fecha, rut_cliente, razon_social, total,
//                        dias_desde_emision}], total }
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";

export default async function handler(req: Request): Promise<Response> {
  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;
  const sql = db();
  const filas = await sql`
    SELECT folio, fecha, rut_cliente, razon_social, total, dias_desde_emision
    FROM v_pendientes ORDER BY fecha`;
  const total = filas.reduce((s, f) => s + Number(f.total), 0);
  return new Response(JSON.stringify({ pendientes: filas, total }), {
    headers: { "Content-Type": "application/json" },
  });
}
```

- [ ] **Step 3: `ventas.ts`**

```typescript
// nube/functions/ventas.ts
// GET ?meses=N (default 6) -> { serie_mensual, ranking_clientes,
//                              ranking_productos }
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";

export default async function handler(req: Request): Promise<Response> {
  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;
  const url = new URL(req.url);
  const meses = Math.min(Math.max(Number(url.searchParams.get("meses")) || 6, 1), 24);
  const sql = db();

  const serie = await sql`
    SELECT date_trunc('month', fecha)::date AS mes,
           SUM(total_real) AS total, COUNT(*) AS n_facturas
    FROM v_ventas_reales
    WHERE fecha >= CURRENT_DATE - make_interval(months => ${meses})
    GROUP BY 1 ORDER BY 1`;
  const clientes = await sql`
    SELECT rut_cliente, razon_social_receptor AS razon_social,
           SUM(total_real) AS total
    FROM v_ventas_reales
    WHERE fecha >= CURRENT_DATE - make_interval(months => ${meses})
    GROUP BY 1, 2 ORDER BY total DESC LIMIT 10`;
  const productos = await sql`
    SELECT nombre_producto, SUM(cantidad) AS unidades
    FROM v_ventas_producto
    WHERE fecha >= CURRENT_DATE - make_interval(months => ${meses})
    GROUP BY 1 ORDER BY unidades DESC LIMIT 10`;

  return new Response(
    JSON.stringify({ serie_mensual: serie, ranking_clientes: clientes,
                     ranking_productos: productos }),
    { headers: { "Content-Type": "application/json" } },
  );
}
```

- [ ] **Step 4: `flujo.ts`**

```typescript
// nube/functions/flujo.ts
// GET -> resultado de proyectarFlujo con datos de las views + sync_meta.
import { db } from "./_shared/db.ts";
import { requireUser } from "./_shared/auth.ts";
import { proyectarFlujo, type FacturaPendiente, type Gasto } from "./_shared/flujo.ts";

export default async function handler(req: Request): Promise<Response> {
  const rechazo = await requireUser(req);
  if (rechazo) return rechazo;
  const sql = db();

  const facturas = await sql`
    SELECT folio, fecha, rut_cliente, razon_social_receptor, monto
    FROM v_flujo_pendientes ORDER BY fecha`;
  const avgs = await sql`SELECT rut_cliente, avg_dias FROM v_dias_pago_cliente`;
  const gastos = await sql`
    SELECT descripcion, proveedor, monto, fecha_vencimiento, categoria,
           recurrente, periodicidad
    FROM cuentas_por_pagar WHERE pagado = FALSE`;
  const [meta] = await sql`
    SELECT valor FROM sync_meta WHERE clave = 'saldo_banco'`;

  const avgDias = Object.fromEntries(
    avgs.map((a) => [a.rut_cliente, Number(a.avg_dias)]),
  );
  const resultado = proyectarFlujo(
    facturas.map((f): FacturaPendiente => ({
      folio: Number(f.folio), fecha: new Date(f.fecha),
      rut_cliente: f.rut_cliente,
      razon_social_receptor: f.razon_social_receptor,
      monto: Number(f.monto),
    })),
    avgDias,
    gastos.map((g): Gasto => ({
      descripcion: g.descripcion, proveedor: g.proveedor,
      monto: Number(g.monto), fecha_vencimiento: new Date(g.fecha_vencimiento),
      categoria: g.categoria, recurrente: g.recurrente ?? false,
      periodicidad: g.periodicidad,
    })),
    Number(meta?.valor?.saldo ?? 0),
    new Date(),
  );
  return new Response(JSON.stringify(resultado), {
    headers: { "Content-Type": "application/json" },
  });
}
```

- [ ] **Step 5: typecheck local**

Run: `deno check nube/functions/*.ts`
Expected: sin errores.

- [ ] **Step 6: commit**

```bash
git add nube/functions/
git commit -m "Agrega edge functions de consulta: kpis, pendientes, ventas y flujo"
```

---

### Task 8: Deploy, secrets y test de paridad de cifras

**Files:**
- Create: `scripts/test_paridad_nube.py`

**Interfaces:**
- Consumes: functions de Task 7 desplegadas; `INSFORGE_DB_URL` e `INSFORGE_JWT_SECRET` en `.env` local.
- Produces: criterio de aceptación nº 2 de la spec verificado; URL base de las functions anotada para el plan de Fase 3.

- [ ] **Step 1: configurar secrets en InsForge** — En el proyecto InsForge (panel → Functions → Secrets, o CLI según docs): `INSFORGE_DB_URL` (connection string interno del proyecto) e `INSFORGE_JWT_SECRET` (el JWT secret del proyecto, ubicación exacta según docs de Auth — consultar context7 `/insforge/insforge` query "auth JWT secret location"). Copiar también `INSFORGE_JWT_SECRET` al `.env` local (lo usa el test de paridad) y agregar la línea vacía a `.env.example`.

- [ ] **Step 2: deploy de las 4 functions** — comando de referencia (verificar verbo exacto en docs del CLI):

```powershell
npx @insforge/cli functions deploy kpis pendientes ventas flujo
```

Expected: URLs desplegadas tipo `https://<proyecto>.functions.insforge.app/<nombre>`. Anotar la URL base en `.env` como `INSFORGE_FUNCTIONS_URL`.

- [ ] **Step 3: escribir el test de paridad**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_paridad_nube.py - Zigurat ERP
Criterio de aceptacion de la spec Zigurat Movil: las cifras de la nube deben
coincidir EXACTAMENTE con la BD local al momento del ultimo sync.

Compara: por cobrar (v_pendientes vs query canonica local), ventas del mes
(v_ventas_reales vs query canonica local) y numero de pendientes.

Script de aceptacion manual (necesita red y el stack desplegado): NO es parte
de `python -m pytest -q`. Correr DESPUES de `python scripts/sync_nube.py`.

Uso:
    python scripts/sync_nube.py && python scripts/test_paridad_nube.py
    python scripts/test_paridad_nube.py --solo-token   # imprime un JWT y sale
"""
import argparse
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

from sync_nube import _load_env, conectar_local  # noqa: E402  (reutiliza patron)

_load_env()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token_jwt() -> str:
    """JWT HS256 minimo firmado con el secret del proyecto (1 hora)."""
    secreto = os.environ.get("INSFORGE_JWT_SECRET")
    if not secreto:
        raise RuntimeError("Falta INSFORGE_JWT_SECRET en el .env")
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    ahora = int(time.time())
    payload = _b64url(json.dumps(
        {"sub": "paridad-local", "iat": ahora, "exp": ahora + 3600}
    ).encode())
    firma = _b64url(hmac.new(secreto.encode(), f"{header}.{payload}".encode(),
                             hashlib.sha256).digest())
    return f"{header}.{payload}.{firma}"


def llamar_api(endpoint: str, token: str) -> dict:
    base = os.environ.get("INSFORGE_FUNCTIONS_URL")
    if not base:
        raise RuntimeError("Falta INSFORGE_FUNCTIONS_URL en el .env")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def cifras_locales() -> dict:
    """Queries canonicas del CLAUDE.md directo contra la BD local."""
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
            por_cobrar, n_pendientes = cur.fetchone()
            cur.execute("""
                SELECT COALESCE(SUM(COALESCE(monto_total_ajustado, monto_total)), 0)
                FROM ventas
                WHERE tipo_documento != '61'
                  AND date_trunc('month', fecha) = date_trunc('month', CURRENT_DATE)
            """)
            (ventas_mes,) = cur.fetchone()
    finally:
        conn.close()
    return {"por_cobrar": float(por_cobrar), "n_pendientes": int(n_pendientes),
            "ventas_mes": float(ventas_mes)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--solo-token", action="store_true")
    args = parser.parse_args()
    token = token_jwt()
    if args.solo_token:
        print(token)
        return 0

    local = cifras_locales()
    kpis = llamar_api("kpis", token)
    errores = []
    for clave in ("por_cobrar", "n_pendientes", "ventas_mes"):
        nube = float(kpis[clave])
        if abs(nube - local[clave]) > 0.005:  # igualdad exacta (tolerancia float)
            errores.append(f"{clave}: local={local[clave]:,.0f} nube={nube:,.0f}")
    if errores:
        print("PARIDAD FALLIDA:\n  " + "\n  ".join(errores))
        print("¿Corriste `python scripts/sync_nube.py` justo antes?")
        return 1
    print(f"PARIDAD OK: por_cobrar={local['por_cobrar']:,.0f}  "
          f"n_pendientes={local['n_pendientes']}  "
          f"ventas_mes={local['ventas_mes']:,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: humo manual con token**

```powershell
python scripts/test_paridad_nube.py --solo-token   # imprime un JWT firmado
curl -H "Authorization: Bearer <token>" "$env:INSFORGE_FUNCTIONS_URL/kpis"
```

Expected: JSON con `ventas_mes`, `por_cobrar`, `ultimo_sync`.

- [ ] **Step 5: correr la aceptación completa**

```powershell
python scripts/sync_nube.py
python scripts/test_paridad_nube.py
```

Expected: `Sync OK ...` y luego `PARIDAD OK: por_cobrar=... n_pendientes=... ventas_mes=...`.

- [ ] **Step 6: verificar 401 sin token**

```powershell
curl -i "$env:INSFORGE_FUNCTIONS_URL/kpis"
```

Expected: `HTTP/1.1 401`.

- [ ] **Step 7: suite completa y commit**

Run: `python -m pytest -q` → verde.

```bash
git add scripts/test_paridad_nube.py .env.example
git commit -m "Agrega test de paridad de cifras local vs nube (aceptacion Fase 2)"
```

---

## Al terminar

- El backend cumple los criterios 2 y 4 de la spec (paridad de cifras; sync < 60 s y no fatal).
- Anotar en el resumen final: URL base de functions, project ID, y cualquier desviación encontrada respecto a las docs de InsForge (firma del handler, verbos del CLI, ubicación del JWT secret).
- Siguiente plan: **Fase 3 — PWA móvil** (consume estos 4 endpoints + InsForge Auth real desde el frontend; el JWT artesanal del test de paridad se reemplaza por el login de la plataforma).
