# Spec: Sistema de Registro de Facturas de Compra

**Fecha:** 2026-06-02  
**Proyecto:** Zigurat ERP — Agente Facturas  
**Estado:** Aprobado

---

## Problema

El script `sync_compras.py` actual no registra los documentos de compra en la base de datos. Solo hace dos cosas con cada DTE recibido:
- Actualizar `precio_neto_unitario` en `maestro_insumos`
- Insertar una fila en `gastos_operativos`

Esto significa que no existe historial de compras, no se puede saber qué facturas están pagadas o pendientes, no hay registro por proveedor, y los documentos XML son la única fuente de verdad.

---

## Objetivo

Registrar todos los DTEs de compra recibidos (tipo 46 y 61) en PostgreSQL con su cabecera y líneas de detalle, habilitando:

1. **Histórico de precios** por insumo a lo largo del tiempo
2. **Costos por período** agrupados por categoría de proveedor
3. **Cuentas por pagar** — qué facturas están pendientes y con quién
4. **Trazabilidad completa** del documento fuente

---

## Diseño

### Tablas nuevas

#### `proveedores`

Un registro por RUT emisor. Reemplaza los diccionarios hardcodeados en `sync_compras.py`.

```sql
CREATE TABLE proveedores (
    rut              TEXT PRIMARY KEY,         -- formato '76045387-0'
    razon_social     TEXT NOT NULL,
    categoria        TEXT,                     -- 'insumos' | 'envases' | 'transporte' | 'servicios' | NULL
    dias_credito     INT  DEFAULT 0,           -- 0 = pago inmediato, 30 = Bucarest
    activo           BOOL DEFAULT TRUE,
    created_at       TIMESTAMP DEFAULT NOW()
);
```

**Proveedores conocidos a cargar en migración:**

| RUT | Nombre | Categoría | Días crédito |
|-----|--------|-----------|-------------|
| `76045387-0` | Mundo Cervecero | insumos | 0 |
| `76448126-7` | Almacén Cervecero | insumos | 0 |
| `77103092-0` | Petainer Chile | envases | 0 |
| `76052927-3` | Autopista Nueva Vespucio Sur | transporte | 0 |
| RUT Bucarest | Bucarest (a confirmar en XML) | insumos | 30 |

#### `compras`

Una fila por DTE recibido.

```sql
CREATE TABLE compras (
    id                SERIAL PRIMARY KEY,
    folio             TEXT    NOT NULL,
    tipo_documento    TEXT    NOT NULL,         -- '46' factura, '61' NC recibida
    fecha_emision     DATE    NOT NULL,
    rut_proveedor     TEXT    REFERENCES proveedores(rut),
    monto_neto        INT     DEFAULT 0,
    iva               INT     DEFAULT 0,
    monto_total       INT     NOT NULL,
    estado_pago       TEXT    DEFAULT 'pendiente', -- 'pendiente' | 'pagado'
    fecha_vencimiento DATE,                     -- fecha_emision + dias_credito del proveedor
    fecha_pago        DATE,
    archivo_xml       TEXT,                     -- nombre del archivo fuente
    created_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE (folio, rut_proveedor)
);
```

#### `compras_detalle`

Una fila por línea de ítem dentro del DTE.

```sql
CREATE TABLE compras_detalle (
    id               SERIAL PRIMARY KEY,
    compra_id        INT     NOT NULL REFERENCES compras(id) ON DELETE CASCADE,
    numero_linea     INT     NOT NULL,
    descripcion      TEXT    NOT NULL,          -- NmbItem del XML
    cantidad         NUMERIC DEFAULT 1,
    precio_unitario  NUMERIC DEFAULT 0,
    monto_linea      INT     DEFAULT 0,
    insumo_id        INT     REFERENCES maestro_insumos(id), -- NULL si no mapea
    created_at       TIMESTAMP DEFAULT NOW()
);
```

### Tabla deprecada

`gastos_operativos` se migra a `compras` y se elimina. Tiene 1 solo registro al momento de esta spec.

---

## Lógica de procesamiento

