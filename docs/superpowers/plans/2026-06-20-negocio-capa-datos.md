# Capa de datos de negocio (Fase 2a — Parte 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Crear funciones de datos de solo lectura (ventas, costos/márgenes, flujo de caja, deuda por cliente) que reutilizan la lógica probada del proyecto y son testeables sin BD, como base de las "calculadoras" que después usará el chat.

**Architecture:** Funciones puras de datos que reciben un cursor `RealDictCursor` y devuelven estructuras Python ya agregadas (mismo patrón que `app/briefing/data.py`). La lógica de flujo de caja se extrae de `scripts/flujo_caja.py` a `app/negocio/flujo.py` para que CLI y futuras herramientas compartan una sola fuente. Sin escrituras, sin cambios de permisos.

**Tech Stack:** Python 3.x, psycopg2 (`RealDictCursor`), pytest. Sin dependencias nuevas.

**Diseño de referencia:** `docs/superpowers/specs/2026-06-20-chat-analisis-confiable-design.md`

---

## File Structure

| Archivo | Responsabilidad | Nuevo/Modificado |
|---|---|---|
| `app/briefing/data.py` | Se agrega `deuda_cliente()`. | Modificado |
| `app/negocio/__init__.py` | Marca el paquete. | Nuevo |
| `app/negocio/ventas.py` | `total`, `ranking`, `por_cliente`, `por_producto`. | Nuevo |
| `app/negocio/costos.py` | `PRECIOS_VENTA_NETO`, `costos_sku`, `margenes`. | Nuevo |
| `app/negocio/flujo.py` | Helpers + `proyectar_flujo()` (extraído de flujo_caja.py). | Nuevo |
| `scripts/flujo_caja.py` | Pasa a importar de `app/negocio/flujo.py`; solo imprime. | Modificado |
| `tests/test_briefing_data.py` | Test de `deuda_cliente`. | Modificado |
| `tests/test_negocio_ventas.py` | Tests de ventas. | Nuevo |
| `tests/test_negocio_costos.py` | Tests de costos/márgenes. | Nuevo |
| `tests/test_negocio_flujo.py` | Tests de la proyección. | Nuevo |

**Convención (igual que `app/briefing/data.py`):** las funciones reciben un cursor `RealDictCursor`, devuelven dicts/listas, y se testean con un cursor falso. Reglas canónicas: `COALESCE(monto_total_ajustado, monto_total)`, `tipo_documento != 61`, `fecha_pago IS NULL` = pendiente, excluir `estado = 'incobrable'` en totales de deuda. Correr pytest con `python -m pytest`. `git add` solo con las rutas indicadas (nunca `git add -A`). Commits en español.

---

### Task 1: `deuda_cliente()` en briefing/data.py

**Files:**
- Modify: `app/briefing/data.py`
- Test: `tests/test_briefing_data.py`

- [ ] **Step 1: Agregar el test al final de `tests/test_briefing_data.py`**

```python
def test_deuda_cliente_suma_y_estructura():
    rows = [
        {"folio": 4640, "fecha": "2026-04-01", "razon_social": "Bar Uno",
         "total": 80000, "dias_vencida": 78},
        {"folio": 4655, "fecha": "2026-05-01", "razon_social": "Bar Uno",
         "total": 20000, "dias_vencida": 48},
    ]
    r = data.deuda_cliente(FakeCursor(rows), "Bar Uno")
    assert r["nombre_consultado"] == "Bar Uno"
    assert r["n_facturas"] == 2
    assert r["total"] == 100000.0
    assert r["facturas"][0]["folio"] == 4640
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_briefing_data.py -v`
Expected: FAIL con `AttributeError: ... has no attribute 'deuda_cliente'`.

- [ ] **Step 3: Agregar la función al final de `app/briefing/data.py`**

```python
def deuda_cliente(cur, nombre):
    """Deuda pendiente de un cliente (por nombre o RUT).

    A diferencia de los totales globales, NO excluye 'incobrable': si se
    pregunta por un cliente puntual se muestra su deuda igual.
    """
    cur.execute("""
        SELECT v.folio, v.fecha, c.razon_social,
               COALESCE(v.monto_total_ajustado, v.monto_total) AS total,
               (CURRENT_DATE - v.fecha) AS dias_vencida
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
          AND v.fecha_pago IS NULL
          AND COALESCE(v.monto_total_ajustado, v.monto_total) > 0
          AND (c.razon_social ILIKE %s OR v.rut_cliente ILIKE %s)
        ORDER BY v.fecha
    """, (f"%{nombre}%", f"%{nombre}%"))
    facturas = [
        {"folio": f["folio"], "fecha": f["fecha"], "cliente": f["razon_social"],
         "total": float(f["total"]), "dias": int(f["dias_vencida"])}
        for f in cur.fetchall()
    ]
    return {
        "nombre_consultado": nombre,
        "n_facturas": len(facturas),
        "total": sum(x["total"] for x in facturas),
        "facturas": facturas,
    }
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_briefing_data.py -v`
Expected: PASS (los previos + el nuevo).

