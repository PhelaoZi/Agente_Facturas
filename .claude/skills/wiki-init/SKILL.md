---
name: wiki-init
description: >
  Inicializa la wiki de Zigurat generando fichas de todos los clientes desde PostgreSQL.
  Usar la primera vez para crear la wiki completa, o para regenerar todo desde cero.
  Ejemplos: "inicializa la wiki", "crea la wiki de clientes", "regenera las fichas".
context: fork
disable-model-invocation: true
allowed-tools: Bash(python *), Bash(mkdir *)
---

# Wiki Init — Zigurat ERP

Inicializa la wiki completa generando una ficha por cada cliente en la BD.

## Reglas

- NUNCA pedir confirmación antes de ejecutar
- Si la wiki ya existe, se regenera (las notas del agente existentes se preservan)

## Paso 1 — Crear estructura

```bash
mkdir -p wiki/clientes
```

## Paso 2 — Generar todas las fichas

```bash
python scripts/wiki_update.py --todos --origen "wiki-init"
```

Si falla: reportar error y detener.

## Paso 3 — Resumen final

Reportar al usuario:
- Total de fichas generadas
- Clientes activos vs incobrables
- Ruta de la wiki: `wiki/`
- Sugerir abrir `wiki/` como vault en Obsidian para ver graph view
