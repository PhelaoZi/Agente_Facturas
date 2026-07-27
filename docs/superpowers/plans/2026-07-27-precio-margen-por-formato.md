# Precio y margen por formato — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el chat del dashboard pueda responder el costo y el margen de cualquier formato (botella incluida) sin agotar sus pasos, deduciendo el precio de venta real desde las facturas.

**Architecture:** Un módulo nuevo `app/negocio/precios_venta.py` reconstruye el precio neto unitario por `(cerveza, formato)` sumando la línea de producto más la logística que le corresponde, siguiendo la regla con que el productor escribe sus facturas. `costos.margenes()` lo consume en vez de su lista de precios escrita a mano. En paralelo, el orquestador deja de botar el trabajo cuando se le acaban las iteraciones.

**Tech Stack:** Python 3.x, psycopg2, pytest. Sin dependencias nuevas.

**Diseño de referencia:** `docs/superpowers/specs/2026-07-27-precio-margen-por-formato-design.md`

## Global Constraints

- **Respuestas y comentarios en español.** Nombres de variables y funciones en inglés camelCase salvo donde el módulo vecino ya use español (`app/negocio/` usa nombres en español: `costos_sku`, `margenes`, `deuda_cliente`). **Seguir la convención del archivo que se toca.**
- **Todo `app/negocio/` es de solo lectura.** Ninguna función de este plan puede escribir en la BD. La única escritura es la migración de la Tarea 1, que corre a mano.
- **Los tests del proyecto no tocan la BD**: usan cursores falsos. Única excepción: la verificación manual de la Tarea 4.
- **Suite completa:** `python -m pytest -q` debe quedar en verde al final de cada tarea.
- **Commits en español**, uno por tarea, describiendo el cambio de negocio y no el archivo.
- Precios en la BD son **netos** (`maestro_insumos.precio_neto_unitario`). El IVA es 19%.
- `tipo_documento` y `folio` son **INTEGER** en esta BD: comparar sin comillas.
- Reglas canónicas de SQL de ventas: `COALESCE(monto_total_ajustado, monto_total)`, `COALESCE(monto_neto_ajustado, monto_neto)`, `WHERE tipo_documento != 61`.

## Estructura de archivos

| Archivo | Responsabilidad | Tarea |
|---------|----------------|-------|
| `scripts/migrate_etiqueta_botella.py` | **Crear.** Alta del insumo Etiqueta y su fila de BOM en los 2 SKU de botella. Idempotente, se corre a mano una vez. | 1 |
| `app/negocio/precios_venta.py` | **Crear.** Clasificar líneas de factura, detectar formato y cerveza, atribuir logística, devolver precio por `(cerveza, formato)`. Solo lectura. | 2, 3 |
| `tests/test_negocio_precios.py` | **Crear.** Un caso por regla del algoritmo, con cursor falso. | 2, 3 |
| `app/negocio/costos.py` | **Modificar.** `margenes()` consume los precios deducidos; la lista escrita a mano queda de respaldo. | 5 |
| `tests/test_negocio_costos.py` | **Modificar.** Se invierte el test de la botella sin precio. | 5 |
| `app/agent/tools_negocio.py` | **Modificar.** La tool `margenes` muestra el respaldo de facturas y deja de decir "solo barriles". | 6 |
| `app/agent/system_prompt.py` | **Modificar.** Regla de no calcular precios con SQL sobre `productos`; CO2 como pass-through. | 6, 8 |
| `app/agent/orchestrator.py` | **Modificar.** `MAX_ITERACIONES` a 12 y turno final forzado sin tools. | 7 |
| `tests/test_orchestrator.py` | **Modificar.** Test del turno de cierre. | 7 |
| `app/dashboard.py`, `scripts/wiki_update.py` (×3), `.claude/skills/reporte-semanal/scripts/reporte.py`, `.claude/CLAUDE.md` | **Modificar.** Excluir el CO2 del filtro canónico de productos. | 8 |

**Desviación consciente del spec:** el spec describía `precios_por_formato` devolviendo una lista de filas con `n_descartadas` en cada fila. En el plan devuelve `{"precios": [...], "descartadas": {motivo: n}}` — el conteo de descartes es de la corrida completa, no de una fila, y repetirlo en cada una era engañoso.

---

### Task 1: La etiqueta entra al costo de la botella

El productor confirmó: **toda etiqueta cuesta $230 con IVA incluido**, una por botella. Hoy el BOM de envasado solo tiene botella, tapa y caja, así que el costo de la botella está subestimado en $193 y el margen sale inflado.

**Files:**
- Create: `scripts/migrate_etiqueta_botella.py`

**Interfaces:**
- Consumes: nada.
- Produces: la fila `Etiqueta` en `maestro_insumos` y dos filas en `sku_envasado`. `vista_costo_sku` recalcula sola (es una vista). Las Tareas 4 y 5 verifican contra los costos nuevos: **Cream Ale 330cc = $891,22** y **Scotch Ale 330cc = $918,93**.

- [ ] **Step 1: Escribir la migración**

`maestro_insumos` tiene `UNIQUE (nombre)` y un CHECK que ya acepta la categoría `etiqueta`. `sku_envasado` tiene `UNIQUE (sku_id, insumo_id)`. Ambos permiten `ON CONFLICT DO NOTHING`, que es lo que la hace idempotente.

Crear `scripts/migrate_etiqueta_botella.py`:

```python
#!/usr/bin/env python3
"""
migrate_etiqueta_botella.py — Zigurat ERP

Agrega el insumo "Etiqueta" y lo suma al BOM de envasado de los SKU de
botella 330ml. Idempotente.

Contexto: el costo de la botella no incluia la etiqueta, asi que salia
subestimado en $193 y el margen inflado en el mismo monto. El productor
confirmo que CUALQUIER etiqueta cuesta $230 con IVA incluido, una por botella.
Los barriles no llevan etiqueta y no se tocan.
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

# $230 con IVA incluido / 1.19 = $193.28 neto. maestro_insumos guarda NETO.
PRECIO_CON_IVA = 230.0
IVA = 1.19
PRECIO_NETO_ETIQUETA = round(PRECIO_CON_IVA / IVA, 2)   # 193.28

SKUS_CON_ETIQUETA = ["CREAM-ALE-BOT-330-C12", "SCOTCH-ALE-BOT-330-C12"]


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Etiqueta en el costo de la botella")
    print("=" * 60)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            print(f"\n[1] Insumo 'Etiqueta' a ${PRECIO_NETO_ETIQUETA} neto "
                  f"(${PRECIO_CON_IVA} con IVA)...")
            cur.execute(
                """
                INSERT INTO maestro_insumos (nombre, unidad, precio_neto_unitario, categoria)
                VALUES ('Etiqueta', 'unidad', %s, 'etiqueta')
                ON CONFLICT (nombre) DO UPDATE SET precio_neto_unitario = EXCLUDED.precio_neto_unitario
                """,
                (PRECIO_NETO_ETIQUETA,)
            )
            cur.execute("SELECT id FROM maestro_insumos WHERE nombre = 'Etiqueta'")
            insumo_id = cur.fetchone()[0]
            print(f"  OK  insumo id={insumo_id}")

            print("\n[2] Sumando la etiqueta al BOM de envasado...")
            agregados, ya_estaban, no_encontrados = 0, 0, []
            for codigo in SKUS_CON_ETIQUETA:
                cur.execute("SELECT id FROM sku WHERE codigo = %s", (codigo,))
                fila = cur.fetchone()
                if not fila:
                    no_encontrados.append(codigo)
                    continue
                cur.execute(
                    """
                    INSERT INTO sku_envasado (sku_id, insumo_id, cantidad)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (sku_id, insumo_id) DO NOTHING
                    """,
                    (fila[0], insumo_id)
                )
                if cur.rowcount:
                    agregados += 1
                    print(f"  NUEVO      {codigo}")
                else:
                    ya_estaban += 1
                    print(f"  YA ESTABA  {codigo}")

            print("\n[3] Costo unitario resultante:")
            cur.execute(
                """
                SELECT codigo, ROUND(costo_total_unitario, 2)
                FROM vista_costo_sku
                WHERE codigo = ANY(%s) ORDER BY codigo
                """,
                (SKUS_CON_ETIQUETA,)
            )
            for codigo, costo in cur.fetchall():
                print(f"  {codigo:26s} ${costo}")

        print(f"\nAgregados: {agregados} · ya estaban: {ya_estaban}")
        if no_encontrados:
            print("\nATENCION — SKU no encontrados (revisar el codigo exacto):")
            for c in no_encontrados:
                print(f"  - {c}")

    except psycopg2.Error as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Correr la migración**

Run: `python scripts/migrate_etiqueta_botella.py`

Expected — el paso [3] debe imprimir exactamente:

```
  CREAM-ALE-BOT-330-C12      $891.22
  SCOTCH-ALE-BOT-330-C12     $918.93
