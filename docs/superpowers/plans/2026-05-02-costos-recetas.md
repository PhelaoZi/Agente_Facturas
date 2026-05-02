# Costos de producción por SKU — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar la capa B del módulo de costos: 5 scripts Python + 3 skills de Claude Code que entregan el costo unitario real por SKU (cerveza × formato) consultable por SQL.

**Architecture:** Extensión del modelo existente (Enfoque 3 del spec). `maestro_insumos` se tipifica con `categoria`. `recetas` gana costos variables por lote. Tablas nuevas `formatos`, `sku`, `sku_envasado` modelan el envasado por SKU. Vista `vista_costo_sku` calcula costo unitario combinando líquido + envasado + mano de obra + servicios variables. Sin tests automatizados (validación manual + constraints SQL — ver spec sección 5).

**Tech Stack:** Python 3.x + psycopg2-binary (igual que el resto del proyecto) + PostgreSQL local. Sin frameworks adicionales.

**Spec:** `docs/superpowers/specs/2026-05-02-costos-recetas-design.md`

---

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `scripts/migrate_costos_v2.py` | Migración idempotente: ALTERs, CREATE TABLEs, seed de `formatos`, recategorización, detección de precios sospechosos, vista nueva. |
| `scripts/actualizar_insumo.py` | Crear o actualizar un insumo en `maestro_insumos` con log de cambio de precio. |
| `scripts/cargar_receta.py` | Upsert de receta + `receta_detalle` desde un archivo JSON. |
| `scripts/cargar_sku.py` | Crear SKU + filas en `sku_envasado` desde un archivo JSON. |
| `scripts/costo_sku.py` | Consultar `vista_costo_sku` y mostrar tabla legible. |
| `.claude/skills/actualizar-precio-insumo/SKILL.md` | Skill que envuelve `actualizar_insumo.py`. |
| `.claude/skills/cargar-receta/SKILL.md` | Skill que envuelve `cargar_receta.py`. |
| `.claude/skills/costos-sku/SKILL.md` | Skill que envuelve `costo_sku.py`. |
| `.claude/CLAUDE.md` | Documentación: nueva sección "Costos de producción (capa B)" + comandos frecuentes. |

---

## Convenciones del proyecto a respetar

- Carga `.env` con función `_load_env()` (NO usar `python-dotenv`).
- Conexión a BD via `DB_CONFIG` con defaults (`dbname=dte_facturas_chile`, `user=postgres`, `port=5432`).
- Transacciones con `with conn:` (commit automático o rollback completo).
- Encoding UTF-8 en todos los archivos. Mensajes al usuario en español.
- Comentarios en español. Variables en inglés camelCase.
- Logs en `logs/`.

---

## Task 1: Migración de esquema

**Files:**
- Create: `scripts/migrate_costos_v2.py`

- [ ] **Step 1: Crear archivo `scripts/migrate_costos_v2.py`**

