# Acciones de gasto desde el chat + mecanismo genérico — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir borrar, editar y marcar pagado un gasto (`cuentas_por_pagar`) desde el chat con el patrón propose/confirm/execute, generalizando la "caja de confirmación" en un registro de acciones reutilizable.

**Architecture:** Un registro `ACCIONES = {tipo_accion: (validar, ejecutar)}` en `app/negocio/acciones.py`. El agente solo lee (`listar_gastos`) y propone (publica `Artifact(tipo="accion")` con `tipo_accion`+`params`+`resumen`). La tarjeta del frontend hace `POST /api/ejecutar-accion {tipo_accion, params}` a un único endpoint determinista que valida (errores → 400) y ejecuta (errores de BD → 500). "Registrar gasto" se migra a este mismo registro.

**Tech Stack:** Python 3.x, psycopg2 (RealDictCursor), `claude_agent_sdk` (`create_sdk_mcp_server`, `tool`), `http.server`, HTML/JS vanilla. Tests con pytest + cursor falso.

## Global Constraints

- El agente NO escribe en la BD. Sus tools nuevas solo leen o publican artefactos. NO cambiar `permission_mode`.
- Toda escritura va parametrizada (`%s`); los nombres de columna en el `UPDATE` de editar provienen solo de una whitelist fija (descripcion, monto, fecha_vencimiento, proveedor, categoria).
- Borrar = `DELETE` definitivo. Marcar pagado = `pagado=TRUE, fecha_pago=<fecha>` (por defecto hoy). Editar = `UPDATE` parcial de {descripcion, monto, fecha→fecha_vencimiento, proveedor, categoria}.
- Borrar/editar/marcar sobre id inexistente → `ValueError("El gasto N ya no existe.")` → el endpoint responde **400** (nunca finge éxito).
- `get_conn()` devuelve `RealDictCursor`: leer columnas por clave (`row["id"]`), nunca por índice. Cerrar la conexión en `finally` siempre.
- Monto chileno: reusar `_normalizar_monto` ('185.000'/'185000'/número). Fechas `YYYY-MM-DD`.
- `orchestrator.py` NO se modifica: las listas de `tool_names` viven dentro de los `build_*_server` y el orquestador las concatena solo.
- TDD estricto: test que falla → implementación mínima → test verde → commit. Un commit por tarea. Comentarios/commits en español, sin trailer Co-Authored-By.
- Comando de tests: `python -m pytest <ruta> -q` (o `-v`).

---

### Task 1: Helpers de lectura de gastos (`obtener_gasto`, `listar`)

**Files:**
- Modify: `app/negocio/gastos.py` (agregar dos funciones al final)
- Test: `tests/test_negocio_gastos.py` (extender `FakeCursor` con `fetchall` + agregar tests)

**Interfaces:**
- Produces:
  - `obtener_gasto(cur, id) -> dict | None` (SELECT por id; columnas id, descripcion, monto, fecha_vencimiento, proveedor, categoria, pagado).
  - `listar(cur, filtro=None, incluir_pagados=False) -> list[dict]` (mismas columnas; ILIKE sobre descripcion; excluye pagados por defecto).
  - `FakeCursor` ahora soporta `fetchall()` y un parámetro `rows=`.

- [ ] **Step 1: Extender FakeCursor y escribir los tests que fallan**

En `tests/test_negocio_gastos.py`, reemplazar la clase `FakeCursor` existente por esta versión (compatible hacia atrás — `FakeCursor({"id":42})` sigue funcionando):

```python
class FakeCursor:
    """Cursor falso estilo RealDictCursor. Captura sql/params; fetchone devuelve
    `row`, fetchall devuelve `rows` (o [row] si solo se pasó row)."""

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
```

Agregar al final del archivo:

