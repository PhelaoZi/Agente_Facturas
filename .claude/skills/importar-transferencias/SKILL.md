---
name: importar-transferencias
description: >
  Importa el archivo Excel de transferencias recibidas del Itau Empresas a PostgreSQL.
  Usar cuando el usuario quiera cargar el Excel del banco, sincronizar transferencias,
  importar pagos recibidos, o actualizar los movimientos bancarios.
  Ejemplos: "importa el Excel del banco", "carga las transferencias", "hay pagos nuevos en el banco".
context: fork
disable-model-invocation: true
allowed-tools: Bash(python *)
---

# Importar Transferencias — Zigurat ERP

Importa el Excel de transferencias del Itau desde la carpeta `transferencias\` a PostgreSQL.

## Reglas

- NUNCA pedir confirmacion antes de ejecutar
- NUNCA continuar si el script falla
- El Excel debe estar en la carpeta `transferencias\` antes de ejecutar

## Paso 1 — Verificar que existe el Excel

```bash
python -c "
import glob, sys
archivos = glob.glob('transferencias/*.xlsx')
if not archivos:
    print('ERROR: No hay archivos .xlsx en transferencias/')
    print('Descarga el Excel del Itau y dejalo en la carpeta transferencias/')
    sys.exit(1)
else:
    print('Archivo encontrado: ' + archivos[0])
"
```

Si falla: reportar error y detener.

## Paso 2 — Importar

```bash
python scripts/import_transferencias.py
```

Si falla: reportar error y detener.

## Paso 3 — Resumen

Mostrar al usuario el resultado del paso 2:
- Transferencias importadas
- Transferencias ya existian (omitidas)
- Sugerir ejecutar `/conciliar-banco` como siguiente paso