```python
#!/usr/bin/env python3
"""
migrate_costos_v2.py - Zigurat ERP
Migración del módulo de costos de producción (capa B).
Idempotente: se puede ejecutar múltiples veces sin efectos secundarios.

Uso:
    python scripts/migrate_costos_v2.py
"""

import os
import sys
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

# Categorías estándar para clasificar insumos
CATEGORIAS_VALIDAS = (
    'malta', 'lupulo', 'levadura', 'adjunto', 'clarificante',
    'envase', 'tapa', 'etiqueta', 'caja'
)

# Migración por pasos. Cada string se ejecuta como sentencia única.
MIGRATIONS = [
    # 1. maestro_insumos: agregar columnas categoria, activo, precio_revisar
    """
    ALTER TABLE maestro_insumos
        ADD COLUMN IF NOT EXISTS categoria VARCHAR(20) NOT NULL DEFAULT 'malta',
        ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE,
        ADD COLUMN IF NOT EXISTS precio_revisar BOOLEAN NOT NULL DEFAULT FALSE
    """,
    # 2. CHECK de categoría (drop+create para idempotencia)
    """
    ALTER TABLE maestro_insumos
        DROP CONSTRAINT IF EXISTS chk_categoria_insumo
    """,
    """
    ALTER TABLE maestro_insumos
        ADD CONSTRAINT chk_categoria_insumo CHECK (categoria IN (
            'malta','lupulo','levadura','adjunto','clarificante',
            'envase','tapa','etiqueta','caja'
        ))
    """,
    # 3. recetas: agregar costo_mano_obra_lote, costo_servicios_lote, merma_porcentaje
    """
    ALTER TABLE recetas
        ADD COLUMN IF NOT EXISTS costo_mano_obra_lote NUMERIC(12,2) NOT NULL DEFAULT 300000,
        ADD COLUMN IF NOT EXISTS costo_servicios_lote NUMERIC(12,2) NOT NULL DEFAULT 185000,
        ADD COLUMN IF NOT EXISTS merma_porcentaje     NUMERIC(5,2)  NOT NULL DEFAULT 5.0
    """,
    """
    ALTER TABLE recetas DROP CONSTRAINT IF EXISTS chk_merma
    """,
    """
    ALTER TABLE recetas ADD CONSTRAINT chk_merma CHECK (merma_porcentaje BETWEEN 0 AND 30)
    """,
    """
    ALTER TABLE recetas DROP CONSTRAINT IF EXISTS chk_litros
    """,
    """
    ALTER TABLE recetas ADD CONSTRAINT chk_litros CHECK (litros_lote_estandar > 0)
    """,
    # 4. Tabla formatos
    """
    CREATE TABLE IF NOT EXISTS formatos (
        id           SERIAL PRIMARY KEY,
        nombre       VARCHAR(50) UNIQUE NOT NULL,
        capacidad_ml INTEGER NOT NULL CHECK (capacidad_ml > 0),
        retornable   BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    # 5. Seed de formatos (idempotente con ON CONFLICT)
    """
    INSERT INTO formatos (nombre, capacidad_ml, retornable) VALUES
        ('Botella 330ml',     330,    FALSE),
        ('Barril 30L acero',  30000,  TRUE),
        ('Barril 30L PET',    30000,  FALSE)
    ON CONFLICT (nombre) DO NOTHING
    """,
    # 6. Tabla sku
    """
    CREATE TABLE IF NOT EXISTS sku (
        id            SERIAL PRIMARY KEY,
        receta_id     INTEGER NOT NULL REFERENCES recetas(id),
        formato_id    INTEGER NOT NULL REFERENCES formatos(id),
        codigo        VARCHAR(30) UNIQUE NOT NULL,
        nombre        VARCHAR(100) NOT NULL,
        unidades_caja INTEGER,
        activo        BOOLEAN NOT NULL DEFAULT TRUE,
        UNIQUE(receta_id, formato_id, unidades_caja),
        CHECK (unidades_caja IS NULL OR unidades_caja IN (12, 24))
    )
    """,
    # 7. Tabla sku_envasado
    """
    CREATE TABLE IF NOT EXISTS sku_envasado (
        id        SERIAL PRIMARY KEY,
        sku_id    INTEGER NOT NULL REFERENCES sku(id) ON DELETE CASCADE,
        insumo_id INTEGER NOT NULL REFERENCES maestro_insumos(id),
        cantidad  NUMERIC(10,4) NOT NULL CHECK (cantidad > 0),
        UNIQUE(sku_id, insumo_id)
    )
    """,
    # 8. Recategorizar los 15 insumos existentes (todos son cerveza/líquido)
    """
    UPDATE maestro_insumos
    SET categoria = CASE
        WHEN nombre ILIKE 'malta%'           THEN 'malta'
        WHEN nombre ILIKE '%trigo malteado%' THEN 'malta'
        WHEN nombre ILIKE 'lupulo%'          THEN 'lupulo'
        WHEN nombre ILIKE 'levadura%'        THEN 'levadura'
        WHEN nombre ILIKE 'clarificante%'    THEN 'clarificante'
        ELSE categoria
    END
    WHERE categoria = 'malta' AND nombre IS NOT NULL
    """,
    # 9. Detección de precios sospechosos (maltas > $50.000/kg)
    """
    UPDATE maestro_insumos
    SET precio_revisar = TRUE
    WHERE categoria = 'malta' AND precio_neto_unitario > 50000
    """,
    # 10. Drop vista vieja (si existe) y crear vista_costo_sku
    """
    DROP VIEW IF EXISTS vista_costos_recetas
    """,
    """
    CREATE OR REPLACE VIEW vista_costo_sku AS
    WITH
    costo_liquido AS (
        SELECT r.id AS receta_id,
               r.litros_lote_estandar * (1 - r.merma_porcentaje/100) AS litros_envasables,
               (SUM(rd.cantidad_requerida * mi.precio_neto_unitario)
                + r.costo_mano_obra_lote
                + r.costo_servicios_lote) AS costo_lote_total
        FROM recetas r
        JOIN receta_detalle rd ON rd.receta_id = r.id
        JOIN maestro_insumos mi ON mi.id = rd.insumo_id
        GROUP BY r.id
    ),
    costo_envase AS (
        SELECT se.sku_id,
               SUM(se.cantidad * mi.precio_neto_unitario) AS costo_envasado_unitario
        FROM sku_envasado se
        JOIN maestro_insumos mi ON mi.id = se.insumo_id
        GROUP BY se.sku_id
    )
    SELECT s.id AS sku_id,
           s.codigo,
           s.nombre,
           r.nombre_cerveza,
           f.nombre AS formato,
           cl.costo_lote_total / cl.litros_envasables * (f.capacidad_ml/1000.0) AS costo_liquido_unitario,
           COALESCE(ce.costo_envasado_unitario, 0) AS costo_envasado_unitario,
           (cl.costo_lote_total / cl.litros_envasables * (f.capacidad_ml/1000.0)
            + COALESCE(ce.costo_envasado_unitario, 0)) AS costo_total_unitario
    FROM sku s
    JOIN recetas r        ON r.id = s.receta_id
    JOIN formatos f       ON f.id = s.formato_id
    JOIN costo_liquido cl ON cl.receta_id = s.receta_id
    LEFT JOIN costo_envase ce ON ce.sku_id = s.id
    WHERE s.activo
    """,
]


def main():
    print("=" * 60)
    print("ZIGURAT ERP — Migración Costos de Producción (capa B)")
    print("=" * 60)
    print()

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    print(f"Conectado a: {DB_CONFIG['dbname']}")
    print()

    try:
        with conn:
            cur = conn.cursor()
            for i, sql in enumerate(MIGRATIONS, 1):
                cur.execute(sql)
                print(f"  OK migración {i}/{len(MIGRATIONS)}")

            # Reportar precios sospechosos detectados
            cur.execute("""
                SELECT id, nombre, unidad, precio_neto_unitario
                FROM maestro_insumos
                WHERE precio_revisar = TRUE
                ORDER BY id
            """)
            sospechosos = cur.fetchall()

        print()
        print("Migración completada.")
        print()
        print("Estructura creada:")
        print("  - maestro_insumos: +categoria, +activo, +precio_revisar")
        print("  - recetas: +costo_mano_obra_lote, +costo_servicios_lote, +merma_porcentaje")
        print("  - tabla nueva: formatos (3 filas seed)")
        print("  - tabla nueva: sku")
        print("  - tabla nueva: sku_envasado")
        print("  - vista nueva: vista_costo_sku (reemplaza vista_costos_recetas)")
        print()

        if sospechosos:
            print(f"ATENCIÓN: {len(sospechosos)} insumo(s) con precio sospechoso (precio_revisar=TRUE):")
            for row in sospechosos:
                print(f"  id={row[0]:3d}  {row[1]:30s}  ${row[3]:>15,.2f}/{row[2]}")
            print()
            print("Corregir con: /actualizar-precio-insumo \"<nombre>\" <precio_correcto>")
        else:
            print("Sin precios sospechosos detectados.")

    except psycopg2.Error as e:
        print(f"\nERROR en migración: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Ejecutar migración**

Run: `python scripts/migrate_costos_v2.py`
Expected: 13 mensajes "OK migración i/13" + lista de precios sospechosos (las 2 maltas a $100.840 deberían aparecer).

- [ ] **Step 3: Ejecutar de nuevo (verificar idempotencia)**

Run: `python scripts/migrate_costos_v2.py`
Expected: misma salida sin errores, sin filas duplicadas en `formatos` (debe seguir teniendo 3 filas).

Verificación SQL:
```sql
SELECT COUNT(*) FROM formatos;             -- debe dar 3
SELECT COUNT(*) FROM information_schema.columns
  WHERE table_name='maestro_insumos' AND column_name='categoria';  -- debe dar 1
