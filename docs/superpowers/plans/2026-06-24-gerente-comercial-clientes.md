# Gerente comercial — Salud de clientes y lista de seguimiento — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el chat del dashboard razone como gerente comercial sobre crecimiento y clientes: diagnostica la salud de la cartera (4 señales priorizadas) y propone una lista de seguimiento persistente que el usuario confirma y va marcando.

**Architecture:** Capa de análisis nueva (`app/negocio/clientes.py`, solo lectura, función pura sobre cursor) + mini-CRM (`app/negocio/seguimiento.py` + tabla `seguimiento_comercial`) cuyas escrituras pasan por el mecanismo propose/confirm/execute ya probado para gastos. Dos tools de lectura nuevas y dos tools de acción nuevas; el agente nunca escribe, solo propone tarjetas.

**Tech Stack:** Python 3.x, psycopg2 (RealDictCursor), PostgreSQL local, claude-agent-sdk (servidores MCP in-process), pytest.

## Global Constraints

- Responder/comentar/commitear **en español**. Código en inglés camelCase/PascalCase, archivos kebab-case (aquí los módulos siguen el patrón existente `app/negocio/*.py`).
- Reglas SQL canónicas en toda consulta de ventas: `tipo_documento != 61`, monto real = `COALESCE(monto_total_ajustado, monto_total)`, excluir `estado = 'incobrable'`, estado de cobro por `fecha_pago`.
- **Invariante de seguridad (no negociable):** el agente NUNCA escribe en la BD. Las tools de acción solo publican `Artifact(tipo="accion")`; la escritura real la hace el endpoint `/api/ejecutar-accion`. No se cambia `permission_mode`. No se toca el endpoint ni la tarjeta genérica del frontend.
- Funciones de la capa de datos: reciben un cursor `RealDictCursor`, devuelven estructuras Python simples, testeables con cursor falso. El commit lo maneja quien llama.
- Trabajar en la rama `gerente-comercial-clientes` (ya creada). No tocar `master`.
- Cada commit termina con el trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Suite de tests: `python -m pytest -q` debe quedar en verde al final de cada tarea.
- Umbrales del análisis (constantes con nombre, valores aprobados): dormido > 60 días; caída de consumo > 40%; baja frecuencia > 1.5× la brecha histórica; nuevo sin recompra entre 21 y 60 días; prioridad alta = top 10 histórico por facturación.

---

## File Structure

| Archivo | Responsabilidad | Acción |
|---|---|---|
| `scripts/migrate_seguimiento_comercial.py` | Crea tabla `seguimiento_comercial` (idempotente). | Crear |
| `app/negocio/clientes.py` | Análisis de salud de clientes (solo lectura). | Crear |
| `app/negocio/seguimiento.py` | CRM de la lista (validar/listar/obtener/agregar/marcar). | Crear |
| `app/negocio/acciones.py` | Registra `agregar_seguimiento` y `marcar_seguimiento`. | Modificar |
| `app/agent/tools_negocio.py` | Tools de lectura `clientes_en_riesgo`, `listar_seguimiento`. | Modificar |
| `app/agent/tools_acciones.py` | Tools `proponer_agregar_seguimiento`, `proponer_marcar_seguimiento` + artifacts. | Modificar |
| `app/agent/system_prompt.py` | Bloque "gerente comercial". | Modificar |
| `tests/test_negocio_clientes.py` | Tests de `salud_clientes` (cursor falso). | Crear |
| `tests/test_negocio_seguimiento.py` | Tests del CRM (cursor falso). | Crear |
| `tests/test_negocio_acciones.py` | Cubre las 2 acciones nuevas. | Modificar |
| `tests/test_tools_negocio.py` | Cubre las 2 tools de lectura nuevas. | Modificar |
| `tests/test_tools_acciones.py` | Cubre artifacts y tools de acción nuevas. | Modificar |
| `tests/test_system_prompt.py` | Cubre el bloque del gerente comercial. | Modificar |

`app/agent/orchestrator.py` **no se modifica**: `allowed_tools` se arma desde las listas que devuelven `build_negocio_server` y `build_acciones_server`, así que las tools nuevas entran solas.

---

### Task 1: Migración de la tabla `seguimiento_comercial`

**Files:**
- Create: `scripts/migrate_seguimiento_comercial.py`

**Interfaces:**
- Consumes: nada.
- Produces: tabla `seguimiento_comercial(id, rut_cliente, motivo, prioridad, estado, senales, fecha_creacion, fecha_objetivo, fecha_contacto, notas)` en PostgreSQL.

- [ ] **Step 1: Crear el script de migración** (copia el patrón de `scripts/migrate_gastos_operativos.py`)

```python
#!/usr/bin/env python3
"""
migrate_seguimiento_comercial.py — Zigurat ERP
Crea la tabla seguimiento_comercial: mini-CRM de la lista de seguimiento
comercial que alimenta el "gerente comercial" del chat. Idempotente.
"""
import os, sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)


def _load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

SQL = """
CREATE TABLE IF NOT EXISTS seguimiento_comercial (
    id              SERIAL PRIMARY KEY,
    rut_cliente     TEXT NOT NULL,
    motivo          TEXT NOT NULL,
    prioridad       TEXT NOT NULL DEFAULT 'media',
    estado          TEXT NOT NULL DEFAULT 'pendiente',
    senales         TEXT,
    fecha_creacion  DATE NOT NULL DEFAULT CURRENT_DATE,
    fecha_objetivo  DATE,
    fecha_contacto  DATE,
    notas           TEXT
);

CREATE INDEX IF NOT EXISTS ix_seguimiento_estado
    ON seguimiento_comercial (estado);
"""


def main():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SQL)
        print("OK — tabla seguimiento_comercial lista (idempotente).")
    except psycopg2.Error as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Correr la migración**

Run: `python scripts/migrate_seguimiento_comercial.py`
Expected: `OK — tabla seguimiento_comercial lista (idempotente).`

- [ ] **Step 3: Correrla de nuevo para verificar idempotencia**

Run: `python scripts/migrate_seguimiento_comercial.py`
Expected: el mismo `OK ...` sin error (la tabla ya existe y no falla).

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_seguimiento_comercial.py
git commit -m "Agrega migracion de la tabla seguimiento_comercial"
```

---

### Task 2: Capa de análisis — `app/negocio/clientes.py`

**Files:**
- Create: `app/negocio/clientes.py`
- Test: `tests/test_negocio_clientes.py`