- [ ] **Step 5: Commit**

```bash
git add app/briefing/data.py tests/test_briefing_data.py
git commit -m "Agrega deuda_cliente a la capa de datos del brief"
```

---

### Task 2: Paquete `app/negocio` + `ventas.total` y `ventas.ranking`

**Files:**
- Create: `app/negocio/__init__.py`
- Create: `app/negocio/ventas.py`
- Test: `tests/test_negocio_ventas.py`

- [ ] **Step 1: Crear `app/negocio/__init__.py`**

```python
"""Capa de datos de negocio de Zigurat (solo lectura)."""
```

- [ ] **Step 2: Crear `tests/test_negocio_ventas.py`**

```python
# tests/test_negocio_ventas.py
from app.negocio import ventas


class FakeCursor:
    """Cursor falso estilo RealDictCursor (fetchall/fetchone devuelven dicts)."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_total_global():
    r = ventas.total(FakeCursor([{"n": 120, "total": 5000000}]))
    assert r["n"] == 120
    assert r["total"] == 5000000.0
    assert r["desde"] is None and r["hasta"] is None


def test_total_con_rango():
    r = ventas.total(FakeCursor([{"n": 6, "total": 756409}]),
                     desde="2026-06-01", hasta="2026-06-30")
    assert r["n"] == 6
    assert r["total"] == 756409.0
    assert r["desde"] == "2026-06-01"


def test_ranking_mapea_filas():
    rows = [
        {"razon_social": "Bar Uno", "rut_cliente": "11-1", "total_real": 900000},
        {"razon_social": "Bar Dos", "rut_cliente": "22-2", "total_real": 400000},
    ]
    r = ventas.ranking(FakeCursor(rows), limite=2)
    assert r[0] == {"cliente": "Bar Uno", "rut": "11-1", "total": 900000.0}
    assert len(r) == 2
```

- [ ] **Step 3: Correr y verificar que falla**

Run: `python -m pytest tests/test_negocio_ventas.py -v`
Expected: FAIL con ImportError (módulo `ventas` no existe aún).

- [ ] **Step 4: Crear `app/negocio/ventas.py`**

```python
"""Consultas de ventas de solo lectura. Reutiliza el SQL probado de
.claude/skills/consultar-ventas/scripts/query_ventas.py, devolviendo datos
estructurados en vez de imprimir. Regla canónica: monto real =
COALESCE(monto_total_ajustado, monto_total); las NC (tipo 61) se excluyen.
"""


def total(cur, desde=None, hasta=None):
    """Total vendido (neto de NC). Global, o por rango de fechas si se pasan ambas."""
    if desde and hasta:
        cur.execute("""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(COALESCE(v.monto_total_ajustado, v.monto_total)), 0) AS total
            FROM ventas v
            WHERE v.tipo_documento != 61 AND v.fecha BETWEEN %s AND %s
        """, (desde, hasta))
    else:
        cur.execute("""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(COALESCE(v.monto_total_ajustado, v.monto_total)), 0) AS total
            FROM ventas v
            WHERE v.tipo_documento != 61
        """)
    f = cur.fetchone()
    return {"n": int(f["n"]), "total": float(f["total"]), "desde": desde, "hasta": hasta}


def ranking(cur, limite=10):
    """Top N clientes por venta real (neto de NC)."""
    cur.execute("""
        SELECT c.razon_social, v.rut_cliente,
               SUM(COALESCE(v.monto_total_ajustado, v.monto_total)) AS total_real
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE v.tipo_documento != 61
        GROUP BY v.rut_cliente, c.razon_social
        ORDER BY total_real DESC
        LIMIT %s
    """, (limite,))
    return [
        {"cliente": f["razon_social"], "rut": f["rut_cliente"], "total": float(f["total_real"])}
        for f in cur.fetchall()
    ]
```

- [ ] **Step 5: Correr y verificar que pasa**