```

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_costos_v2.py
git commit -m "Agrega migración de capa B de costos (formatos, sku, sku_envasado)"
```

---

## Task 2: Script `actualizar_insumo.py`

**Files:**
- Create: `scripts/actualizar_insumo.py`

- [ ] **Step 1: Crear archivo**

```python
#!/usr/bin/env python3
"""
actualizar_insumo.py - Zigurat ERP
Crea o actualiza un insumo en maestro_insumos.
Loggea cambios de precio en logs/insumos_precios.log.

Uso:
    python actualizar_insumo.py "nombre" unidad precio_neto categoria

Ejemplo:
    python actualizar_insumo.py "Lupulo Citra" gr 9500 lupulo
    python actualizar_insumo.py "Botella 330ml ambar" un 250 envase
"""

import os
import sys
from pathlib import Path
from datetime import datetime

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2.")
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

CATEGORIAS_VALIDAS = (
    'malta', 'lupulo', 'levadura', 'adjunto', 'clarificante',
    'envase', 'tapa', 'etiqueta', 'caja'
)


def _log_cambio_precio(nombre, precio_anterior, precio_nuevo):
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "insumos_precios.log"
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"{ts} | {nombre} | {precio_anterior} -> {precio_nuevo}\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)


def main():
    if len(sys.argv) != 5:
        print('Uso: python actualizar_insumo.py "nombre" unidad precio_neto categoria')
        print('Ejemplo: python actualizar_insumo.py "Lupulo Citra" gr 9500 lupulo')
        print(f'Categorías válidas: {", ".join(CATEGORIAS_VALIDAS)}')
        sys.exit(1)

    nombre        = sys.argv[1].strip()
    unidad        = sys.argv[2].strip()
    precio_raw    = sys.argv[3].replace('.', '').replace(',', '.')
    categoria     = sys.argv[4].strip().lower()

    if categoria not in CATEGORIAS_VALIDAS:
        print(f"ERROR: Categoría '{categoria}' inválida.")
        print(f"Válidas: {', '.join(CATEGORIAS_VALIDAS)}")
        sys.exit(1)

    try:
        precio = float(precio_raw)
    except ValueError:
        print(f"ERROR: Precio inválido: {sys.argv[3]}")
        sys.exit(1)

    if precio <= 0:
        print(f"ERROR: Precio debe ser > 0 (recibido: {precio})")
        sys.exit(1)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    with conn:
        cur = conn.cursor()
        # Buscar si existe
        cur.execute("SELECT id, precio_neto_unitario FROM maestro_insumos WHERE nombre = %s", (nombre,))
        row = cur.fetchone()

        if row:
            insumo_id, precio_anterior = row
            cur.execute("""
                UPDATE maestro_insumos
                SET unidad = %s,
                    precio_neto_unitario = %s,
                    categoria = %s,
                    precio_revisar = FALSE,
                    actualizado_el = NOW()
                WHERE id = %s
            """, (unidad, precio, categoria, insumo_id))
            _log_cambio_precio(nombre, precio_anterior, precio)
            accion = "actualizado"
        else:
            cur.execute("""
                INSERT INTO maestro_insumos (nombre, unidad, precio_neto_unitario, categoria, actualizado_el)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
            """, (nombre, unidad, precio, categoria))
            insumo_id = cur.fetchone()[0]
            _log_cambio_precio(nombre, None, precio)
            accion = "creado"

    conn.close()

    precio_fmt = "$" + "{:,.2f}".format(precio).replace(",", ".")
    print(f"Insumo {accion} (id={insumo_id})")
    print(f"   Nombre:    {nombre}")
    print(f"   Unidad:    {unidad}")
    print(f"   Precio:    {precio_fmt} / {unidad}")
    print(f"   Categoría: {categoria}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Probar creación de insumo nuevo**

Run: `python scripts/actualizar_insumo.py "Lupulo Citra" gr 9500 lupulo`
Expected: "Insumo creado (id=N)" donde N es un id nuevo.

- [ ] **Step 3: Probar actualización de insumo existente**

Run: `python scripts/actualizar_insumo.py "Lupulo Citra" gr 10500 lupulo`
Expected: "Insumo actualizado (id=N)" con el mismo id.

Verificación:
```sql
SELECT * FROM maestro_insumos WHERE nombre='Lupulo Citra';
```
Debe mostrar precio 10500. Y `cat logs/insumos_precios.log` debe tener 2 líneas (creación + actualización).

- [ ] **Step 4: Probar validaciones**

Run cada uno y verificar que falla con mensaje claro:
- `python scripts/actualizar_insumo.py "X" kg 100 categoria_falsa` → ERROR de categoría
- `python scripts/actualizar_insumo.py "X" kg -5 malta` → ERROR de precio ≤ 0
- `python scripts/actualizar_insumo.py "X" kg abc malta` → ERROR de precio inválido

- [ ] **Step 5: Commit**

```bash
git add scripts/actualizar_insumo.py
git commit -m "Agrega script actualizar_insumo.py con log de cambios de precio"
```

---

## Task 3: Script `cargar_receta.py`

**Files:**
- Create: `scripts/cargar_receta.py`
- Create: `scripts/_test_receta_demo.json` (archivo de prueba, se borra al final)

- [ ] **Step 1: Crear archivo `cargar_receta.py`**

```python
#!/usr/bin/env python3
"""
cargar_receta.py - Zigurat ERP
Crea o actualiza una receta y su BOM (receta_detalle) desde un JSON.

Uso:
    python cargar_receta.py recipe.json

Formato del JSON:
{
  "nombre_cerveza": "IPA West Coast Mandarina",
  "litros_lote_estandar": 540,
  "costo_mano_obra_lote": 300000,
  "costo_servicios_lote": 185000,
  "merma_porcentaje": 5.0,
  "insumos": [
    {"nombre": "Malta Pale Ale", "cantidad": 110},
    {"nombre": "Lupulo Citra", "cantidad": 800}
  ]
}

Las cantidades se interpretan en la unidad ya registrada del insumo.
"""

import json
import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2.")
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

CAMPOS_REQUERIDOS = ("nombre_cerveza", "litros_lote_estandar", "insumos")


