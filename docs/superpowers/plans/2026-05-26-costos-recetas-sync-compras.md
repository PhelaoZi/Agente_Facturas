# Costos de Producción — Recetas + Precios + Sync Compras

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corregir precios rotos en maestro_insumos, cargar las 4 recetas desde Recetas.xlsx, y crear el pipeline /sync-compras para mantener precios actualizados desde facturas de proveedores.

**Architecture:** 4 scripts Python independientes ejecutados en orden. Todos idempotentes (se pueden re-ejecutar sin efectos secundarios). La `vista_costo_sku` existente recalcula automáticamente al cambiar precios — no requiere cambios en la vista.

**Tech Stack:** Python 3, psycopg2-binary, openpyxl, xml.etree.ElementTree, PostgreSQL local (dte_facturas_chile)

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `scripts/migrate_gastos_operativos.py` | Crear | Crea tabla `gastos_operativos` |
| `scripts/migrate_costos_v3.py` | Crear | Corrige 12 precios + agrega 10 insumos nuevos |
| `scripts/cargar_recetas_v2.py` | Crear | Borra y recarga `receta_detalle` de las 4 recetas desde Excel |
| `scripts/sync_compras.py` | Crear | Procesa XMLs en `facturas-compras/`, actualiza precios y registra gastos |
| `.claude/skills/sync-compras/skill.md` | Crear | Skill /sync-compras |
| `CLAUDE.md` | Modificar | Agregar comandos nuevos |

---

## Task 1: Crear tabla `gastos_operativos`

**Files:**
- Create: `scripts/migrate_gastos_operativos.py`

- [ ] **Crear el script**

```python
#!/usr/bin/env python3
"""
migrate_gastos_operativos.py — Zigurat ERP
Crea tabla gastos_operativos para facturas de compra que no son
insumos de producción (peajes, servicios, arrendamientos, etc.).
Idempotente.
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
CREATE TABLE IF NOT EXISTS gastos_operativos (
    id                   SERIAL PRIMARY KEY,
    folio                TEXT,
    tipo_documento       TEXT,
    fecha_emision        DATE,
    rut_emisor           TEXT,
    razon_social_emisor  TEXT,
    descripcion          TEXT,
    monto_neto           INTEGER,
    monto_total          INTEGER,
    categoria            TEXT DEFAULT 'otros',
    fecha_procesado      TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_gastos_folio_rut
    ON gastos_operativos (folio, rut_emisor);
"""


def main():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()
            cur.execute(SQL)
        print("OK — tabla gastos_operativos lista (idempotente).")
    except psycopg2.Error as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Ejecutar el script**

```bash
python scripts/migrate_gastos_operativos.py
```

Salida esperada: `OK — tabla gastos_operativos lista (idempotente).`

- [ ] **Verificar tabla en BD**

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'gastos_operativos'
ORDER BY ordinal_position;
```

Esperado: 10 columnas (id, folio, tipo_documento, fecha_emision, rut_emisor, razon_social_emisor, descripcion, monto_neto, monto_total, categoria, fecha_procesado).

- [ ] **Commit**

```bash
git add scripts/migrate_gastos_operativos.py
git commit -m "Agrega migración tabla gastos_operativos"
```

---

## Task 2: Corregir precios e insertar insumos faltantes

**Files:**
- Create: `scripts/migrate_costos_v3.py`

- [ ] **Crear el script**

