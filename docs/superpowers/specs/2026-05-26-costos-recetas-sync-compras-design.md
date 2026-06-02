# Diseño: Costos de Producción + Recetas + Sync Compras

**Fecha:** 2026-05-26  
**Estado:** Aprobado

---

## Contexto

Zigurat Brewery necesita que los costos de producción (vista_costo_sku) reflejen valores reales. Se detectaron dos problemas bloqueantes en la BD actual y se agregan dos recetas nuevas desde el Excel maestro.

### Problemas encontrados

1. **Precios incorrectos en `maestro_insumos`**: 12 insumos tienen guardado el precio total del paquete como si fuera el precio por unidad ($/gr o $/kg). Esto infla los costos calculados entre 100x y 500x (ej: levadura aparece costando $21M por lote en vez de ~$42K).

2. **Recetas en BD no coinciden con el Excel**: Las recetas Cream Ale y Scotch Ale tienen ingredientes y cantidades distintos a los del archivo `Recetas.xlsx`, que es la fuente de verdad actual.

3. **Recetas faltantes**: Paint it Black y Stout Café/Cacao no están en la BD.

4. **Sin registro de gastos operativos**: Facturas de compra como peajes no tienen tabla destino.

---

## Alcance

### Fuera de alcance
- Inventario / descuento de stock al producir
- Órdenes de producción
- Overhead / costos fijos (capa C)
- Tracking de precio histórico en USD

---

## Convención de precios (regla fija)

`precio_neto_unitario` en `maestro_insumos` es siempre **precio por unidad de medida**:

| Unidad | Significado |
|--------|-------------|
| `kg` | precio por 1 kilogramo |
| `gr` | precio por 1 gramo |
| `ml` | precio por 1 mililitro |
| `litro` | precio por 1 litro |
| `unidad` | precio por pieza |

`receta_detalle.cantidad_requerida` = cantidad en esa unidad usada por lote.  
`costo_linea = precio_neto_unitario × cantidad_requerida`

---

## Cambios en `maestro_insumos`

### 1. Corrección de precios existentes

| Insumo | Precio actual (incorrecto) | Precio correcto | Fuente |
|--------|---------------------------|-----------------|--------|
| Malta Cara Pils | $18,487.39/kg | $2,310.92/kg | Costos_Zigurat.xlsx (18487/8) |
| Malta Cara Ruby | $19,327.73/kg | $2,415.97/kg | Costos_Zigurat.xlsx (19328/8) |
| Malta Arome | $17,411.76/kg | $2,176.47/kg | Costos_Zigurat.xlsx (17412/8) |
| Malta Biscuit | $19,327.73/kg | $2,415.97/kg | Costos_Zigurat.xlsx (19328/8) |
| Malta Chocolate | $7,764.71/kg | $2,512.61/kg | XML Mundo Cervecero 2026-05-20 |
| Malta Cara Aroma | $2,840.34/kg | $2,932.77/kg | XML Mundo Cervecero 2026-05-20 |
| Trigo Malteado claro | $11,764.71/kg | $1,470.59/kg | Costos_Zigurat.xlsx (11765/8) |
| Lupulo Magnum | $8,508.40/gr | $36.37/gr | XML Almacén Cervecero (3636.60/100) |
| Levadura AY4 | $42,016.81/gr | $89.11/gr | XML Almacén Cervecero (44555/500) |
| Clarificante Polyclar coccion | $4,201.68/gr | $49.40/gr | XML Almacén Cervecero (4940/100) |
| Clarificante Polyclar 10 maduracion | $3,781.51/gr | $32.40/gr | XML Almacén Cervecero (3239.50/100) |
| Clarificante SB3 maduracion | $6,806.72/ml | $13.61/ml | Costos_Zigurat.xlsx (6807/500) |

### 2. Nuevos insumos (precio $0 provisional)

