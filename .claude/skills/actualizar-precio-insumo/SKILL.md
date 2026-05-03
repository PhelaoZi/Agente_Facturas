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
