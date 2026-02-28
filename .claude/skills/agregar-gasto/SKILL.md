---
name: agregar-gasto
description: >
  Registra una cuenta por pagar (gasto programado) en la base de datos.
  Usar cuando el usuario quiera agregar un gasto, registrar una cuenta por pagar,
  ingresar un pago futuro o una obligacion de pago.
  Ejemplos: "agrega el arriendo de marzo", "registra el pago al proveedor",
  "anota el gasto de insumos", "tengo que pagar X el DD/MM".
argument-hint: '"descripcion" monto YYYY-MM-DD [proveedor] [categoria]'
disable-model-invocation: false
allowed-tools: Bash(python *)
---

# Agregar Gasto — Zigurat ERP

Registra una nueva cuenta por pagar en la tabla `cuentas_por_pagar`.

## Reglas

- NUNCA pedir confirmacion antes de ejecutar
- Inferir los parametros del mensaje del usuario
- Si faltan datos criticos (descripcion, monto, fecha), preguntar

## Paso 1 — Interpretar y extraer parametros

Del mensaje del usuario extraer:
- `descripcion`: que es el gasto (requerido)
- `monto`: cuanto (requerido, en pesos chilenos, sin puntos)
- `fecha_vencimiento`: cuando vence en formato YYYY-MM-DD (requerido)
- `proveedor`: a quien se paga (opcional)
- `categoria`: tipo — 'insumos', 'arriendo', 'servicios', 'remuneraciones', 'impuestos', 'otros' (opcional)

## Paso 2 — Ejecutar

```bash
python .claude/skills/agregar-gasto/scripts/agregar_gasto.py "DESCRIPCION" MONTO YYYY-MM-DD "PROVEEDOR" CATEGORIA
```

Omitir proveedor y/o categoria si no fueron dados.

## Paso 3 — Confirmar al usuario

Mostrar el resultado del script confirmando que el gasto fue registrado.
Sugerir `/flujo-caja` para ver el impacto en la proyeccion.