```

Si los montos no calzan, **detenerse**: significa que el BOM de envasado tenía algo distinto de lo esperado. No continuar con las demás tareas.

- [ ] **Step 3: Verificar que es idempotente**

Run: `python scripts/migrate_etiqueta_botella.py`

Expected: ambos SKU dicen `YA ESTABA`, y los costos del paso [3] son **los mismos** ($891.22 / $918.93). Correrla dos veces no puede sumar la etiqueta dos veces.

- [ ] **Step 4: Confirmar que la suite sigue verde**

Run: `python -m pytest -q`
Expected: sin fallos nuevos. (`test_negocio_costos.py` usa cursores falsos, así que no lo afecta el cambio de datos.)

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_etiqueta_botella.py
git commit -m "Suma la etiqueta al costo de la botella de 330cc

Cada etiqueta cuesta \$230 con IVA (\$193,28 neto), una por botella, y
no estaba en el BOM de envasado. Sin ella la Cream salia a \$698 y la
Scotch a \$726; el costo real es \$891 y \$919."
```

---

### Task 2: Leer una línea de factura — clase, formato y cerveza

Base del algoritmo: dada una línea de `productos`, decidir **qué es** (cerveza, logística o pass-through), **de qué formato** y **de qué cerveza**. Sin esto no se puede atribuir nada.

**Files:**
- Create: `app/negocio/precios_venta.py`
- Create: `tests/test_negocio_precios.py`

**Interfaces:**
- Consumes: nada.
- Produces, para la Tarea 3:
  - `_norm(s) -> str`
  - `_clase(nombre_norm) -> "cerveza" | "logistica" | "pass_through"`
  - `_familia_y_capacidad(nombre_norm) -> (familia|None, capacidad_ml|None)` — familia es `"barril"`, `"botella"` o `"lata"`.
  - `_detectar_cerveza(nombre_norm, recetas) -> str|None` — `recetas` es una lista de nombres tal como vienen de `recetas.nombre_cerveza`.
  - `clave_formato(familia, capacidad_ml) -> str|None` — `"barril 30L"` para todo barril; `"botella 330"`, `"lata 470"` para el resto.
  - `clave_formato_desde_nombre(nombre) -> str|None` — la usa la Tarea 5 sobre `formatos.nombre`.
  - Constante `LITROS_BARRIL_REFERENCIA = 30.0`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_negocio_precios.py`:

```python
# tests/test_negocio_precios.py
from app.negocio import precios_venta as pv

RECETAS = ["Cream Ale", "Scotch Ale", "Stout Café/Cacao", "Wee Heavy Pistacho"]


# --- Clase de la linea ---

def test_la_logistica_se_reconoce_en_todas_sus_variantes():
    # El productor la escribe de 23 formas distintas en el historico.
    for nombre in ["logistica", "logistica cream ale", "logistic",
                   "logistica 30l", "logistica barril 25l"]:
        assert pv._clase(nombre) == "logistica", nombre


def test_el_envase_pet_es_pass_through():
    # Es el costo del envase desechable traspasado al cliente, sin margen.
    for nombre in ["barril pet 30l", "pet 20l", "barriles pet 30l"]:
        assert pv._clase(nombre) == "pass_through", nombre


def test_el_co2_es_pass_through():
    # La schopera y el cilindro son de Zigurat: la recarga se compra en Clean
    # Ice y se cobra al cliente a costo. No es venta de cerveza.
    for nombre in ["9 kg co2", "carga co2", "recarga co2 9 kg", "co2 9kg"]:
        assert pv._clase(nombre) == "pass_through", nombre


def test_un_barril_de_cerveza_es_cerveza():
    assert pv._clase("barril 30l cream ale") == "cerveza"


# --- Familia y capacidad ---

def test_capacidad_de_barril_y_botella():
    assert pv._familia_y_capacidad("barril 30l cream ale") == ("barril", 30000)
    assert pv._familia_y_capacidad("barril 25l cream ale") == ("barril", 25000)
    assert pv._familia_y_capacidad("botella 330cc cream ale") == ("botella", 330)
    assert pv._familia_y_capacidad("lata 470 cc sour berries") == ("lata", 470)


def test_tolera_la_errata_baril():
    # "Baril 30L Stout Cafe" aparece tal cual en los folios 4286 y 4518.
    assert pv._familia_y_capacidad("baril 30l stout cafe") == ("barril", 30000)


def test_tolera_la_errata_330c():
    # "Botella 330c Cream Ale", folio 4732.
    assert pv._familia_y_capacidad("botella 330c cream ale") == ("botella", 330)


def test_barril_sin_capacidad_escrita_se_asume_de_30l():
    assert pv._familia_y_capacidad("barril cream ale") == ("barril", 30000)


def test_linea_sin_familia_reconocible():
    assert pv._familia_y_capacidad("carga co2") == (None, None)


# --- Cerveza ---

def test_detecta_la_cerveza_por_nombre_completo():
    assert pv._detectar_cerveza("barril 30l cream ale", RECETAS) == "Cream Ale"
    assert pv._detectar_cerveza("botella 330cc scotch ale", RECETAS) == "Scotch Ale"


def test_detecta_la_cerveza_con_el_nombre_a_medias():
    # La factura casi nunca escribe el nombre completo de la receta.
    assert pv._detectar_cerveza("barril 30l stout cafe/ca", RECETAS) == "Stout Café/Cacao"
    assert pv._detectar_cerveza("barril 30l wee heavy", RECETAS) == "Wee Heavy Pistacho"


def test_gana_la_receta_que_calza_en_mas_palabras():
    # "ale" esta en Cream Ale y en Scotch Ale: debe ganar la que ademas
    # aporta "cream".
    assert pv._detectar_cerveza("barril 30l cream ale", RECETAS) == "Cream Ale"


def test_sin_cerveza_identificable_devuelve_none():
    # Empate: "ale" calza igual con Cream Ale y Scotch Ale.
    assert pv._detectar_cerveza("barril 30l ale", RECETAS) is None
    # RIS no esta en el catalogo de recetas.
    assert pv._detectar_cerveza("barril 30l ris", RECETAS) is None


# --- Clave de formato ---

def test_todos_los_barriles_comparten_clave():
    # Un "Barril 25L" es el barril de 30L con menos cerveza adentro, no otro
    # formato: el precio se normaliza y la clave es la misma.
    assert pv.clave_formato("barril", 30000) == "barril 30L"
    assert pv.clave_formato("barril", 25000) == "barril 30L"


def test_clave_de_botella_y_lata_lleva_su_capacidad():
    assert pv.clave_formato("botella", 330) == "botella 330"
    assert pv.clave_formato("lata", 470) == "lata 470"


def test_clave_desde_el_nombre_de_formato_del_catalogo():
    # Los nombres vienen de la tabla `formatos` y los usa margenes().
    assert pv.clave_formato_desde_nombre("Barril 30L acero") == "barril 30L"
    assert pv.clave_formato_desde_nombre("Barril 30L PET") == "barril 30L"
    assert pv.clave_formato_desde_nombre("Botella 330ml") == "botella 330"
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_negocio_precios.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.negocio.precios_venta'`

- [ ] **Step 3: Escribir el módulo**

Crear `app/negocio/precios_venta.py`:

```python
"""Precio de venta real por formato, deducido de las facturas (solo lectura).

No existe una lista de precios en la base: el precio real de un barril o una
botella es la SUMA de la linea de producto mas la linea de logistica que le
corresponde (estructura de doble linea, ver CLAUDE.md). Este modulo reconstruye
esa suma leyendo `ventas` + `productos`.

Vive aparte de costos.py a proposito: costos.py habla con la capa de costos
(recetas, insumos, SKU) y esto habla con la capa de ventas. La dependencia va en
un solo sentido (costos.py importa este modulo, nunca al reves), por eso `_norm`
esta duplicado en ambos en vez de compartirse.
"""
import re
import unicodedata

# Todos los barriles son de 30L. Cuando los ultimos litros del fermentador no
# alcanzan a llenar uno, se despacha ese mismo barril con 20 o 25 litros y se
# factura como "Barril 25L": el precio escala con los litros. Por eso el precio
# se normaliza a este tamaño y queda UNA serie por cerveza en vez de tres.
LITROS_BARRIL_REFERENCIA = 30.0
CAPACIDAD_BARRIL_ESTANDAR_ML = 30000

# El envase PET es el costo del envase desechable traspasado al cliente.
_RE_PET = re.compile(r"^(barril(es)?\s+)?pet\b")

# Primer numero seguido de una unidad de volumen. El orden de la alternancia
# importa: las unidades largas van primero para que "ml" no se lea como "l".
# "cc?" cubre la errata "330c" del folio 4732.
_RE_CAPACIDAD = re.compile(r"(\d+(?:[.,]\d+)?)\s*(litros?|lts|lt|ml|cc?|l)\b")

# "barr?il" tolera la errata "Baril" (folios 4286 y 4518).
_FAMILIAS = [
    ("barril", re.compile(r"\bbarr?il(es)?\b")),
    ("botella", re.compile(r"\bbotellas?\b")),
    ("lata", re.compile(r"\blatas?\b")),
]

_RE_PALABRAS = re.compile(r"[a-z0-9]+")


def _norm(s):
    """Minusculas, sin tildes, espacios simples."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _clase(nombre_norm):
    """Que es esta linea de la factura.

    - "logistica": desglose tributario, es parte del precio de la cerveza.
    - "pass_through": envase PET o carga de CO2, costo traspasado sin margen.
    - "cerveza": todo lo demas.
    """
    if "logist" in nombre_norm:
        return "logistica"
    if _RE_PET.match(nombre_norm) or "co2" in nombre_norm:
        return "pass_through"
    return "cerveza"


def _familia_y_capacidad(nombre_norm):
    """(familia, capacidad_ml) de una linea, o (None, None) si no se reconoce.

    Un barril sin capacidad escrita se asume de 30L: es el estandar, y los de
    20 o 25 siempre la escriben porque justamente son la excepcion.
    """
    familia = None
    for nombre_familia, patron in _FAMILIAS:
        if patron.search(nombre_norm):
            familia = nombre_familia
            break
    if familia is None:
        return None, None

    m = _RE_CAPACIDAD.search(nombre_norm)
    if m:
        valor = float(m.group(1).replace(",", "."))
        unidad = m.group(2)
        capacidad_ml = valor * 1000 if unidad.startswith("l") else valor
        return familia, int(round(capacidad_ml))
    if familia == "barril":
        return familia, CAPACIDAD_BARRIL_ESTANDAR_ML
    return familia, None


def _detectar_cerveza(nombre_norm, recetas):
    """Que cerveza nombra esta linea, o None.

    La factura casi nunca escribe el nombre completo de la receta ("Barril 30L
    Stout cafe/ca" para "Stout Café/Cacao"), asi que se cuenta cuantas palabras
    de la receta aparecen en la linea y gana la que calza en mas. Un empate
    devuelve None: preferimos no atribuir antes que atribuir mal.
    """
    palabras_linea = set(_RE_PALABRAS.findall(nombre_norm))
    mejor, mejor_puntaje, empatada = None, 0, False
    for receta in recetas:
        palabras = _RE_PALABRAS.findall(_norm(receta))
        puntaje = sum(1 for p in palabras if p in palabras_linea)
        if puntaje > mejor_puntaje:
            mejor, mejor_puntaje, empatada = receta, puntaje, False
        elif puntaje == mejor_puntaje and puntaje > 0:
            empatada = True
    if mejor_puntaje == 0 or empatada:
        return None
    return mejor


def clave_formato(familia, capacidad_ml):
    """Clave con que se agrupa un precio. Todos los barriles comparten clave
    porque su precio ya viene normalizado a 30L."""
    if familia is None:
        return None
    if familia == "barril":
        return "barril 30L"
    if capacidad_ml is None:
        return None
    return f"{familia} {int(capacidad_ml)}"


def clave_formato_desde_nombre(nombre):
    """Clave de formato a partir de un nombre suelto. La usa margenes() sobre
    los nombres de la tabla `formatos` ("Barril 30L acero", "Botella 330ml")."""
    familia, capacidad_ml = _familia_y_capacidad(_norm(nombre))
    return clave_formato(familia, capacidad_ml)
```

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `python -m pytest tests/test_negocio_precios.py -q`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add app/negocio/precios_venta.py tests/test_negocio_precios.py
git commit -m "Lee el formato y la cerveza de una linea de factura

Primera mitad del calculo de precio real: distingue cerveza, logistica y
pass-through (PET y CO2), saca familia y capacidad tolerando las erratas
que trae el historico (Baril, 330c), e identifica la cerveza aunque la
factura escriba el nombre a medias.

Todos los barriles comparten clave de formato: un Barril 25L es el de
30L con menos cerveza adentro, no otro producto."
```

---

### Task 3: Atribuir la logística y calcular el precio

El corazón del algoritmo. La logística es **la mitad del precio**, así que atribuirla mal es equivocarse por el doble.

**Files:**
- Modify: `app/negocio/precios_venta.py`
- Modify: `tests/test_negocio_precios.py`

**Interfaces:**
- Consumes: todo lo de la Tarea 2.
- Produces, para la Tarea 5:
  ```python
  precios_por_formato(cur, dias=None) -> {
      "precios": [
          {"cerveza": str, "formato": str,
           "precio_ultimo": float, "fecha_ultimo": date, "folio_ultimo": int,
           "precio_promedio": float, "n_facturas": int},
          ...
      ],
      "descartadas": {"familia_mixta": int, "residual_negativo": int,
                      "sin_base_de_reparto": int},
  }
  ```
  `dias` limita el promedio a los últimos N días (`None` = todo el histórico). `precio_ultimo` siempre sale del histórico completo.
  `descartadas` trae **solo los motivos que ocurrieron** (dict vacío si no se descartó nada).
  Emite **exactamente dos** `cur.execute()`, en este orden: recetas, luego líneas.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `tests/test_negocio_precios.py`:

```python
# ─── Atribucion de logistica y precio ────────────────────────────────────────
from datetime import date


class FakeCursor:
    """Devuelve una lista de filas distinta por cada execute(), en orden.
    precios_por_formato hace exactamente dos consultas: recetas y luego lineas."""

    def __init__(self, *respuestas):
        self._respuestas = list(respuestas)
        self._actual = []
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._actual = self._respuestas.pop(0) if self._respuestas else []

    def fetchall(self):
        return self._actual


def _linea(folio, neto, nombre, cantidad, total, fecha=date(2026, 7, 1)):
    return {"folio": folio, "fecha": fecha, "monto_neto": neto,
            "nombre_producto": nombre, "cantidad": cantidad, "total_linea": total}


def _cursor(lineas, recetas=RECETAS):
    return FakeCursor([{"nombre_cerveza": r} for r in recetas], lineas)


def _precio(resultado, cerveza, formato):
    for fila in resultado["precios"]:
        if fila["cerveza"] == cerveza and fila["formato"] == formato:
            return fila["precio_ultimo"]
    raise AssertionError(
        f"no hay precio para {cerveza} / {formato}: {resultado['precios']}")


def test_logistica_nombrada_se_cruza_por_cantidad():
    """Folio 4694: la logistica nombrada lleva la misma cantidad que su barril,
    asi que la atribucion es exacta. 20.000 + 35.370 = 55.370, el precio
    confirmado por el productor."""
    r = pv.precios_por_formato(_cursor([
        _linea(4694, 110740, "Barril 30L Cream Ale", 2, 40000),
        _linea(4694, 110740, "Logistica Cream Ale", 2, 70740),
    ]))
    assert _precio(r, "Cream Ale", "barril 30L") == 55370.0


def test_residual_se_reparte_y_el_co2_no_recibe_nada():
    """Folio 4736: la linea "Logistica" a secas no queda guardada en productos,
    asi que aparece como residual (neto menos las lineas). La carga de CO2 es
    pass-through y NO entra en la base de reparto: si entrara, el barril daria
    menos de 55.370."""
    r = pv.precios_por_formato(_cursor([
        _linea(4736, 181110, "Barril 30L Cream Ale", 3, 60000),
        _linea(4736, 181110, "Carga CO2", 1, 15000),
    ]))
    assert _precio(r, "Cream Ale", "barril 30L") == 55370.0


def test_barril_parcial_se_normaliza_a_30l():
    """Folio 4672: un barril de 25L a 46.141 es el mismo precio que uno de 30L
    a 55.370, solo que con menos cerveza adentro."""
    r = pv.precios_por_formato(_cursor([
        _linea(4672, 61141, "Barril 25L Cream Ale", 1, 16666),
        _linea(4672, 61141, "Recarga CO2 9 kg", 1, 15000),
    ]))
    assert abs(_precio(r, "Cream Ale", "barril 30L") - 55370.0) < 2.0