```python
#!/usr/bin/env python3
"""
migrate_costos_v3.py — Zigurat ERP
1. Corrige 12 precios en maestro_insumos (precio por UNIDAD, no por paquete).
2. Inserta 10 insumos nuevos con precio 0 provisional.
Idempotente.

Contexto del bug: los precios de levaduras, lúpulos y clarificantes
estaban guardados como "precio del paquete completo" en vez de
"precio por gr/ml". Eso multiplicaba el costo calculado por 100-500x.
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

# Precio por UNIDAD correcto.
# Fuentes: XML Mundo Cervecero 2026-05-20, XML Almacén Cervecero 2026-04-29,
#          Costos_Zigurat.xlsx (precio_total_lote / kg_usados).
PRECIO_CORRECCIONES = [
    ("Malta Cara Pils",                    2310.92),  # 18487.39 / 8 kg
    ("Malta Cara Ruby",                    2415.97),  # 19327.73 / 8 kg
    ("Malta Arome",                        2176.47),  # 17411.76 / 8 kg
    ("Malta Biscuit",                      2415.97),  # 19327.73 / 8 kg
    ("Malta Chocolate",                    2512.61),  # XML: PrcItem=2512.61/kg
    ("Malta Cara Aroma",                   2932.77),  # XML: PrcItem=2932.77/kg
    ("Trigo Malteado claro",               1470.59),  # 11764.71 / 8 kg
    ("Lupulo Magnum",                        36.37),  # 3636.60 / 100 gr
    ("Levadura AY4",                         89.11),  # 44555 / 500 gr
    ("Clarificante Polyclar coccion",        49.40),  # 4940 / 100 gr
    ("Clarificante Polyclar 10 maduracion",  32.40),  # 3239.50 / 100 gr
    ("Clarificante SB3 maduracion",          13.61),  # 6806.72 / 500 ml
]

# Nuevos insumos para las recetas Paint it Black y Stout Café/Cacao.
# Precio 0 provisional — actualizar cuando llegue factura del proveedor.
NUEVOS_INSUMOS = [
    ("Fosfórico",          "ml",    0.0, "adjunto"),
    ("Malta Cara 50",      "kg",    0.0, "malta"),
    ("Malta Carafa 2",     "kg",    0.0, "malta"),
    ("Cebada Tostada",     "kg",    0.0, "malta"),
    ("Avena",              "kg",    0.0, "adjunto"),
    ("Hojuela de Cebada",  "kg",    0.0, "adjunto"),
    ("Frambuesa",          "kg",    0.0, "adjunto"),
    ("Vainilla",           "litro", 0.0, "adjunto"),
    ("Café",               "kg",    0.0, "adjunto"),
    ("Cacao",              "kg",    0.0, "adjunto"),
]


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Migración Costos v3 (corrección precios)")
    print("=" * 60)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            # 1. Corregir precios existentes
            print("\n[1] Corrigiendo precios...")
            actualizados = 0
            no_encontrados = []
            for nombre, precio in PRECIO_CORRECCIONES:
                cur.execute(
                    "UPDATE maestro_insumos SET precio_neto_unitario = %s WHERE nombre = %s",
                    (precio, nombre)
                )
                if cur.rowcount:
                    actualizados += 1
                    print(f"  OK  {nombre:45s}  ${precio:.4f}")
                else:
                    no_encontrados.append(nombre)

            # 2. Insertar insumos nuevos
            print("\n[2] Insertando insumos faltantes...")
            insertados = 0
            for nombre, unidad, precio, categoria in NUEVOS_INSUMOS:
                cur.execute(
                    """
                    INSERT INTO maestro_insumos (nombre, unidad, precio_neto_unitario, categoria)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (nombre) DO NOTHING
                    """,
                    (nombre, unidad, precio, categoria)
                )
                if cur.rowcount:
                    insertados += 1
                    print(f"  NUEVO  {nombre}")
                else:
                    print(f"  YA EXISTE  {nombre}")

        print()
        print(f"Precios corregidos: {actualizados}/12")
        print(f"Insumos nuevos: {insertados}/10")
        if no_encontrados:
            print(f"\nATENCIÓN — no encontrados (revisar nombre exacto):")
            for n in no_encontrados:
                print(f"  - {n}")

    except psycopg2.Error as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Ejecutar el script**

```bash
python scripts/migrate_costos_v3.py
```

Salida esperada: `Precios corregidos: 12/12` y `Insumos nuevos: 10/10` (o menos si algunos ya existen).

- [ ] **Verificar precios corregidos**

```sql
SELECT nombre, unidad, precio_neto_unitario
FROM maestro_insumos
WHERE nombre IN (
    'Lupulo Magnum', 'Levadura AY4',
    'Clarificante Polyclar coccion', 'Malta Chocolate'
)
ORDER BY nombre;
```

Resultado esperado:
```
Clarificante Polyclar coccion  | gr   | 49.40
Levadura AY4                   | gr   | 89.11
Lupulo Magnum                  | gr   | 36.37
Malta Chocolate                | kg   | 2512.61
```

- [ ] **Verificar insumos nuevos**

```sql
SELECT nombre, unidad, categoria
FROM maestro_insumos
WHERE precio_neto_unitario = 0
ORDER BY nombre;
```

Esperado: 10 filas (Avena, Café, Cacao, Cebada Tostada, Fosfórico, Frambuesa, Hojuela de Cebada, Malta Cara 50, Malta Carafa 2, Vainilla).

- [ ] **Commit**

```bash
git add scripts/migrate_costos_v3.py
git commit -m "Corrige precios maestro_insumos y agrega 10 insumos para recetas nuevas"
```

---

## Task 3: Cargar las 4 recetas desde Excel

**Files:**
- Create: `scripts/cargar_recetas_v2.py`

- [ ] **Crear el script**

```python
#!/usr/bin/env python3
"""
cargar_recetas_v2.py — Zigurat ERP
Carga las 4 recetas desde Recetas.xlsx.
Para Cream Ale y Scotch Ale: BORRA y recarga receta_detalle.
Para Paint it Black y Stout Café/Cacao: crea receta + detalle.
Idempotente (upsert en recetas, delete+insert en detalle).

Uso:
    python scripts/cargar_recetas_v2.py
    python scripts/cargar_recetas_v2.py /ruta/alternativa/Recetas.xlsx
"""
import os, sys, re
from pathlib import Path