| Nombre | Unidad | Categoría |
|--------|--------|-----------|
| Fosfórico | ml | adjunto |
| Malta Cara 50 | kg | malta |
| Malta Carafa 2 | kg | malta |
| Cebada Tostada | kg | malta |
| Avena | kg | adjunto |
| Hojuela de Cebada | kg | adjunto |
| Frambuesa | kg | adjunto |
| Vainilla | litro | adjunto |
| Café | kg | adjunto |
| Cacao | kg | adjunto |

---

## Recetas — fuente de verdad: `Recetas.xlsx`

Lote estándar: **540 litros**. Mano de obra: $300,000. Servicios: $185,000. Merma: 5%.

### Cream Ale

| Insumo | Cantidad | Unidad |
|--------|----------|--------|
| Malta Pilsen | 100 | kg |
| Malta Caradex | 8 | kg |
| Trigo Malteado claro | 8 | kg |
| Levadura AY4 | 500 | gr |
| Lupulo Magnum | 200 | gr |
| Fosfórico | 170 | ml |
| Clarificante Polyclar coccion | 100 | gr |
| Clarificante Polyclar 10 maduracion | 100 | gr |
| Clarificante SB3 maduracion | 800 | ml |

### Scotch Ale

| Insumo | Cantidad | Unidad |
|--------|----------|--------|
| Malta Pale Ale | 100 | kg |
| Malta Munich | 12 | kg |
| Malta Cara 50 | 8 | kg |
| Malta Biscuit | 8 | kg |
| Malta Arome | 8 | kg |
| Malta Chocolate | 3 | kg |
| Malta Cara Aroma | 1 | kg |
| Levadura AY4 | 500 | gr |
| Lupulo Magnum | 200 | gr |
| Fosfórico | 170 | ml |
| Clarificante Polyclar coccion | 100 | gr |
| Clarificante Polyclar 10 maduracion | 100 | gr |
| Clarificante SB3 maduracion | 800 | ml |

### Paint it Black

| Insumo | Cantidad | Unidad |
|--------|----------|--------|
| Malta Pale Ale | 100 | kg |
| Malta Munich | 25 | kg |
| Malta Biscuit | 25 | kg |
| Malta Carafa 2 | 10 | kg |
| Cebada Tostada | 5 | kg |
| Malta Chocolate | 5 | kg |
| Avena | 15 | kg |
| Hojuela de Cebada | 10 | kg |
| Levadura AY4 | 500 | gr |
| Lupulo Magnum | 500 | gr |
| Fosfórico | 170 | ml |
| Clarificante Polyclar coccion | 100 | gr |
| Clarificante Polyclar 10 maduracion | 100 | gr |
| Clarificante SB3 maduracion | 800 | ml |
| Frambuesa | 20 | kg |
| Vainilla | 2 | litro |

### Stout Café/Cacao

| Insumo | Cantidad | Unidad |
|--------|----------|--------|
| Malta Pale Ale | 100 | kg |
| Malta Munich | 25 | kg |
| Malta Biscuit | 10 | kg |
| Malta Cara 50 | 10 | kg |
| Malta Carafa 2 | 5 | kg |
| Cebada Tostada | 5 | kg |
| Malta Chocolate | 5 | kg |
| Avena | 10 | kg |
| Hojuela de Cebada | 5 | kg |
| Levadura AY4 | 500 | gr |
| Lupulo Magnum | 300 | gr |
| Fosfórico | 170 | ml |
| Clarificante Polyclar coccion | 100 | gr |
| Clarificante Polyclar 10 maduracion | 100 | gr |
| Clarificante SB3 maduracion | 800 | ml |
| Café | 1 | kg |
| Cacao | 1 | kg |

---

## Nueva tabla `gastos_operativos`

