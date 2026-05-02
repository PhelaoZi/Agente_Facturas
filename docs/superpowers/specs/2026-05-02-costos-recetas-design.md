# Costos de producción por SKU — Capa B

**Fecha:** 2026-05-02
**Proyecto:** Zigurat ERP — Agente Facturas
**Alcance:** Calcular el costo unitario real de cada SKU vendible (botella, barril) considerando insumos de cerveza, envasado, mano de obra variable y servicios variables del lote.

---

## 1. Objetivo y motivación

Hoy la base de datos tiene un esquema parcial de costos (`maestro_insumos`, `recetas`, `receta_detalle`, `vista_costos_recetas`) que solo cubre insumos de cerveza y devuelve costos absurdos (~$70.000/L) por precios mal escalados.

Esta capa entrega un costo unitario **realista y operacional por SKU** que sirva para:

- Fijar precios de venta con margen sano.
- Detectar SKUs poco rentables.
- Servir de base para una capa C futura (overhead) y para cruces con `ventas` (margen real por cliente).

### Fuera de alcance (capas posteriores)

- Inventario de insumos consumidos (no se descuenta stock al producir).
- Órdenes de producción.
- Prorrateo de costos fijos / overhead (capa C).
- Procesamiento de DTEs recibidos del SII para auto-actualizar precios (sub-proyecto aparte).
- Margen real por cliente cruzando `vista_costo_sku` con `ventas` (skill posterior).

### Cervezas en alcance

1. Cream Ale (existente en BD)
2. Scotch Ale (existente en BD)
3. IPA West Coast con mandarina (nueva)
4. Stout con café y cacao (nueva)

### Formatos en alcance

| Formato | Capacidad | Lleva costo de envase |
|---|---|---|
| Botella 330 ml | 330 ml | Sí (botella + tapa + etiqueta + caja prorrateada) |
| Barril 30 L acero (retornable) | 30.000 ml | No |
| Barril 30 L PET (un solo uso, regiones) | 30.000 ml | Sí (barril PET + tapón) |

Las botellas se venden en **caja de 12 o caja de 24** según cliente — son SKUs distintos.

### Parámetros de costo del lote estándar (540 L)

- Mano de obra (retiros tuyo + socio): **$300.000/lote** (deriva de $150.000/semana c/u, 1 lote/semana).
- Servicios variables (agua + electricidad + gas): **$185.000/lote** ($420.000 + $320.000 mensual / 4 lotes).
- Merma de envasado: **5%** (540 L teórico → 513 L envasable).

Estos valores son editables por receta.

---

## 2. Modelo de datos

### 2.1 `maestro_insumos` (existente, +2 columnas)

```sql
ALTER TABLE maestro_insumos
  ADD COLUMN IF NOT EXISTS categoria VARCHAR(20) NOT NULL DEFAULT 'malta',
  ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS precio_revisar BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE maestro_insumos
  ADD CONSTRAINT chk_categoria_insumo CHECK (categoria IN (
    'malta', 'lupulo', 'levadura', 'adjunto', 'clarificante',
    'envase', 'tapa', 'etiqueta', 'caja'
  ));
```

- `categoria` distingue insumos de líquido vs envasado.
- `activo=FALSE` marca insumo discontinuado sin perder histórico.
- `precio_revisar=TRUE` flagea precios sospechosos detectados por la migración (no se borra el dato — el usuario decide).

### 2.2 `recetas` (existente, +3 columnas)

```sql
ALTER TABLE recetas
  ADD COLUMN IF NOT EXISTS costo_mano_obra_lote NUMERIC(12,2) NOT NULL DEFAULT 300000,
  ADD COLUMN IF NOT EXISTS costo_servicios_lote NUMERIC(12,2) NOT NULL DEFAULT 185000,
  ADD COLUMN IF NOT EXISTS merma_porcentaje     NUMERIC(5,2)  NOT NULL DEFAULT 5.0;

ALTER TABLE recetas
  ADD CONSTRAINT chk_merma CHECK (merma_porcentaje BETWEEN 0 AND 30),
  ADD CONSTRAINT chk_litros CHECK (litros_lote_estandar > 0);

-- La columna existente `costo_fijo_estimado` (siempre 0.00 hoy) queda
-- deprecada. NO se elimina en esta migración para no romper consumidores
-- históricos. La nueva vista `vista_costo_sku` la ignora. Se eliminará
-- en una migración posterior una vez verificado que nadie la lee.
```