def test_logistica_que_nombra_la_capacidad_y_no_la_cerveza():
    """Folio 4572: "Logistica Barril 25L" no nombra cerveza, pero identifica sin
    ambiguedad al unico barril de 25L. El resto de la logistica (residual) se
    reparte entre los cuatro barriles llenos. Los tres precios convergen al
    mismo valor: es un cliente con descuento."""
    r = pv.precios_por_formato(_cursor([
        _linea(4572, 231209, "Barril 30L Cream Ale", 2, 30002),
        _linea(4572, 231209, "Barril 30L Scotch Ale", 2, 30000),
        _linea(4572, 231209, "Barril 25L Cream Ale", 1, 12500),
        _linea(4572, 231209, "Logistica Barril 25L", 1, 27363),
    ]))
    assert abs(_precio(r, "Cream Ale", "barril 30L") - 47836.0) < 2.0
    assert abs(_precio(r, "Scotch Ale", "barril 30L") - 47836.0) < 2.0


def test_botellas_reparten_el_residual_por_unidad():
    """Folio 4743: dos cervezas distintas a distinto precio de producto, pero la
    logistica es la misma ($900 por botella) y por eso el productor puso una
    sola linea. Scotch 400+900 y Stout 600+900."""
    r = pv.precios_por_formato(_cursor([
        _linea(4743, 33600, "Botella 330cc Scotch Ale", 12, 4800),
        _linea(4743, 33600, "Botella 330cc Stout Cafe", 12, 7200),
    ]))
    assert _precio(r, "Scotch Ale", "botella 330") == 1300.0
    assert _precio(r, "Stout Café/Cacao", "botella 330") == 1500.0


def test_el_envase_pet_no_recibe_logistica():
    """El PET es pass-through: la logistica del barril no se diluye en el."""
    r = pv.precios_por_formato(_cursor([
        _linea(4664, 70697, "Barril 30L Cream Ale", 1, 20000),
        _linea(4664, 70697, "Barril Pet 30L", 1, 15327),
        _linea(4664, 70697, "Logistica Cream Ale", 1, 35370),
    ]))
    assert _precio(r, "Cream Ale", "barril 30L") == 55370.0


def test_factura_de_familia_mixta_se_descarta():
    """Barriles y botellas con una sola logistica sin nombrar: no hay forma de
    saber cuanto le toca a cada uno. Preferimos no responder antes que inventar
    un margen. Hoy no ocurre en ninguna factura del historico."""
    r = pv.precios_por_formato(_cursor([
        _linea(4999, 100000, "Barril 30L Cream Ale", 1, 20000),
        _linea(4999, 100000, "Botella 330cc Cream Ale", 12, 4800),
    ]))
    assert r["precios"] == []
    assert r["descartadas"]["familia_mixta"] == 1


def test_la_consulta_excluye_las_facturas_con_nota_de_credito():
    """Una factura anulada por NC no dice a cuanto se vende, y una NC parcial
    rebajaria el neto sin tocar las lineas de productos (el residual saldria
    corto). Se filtran en el SQL."""
    cur = _cursor([])
    pv.precios_por_formato(cur)
    sql_lineas = cur.sql[1]
    assert "monto_neto_ajustado IS NULL" in sql_lineas
    assert "tipo_documento != 61" in sql_lineas


def test_precio_ultimo_y_promedio_se_calculan_por_separado():
    """El ultimo es el precio vigente; el promedio revela los descuentos."""
    r = pv.precios_por_formato(_cursor([
        _linea(4691, 95672, "Barril 30L Cream Ale", 2, 30000, date(2026, 5, 27)),
        _linea(4691, 95672, "Logistica Cream Ale", 2, 65672, date(2026, 5, 27)),
        _linea(4736, 166110, "Barril 30L Cream Ale", 3, 60000, date(2026, 7, 15)),
    ]))
    fila = r["precios"][0]
    assert fila["precio_ultimo"] == 55370.0
    assert fila["folio_ultimo"] == 4736
    assert fila["n_facturas"] == 2
    assert 47836.0 < fila["precio_promedio"] < 55370.0
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_negocio_precios.py -q`
Expected: 9 FAIL con `AttributeError: module 'app.negocio.precios_venta' has no attribute 'precios_por_formato'`. Los 16 de la Tarea 2 siguen en verde.

- [ ] **Step 3: Implementar la atribución**

Primero, agregar los imports que faltan **al principio** del archivo, junto a `import re`:

```python
from collections import defaultdict
from datetime import date, timedelta
```

Y agregar al final de `app/negocio/precios_venta.py`:

```python
# Tolerancia en pesos para el residual: los montos del SII son enteros y
# arrastran redondeos de un peso.
TOLERANCIA_PESOS = 1.0

SQL_RECETAS = "SELECT nombre_cerveza FROM recetas"

# Solo facturas de venta sin nota de credito aplicada: una anulada no dice a
# cuanto se vende, y una parcial rebajaria el neto sin tocar las lineas de
# `productos`, dejando el residual corto.
SQL_LINEAS = """
    SELECT v.folio, v.fecha, v.monto_neto,
           p.nombre_producto, p.cantidad, p.total_linea
    FROM ventas v
    JOIN productos p ON p.folio = v.folio
                    AND p.tipo_documento = v.tipo_documento
    WHERE v.tipo_documento != 61
      AND v.monto_neto_ajustado IS NULL
      AND v.monto_neto > 0
    ORDER BY v.fecha, v.folio, p.id
"""


def _leer_linea(fila, recetas):
    """Convierte una fila cruda en el registro con que trabaja el algoritmo."""
    nombre_norm = _norm(fila["nombre_producto"])
    clase = _clase(nombre_norm)
    familia, capacidad_ml = _familia_y_capacidad(nombre_norm)
    return {
        "nombre_norm": nombre_norm,
        "clase": clase,
        "familia": familia,
        "capacidad_ml": capacidad_ml,
        "cerveza": _detectar_cerveza(nombre_norm, recetas) if clase != "pass_through" else None,
        "cantidad": float(fila["cantidad"] or 0),
        "total_linea": float(fila["total_linea"] or 0),
        "logistica": 0.0,
    }


def _atribuir_nombrada(logisticas, cervezas):
    """Primera pasada: cada logistica que identifique UNA sola linea de cerveza
    le entrega su monto. Devuelve las que quedaron sin atribuir.

    El selector es la cerveza que nombra y/o la capacidad que nombra. La
    capacidad es necesaria por casos como "Logistica Barril 25L" (folio 4572),
    que no nombra cerveza pero senala sin ambiguedad al unico barril de 25L.
    """
    sin_atribuir = []
    for log in logisticas:
        _familia, capacidad = _familia_y_capacidad(log["nombre_norm"])
        candidatas = [
            c for c in cervezas
            if (log["cerveza"] is None or c["cerveza"] == log["cerveza"])
            and (capacidad is None or c["capacidad_ml"] == capacidad)
        ]
        # Un selector vacio calzaria con todas: eso no identifica nada.
        identifica_algo = log["cerveza"] is not None or capacidad is not None
        if identifica_algo and len(candidatas) == 1:
            candidatas[0]["logistica"] += log["total_linea"]
        else:
            sin_atribuir.append(log)
    return sin_atribuir


def _repartir_residual(residual, pendientes):
    """Segunda pasada: la logistica sin nombrar se reparte entre las lineas que
    no recibieron ninguna. Devuelve el motivo de descarte, o None si salio bien.

    En barriles se reparte POR LITRO, porque un barril parcial pago menos
    logistica en la misma proporcion en que lleva menos cerveza. En botellas y
    latas se reparte POR UNIDAD: son todas del mismo tamaño y asi una errata de
    capacidad ("33cc" por "330cc") no deforma el reparto.
    """
    if residual <= TOLERANCIA_PESOS:
        return None
    if not pendientes:
        return "sin_base_de_reparto"
    familias = {c["familia"] for c in pendientes}
    if len(familias) > 1:
        return "familia_mixta"

    familia = familias.pop()
    if familia == "barril":
        pesos = [c["cantidad"] * (c["capacidad_ml"] or 0) / 1000.0 for c in pendientes]
    else:
        pesos = [c["cantidad"] for c in pendientes]
    total = sum(pesos)
    if total <= 0:
        return "sin_base_de_reparto"
    for linea, peso in zip(pendientes, pesos):
        linea["logistica"] += residual * peso / total
    return None


def _precio_de_linea(linea):
    """Precio neto por unidad, normalizado a barril de 30L cuando corresponde."""
    if linea["cantidad"] <= 0:
        return None
    precio = (linea["total_linea"] + linea["logistica"]) / linea["cantidad"]
    if linea["familia"] == "barril":
        litros = (linea["capacidad_ml"] or 0) / 1000.0
        if litros <= 0:
            return None
        precio *= LITROS_BARRIL_REFERENCIA / litros
    return precio


