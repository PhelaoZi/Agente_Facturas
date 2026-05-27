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
   - Proveedor de insumos -> `PROVEEDORES_INSUMOS[rut] = "Nombre"`
   - Proveedor de gastos -> `PROVEEDORES_GASTOS[rut] = ("Nombre", "categoria")`

4. Verificar costos actualizados:
   ```
   python scripts/costo_sku.py
   ```

## Notas

- Idempotente: XMLs ya procesados se saltan automáticamente (`.procesados.json`)
- Los XMLs van en `facturas-compras/` (formato DTE del SII, ISO-8859-1)
- Para agregar nuevos ítems al mapeo: editar `ITEM_MAP` en `sync_compras.py`