### 2.3 `formatos` (nueva)

```sql
CREATE TABLE IF NOT EXISTS formatos (
  id           SERIAL PRIMARY KEY,
  nombre       VARCHAR(50) UNIQUE NOT NULL,
  capacidad_ml INTEGER NOT NULL CHECK (capacidad_ml > 0),
  retornable   BOOLEAN NOT NULL DEFAULT FALSE
);

INSERT INTO formatos (nombre, capacidad_ml, retornable) VALUES
  ('Botella 330ml',     330,    FALSE),
  ('Barril 30L acero',  30000,  TRUE),
  ('Barril 30L PET',    30000,  FALSE)
ON CONFLICT (nombre) DO NOTHING;
```

### 2.4 `sku` (nueva)

```sql
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
);
```

Convenciones de código:
- Botella: `<RECETA>-330-C12` o `<RECETA>-330-C24`
- Barril acero: `<RECETA>-30LA`
- Barril PET: `<RECETA>-30LP`

### 2.5 `sku_envasado` (nueva)

```sql
CREATE TABLE IF NOT EXISTS sku_envasado (
  id        SERIAL PRIMARY KEY,
  sku_id    INTEGER NOT NULL REFERENCES sku(id) ON DELETE CASCADE,
  insumo_id INTEGER NOT NULL REFERENCES maestro_insumos(id),
  cantidad  NUMERIC(10,4) NOT NULL CHECK (cantidad > 0),
  UNIQUE(sku_id, insumo_id)
);
```

Ejemplos:

| SKU | Insumos en `sku_envasado` |
|---|---|
| `CREAM-330-C12` | 1 botella + 1 tapa + 1 etiqueta + 0.0833 caja(12) |
| `CREAM-330-C24` | 1 botella + 1 tapa + 1 etiqueta + 0.0417 caja(24) |
| `CREAM-30LA` | (sin filas — barril retornable, sin envase) |
| `CREAM-30LP` | 1 barril PET + 1 tapón PET |

### 2.6 `vista_costo_sku` (nueva, reemplaza `vista_costos_recetas`)

```sql
DROP VIEW IF EXISTS vista_costos_recetas;

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
JOIN recetas r       ON r.id = s.receta_id
JOIN formatos f      ON f.id = s.formato_id
JOIN costo_liquido cl ON cl.receta_id = s.receta_id
LEFT JOIN costo_envase ce ON ce.sku_id = s.id
WHERE s.activo;
```

---

## 3. Componentes (scripts y skills)

### 3.1 Scripts en `scripts/`

| Script | Propósito | Argumentos |
|---|---|---|
| `migrate_costos_v2.py` | Migración idempotente del esquema. | (sin args) |
| `actualizar_insumo.py` | Crea o actualiza un insumo. Loggea precio anterior. | `nombre unidad precio_neto categoria` |
| `cargar_receta.py` | Crea/actualiza una receta y su BOM desde JSON. | `recipe.json` |
| `cargar_sku.py` | Crea un SKU y su `sku_envasado` desde JSON. | `sku.json` |
| `costo_sku.py` | Consulta `vista_costo_sku` y muestra tabla. | `[--sku CODIGO] [--receta NOMBRE]` |

Todos cargan `.env` con la función `_load_env()` ya usada en el resto del proyecto. Todos usan `with conn:` para transacciones y rollback automático en error.

### 3.2 Skills en `.claude/skills/`

| Skill | Wraps | Cuándo se usa |
|---|---|---|
| `/cargar-receta` | `cargar_receta.py` | Cargar las 4 recetas iniciales y futuras. El agente arma el JSON desde lenguaje natural. |
| `/costos-sku` | `costo_sku.py` | Consultar costos: "¿cuánto me cuesta una Cream Ale 330?" |
| `/actualizar-precio-insumo` | `actualizar_insumo.py` | "Subió el lúpulo Magnum a $9.500/kg" |

### 3.3 Detalle por script

**`migrate_costos_v2.py`** — orden de operaciones:

1. `ALTER TABLE maestro_insumos` (categoria, activo, precio_revisar) — `IF NOT EXISTS`.
2. `ALTER TABLE recetas` (3 columnas + 2 CHECKs).
3. `CREATE TABLE IF NOT EXISTS formatos`, `sku`, `sku_envasado`.
4. `INSERT INTO formatos` con las 3 filas seed (`ON CONFLICT DO NOTHING`).
5. **Recategorizar** los 15 insumos existentes: todos son cerveza (malta/lupulo/levadura/clarificante), ninguno es envase. UPDATE con CASE basado en `nombre`.
6. **Detección de precios sospechosos**: marca `precio_revisar=TRUE` en insumos con precio > $50.000 (maltas/kg) o > $50.000 (lúpulos/gr). Emite log con la lista. **No los modifica.**
7. `DROP VIEW IF EXISTS vista_costos_recetas; CREATE VIEW vista_costo_sku ...`.

Idempotencia: una segunda corrida no debe duplicar filas, no debe re-flagear precios ya corregidos manualmente, no debe tirar error.

**`cargar_receta.py`** — input JSON:

```json
{
  "nombre_cerveza": "IPA West Coast Mandarina",
  "litros_lote_estandar": 540,
  "costo_mano_obra_lote": 300000,
  "costo_servicios_lote": 185000,
  "merma_porcentaje": 5.0,
  "insumos": [
    {"nombre": "Malta Pale Ale", "cantidad": 110},
    {"nombre": "Lupulo Citra",   "cantidad": 800},
    {"nombre": "Mandarina deshidratada", "cantidad": 2000}
  ]
}
```

Reglas:
- La `cantidad` se interpreta en la unidad ya registrada en `maestro_insumos` para ese insumo (kg, gr, ml). El JSON no especifica unidad — se confía en la del maestro.
- Insumo no encontrado → falla con mensaje "Insumo X no existe. Crea primero con `/actualizar-precio-insumo`".
- Receta existente → upsert: borra `receta_detalle` y reinserta dentro de `with conn:`. Rollback completo en error.
- `litros_lote_estandar > 0`, `0 ≤ merma_porcentaje ≤ 30`, suma de cantidades > 0.

**`cargar_sku.py`** — input JSON:

```json
{
  "codigo": "IPA-MAND-330-C12",
  "receta": "IPA West Coast Mandarina",
  "formato": "Botella 330ml",
  "unidades_caja": 12,
  "envasado": [
    {"insumo": "Botella 330ml ambar",  "cantidad": 1},
    {"insumo": "Tapa corona",          "cantidad": 1},
    {"insumo": "Etiqueta IPA Mandarina","cantidad": 1},
    {"insumo": "Caja carton 12",       "cantidad": 0.0833}
  ]
}
```

Reglas:
- Receta y formato deben existir.
- Código SKU único; si ya existe con receta/formato distintos, rechaza.
- Botella 330ml exige `unidades_caja IN (12, 24)`; barril rechaza `unidades_caja` ≠ NULL.
- Insumos de envasado deben tener `categoria IN ('envase','tapa','etiqueta','caja')`.

**`costo_sku.py`** — output ejemplo:

```
SKU              CERVEZA          FORMATO            LIQUIDO   ENVASE   TOTAL
CREAM-330-C12    Cream Ale        Botella 330ml         $487     $312     $799
CREAM-330-C24    Cream Ale        Botella 330ml         $487     $284     $771
CREAM-30LA       Cream Ale        Barril 30L acero    $44.290      $0  $44.290
CREAM-30LP       Cream Ale        Barril 30L PET      $44.290   $7.500  $51.790
```

- Si `costo_total_unitario` < 0 o NULL → marca `[!]` y pide revisar receta.
- Si SKU es botella sin `sku_envasado` → warning "falta cargar envasado".

---

## 4. Flujo de uso

### 4.1 Setup inicial (una sola vez)

```
1. python scripts/migrate_costos_v2.py
2. /actualizar-precio-insumo  ×N   (corregir 15 precios + cargar nuevos insumos)
3. /cargar-receta             ×4   (Cream, Scotch, IPA Mandarina, Stout café/cacao)
4. /cargar-sku                ×N   (hasta 16 SKUs: 4 cervezas × {330-C12, 330-C24, 30LA, 30LP})
5. /costos-sku                     (validación de cordura)
```