def _procesar_factura(filas, recetas, descartadas):
    """Devuelve las muestras de precio de una factura: (cerveza, formato, precio,
    unidades). Una factura ambigua no aporta ninguna y se cuenta aparte."""
    lineas = [_leer_linea(f, recetas) for f in filas]
    cervezas = [l for l in lineas if l["clase"] == "cerveza" and l["familia"]]
    logisticas = [l for l in lineas if l["clase"] == "logistica"]

    sin_atribuir = _atribuir_nombrada(logisticas, cervezas)

    # El residual es la linea "Logistica" exacta, que parse_dte no guarda en
    # `productos` (ITEMS_NO_CATALOGO), mas las logisticas que no identificaron
    # a nadie ("Logistic", "Logistica Cream/Scotch").
    neto = float(filas[0]["monto_neto"] or 0)
    residual = neto - sum(l["total_linea"] for l in lineas)
    residual += sum(l["total_linea"] for l in sin_atribuir)

    if residual < -TOLERANCIA_PESOS:
        descartadas["residual_negativo"] += 1
        return []

    pendientes = [c for c in cervezas if c["logistica"] == 0.0]
    motivo = _repartir_residual(residual, pendientes)
    if motivo:
        descartadas[motivo] += 1
        return []

    muestras = []
    for linea in cervezas:
        if not linea["cerveza"]:
            continue                      # no esta en el catalogo de recetas
        clave = clave_formato(linea["familia"], linea["capacidad_ml"])
        precio = _precio_de_linea(linea)
        if clave and precio is not None:
            muestras.append((linea["cerveza"], clave, precio, linea["cantidad"]))
    return muestras


def precios_por_formato(cur, dias=None):
    """Precio neto de venta por (cerveza, formato), deducido de las facturas.

    `dias` limita el PROMEDIO a los ultimos N dias (None = todo el historico);
    `precio_ultimo` sale siempre del historico completo.
    """
    cur.execute(SQL_RECETAS)
    recetas = [r["nombre_cerveza"] for r in cur.fetchall()]

    cur.execute(SQL_LINEAS)
    por_factura = defaultdict(list)
    for fila in cur.fetchall():
        por_factura[fila["folio"]].append(fila)

    descartadas = defaultdict(int)
    # Una factura puede traer la misma cerveza en dos lineas (un barril lleno y
    # uno parcial): se promedian ponderadas por unidades para que cada factura
    # aporte UNA muestra por formato.
    muestras = defaultdict(list)
    for folio, filas in por_factura.items():
        for cerveza, clave, precio, unidades in _procesar_factura(filas, recetas, descartadas):
            muestras[(cerveza, clave)].append(
                {"folio": folio, "fecha": filas[0]["fecha"], "precio": precio,
                 "unidades": unidades})

    corte = (date.today() - timedelta(days=dias)) if dias else None
    precios = []
    for (cerveza, clave), lista in muestras.items():
        por_folio = defaultdict(list)
        for m in lista:
            por_folio[m["folio"]].append(m)

        agregadas = []
        for folio, ms in por_folio.items():
            unidades = sum(m["unidades"] for m in ms) or 1.0
            precio = sum(m["precio"] * m["unidades"] for m in ms) / unidades
            agregadas.append({"folio": folio, "fecha": ms[0]["fecha"], "precio": precio})

        agregadas.sort(key=lambda m: (m["fecha"], m["folio"]))
        ultima = agregadas[-1]
        ventana = [m for m in agregadas if corte is None or m["fecha"] >= corte] or agregadas

        precios.append({
            "cerveza": cerveza,
            "formato": clave,
            "precio_ultimo": round(ultima["precio"], 2),
            "fecha_ultimo": ultima["fecha"],
            "folio_ultimo": ultima["folio"],
            "precio_promedio": round(sum(m["precio"] for m in ventana) / len(ventana), 2),
            "n_facturas": len(ventana),
        })

    precios.sort(key=lambda p: (p["cerveza"], p["formato"]))
    return {"precios": precios, "descartadas": dict(descartadas)}
```

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `python -m pytest tests/test_negocio_precios.py -q`
Expected: PASS, 25 tests.

- [ ] **Step 5: Correr la suite completa**

Run: `python -m pytest -q`
Expected: sin fallos nuevos.

- [ ] **Step 6: Commit**

```bash
git add app/negocio/precios_venta.py tests/test_negocio_precios.py
git commit -m "Deduce el precio de venta real desde las facturas

El precio de un barril o una botella es la linea de producto mas la de
logistica. Cuando el productor la desglosa por estilo, se cruza por
cantidad; cuando pone una sola linea, se reparte entre las unidades que
no recibieron ninguna: por litro en barriles (un barril parcial pago
menos logistica en la misma proporcion) y por unidad en botellas.

PET y CO2 quedan fuera del reparto: son costos traspasados al cliente,
no cerveza. Si una factura mezcla barriles y botellas sin desglosar la
logistica, no se usa y se cuenta aparte: no hay forma de saber cuanto
le toca a cada uno."
```

---

### Task 4: Verificar el algoritmo contra la base real

Los tests usan cursores falsos y solo prueban lo que el autor imaginó. Esta tarea lo corre contra las 812 facturas de verdad. **Es la que decide si el algoritmo sirve.**

**Files:**
- Ninguno permanente (verificación manual).

**Interfaces:**
- Consumes: `precios_por_formato` de la Tarea 3.
- Produces: confianza. Si algo no calza, se arregla en `precios_venta.py` **antes** de seguir.

- [ ] **Step 1: Correr el algoritmo contra la BD real**

Run:

```bash
python -c "
import sys; sys.path.insert(0,'.')
from app.config import DB_URL
from app.negocio import precios_venta
import psycopg2
from psycopg2.extras import RealDictCursor
c = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
r = precios_venta.precios_por_formato(c.cursor())
for p in r['precios']:
    print('%-22s %-14s ultimo \$%-10s prom \$%-10s (%s facturas)' % (
        p['cerveza'], p['formato'], p['precio_ultimo'], p['precio_promedio'], p['n_facturas']))
print()
print('descartadas:', r['descartadas'])
"
```

Expected — deben aparecer, entre otras, estas filas (el precio último puede haber cambiado si se emitieron facturas nuevas; lo que no puede cambiar es el orden de magnitud):

- `Cream Ale barril 30L` con precio último alrededor de **$55.370**
- `Scotch Ale barril 30L` alrededor de **$55.370**
- `Cream Ale botella 330` alrededor de **$1.301**
- `Scotch Ale botella 330` alrededor de **$1.300**

Y `descartadas` debe ser **`{}` o muy cercano**: si descarta decenas de facturas, el algoritmo no está entendiendo el formato real y hay que revisarlo antes de seguir.

- [ ] **Step 2: Verificar factura por factura los cinco casos que fijaron el diseño**

Run:

```bash
python -c "
import sys; sys.path.insert(0,'.')
from app.config import DB_URL
from app.negocio import precios_venta as pv
import psycopg2
from psycopg2.extras import RealDictCursor

ESPERADO = {
    4694: ('Cream Ale', 'barril 30L', 55370),
    4736: ('Cream Ale', 'barril 30L', 55370),
    4672: ('Cream Ale', 'barril 30L', 55370),
    4572: ('Cream Ale', 'barril 30L', 47836),
    4743: ('Scotch Ale', 'botella 330', 1300),
}
c = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
cur = c.cursor()
cur.execute(pv.SQL_RECETAS)
recetas = [r['nombre_cerveza'] for r in cur.fetchall()]
ok = True
for folio, (cerveza, formato, esperado) in ESPERADO.items():
    cur.execute('''SELECT v.folio, v.fecha, v.monto_neto, p.nombre_producto,
                          p.cantidad, p.total_linea
                   FROM ventas v JOIN productos p
                     ON p.folio=v.folio AND p.tipo_documento=v.tipo_documento
                   WHERE v.folio=%s AND v.tipo_documento!=61 ORDER BY p.id''', (folio,))
    filas = cur.fetchall()
    from collections import defaultdict
    d = defaultdict(int)
    muestras = pv._procesar_factura(filas, recetas, d)
    hallado = [m[2] for m in muestras if m[0]==cerveza and m[1]==formato]
    marca = 'OK ' if hallado and abs(hallado[0]-esperado) < 3 else 'MAL'
    if marca == 'MAL': ok = False
    print('%s folio %s  %s %s  esperado \$%s  obtenido %s' % (
        marca, folio, cerveza, formato, esperado,
        ('\$%.0f' % hallado[0]) if hallado else 'NADA'))
