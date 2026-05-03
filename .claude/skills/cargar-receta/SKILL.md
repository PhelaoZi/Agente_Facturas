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