Run: `python -m pytest tests/test_negocio_ventas.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/negocio/__init__.py app/negocio/ventas.py tests/test_negocio_ventas.py
git commit -m "Agrega capa de datos de ventas: total y ranking"
```

---

### Task 3: `ventas.por_cliente` y `ventas.por_producto`

**Files:**
- Modify: `app/negocio/ventas.py`
- Test: `tests/test_negocio_ventas.py`

- [ ] **Step 1: Agregar tests al final de `tests/test_negocio_ventas.py`**

```python
def test_por_cliente_separa_facturas_y_nc():
    rows = [
        {"folio": 10, "tipo_documento": 33, "fecha": "2026-06-01", "monto": 100000},
        {"folio": 11, "tipo_documento": 61, "fecha": "2026-06-02", "monto": 20000},
    ]
    r = ventas.por_cliente(FakeCursor(rows), "Bar Uno")
    assert r["n_facturas"] == 1
    assert r["n_notas_credito"] == 1
    assert r["total_real"] == 100000.0
    assert len(r["documentos"]) == 2


def test_por_producto_mapea():
    rows = [
        {"folio": 10, "fecha": "2026-06-01", "razon_social": "Bar Uno",
         "descripcion": "Barril 30L Cream Ale", "cantidad": 2, "precio_unitario": 20000},
    ]
    r = ventas.por_producto(FakeCursor(rows), "Cream")
    assert r[0]["producto"] == "Barril 30L Cream Ale"
    assert r[0]["cantidad"] == 2
    assert r[0]["precio_unitario"] == 20000.0
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `python -m pytest tests/test_negocio_ventas.py -v`
Expected: FAIL con AttributeError en `por_cliente` / `por_producto`.

- [ ] **Step 3: Agregar las funciones al final de `app/negocio/ventas.py`**

```python
def por_cliente(cur, nombre):
    """Documentos de un cliente. Separa facturas de NC; el total real suma
    solo facturas (las NC ya están descontadas en los montos ajustados)."""
    cur.execute("""
        SELECT v.folio, v.tipo_documento, v.fecha,
               CASE WHEN v.tipo_documento = 61 THEN v.monto_total
                    ELSE COALESCE(v.monto_total_ajustado, v.monto_total)
               END AS monto
        FROM ventas v
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE c.razon_social ILIKE %s
        ORDER BY v.fecha DESC
    """, (f"%{nombre}%",))
    filas = cur.fetchall()
    facturas = [r for r in filas if int(r["tipo_documento"]) != 61]
    return {
        "nombre_consultado": nombre,
        "n_facturas": len(facturas),
        "n_notas_credito": len(filas) - len(facturas),
        "total_real": sum(float(r["monto"]) for r in facturas),
        "documentos": [
            {"folio": r["folio"], "tipo": int(r["tipo_documento"]),
             "fecha": r["fecha"], "monto": float(r["monto"])}
            for r in filas
        ],
    }


def por_producto(cur, nombre):
    """Líneas de detalle que coinciden con un producto (excluye NC)."""
    cur.execute("""
        SELECT p.folio, v.fecha, c.razon_social, p.descripcion,
               p.cantidad, p.precio_unitario
        FROM productos p
        JOIN ventas v ON v.folio = p.folio
        JOIN clientes c ON c.rut_cliente = v.rut_cliente
        WHERE p.descripcion ILIKE %s AND v.tipo_documento != 61
        ORDER BY v.fecha DESC
    """, (f"%{nombre}%",))
    return [
        {"folio": r["folio"], "fecha": r["fecha"], "cliente": r["razon_social"],
         "producto": r["descripcion"], "cantidad": r["cantidad"],
         "precio_unitario": (float(r["precio_unitario"])
                             if r["precio_unitario"] is not None else None)}
        for r in cur.fetchall()
    ]
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `python -m pytest tests/test_negocio_ventas.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/negocio/ventas.py tests/test_negocio_ventas.py
git commit -m "Agrega ventas por cliente y por producto"
```

---

### Task 4: `costos.py` (costos por SKU + márgenes)

**Files:**
- Create: `app/negocio/costos.py`
- Test: `tests/test_negocio_costos.py`

- [ ] **Step 1: Crear `tests/test_negocio_costos.py`**