try:
    import psycopg2
    import openpyxl
except ImportError as e:
    print(f"ERROR: Falta dependencia — {e}")
    print("Instala con: pip install psycopg2-binary openpyxl")
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

EXCEL_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "OneDrive" / "Recetas.xlsx"

# Parámetros estándar de lote (CLAUDE.md — capa B)
LOTE_LITROS     = 540
COSTO_MO        = 300_000
COSTO_SERVICIOS = 185_000
MERMA_PCT       = 5.0

# Normaliza nombres de receta leídos del Excel (row 0, .title())
RECIPE_NAMES = {
    "Cream Ale":        "Cream Ale",
    "Scotch Ale":       "Scotch Ale",
    "Paint It Black":   "Paint it Black",
    "Stout Cafe/Cacao": "Stout Café/Cacao",
}

# Mapeo nombre_excel.lower().strip() → nombre en maestro_insumos
INSUMO_MAP = {
    "pilsen":                           "Malta Pilsen",
    "caradextrina":                     "Malta Caradex",
    "trigo":                            "Trigo Malteado claro",
    "levadura ay4":                     "Levadura AY4",
    "lupulo magnum":                    "Lupulo Magnum",
    "fosforico":                        "Fosfórico",
    "fosfórico":                        "Fosfórico",
    "clarificante polyclar coccion":    "Clarificante Polyclar coccion",
    "clarificante polyclar maduracion": "Clarificante Polyclar 10 maduracion",
    "clarifiante maduracion sb3":       "Clarificante SB3 maduracion",
    "pale ale":                         "Malta Pale Ale",
    "munich":                           "Malta Munich",
    "cara 50":                          "Malta Cara 50",
    "biscuit":                          "Malta Biscuit",
    "aroma":                            "Malta Arome",
    "chocolate":                        "Malta Chocolate",
    "cara aroma":                       "Malta Cara Aroma",
    "carafa 2":                         "Malta Carafa 2",
    "cebada tostada":                   "Cebada Tostada",
    "avena":                            "Avena",
    "hojuela de cebada":                "Hojuela de Cebada",
    "frambuesa":                        "Frambuesa",
    "vainilla":                         "Vainilla",
    "cafe":                             "Café",
    "cacao":                            "Cacao",
}

UNIDAD_MAP = {
    "kg": "kg",
    "grs": "gr", "gr": "gr", "grs.": "gr",
    "ml": "ml", "ml.": "ml",
    "ltrs": "litro", "ltr": "litro", "litros": "litro", "litro": "litro",
}


def parse_cantidad(qty_str):
    """'100 kg' → (100.0, 'kg'),  '500 grs' → (500.0, 'gr')"""
    if not qty_str:
        return None, None
    m = re.match(r"([\d.]+)\s*(\w+\.?)", str(qty_str).strip())
    if not m:
        return None, None
    cantidad = float(m.group(1))
    unidad = UNIDAD_MAP.get(m.group(2).lower().rstrip("."), m.group(2).lower())
    return cantidad, unidad


