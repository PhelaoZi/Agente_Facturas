---
name: sync-nc
description: Sincroniza Notas de Crédito DTE (tipo 61) desde XMLs del SII a PostgreSQL (Zigurat ERP). Sin argumento detecta y procesa automáticamente todos los XMLs pendientes en "Notas de Credito/". Con argumento procesa ese archivo específico. Usar cuando se mencione sincronizar notas de crédito, procesar NC, cargar DTE tipo 61, o cuando se quiera saber si hay NCs pendientes.
argument-hint: "[NOMBRE_ARCHIVO.xml] (opcional — sin argumento procesa todos los pendientes)"
context: fork
disable-model-invocation: false
allowed-tools: Bash(python *)
---

# Sync Notas de Crédito — Zigurat ERP

> SKILL DE PROYECTO: Ejecutar siempre desde el directorio raíz `Agente_Facturas\`.

## Reglas

- NUNCA pedir confirmación antes de ejecutar
- NUNCA saltar la validación
- NUNCA continuar si cualquier paso falla
- Si se pasa argumento → modo específico. Si no → modo automático.

---

## Modo automático (sin argumento)

### Paso A1 — Detectar XMLs pendientes

```bash
python -X utf8 .claude/skills/sync-nc/scripts/detectar_pendientes_nc.py
```

- Si la línea `__PENDIENTES__:` viene vacía → reportar "✅ Todo sincronizado. No hay XMLs pendientes en 'Notas de Credito/'." y detener.
- Si viene con archivos → continuar con cada uno en orden.

### Paso A2 — Procesar cada pendiente

Para cada archivo en `__PENDIENTES__`, ejecutar los 3 pasos en secuencia:

```bash
python -X utf8 scripts/parse_dte.py "Notas de Credito/ARCHIVO.xml"
```
Si falla → reportar error y pasar al siguiente archivo.

```bash
python -X utf8 scripts/validate_changes.py changes.json
```
Si falla → reportar errores y pasar al siguiente archivo.

```bash
python -X utf8 scripts/sync_db.py changes.json
```
Si falla → reportar error.

### Paso A3 — Resumen final

Mostrar:
- Archivos procesados exitosamente
- Archivos con errores (si hubo)
- Total de NCs insertadas y facturas ajustadas

---

## Modo específico (con argumento)

### Paso E1 — Validar argumento

Si `$ARGUMENTS` está vacío → modo automático (ir al Paso A1).
Si tiene valor → continuar.

### Paso E2 — Pipeline sobre el archivo indicado

```bash
python -X utf8 scripts/parse_dte.py "Notas de Credito/$ARGUMENTS"
```
Si falla → reportar error y detener.

```bash
python -X utf8 scripts/validate_changes.py changes.json
```
Si falla → mostrar errores y detener.

```bash
python -X utf8 scripts/sync_db.py changes.json
```
Si falla → reportar error.

### Paso E3 — Resumen final

Reportar:
- Archivo procesado
- NCs insertadas / duplicados omitidos
- Facturas referenciadas ajustadas
- Tiempo total