**Interfaces:**
- Consumes: un cursor `RealDictCursor`. La consulta interna devuelve, por cliente, las claves: `rut_cliente, razon_social, n_facturas, total_historico, ultima_venta, dias_desde_ultima, ventas_ult_60, ventas_prev_60, brecha_historica_dias, brecha_reciente_dias`.
- Produces: `salud_clientes(cur) -> list[dict]`. Cada dict: `{rut, cliente, senales: list[str], prioridad: "alta"|"media", motivo: str, dias_desde_ultima: int, ultima_venta, total_historico: float, n_facturas: int}`. Solo incluye clientes con ≥1 señal; ordenado por prioridad (alta primero) y luego `total_historico` descendente. Señales posibles: `"dormido"`, `"caida_consumo"`, `"bajo_frecuencia"`, `"nuevo_sin_recompra"`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_negocio_clientes.py
from app.negocio import clientes


class FakeCursor:
    """Cursor falso estilo RealDictCursor: ignora el SQL y devuelve las filas
    precargadas (patrón de test del proyecto)."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows


def _fila(**kw):
    """Fila base 'sana' (sin señales). Cada test sobreescribe lo que necesita."""
    base = {
        "rut_cliente": "1-1", "razon_social": "Bar X", "n_facturas": 5,
        "total_historico": 1_000_000, "ultima_venta": "2026-06-01",
        "dias_desde_ultima": 10, "ventas_ult_60": 100_000, "ventas_prev_60": 100_000,
        "brecha_historica_dias": 7, "brecha_reciente_dias": 7,
    }
    base.update(kw)
    return base


def test_cliente_sano_no_aparece():
    assert clientes.salud_clientes(FakeCursor([_fila()])) == []


def test_dormido_dispara_y_omite_las_otras_senales():
    r = clientes.salud_clientes(FakeCursor([_fila(dias_desde_ultima=90)]))
    assert r[0]["senales"] == ["dormido"]


def test_caida_consumo_sobre_umbral():
    r = clientes.salud_clientes(FakeCursor([
        _fila(ventas_prev_60=100_000, ventas_ult_60=40_000)]))  # -60%
    assert "caida_consumo" in r[0]["senales"]


def test_caida_consumo_bajo_umbral_no_dispara():
    r = clientes.salud_clientes(FakeCursor([
        _fila(ventas_prev_60=100_000, ventas_ult_60=80_000)]))  # -20%
    assert r == []


def test_bajo_frecuencia():
    r = clientes.salud_clientes(FakeCursor([
        _fila(brecha_historica_dias=7, brecha_reciente_dias=20)]))  # 20 > 1.5*7
    assert "bajo_frecuencia" in r[0]["senales"]


def test_nuevo_sin_recompra():
    r = clientes.salud_clientes(FakeCursor([
        _fila(n_facturas=1, dias_desde_ultima=30, ventas_prev_60=0,
              brecha_historica_dias=None, brecha_reciente_dias=None)]))
    assert r[0]["senales"] == ["nuevo_sin_recompra"]


def test_prioridad_alta_solo_para_top10():
    # 12 clientes dormidos con facturación creciente; solo los 10 mayores = alta
    filas = [_fila(rut_cliente=f"{i}-0", total_historico=i * 1000, dias_desde_ultima=90)
             for i in range(1, 13)]
    r = clientes.salud_clientes(FakeCursor(filas))
    assert sum(1 for c in r if c["prioridad"] == "alta") == 10


def test_motivo_no_vacio():
    r = clientes.salud_clientes(FakeCursor([_fila(dias_desde_ultima=90)]))
    assert r[0]["motivo"]
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python -m pytest tests/test_negocio_clientes.py -q`
Expected: FAIL (ModuleNotFoundError: no existe `app.negocio.clientes`).

- [ ] **Step 3: Escribir la implementación**

```python
# app/negocio/clientes.py
"""Análisis de salud de la cartera de clientes (solo lectura).

`salud_clientes(cur)` detecta y prioriza clientes con señales de alerta
comercial (dormido, caída de consumo, baja frecuencia, nuevo sin recompra).
Es el cerebro del "gerente comercial" en el área de crecimiento y clientes:
solo detecta y prioriza; NO escribe ni decide a quién contactar.

