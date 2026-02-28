---
name: reporte-semanal
description: >
  Genera un reporte semanal de ventas de Zigurat Brewery: total vendido, top clientes,
  top productos, y comparativo con la semana anterior. Usar cuando el usuario pida
  el reporte de la semana, resumen semanal, cómo le fue esta semana, o quiera ver
  el comparativo de ventas. Ejemplos: "dame el reporte semanal", "cómo estuvo la semana",
  "resumen de esta semana", "reporte de ventas".
disable-model-invocation: true
allowed-tools: Bash(python *)
context: fork
---

# Reporte Semanal — Zigurat ERP

Genera el reporte consolidado de ventas de la semana actual con comparativo.

## Reglas

- NUNCA pedir confirmación antes de ejecutar
- SIEMPRE ejecutar el script y mostrar el output completo

## Paso 1 — Generar reporte

```bash
python .claude/skills/reporte-semanal/scripts/reporte.py
```

Si falla la conexión: reportar el error al usuario.

## Paso 2 — Mostrar resultados

Presentar el output del script tal cual al usuario.