print()
print('TODOS OK' if ok else 'HAY CASOS MALOS — revisar precios_venta.py antes de seguir')
"
```

Expected: las cinco líneas dicen `OK` y la última dice `TODOS OK`.

Si alguna dice `MAL`, **parar**. Arreglar `app/negocio/precios_venta.py`, agregar el caso a `tests/test_negocio_precios.py` para que no vuelva a romperse, y repetir.

- [ ] **Step 3: Commit (solo si hubo arreglos)**

Si los pasos 1 y 2 pasaron sin tocar nada, no hay nada que commitear — seguir a la Tarea 5. Si hubo arreglos:

```bash
git add app/negocio/precios_venta.py tests/test_negocio_precios.py
git commit -m "Ajusta la deduccion de precios contra las facturas reales"
```

---

### Task 5: `margenes()` usa el precio deducido

Ahora la botella deja de dar "sin precio de venta confirmado".

**Files:**
- Modify: `app/negocio/costos.py`
- Modify: `tests/test_negocio_costos.py`

**Interfaces:**
- Consumes: `precios_por_formato(cur, dias)` y `clave_formato_desde_nombre(nombre)` de las Tareas 2 y 3.
- Produces, para la Tarea 6 — `margenes()` devuelve por SKU:
  ```python
  {"codigo", "cerveza", "formato", "costo_total",
   "precio_venta", "margen", "margen_pct",
   "origen": "facturas" | "lista" | None,
   "precio_promedio": float|None, "n_facturas": int|None}
  ```

- [ ] **Step 1: Escribir los tests que fallan**

En `tests/test_negocio_costos.py`, **reemplazar** `test_margenes_botella_sin_precio_queda_none` por lo siguiente y dejar el resto del archivo intacto:

```python
class FakeCursorPrecios:
    """costos.margenes() consulta la vista de costos y, aparte,
    precios_venta.precios_por_formato() hace sus dos consultas. Este cursor
    responde en ese orden: costos, recetas, lineas de factura."""

    def __init__(self, filas_costos, lineas_factura, recetas=("Cream Ale",)):
        self._respuestas = [
            filas_costos,
            [{"nombre_cerveza": r} for r in recetas],
            lineas_factura,
        ]
        self._actual = []

    def execute(self, sql, params=None):
        self._actual = self._respuestas.pop(0) if self._respuestas else []

    def fetchall(self):
        return self._actual


BOTELLA_CREAM = {
    "codigo": "CREAM-330", "nombre_cerveza": "Cream Ale", "formato": "Botella 330ml",
    "costo_liquido_unitario": 626, "costo_envasado_unitario": 265,
    "costo_total_unitario": 891,
}


def test_margenes_botella_usa_el_precio_deducido_de_las_facturas():
    """Antes devolvia None porque la lista escrita a mano solo tenia barriles.
    Ahora el precio sale de la factura: 400 de producto + 900 de logistica."""
    from datetime import date
    lineas = [{"folio": 4743, "fecha": date(2026, 7, 22), "monto_neto": 15600,
               "nombre_producto": "Botella 330cc Cream Ale",
               "cantidad": 12, "total_linea": 4800}]
    r = costos.margenes(FakeCursorPrecios([BOTELLA_CREAM], lineas))
    assert r[0]["precio_venta"] == 1300.0
    assert r[0]["margen"] == 1300.0 - 891.0
    assert r[0]["origen"] == "facturas"
    assert r[0]["n_facturas"] == 1


def test_margenes_cae_a_la_lista_cuando_no_hay_facturas():
    """Un SKU sin ventas todavia: el precio confirmado por el productor sigue
    sirviendo de respaldo, pero marcado como tal."""
    barril = {"codigo": "CREAM-B30", "nombre_cerveza": "Cream Ale",
              "formato": "Barril 30L acero", "costo_liquido_unitario": 18000,
              "costo_envasado_unitario": 0, "costo_total_unitario": 18000}
    r = costos.margenes(FakeCursorPrecios([barril], []))
    assert r[0]["precio_venta"] == 55370.0
    assert r[0]["origen"] == "lista"


def test_margenes_sin_facturas_ni_lista_no_inventa_un_margen():
    lata = {"codigo": "SOUR-LATA", "nombre_cerveza": "Sour Berries",
            "formato": "Lata 470cc", "costo_liquido_unitario": 500,
            "costo_envasado_unitario": 200, "costo_total_unitario": 700}
    r = costos.margenes(FakeCursorPrecios([lata], [], recetas=("Sour Berries",)))
    assert r[0]["precio_venta"] is None
    assert r[0]["margen"] is None
    assert r[0]["origen"] is None
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_negocio_costos.py -q`
Expected: 3 FAIL — `KeyError: 'origen'` y precios en `None`.

- [ ] **Step 3: Reescribir `margenes()`**

En `app/negocio/costos.py`, actualizar el docstring del módulo y reemplazar la función `margenes` completa. **No tocar** `costos_sku`, `_norm`, `_precio_venta` ni `PRECIOS_VENTA_NETO`.

Agregar el import arriba, junto a `import unicodedata`:

```python
from app.negocio import precios_venta
```

Actualizar el docstring del módulo (las tres últimas líneas, que decían que para botellas no hay precio):

```python
"""Costos por SKU y márgenes (solo lectura).

Costos: misma consulta a vista_costo_sku que scripts/costo_sku.py.
Márgenes: cruza el costo total con el precio de venta REAL, deducido de las
facturas por app/negocio/precios_venta.py — cubre todos los formatos, no solo
los barriles. PRECIOS_VENTA_NETO quedó como respaldo para un SKU que todavía
no se ha vendido nunca.
"""
```

Reemplazar `margenes` por:

```python
# Ventana del promedio: mas atras el precio ya no es comparable (cambian los
# costos y la lista). El precio ULTIMO no usa ventana.
DIAS_PROMEDIO = 180


def margenes(cur, receta=None):
    """Margen por SKU = precio de venta − costo total.

    El precio sale de las facturas (fuente principal, refleja lo que realmente
    se cobró, descuentos incluidos). Si el SKU no se ha vendido nunca, cae al
    precio confirmado por el productor. Sin ninguno de los dos, el margen queda
    en None: nunca se inventa.
    """
    skus = costos_sku(cur, receta=receta)
    deducidos = precios_venta.precios_por_formato(cur, dias=DIAS_PROMEDIO)["precios"]
    por_clave = {(_norm(p["cerveza"]), p["formato"]): p for p in deducidos}

    salida = []
    for sku in skus:
        clave = precios_venta.clave_formato_desde_nombre(sku["formato"])
        ref = por_clave.get((_norm(sku["cerveza"]), clave)) if clave else None

        if ref:
            precio, origen = ref["precio_ultimo"], "facturas"
        else:
            precio = _precio_venta(sku["cerveza"], sku["formato"])
            origen = "lista" if precio is not None else None

        margen = margen_pct = None
        if precio is not None and sku["costo_total"] is not None:
            margen = float(precio) - sku["costo_total"]
            margen_pct = round(100 * margen / precio, 1) if precio else None

        salida.append({
            "codigo": sku["codigo"], "cerveza": sku["cerveza"], "formato": sku["formato"],
            "costo_total": sku["costo_total"],
            "precio_venta": float(precio) if precio is not None else None,
            "margen": margen, "margen_pct": margen_pct,
            "origen": origen,
            "precio_promedio": ref["precio_promedio"] if ref else None,
            "n_facturas": ref["n_facturas"] if ref else None,
        })
    return salida
```

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `python -m pytest tests/test_negocio_costos.py -q`
Expected: PASS.

- [ ] **Step 5: Verificar contra la base real**

Run:

```bash
python -c "
import sys; sys.path.insert(0,'.')
from app.config import DB_URL
from app.negocio import costos
import psycopg2
from psycopg2.extras import RealDictCursor
c = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
for m in costos.margenes(c.cursor()):
    print('%-22s %-18s costo \$%-9s precio \$%-10s margen \$%-9s %s%% [%s]' % (
        m['cerveza'], m['formato'], round(m['costo_total']),
        m['precio_venta'], m['margen'] and round(m['margen']),
        m['margen_pct'], m['origen']))
"
```

Expected — las dos botellas ya no salen sin precio, y dan aproximadamente:

```
Cream Ale    Botella 330ml   costo $891  precio $1301  margen $410  31.5% [facturas]
Scotch Ale   Botella 330ml   costo $919  precio $1300  margen $381  29.3% [facturas]
```

- [ ] **Step 6: Correr la suite completa y commitear**

Run: `python -m pytest -q`
Expected: sin fallos.

```bash
git add app/negocio/costos.py tests/test_negocio_costos.py
git commit -m "El margen deja de estar ciego fuera de los barriles

