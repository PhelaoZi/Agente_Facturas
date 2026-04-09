---
name: sync-facturas
description: Sincroniza facturas DTE chilenas desde un archivo XML del SII a PostgreSQL (Zigurat ERP). Ejecuta parse, validación e inserción en secuencia. Usar cuando se mencione sincronizar facturas, procesar XML del SII, cargar DTE o importar facturas a la base de datos.
argument-hint: "[DTE_DDMMYYYY]"
context: fork
disable-model-invocation: true
allowed-tools: Bash(python *)
---

# Sync Facturas — Zigurat ERP

> SKILL DE PROYECTO: Esta skill es específica del proyecto Zigurat ERP.
> Los scripts en `scripts/` son el núcleo del proyecto y residen en la
> raíz del repositorio por diseño. DEBES ejecutar esta skill desde el
> directorio raíz del proyecto (`Agente_Facturas\`).

## Reglas

- NUNCA pedir confirmación antes de ejecutar
- NUNCA saltar la validación
- NUNCA continuar si cualquier paso falla
- SIEMPRE verificar que $ARGUMENTS no esté vacío antes de ejecutar

## Paso 0 — Validar argumento

Si `$ARGUMENTS` está vacío o no fue proporcionado:
Reportar: "ERROR: Debes indicar el nombre del archivo. Uso correcto: /sync-facturas DTE_DDMMYYYY"
Detener el proceso aquí. NO continuar.

Si `$ARGUMENTS` tiene valor: continuar al Paso 1 de inmediato.

## Paso 1 — Ejecutar inmediatamente

```bash
python scripts/parse_dte.py facturas/$ARGUMENTS
```

Si falla: reportar error y detener todo.
Si exitoso: ejecutar Paso 2 de inmediato.

## Paso 2 — Ejecutar inmediatamente

```bash
python scripts/validate_changes.py changes.json
```

Si retorna exit code 1: mostrar errores y detener todo. NUNCA continuar si hay errores.
Si exitoso: ejecutar Paso 3 de inmediato.

## Paso 3 — Ejecutar inmediatamente

```bash
python scripts/sync_db.py changes.json
```

Si falla: reportar error.
Si exitoso: mostrar resumen del Paso 4.

## Paso 4 — Mostrar resumen final

Reportar al usuario:
- Archivo procesado: `facturas/$ARGUMENTS`
- Facturas insertadas
- Productos insertados
- Folios duplicados omitidos (si hubo)
- Tiempo total del proceso

## Paso 5 — Actualizar wiki (no-bloqueante)

Parsear los RUTs de clientes del output del Paso 3. Las líneas con formato
`✓ Folio XXXX | NOMBRE | $MONTO | N producto(s)` indican clientes procesados.
Extraer los RUTs únicos consultando el output. Si no se pueden parsear,
usar `--todos` como fallback.

```bash
python scripts/wiki_update.py --ruts RUT1,RUT2,RUT3 --origen "sync-facturas"
```

Si falla: mostrar warning "⚠️ No se pudo actualizar la wiki" pero NO fallar el proceso.
La sincronización de facturas ya se completó exitosamente.