def _validar_payload(data):
    errores = []
    for campo in CAMPOS_REQUERIDOS:
        if campo not in data:
            errores.append(f"Falta campo requerido: '{campo}'")

    if "litros_lote_estandar" in data and data["litros_lote_estandar"] <= 0:
        errores.append("litros_lote_estandar debe ser > 0")

    merma = data.get("merma_porcentaje", 5.0)
    if not (0 <= merma <= 30):
        errores.append("merma_porcentaje debe estar entre 0 y 30")

    insumos = data.get("insumos", [])
    if not insumos:
        errores.append("La receta no tiene insumos")
    suma = sum(i.get("cantidad", 0) for i in insumos)
    if suma <= 0:
        errores.append("La suma de cantidades de insumos debe ser > 0")

    for i, ins in enumerate(insumos):
        if "nombre" not in ins or "cantidad" not in ins:
            errores.append(f"insumos[{i}] requiere 'nombre' y 'cantidad'")
        elif ins["cantidad"] <= 0:
            errores.append(f"insumos[{i}]: cantidad debe ser > 0")

    return errores


def main():
    if len(sys.argv) != 2:
        print("Uso: python cargar_receta.py recipe.json")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"ERROR: Archivo no encontrado: {json_path}")
        sys.exit(1)

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    errores = _validar_payload(data)
    if errores:
        print("ERROR: payload inválido:")
        for e in errores:
            print(f"  - {e}")
        sys.exit(1)

    nombre_cerveza        = data["nombre_cerveza"].strip()
    litros_lote           = int(data["litros_lote_estandar"])
    costo_mano_obra       = float(data.get("costo_mano_obra_lote", 300000))
    costo_servicios       = float(data.get("costo_servicios_lote", 185000))
    merma                 = float(data.get("merma_porcentaje", 5.0))
    insumos               = data["insumos"]

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            # Mapear nombres de insumos a IDs y validar que existan
            nombres = [i["nombre"].strip() for i in insumos]
            cur.execute(
                "SELECT id, nombre FROM maestro_insumos WHERE nombre = ANY(%s)",
                (nombres,)
            )
            mapa = {row[1]: row[0] for row in cur.fetchall()}
            faltantes = [n for n in nombres if n not in mapa]
            if faltantes:
                print("ERROR: insumos no existen en maestro_insumos:")
                for n in faltantes:
                    print(f"  - {n}")
                print("Crea primero con: /actualizar-precio-insumo")
                sys.exit(1)

            # Upsert receta
            cur.execute(
                "SELECT id FROM recetas WHERE nombre_cerveza = %s",
                (nombre_cerveza,)
            )
            row = cur.fetchone()
            if row:
                receta_id = row[0]
                cur.execute("""
                    UPDATE recetas
                    SET litros_lote_estandar = %s,
                        costo_mano_obra_lote = %s,
                        costo_servicios_lote = %s,
                        merma_porcentaje     = %s
                    WHERE id = %s
                """, (litros_lote, costo_mano_obra, costo_servicios, merma, receta_id))
                cur.execute("DELETE FROM receta_detalle WHERE receta_id = %s", (receta_id,))
                accion = "actualizada"
            else:
                cur.execute("""
                    INSERT INTO recetas
                        (nombre_cerveza, litros_lote_estandar,
                         costo_mano_obra_lote, costo_servicios_lote, merma_porcentaje)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (nombre_cerveza, litros_lote, costo_mano_obra, costo_servicios, merma))
                receta_id = cur.fetchone()[0]
                accion = "creada"

            # Insertar receta_detalle
            for ins in insumos:
                cur.execute("""
                    INSERT INTO receta_detalle (receta_id, insumo_id, cantidad_requerida)
                    VALUES (%s, %s, %s)
                """, (receta_id, mapa[ins["nombre"].strip()], ins["cantidad"]))

            # Costo total del lote (informativo)
            cur.execute("""
                SELECT SUM(rd.cantidad_requerida * mi.precio_neto_unitario)
                FROM receta_detalle rd
                JOIN maestro_insumos mi ON mi.id = rd.insumo_id
                WHERE rd.receta_id = %s
            """, (receta_id,))
            costo_insumos = cur.fetchone()[0] or 0
    finally:
        conn.close()

    costo_lote_total = float(costo_insumos) + costo_mano_obra + costo_servicios
    litros_envasables = litros_lote * (1 - merma / 100)
    costo_por_litro = costo_lote_total / litros_envasables if litros_envasables > 0 else 0

    fmt = lambda v: "$" + "{:,.0f}".format(v).replace(",", ".")
    print(f"Receta {accion} (id={receta_id})")
    print(f"   Cerveza:           {nombre_cerveza}")
    print(f"   Litros lote:       {litros_lote} L (envasables: {litros_envasables:.1f} L con {merma}% merma)")
    print(f"   Insumos:           {len(insumos)}")
    print(f"   Costo insumos:     {fmt(float(costo_insumos))}")
    print(f"   Mano de obra:      {fmt(costo_mano_obra)}")
    print(f"   Servicios:         {fmt(costo_servicios)}")
    print(f"   Costo lote total:  {fmt(costo_lote_total)}")
    print(f"   Costo por litro:   {fmt(costo_por_litro)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Crear JSON de prueba `scripts/_test_receta_demo.json`**

```json
{
  "nombre_cerveza": "Demo Test",
  "litros_lote_estandar": 540,
  "costo_mano_obra_lote": 300000,
  "costo_servicios_lote": 185000,
  "merma_porcentaje": 5.0,
  "insumos": [
    {"nombre": "Lupulo Citra", "cantidad": 500}
  ]
}
```

(Asume que ya cargaste "Lupulo Citra" en la Task 2.)

- [ ] **Step 3: Probar carga**

Run: `python scripts/cargar_receta.py scripts/_test_receta_demo.json`
Expected: "Receta creada (id=N)" con resumen de costos.

- [ ] **Step 4: Probar idempotencia (re-cargar)**

Run de nuevo: `python scripts/cargar_receta.py scripts/_test_receta_demo.json`
Expected: "Receta actualizada (id=N)" mismo id, mismo número de insumos (no se duplican).

Verificación:
```sql
SELECT COUNT(*) FROM receta_detalle rd
  JOIN recetas r ON r.id=rd.receta_id
  WHERE r.nombre_cerveza='Demo Test';
-- debe dar 1 (no 2)
```

- [ ] **Step 5: Probar validaciones**

Crear `scripts/_test_receta_invalida.json`:
```json
{"nombre_cerveza": "X", "litros_lote_estandar": 540, "insumos": [
  {"nombre": "Insumo Inexistente XYZ", "cantidad": 10}
]}
```

Run: `python scripts/cargar_receta.py scripts/_test_receta_invalida.json`
Expected: ERROR con "insumos no existen en maestro_insumos".

- [ ] **Step 6: Limpiar archivos de prueba y receta demo**

```sql
DELETE FROM receta_detalle WHERE receta_id = (SELECT id FROM recetas WHERE nombre_cerveza='Demo Test');
DELETE FROM recetas WHERE nombre_cerveza='Demo Test';
```

```bash
rm scripts/_test_receta_demo.json scripts/_test_receta_invalida.json
```

- [ ] **Step 7: Commit**

```bash
git add scripts/cargar_receta.py
git commit -m "Agrega script cargar_receta.py con upsert idempotente desde JSON"
```

---

## Task 4: Script `cargar_sku.py`

**Files:**
- Create: `scripts/cargar_sku.py`

- [ ] **Step 1: Crear archivo**

```python
#!/usr/bin/env python3
"""
cargar_sku.py - Zigurat ERP
Crea un SKU (combinación cerveza × formato) y su sku_envasado desde JSON.

Uso:
    python cargar_sku.py sku.json

Formato del JSON:
{
  "codigo": "IPA-MAND-330-C12",
  "nombre": "IPA WC Mandarina botella 330ml caja 12",
  "receta": "IPA West Coast Mandarina",
  "formato": "Botella 330ml",
  "unidades_caja": 12,
  "envasado": [
    {"insumo": "Botella 330ml ambar", "cantidad": 1},
    {"insumo": "Tapa corona", "cantidad": 1},
    {"insumo": "Etiqueta IPA Mandarina", "cantidad": 1},
    {"insumo": "Caja carton 12", "cantidad": 0.0833}
  ]
}

Para barriles: omitir unidades_caja y dejar envasado vacío (acero) o
con barril+tapón (PET).
"""

import json
import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2.")
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

CATEGORIAS_ENVASE_VALIDAS = ('envase', 'tapa', 'etiqueta', 'caja')


def main():
    if len(sys.argv) != 2:
        print("Uso: python cargar_sku.py sku.json")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"ERROR: Archivo no encontrado: {json_path}")
        sys.exit(1)

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    # Validación básica de campos
    for campo in ("codigo", "nombre", "receta", "formato"):
        if campo not in data:
            print(f"ERROR: falta campo '{campo}'")
            sys.exit(1)

    codigo        = data["codigo"].strip()
    nombre        = data["nombre"].strip()
    receta_nombre = data["receta"].strip()
    formato_nombre = data["formato"].strip()
    unidades_caja = data.get("unidades_caja")
    envasado      = data.get("envasado", [])

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn:
            cur = conn.cursor()

            # Resolver receta y formato
            cur.execute("SELECT id FROM recetas WHERE nombre_cerveza = %s", (receta_nombre,))
            row = cur.fetchone()
            if not row:
                print(f"ERROR: Receta '{receta_nombre}' no existe.")
                sys.exit(1)
            receta_id = row[0]

            cur.execute("SELECT id, capacidad_ml FROM formatos WHERE nombre = %s", (formato_nombre,))
            row = cur.fetchone()
            if not row:
                print(f"ERROR: Formato '{formato_nombre}' no existe.")
                sys.exit(1)
            formato_id, capacidad_ml = row

            # Validar regla unidades_caja según formato
            es_botella = capacidad_ml < 1000
            if es_botella:
                if unidades_caja not in (12, 24):
                    print("ERROR: para Botella, unidades_caja debe ser 12 o 24.")
                    sys.exit(1)
            else:
                if unidades_caja is not None:
                    print("ERROR: para Barril, unidades_caja debe ser null/omitido.")
                    sys.exit(1)

            # Resolver insumos del envasado y validar categoría
            if envasado:
                nombres_envase = [e["insumo"].strip() for e in envasado]
                cur.execute(
                    "SELECT id, nombre, categoria FROM maestro_insumos WHERE nombre = ANY(%s)",
                    (nombres_envase,)
                )
                rows = cur.fetchall()
                mapa = {r[1]: r for r in rows}
                faltantes = [n for n in nombres_envase if n not in mapa]
                if faltantes:
                    print("ERROR: insumos de envasado no existen en maestro_insumos:")
                    for n in faltantes:
                        print(f"  - {n}")
                    sys.exit(1)
                for n, _, cat in [(r[1], r[0], r[2]) for r in rows]:
                    if cat not in CATEGORIAS_ENVASE_VALIDAS:
                        print(f"ERROR: insumo '{n}' tiene categoría '{cat}' (debe ser envase/tapa/etiqueta/caja).")
                        sys.exit(1)
            else:
                mapa = {}

            # Verificar conflicto de código
            cur.execute("SELECT id, receta_id, formato_id FROM sku WHERE codigo = %s", (codigo,))
            existente = cur.fetchone()
            if existente and (existente[1] != receta_id or existente[2] != formato_id):
                print(f"ERROR: código '{codigo}' ya existe con otra receta/formato.")
                sys.exit(1)

            # Upsert SKU
            if existente:
                sku_id = existente[0]
                cur.execute("""
                    UPDATE sku SET nombre = %s, unidades_caja = %s, activo = TRUE WHERE id = %s
                """, (nombre, unidades_caja, sku_id))
                cur.execute("DELETE FROM sku_envasado WHERE sku_id = %s", (sku_id,))
                accion = "actualizado"
            else:
                cur.execute("""
                    INSERT INTO sku (receta_id, formato_id, codigo, nombre, unidades_caja)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (receta_id, formato_id, codigo, nombre, unidades_caja))
                sku_id = cur.fetchone()[0]
                accion = "creado"

            for e in envasado:
                cur.execute("""
                    INSERT INTO sku_envasado (sku_id, insumo_id, cantidad)
                    VALUES (%s, %s, %s)
                """, (sku_id, mapa[e["insumo"].strip()][0], e["cantidad"]))
    finally:
        conn.close()

    print(f"SKU {accion} (id={sku_id})")
    print(f"   Código:   {codigo}")
    print(f"   Nombre:   {nombre}")
    print(f"   Receta:   {receta_nombre}")
    print(f"   Formato:  {formato_nombre}")
    if unidades_caja:
        print(f"   Caja:     {unidades_caja} unidades")
    print(f"   Envasado: {len(envasado)} insumo(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Recrear receta demo (necesaria para probar el SKU)**

Crear `scripts/_test_receta_demo.json`:
```json
{
  "nombre_cerveza": "Demo Test",
  "litros_lote_estandar": 540,
  "costo_mano_obra_lote": 300000,
  "costo_servicios_lote": 185000,
  "merma_porcentaje": 5.0,
  "insumos": [
    {"nombre": "Lupulo Citra", "cantidad": 500}
  ]
}
```

Run: `python scripts/cargar_receta.py scripts/_test_receta_demo.json`
Expected: "Receta creada (id=N)".

- [ ] **Step 3: Probar caso "barril acero" (sin envasado)**

Crear `scripts/_test_sku_barril.json`:
```json
{
  "codigo": "DEMO-30LA",
  "nombre": "Demo barril 30L acero",
  "receta": "Demo Test",
  "formato": "Barril 30L acero",
  "envasado": []
}
```

Run: `python scripts/cargar_sku.py scripts/_test_sku_barril.json`
Expected: "SKU creado (id=N)".

- [ ] **Step 4: Probar regla "barril no lleva unidades_caja"**

Crear `scripts/_test_sku_barril_invalido.json`:
```json
{
  "codigo": "DEMO-30LA-X",
  "nombre": "X",
  "receta": "Demo Test",
  "formato": "Barril 30L acero",
  "unidades_caja": 12,
  "envasado": []
}
```

Run: `python scripts/cargar_sku.py scripts/_test_sku_barril_invalido.json`
Expected: ERROR "para Barril, unidades_caja debe ser null/omitido."

- [ ] **Step 5: Limpiar**

```sql
DELETE FROM sku WHERE codigo IN ('DEMO-30LA','DEMO-30LA-X');
DELETE FROM receta_detalle WHERE receta_id = (SELECT id FROM recetas WHERE nombre_cerveza='Demo Test');
DELETE FROM recetas WHERE nombre_cerveza='Demo Test';
```

```bash
rm scripts/_test_sku_barril.json scripts/_test_sku_barril_invalido.json scripts/_test_receta_demo.json
```

- [ ] **Step 6: Commit**

```bash
git add scripts/cargar_sku.py
git commit -m "Agrega script cargar_sku.py con validación de envasado por formato"
```

---

## Task 5: Script `costo_sku.py`

**Files:**
- Create: `scripts/costo_sku.py`

- [ ] **Step 1: Crear archivo**

```python
#!/usr/bin/env python3
"""
costo_sku.py - Zigurat ERP
Consulta vista_costo_sku y muestra una tabla legible de costos unitarios.

Uso:
    python costo_sku.py
    python costo_sku.py --sku CREAM-330-C12
    python costo_sku.py --receta "Cream Ale"
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2.")
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


def fmt_clp(v):
    if v is None:
        return "    --"
    return "$" + "{:,.0f}".format(v).replace(",", ".")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sku", help="Filtrar por código de SKU")
    ap.add_argument("--receta", help="Filtrar por nombre de cerveza")
    args = ap.parse_args()

    where = []
    params = []
    if args.sku:
        where.append("codigo = %s")
        params.append(args.sku)
    if args.receta:
        where.append("nombre_cerveza ILIKE %s")
        params.append(f"%{args.receta}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT codigo, nombre_cerveza, formato,
               costo_liquido_unitario, costo_envasado_unitario, costo_total_unitario
        FROM vista_costo_sku
        {where_sql}
        ORDER BY nombre_cerveza, formato, codigo
    """

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    with conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

        # SKUs sospechosos: botella sin envasado
        cur.execute("""
            SELECT s.codigo
            FROM sku s
            JOIN formatos f ON f.id = s.formato_id
            LEFT JOIN sku_envasado se ON se.sku_id = s.id
            WHERE s.activo AND f.capacidad_ml < 1000 AND se.id IS NULL
        """)
        botellas_sin_envase = {r[0] for r in cur.fetchall()}
    conn.close()

    if not rows:
        print("Sin SKUs cargados (o sin coincidencias).")
        return

    print(f"{'SKU':<18} {'CERVEZA':<25} {'FORMATO':<20} {'LIQUIDO':>10} {'ENVASE':>10} {'TOTAL':>10}")
    print("-" * 100)
    for r in rows:
        codigo, cerveza, formato, liquido, envase, total = r
        flag = ""
        if total is None or total < 0:
            flag = " [!]"
        elif codigo in botellas_sin_envase:
            flag = " [sin envasado]"
        print(f"{codigo:<18} {cerveza:<25} {formato:<20} {fmt_clp(liquido):>10} {fmt_clp(envase):>10} {fmt_clp(total):>10}{flag}")

    print()
    print(f"Total: {len(rows)} SKU(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Probar consulta sin SKUs cargados**

Run: `python scripts/costo_sku.py`
Expected: "Sin SKUs cargados (o sin coincidencias)." (es lo esperado en este punto del plan).

- [ ] **Step 3: Probar filtros (no deberían fallar aunque no haya datos)**

Run: `python scripts/costo_sku.py --sku CREAM-330-C12`
Run: `python scripts/costo_sku.py --receta "Cream"`
Expected: Sin errores.

- [ ] **Step 4: Commit**

```bash
git add scripts/costo_sku.py
git commit -m "Agrega script costo_sku.py para consultar vista_costo_sku"
```

---

## Task 6: Skill `/actualizar-precio-insumo`

**Files:**
- Create: `.claude/skills/actualizar-precio-insumo/SKILL.md`

- [ ] **Step 1: Crear archivo**

```markdown
---
name: actualizar-precio-insumo
description: >
  Crea o actualiza el precio neto unitario de un insumo en maestro_insumos
  (capa de costos). Usar cuando el usuario diga que subió/bajó un insumo,
  cuando quiera cargar un insumo nuevo (malta, lúpulo, levadura, adjunto,
  botella, tapa, etiqueta, caja, barril PET), o cuando la migración haya
  marcado precios sospechosos. Ejemplos: "subió el lupulo Citra a 9500",
  "agrega botella 330ml a $250", "cambia el precio de Malta Pale Ale".
argument-hint: '"nombre" unidad precio_neto categoria'
disable-model-invocation: false
allowed-tools: Bash(python *)
---

# Actualizar Precio de Insumo — Zigurat ERP

Wraps `scripts/actualizar_insumo.py`. Crea o actualiza un insumo en
`maestro_insumos`. El script hace upsert por `nombre` y deja log del
precio anterior en `logs/insumos_precios.log`.

## Reglas

- NUNCA pedir confirmación antes de ejecutar.
- Inferir parámetros del mensaje del usuario.
- Si el usuario no da unidad, preguntarla (kg, gr, ml, un).
- Si el usuario no da categoría, inferirla del nombre o preguntarla.

## Categorías válidas

- `malta`, `lupulo`, `levadura`, `clarificante` — insumos de líquido
- `adjunto` — mandarina, café, cacao, miel, frutas, especias
- `envase` — botella vacía, barril PET
- `tapa` — tapa corona, tapón PET
- `etiqueta` — etiqueta de cerveza específica
- `caja` — caja de cartón 12 o 24

## Paso 1 — Extraer parámetros

Del mensaje extraer:
- `nombre` (ej: "Lupulo Citra", "Botella 330ml ambar")
- `unidad` (kg, gr, ml, un)
- `precio_neto` (en pesos chilenos, sin puntos ni signo)
- `categoria` (de la lista anterior)

## Paso 2 — Ejecutar

```bash
python scripts/actualizar_insumo.py "NOMBRE" UNIDAD PRECIO CATEGORIA
```

## Paso 3 — Confirmar

Mostrar el resultado del script. Si era un insumo de líquido y existen
SKUs activos que lo usan, sugerir `/costos-sku` para ver el impacto.
```

- [ ] **Step 2: Verificar formato (no romper YAML frontmatter)**

Run: `python -c "import yaml; print(yaml.safe_load(open('.claude/skills/actualizar-precio-insumo/SKILL.md').read().split('---')[1]))"`
Expected: Imprime un dict con name, description, etc.

(Si no tienes pyyaml: `pip install pyyaml` o salta este paso y verifica el SKILL.md visualmente.)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/actualizar-precio-insumo/SKILL.md
git commit -m "Agrega skill /actualizar-precio-insumo"
```

---

## Task 7: Skill `/cargar-receta`

**Files:**
- Create: `.claude/skills/cargar-receta/SKILL.md`

- [ ] **Step 1: Crear archivo**

```markdown
---
name: cargar-receta
description: >
  Crea o actualiza una receta de cerveza con su BOM (lista de insumos)
  en la base de datos de costos. Usar cuando el usuario quiera ingresar
  una receta nueva, modificar una existente, o revisar la fórmula de una
  cerveza. Ejemplos: "carga la receta de IPA Mandarina", "actualiza la
  Cream Ale", "ingresa la fórmula del Stout café cacao".
argument-hint: '<nombre_receta_o_descripcion_libre>'
disable-model-invocation: false
allowed-tools: Bash(python *), Write
---

# Cargar Receta — Zigurat ERP

Wraps `scripts/cargar_receta.py`. El script lee un JSON con la receta
y hace upsert en las tablas `recetas` y `receta_detalle`.

## Reglas

- Las cantidades en el JSON se interpretan en la unidad ya registrada
  en `maestro_insumos` para cada insumo (kg, gr, ml).
- Todos los insumos deben existir antes en `maestro_insumos`. Si falta
  alguno, primero ejecutar `/actualizar-precio-insumo` para crearlo.

## Paso 1 — Recolectar la receta del usuario

Pedir o inferir del mensaje:
- Nombre de la cerveza
- Litros del lote estándar (default 540)
- Lista de insumos con cantidad cada uno
- (Opcional) costo_mano_obra_lote (default 300000)
- (Opcional) costo_servicios_lote (default 185000)
- (Opcional) merma_porcentaje (default 5.0)

## Paso 2 — Validar que insumos existen

Antes de armar el JSON, consultar maestro_insumos:

```sql
SELECT nombre FROM maestro_insumos WHERE nombre = ANY(ARRAY['nombre1','nombre2',...]);
```

Si falta alguno → pedir al usuario los datos del insumo faltante y ejecutar
`/actualizar-precio-insumo` antes de continuar.

## Paso 3 — Escribir JSON temporal

Usar Write para crear `logs/_receta_YYYYMMDD_HHMMSS.json` (la carpeta `logs/`
ya existe en el repo y está en `.gitignore` para los temporales). Estructura:

```json
{
  "nombre_cerveza": "...",
  "litros_lote_estandar": 540,
  "costo_mano_obra_lote": 300000,
  "costo_servicios_lote": 185000,
  "merma_porcentaje": 5.0,
  "insumos": [
    {"nombre": "...", "cantidad": ...}
  ]
}
```

## Paso 4 — Ejecutar

```bash
python scripts/cargar_receta.py logs/_receta_YYYYMMDD_HHMMSS.json
```

## Paso 5 — Confirmar

Mostrar el resumen de costos del script. Sugerir `/costos-sku --receta "<nombre>"`
si ya hay SKUs cargados para esta cerveza.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/cargar-receta/SKILL.md
git commit -m "Agrega skill /cargar-receta"
```

---

## Task 8: Skill `/costos-sku`

**Files:**
- Create: `.claude/skills/costos-sku/SKILL.md`

- [ ] **Step 1: Crear archivo**

```markdown
---
name: costos-sku
description: >
  Consulta el costo unitario real de los SKUs (cervezas × formatos) ya
  cargados en la base de datos. Usar cuando el usuario quiera saber cuánto
  cuesta producir una botella o un barril, comparar costos entre cervezas
  o formatos, o validar el resultado después de actualizar precios.
  Ejemplos: "cuánto me cuesta una Cream Ale 330", "muestra los costos",
  "costo de los barriles", "qué tan caro está el Stout".
argument-hint: '[--sku CODIGO | --receta NOMBRE]'
disable-model-invocation: false
allowed-tools: Bash(python *)
---

# Costos por SKU — Zigurat ERP

Wraps `scripts/costo_sku.py`. Consulta `vista_costo_sku` y muestra una
tabla con costo del líquido, costo de envasado y costo total por unidad.

## Reglas

- NUNCA pedir confirmación antes de consultar.
- Si el usuario pregunta por una cerveza específica, usar `--receta`.
- Si pregunta por un código exacto, usar `--sku`.
- Sin filtros muestra todos los SKUs activos.

## Paso 1 — Decidir el filtro

| Pregunta del usuario | Comando |
|---|---|
| "cuánto me cuesta X" donde X es nombre cerveza | `--receta "X"` |
| "cuánto cuesta el SKU Y" donde Y es código | `--sku Y` |
| "muestra los costos" / "todos" | sin argumentos |

## Paso 2 — Ejecutar

```bash
python scripts/costo_sku.py [--sku CODIGO] [--receta NOMBRE]
```

## Paso 3 — Interpretar la salida

La salida tiene columnas: SKU | CERVEZA | FORMATO | LIQUIDO | ENVASE | TOTAL.

- `[!]` después del costo → revisar receta (costo negativo o NULL).
- `[sin envasado]` → SKU de botella sin filas en `sku_envasado`. Ejecutar
  `/cargar-sku` para corregir.

Comentar al usuario:
- El SKU más barato y el más caro
- Si hay SKUs marcados con `[!]` o `[sin envasado]`, mencionarlos
- Cualquier costo que parezca fuera de banda (botella 330ml < $400 o > $1.500
  suele ser señal de error en datos)
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/costos-sku/SKILL.md
git commit -m "Agrega skill /costos-sku"
```

---

## Task 9: Documentación CLAUDE.md

**Files:**
- Modify: `.claude/CLAUDE.md`

- [ ] **Step 1: Leer la sección "Comandos frecuentes" actual**

```bash
grep -n "Comandos frecuentes" .claude/CLAUDE.md
```

- [ ] **Step 2: Agregar comandos de costos en "Comandos frecuentes"**

Después del bloque de comandos de wiki (`/wiki-init`, `/perfil-cliente`, `/wiki-lint`), agregar antes de `# Ejecutar scripts individuales`:

```markdown
# Costos de producción (capa B)
/actualizar-precio-insumo "Lupulo Citra" gr 9500 lupulo
/cargar-receta                    # Carga/actualiza receta + BOM desde JSON
/costos-sku                       # Tabla de costo unitario por SKU
/costos-sku --sku CREAM-330-C12   # Costo de un SKU específico
/costos-sku --receta "Cream Ale"  # Costos de todos los formatos de una cerveza
```

Y en la sección `# Ejecutar scripts individuales`:

```markdown
python scripts/migrate_costos_v2.py                          # Migración esquema costos
python scripts/actualizar_insumo.py "nombre" unidad precio cat
python scripts/cargar_receta.py recipe.json
python scripts/cargar_sku.py sku.json
python scripts/costo_sku.py [--sku COD | --receta NOMBRE]
```

- [ ] **Step 3: Agregar nueva sección "Costos de producción (capa B)"**

Después de la sección "Wiki de clientes (Karpathy LLM Wiki)" agregar:

```markdown
## Costos de producción (capa B)

Calcula el costo unitario real de cada SKU vendible (cerveza × formato)
combinando insumos de líquido + envasado + mano de obra + servicios
variables del lote.

### Tablas

| Tabla | Propósito |
|-------|-----------|
| `maestro_insumos` | Catálogo de insumos con `categoria` (malta, lupulo, levadura, adjunto, clarificante, envase, tapa, etiqueta, caja) y `precio_neto_unitario`. |
| `recetas` | Una fila por cerveza, con `costo_mano_obra_lote`, `costo_servicios_lote` y `merma_porcentaje`. |
| `receta_detalle` | BOM de líquido por receta. |
| `formatos` | Catálogo plano: Botella 330ml / Barril 30L acero / Barril 30L PET. |
| `sku` | Una fila por (receta, formato, unidades_caja). Caja 12 y caja 24 son SKUs distintos. |
| `sku_envasado` | BOM de envasado por SKU. Vacío para barriles retornables. |

### Vista

`vista_costo_sku` entrega `costo_liquido_unitario`, `costo_envasado_unitario`
y `costo_total_unitario` por cada SKU activo. **Nunca calcular costo a mano** — consultar siempre la vista.

### Flujo de uso

```
/actualizar-precio-insumo  → mantiene maestro_insumos
/cargar-receta             → mantiene recetas + receta_detalle
/cargar-sku (CLI)          → mantiene sku + sku_envasado
/costos-sku                → consulta vista_costo_sku
```

### Parámetros estándar (lote 540 L, 4 lotes/mes)

- Mano de obra: $300.000/lote (retiros tuyo + socio).
- Servicios variables (agua/luz/gas): $185.000/lote.
- Merma de envasado: 5%.

Editables por receta.

### Lo que NO hace esta capa

- No descuenta inventario al producir.
- No registra órdenes de producción.
- No prorratea costos fijos / overhead (capa C).
- No procesa DTEs recibidos (sub-proyecto aparte).
```

- [ ] **Step 4: Verificar visualmente que el archivo sigue válido**

```bash
head -20 .claude/CLAUDE.md && echo "---" && wc -l .claude/CLAUDE.md
```

- [ ] **Step 5: Commit**

```bash
git add .claude/CLAUDE.md
git commit -m "Documenta capa B de costos en CLAUDE.md"
```

---

## Validación final del plan

Después de completar las 9 tareas, validar end-to-end:

- [ ] **Migración corre limpia sobre la BD actual**

```bash
python scripts/migrate_costos_v2.py
```
Expected: Lista los precios sospechosos (Malta Pale Ale, Malta Pilsen).

- [ ] **Setup operativo (FUERA del scope técnico — requiere datos del usuario)**

Esto NO está en el plan automatizable. Después de ejecutar las 9 tareas,
el usuario debe (en sesión interactiva con el agente):

1. Corregir precios mal cargados con `/actualizar-precio-insumo` (15 insumos).
2. Cargar insumos faltantes: lúpulos nuevos, mandarina deshidratada, café,
   cacao, botella 330ml, tapa corona, 4 etiquetas (una por cerveza), caja 12,
   caja 24, barril PET 30L, tapón PET.
3. Cargar las 4 recetas con `/cargar-receta`.
4. Cargar los SKUs con `python scripts/cargar_sku.py <sku>.json` (uno por
   archivo, ~16 SKUs).
5. Validar con `/costos-sku`:
   - Cream Ale 330ml caja 12 → entre **$500 y $1.200**
   - Scotch Ale 30L acero → entre **$25.000 y $55.000**

Si los costos están fuera de banda, hay precios mal cargados.

---

## Resumen del plan

9 tareas, ~12-15 commits, 0 dependencias nuevas. Todo el código corre con
las dependencias ya instaladas (`psycopg2-binary` ya está). La única
tarea no-automatizable es la carga de datos reales (precios y recetas
exactas), que se hace en sesión interactiva después.
