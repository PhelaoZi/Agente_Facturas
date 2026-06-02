# Wiki Zigurat — Spec de Diseño

**Fecha:** 2026-04-08
**Autor:** Christian de la Fuente + Claude
**Estado:** Aprobado
**Enfoque:** Wiki nativa integrada (sin frameworks externos)

---

## 1. Contexto y motivación

### El problema

Zigurat ERP (Agente Facturas) tiene ~3,300 líneas de Python, 10 skills de Claude Code, y 6 tablas PostgreSQL que gestionan facturación, conciliación bancaria y flujo de caja. Cada vez que se consulta información de un cliente, se hace una query SQL cruda que devuelve números sin contexto.

No existe un lugar donde se acumule conocimiento sobre el negocio: patrones de pago, cambios de comportamiento, observaciones históricas. Esa información vive en la cabeza de Christian y se pierde entre sesiones de Claude Code.

### La solución

Aplicar el patrón **LLM Wiki** (Andrej Karpathy, abril 2026) al ERP: una base de conocimiento persistente en archivos Markdown, mantenida automáticamente por los agentes, que compila y sintetiza datos de la BD en fichas legibles con contexto de negocio.

### Inspiración

- [LLM Wiki — Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- Patrón: "Obsidian es el IDE, el LLM es el programador, la wiki es el codebase"
- Diferencia clave vs RAG: el conocimiento se compila una vez y se mantiene, en vez de redescubrirse en cada consulta

### Alcance de Fase 1

**Implementar:** Wiki de perfiles de clientes (fichas ejecutivas ~30 líneas).
**No implementar (futuro):** Inteligencia financiera, operacional/producción, agentes programados.

---

## 2. Arquitectura

### 2.1 Estructura de carpetas

```
wiki/                         # Vault de Obsidian dentro del repo
├── index.md                  # Catálogo maestro: tabla con link + resumen por cliente
├── log.md                    # Registro cronológico de operaciones del wiki
└── clientes/                 # Una ficha .md por cliente
    ├── cerveceria-marina-spa.md
    ├── distribuidora-xyz-ltda.md
    └── ...
```

La carpeta `wiki/` vive dentro de `Agente_Facturas/` (mismo repo, mismo git). Se abre como vault en Obsidian para visualización con graph view y backlinks.

**La wiki va versionada en git** — es un artefacto valioso, no temporal.

### 2.2 Convención de nombres de archivo

- Derivado de `razon_social` en tabla `clientes`
- Formato: kebab-case, sin caracteres especiales ni tildes
- Ejemplo: "CERVECERÍA MARINA SPA" → `cerveceria-marina-spa.md`
- Función Python `slugify_razon_social(razon_social)` para generar el nombre

### 2.3 Flujo de datos

```
sync-facturas / sync-nc / conciliar-banco
        ↓ (paso nuevo al final, no-bloqueante)
wiki_update.py --ruts RUT1,RUT2
        ↓
Lee PostgreSQL (6 queries por cliente)
        ↓
Genera/actualiza ficha .md (preserva notas existentes)
        ↓
Actualiza index.md + log.md
        ↓
Listo para /perfil-cliente o Obsidian
```

---

## 3. Template de ficha de cliente

```markdown
---
rut: "76.xxx.xxx-x"
razon_social: "Cervecería Marina SPA"
estado: activo
ultima_actualizacion: 2026-04-08
---

# Cervecería Marina SPA

## Métricas clave
| Métrica | Valor |
|---------|-------|
| Total vendido | $12.450.000 |
| Facturas emitidas | 24 |
| Facturas pendientes | 3 ($2.100.000) |
| Promedio días de pago | 22 días |
| Último pago | 2026-03-15 |

## Estado de cuenta
- 3 facturas pendientes por $2.100.000
- Sin facturas vencidas (>30 días)

## Patrón de comportamiento
- Cliente regular desde 2025-06. Compra mensualmente.
- Paga consistentemente entre 15-25 días.
- Producto principal: Cerveza Lager 330cc.

## Notas del agente
- 2026-04-01: Pagó facturas #1201 y #1202 juntas vía transferencia.
- 2026-03-15: Primera vez que compra Cerveza IPA 500cc.
```

### Reglas del template

- **Frontmatter YAML:** rut, razon_social, estado, ultima_actualizacion. Obsidian lo usa para Dataview.
- **Métricas clave:** tabla con 5 indicadores principales. Se recalcula en cada actualización.
- **Estado de cuenta:** resumen textual de facturas pendientes y vencidas.
- **Patrón de comportamiento:** generado por el agente. Describe frecuencia de compra, velocidad de pago, productos principales.
- **Notas del agente:** sección append-only. El script agrega observaciones, nunca borra las existentes.

---

## 4. index.md

```markdown
# Wiki Zigurat — Índice de Clientes

> Última actualización: 2026-04-08 | Total clientes: 45 (36 activos, 9 incobrables)

## Clientes activos

| Cliente | RUT | Deuda pendiente | Prom. días pago | Última actualización |
|---------|-----|-----------------|-----------------|---------------------|
| [[Cervecería Marina SPA]] | 76.xxx.xxx-x | $2.100.000 | 22 | 2026-04-08 |
| [[Distribuidora XYZ]] | 77.xxx.xxx-x | $0 | 15 | 2026-04-05 |

## Clientes incobrables

| Cliente | RUT | Deuda histórica | Motivo | Última actualización |
|---------|-----|-----------------|--------|---------------------|
| [[Empresa Cerrada SA]] | 78.xxx.xxx-x | $1.500.000 | Empresa ya no existe | 2026-04-08 |
```

- Wikilinks `[[Nombre]]` para que Obsidian resuelva backlinks y graph view
- Separado en activos e incobrables
- Ordenado por deuda pendiente descendente

---

## 5. log.md

```markdown
# Wiki Zigurat — Log de Operaciones

## 2026-04-08
- **wiki-init**: Wiki inicializada con 45 clientes (36 activos, 9 incobrables) desde BD.

## 2026-04-08
- **sync-facturas DTE_07042026**: Actualizadas fichas de Marina, XYZ. 5 facturas nuevas.
- **conciliar-banco**: Marina pagó #1201 y #1202. Promedio días pago actualizado: 22→21.
```

- Entradas agrupadas por fecha
- Cada operación indica: qué skill la disparó, qué clientes se tocaron, resumen del cambio
- Git da historial detallado; log.md da contexto rápido para el agente

---

## 6. Script: wiki_update.py

### 6.1 Interfaz

```bash
python scripts/wiki_update.py --todos                    # Regenera todas las fichas
python scripts/wiki_update.py --ruts 76123456-7,77890123-4  # Actualiza clientes específicos
python scripts/wiki_update.py --cliente 76123456-7       # Actualiza un cliente
```

### 6.2 Queries SQL por cliente

Todas las queries respetan las reglas del proyecto:
- `COALESCE(monto_total_ajustado, monto_total)` para montos reales
- `tipo_documento != '61'` para excluir NCs de sumas
- Parámetros preparados (sin concatenación de strings)

| # | Query | Dato |
|---|-------|------|
| 1 | `SELECT razon_social, estado, direccion, comuna FROM clientes WHERE rut_cliente = %s` | Datos maestros |
| 2 | `SELECT COUNT(*), SUM(COALESCE(monto_total_ajustado, monto_total)) FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61'` | Total vendido, facturas emitidas |
| 3 | `SELECT COUNT(*), SUM(COALESCE(monto_total_ajustado, monto_total)) FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61' AND fecha_pago IS NULL` | Facturas pendientes, deuda |
| 4 | `SELECT AVG(dias_pago), MAX(fecha_pago) FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61' AND fecha_pago IS NOT NULL` | Promedio días pago, último pago |
| 5 | `SELECT nombre_producto, SUM(cantidad) FROM productos p JOIN ventas v ON ... WHERE v.rut_cliente = %s AND v.tipo_documento != '61' GROUP BY nombre_producto ORDER BY SUM(cantidad) DESC LIMIT 3` | Top 3 productos |
| 6 | `SELECT MIN(fecha) FROM ventas WHERE rut_cliente = %s AND tipo_documento != '61'` | Cliente desde cuándo |

### 6.3 Lógica de actualización

```
Para cada RUT:
  1. Ejecutar 6 queries
  2. Si ficha NO existe → crear desde template
  3. Si ficha YA existe:
     a. Leer archivo actual
     b. Actualizar frontmatter (ultima_actualizacion)
     c. Reescribir secciones: Métricas, Estado de cuenta, Patrón de comportamiento
     d. PRESERVAR sección "Notas del agente" (append-only)
     e. Detectar eventos notables → agregar a Notas del agente
  4. Actualizar línea del cliente en index.md
  5. Agregar entrada en log.md
```

### 6.4 Detección de eventos notables

El script detecta y registra automáticamente en "Notas del agente":

| Evento | Condición |
|--------|-----------|
| Producto nuevo | Producto en última factura que no aparece en historial previo |
| Pago múltiple | >1 factura pagada en la misma fecha |
| Cambio en velocidad de pago | Último pago difiere >30% del promedio histórico |
| Factura vencida | Factura con >30 días sin pago |
| Cliente inactivo | >60 días sin nueva factura |

### 6.5 Output

```
Wiki actualizada: 3 clientes
  ✓ Cervecería Marina SPA — 2 facturas nuevas, deuda $2.1M
  ✓ Distribuidora XYZ — pago recibido, deuda $0
  ✓ Bar El Trébol — nueva ficha creada
Index actualizado. Log actualizado.
```

### 6.6 Manejo de errores

- Si falla la conexión a BD → error, no toca archivos
- Si falla la escritura de un archivo → log del error, continúa con el siguiente cliente
- Si el script falla, NO afecta al sync que lo llamó (paso no-bloqueante)

---

## 7. Skills nuevos

### 7.1 /wiki-init

**Propósito:** Inicialización completa de la wiki (una sola vez).

**Pasos:**
1. Crear carpetas `wiki/` y `wiki/clientes/`
2. Ejecutar `python scripts/wiki_update.py --todos`
3. Mostrar resumen: total fichas, activos, incobrables

**Contexto:** fork (ejecuta script, no invoca modelo)

### 7.2 /perfil-cliente

**Propósito:** Consultar wiki + BD para dar una respuesta enriquecida sobre un cliente.

**Argumentos:** `[nombre o parte del nombre]`

**Pasos:**
1. Buscar en `wiki/clientes/` el archivo que matchee (búsqueda fuzzy en nombre de archivo y contenido del frontmatter)
2. Leer la ficha completa
3. Opcionalmente consultar BD para datos en tiempo real
4. Claude presenta la información con contexto e insights narrativos

**Contexto:** conversación (el modelo interpreta y enriquece)

### 7.3 /wiki-lint

**Propósito:** Auditoría de salud de la wiki.

**Pasos:**
1. Obtener lista de clientes en BD
2. Obtener lista de fichas en `wiki/clientes/`
3. Detectar:
   - Clientes en BD sin ficha
   - Fichas sin cliente en BD (huérfanas)
   - Fichas desactualizadas (ultima_actualizacion > 7 días con movimientos recientes)
   - Métricas inconsistentes (deuda en ficha ≠ deuda en BD)
4. Mostrar reporte con acciones sugeridas

**Contexto:** fork (ejecuta script de verificación)

---

## 8. Modificaciones a skills existentes

### Cambio mínimo en 4 skills

Los siguientes skills agregan **un paso final no-bloqueante** después de su ejecución actual:

| Skill | Paso nuevo |
|-------|-----------|
| `/sync-facturas` | Parsear RUTs del output de sync_db.py → `python scripts/wiki_update.py --ruts RUT1,RUT2` |
| `/sync-nc` | Parsear RUTs afectados → `python scripts/wiki_update.py --ruts RUT1,RUT2` |
| `/conciliar-banco` | Parsear RUTs conciliados → `python scripts/wiki_update.py --ruts RUT1,RUT2` |
| `/monitoreo-facturas` | Mismo patrón que sync-facturas |

**Mecanismo de comunicación de RUTs:** El skill de Claude Code parsea el output de texto de `sync_db.py` (que ya imprime los clientes procesados) y extrae los RUTs. No se modifica `sync_db.py` — el parsing lo hace el skill en su SKILL.md. Si no se pueden extraer RUTs del output, se ejecuta `wiki_update.py --todos` como fallback.

**Regla:** Si `wiki_update.py` falla, el skill muestra warning pero NO falla. El sync/conciliación ya se completó exitosamente.

---

## 9. Lo que NO cambia

- Pipeline DTE: parse_dte.py → validate_changes.py → sync_db.py (intacto)
- Esquema de BD: no se agregan tablas ni columnas
- Flag `.changes_validated` y hook de protección de `changes.json`
- Skills existentes: `/consultar-ventas`, `/flujo-caja`, `/agregar-gasto`, `/reporte-semanal` funcionan exactamente igual
- Archivo `.env` y `.mcp.json`

---

## 10. Visión futura (no se implementa ahora)

| Fase | Descripción | Dependencia |
|------|-------------|-------------|
| Fase 2 | Wiki de inteligencia financiera (tendencias, estacionalidad, márgenes) | Fase 1 funcionando |
| Fase 3 | Wiki operacional/producción (lotes, inventario, capacidad) | Datos de producción en BD |
| Fase 4 | Agentes programados con scheduled tasks (actualización automática sin intervención) | Fase 1 estable |
| Fase 5 | Skills que consultan wiki para enriquecer respuestas (integrar wiki en /consultar-ventas, etc.) | Fase 1 validada |

---

## 11. Criterios de éxito

La Fase 1 se considera exitosa si:

1. `/wiki-init` genera fichas para todos los clientes existentes en BD
2. Después de `/sync-facturas`, las fichas de clientes afectados se actualizan automáticamente
3. `/perfil-cliente Marina` devuelve una respuesta con métricas + contexto narrativo
4. `/wiki-lint` detecta correctamente inconsistencias
5. La carpeta `wiki/` se abre como vault en Obsidian con graph view funcional
6. Las notas del agente se preservan entre actualizaciones (append-only)
7. Ningún skill existente se rompe
