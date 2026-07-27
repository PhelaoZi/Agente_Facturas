---
paths:
  - "scripts/costo_sku.py"
  - "scripts/cargar_receta*.py"
  - "scripts/cargar_sku.py"
  - "scripts/actualizar_insumo.py"
  - "scripts/migrate_costos_*.py"
---

# Costos de producción (capa B)

Calcula el costo unitario real de cada SKU vendible (cerveza × formato)
combinando insumos de líquido + envasado + mano de obra + servicios
variables del lote.

## Tablas

| Tabla | Propósito |
|-------|-----------|
| `maestro_insumos` | Catálogo de insumos con `categoria` (malta, lupulo, levadura, adjunto, clarificante, envase, tapa, etiqueta, caja) y `precio_neto_unitario`. `precio_fecha_dte` = fecha de la factura que fijó ese precio: `sync_compras` solo lo pisa con una factura igual o más nueva, así que reimportar una compra vieja no lo retrocede. |
| `recetas` | Una fila por cerveza, con `costo_mano_obra_lote`, `costo_servicios_lote` y `merma_porcentaje`. |
| `receta_detalle` | BOM de líquido por receta. |
| `formatos` | Catálogo plano: Botella 330ml / Barril 30L acero / Barril 30L PET. |
| `sku` | Una fila por (receta, formato, unidades_caja). Caja 12 y caja 24 son SKUs distintos. |
| `sku_envasado` | BOM de envasado por SKU. Vacío para barriles retornables. |

## Vista

`vista_costo_sku` entrega `costo_liquido_unitario`, `costo_envasado_unitario`
y `costo_total_unitario` por cada SKU activo. **Nunca calcular costo a mano** — consultar siempre la vista.

## Flujo de uso

```
/actualizar-precio-insumo  → mantiene maestro_insumos
/cargar-receta             → mantiene recetas + receta_detalle
/cargar-sku (CLI)          → mantiene sku + sku_envasado
/costos-sku                → consulta vista_costo_sku
```

## Parámetros estándar (lote 540 L, 4 lotes/mes)

- Mano de obra: $300.000/lote (retiros tuyo + socio).
- Servicios variables (agua/luz/gas): $185.000/lote.
- Merma de envasado: 5%.

Editables por receta.

## Lo que NO hace esta capa

- No descuenta inventario al producir.
- No registra órdenes de producción.
- No prorratea costos fijos / overhead (capa C).
- No procesa DTEs recibidos (sub-proyecto aparte).