margenes() toma el precio de venta de las facturas en vez de la lista
escrita a mano, asi que la botella de 330cc ya tiene margen: \$410 la
Cream y \$381 la Scotch. La lista confirmada queda de respaldo para un
SKU que todavia no se ha vendido nunca."
```

---

### Task 6: Que el agente sepa usarlo

De nada sirve el cálculo si el modelo sigue improvisando SQL. **Esta es la causa raíz del síntoma original.**

**Files:**
- Modify: `app/agent/tools_negocio.py:165-181`
- Modify: `app/agent/system_prompt.py:39-51`
- Modify: `tests/test_tools_negocio.py`

**Interfaces:**
- Consumes: `margenes()` de la Tarea 5, con `origen`, `precio_promedio` y `n_facturas`.
- Produces: nada para tareas siguientes.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `tests/test_tools_negocio.py`:

```python
def test_la_tool_margenes_ya_no_dice_que_solo_cubre_barriles():
    """La descripcion es lo unico que el modelo lee antes de decidir si la usa.
    Mientras dijo 'solo barriles', ante una pregunta por botellas se iba a
    improvisar SQL sobre `productos` y agotaba sus pasos."""
    server, _names = build_negocio_server()
    import asyncio
    from mcp.types import ListToolsRequest
    handler = server.request_handlers[ListToolsRequest]
    res = asyncio.run(handler(ListToolsRequest()))
    descripciones = {t.name: t.description for t in res.root.tools}
    assert "solo barriles" not in descripciones["margenes"].lower()
    assert "botella" in descripciones["margenes"].lower()
```

Agregar al final de `tests/test_system_prompt.py`:

```python
def test_el_prompt_prohibe_calcular_precios_sobre_productos():
    """La linea `Logistica` exacta no se guarda en `productos`, asi que
    cualquier precio deducido con SQL a mano sale a la mitad. El agente del
    chat no lee CLAUDE.md: si la regla no esta aqui, no existe para el."""
    from app.agent.system_prompt import SYSTEM_PROMPT
    assert "NUNCA calcules un precio de venta con SQL" in SYSTEM_PROMPT
    assert "mcp__negocio__margenes" in SYSTEM_PROMPT
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_tools_negocio.py tests/test_system_prompt.py -q`
Expected: FAIL en `test_la_tool_margenes_ya_no_dice_que_solo_cubre_barriles` (la descripción todavía dice "solo barriles").

- [ ] **Step 3: Actualizar la tool `margenes`**

En `app/agent/tools_negocio.py`, reemplazar el bloque de la tool `margenes` (líneas 165-181) por:

```python
    @tool("margenes", "Margen por cerveza y formato: precio de venta real menos "
                      "costo unitario. Cubre barriles Y botellas. El precio se "
                      "deduce de las facturas emitidas. Opcional: filtrar por "
                      "receta.", {"receta": str})
    @_tool_seguro
    async def margenes(args):
        r = _con_cursor(costos_data.margenes, args.get("receta"))
        if not r:
            return _texto("Sin SKUs cargados.")
        lineas = []
        for m in r:
            if m["margen"] is None:
                lineas.append(f"- {m['cerveza']} {m['formato']}: costo "
                              f"{_pesos(m['costo_total'])} (aún sin ventas, "
                              f"así que no hay precio de venta conocido)")
                continue
            if m["origen"] == "facturas":
                respaldo = (f" [{m['n_facturas']} facturas; promedio "
                            f"{_pesos(m['precio_promedio'])}]")
            else:
                respaldo = " [precio de lista, este SKU aún no se ha vendido]"
            lineas.append(f"- {m['cerveza']} {m['formato']}: precio "
                          f"{_pesos(m['precio_venta'])} − costo "
                          f"{_pesos(m['costo_total'])} = margen "
                          f"{_pesos(m['margen'])} ({m['margen_pct']}%)" + respaldo)
        return _texto("\n".join(lineas))
```

- [ ] **Step 4: Agregar la regla al system prompt**

En `app/agent/system_prompt.py`, reemplazar la línea que dice
`- Costos y márgenes por SKU: mcp__negocio__costos_sku, mcp__negocio__margenes.`
por:

```
- Costos y márgenes por SKU: mcp__negocio__costos_sku, mcp__negocio__margenes.
  Cubren TODOS los formatos (barriles y botellas). Para costo, precio de venta
  o margen usa SIEMPRE estas dos: NUNCA calcules un precio de venta con SQL
  sobre `productos`. La línea "Logistica" a secas no se guarda en esa tabla, y
  la logística es la mitad del precio: cualquier precio deducido a mano desde
  `productos` te va a salir a la mitad del real.
```

- [ ] **Step 5: Correr los tests para verificar que pasan**

Run: `python -m pytest tests/test_tools_negocio.py tests/test_system_prompt.py -q`
Expected: PASS.

- [ ] **Step 6: Correr la suite completa y commitear**

Run: `python -m pytest -q`
Expected: sin fallos.

```bash
git add app/agent/tools_negocio.py app/agent/system_prompt.py tests/test_tools_negocio.py tests/test_system_prompt.py
git commit -m "Le dice al agente que los margenes ya cubren las botellas