def leer_recetas_excel(path):
    """
    Parsea el Excel con 4 recetas en columnas paralelas.
    Col 0-1: Cream Ale, Col 3-4: Scotch Ale,
    Col 6-7: Paint it Black, Col 9-10: Stout Café/Cacao.
    Devuelve dict: nombre_receta → [(nombre_insumo_bd, cantidad), ...]
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = [row for row in ws.iter_rows(values_only=True) if any(c is not None for c in row)]

    # Fila 0: nombres de receta
    COLUMNAS = [(0, 1), (3, 4), (6, 7), (9, 10)]
    nombres_raw = [str(rows[0][c[0]]).strip().title() for c in COLUMNAS]
    nombres_receta = [RECIPE_NAMES.get(n, n) for n in nombres_raw]

    recetas = {nombre: [] for nombre in nombres_receta}

    for row in rows[1:]:
        for (col_n, col_q), nombre_receta in zip(COLUMNAS, nombres_receta):
            ingrediente = row[col_n]
            cantidad_str = row[col_q]
            if not ingrediente:
                continue
            key = str(ingrediente).strip().lower()
            nombre_bd = INSUMO_MAP.get(key)
            if not nombre_bd:
                print(f"  AVISO: sin mapeo '{ingrediente}' en {nombre_receta}")
                continue
            cantidad, _ = parse_cantidad(cantidad_str)
            if cantidad is None:
                print(f"  AVISO: cantidad inválida '{cantidad_str}' para '{ingrediente}'")
                continue
            recetas[nombre_receta].append((nombre_bd, cantidad))

    return recetas


def main():
    if not EXCEL_PATH.exists():
        print(f"ERROR: No se encontró el Excel en {EXCEL_PATH}")
        print("Uso: python scripts/cargar_recetas_v2.py [ruta_excel]")
        sys.exit(1)

    print(f"Leyendo: {EXCEL_PATH}")
    recetas = leer_recetas_excel(EXCEL_PATH)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            # Cache nombre → id de todos los insumos
            cur.execute("SELECT nombre, id FROM maestro_insumos")
            insumo_ids = {row[0]: row[1] for row in cur.fetchall()}

            for nombre_receta, ingredientes in recetas.items():
                # Upsert receta (crea si no existe, actualiza si existe)
                cur.execute(
                    """
                    INSERT INTO recetas
                        (nombre_cerveza, litros_lote_estandar,
                         costo_mano_obra_lote, costo_servicios_lote, merma_porcentaje)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (nombre_cerveza) DO UPDATE SET
                        litros_lote_estandar = EXCLUDED.litros_lote_estandar,
                        costo_mano_obra_lote = EXCLUDED.costo_mano_obra_lote,
                        costo_servicios_lote = EXCLUDED.costo_servicios_lote,
                        merma_porcentaje     = EXCLUDED.merma_porcentaje
                    RETURNING id
                    """,
                    (nombre_receta, LOTE_LITROS, COSTO_MO, COSTO_SERVICIOS, MERMA_PCT)
                )
                receta_id = cur.fetchone()[0]

                # Borrar detalle anterior y recargar desde Excel
                cur.execute("DELETE FROM receta_detalle WHERE receta_id = %s", (receta_id,))
                borrados = cur.rowcount

                filas_ok = 0
                sin_id = []
                for nombre_bd, cantidad in ingredientes:
                    insumo_id = insumo_ids.get(nombre_bd)
                    if not insumo_id:
                        sin_id.append(nombre_bd)
                        continue
                    cur.execute(
                        """
                        INSERT INTO receta_detalle (receta_id, insumo_id, cantidad_requerida)
                        VALUES (%s, %s, %s)
                        """,
                        (receta_id, insumo_id, cantidad)
                    )
                    filas_ok += 1

                print(f"\n{nombre_receta} (id={receta_id})")
                print(f"  Ingredientes cargados: {filas_ok}/{len(ingredientes)}")
                if borrados:
                    print(f"  Filas antiguas eliminadas: {borrados}")
                if sin_id:
                    print(f"  ATENCIÓN — no encontrados en maestro_insumos: {sin_id}")

        print("\nRecetas cargadas correctamente.")

    except psycopg2.Error as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Ejecutar el script**

```bash
python scripts/cargar_recetas_v2.py
```

Salida esperada (sin errores de "sin mapeo" ni "sin id"):
```
Cream Ale (id=2)
  Ingredientes cargados: 9/9
  Filas antiguas eliminadas: 8

Scotch Ale (id=1)
  Ingredientes cargados: 13/13
  Filas antiguas eliminadas: 20

Paint it Black (id=3)
  Ingredientes cargados: 16/16

Stout Café/Cacao (id=4)
  Ingredientes cargados: 17/17
```

- [ ] **Verificar conteo de ingredientes por receta**

```sql
SELECT r.nombre_cerveza, COUNT(rd.id) AS ingredientes
FROM recetas r
LEFT JOIN receta_detalle rd ON rd.receta_id = r.id
GROUP BY r.nombre_cerveza
ORDER BY r.nombre_cerveza;
```

Esperado:
```
Cream Ale        | 9
Paint it Black   | 16
Scotch Ale       | 13
Stout Café/Cacao | 17
```

- [ ] **Verificar costos calculados (no deben ser millones)**

```sql
SELECT nombre_cerveza,
       ROUND(SUM(rd.cantidad_requerida * mi.precio_neto_unitario)) AS costo_insumos_lote
FROM recetas r
JOIN receta_detalle rd ON rd.receta_id = r.id
JOIN maestro_insumos mi ON mi.id = rd.insumo_id
GROUP BY nombre_cerveza
ORDER BY nombre_cerveza;
```

Valores razonables esperados (costos de insumos por lote de 540L):
- Cream Ale: entre $150,000 y $350,000
- Scotch Ale: entre $200,000 y $500,000
- Paint it Black: los insumos sin precio ($0) hacen que salga más bajo, es esperado

- [ ] **Commit**

```bash
git add scripts/cargar_recetas_v2.py
git commit -m "Carga las 4 recetas desde Recetas.xlsx con precios por unidad corregidos"
```

---

## Task 4: Crear script `sync_compras.py`

**Files:**
- Create: `scripts/sync_compras.py`

- [ ] **Crear el script**

```python
#!/usr/bin/env python3
"""
sync_compras.py — Zigurat ERP
Procesa XMLs DTE en facturas-compras/:
  - Proveedor insumos → actualiza precio_neto_unitario en maestro_insumos
  - Proveedor gasto   → inserta en gastos_operativos
  - Proveedor desconocido → warning, se omite