```sql
CREATE TABLE IF NOT EXISTS gastos_operativos (
    id               SERIAL PRIMARY KEY,
    folio            TEXT,
    tipo_documento   TEXT,
    fecha_emision    DATE,
    rut_emisor       TEXT,
    razon_social_emisor TEXT,
    descripcion      TEXT,
    monto_neto       INTEGER,
    monto_total      INTEGER,
    categoria        TEXT   -- 'transporte', 'servicios', 'otros'
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gastos_operativos_folio_rut
    ON gastos_operativos (folio, rut_emisor);
```

El peaje Autopista Vespucio (folio 14874658, $71,378) se inserta en esta tabla con `categoria = 'transporte'`.

---

## Script `scripts/sync_compras.py`

Lee todos los XMLs en `facturas-compras/` y clasifica cada ítem:

- **Insumo de producción** → `UPDATE maestro_insumos SET precio_neto_unitario = ...`
- **Gasto operativo** → `INSERT INTO gastos_operativos ...`

### Lógica de clasificación

1. Si el emisor está en `PROVEEDORES_INSUMOS` (dict configurable en el script) → sus ítems se mapean a `maestro_insumos`.
2. Si el emisor está en `PROVEEDORES_GASTOS` → la factura completa va a `gastos_operativos`.
3. Si el emisor no está en ninguno → warning, se omite.

### Mapping insumos (dict en el script)

```python
ITEM_MAP = {
    # nombre_en_xml (lowercase, partial match) → (nombre_en_maestro_insumos, tamaño_paquete, unidad)
    # tamaño_paquete: cuántas unidades trae el paquete que vende el proveedor
    "malta chocolate":       ("Malta Chocolate",                    1,   "kg"),
    "malta caraaroma":       ("Malta Cara Aroma",                   1,   "kg"),
    "fermoale ay4":          ("Levadura AY4",                     500,   "gr"),
    "lupulo100gr magnum":    ("Lupulo Magnum",                    100,   "gr"),
    "polyclar brewbrite":    ("Clarificante Polyclar coccion",    100,   "gr"),
    "polyclar10":            ("Clarificante Polyclar 10 maduracion", 100, "gr"),
}
```

`precio_neto_unitario = PrcItem / tamaño_paquete`

Ejemplo: FermoAle AY4, PrcItem=$44,555, tamaño=500 → precio/gr = $89.11

### Idempotencia

El script registra los archivos procesados en `facturas-compras/.procesados.json`. Si un XML ya fue procesado, lo omite.

---

## Skill `/sync-compras`

Archivo: `.claude/skills/sync-compras/skill.md`

Detecta XMLs nuevos en `facturas-compras/`, ejecuta `sync_compras.py`, y reporta:
- Insumos actualizados con nuevos precios
- Gastos operativos registrados
- XMLs omitidos (sin mapeo configurado)

---

## Flujo precio variable (Bucarest / dólar)

```
Llega factura Bucarest → facturas-compras/DTE_*.xml
    → /sync-compras
    → precio/kg de maltas base se actualiza en maestro_insumos
    → vista_costo_sku recalcula automáticamente
    → /costos-sku muestra costos actualizados
```

No se almacena precio en USD. El precio en CLP de la factura es la fuente de verdad.

---

## Scripts necesarios

| Script | Propósito |
|--------|-----------|
| `scripts/migrate_costos_v3.py` | Corrige precios en maestro_insumos + agrega nuevos insumos |
| `scripts/cargar_recetas_v2.py` | **Borra y recarga** receta_detalle de las 4 recetas desde Recetas.xlsx (DELETE + INSERT) |
| `scripts/migrate_gastos_operativos.py` | Crea tabla gastos_operativos |
| `scripts/sync_compras.py` | Procesa XMLs de facturas-compras/ |

---

## Orden de ejecución

1. `migrate_gastos_operativos.py` — crea tabla nueva (idempotente)
2. `migrate_costos_v3.py` — corrige precios, agrega insumos faltantes
3. `cargar_recetas_v2.py` — recarga las 4 recetas
4. `sync_compras.py` — procesa los 4 XMLs actuales
5. Verificar con `/costos-sku`