Reglas canónicas: solo facturas (tipo_documento != 61), monto real =
COALESCE(monto_total_ajustado, monto_total), excluye clientes 'incobrable'.
Patrón de app/briefing/data.py: SQL agregada -> clasificación en Python,
testeable con cursor falso.
"""

# Umbrales (constantes con nombre, ajustables)
UMBRAL_DORMIDO_DIAS = 60          # sin comprar hace más de esto = dormido
CAIDA_CONSUMO_PCT = 0.40          # caída > 40% vs la ventana previa de 60 días
FACTOR_FRECUENCIA = 1.5           # brecha reciente > 1.5x la histórica
NUEVO_SIN_RECOMPRA_MIN_DIAS = 21  # 1 sola compra hace al menos esto (y <= dormido)
TOP_N_PRIORIDAD = 10              # top N histórico por facturación = prioridad alta


def salud_clientes(cur):
    """Lista de clientes con al menos una señal de alerta, priorizada
    (alta primero, luego mayor facturación histórica)."""
    cur.execute(_SQL)
    filas = cur.fetchall()
    top_ruts = _top_ruts(filas)
    resultado = []
    for f in filas:
        senales = _senales(f)
        if not senales:
            continue
        prioridad = "alta" if f["rut_cliente"] in top_ruts else "media"
        resultado.append({
            "rut": f["rut_cliente"],
            "cliente": f["razon_social"],
            "senales": senales,
            "prioridad": prioridad,
            "motivo": _motivo(senales, f, prioridad),
            "dias_desde_ultima": int(f["dias_desde_ultima"]),
            "ultima_venta": f["ultima_venta"],
            "total_historico": float(f["total_historico"]),
            "n_facturas": int(f["n_facturas"]),
        })
    resultado.sort(key=lambda c: (0 if c["prioridad"] == "alta" else 1,
                                  -c["total_historico"]))
    return resultado


def _top_ruts(filas):
    """RUTs del top N histórico por facturación (= prioridad alta)."""
    ordenados = sorted(filas, key=lambda f: float(f["total_historico"]), reverse=True)
    return {f["rut_cliente"] for f in ordenados[:TOP_N_PRIORIDAD]}


def _senales(f):
    """Señales activas para una fila de cliente."""
    dias = int(f["dias_desde_ultima"])
    if dias > UMBRAL_DORMIDO_DIAS:
        return ["dormido"]  # dormido: ya no se evalúan caída/frecuencia
    senales = []
    if _caida_consumo(f):
        senales.append("caida_consumo")
    if _bajo_frecuencia(f):
        senales.append("bajo_frecuencia")
    if _nuevo_sin_recompra(f, dias):
        senales.append("nuevo_sin_recompra")
    return senales


def _caida_consumo(f):
    prev = f["ventas_prev_60"]
    if not prev or float(prev) <= 0:
        return False  # sin base previa no se evalúa (evita falsos positivos)
    ult = float(f["ventas_ult_60"] or 0)
    return (float(prev) - ult) / float(prev) > CAIDA_CONSUMO_PCT


def _bajo_frecuencia(f):
    bh, br = f["brecha_historica_dias"], f["brecha_reciente_dias"]
    if not bh or not br or float(bh) <= 0:
        return False
    return float(br) > FACTOR_FRECUENCIA * float(bh)


def _nuevo_sin_recompra(f, dias):
    return (int(f["n_facturas"]) == 1
            and NUEVO_SIN_RECOMPRA_MIN_DIAS <= dias <= UMBRAL_DORMIDO_DIAS)


def _motivo(senales, f, prioridad):
    partes = []
    if "dormido" in senales:
        partes.append(f"dormido {int(f['dias_desde_ultima'])}d sin comprar")
    if "caida_consumo" in senales:
        prev = float(f["ventas_prev_60"] or 0)
        ult = float(f["ventas_ult_60"] or 0)
        pct = int(round((prev - ult) / prev * 100)) if prev > 0 else 0
        partes.append(f"-{pct}% de consumo vs sus 2 meses previos")
    if "bajo_frecuencia" in senales:
        partes.append(f"compra cada ~{int(round(float(f['brecha_reciente_dias'])))}d "
                      f"(antes ~{int(round(float(f['brecha_historica_dias'])))}d)")
    if "nuevo_sin_recompra" in senales:
        partes.append(f"compró 1 vez hace {int(f['dias_desde_ultima'])}d y no volvió")
    prefijo = "Cliente top: " if prioridad == "alta" else ""
    return prefijo + "; ".join(partes)


_SQL = """
WITH base AS (
    SELECT v.rut_cliente, c.razon_social, v.fecha,
           COALESCE(v.monto_total_ajustado, v.monto_total) AS monto,
           ROW_NUMBER() OVER (PARTITION BY v.rut_cliente ORDER BY v.fecha DESC) AS rn
    FROM ventas v
    JOIN clientes c ON c.rut_cliente = v.rut_cliente
    WHERE v.tipo_documento != 61
      AND COALESCE(c.estado, '') <> 'incobrable'
      AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
)
SELECT rut_cliente, razon_social,
       COUNT(*) AS n_facturas,
       SUM(monto) AS total_historico,
       MAX(fecha) AS ultima_venta,
       (CURRENT_DATE - MAX(fecha)) AS dias_desde_ultima,
       SUM(monto) FILTER (WHERE fecha >= CURRENT_DATE - 60) AS ventas_ult_60,
       SUM(monto) FILTER (WHERE fecha >= CURRENT_DATE - 120
                            AND fecha < CURRENT_DATE - 60) AS ventas_prev_60,
       CASE WHEN COUNT(*) >= 3
            THEN (MAX(fecha) - MIN(fecha))::numeric / (COUNT(*) - 1)
            ELSE NULL END AS brecha_historica_dias,
       (MAX(fecha) - MAX(fecha) FILTER (WHERE rn = 2)) AS brecha_reciente_dias