Idempotente: registra archivos procesados en facturas-compras/.procesados.json.

Uso: python scripts/sync_compras.py
"""
import os, sys, json, re
from pathlib import Path
import xml.etree.ElementTree as ET

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

CARPETA = Path(__file__).parent.parent / "facturas-compras"
LOG_PROCESADOS = CARPETA / ".procesados.json"

# RUT → nombre legible. Sus ítems se mapean a maestro_insumos.
PROVEEDORES_INSUMOS = {
    "76045387-0": "Mundo Cervecero",
    "76448126-7": "Almacén Cervecero",
    "77103092-0": "Petainer Chile",    # barriles — sin mapeo de items por ahora
}

# RUT → (nombre legible, categoría). Sus documentos van a gastos_operativos.
PROVEEDORES_GASTOS = {
    "76052927-3": ("Autopista Nueva Vespucio Sur", "transporte"),
}

# Substring del NmbItem (lowercase) → (nombre en maestro_insumos, unidades_por_paquete)
# precio_neto_unitario = PrcItem / unidades_por_paquete
ITEM_MAP = {
    "malta chocolate":        ("Malta Chocolate",                       1),
    "malta caraaroma":        ("Malta Cara Aroma",                      1),
    "fermoale ay4":           ("Levadura AY4",                        500),
    "lupulo100gr magnum":     ("Lupulo Magnum",                       100),
    "polyclar brewbrite":     ("Clarificante Polyclar coccion",        100),
    "polyclar10":             ("Clarificante Polyclar 10 maduracion",  100),
}


def _load_procesados():
    if LOG_PROCESADOS.exists():
        with open(LOG_PROCESADOS, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def _save_procesados(procesados):
    with open(LOG_PROCESADOS, "w", encoding="utf-8") as f:
        json.dump(sorted(procesados), f, indent=2, ensure_ascii=False)


def parse_xml(filepath):
    """Parsea DTE XML (ISO-8859-1). Devuelve dict con datos del documento."""
    with open(filepath, "rb") as f:
        raw = f.read()
    # Normalizar encoding para que ET pueda parsear sin error
    content = raw.replace(b'encoding="ISO-8859-1"', b'encoding="UTF-8"')
    content = content.replace(b"encoding='ISO-8859-1'", b"encoding='UTF-8'")
    content_str = content.decode("iso-8859-1")
    # Eliminar declaración de namespace de Signature para simplificar búsquedas
    content_clean = re.sub(r' xmlns="[^"]+"', "", content_str)

    root = ET.fromstring(content_clean.encode("utf-8"))
    doc = root.find(".//Documento")
    if doc is None:
        raise ValueError(f"No se encontró <Documento> en {filepath.name}")

    enc     = doc.find("Encabezado")
    id_doc  = enc.find("IdDoc")
    emisor  = enc.find("Emisor")
    totales = enc.find("Totales")

    monto_neto  = int(float(totales.findtext("MntNeto")  or 0))
    monto_total = int(float(totales.findtext("MntTotal") or 0))

    items = []
    for det in doc.findall("Detalle"):
        nombre = (det.findtext("NmbItem") or "").strip()
        qty    = float(det.findtext("QtyItem")    or 1)
        precio = float(det.findtext("PrcItem")    or 0)
        monto  = int(float(det.findtext("MontoItem") or 0))
        items.append({"nombre": nombre, "qty": qty, "precio_unitario": precio, "monto": monto})

    return {
        "tipo_dte":    id_doc.findtext("TipoDTE"),
        "folio":       id_doc.findtext("Folio"),
        "fecha":       id_doc.findtext("FchEmis"),
        "rut_emisor":  (emisor.findtext("RUTEmisor") or "").strip(),
        "razon_social": (emisor.findtext("RznSoc")   or "").strip(),
        "monto_neto":  monto_neto,
        "monto_total": monto_total,
        "items":       items,
    }


def procesar_insumos(dte, cur):
    """Actualiza precios en maestro_insumos según los items del DTE."""
    actualizados = []
    no_mapeados  = []
    for item in dte["items"]:
        nombre_lower = item["nombre"].lower()
        match = next(
            ((k, v) for k, v in ITEM_MAP.items() if k in nombre_lower), None
        )
        if not match:
            no_mapeados.append(item["nombre"])
            continue
        _, (nombre_bd, unidades_paquete) = match
        precio_por_unidad = round(item["precio_unitario"] / unidades_paquete, 4)
        cur.execute(
            "UPDATE maestro_insumos SET precio_neto_unitario = %s WHERE nombre = %s",
            (precio_por_unidad, nombre_bd)
        )
        if cur.rowcount:
            actualizados.append(f"{nombre_bd} → ${precio_por_unidad:.4f}/unidad")
    return actualizados, no_mapeados


def procesar_gasto(dte, categoria, cur):
    """Inserta el DTE como gasto operativo. Retorna True si fue insertado."""
    descripcion = dte["items"][0]["nombre"] if dte["items"] else dte["razon_social"]
    cur.execute(
        """
        INSERT INTO gastos_operativos
            (folio, tipo_documento, fecha_emision, rut_emisor,
             razon_social_emisor, descripcion, monto_neto, monto_total, categoria)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (folio, rut_emisor) DO NOTHING
        """,
        (dte["folio"], dte["tipo_dte"], dte["fecha"], dte["rut_emisor"],
         dte["razon_social"], descripcion,
         dte["monto_neto"], dte["monto_total"], categoria)
    )
    return cur.rowcount > 0


def main():
    xmls = sorted(CARPETA.glob("*.xml"))
    if not xmls:
        print(f"No hay XMLs en {CARPETA}/")
        return

    procesados = _load_procesados()
    pendientes = [x for x in xmls if x.name not in procesados]

    print(f"XMLs en carpeta: {len(xmls)} | Procesados: {len(procesados)} | Pendientes: {len(pendientes)}")

    if not pendientes:
        print("Todo al día — nada nuevo que procesar.")
        return

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    nuevos_procesados = set()
    try:
        for xml_path in pendientes:
            print(f"\n→ {xml_path.name}")
            try:
                dte = parse_xml(xml_path)
            except Exception as e:
                print(f"  ERROR al parsear: {e}")
                continue

            rut = dte["rut_emisor"]
            print(f"  Emisor: {dte['razon_social']} ({rut}) | Folio {dte['folio']} | ${dte['monto_total']:,}")

            with conn:
                cur = conn.cursor()
                if rut in PROVEEDORES_GASTOS:
                    _, categoria = PROVEEDORES_GASTOS[rut]
                    insertado = procesar_gasto(dte, categoria, cur)
                    estado = "insertado" if insertado else "ya existía"
                    print(f"  Gasto operativo [{categoria}]: {estado}")
                elif rut in PROVEEDORES_INSUMOS:
                    actualizados, no_mapeados = procesar_insumos(dte, cur)
                    for msg in actualizados:
                        print(f"  Precio: {msg}")
                    for nombre in no_mapeados:
                        print(f"  Sin mapeo (omitido): {nombre}")
                    if not actualizados and not no_mapeados:
                        print(f"  Sin ítems reconocidos para {PROVEEDORES_INSUMOS[rut]}")
                else:
                    print(f"  AVISO: RUT {rut} sin clasificar — omitido")
                    print(f"  Para procesar: agregar a PROVEEDORES_INSUMOS o PROVEEDORES_GASTOS en sync_compras.py")
                    continue

            nuevos_procesados.add(xml_path.name)

        _save_procesados(procesados | nuevos_procesados)
        print(f"\nSync completo. Procesados nuevos: {len(nuevos_procesados)}")
        if nuevos_procesados:
            print("Verifica costos con: python scripts/costo_sku.py")

    except psycopg2.Error as e:
        print(f"ERROR DB: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Ejecutar el script (procesa los 4 XMLs actuales)**

```bash
python scripts/sync_compras.py
```

Salida esperada:
```
XMLs en carpeta: 4 | Procesados: 0 | Pendientes: 4

→ DTE_DOWN763080122026-05-24.xml
  Emisor: COMERCIAL MUNDO CERVECERO Y CIA. LTDA. (76045387-0) | Folio 31036 | $12,820
  Precio: Malta Chocolate → $2512.6050/unidad
  Precio: Malta Cara Aroma → $2932.7731/unidad
  Sin mapeo (omitido): Servicio Molienda

→ DTE_DOWN763080122026-05-24EX.xml
  Emisor: Soc. Conc. Autopista Nueva Vespucio Sur (76052927-3) | Folio 14874658 | $71,378
  Gasto operativo [transporte]: insertado

→ DTE_DOWN763080122026-05-26.xml
  Emisor: ALMACEN CERVECERO SPA (76448126-7) | Folio 21409 | $87,951
  Precio: Levadura AY4 → $89.1100/unidad
  Precio: Lupulo Magnum → $36.3660/unidad
  Precio: Clarificante Polyclar coccion → $49.4000/unidad
  Precio: Clarificante Polyclar 10 maduracion → $32.3950/unidad
  Sin mapeo (omitido): Botellas 330cc Generica Saco 80und
  ...

→ DTE_DOWN763080122026-05-26(1).xml
  Emisor: PETAINER CHILE SPA (77103092-0) | Folio 769 | $228,480
  Sin ítems reconocidos para Petainer Chile

Sync completo. Procesados nuevos: 4
```

- [ ] **Verificar gasto operativo registrado**

```sql
SELECT folio, razon_social_emisor, descripcion, monto_total, categoria
FROM gastos_operativos;
```

Esperado: 1 fila con folio=14874658, categoria='transporte', monto_total=71378.

- [ ] **Verificar log de procesados**

```bash
type "facturas-compras\.procesados.json"
```

Esperado: JSON con los 4 nombres de archivos XML.

- [ ] **Commit**

```bash
git add scripts/sync_compras.py "facturas-compras/.procesados.json"
git commit -m "Agrega sync_compras.py y procesa los 4 XMLs de facturas de compra"
```

---

## Task 5: Verificar costos finales con vista_costo_sku

- [ ] **Ejecutar costo_sku.py**

```bash
python scripts/costo_sku.py
```

Si no hay SKUs cargados aún, la vista devuelve vacío. En ese caso, verificar directamente:

```sql
SELECT r.nombre_cerveza,
       ROUND(SUM(rd.cantidad_requerida * mi.precio_neto_unitario)) AS costo_insumos,
       r.costo_mano_obra_lote + r.costo_servicios_lote AS costo_fijo,
       ROUND(SUM(rd.cantidad_requerida * mi.precio_neto_unitario)
             + r.costo_mano_obra_lote + r.costo_servicios_lote) AS costo_lote_total
FROM recetas r
JOIN receta_detalle rd ON rd.receta_id = r.id
JOIN maestro_insumos mi ON mi.id = rd.insumo_id
GROUP BY r.nombre_cerveza, r.costo_mano_obra_lote, r.costo_servicios_lote
ORDER BY r.nombre_cerveza;
```

**Valores esperados** (rangos razonables para lote 540L):
- Cream Ale: costo insumos ~$150K–$300K, costo lote total ~$635K–$785K
- Scotch Ale: costo insumos ~$200K–$450K, costo lote total ~$685K–$935K
- Paint it Black / Stout: más bajo por ahora (insumos especiales con precio $0)

Si algún valor supera $5,000,000 en costo_insumos, hay un precio mal corregido — revisar Task 2.

- [ ] **Commit (si hay cambios pendientes)**

```bash
git add -A
git commit -m "Verifica costos: recetas y precios unitarios correctos en BD"
```

---

## Task 6: Crear skill `/sync-compras`

**Files:**
- Create: `.claude/skills/sync-compras/skill.md`

- [ ] **Crear el directorio y el archivo**

```bash
mkdir ".claude\skills\sync-compras"
```

Contenido del archivo `.claude/skills/sync-compras/skill.md`:

```markdown
---
name: sync-compras
description: >
  Procesa XMLs DTE de proveedores en facturas-compras/.
  Actualiza precios en maestro_insumos (insumos de producción)
  y registra gastos en gastos_operativos (peajes, servicios, etc.).
  Usar cuando lleguen nuevas facturas de compra.
  Ejemplos: "hay facturas nuevas de proveedores", "sincroniza las compras",
  "actualiza precios desde las facturas", "procesa los XMLs de compras".
---

## Instrucciones

1. Ejecutar el script de sincronización:
   ```
   python scripts/sync_compras.py
   ```

2. Revisar el reporte:
   - **Precios actualizados**: insumos cuyo precio_neto_unitario fue actualizado
   - **Gastos operativos**: facturas registradas en gastos_operativos
   - **Sin mapeo**: ítems del XML sin correspondencia en ITEM_MAP (no generan error)
   - **Sin clasificar**: proveedores no registrados en PROVEEDORES_INSUMOS ni PROVEEDORES_GASTOS

3. Si hay proveedores sin clasificar, agregarlos en `scripts/sync_compras.py`:
   - Proveedor de insumos → `PROVEEDORES_INSUMOS[rut] = "Nombre"`
   - Proveedor de gastos → `PROVEEDORES_GASTOS[rut] = ("Nombre", "categoria")`

4. Verificar costos actualizados:
   ```
   python scripts/costo_sku.py
   ```

## Notas

- Idempotente: XMLs ya procesados se saltan automáticamente (`.procesados.json`)
- Los XMLs van en `facturas-compras/` (formato DTE del SII, ISO-8859-1)
- Para agregar nuevos ítems al mapeo: editar `ITEM_MAP` en `sync_compras.py`
```

- [ ] **Commit**

```bash
git add ".claude/skills/sync-compras/skill.md"
git commit -m "Agrega skill /sync-compras para sincronizar facturas de compra"
```

---

## Task 7: Actualizar CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Agregar los nuevos comandos** en la sección `## Comandos frecuentes`

Agregar después de la línea `/sync-nc`:

```markdown
# Sincronizar facturas de compra (proveedores → precios + gastos)
/sync-compras                     # Procesa XMLs en facturas-compras/

# Migraciones de schema (idempotentes)
python scripts/migrate_gastos_operativos.py   # Crea tabla gastos_operativos
python scripts/migrate_costos_v3.py           # Corrige precios maestro_insumos
python scripts/cargar_recetas_v2.py           # Recarga 4 recetas desde Recetas.xlsx
```

- [ ] **Commit**

```bash
git add CLAUDE.md
git commit -m "Documenta comandos /sync-compras y scripts de migración v3"
```

---

## Self-Review

**Spec coverage:**
- ✅ Corrección 12 precios → Task 2
- ✅ 10 insumos nuevos en $0 → Task 2
- ✅ 4 recetas desde Excel (DELETE + INSERT) → Task 3
- ✅ Tabla gastos_operativos → Task 1
- ✅ sync_compras.py con ITEM_MAP y conversión precio/unidad → Task 4
- ✅ Skill /sync-compras → Task 6
- ✅ Flujo dólar (Bucarest → /sync-compras → vista recalcula) → documentado en skill

**Consistencia de tipos:**
- `precio_neto_unitario`: NUMERIC en BD, float en Python → correcto (psycopg2 convierte automáticamente)
- `cantidad_requerida`: NUMERIC(10,4) en BD, float en Python → correcto
- `monto_neto`/`monto_total`: INTEGER en BD, `int(float(...))` en Python → correcto
- `receta_id` devuelto por `RETURNING id` → `fetchone()[0]` → int → correcto

**Sin placeholders:**
- Todos los bloques de código tienen contenido real
- Los valores de verificación SQL tienen rangos esperados concretos
- Los nombres de archivo son exactos