```python
# tests/test_negocio_costos.py
from app.negocio import costos


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_costos_sku_mapea():
    rows = [
        {"codigo": "CREAM-B30", "nombre_cerveza": "Cream Ale", "formato": "Barril 30L acero",
         "costo_liquido_unitario": 18000, "costo_envasado_unitario": 0,
         "costo_total_unitario": 18000},
    ]
    r = costos.costos_sku(FakeCursor(rows))
    assert r[0]["codigo"] == "CREAM-B30"
    assert r[0]["costo_total"] == 18000.0


def test_margenes_calcula_para_barril_con_precio():
    rows = [
        {"codigo": "CREAM-B30", "nombre_cerveza": "Cream Ale", "formato": "Barril 30L acero",
         "costo_liquido_unitario": 18000, "costo_envasado_unitario": 0,
         "costo_total_unitario": 18000},
    ]
    r = costos.margenes(FakeCursor(rows))
    assert r[0]["precio_venta"] == 55370.0
    assert r[0]["margen"] == 55370.0 - 18000.0


def test_margenes_botella_sin_precio_queda_none():
    rows = [
        {"codigo": "CREAM-330", "nombre_cerveza": "Cream Ale", "formato": "Botella 330ml",
         "costo_liquido_unitario": 600, "costo_envasado_unitario": 300,
         "costo_total_unitario": 900},
    ]
    r = costos.margenes(FakeCursor(rows))
    assert r[0]["precio_venta"] is None
    assert r[0]["margen"] is None
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_negocio_costos.py -v`
Expected: FAIL con ImportError.

- [ ] **Step 3: Crear `app/negocio/costos.py`**

```python
"""Costos por SKU y márgenes (solo lectura).

Costos: misma consulta a vista_costo_sku que scripts/costo_sku.py.
Márgenes: cruza el costo total con los precios de venta netos confirmados.
Los precios son por BARRIL 30L (confirmados por el productor, ver CLAUDE.md);
para botellas no hay precio confirmado, así que el margen queda en None.
"""
import unicodedata

# Precios de venta netos confirmados por barril 30L (desde CLAUDE.md).
PRECIOS_VENTA_NETO = {
    "cream ale": 55370,
    "scotch ale": 55370,
    "stout cafe": 75000,
    "stout cacao": 75000,
    "paint it black": 98000,
}


def _norm(s):
    """Normaliza para comparar nombres: minúsculas, sin tildes, espacios simples."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def costos_sku(cur, receta=None, sku=None):
    """Costo unitario por SKU desde vista_costo_sku. Filtros opcionales."""
    where, params = [], []
    if sku:
        where.append("codigo = %s")
        params.append(sku)
    if receta:
        where.append("nombre_cerveza ILIKE %s")
        params.append(f"%{receta}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    cur.execute(f"""
        SELECT codigo, nombre_cerveza, formato,
               costo_liquido_unitario, costo_envasado_unitario, costo_total_unitario
        FROM vista_costo_sku
        {where_sql}
        ORDER BY nombre_cerveza, formato, codigo
    """, params)
    return [
        {"codigo": r["codigo"], "cerveza": r["nombre_cerveza"], "formato": r["formato"],
         "costo_liquido": (float(r["costo_liquido_unitario"])
                           if r["costo_liquido_unitario"] is not None else None),
         "costo_envasado": (float(r["costo_envasado_unitario"])
                            if r["costo_envasado_unitario"] is not None else None),
         "costo_total": (float(r["costo_total_unitario"])
                         if r["costo_total_unitario"] is not None else None)}
        for r in cur.fetchall()
    ]


def margenes(cur, receta=None):
    """Margen por SKU = precio de venta confirmado − costo total.

    Solo para formatos de barril (donde hay precio confirmado). Para botellas
    u otros, precio_venta y margen quedan en None (no se inventa un margen).
    """
    salida = []
    for sku in costos_sku(cur, receta=receta):
        precio = None
        if "barril" in _norm(sku["formato"]):
            precio = PRECIOS_VENTA_NETO.get(_norm(sku["cerveza"]))
        margen = None
        margen_pct = None
        if precio is not None and sku["costo_total"] is not None:
            margen = float(precio) - sku["costo_total"]
            margen_pct = round(100 * margen / precio, 1) if precio else None
        salida.append({
            "codigo": sku["codigo"], "cerveza": sku["cerveza"], "formato": sku["formato"],
            "costo_total": sku["costo_total"],
            "precio_venta": float(precio) if precio is not None else None,
            "margen": margen, "margen_pct": margen_pct,
        })
    return salida
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_negocio_costos.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/negocio/costos.py tests/test_negocio_costos.py
git commit -m "Agrega costos por SKU y margenes por barril"
```

---

### Task 5: `flujo.py` — extraer `proyectar_flujo()`