### Clasificación por proveedor

```
proveedor.categoria = 'insumos'  → registra compras + compras_detalle + actualiza maestro_insumos
proveedor.categoria = otro       → registra compras + compras_detalle
proveedor.categoria = NULL       → registra compras + compras_detalle (pendiente clasificar)
```

La categoría `NULL` reemplaza el comportamiento actual de "omitir con warning". Ahora **ningún DTE se pierde**.

### Mapeo ítem → insumo

El `ITEM_MAP` actual (substring NmbItem → nombre en maestro_insumos) se conserva. Al hacer match:
1. Se actualiza `precio_neto_unitario` en `maestro_insumos`
2. Se guarda `insumo_id` en `compras_detalle` para trazabilidad histórica

### Idempotencia

Se conserva el mecanismo `.procesados.json`. Además, `compras` tiene `UNIQUE(folio, rut_proveedor)` con `ON CONFLICT DO NOTHING` como segunda línea de defensa.

### Fecha de vencimiento

Al insertar en `compras`:
```
fecha_vencimiento = fecha_emision + INTERVAL '{dias_credito} days'
```
Si `dias_credito = 0`, `fecha_vencimiento = fecha_emision` (pago al día).

---

## Scripts a crear / modificar

| Script | Acción |
|--------|--------|
| `scripts/migrate_compras_v1.py` | Crea las 3 tablas, inserta proveedores conocidos, migra `gastos_operativos` |
| `scripts/sync_compras.py` | Reescritura completa manteniendo interfaz externa igual |
| `.claude/skills/sync-compras/` | Actualizar skill para reflejar nuevo comportamiento |

---

## Consultas habilitadas

```sql
-- Deuda pendiente por proveedor
SELECT p.razon_social, SUM(c.monto_total) AS deuda
FROM compras c JOIN proveedores p ON p.rut = c.rut_proveedor
WHERE c.estado_pago = 'pendiente'
GROUP BY p.razon_social ORDER BY deuda DESC;

-- Histórico de precio de un insumo
SELECT c.fecha_emision, cd.precio_unitario, p.razon_social
FROM compras_detalle cd
JOIN compras c ON c.id = cd.compra_id
JOIN maestro_insumos m ON m.id = cd.insumo_id
JOIN proveedores p ON p.rut = c.rut_proveedor
WHERE m.nombre = 'Malta Pilsen'
ORDER BY c.fecha_emision;

-- Gasto total por categoría en un período
SELECT p.categoria, SUM(c.monto_total) AS total
FROM compras c JOIN proveedores p ON p.rut = c.rut_proveedor
WHERE c.fecha_emision BETWEEN '2026-01-01' AND '2026-05-31'
  AND c.tipo_documento = '46'
GROUP BY p.categoria ORDER BY total DESC;

-- Facturas próximas a vencer (próximos 7 días)
SELECT p.razon_social, c.folio, c.fecha_vencimiento, c.monto_total
FROM compras c JOIN proveedores p ON p.rut = c.rut_proveedor
WHERE c.estado_pago = 'pendiente'
  AND c.fecha_vencimiento <= CURRENT_DATE + INTERVAL '7 days'
ORDER BY c.fecha_vencimiento;
```

---

## Lo que NO hace este sistema

- No registra pagos parciales (una factura está pagada o pendiente, sin montos intermedios)
- No cruza automáticamente con `movimientos_banco` (puede hacerse en el futuro como extensión de `/conciliar-banco`)
- No procesa notas de débito (tipo 56)
- No valida montos contra el SII

---

## Migración de datos existentes

El único registro en `gastos_operativos` se migra así:

```
folio: 14874658
rut_proveedor: 76052927-3 (Autopista Nueva Vespucio Sur)
fecha_emision: 2026-05-13
monto_neto: 0
monto_total: 71378
estado_pago: 'pagado'    ← asumimos pagado (peaje automático)
```

Línea de detalle:
```
descripcion: 'Peajes periodo VS'
cantidad: 1
precio_unitario: 71378
monto_linea: 71378
insumo_id: NULL
```