```python
def test_obtener_gasto_devuelve_dict():
    fila = {"id": 5, "descripcion": "Contadora", "monto": 50000,
            "fecha_vencimiento": "2026-06-30", "proveedor": None,
            "categoria": None, "pagado": False}
    cur = FakeCursor(row=fila)
    r = gastos.obtener_gasto(cur, 5)
    assert r["descripcion"] == "Contadora"
    assert cur.params == (5,)
    assert "cuentas_por_pagar" in cur.sql


def test_obtener_gasto_inexistente_devuelve_none():
    assert gastos.obtener_gasto(FakeCursor(row=None), 999) is None


def test_listar_excluye_pagados_por_defecto():
    filas = [{"id": 3, "descripcion": "Gas", "monto": 200000,
              "fecha_vencimiento": "2026-06-30", "proveedor": None,
              "categoria": None, "pagado": False}]
    cur = FakeCursor(rows=filas)
    r = gastos.listar(cur)
    assert len(r) == 1 and r[0]["descripcion"] == "Gas"
    assert "pagado = FALSE" in cur.sql


def test_listar_con_filtro_usa_ilike():
    cur = FakeCursor(rows=[])
    gastos.listar(cur, filtro="luz")
    assert "ILIKE" in cur.sql
    assert cur.params == ("%luz%",)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `python -m pytest tests/test_negocio_gastos.py::test_obtener_gasto_devuelve_dict -v`
Expected: FAIL (`AttributeError: module 'app.negocio.gastos' has no attribute 'obtener_gasto'`)

- [ ] **Step 3: Implementar los helpers en `app/negocio/gastos.py`**

Agregar al final del archivo:

```python
def obtener_gasto(cur, id):
    """Devuelve el gasto por id como dict, o None si no existe."""
    cur.execute(
        """
        SELECT id, descripcion, monto, fecha_vencimiento, proveedor, categoria, pagado
        FROM cuentas_por_pagar WHERE id = %s
        """,
        (id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def listar(cur, filtro=None, incluir_pagados=False):
    """Lista gastos ordenados por vencimiento. `filtro` hace ILIKE sobre la
    descripción; por defecto excluye los ya pagados."""
    cond = []
    params = []
    if not incluir_pagados:
        cond.append("(pagado = FALSE OR pagado IS NULL)")
    if filtro:
        cond.append("descripcion ILIKE %s")
        params.append(f"%{filtro}%")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    cur.execute(
        f"""
        SELECT id, descripcion, monto, fecha_vencimiento, proveedor, categoria, pagado
        FROM cuentas_por_pagar
        {where}
        ORDER BY fecha_vencimiento ASC NULLS LAST, id
        """,
        tuple(params),
    )
    return [dict(r) for r in cur.fetchall()]
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_negocio_gastos.py -v`
Expected: PASS (los nuevos + los existentes)

- [ ] **Step 5: Commit**

```bash
git add app/negocio/gastos.py tests/test_negocio_gastos.py
git commit -m "Agrega helpers de lectura de gastos (obtener_gasto, listar)"
```

---

### Task 2: Borrar gasto (`_validar_id`, `validar_borrar`, `borrar_gasto`)

**Files:**
- Modify: `app/negocio/gastos.py`
- Test: `tests/test_negocio_gastos.py`

**Interfaces:**
- Produces:
  - `_validar_id(params) -> int` (id entero > 0, si no `ValueError`). **Reutilizado por Tasks 3 y 4.**
  - `validar_borrar(params) -> {"id": int}`.
  - `borrar_gasto(cur, id) -> {"id", "descripcion", "mensaje"}` (DELETE RETURNING; si no existe `ValueError`).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_negocio_gastos.py`:

```python
def test_validar_borrar_acepta_id_string_o_int():
    assert gastos.validar_borrar({"id": "5"}) == {"id": 5}
    assert gastos.validar_borrar({"id": 5}) == {"id": 5}


def test_validar_borrar_rechaza_id_malo():
    import pytest
    for malo in ({"id": "abc"}, {"id": 0}, {"id": -2}, {}):
        with pytest.raises(ValueError):
            gastos.validar_borrar(malo)


def test_borrar_gasto_devuelve_mensaje_y_borra():
    cur = FakeCursor(row={"descripcion": "Contadora"})
    r = gastos.borrar_gasto(cur, 5)
    assert r == {"id": 5, "descripcion": "Contadora", "mensaje": "Gasto borrado: Contadora"}
    assert "DELETE" in cur.sql and "cuentas_por_pagar" in cur.sql
    assert cur.params == (5,)


def test_borrar_gasto_inexistente_lanza_valueerror():
    import pytest
    with pytest.raises(ValueError):
        gastos.borrar_gasto(FakeCursor(row=None), 999)
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_negocio_gastos.py::test_validar_borrar_acepta_id_string_o_int -v`
Expected: FAIL (`has no attribute 'validar_borrar'`)

- [ ] **Step 3: Implementar en `app/negocio/gastos.py`**

Agregar al final:

```python
def _validar_id(params):
    """Extrae y valida un id de gasto (entero > 0). Lanza ValueError si no sirve."""
    raw = params.get("id")
    try:
        id_ = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Id de gasto inválido: {raw!r}.")
    if id_ <= 0:
        raise ValueError(f"Id de gasto inválido: {id_}.")
    return id_


def validar_borrar(params):
    """Valida los params de borrar: requiere un id entero > 0."""
    return {"id": _validar_id(params)}


def borrar_gasto(cur, id):
    """Borra el gasto por id. Devuelve {id, descripcion, mensaje}.
    Lanza ValueError si el gasto no existe."""
    cur.execute(
        "DELETE FROM cuentas_por_pagar WHERE id = %s RETURNING descripcion",
        (id,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"El gasto {id} ya no existe.")
    desc = row["descripcion"]
    return {"id": id, "descripcion": desc, "mensaje": f"Gasto borrado: {desc}"}
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_negocio_gastos.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/negocio/gastos.py tests/test_negocio_gastos.py
git commit -m "Agrega borrar gasto (validar_borrar, borrar_gasto)"
```

---

### Task 3: Marcar gasto pagado (`validar_marcar_pagado`, `marcar_gasto_pagado`)

**Files:**
- Modify: `app/negocio/gastos.py` (incluye cambiar el import de datetime)
- Test: `tests/test_negocio_gastos.py`

**Interfaces:**
- Consumes: `_validar_id` (Task 2).
- Produces:
  - `validar_marcar_pagado(params) -> {"id": int, "fecha_pago": str(YYYY-MM-DD)}` (fecha por defecto hoy).
  - `marcar_gasto_pagado(cur, id, fecha_pago) -> {"id", "descripcion", "mensaje"}`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_negocio_gastos.py`:

```python
def test_validar_marcar_pagado_fecha_por_defecto_hoy():
    from datetime import date
    r = gastos.validar_marcar_pagado({"id": 3})
    assert r["id"] == 3
    assert r["fecha_pago"] == date.today().isoformat()


def test_validar_marcar_pagado_con_fecha_explicita():
    r = gastos.validar_marcar_pagado({"id": 3, "fecha_pago": "2026-06-01"})
    assert r == {"id": 3, "fecha_pago": "2026-06-01"}


def test_validar_marcar_pagado_rechaza_fecha_mala():
    import pytest
    with pytest.raises(ValueError):
        gastos.validar_marcar_pagado({"id": 3, "fecha_pago": "01/06/2026"})


def test_marcar_gasto_pagado_actualiza_y_devuelve_mensaje():
    cur = FakeCursor(row={"descripcion": "Contadora"})
    r = gastos.marcar_gasto_pagado(cur, 5, "2026-06-01")
    assert r["mensaje"] == "Gasto marcado como pagado: Contadora"
    assert "UPDATE" in cur.sql and "pagado = TRUE" in cur.sql
    assert cur.params == ("2026-06-01", 5)


def test_marcar_gasto_pagado_inexistente_lanza_valueerror():
    import pytest
    with pytest.raises(ValueError):
        gastos.marcar_gasto_pagado(FakeCursor(row=None), 999, "2026-06-01")
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_negocio_gastos.py::test_validar_marcar_pagado_fecha_por_defecto_hoy -v`
Expected: FAIL (`has no attribute 'validar_marcar_pagado'`)

- [ ] **Step 3: Implementar en `app/negocio/gastos.py`**

Primero, cambiar el import del tope del archivo de:

```python
from datetime import datetime
```

a:

```python
from datetime import datetime, date
```

Luego agregar al final:

```python
def validar_marcar_pagado(params):
    """Valida marcar-pagado: id válido; fecha_pago por defecto hoy, si viene
    debe ser YYYY-MM-DD."""
    id_ = _validar_id(params)
    fecha = params.get("fecha_pago") or params.get("fecha")
    if not fecha:
        fecha_pago = date.today().isoformat()
    else:
        try:
            fecha_pago = datetime.strptime(str(fecha).strip(), "%Y-%m-%d").date().isoformat()
        except (ValueError, TypeError):
            raise ValueError(f"Fecha inválida: {fecha!r}. Formato esperado: YYYY-MM-DD.")
    return {"id": id_, "fecha_pago": fecha_pago}


def marcar_gasto_pagado(cur, id, fecha_pago):
    """Marca el gasto como pagado en la fecha dada. Lanza ValueError si no existe."""
    cur.execute(
        """
        UPDATE cuentas_por_pagar SET pagado = TRUE, fecha_pago = %s
        WHERE id = %s RETURNING descripcion
        """,
        (fecha_pago, id),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"El gasto {id} ya no existe.")
    desc = row["descripcion"]
    return {"id": id, "descripcion": desc, "mensaje": f"Gasto marcado como pagado: {desc}"}
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_negocio_gastos.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/negocio/gastos.py tests/test_negocio_gastos.py
git commit -m "Agrega marcar gasto pagado (validar + ejecutar)"
```

---

### Task 4: Editar gasto (`validar_editar`, `editar_gasto`)

**Files:**
- Modify: `app/negocio/gastos.py`
- Test: `tests/test_negocio_gastos.py`

**Interfaces:**
- Consumes: `_validar_id` (Task 2), `_normalizar_monto` (ya existe).
- Produces:
  - `validar_editar(params) -> {"id": int, "cambios": dict}` (cambios mapea columnas reales: `fecha`→`fecha_vencimiento`; al menos un campo; si no, `ValueError`).
  - `editar_gasto(cur, id, cambios) -> {"id", "descripcion", "mensaje"}` (UPDATE dinámico parametrizado; si no existe `ValueError`).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_negocio_gastos.py`:

```python
def test_validar_editar_normaliza_monto():
    r = gastos.validar_editar({"id": 4, "monto": "180.000"})
    assert r == {"id": 4, "cambios": {"monto": 180000.0}}


def test_validar_editar_mapea_fecha_a_vencimiento():
    r = gastos.validar_editar({"id": 4, "fecha": "2026-07-01"})
    assert r["cambios"] == {"fecha_vencimiento": "2026-07-01"}


def test_validar_editar_sin_campos_lanza_valueerror():
    import pytest
    with pytest.raises(ValueError):
        gastos.validar_editar({"id": 4})


def test_validar_editar_rechaza_monto_malo():
    import pytest
    with pytest.raises(ValueError):
        gastos.validar_editar({"id": 4, "monto": "abc"})


def test_editar_gasto_arma_update_parametrizado():
    cur = FakeCursor(row={"descripcion": "Gas"})
    r = gastos.editar_gasto(cur, 4, {"monto": 180000.0})
    assert r["mensaje"] == "Gasto actualizado: Gas"
    assert "UPDATE cuentas_por_pagar SET monto = %s" in cur.sql
    assert cur.params == (180000.0, 4)


def test_editar_gasto_inexistente_lanza_valueerror():
    import pytest
    with pytest.raises(ValueError):
        gastos.editar_gasto(FakeCursor(row=None), 999, {"monto": 1.0})
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_negocio_gastos.py::test_validar_editar_normaliza_monto -v`
Expected: FAIL (`has no attribute 'validar_editar'`)

- [ ] **Step 3: Implementar en `app/negocio/gastos.py`**

Agregar al final:

```python
def validar_editar(params):
    """Valida editar: id válido + al menos un campo. Normaliza monto y fecha.
    Devuelve {id, cambios:{columna_real: valor}}."""
    id_ = _validar_id(params)
    cambios = {}
    if params.get("descripcion") is not None:
        desc = str(params["descripcion"]).strip()
        if not desc:
            raise ValueError("La descripción no puede quedar vacía.")
        cambios["descripcion"] = desc
    if params.get("monto") is not None:
        m = _normalizar_monto(params["monto"])
        if m is None or m <= 0:
            raise ValueError(f"Monto inválido: {params['monto']!r}. Debe ser mayor que 0.")
        cambios["monto"] = m
    if params.get("fecha") is not None:
        try:
            cambios["fecha_vencimiento"] = datetime.strptime(
                str(params["fecha"]).strip(), "%Y-%m-%d").date().isoformat()
        except (ValueError, TypeError):
            raise ValueError(f"Fecha inválida: {params['fecha']!r}. Formato esperado: YYYY-MM-DD.")
    if params.get("proveedor") is not None:
        cambios["proveedor"] = str(params["proveedor"]).strip() or None
    if params.get("categoria") is not None:
        cambios["categoria"] = str(params["categoria"]).strip() or None

    if not cambios:
        raise ValueError("No indicaste ningún campo para cambiar.")
    return {"id": id_, "cambios": cambios}


def editar_gasto(cur, id, cambios):
    """Aplica los cambios al gasto (UPDATE parcial parametrizado). Las claves de
    `cambios` son siempre columnas de la whitelist (las produce validar_editar).
    Lanza ValueError si no hay cambios o el gasto no existe."""
    if not cambios:
        raise ValueError("No hay cambios que aplicar.")
    set_sql = ", ".join(f"{col} = %s" for col in cambios)
    valores = list(cambios.values()) + [id]
    cur.execute(
        f"UPDATE cuentas_por_pagar SET {set_sql} WHERE id = %s RETURNING descripcion",
        tuple(valores),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"El gasto {id} ya no existe.")
    return {"id": id, "descripcion": row["descripcion"],
            "mensaje": f"Gasto actualizado: {row['descripcion']}"}
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_negocio_gastos.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/negocio/gastos.py tests/test_negocio_gastos.py
git commit -m "Agrega editar gasto (validar_editar, editar_gasto)"
```

---

### Task 5: Registro de acciones (`app/negocio/acciones.py`)

**Files:**
- Create: `app/negocio/acciones.py`
- Test: `tests/test_negocio_acciones.py`

**Interfaces:**
- Consumes: `gastos.validar_gasto`, `registrar_gasto`, `validar_borrar`, `borrar_gasto`, `validar_editar`, `editar_gasto`, `validar_marcar_pagado`, `marcar_gasto_pagado` (Tasks 1–4 + existentes).
- Produces:
  - `validar(tipo_accion, params) -> dict` (ValueError si tipo desconocido o params inválidos).
  - `ejecutar(cur, tipo_accion, clean) -> dict` (resultado uniforme `{mensaje, id?, ...}`).
  - `ACCIONES` dict.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_negocio_acciones.py`:

```python
# tests/test_negocio_acciones.py
import pytest
from app.negocio import acciones


class FakeCursor:
    def __init__(self, row=None):
        self._row = row
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row


def test_validar_tipo_desconocido_lanza_valueerror():
    with pytest.raises(ValueError):
        acciones.validar("inventado", {})


def test_ejecutar_tipo_desconocido_lanza_valueerror():
    with pytest.raises(ValueError):
        acciones.ejecutar(FakeCursor(), "inventado", {})


def test_validar_registrar_enruta_a_validar_gasto():
    clean = acciones.validar("registrar_gasto",
                             {"descripcion": "Luz", "monto": "185000", "fecha": "2026-06-30"})
    assert clean["descripcion"] == "Luz"
    assert clean["monto"] == 185000.0


def test_validar_borrar_enruta():
    assert acciones.validar("borrar_gasto", {"id": "5"}) == {"id": 5}


def test_ejecutar_borrar_devuelve_resultado_uniforme():
    cur = FakeCursor(row={"descripcion": "Contadora"})
    r = acciones.ejecutar(cur, "borrar_gasto", {"id": 5})
    assert r["mensaje"] == "Gasto borrado: Contadora"


def test_ejecutar_registrar_incluye_id_y_mensaje():
    cur = FakeCursor(row={"id": 42})
    r = acciones.ejecutar(cur, "registrar_gasto",
                          {"descripcion": "Luz", "monto": 185000.0, "fecha": "2026-06-30",
                           "proveedor": None, "categoria": None})
    assert r["id"] == 42
    assert "Gasto registrado" in r["mensaje"]
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_negocio_acciones.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.negocio.acciones'`)

- [ ] **Step 3: Implementar `app/negocio/acciones.py`**

```python
"""Registro de acciones de escritura confirmadas (propose/confirm/execute).

Cada acción es un par (validar, ejecutar) de interfaz uniforme:
- validar(params: dict) -> dict limpio (lanza ValueError si algo está mal)
- ejecutar(cur, clean: dict) -> dict resultado {mensaje, id?}

El endpoint determinista usa `validar` (sin BD → 400) y `ejecutar` (con BD →
500/400). El agente nunca escribe: solo propone artefactos `accion`.
"""
from app.negocio import gastos


def _validar_registrar(params):
    return gastos.validar_gasto(
        params.get("descripcion"), params.get("monto"), params.get("fecha"),
        params.get("proveedor"), params.get("categoria"))


def _ejecutar_registrar(cur, clean):
    new_id = gastos.registrar_gasto(cur, **clean)
    monto_fmt = "$" + f"{int(round(float(clean['monto']))):,}".replace(",", ".")
    return {"id": new_id,
            "mensaje": f"Gasto registrado (id {new_id}): {clean['descripcion']} · {monto_fmt}"}


def _ejecutar_borrar(cur, clean):
    return gastos.borrar_gasto(cur, clean["id"])


def _ejecutar_editar(cur, clean):
    return gastos.editar_gasto(cur, clean["id"], clean["cambios"])


def _ejecutar_marcar_pagado(cur, clean):
    return gastos.marcar_gasto_pagado(cur, clean["id"], clean["fecha_pago"])


ACCIONES = {
    "registrar_gasto":     (_validar_registrar, _ejecutar_registrar),
    "borrar_gasto":        (gastos.validar_borrar, _ejecutar_borrar),
    "editar_gasto":        (gastos.validar_editar, _ejecutar_editar),
    "marcar_gasto_pagado": (gastos.validar_marcar_pagado, _ejecutar_marcar_pagado),
}


def validar(tipo_accion, params):
    """Valida los params de una acción. Lanza ValueError si el tipo es
    desconocido o los params no sirven."""
    if tipo_accion not in ACCIONES:
        raise ValueError(f"Acción desconocida: {tipo_accion!r}")
    return ACCIONES[tipo_accion][0](params or {})


def ejecutar(cur, tipo_accion, clean):
    """Ejecuta una acción ya validada. Lanza ValueError si el tipo es desconocido."""
    if tipo_accion not in ACCIONES:
        raise ValueError(f"Acción desconocida: {tipo_accion!r}")
    return ACCIONES[tipo_accion][1](cur, clean)
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_negocio_acciones.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/negocio/acciones.py tests/test_negocio_acciones.py
git commit -m "Agrega registro generico de acciones (validar/ejecutar)"
```

---

### Task 6: Endpoint genérico `POST /api/ejecutar-accion`

**Files:**
- Modify: `app/dashboard.py:1060-1096` (reemplazar el branch `/api/registrar-gasto` en `Handler.do_POST`)

**Interfaces:**
- Consumes: `app.negocio.acciones.validar`, `acciones.ejecutar` (Task 5); `get_conn()` (existe).

- [ ] **Step 1: Reemplazar el branch del endpoint**

En `app/dashboard.py`, dentro de `Handler.do_POST`, reemplazar el bloque actual
`elif path == "/api/registrar-gasto": ... return` (líneas ~1060–1096, hasta justo
antes del `else:` final) por:

```python
        elif path == "/api/ejecutar-accion":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except Exception:
                self._send(400, json.dumps({"ok": False, "error": "JSON inválido"}))
                return
            try:
                from app.negocio import acciones
            except Exception as e:  # pragma: no cover
                self._send(500, json.dumps({"ok": False, "error": "módulo de acciones no disponible",
                                            "detalle": str(e)}))
                return
            tipo_accion = body.get("tipo_accion")
            params = body.get("params") or {}
            try:
                clean = acciones.validar(tipo_accion, params)
            except ValueError as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
                return
            try:
                conn = get_conn()
                try:
                    with conn:
                        with conn.cursor() as cur:
                            result = acciones.ejecutar(cur, tipo_accion, clean)
                finally:
                    conn.close()
            except ValueError as e:
                # p. ej. "El gasto N ya no existe" durante la ejecución
                self._send(400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
                return
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "error": "error al escribir en la base",
                                            "detalle": str(e)}, ensure_ascii=False))
                return
            self._send(200, json.dumps({"ok": True, **result},
                                       default=_json_default, ensure_ascii=False))
```

- [ ] **Step 2: Verificar el endpoint en vivo (in-process, sin dejar servidor abierto)**

Escribir un script temporal que levante `ThreadingHTTPServer(("127.0.0.1", 8799), app.dashboard.Handler)` en un hilo daemon y, vía `urllib.request`, pruebe:
- `POST /api/ejecutar-accion {"tipo_accion":"registrar_gasto","params":{"descripcion":"PRUEBA t6","monto":"1.234","fecha":"2026-12-31"}}` → 200, `ok=true`, con `id` y `mensaje`. **Guardar ese id.**
- `POST .../ejecutar-accion {"tipo_accion":"borrar_gasto","params":{"id":<ese id>}}` → 200, `ok=true`, `mensaje` "Gasto borrado: PRUEBA t6".
- `POST .../ejecutar-accion {"tipo_accion":"borrar_gasto","params":{"id":999999}}` → 400, `ok=false` ("ya no existe").
- `POST .../ejecutar-accion {"tipo_accion":"inventado","params":{}}` → 400.
Cerrar el servidor (`server.shutdown()`) y borrar el script. (El registrar+borrar deja la BD limpia; igual confirmar con un SELECT que no quede "PRUEBA t6".)

Expected: 200/200/400/400 y sin filas "PRUEBA t6" remanentes.

- [ ] **Step 3: Correr la suite completa (regresión)**

Run: `python -m pytest -q`
Expected: PASS (sin el endpoint viejo; nada más lo usaba).

- [ ] **Step 4: Commit**

```bash
git add app/dashboard.py
git commit -m "Reemplaza el endpoint de gasto por /api/ejecutar-accion generico"
```

---

### Task 7: Tarjeta genérica en el frontend

**Files:**
- Modify: `app/dashboard_ui.html:733` (la rama `a.tipo==='accion'` de `renderArtefactos`)

**Interfaces:**
- Consumes: endpoint `/api/ejecutar-accion` (Task 6). En `renderArtefactos`, `p` es `a.payload` y trae `p.tipo_accion` y `p.params`.

- [ ] **Step 1: Cambiar el destino del fetch**

En `app/dashboard_ui.html`, dentro de la rama `a.tipo==='accion'`, reemplazar la línea:

```javascript
          const r=await fetch('/api/registrar-gasto',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(params)});
```

por:

```javascript
          const r=await fetch('/api/ejecutar-accion',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tipo_accion:p.tipo_accion,params})});
```

(No tocar nada más de la tarjeta: el resumen, los botones y el manejo de éxito/error se quedan igual.)

- [ ] **Step 2: Verificar que la página sigue sirviendo y contiene el nuevo destino**

Levantar `ThreadingHTTPServer(("127.0.0.1", 8798), app.dashboard.Handler)` en un hilo daemon, hacer GET `http://127.0.0.1:8798/`, y verificar HTTP 200 y que el body contenga `/api/ejecutar-accion` y NO contenga `/api/registrar-gasto`. Cerrar el servidor. Borrar el script temporal.

Expected: 200, contiene `/api/ejecutar-accion`, ya no contiene `/api/registrar-gasto`.

- [ ] **Step 3: Correr la suite (regresión Python)**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/dashboard_ui.html
git commit -m "La tarjeta de accion ahora usa el endpoint generico"
```

---

### Task 8: Herramienta de lectura `listar_gastos`

**Files:**
- Modify: `app/agent/tools_negocio.py` (import + nueva `@tool` + tool_names)
- Test: `tests/test_tools_negocio.py`

**Interfaces:**
- Consumes: `gastos.listar` (Task 1).
- Produces: tool MCP `mcp__negocio__listar_gastos` (param opcional `{filtro: str}`).

- [ ] **Step 1: Escribir el test que falla**

Agregar a `tests/test_tools_negocio.py`:

```python
def test_listar_gastos_registrado_en_tools():
    from app.agent.tools_negocio import build_negocio_server
    _server, tool_names = build_negocio_server()
    assert "mcp__negocio__listar_gastos" in tool_names
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_tools_negocio.py::test_listar_gastos_registrado_en_tools -v`
Expected: FAIL (`mcp__negocio__listar_gastos` no está)

- [ ] **Step 3: Implementar en `app/agent/tools_negocio.py`**

En los imports del tope, agregar junto a los otros `from app.negocio import ...`:

```python
from app.negocio import gastos as gastos_data
```

Dentro de `build_negocio_server`, agregar esta tool junto a las demás (antes del `server = create_sdk_mcp_server(...)`):

```python
    @tool("listar_gastos", "Lista los gastos pendientes (cuentas por pagar) con su id, "
                           "para ubicar uno antes de borrarlo, editarlo o marcarlo pagado. "
                           "Opcional: filtro de texto sobre la descripción.",
          {"filtro": str})
    async def listar_gastos(args):
        r = _con_cursor(gastos_data.listar, args.get("filtro"))
        if not r:
            suf = f" que coincidan con '{args['filtro']}'." if args.get("filtro") else "."
            return _texto("No hay gastos pendientes" + suf)
        return _texto("\n".join(
            f"- id {g['id']}: {g['descripcion']} · {_pesos(g['monto'])} · vence {g['fecha_vencimiento']}"
            + (f" · {g['proveedor']}" if g.get("proveedor") else "")
            for g in r))
```

En la llamada `create_sdk_mcp_server(name="negocio", ..., tools=[ ... ])`, agregar `listar_gastos` al final de la lista `tools=[...]`. En la lista `tool_names = [...]`, agregar al final:

```python
        "mcp__negocio__listar_gastos",
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_tools_negocio.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/tools_negocio.py tests/test_tools_negocio.py
git commit -m "Agrega herramienta de lectura listar_gastos"
```

---

### Task 9: Herramientas de propuesta (borrar / editar / marcar pagado)

**Files:**
- Modify: `app/agent/tools_acciones.py` (imports, 3 builders puros, 3 `@tool`, helper `_obtener_gasto`, tool_names)
- Test: `tests/test_tools_acciones.py`

**Interfaces:**
- Consumes: `gastos.obtener_gasto`, `gastos.validar_editar` (Tasks 1, 4); `Artifact` (existe); `_pesos`, `_fecha_dmy` (existen en el archivo); `DB_URL`.
- Produces: builders `borrar_gasto_artifact(g)`, `marcar_pagado_artifact(g, fecha_pago)`, `editar_gasto_artifact(g, params, cambios)`; tools `mcp__acciones__proponer_borrar_gasto`, `mcp__acciones__proponer_editar_gasto`, `mcp__acciones__proponer_marcar_gasto_pagado`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_tools_acciones.py`:

```python
def test_borrar_gasto_artifact():
    g = {"id": 5, "descripcion": "Contadora", "monto": 50000, "fecha_vencimiento": "2026-06-30"}
    art = tools_acciones.borrar_gasto_artifact(g)
    assert art.tipo == "accion"
    assert art.payload["tipo_accion"] == "borrar_gasto"
    assert art.payload["params"] == {"id": 5}
    assert "Contadora" in art.payload["resumen"]
    assert "50.000" in art.payload["resumen"]


def test_marcar_pagado_artifact():
    g = {"id": 5, "descripcion": "Contadora", "monto": 50000, "fecha_vencimiento": "2026-06-30"}
    art = tools_acciones.marcar_pagado_artifact(g, "2026-06-22")
    assert art.payload["tipo_accion"] == "marcar_gasto_pagado"
    assert art.payload["params"] == {"id": 5, "fecha_pago": "2026-06-22"}
    assert "22/06/2026" in art.payload["resumen"]


def test_editar_gasto_artifact_muestra_antes_despues():
    g = {"id": 4, "descripcion": "Gas", "monto": 200000, "fecha_vencimiento": "2026-06-30"}
    art = tools_acciones.editar_gasto_artifact(g, {"id": 4, "monto": "180000"}, {"monto": 180000.0})
    assert art.payload["tipo_accion"] == "editar_gasto"
    assert art.payload["params"] == {"id": 4, "monto": "180000"}
    assert "200.000" in art.payload["resumen"] and "180.000" in art.payload["resumen"]


def test_build_acciones_server_incluye_las_tres_nuevas():
    server, tool_names = tools_acciones.build_acciones_server(Collector())
    for n in ("mcp__acciones__proponer_borrar_gasto",
              "mcp__acciones__proponer_editar_gasto",
              "mcp__acciones__proponer_marcar_gasto_pagado"):
        assert n in tool_names
    # No rompe la existente:
    assert "mcp__acciones__proponer_gasto" in tool_names
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_tools_acciones.py::test_borrar_gasto_artifact -v`
Expected: FAIL (`has no attribute 'borrar_gasto_artifact'`)

- [ ] **Step 3: Implementar en `app/agent/tools_acciones.py`**

En los imports del tope, agregar:

```python
import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import DB_URL
from app.negocio import gastos
```

(La línea existente `from app.negocio.gastos import validar_gasto` se mantiene.)

Agregar, después de `accion_gasto_artifact` (y antes de `build_acciones_server`), los builders puros y el helper de lectura:

```python
def _obtener_gasto(id):
    """Lee un gasto por id con su propia conexión de solo lectura."""
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            return gastos.obtener_gasto(cur, id)
    finally:
        conn.close()


def borrar_gasto_artifact(g) -> Artifact:
    resumen = (f"Borrar: {g['descripcion']} · {_pesos(g['monto'])} · "
               f"vence {_fecha_dmy(g['fecha_vencimiento'])}")
    return Artifact(tipo="accion", titulo="Confirmar borrado",
        payload={"tipo_accion": "borrar_gasto", "params": {"id": g["id"]}, "resumen": resumen})


def marcar_pagado_artifact(g, fecha_pago) -> Artifact:
    resumen = (f"Marcar pagado: {g['descripcion']} · {_pesos(g['monto'])} · "
               f"el {_fecha_dmy(fecha_pago)}")
    return Artifact(tipo="accion", titulo="Confirmar pago de gasto",
        payload={"tipo_accion": "marcar_gasto_pagado",
                 "params": {"id": g["id"], "fecha_pago": fecha_pago}, "resumen": resumen})


def editar_gasto_artifact(g, params, cambios) -> Artifact:
    partes = []
    for col, nuevo in cambios.items():
        viejo = g.get(col)
        if col == "monto":
            partes.append(f"monto {_pesos(viejo)} → {_pesos(nuevo)}")
        elif col == "fecha_vencimiento":
            partes.append(f"vence {_fecha_dmy(viejo)} → {_fecha_dmy(nuevo)}")
        else:
            partes.append(f"{col}: {viejo} → {nuevo}")
    resumen = f"Editar {g['descripcion']}: " + ", ".join(partes)
    return Artifact(tipo="accion", titulo="Confirmar edición",
        payload={"tipo_accion": "editar_gasto", "params": params, "resumen": resumen})
```

Dentro de `build_acciones_server`, agregar estas tres tools junto a `proponer_gasto`:

```python
    @tool("proponer_borrar_gasto",
          "Propone BORRAR un gasto (cuenta por pagar) por su id, para que el usuario "
          "confirme. NO borra: publica una tarjeta. Usa listar_gastos primero para el id.",
          {"id": int})
    async def proponer_borrar_gasto(args):
        g = _obtener_gasto(args.get("id"))
        if not g:
            return {"content": [{"type": "text",
                    "text": f"No encontré un gasto con id {args.get('id')}. Usa listar_gastos para ver los ids."}]}
        collector.add(borrar_gasto_artifact(g))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Borrar {g['descripcion']}. Quedó como tarjeta; "
                        "el usuario debe apretar Confirmar. NO afirmes que ya se borró."}]}

    @tool("proponer_marcar_gasto_pagado",
          "Propone marcar un gasto como PAGADO por su id (fecha opcional, por defecto hoy). "
          "NO escribe: publica una tarjeta. Usa listar_gastos primero.",
          {"id": int, "fecha": str})
    async def proponer_marcar_gasto_pagado(args):
        g = _obtener_gasto(args.get("id"))
        if not g:
            return {"content": [{"type": "text",
                    "text": f"No encontré un gasto con id {args.get('id')}. Usa listar_gastos para ver los ids."}]}
        from datetime import date
        fecha = (args.get("fecha") or "").strip() or date.today().isoformat()
        collector.add(marcar_pagado_artifact(g, fecha))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Marcar pagado {g['descripcion']}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. NO afirmes que ya se pagó."}]}

    @tool("proponer_editar_gasto",
          "Propone EDITAR campos de un gasto por su id (descripcion/monto/fecha/proveedor/categoria). "
          "NO escribe: publica una tarjeta. Usa listar_gastos primero. Pasa solo los campos a cambiar.",
          {"id": int, "descripcion": str, "monto": str, "fecha": str, "proveedor": str, "categoria": str})
    async def proponer_editar_gasto(args):
        g = _obtener_gasto(args.get("id"))
        if not g:
            return {"content": [{"type": "text",
                    "text": f"No encontré un gasto con id {args.get('id')}. Usa listar_gastos para ver los ids."}]}
        params = {"id": g["id"]}
        for campo in ("descripcion", "monto", "fecha", "proveedor", "categoria"):
            v = args.get(campo)
            if v is not None and str(v).strip() != "":
                params[campo] = v
        try:
            clean = gastos.validar_editar(params)
        except ValueError as e:
            return {"content": [{"type": "text", "text": f"No puedo proponer la edición: {e}"}]}
        collector.add(editar_gasto_artifact(g, params, clean["cambios"]))
        return {"content": [{"type": "text",
                "text": f"Propuesta lista para confirmar — Editar {g['descripcion']}. "
                        "Quedó como tarjeta; el usuario debe apretar Confirmar. NO afirmes que ya se editó."}]}
```

Actualizar `create_sdk_mcp_server(name="acciones", ..., tools=[proponer_gasto])` para incluir las cuatro:

```python
    server = create_sdk_mcp_server(name="acciones", version="1.0.0", tools=[
        proponer_gasto, proponer_borrar_gasto, proponer_marcar_gasto_pagado, proponer_editar_gasto,
    ])
    tool_names = [
        "mcp__acciones__proponer_gasto",
        "mcp__acciones__proponer_borrar_gasto",
        "mcp__acciones__proponer_marcar_gasto_pagado",
        "mcp__acciones__proponer_editar_gasto",
    ]
    return server, tool_names
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_tools_acciones.py -v`
Expected: PASS (los nuevos + los existentes de la acción 1)

- [ ] **Step 5: Commit**

```bash
git add app/agent/tools_acciones.py tests/test_tools_acciones.py
git commit -m "Agrega tools de proponer borrar/editar/marcar-pagado gasto"
```

---

### Task 10: Regla ampliada en el system prompt

**Files:**
- Modify: `app/agent/system_prompt.py` (reemplazar el bloque "REGISTRAR GASTOS")
- Test: `tests/test_system_prompt.py`

**Interfaces:**
- Produces: `SYSTEM_PROMPT` menciona `listar_gastos`, `proponer_borrar_gasto`, `proponer_editar_gasto`, `proponer_marcar_gasto_pagado`.

- [ ] **Step 1: Escribir el test que falla**

Agregar a `tests/test_system_prompt.py`:

```python
def test_system_prompt_menciona_acciones_de_gasto():
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "listar_gastos" in SYSTEM_PROMPT
    assert "proponer_borrar_gasto" in SYSTEM_PROMPT
    assert "proponer_editar_gasto" in SYSTEM_PROMPT
    assert "proponer_marcar_gasto_pagado" in SYSTEM_PROMPT
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_system_prompt.py::test_system_prompt_menciona_acciones_de_gasto -v`
Expected: FAIL

- [ ] **Step 3: Reemplazar el bloque del prompt**

En `app/agent/system_prompt.py`, reemplazar el bloque que empieza en
`REGISTRAR GASTOS (acción con confirmación):` (hasta antes del cierre `"""`) por:

```python
ACCIONES SOBRE GASTOS (con confirmación): puedes registrar, listar, borrar,
editar y marcar como pagado un gasto (cuenta por pagar). Cada acción solo deja
una TARJETA para que el usuario apriete Confirmar; NUNCA digas que la acción ya
ocurrió ("quedó registrado", "lo borré", "ya está pagado"): di que dejaste la
propuesta lista para confirmar.
- Registrar: mcp__acciones__proponer_gasto con descripción, monto y fecha de
  vencimiento (proveedor y categoría opcionales). Si falta un dato clave, pídelo.
- Para borrar, editar o marcar pagado primero usa mcp__negocio__listar_gastos
  para ubicar el gasto y su id. Si hay VARIOS que calzan, muéstralos numerados y
  pregunta cuál antes de proponer.
- Borrar: mcp__acciones__proponer_borrar_gasto con el id (borrado definitivo).
- Editar: mcp__acciones__proponer_editar_gasto con el id y solo los campos a
  cambiar (descripcion/monto/fecha/proveedor/categoria).
- Marcar pagado: mcp__acciones__proponer_marcar_gasto_pagado con el id (fecha
  opcional, por defecto hoy).
```

- [ ] **Step 4: Correr los tests y verificar verde**

Run: `python -m pytest tests/test_system_prompt.py -v`
Expected: PASS (el nuevo + los existentes, incluido el de la acción 1 que verifica `proponer_gasto`)

- [ ] **Step 5: Commit**

```bash
git add app/agent/system_prompt.py tests/test_system_prompt.py
git commit -m "Amplia la regla del prompt a borrar/editar/marcar-pagado gasto"
```

---

### Task 11: Verificación integral

**Files:** ninguno (solo verificación).

- [ ] **Step 1: Suite completa**

Run: `python -m pytest -q`
Expected: PASS sin fallos (incluye test_negocio_gastos, test_negocio_acciones, test_tools_negocio, test_tools_acciones, test_system_prompt).

- [ ] **Step 2: Extremo a extremo con el agente en vivo (sin browser)**

Crear un gasto de prueba conocido y verificar el ciclo listar → proponer →
ejecutar de cada acción vía `run_agent` + el endpoint, en un script temporal:

1. `INSERT` directo (vía `app.dashboard.get_conn()`) de un gasto
   `descripcion='PRUEBA t11', monto=99000, fecha_vencimiento='2026-12-31'` y
   guardar su id.
2. `app.dashboard.run_agent("marca como pagado el gasto PRUEBA t11")` →
   verificar que la respuesta trae un artefacto `accion` con
   `tipo_accion="marcar_gasto_pagado"` y `params.id` == el id de prueba.
3. `app.dashboard.run_agent("edita el gasto PRUEBA t11, cámbiale el monto a 88000")`
   → artefacto `tipo_accion="editar_gasto"` con `params` que incluya el monto.
4. `app.dashboard.run_agent("borra el gasto PRUEBA t11")` → artefacto
   `tipo_accion="borrar_gasto"` con `params.id` == el id de prueba.
   (run_agent NO escribe; solo confirma que el agente lista y propone bien.)
5. Ejecutar el borrado de verdad vía el endpoint
   (`POST /api/ejecutar-accion {"tipo_accion":"borrar_gasto","params":{"id":<id>}}`)
   o un `DELETE` directo, y confirmar con un SELECT que no quede `PRUEBA t11`.
   Borrar el script temporal.

Expected: cada `run_agent` devuelve el artefacto del tipo correcto apuntando al
id de prueba; la fila queda eliminada al final.

- [ ] **Step 3: Limpieza**

Confirmar con `SELECT COUNT(*) FROM cuentas_por_pagar WHERE descripcion LIKE 'PRUEBA t%'` que es 0. Si queda algo, borrarlo.

- [ ] **Step 4: Commit (si hubo ajuste de docs)**

Si esta tarea no cambió código, no hay commit. Si se documentó algo (p. ej. una
línea en CLAUDE.md sobre las acciones de gasto del chat), commitearlo:

```bash
git add -A
git commit -m "Documenta las acciones de gasto desde el chat"
```

---

## Self-Review

**1. Cobertura del spec:**
- Mecanismo genérico (registro + endpoint único + tarjeta genérica) → Tasks 5, 6, 7. ✓
- Migración de registrar_gasto al registro → Task 5 (`_validar_registrar`/`_ejecutar_registrar`) + Task 6 (endpoint genérico). ✓
- Borrar (hard delete) → Tasks 2 (negocio), 9 (proposer). ✓
- Editar → Tasks 4 (negocio), 9 (proposer). ✓
- Marcar pagado → Tasks 3 (negocio), 9 (proposer). ✓
- `listar_gastos` para ubicar el gasto → Tasks 1 (negocio.listar) + 8 (tool). ✓
- Resumen con datos exactos / antes→después → Task 9 builders. ✓
- Regla del prompt (listar antes; nunca afirmar) → Task 10. ✓
- Manejo de errores 400/500, "ya no existe" → 400 → Task 6 (+ ValueError en Tasks 2–4). ✓
- Seguridad: agente no escribe, permission_mode intacto → ninguna task lo cambia; el endpoint es el único que escribe. ✓
- Tests por capa → cada task tiene TDD; integración → Task 11. ✓
- **Corrección al spec:** la tabla del spec marcaba `orchestrator.py` como "Modificado"; en realidad NO se toca (las listas de tools viven en los `build_*_server`). El plan no incluye tarea de orquestador. ✓

**2. Escaneo de placeholders:** Sin "TBD"/"añadir validación"/"similar a Task N". Todo el código va completo. ✓

**3. Consistencia de tipos:**
- `validar(tipo, params) -> clean` y `ejecutar(cur, tipo, clean) -> {mensaje,…}`; el endpoint usa exactamente esos nombres. ✓
- `validar_editar -> {"id", "cambios"}`; `_ejecutar_editar` pasa `clean["cambios"]` a `editar_gasto(cur, id, cambios)`. ✓
- `validar_marcar_pagado -> {"id","fecha_pago"}`; `_ejecutar_marcar_pagado` pasa `clean["fecha_pago"]`. ✓
- Builders de Task 9 producen `payload.tipo_accion` que el registro (Task 5) reconoce: `borrar_gasto`, `editar_gasto`, `marcar_gasto_pagado`, `registrar_gasto`. ✓
- La tarjeta (Task 7) postea `{tipo_accion: p.tipo_accion, params: p.params}` que el endpoint (Task 6) lee como `body.get("tipo_accion")` / `body.get("params")`. ✓
- `_validar_id` definido en Task 2, reutilizado en Tasks 3 y 4. ✓

## Execution Handoff

**Plan completo y guardado en `docs/superpowers/plans/2026-06-22-acciones-gasto-mecanismo-generico.md`. Dos opciones de ejecución:**

**1. Subagent-Driven (recomendada)** — despacho un subagente nuevo por tarea, reviso entre tareas, iteración rápida.

**2. Inline Execution** — ejecuto las tareas en esta sesión con executing-plans, por lotes con checkpoints.

**¿Cuál prefieres?**