**Files:**
- Create: `app/negocio/flujo.py`
- Test: `tests/test_negocio_flujo.py`

Esta tarea extrae la lógica de proyección de `scripts/flujo_caja.py` a una función
reutilizable que devuelve un dict (sin imprimir). Las funciones auxiliares se
copian tal cual del script original (mismo SQL).

- [ ] **Step 1: Crear `tests/test_negocio_flujo.py`**

```python
# tests/test_negocio_flujo.py
from datetime import date
from app.negocio import flujo


class FakeCursorSecuencial:
    """Devuelve un result-set por cada execute(), en orden. proyectar_flujo hace
    estas consultas en este orden: saldo banco, avg días, facturas pendientes,
    gastos puntuales, gastos recurrentes."""

    def __init__(self, resultados):
        self._resultados = list(resultados)
        self._actual = []

    def execute(self, sql, params=None):
        self._actual = self._resultados.pop(0) if self._resultados else []

    def fetchall(self):
        return self._actual

    def fetchone(self):
        return self._actual[0] if self._actual else None


def test_proyectar_flujo_estructura_y_ingreso_en_ventana():
    hoy = date(2026, 6, 20)
    resultados = [
        [{"saldo_diario": 1000000, "fecha": hoy}],          # saldo banco
        [{"rut_cliente": "11-1", "avg_dias": 30}],          # avg días
        [{"folio": 1, "fecha": date(2026, 5, 31), "rut_cliente": "11-1",
          "razon_social_receptor": "Bar Uno", "monto": 200000}],  # facturas
        [],                                                  # gastos puntuales
        [],                                                  # gastos recurrentes
    ]
    r = flujo.proyectar_flujo(FakeCursorSecuencial(resultados), hoy=hoy)
    assert r["saldo_inicial"] == 1000000.0
    assert len(r["semanas"]) == 4
    # factura del 31/05 + 30 días = 30/06, dentro del horizonte (hoy+28d) -> cuenta
    assert r["total_ingresos"] == 200000.0


def test_proyectar_flujo_saldo_manual():
    hoy = date(2026, 6, 20)
    resultados = [
        [],   # avg días (no consulta saldo banco porque saldo_inicial viene dado)
        [],   # facturas
        [],   # gastos puntuales
        [],   # gastos recurrentes
    ]
    r = flujo.proyectar_flujo(FakeCursorSecuencial(resultados),
                              saldo_inicial=500000, hoy=hoy)
    assert r["saldo_inicial"] == 500000.0
    assert r["total_ingresos"] == 0.0
    assert r["total_egresos"] == 0.0
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_negocio_flujo.py -v`
Expected: FAIL con ImportError.

- [ ] **Step 3: Crear `app/negocio/flujo.py`**