FROM base
GROUP BY rut_cliente, razon_social
"""
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_negocio_clientes.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/negocio/clientes.py tests/test_negocio_clientes.py
git commit -m "Agrega capa de analisis de salud de clientes (4 senales priorizadas)"
```

---

### Task 3: Mini-CRM — `app/negocio/seguimiento.py`

**Files:**
- Create: `app/negocio/seguimiento.py`
- Test: `tests/test_negocio_seguimiento.py`

**Interfaces:**
- Consumes: cursor `RealDictCursor`; tabla `seguimiento_comercial` (Task 1).
- Produces:
  - `validar_agregar(params) -> {rut_cliente, motivo, prioridad, senales, fecha_objetivo, notas}` (ValueError si falta rut o motivo, o prioridad inválida).
  - `agregar(cur, rut_cliente, motivo, prioridad, senales, fecha_objetivo, notas) -> {id, mensaje}` (ValueError si el cliente ya tiene un seguimiento `pendiente`).
  - `validar_marcar(params) -> {id, estado, fecha_contacto}` (ValueError si id malo o estado ∉ {contactado, descartado}).
  - `marcar(cur, id, estado, fecha_contacto) -> {id, mensaje}` (ValueError si el id no existe).
  - `obtener(cur, id) -> dict | None` y `listar(cur, estado="pendiente") -> list[dict]` (join a `clientes` para `razon_social`).

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/test_negocio_seguimiento.py
import pytest
from datetime import date
from app.negocio import seguimiento


class FakeCursor:
    """Cursor falso estilo RealDictCursor. Captura sql/params; fetchone devuelve
    `row`, fetchall devuelve `rows`."""

    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def test_validar_agregar_normaliza_y_valida():
    r = seguimiento.validar_agregar(
        {"rut_cliente": "77-1", "motivo": "Se enfrió", "prioridad": "ALTA",
         "senales": "caida_consumo"})
    assert r["rut_cliente"] == "77-1"
    assert r["motivo"] == "Se enfrió"
    assert r["prioridad"] == "alta"
    assert r["senales"] == "caida_consumo"
    assert r["fecha_objetivo"] is None


def test_validar_agregar_rechaza_sin_rut():
    with pytest.raises(ValueError):
        seguimiento.validar_agregar({"motivo": "x"})


def test_validar_agregar_rechaza_sin_motivo():
    with pytest.raises(ValueError):
        seguimiento.validar_agregar({"rut_cliente": "77-1", "motivo": "  "})


def test_validar_agregar_rechaza_prioridad_invalida():
    with pytest.raises(ValueError):
        seguimiento.validar_agregar(
            {"rut_cliente": "77-1", "motivo": "x", "prioridad": "urgentisima"})


def test_agregar_inserta_y_devuelve_id():
    # fetchone se llama dos veces: guard de pendiente (None) y RETURNING id.
    class Cur(FakeCursor):
        def __init__(self):
            super().__init__()
            self._calls = 0

        def fetchone(self):
            self._calls += 1
            return None if self._calls == 1 else {"id": 7}

    cur = Cur()
    r = seguimiento.agregar(cur, "77-1", "Se enfrió", "alta", "caida_consumo", None, None)
    assert r["id"] == 7
    assert "seguimiento_comercial" in cur.sql
    assert "RETURNING id" in cur.sql


def test_agregar_rechaza_si_ya_hay_pendiente():
    cur = FakeCursor(row={"id": 3})  # hay un pendiente
    with pytest.raises(ValueError):
        seguimiento.agregar(cur, "77-1", "x", "media", None, None, None)


def test_validar_marcar_estado_y_fecha_por_defecto():
    r = seguimiento.validar_marcar({"id": 5, "estado": "contactado"})
    assert r["id"] == 5
    assert r["estado"] == "contactado"
    assert r["fecha_contacto"] == date.today().isoformat()


def test_validar_marcar_rechaza_estado_malo():
    with pytest.raises(ValueError):
        seguimiento.validar_marcar({"id": 5, "estado": "pendiente"})


def test_marcar_actualiza_y_devuelve_mensaje():
    cur = FakeCursor(row={"rut_cliente": "77-1", "motivo": "Se enfrió"})
    r = seguimiento.marcar(cur, 5, "contactado", "2026-06-24")
    assert "contactado" in r["mensaje"]
    assert "UPDATE" in cur.sql and "seguimiento_comercial" in cur.sql
    assert cur.params == ("contactado", "2026-06-24", 5)


def test_marcar_inexistente_lanza_valueerror():
    with pytest.raises(ValueError):
        seguimiento.marcar(FakeCursor(row=None), 999, "contactado", "2026-06-24")


def test_listar_filtra_por_estado():
    filas = [{"id": 1, "rut_cliente": "77-1", "razon_social": "Bar X",
              "motivo": "x", "prioridad": "alta", "estado": "pendiente",
              "senales": None, "fecha_creacion": "2026-06-24",
              "fecha_objetivo": None, "fecha_contacto": None}]
    cur = FakeCursor(rows=filas)
    r = seguimiento.listar(cur, estado="pendiente")
    assert len(r) == 1 and r[0]["razon_social"] == "Bar X"
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python -m pytest tests/test_negocio_seguimiento.py -q`
Expected: FAIL (ModuleNotFoundError: no existe `app.negocio.seguimiento`).

- [ ] **Step 3: Escribir la implementación**

```python
# app/negocio/seguimiento.py
"""Capa determinista del mini-CRM de seguimiento comercial.

Espejo de app/negocio/gastos.py: funciones puras (validar*) que normalizan y
validan antes de cualquier escritura, y funciones de BD (agregar/marcar) que
reciben un cursor (el commit lo maneja quien llama). Alimenta la lista de
seguimiento que el "gerente comercial" propone y el usuario confirma.
"""
from datetime import datetime, date

PRIORIDADES = {"alta", "media"}
ESTADOS_MARCAR = {"contactado", "descartado"}


def _norm_fecha_opt(f):
    """Normaliza una fecha opcional a 'YYYY-MM-DD' o None. ValueError si no parsea."""
    if not f:
        return None
    try:
        return datetime.strptime(str(f).strip(), "%Y-%m-%d").date().isoformat()
    except (ValueError, TypeError):
        raise ValueError(f"Fecha inválida: {f!r}. Formato esperado: YYYY-MM-DD.")


def _validar_id(params):
    raw = params.get("id")
    try:
        id_ = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Id de seguimiento inválido: {raw!r}.")
    if id_ <= 0:
        raise ValueError(f"Id de seguimiento inválido: {id_}.")
    return id_


def validar_agregar(params):
    """Valida y normaliza un alta de seguimiento. ValueError si falta lo obligatorio."""
    rut = (params.get("rut_cliente") or "").strip()
    if not rut:
        raise ValueError("Falta el RUT del cliente para el seguimiento.")
    motivo = (params.get("motivo") or "").strip()
    if not motivo:
        raise ValueError("El motivo del seguimiento no puede estar vacío.")
    prioridad = (params.get("prioridad") or "media").strip().lower()
    if prioridad not in PRIORIDADES:
        raise ValueError(f"Prioridad inválida: {prioridad!r}. Usa 'alta' o 'media'.")
    return {
        "rut_cliente": rut,
        "motivo": motivo,
        "prioridad": prioridad,
        "senales": (params.get("senales") or "").strip() or None,
        "fecha_objetivo": _norm_fecha_opt(params.get("fecha_objetivo")),
        "notas": (params.get("notas") or "").strip() or None,
    }


def hay_pendiente(cur, rut_cliente):
    """True si el cliente ya tiene un seguimiento en estado 'pendiente'."""
    cur.execute(
        "SELECT id FROM seguimiento_comercial "
        "WHERE rut_cliente = %s AND estado = 'pendiente' LIMIT 1",
        (rut_cliente,),
    )
    return cur.fetchone() is not None


def agregar(cur, rut_cliente, motivo, prioridad, senales, fecha_objetivo, notas):
    """Inserta un seguimiento y devuelve {id, mensaje}. Guard: no duplica
    pendientes del mismo cliente (ValueError si ya hay uno)."""
    if hay_pendiente(cur, rut_cliente):
        raise ValueError(f"El cliente {rut_cliente} ya tiene un seguimiento pendiente.")
    cur.execute(
        """
        INSERT INTO seguimiento_comercial
            (rut_cliente, motivo, prioridad, senales, fecha_objetivo, notas)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (rut_cliente, motivo, prioridad, senales, fecha_objetivo, notas),
    )
    new_id = cur.fetchone()["id"]
    return {"id": new_id, "mensaje": f"Seguimiento creado (id {new_id}): {motivo}"}


def validar_marcar(params):
    """Valida marcar: id válido + estado ∈ {contactado, descartado}; fecha por
    defecto hoy."""
    id_ = _validar_id(params)
    estado = (params.get("estado") or "").strip().lower()
    if estado not in ESTADOS_MARCAR:
        raise ValueError(f"Estado inválido: {estado!r}. Usa 'contactado' o 'descartado'.")
    fecha = params.get("fecha_contacto") or params.get("fecha")
    fecha_contacto = _norm_fecha_opt(fecha) if fecha else date.today().isoformat()
    return {"id": id_, "estado": estado, "fecha_contacto": fecha_contacto}