La descripcion de la tool decia 'solo barriles', asi que ante una
pregunta por botellas el modelo la descartaba y se iba a improvisar SQL
sobre productos, donde la doble linea lo engaña. Ahora la tool cubre
todo formato y el prompt prohibe explicitamente deducir precios a mano."
```

---

### Task 7: Que el agente nunca más se quede mudo

Aunque el resto funcione, cualquier pregunta larga puede agotar los pasos. Hoy eso **bota todo el trabajo del turno** y devuelve una disculpa vacía.

**Files:**
- Modify: `app/agent/orchestrator.py:26` y `:284`
- Modify: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: nada.
- Produces: nada.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `tests/test_orchestrator.py`:

```python
def test_al_agotar_los_pasos_responde_con_lo_que_alcanzo_a_reunir(monkeypatch):
    """Antes devolvia una disculpa vacia y tiraba a la basura todo lo que el
    agente ya habia averiguado en el turno. Ahora hace una ultima llamada SIN
    herramientas para que cierre con lo que tenga.

    La tool que se pide a proposito no existe: asi el loop gira sin tocar la BD.
    """
    llamadas = []

    def mock_api(api_key, model, system, messages, tools=None):
        llamadas.append(tools)
        if tools:
            return {"choices": [{
                "message": {"role": "assistant", "content": None,
                            "tool_calls": [{"id": f"c{len(llamadas)}",
                                            "type": "function",
                                            "function": {"name": "mcp__inexistente__x",
                                                         "arguments": "{}"}}]},
                "finish_reason": "tool_calls"}]}
        return {"choices": [{
            "message": {"role": "assistant",
                        "content": "Alcancé a ver el costo. Me faltó el margen."},
            "finish_reason": "stop"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    texto, _sid = orchestrator.run("pregunta larga", Collector())

    assert texto == "Alcancé a ver el costo. Me faltó el margen."
    assert "límite de pasos" not in texto
    assert len(llamadas) == orchestrator.MAX_ITERACIONES + 1
    assert llamadas[-1] is None, "el turno de cierre va SIN herramientas"


def test_si_el_turno_de_cierre_falla_queda_el_mensaje_de_siempre(monkeypatch):
    """Red de seguridad: si la ultima llamada revienta, el usuario igual recibe
    una explicacion en vez de un string vacio."""
    def mock_api(api_key, model, system, messages, tools=None):
        if tools is None:
            raise RuntimeError("OpenRouter caido")
        return {"choices": [{
            "message": {"role": "assistant", "content": None,
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "mcp__inexistente__x",
                                                     "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(orchestrator, "llamar_openrouter_api", mock_api)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake_key")

    texto, _sid = orchestrator.run("otra pregunta", Collector())

    assert "límite de pasos" in texto
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_orchestrator.py -q`
Expected: FAIL — el primero devuelve el string de disculpa en vez del texto de cierre.

- [ ] **Step 3: Implementar el turno de cierre**

En `app/agent/orchestrator.py`, cambiar la constante de la línea 26:

```python
# Límite de turns e historial
MAX_ITERACIONES = 12
MAX_TOKENS = 1500
```

Agregar, justo antes de `async def correr_loop_agente`:

```python
MENSAJE_SIN_PASOS = ("No alcancé a terminar la consulta (límite de pasos del "
                     "agente). Intenta acotar tu pregunta.")

INSTRUCCION_CIERRE = (
    "Se acabaron los pasos disponibles para herramientas. Responde AHORA al "
    "usuario con lo que ya averiguaste, sin pedir mas herramientas. Si algo "
    "quedo incompleto, dilo explicitamente en una linea al final."
)


def _respuesta_de_cierre(api_key, model, system_prompt, historial):
    """Ultimo turno SIN tools: el modelo cierra con lo que ya reunio.

    Sin esto, agotar MAX_ITERACIONES botaba todo el trabajo del turno y el
    usuario recibia una disculpa vacia. Una respuesta parcial y honesta le
    sirve; la disculpa no. La instruccion de cierre NO se guarda en el
    historial: es andamiaje de este turno, no parte de la conversacion.
    """
    mensajes = historial + [{"role": "user", "content": INSTRUCCION_CIERRE}]
    try:
        resp = llamar_openrouter_api(api_key, model, system_prompt, mensajes, None)
        return (resp["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        print(f"El turno de cierre falló: {e}")
        return ""
```

Y reemplazar la última línea de `correr_loop_agente` (el `return` de la disculpa, línea 284):

```python
    texto = _respuesta_de_cierre(api_key, model, system_prompt, historial)
    if texto:
        historial.append({"role": "assistant", "content": texto})
        return texto
    return MENSAJE_SIN_PASOS
```

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `python -m pytest tests/test_orchestrator.py -q`
Expected: PASS.

- [ ] **Step 5: Correr la suite completa y commitear**

Run: `python -m pytest -q`
Expected: sin fallos.

```bash
git add app/agent/orchestrator.py tests/test_orchestrator.py
git commit -m "El agente responde con lo que tiene en vez de quedarse mudo

Al agotar sus pasos botaba todo el trabajo del turno y devolvia una
disculpa vacia. Ahora hace una ultima llamada sin herramientas para que
cierre con lo que alcanzo a reunir y diga que le falto. El limite sube
de 8 a 12 pasos, pero eso es lo de menos: lo que importa es no perder
lo ya averiguado."
```

---

### Task 8: El CO2 deja de contarse como producto vendido

Arreglo de arrastre que salió de la misma corrección del productor: la schopera y el cilindro son de Zigurat, y la recarga se compra en Clean Ice y se le cobra al cliente **a costo**. Es pass-through igual que el envase PET, pero el filtro canónico no lo excluye, así que hoy sus 6 líneas se cuentan como si fueran un producto del catálogo en el dashboard, la wiki y el reporte semanal.

**Files:**
- Modify: `app/dashboard.py:262-263` (dentro de `q_top_productos`)
- Modify: `scripts/wiki_update.py:187-188`, `:513-514`, `:897-898`
- Modify: `.claude/skills/reporte-semanal/scripts/reporte.py:95-96`
- Modify: `.claude/CLAUDE.md` (bloque del filtro canónico)
- Modify: `app/agent/system_prompt.py`

**Interfaces:**
- Consumes: nada.
- Produces: nada.

**Ojo con el escapado de `%`:** en las consultas que llevan parámetros `%s` el signo va **doblado** (`'%%co2%%'`); en las que no llevan ninguno va **simple** (`'%co2%'`). Copiar el estilo de la línea de `logist` que ya está justo encima en cada sitio — ahí ya está resuelto.

- [ ] **Step 1: Agregar el filtro en `app/dashboard.py`**

En la consulta de `q_top_productos` (línea 262), después de la línea del filtro PET:

```python
          AND p.{name_col} NOT ILIKE '%%logist%%'
          AND p.{name_col} !~* '^(barril(es)?\\s+)?pet\\y'
          AND p.{name_col} NOT ILIKE '%%co2%%'
```

- [ ] **Step 2: Agregar el filtro en los tres sitios de `scripts/wiki_update.py`**

Línea ~187 (con parámetros, `%` doblado) — después del filtro PET:

```python
        "AND p.nombre_producto NOT ILIKE '%%co2%%' "
```

Línea ~513 (con parámetros, `%` doblado) — después del filtro PET:

```python
            "  AND p.nombre_producto NOT ILIKE '%%co2%%' "
```

Línea ~897 (sin parámetros, `%` **simple**) — después del filtro PET:

```python
        "AND p.nombre_producto NOT ILIKE '%co2%' "
```

Y actualizar los dos comentarios que dicen `Excluir lineas que no son producto (ver CLAUDE.md): Logistica y envase PET` para que digan `Logistica, envase PET y carga de CO2`.

- [ ] **Step 3: Agregar el filtro en la skill del reporte semanal**

En `.claude/skills/reporte-semanal/scripts/reporte.py` (línea ~96), después del filtro PET:

```sql
      AND p.nombre_producto NOT ILIKE '%%co2%%'
```

Y en el comentario de arriba, cambiar `Logistica y envases PET traspasados al cliente (pass-through)` por `Logistica, envases PET y cargas de CO2 traspasados al cliente (pass-through)`.

- [ ] **Step 4: Actualizar `.claude/CLAUDE.md`**

En la sección "Línea de envase «Barril PET»", reemplazar el bloque del filtro canónico por:

````markdown
**Filtro canónico para rankings/agregados por producto** (aplicado en
`app/dashboard.py`, `scripts/wiki_update.py` y la skill reporte-semanal):

```sql
AND p.nombre_producto NOT ILIKE '%logist%'
AND p.nombre_producto !~* '^(barril(es)?\s+)?pet\y'
AND p.nombre_producto NOT ILIKE '%co2%'
```
````

Y agregar, justo después de esa subsección, una nueva:

```markdown
### Línea de CO2 — pass-through, tampoco es venta de cerveza

Zigurat instala en algunos restaurantes una **schopera de su propiedad**, y el
cilindro de CO2 que empuja la cerveza también es suyo. Cuando se acaba, le
llevan una carga nueva comprada en **Clean Ice** (aparece en las facturas de
compra) y se le cobra al cliente **exactamente lo que costó**.

- La línea de CO2 ("9 kg CO2", "Carga CO2", "Recarga CO2 9 kg"… hay variantes)
  es un **traspaso de costo sin margen**, igual que el envase PET: no es un
  producto del catálogo ni venta de cerveza, aunque sí suma en el monto
  facturado.
- Va excluida del filtro canónico de arriba y de la base de reparto de la
  logística en `app/negocio/precios_venta.py`.
```

- [ ] **Step 5: Agregar la regla al system prompt**

En `app/agent/system_prompt.py`, en el párrafo "ESTRUCTURA DE FACTURACIÓN", después de la frase sobre las líneas PET, agregar:

```
Las líneas de CO2 ("Carga CO2", "Recarga CO2 9 kg") son lo mismo: la schopera y
el cilindro son de Zigurat y la recarga se le cobra al cliente a costo, sin
margen. No son venta de cerveza: exclúyelas de rankings de producto
(NOT ILIKE '%co2%').
```

- [ ] **Step 6: Verificar que el CO2 desapareció de los rankings**

Run:

```bash
python -c "
import sys; sys.path.insert(0,'.')
from app.config import DB_URL
from app import dashboard
import psycopg2
from psycopg2.extras import RealDictCursor
c = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
cur = c.cursor()
cols, _objs = dashboard.introspect(cur)
filas = dashboard.q_top_productos(cur, cols, limit=50)
hay_co2 = [f for f in filas if 'co2' in str(f['producto']).lower()]
print('productos en el ranking:', len(filas))
print('lineas de CO2 coladas:', len(hay_co2), hay_co2)
assert not hay_co2, 'el CO2 sigue contandose como producto'
print('OK — el CO2 ya no aparece como producto')
"
```

Expected: `lineas de CO2 coladas: 0` y `OK`.

- [ ] **Step 7: Correr la suite completa y commitear**

Run: `python -m pytest -q`
Expected: sin fallos.

```bash
git add app/dashboard.py scripts/wiki_update.py .claude/skills/reporte-semanal/scripts/reporte.py .claude/CLAUDE.md app/agent/system_prompt.py
git commit -m "El CO2 deja de contarse como un producto vendido

La schopera y el cilindro son de Zigurat: la recarga se compra en Clean
Ice y se le cobra al cliente a costo, sin margen. Es el mismo
pass-through que el envase PET, pero el filtro canonico no lo excluia y
sus 6 lineas aparecian como producto del catalogo en el dashboard, la
wiki y el reporte semanal."
```

---

## Cierre

- [ ] **Verificación final: la pregunta original**

Levantar el dashboard (`python app/dashboard.py`), abrir http://localhost:8777 → Asistente & Informes, y preguntar textualmente:

> cual es el costo de la botella de cream y scotch de 330cc, quiero saber el margen de ganancia

Expected: responde con las cuatro cifras (costo, precio, margen y porcentaje de cada cerveza), **sin** el mensaje de límite de pasos. Los números deben coincidir con lo que imprimió el paso 5 de la Tarea 5.

- [ ] **Push**

```bash
git push
```