```python
"""Proyección de flujo de caja a N semanas (lógica reutilizable, solo lectura).

Extraída de scripts/flujo_caja.py para que el CLI y las herramientas del agente
usen la misma fuente. Las funciones reciben un cursor RealDictCursor.
"""
from datetime import date, timedelta
from calendar import monthrange
from collections import defaultdict

SEMANAS = 4
AVG_DIAS_GLOBAL = 30
MIN_FACTURAS_PARA_AVG = 3


def obtener_saldo_banco(cur):
    """Último saldo_diario registrado en movimientos_banco -> (saldo, fecha)."""
    cur.execute("""
        SELECT saldo_diario, fecha
        FROM movimientos_banco
        WHERE saldo_diario IS NOT NULL
        ORDER BY fecha DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        return float(row["saldo_diario"]), row["fecha"]
    return None, None


def obtener_avg_dias_por_cliente(cur):
    """Promedio de días de pago por cliente (últimas 10 facturas pagadas)."""
    cur.execute("""
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
        HAVING COUNT(*) >= %s
    """, (MIN_FACTURAS_PARA_AVG,))
    return {row["rut_cliente"]: float(row["avg_dias"]) for row in cur.fetchall()}


def obtener_facturas_pendientes(cur):
    """Facturas sin fecha_pago (cuentas por cobrar)."""
    cur.execute("""
        SELECT folio, fecha, rut_cliente, razon_social_receptor,
               COALESCE(monto_total_ajustado, monto_total) AS monto
        FROM ventas
        WHERE fecha_pago IS NULL AND tipo_documento != '61'
        ORDER BY fecha
    """)
    return cur.fetchall()


def obtener_gastos_pendientes(cur, hoy, horizonte):
    """Gastos a pagar dentro del horizonte: puntuales + recurrentes proyectados."""
    cur.execute("""
        SELECT id, descripcion, proveedor, monto, fecha_vencimiento, categoria
        FROM cuentas_por_pagar
        WHERE pagado = FALSE
          AND (recurrente = FALSE OR recurrente IS NULL)
          AND fecha_vencimiento BETWEEN %s AND %s
        ORDER BY fecha_vencimiento
    """, (hoy, horizonte))
    gastos = list(cur.fetchall())

    cur.execute("""
        SELECT id, descripcion, proveedor, monto, fecha_vencimiento, categoria
        FROM cuentas_por_pagar
        WHERE recurrente = TRUE AND periodicidad = 'mensual'
    """)
    for row in cur.fetchall():
        dia_mes = row["fecha_vencimiento"].day
        for delta_m in range(3):
            mes_abs = hoy.month + delta_m
            anio = hoy.year + (mes_abs - 1) // 12
            mes = (mes_abs - 1) % 12 + 1
            dia = min(dia_mes, monthrange(anio, mes)[1])
            fecha_proj = date(anio, mes, dia)
            if hoy <= fecha_proj <= horizonte:
                ocurrencia = dict(row)
                ocurrencia["fecha_vencimiento"] = fecha_proj
                gastos.append(ocurrencia)

    gastos.sort(key=lambda x: x["fecha_vencimiento"])
    return gastos


def semana_de(d, inicio_periodo):
    """Número de semana (0-based) de una fecha respecto al inicio."""
    return (d - inicio_periodo).days // 7


def proyectar_flujo(cur, saldo_inicial=None, semanas=SEMANAS, hoy=None):
    """Proyecta el flujo de caja. Devuelve un dict estructurado (no imprime).

    saldo_inicial: si es None, se toma el último saldo bancario de la BD.
    hoy: inyectable para tests; por defecto date.today().
    """
    hoy = hoy or date.today()
    horizonte = hoy + timedelta(weeks=semanas)

    if saldo_inicial is None:
        saldo_inicial, saldo_fecha = obtener_saldo_banco(cur)
        if saldo_inicial is None:
            saldo_inicial, saldo_fecha = 0.0, None
    else:
        saldo_fecha = hoy

    avg_dias = obtener_avg_dias_por_cliente(cur)
    facturas = obtener_facturas_pendientes(cur)
    gastos = obtener_gastos_pendientes(cur, hoy, horizonte)

    ingresos_semana = defaultdict(list)
    ingresos_fuera = []
    for f in facturas:
        avg = avg_dias.get(f["rut_cliente"], AVG_DIAS_GLOBAL)
        proyectada = f["fecha"] + timedelta(days=int(avg))
        if proyectada < hoy:
            proyectada = hoy
        if proyectada <= horizonte:
            sem = max(0, min(semana_de(proyectada, hoy), semanas - 1))
            ingresos_semana[sem].append({
                "folio": f["folio"], "cliente": f["razon_social_receptor"],
                "monto": float(f["monto"]), "fecha_proyectada": proyectada,
                "avg_dias": int(avg),
            })
        else:
            ingresos_fuera.append({
                "folio": f["folio"], "cliente": f["razon_social_receptor"],
                "monto": float(f["monto"]),
            })

    gastos_semana = defaultdict(list)
    for g in gastos:
        sem = max(0, min(semana_de(g["fecha_vencimiento"], hoy), semanas - 1))
        gastos_semana[sem].append({
            "descripcion": g["descripcion"], "proveedor": g["proveedor"],
            "monto": float(g["monto"]), "fecha_vencimiento": g["fecha_vencimiento"],
            "categoria": g["categoria"],
        })

    saldo_acum = float(saldo_inicial)
    total_ingresos = total_egresos = 0.0
    semanas_out = []
    for sem in range(semanas):
        inicio = hoy + timedelta(weeks=sem)
        fin = inicio + timedelta(days=6)
        ingresos = sum(i["monto"] for i in ingresos_semana.get(sem, []))
        egresos = sum(g["monto"] for g in gastos_semana.get(sem, []))
        saldo_acum += ingresos - egresos
        total_ingresos += ingresos
        total_egresos += egresos
        semanas_out.append({
            "semana": sem + 1,
            "label": f"{inicio.strftime('%d/%m')}-{fin.strftime('%d/%m')}",
            "ingresos": ingresos, "egresos": egresos,
            "saldo_acumulado": saldo_acum, "riesgo": saldo_acum < 0,
            "detalle_ingresos": ingresos_semana.get(sem, []),
            "detalle_egresos": gastos_semana.get(sem, []),
        })

    return {
        "saldo_inicial": float(saldo_inicial), "saldo_fecha": saldo_fecha,
        "hoy": hoy, "horizonte": horizonte, "semanas": semanas_out,
        "total_ingresos": total_ingresos, "total_egresos": total_egresos,
        "ingresos_fuera": ingresos_fuera,
    }
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_negocio_flujo.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/negocio/flujo.py tests/test_negocio_flujo.py
git commit -m "Extrae proyectar_flujo a la capa de negocio (reutilizable)"
```