def marcar(cur, id, estado, fecha_contacto):
    """Marca un seguimiento como contactado/descartado. ValueError si no existe."""
    cur.execute(
        """
        UPDATE seguimiento_comercial SET estado = %s, fecha_contacto = %s
        WHERE id = %s RETURNING rut_cliente, motivo
        """,
        (estado, fecha_contacto, id),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"El seguimiento {id} ya no existe.")
    return {"id": id, "mensaje": f"Seguimiento marcado como {estado}: {row['motivo']}"}


def obtener(cur, id):
    """Devuelve el seguimiento por id (con razón social) como dict, o None."""
    cur.execute(
        """
        SELECT s.id, s.rut_cliente, c.razon_social, s.motivo, s.prioridad,
               s.estado, s.senales, s.fecha_creacion, s.fecha_objetivo,
               s.fecha_contacto, s.notas
        FROM seguimiento_comercial s
        LEFT JOIN clientes c ON c.rut_cliente = s.rut_cliente
        WHERE s.id = %s
        """,
        (id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def listar(cur, estado="pendiente"):
    """Lista seguimientos del estado dado (None = todos), alta primero."""
    cur.execute(
        """
        SELECT s.id, s.rut_cliente, c.razon_social, s.motivo, s.prioridad,
               s.estado, s.senales, s.fecha_creacion, s.fecha_objetivo,
               s.fecha_contacto
        FROM seguimiento_comercial s
        LEFT JOIN clientes c ON c.rut_cliente = s.rut_cliente
        WHERE (%s IS NULL OR s.estado = %s)
        ORDER BY CASE s.prioridad WHEN 'alta' THEN 0 ELSE 1 END, s.fecha_creacion
        """,
        (estado, estado),
    )
    return [dict(r) for r in cur.fetchall()]
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_negocio_seguimiento.py -q`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add app/negocio/seguimiento.py tests/test_negocio_seguimiento.py
git commit -m "Agrega mini-CRM de seguimiento comercial (validar/agregar/marcar/listar)"
```

---

### Task 4: Registrar acciones en `app/negocio/acciones.py`

**Files:**
- Modify: `app/negocio/acciones.py`
- Test: `tests/test_negocio_acciones.py`

**Interfaces:**
- Consumes: `seguimiento.validar_agregar/validar_marcar/agregar/marcar` (Task 3).
- Produces: en `ACCIONES`, las claves `"agregar_seguimiento"` y `"marcar_seguimiento"` enrutables vía `acciones.validar(tipo, params)` y `acciones.ejecutar(cur, tipo, clean)`.

- [ ] **Step 1: Escribir los tests que fallan** (agregar al final de `tests/test_negocio_acciones.py`)

```python
def test_validar_agregar_seguimiento_enruta():
    clean = acciones.validar("agregar_seguimiento",
                             {"rut_cliente": "77-1", "motivo": "Se enfrió"})
    assert clean["rut_cliente"] == "77-1"
    assert clean["prioridad"] == "media"


def test_ejecutar_agregar_seguimiento_incluye_id_y_mensaje():
    class Cur(FakeCursor):
        def __init__(self):
            super().__init__()
            self._calls = 0

        def fetchone(self):
            self._calls += 1
            return None if self._calls == 1 else {"id": 9}

    r = acciones.ejecutar(Cur(), "agregar_seguimiento",
                          {"rut_cliente": "77-1", "motivo": "Se enfrió",
                           "prioridad": "alta", "senales": None,
                           "fecha_objetivo": None, "notas": None})
    assert r["id"] == 9
    assert "Seguimiento creado" in r["mensaje"]


def test_validar_marcar_seguimiento_enruta():
    clean = acciones.validar("marcar_seguimiento", {"id": "5", "estado": "contactado"})
    assert clean == {"id": 5, "estado": "contactado",
                     "fecha_contacto": clean["fecha_contacto"]}


def test_ejecutar_marcar_seguimiento_devuelve_mensaje():
    cur = FakeCursor(row={"rut_cliente": "77-1", "motivo": "Se enfrió"})
    r = acciones.ejecutar(cur, "marcar_seguimiento",
                          {"id": 5, "estado": "contactado", "fecha_contacto": "2026-06-24"})
    assert "mensaje" in r
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python -m pytest tests/test_negocio_acciones.py -q`
Expected: FAIL (`ValueError: Acción desconocida: 'agregar_seguimiento'`).

- [ ] **Step 3: Escribir la implementación** (editar `app/negocio/acciones.py`)

Agregar el import junto al de gastos:

```python
from app.negocio import gastos
from app.negocio import seguimiento
```

Agregar los ejecutores (después de `_ejecutar_marcar_pagado`):

```python
def _ejecutar_agregar_seguimiento(cur, clean):
    return seguimiento.agregar(cur, **clean)


def _ejecutar_marcar_seguimiento(cur, clean):
    return seguimiento.marcar(cur, clean["id"], clean["estado"], clean["fecha_contacto"])
```

Agregar las filas al dict `ACCIONES`:

```python
ACCIONES = {
    "registrar_gasto":     (_validar_registrar, _ejecutar_registrar),
    "borrar_gasto":        (gastos.validar_borrar, _ejecutar_borrar),
    "editar_gasto":        (gastos.validar_editar, _ejecutar_editar),
    "marcar_gasto_pagado": (gastos.validar_marcar_pagado, _ejecutar_marcar_pagado),
    "agregar_seguimiento": (seguimiento.validar_agregar, _ejecutar_agregar_seguimiento),
    "marcar_seguimiento":  (seguimiento.validar_marcar, _ejecutar_marcar_seguimiento),
}
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_negocio_acciones.py -q`
Expected: PASS (los nuevos + los existentes).

- [ ] **Step 5: Commit**

```bash
git add app/negocio/acciones.py tests/test_negocio_acciones.py
git commit -m "Registra acciones agregar_seguimiento y marcar_seguimiento"
```

---

### Task 5: Tools de lectura en `app/agent/tools_negocio.py`

**Files:**
- Modify: `app/agent/tools_negocio.py`
- Test: `tests/test_tools_negocio.py`

**Interfaces:**
- Consumes: `clientes.salud_clientes` (Task 2), `seguimiento.listar` (Task 3), helpers existentes `_con_cursor`, `_texto`.
- Produces: en la lista `tool_names` de `build_negocio_server`, `"mcp__negocio__clientes_en_riesgo"` y `"mcp__negocio__listar_seguimiento"` (total 14 tools).

- [ ] **Step 1: Escribir los tests que fallan** (editar `tests/test_tools_negocio.py`)

Actualizar el conteo y agregar un test:

```python
def test_negocio_server_registra_los_tools():
    server, names = build_negocio_server()
    assert server is not None
    assert len(names) == 14
    for esperado in [
        "mcp__negocio__deuda_total",
        "mcp__negocio__deuda_cliente",
        "mcp__negocio__ranking_deudores",
        "mcp__negocio__facturas_vencidas",
        "mcp__negocio__ventas_total",
        "mcp__negocio__ranking_clientes",
        "mcp__negocio__ventas_cliente",
        "mcp__negocio__ventas_producto",
        "mcp__negocio__flujo_caja",
        "mcp__negocio__costos_sku",
        "mcp__negocio__margenes",
    ]:
        assert esperado in names


def test_tools_gerente_comercial_registradas():
    _server, names = build_negocio_server()
    assert "mcp__negocio__clientes_en_riesgo" in names
    assert "mcp__negocio__listar_seguimiento" in names
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python -m pytest tests/test_tools_negocio.py -q`
Expected: FAIL (assert 12 == 14 y falta `clientes_en_riesgo`).

- [ ] **Step 3: Escribir la implementación** (editar `app/agent/tools_negocio.py`)

Agregar los imports junto a los demás:

```python
from app.negocio import clientes as clientes_data
from app.negocio import seguimiento as seguimiento_data
```

Agregar las dos tools dentro de `build_negocio_server` (después de `listar_gastos`):

```python
    @tool("clientes_en_riesgo",
          "Clientes con señales de alerta comercial (dormido, caída de consumo, "
          "baja frecuencia, nuevo sin recompra), priorizados (los grandes primero). "
          "Úsala para diagnosticar la salud de la cartera y a quién contactar.", {})
    async def clientes_en_riesgo(args):
        r = _con_cursor(clientes_data.salud_clientes)
        if not r:
            return _texto("Ningún cliente con señales de alerta ahora mismo.")
        lineas = [f"- [{c['prioridad']}] {c['cliente']} (RUT {c['rut']}): {c['motivo']}"
                  for c in r]
        return _texto("Clientes en riesgo (priorizados):\n" + "\n".join(lineas))

    @tool("listar_seguimiento",
          "Lista la lista de seguimiento comercial con su id y estado, para ubicar "
          "uno antes de marcarlo. Opcional: estado (pendiente/contactado/descartado; "
          "por defecto pendiente).", {"estado": str})
    async def listar_seguimiento(args):
        estado = (args.get("estado") or "pendiente").strip() or "pendiente"
        r = _con_cursor(seguimiento_data.listar, estado)
        if not r:
            return _texto(f"No hay seguimientos en estado '{estado}'.")
        return _texto("\n".join(
            f"- id {s['id']} [{s['prioridad']}] "
            f"{s.get('razon_social') or s['rut_cliente']}: {s['motivo']}"
            for s in r))
```

Agregar ambas al `create_sdk_mcp_server(... tools=[...])` y a la lista `tool_names`:

```python
    server = create_sdk_mcp_server(name="negocio", version="1.0.0", tools=[
        deuda_total, deuda_cliente, ranking_deudores, facturas_vencidas,
        ventas_total, ranking_clientes, ventas_cliente, ventas_producto,
        flujo_caja, costos_sku, margenes, listar_gastos,
        clientes_en_riesgo, listar_seguimiento,
    ])
    tool_names = [
        "mcp__negocio__deuda_total", "mcp__negocio__deuda_cliente",
        "mcp__negocio__ranking_deudores", "mcp__negocio__facturas_vencidas",
        "mcp__negocio__ventas_total", "mcp__negocio__ranking_clientes",
        "mcp__negocio__ventas_cliente", "mcp__negocio__ventas_producto",
        "mcp__negocio__flujo_caja", "mcp__negocio__costos_sku", "mcp__negocio__margenes",
        "mcp__negocio__listar_gastos",
        "mcp__negocio__clientes_en_riesgo", "mcp__negocio__listar_seguimiento",
    ]
    return server, tool_names
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_tools_negocio.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agent/tools_negocio.py tests/test_tools_negocio.py
git commit -m "Agrega tools de lectura clientes_en_riesgo y listar_seguimiento"
```

---

### Task 6: Tools de acción en `app/agent/tools_acciones.py`

**Files:**
- Modify: `app/agent/tools_acciones.py`
- Test: `tests/test_tools_acciones.py`

**Interfaces:**
- Consumes: `seguimiento.validar_agregar`, `seguimiento.obtener` (Task 3); helpers existentes `Artifact`, `Collector`, `DB_URL`, `psycopg2`, `RealDictCursor`.
- Produces:
  - `agregar_seguimiento_artifact(params) -> Artifact` (tipo "accion", `tipo_accion="agregar_seguimiento"`).
  - `marcar_seguimiento_artifact(s, estado) -> Artifact` (tipo "accion", `tipo_accion="marcar_seguimiento"`).
  - Tools `mcp__acciones__proponer_agregar_seguimiento` y `mcp__acciones__proponer_marcar_seguimiento` en `build_acciones_server` (total 6 tools).

- [ ] **Step 1: Escribir los tests que fallan** (agregar al final de `tests/test_tools_acciones.py`)

```python
def test_agregar_seguimiento_artifact_arma_payload():
    params = {"rut_cliente": "77-1", "cliente": "Bar X", "motivo": "Se enfrió",
              "prioridad": "alta", "senales": "caida_consumo"}
    art = tools_acciones.agregar_seguimiento_artifact(params)
    assert art.tipo == "accion"
    assert art.payload["tipo_accion"] == "agregar_seguimiento"
    # El cliente (razón social) es solo para el resumen, no va en los params de la acción:
    assert "cliente" not in art.payload["params"]
    assert art.payload["params"]["rut_cliente"] == "77-1"
    assert art.payload["params"]["motivo"] == "Se enfrió"
    assert "Bar X" in art.payload["resumen"]


def test_marcar_seguimiento_artifact_arma_payload():
    s = {"id": 5, "rut_cliente": "77-1", "razon_social": "Bar X", "motivo": "Se enfrió"}
    art = tools_acciones.marcar_seguimiento_artifact(s, "contactado")
    assert art.payload["tipo_accion"] == "marcar_seguimiento"
    assert art.payload["params"] == {"id": 5, "estado": "contactado"}
    assert "Bar X" in art.payload["resumen"]
    assert "contactado" in art.payload["resumen"]


def test_build_acciones_server_incluye_las_de_seguimiento():
    _server, tool_names = tools_acciones.build_acciones_server(Collector())
    assert "mcp__acciones__proponer_agregar_seguimiento" in tool_names
    assert "mcp__acciones__proponer_marcar_seguimiento" in tool_names
    # No rompe las existentes:
    assert "mcp__acciones__proponer_gasto" in tool_names
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python -m pytest tests/test_tools_acciones.py -q`
Expected: FAIL (`AttributeError: module ... has no attribute 'agregar_seguimiento_artifact'`).

- [ ] **Step 3: Escribir la implementación** (editar `app/agent/tools_acciones.py`)

Agregar el import junto a los de gastos:

```python
from app.negocio import gastos
from app.negocio.gastos import validar_gasto
from app.negocio import seguimiento
```

Agregar los constructores de artefactos (después de `editar_gasto_artifact`):

```python
def agregar_seguimiento_artifact(params: dict) -> Artifact:
    """Tarjeta para agregar un cliente a la lista de seguimiento. `cliente`
    (razón social) es solo para mostrar; no viaja en los params de la acción."""
    quien = params.get("cliente") or params.get("rut_cliente")
    resumen = f"Seguimiento: {quien} · {params.get('motivo', '')} · prioridad {params.get('prioridad', 'media')}"
    accion_params = {
        "rut_cliente": params.get("rut_cliente"),
        "motivo": params.get("motivo"),
        "prioridad": params.get("prioridad", "media"),
        "senales": params.get("senales"),
    }
    return Artifact(tipo="accion", titulo="Confirmar seguimiento",
        payload={"tipo_accion": "agregar_seguimiento", "params": accion_params,
                 "resumen": resumen})


def marcar_seguimiento_artifact(s, estado) -> Artifact:
    quien = s.get("razon_social") or s["rut_cliente"]
    resumen = f"Marcar {quien} como {estado}: {s.get('motivo', '')}"
    return Artifact(tipo="accion", titulo="Confirmar seguimiento",
        payload={"tipo_accion": "marcar_seguimiento",
                 "params": {"id": s["id"], "estado": estado}, "resumen": resumen})


def _obtener_seguimiento(id):
    """Lee un seguimiento por id con su propia conexión de solo lectura."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return seguimiento.obtener(cur, id)
    finally:
        conn.close()
```

Agregar las dos tools dentro de `build_acciones_server` (después de `proponer_editar_gasto`):

```python
    @tool("proponer_agregar_seguimiento",
          "Propone agregar un cliente a la lista de seguimiento comercial, para que "
          "el usuario confirme. NO escribe: publica una tarjeta. Pasa rut_cliente, "
          "cliente (razón social, para mostrar), motivo, prioridad (alta/media) y "
          "senales (texto opcional). Úsala tras diagnosticar con clientes_en_riesgo.",
          {"rut_cliente": str, "cliente": str, "motivo": str,
           "prioridad": str, "senales": str})
    async def proponer_agregar_seguimiento(args):
        params = {"rut_cliente": args.get("rut_cliente"), "motivo": args.get("motivo"),
                  "prioridad": (args.get("prioridad") or "media"),
                  "senales": args.get("senales")}
        try:
            seguimiento.validar_agregar(params)
        except ValueError as e:
            return {"content": [{"type": "text",
                    "text": f"No puedo proponer el seguimiento: {e}"}]}
        collector.add(agregar_seguimiento_artifact({**params, "cliente": args.get("cliente")}))
        quien = args.get("cliente") or args.get("rut_cliente")
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — seguimiento de {quien}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. "
                        "NO afirmes que ya quedó en la lista."}]}

    @tool("proponer_marcar_seguimiento",
          "Propone marcar un seguimiento como 'contactado' o 'descartado' por su id, "
          "para que el usuario confirme. NO escribe: publica una tarjeta. Usa "
          "listar_seguimiento primero para ubicar el id.",
          {"id": int, "estado": str})
    async def proponer_marcar_seguimiento(args):
        s = _obtener_seguimiento(args.get("id"))
        if not s:
            return {"content": [{"type": "text",
                    "text": f"No encontré un seguimiento con id {args.get('id')}. "
                            "Usa listar_seguimiento para ver los ids."}]}
        estado = (args.get("estado") or "").strip().lower()
        if estado not in ("contactado", "descartado"):
            return {"content": [{"type": "text",
                    "text": "El estado debe ser 'contactado' o 'descartado'."}]}
        collector.add(marcar_seguimiento_artifact(s, estado))
        quien = s.get("razon_social") or s["rut_cliente"]
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — marcar {quien} como {estado}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. "
                        "NO afirmes que ya se marcó."}]}
```

Agregar ambas al `create_sdk_mcp_server(... tools=[...])` y a `tool_names`:

```python
    server = create_sdk_mcp_server(name="acciones", version="1.0.0", tools=[
        proponer_gasto, proponer_borrar_gasto, proponer_marcar_gasto_pagado,
        proponer_editar_gasto,
        proponer_agregar_seguimiento, proponer_marcar_seguimiento,
    ])
    tool_names = [
        "mcp__acciones__proponer_gasto",
        "mcp__acciones__proponer_borrar_gasto",
        "mcp__acciones__proponer_marcar_gasto_pagado",
        "mcp__acciones__proponer_editar_gasto",
        "mcp__acciones__proponer_agregar_seguimiento",
        "mcp__acciones__proponer_marcar_seguimiento",
    ]
    return server, tool_names
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `python -m pytest tests/test_tools_acciones.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agent/tools_acciones.py tests/test_tools_acciones.py
git commit -m "Agrega tools proponer_agregar_seguimiento y proponer_marcar_seguimiento"
```

---

### Task 7: Bloque "gerente comercial" en el system prompt

**Files:**
- Modify: `app/agent/system_prompt.py`
- Test: `tests/test_system_prompt.py`

**Interfaces:**
- Consumes: nada (texto).
- Produces: `SYSTEM_PROMPT` menciona `clientes_en_riesgo`, `listar_seguimiento`, `proponer_agregar_seguimiento`, `proponer_marcar_seguimiento` y la disciplina de no afirmar que quedó guardado.

- [ ] **Step 1: Escribir el test que falla** (agregar al final de `tests/test_system_prompt.py`)

```python
def test_system_prompt_incluye_gerente_comercial():
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "clientes_en_riesgo" in SYSTEM_PROMPT
    assert "listar_seguimiento" in SYSTEM_PROMPT
    assert "proponer_agregar_seguimiento" in SYSTEM_PROMPT
    assert "proponer_marcar_seguimiento" in SYSTEM_PROMPT
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_system_prompt.py -q`
Expected: FAIL (`assert "clientes_en_riesgo" in SYSTEM_PROMPT`).

- [ ] **Step 3: Escribir la implementación** (editar `app/agent/system_prompt.py`)

Agregar este bloque antes del cierre `"""` del `SYSTEM_PROMPT` (después de la sección de ACCIONES SOBRE GASTOS):

```
GERENTE COMERCIAL — CRECIMIENTO Y CLIENTES: cuando te pregunten cómo van los
clientes, a quién contactar, quién se está enfriando o quién dejó de comprar,
actúa como gerente comercial. Primero DIAGNOSTICA con mcp__negocio__clientes_en_riesgo
(nunca SQL crudo): resume priorizado y conciso quién se enfría, quién se durmió y
quién no recompró, con los clientes grandes primero. Puedes publicar una tabla en
el lienzo. LUEGO, para los casos más críticos, PROPÓN agregarlos a la lista de
seguimiento con mcp__acciones__proponer_agregar_seguimiento (rut_cliente, cliente,
motivo, prioridad, senales tomados del diagnóstico). Para ver o gestionar la lista
usa mcp__negocio__listar_seguimiento y mcp__acciones__proponer_marcar_seguimiento
(contactado/descartado) por id. Igual que con los gastos: cada acción solo deja una
TARJETA; NUNCA digas que ya quedó en la lista o que ya se marcó hasta que el usuario
confirme.
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `python -m pytest tests/test_system_prompt.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agent/system_prompt.py tests/test_system_prompt.py
git commit -m "Agrega rol de gerente comercial (salud de clientes y seguimiento) al system prompt"
```

---

### Task 8: Verificación de integración real (extremo a extremo)

**Files:**
- Ninguno (verificación manual sobre el dashboard ya construido).

**Interfaces:**
- Consumes: todo lo anterior + tabla `seguimiento_comercial` creada (Task 1).
- Produces: evidencia de que el flujo propose/confirm/execute funciona de punta a punta.

- [ ] **Step 1: Correr toda la suite**

Run: `python -m pytest -q`
Expected: PASS, sin romper ninguno de los tests existentes.

- [ ] **Step 2: Levantar el dashboard**

Run: `python app/dashboard.py`
Expected: servidor en http://localhost:8777 sin errores en consola.

- [ ] **Step 3: Diagnóstico en el chat**

En el chat escribir: `¿a quién debería contactar?`
Expected: el agente llama `clientes_en_riesgo`, responde con un diagnóstico priorizado (clientes grandes primero) y deja una o más tarjetas "Confirmar seguimiento". El texto NO afirma que ya quedaron guardados.

- [ ] **Step 4: Confirmar una tarjeta y verificar la escritura**

Apretar **Confirmar** en una tarjeta. Luego verificar en la BD:

Run: `python -c "import psycopg2; from app.config import DB_URL; from psycopg2.extras import RealDictCursor; c=psycopg2.connect(DB_URL,cursor_factory=RealDictCursor); cur=c.cursor(); cur.execute('SELECT id, rut_cliente, motivo, prioridad, estado FROM seguimiento_comercial ORDER BY id DESC LIMIT 5'); print(cur.fetchall()); c.close()"`
Expected: aparece la fila recién creada con `estado = 'pendiente'`.

- [ ] **Step 5: Marcar como contactado y verificar**

En el chat: `muéstrame la lista de seguimiento` (debe listar con id), luego
`marca como contactado el seguimiento <id>`. Apretar **Confirmar**.
Volver a correr el comando del Step 4.
Expected: la fila ahora tiene `estado = 'contactado'` y `fecha_contacto` con la fecha de hoy.

- [ ] **Step 6: Verificar el guard de duplicados**

En el chat, pedir agregar de nuevo a un cliente que ya tenga un seguimiento
`pendiente` y confirmar. Expected: el endpoint responde error (no se crea un
segundo pendiente para el mismo cliente); el agente lo reporta.

- [ ] **Step 7: Commit final (si quedaron ajustes de los pasos anteriores)**

```bash
git add -A
git commit -m "Verifica integracion extremo a extremo del gerente comercial"
```

---

## Self-Review

**1. Cobertura del spec:**
- Capa de análisis (4 señales, umbrales, prioridad top 10, salida) → Task 2. ✓
- Tabla `seguimiento_comercial` + migración idempotente → Task 1. ✓
- CRM `seguimiento.py` (validar/agregar/marcar/obtener/listar + guard duplicados) → Task 3. ✓
- Registro de acciones (`agregar_seguimiento`, `marcar_seguimiento`) → Task 4. ✓
- Tools de lectura (`clientes_en_riesgo`, `listar_seguimiento`) → Task 5. ✓
- Tools de acción (`proponer_agregar_seguimiento`, `proponer_marcar_seguimiento`) → Task 6. ✓
- System prompt gerente comercial → Task 7. ✓
- Tests por módulo + integración real → en cada task + Task 8. ✓
- Separación de responsabilidades (clientes.py vs seguimiento.py) → respetada. ✓
- Invariante de seguridad (agente nunca escribe; endpoint y tarjeta sin cambios) → respetado; orchestrator sin cambios (corrige el spec, que lo daba como modificado). ✓

**2. Placeholders:** ninguno; cada step trae el código completo.

**3. Consistencia de tipos:** `validar_agregar` produce exactamente las claves que consume `seguimiento.agregar(**clean)`; `validar_marcar` produce `{id, estado, fecha_contacto}` que consume `_ejecutar_marcar_seguimiento`; los nombres de tools coinciden entre `build_*_server` y los tests. ✓
