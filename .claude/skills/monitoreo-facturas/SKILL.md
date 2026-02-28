---
name: monitoreo-facturas
description: >
  Detecta XMLs en facturas\ que aún no han sido sincronizados con la base de datos
  y los procesa automáticamente. Usar cuando el usuario quiera saber si hay facturas
  pendientes, quiera procesar todas las nuevas de una vez, o pregunte "¿qué facturas
  faltan sincronizar?". Ejemplos: "hay facturas nuevas?", "sincroniza todo lo pendiente",
  "qué xmls faltan?", "monitorea las facturas".
context: fork
allowed-tools: Bash(python *)
---

# Monitoreo de Facturas — Zigurat ERP

Detecta y sincroniza automáticamente los XMLs en `facturas\` que no están en PostgreSQL.

## Reglas

- NUNCA pedir confirmación antes de ejecutar
- NUNCA saltar la validación al sincronizar cada archivo
- SIEMPRE procesar todos los pendientes detectados

## Paso 1 — Detectar XMLs pendientes

```bash
python .claude/skills/monitoreo-facturas/scripts/detectar_pendientes.py
```

Analiza el output:
- Si dice "✅ Todo sincronizado" → reportar al usuario y detener.
- Si lista archivos pendientes (línea `__PENDIENTES__:archivo1,archivo2`) → continuar.

## Paso 2 — Sincronizar cada pendiente

Para cada archivo en la lista `__PENDIENTES__`, ejecutar los 3 pasos en orden:

```bash
python scripts/parse_dte.py facturas/<ARCHIVO>
```
Si falla → reportar error y pasar al siguiente archivo.

```bash
python scripts/validate_changes.py changes.json
```
Si falla (exit code 1) → reportar errores y pasar al siguiente archivo.

```bash
python scripts/sync_db.py changes.json
```
Si falla → reportar error.

## Paso 3 — Resumen final

Mostrar al usuario:
- Archivos procesados exitosamente
- Archivos con errores (si hubo)
- Total de facturas insertadas