---

### Task 6: Refactorizar `scripts/flujo_caja.py` para usar `flujo.py`

**Files:**
- Modify: `scripts/flujo_caja.py`

Objetivo: que el script use `app.negocio.flujo` como única fuente de la lógica,
manteniendo su salida de consola (tabla semanal + detalles). Se elimina la lógica
duplicada del script y se imprime a partir del dict de `proyectar_flujo()`.

- [ ] **Step 1: Reemplazar el contenido de `scripts/flujo_caja.py` por:**

```python
#!/usr/bin/env python3
"""
flujo_caja.py - Zigurat ERP
Proyecta el flujo de caja de las próximas 4 semanas (CLI).

La lógica vive en app/negocio/flujo.py; este script solo conecta a la BD,
llama a proyectar_flujo() e imprime el resultado.

Uso:
    python scripts/flujo_caja.py
    python scripts/flujo_caja.py --saldo-inicial 5000000
"""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: Falta psycopg2.")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.negocio import flujo  # noqa: E402


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
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", 5432)),
    "dbname": os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


def parsear_saldo_arg():
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--saldo-inicial" and i < len(sys.argv):
            try:
                return float(sys.argv[i + 1].replace(".", "").replace(",", "."))
            except (ValueError, IndexError):
                pass
    return None


def fmt_pesos(n):
    return "$" + "{:,.0f}".format(float(n)).replace(",", ".")


def main():
    saldo_arg = parsear_saldo_arg()

    print("=" * 70)
    print("ZIGURAT ERP - Proyeccion de Flujo de Caja")
    print("=" * 70)
    print()

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    with conn:
        with conn.cursor() as cur:
            r = flujo.proyectar_flujo(cur, saldo_inicial=saldo_arg)
    conn.close()

    if saldo_arg is not None:
        print(f"  Saldo inicial (manual): {fmt_pesos(r['saldo_inicial'])}")
    elif r["saldo_fecha"] is not None:
        dias_viejo = (r["hoy"] - r["saldo_fecha"]).days
        if dias_viejo > 7:
            print(f"  [!] El ultimo saldo en BD es de hace {dias_viejo} dias ({r['saldo_fecha']})")
            print("      Para mayor precision usa: python scripts/flujo_caja.py --saldo-inicial MONTO")
        print(f"  Saldo inicial (BD {r['saldo_fecha']}): {fmt_pesos(r['saldo_inicial'])}")
    else:
        print("  [!] No hay saldo bancario en la BD.")
        print("      Usa: python scripts/flujo_caja.py --saldo-inicial MONTO")
        print(f"  Asumiendo saldo inicial: {fmt_pesos(r['saldo_inicial'])}")

    print()
    print(f"  Horizonte: {r['hoy'].strftime('%d/%m/%Y')} -> {r['horizonte'].strftime('%d/%m/%Y')}")
    en_ventana = sum(len(s["detalle_ingresos"]) for s in r["semanas"])
    print(f"  Facturas por cobrar en ventana: {en_ventana}")
    print(f"  Facturas fuera de ventana:      {len(r['ingresos_fuera'])}")
    print()

    sep = "=" * 70
    print(sep)
    print(f"  {'SEMANA':<18} {'INGRESOS':>14} {'EGRESOS':>14} {'SALDO':>14}")
    print("-" * 70)
    for s in r["semanas"]:
        alerta = " <--RIESGO" if s["riesgo"] else ""
        print(f"  {s['label']:<18} {fmt_pesos(s['ingresos']):>14} "
              f"{fmt_pesos(s['egresos']):>14} {fmt_pesos(s['saldo_acumulado']):>14}{alerta}")
    print("-" * 70)
    print(f"  {'TOTAL':<18} {fmt_pesos(r['total_ingresos']):>14} {fmt_pesos(r['total_egresos']):>14}")
    print(sep)
    print()

    print("DETALLE INGRESOS PROYECTADOS")
    print("-" * 70)
    for s in r["semanas"]:
        if s["detalle_ingresos"]:
            print(f"  Semana {s['semana']} ({s['label']}):")
            for i in s["detalle_ingresos"]:
                print(f"    Folio {i['folio']:>5} | {str(i['cliente'])[:35]:<35} | "
                      f"{fmt_pesos(i['monto']):>12} | ~{i['fecha_proyectada'].strftime('%d/%m')} "
                      f"(avg {int(i['avg_dias'])}d)")
    if r["ingresos_fuera"]:
        print()
        print(f"  Fuera de las 4 semanas ({len(r['ingresos_fuera'])} facturas):")
        for f in r["ingresos_fuera"][:5]:
            print(f"    Folio {f['folio']:>5} | {str(f['cliente'])[:35]:<35} | {fmt_pesos(f['monto']):>12}")
        if len(r["ingresos_fuera"]) > 5:
            print(f"    ... y {len(r['ingresos_fuera']) - 5} mas")
    print()

    if any(s["detalle_egresos"] for s in r["semanas"]):
        print("DETALLE EGRESOS PROYECTADOS")
        print("-" * 70)
        for s in r["semanas"]:
            if s["detalle_egresos"]:
                print(f"  Semana {s['semana']} ({s['label']}):")
                for g in s["detalle_egresos"]:
                    cat = f"[{g['categoria']}]" if g["categoria"] else ""
                    prov = str(g["proveedor"] or "")
                    print(f"    {str(g['descripcion'])[:35]:<35} {prov[:20]:<20} "
                          f"{fmt_pesos(g['monto']):>12} vence "
                          f"{g['fecha_vencimiento'].strftime('%d/%m')} {cat}")
        print()
    else:
        print("Sin gastos registrados en la ventana de 4 semanas.")
        print("Usa /agregar-gasto para registrar cuentas por pagar.")
        print()

    print(sep)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verificar que el CLI sigue funcionando contra la BD real**

Run: `python scripts/flujo_caja.py`
Expected: imprime el encabezado, la tabla semanal con 4 semanas (INGRESOS/EGRESOS/SALDO), los totales y el detalle de ingresos, sin errores. Las cifras deben ser coherentes con la versión anterior (se puede comparar con `git stash` / el commit previo si hay dudas).

- [ ] **Step 3: Correr toda la suite (no romper nada)**

Run: `python -m pytest -q`
Expected: PASS — todos los tests (los nuevos de negocio + los existentes).

- [ ] **Step 4: Commit**

```bash
git add scripts/flujo_caja.py
git commit -m "Refactoriza flujo_caja.py para usar app/negocio/flujo.py"
```

---

## Self-Review

**1. Cobertura del spec (capa de datos):**
- Deuda por cliente → Task 1 (`deuda_cliente`). ✅
- Ventas (total, ranking, por cliente, por producto) → Tasks 2–3. ✅
- Costos por SKU + márgenes (con límite de botellas) → Task 4. ✅
- Flujo de caja reutilizable + refactor del CLI → Tasks 5–6. ✅
- `deuda_total`, `ranking_deudores`, `facturas_vencidas` → ya existen en `app/briefing/data.py` (Fase 1), se reutilizan en la Parte 2; no requieren tarea aquí.

**2. Sin placeholders:** todo el código está completo y literal (data, refactor, tests). ✅

**3. Consistencia de tipos/nombres:** las claves que producen las funciones (`total`, `n`, `cliente`, `rut`, `costo_total`, `precio_venta`, `margen`, `semanas`, `saldo_inicial`, etc.) coinciden con las que asumen los tests. Las funciones auxiliares de `flujo.py` (`obtener_saldo_banco`, `obtener_avg_dias_por_cliente`, `obtener_facturas_pendientes`, `obtener_gastos_pendientes`, `semana_de`) se usan consistentemente en `proyectar_flujo` y ya no se referencian desde el script (que ahora importa `proyectar_flujo`). ✅

**Riesgo conocido (Task 6):** la salida del CLI se reescribe desde el dict; es *equivalente* a la original (mismas secciones y cifras) pero podría diferir en detalles menores de formato. Se valida corriéndolo en Step 2. El comportamiento de datos no cambia (mismo SQL, misma lógica de proyección).

---

## Fuera de alcance (va en la Parte 2)

- `app/agent/tools_negocio.py` (servidor MCP que envuelve estas funciones).
- Cableado en `app/agent/orchestrator.py` y la "regla de oro" en `system_prompt.py`.
- Verificación de integración con el chat (deuda del chat == `/consultar-ventas`).
