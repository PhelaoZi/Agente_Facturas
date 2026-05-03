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