### 4.2 Uso recurrente

| Caso | Comando |
|---|---|
| Subió un insumo | `/actualizar-precio-insumo "Lupulo Citra" 9500` |
| Cambia retiro / servicios | `UPDATE recetas SET costo_mano_obra_lote = X` (o script auxiliar futuro) |
| Costo actual de todos los SKUs | `/costos-sku` |
| Costo de un SKU específico | `/costos-sku --sku CREAM-330-C12` |
| Nueva cerveza | `/cargar-receta` + `/cargar-sku` por cada formato |

### 4.3 Integración con el resto del ERP

- **Sin tocar** `ventas`, `clientes`, `productos`, `cuentas_por_pagar`, `wiki/*`.
- Lectura pura desde SQL — no se ejecuta en cada `/sync-facturas`.
- `vista_costo_sku` queda preparada para extenderse con columna `costo_overhead_unitario` en capa C, sin romper consumidores.
- Capa de DTE recibidos podrá auto-disparar `actualizar_insumo.py` al detectar factura de compra de insumo conocido — la firma del script ya lo permite.

---

## 5. Manejo de errores y edge cases

### Validaciones por script

Detalladas en sección 3.3. Resumen:
- Precios ≤ 0 rechazados.
- Categorías fuera del CHECK rechazadas.
- Receta vacía o con insumos inexistentes rechazada.
- SKU con formato/cantidad de caja inválido rechazado.

### Edge cases

| Caso | Comportamiento |
|---|---|
| Lote sin merma | Permitido (0–30%). |
| SKU sin envasado | LEFT JOIN → `costo_envasado_unitario = 0`. |
| `costo_servicios_lote = 0` | Permitido — caso transitorio. |
| Insumo discontinuado | `activo=FALSE`. Recetas existentes siguen funcionando. Nuevas recetas no pueden agregarlo. |
| Cambio de unidad de un insumo | No se permite. Crear insumo nuevo y migrar recetas. |
| Caja 12 vs 24 | SKUs distintos con prorrateo distinto. |
| Precio CLP con miles formateados | Scripts limpian `.` y `,` antes de parsear. |

### Testing manual de aceptación

1. `migrate_costos_v2.py` corre 2 veces sin error ni duplicados.
2. Después del setup completo:
   - Cream Ale 330ml caja 12 → costo entre **$500 y $1.200**.
   - Scotch Ale 30L acero → costo entre **$25.000 y $55.000**.
   - Fuera de banda → revisar precios mal cargados.
3. Cambiar precio de Malta Pale Ale → todos los SKUs que la usan se mueven en consecuencia al consultar `/costos-sku`.

### Sin tests automáticos

Esta capa no incluye pytest. Razón: SQL declarativo + scripts CRUD finos. La validación es manual por checklist + constraints de BD (CHECK, FK, UNIQUE) que actúan como tests permanentes. Si surgen bugs en uso, se agrega suite en iteración siguiente.

---

## 6. Documentación

Se actualiza `.claude/CLAUDE.md` con:

- Nueva sección **"Costos de producción (capa B)"** con diagrama del flujo `actualizar_insumo` → `cargar_receta` → `cargar_sku` → `costo_sku`.
- Lista de tablas nuevas y vista nueva (`formatos`, `sku`, `sku_envasado`, `vista_costo_sku`).
- Regla nueva en sección de SQL: **"Para costos de SKU, consultar siempre `vista_costo_sku`. Nunca calcular costo a mano."**
- Mención de las 3 skills nuevas en la sección "Comandos frecuentes".

---

## 7. Capas posteriores (referencia, no en alcance)

- **Capa C — overhead prorrateado**: arriendo, internet, contador, depreciación de equipos. Suma una columna `costo_overhead_unitario` a `vista_costo_sku`. Requiere antes la capa de "gastos del mes a mes" (control de OPEX histórico).
- **Capa de DTE recibidos**: parser de facturas de compra del SII para auto-actualizar `maestro_insumos` y registrar histórico de precios.
- **Skill `/margen-cliente`**: cruza `vista_costo_sku` con `ventas` para entregar margen real por cliente y por SKU.
