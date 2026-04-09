---
name: wiki-lint
description: >
  Audita la consistencia entre la wiki de clientes y la base de datos.
  Detecta fichas faltantes, huérfanas o desactualizadas.
  Usar cuando el usuario quiera verificar la salud de la wiki.
  Ejemplos: "revisa la wiki", "hay fichas desactualizadas?", "lint wiki",
  "la wiki está bien?".
context: fork
disable-model-invocation: true
allowed-tools: Bash(python *)
---

# Wiki Lint — Zigurat ERP

Audita la wiki de clientes para encontrar inconsistencias con la BD.

## Reglas

- NUNCA modificar archivos, solo reportar
- Si hay problemas, sugerir `/wiki-init` para regenerar todo o `wiki_update.py --ruts` para corregir selectivamente

## Paso 1 — Ejecutar auditoría

```bash
python scripts/wiki_lint.py
```

## Paso 2 — Interpretar resultados

Mostrar al usuario los problemas encontrados con contexto:
- **SIN FICHA**: cliente en BD que no tiene wiki → sugerir wiki_update
- **HUÉRFANA**: ficha sin cliente en BD → puede ser un cliente eliminado
- **DESACTUALIZADA**: ficha vieja con movimientos nuevos → sugerir wiki_update
