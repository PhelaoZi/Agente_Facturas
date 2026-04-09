---
name: perfil-cliente
description: >
  Muestra el perfil completo de un cliente consultando la wiki y la base de datos.
  Usar cuando el usuario quiera saber sobre un cliente específico, su estado de cuenta,
  patrón de pago, o historial. Ejemplos: "cómo va Marina?", "perfil de Distribuidora XYZ",
  "qué onda con el cliente tal?", "muéstrame la ficha de...".
argument-hint: "[nombre del cliente]"
context: conversation
allowed-tools: Read, Glob, Bash(python *)
---

# Perfil Cliente — Zigurat ERP

Consulta la wiki de clientes y complementa con datos en tiempo real de la BD.

## Reglas

- SIEMPRE buscar primero en la wiki (`wiki/clientes/`)
- Si la ficha no existe, sugerir ejecutar `/wiki-init`
- Presentar la información de forma narrativa, no solo copiar el Markdown
- Destacar alertas: facturas vencidas, clientes inactivos, cambios de comportamiento

## Paso 1 — Buscar ficha del cliente

Buscar en `wiki/clientes/` el archivo que coincida con el nombre proporcionado en `$ARGUMENTS`:
1. Hacer glob en `wiki/clientes/*.md`
2. Buscar coincidencia parcial en el nombre del archivo (case-insensitive)
3. Si hay múltiples matches, mostrarlos y pedir que el usuario elija
4. Si no hay match, buscar dentro de los archivos por `razon_social` en el frontmatter

## Paso 2 — Leer la ficha

Leer el archivo `.md` completo del cliente encontrado.

## Paso 3 — Presentar al usuario

Presentar la información de forma narrativa y clara:
- Nombre y estado del cliente
- Métricas clave con contexto (ej: "paga rápido", "tiene deuda pendiente")
- Patrón de comportamiento
- Notas del agente relevantes
- Si hay alertas (facturas vencidas, inactividad), destacarlas al inicio

No copiar el Markdown tal cual — interpretar y presentar con insights.
