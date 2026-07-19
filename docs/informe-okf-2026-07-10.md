# Informe: Open Knowledge Format (OKF) de Google — ¿conviene adoptarlo en Agente Facturas?

**Fecha:** 2026-07-10
**Conclusión anticipada:** Sí conviene, pero en modo "adopción ligera": hacer la wiki
de clientes conforme a OKF (costo ~1-2 horas en `wiki_update.py`) sin adoptar ninguna
herramienta de Google. No es urgente. Lo que NO conviene es reestructurar nada del
proyecto alrededor de OKF hoy: el estándar tiene menos de un mes de vida (v0.1).

---

## 1. Qué es OKF

El **Open Knowledge Format** es una especificación abierta publicada por Google Cloud
el **12 de junio de 2026** (tech leads: Sam McVeety y Amir Hormati). Formaliza el
patrón **"LLM-wiki" de Andrej Karpathy** — exactamente el patrón que este proyecto ya
implementa en `wiki/` — en un formato portable y neutral de proveedor.

Un "bundle" OKF es simplemente **un directorio de archivos Markdown con frontmatter
YAML**, donde cada archivo representa un "concepto": una tabla, una métrica, un
playbook, una API, un cliente… lo que sea. La idea: que humanos, agentes de IA y
herramientas lean el mismo conocimiento sin SDK ni plataforma propietaria.

**Dato clave para este proyecto:** el anuncio de Google cita literalmente a Karpathy
("los LLMs no se aburren, no olvidan actualizar referencias cruzadas…"). OKF es la
estandarización del patrón que tú ya elegiste para la wiki de clientes. No es una
tecnología nueva a evaluar — es una validación externa de tu arquitectura actual.

## 2. La especificación v0.1 (resumen técnico)

Fuente: [SPEC.md en GitHub](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) (repo `GoogleCloudPlatform/knowledge-catalog`, Apache 2.0, ~6.600 estrellas).

### Estructura del bundle

```
bundle/
├── index.md          # opcional, reservado: índice sin frontmatter
├── log.md            # opcional, reservado: registro de cambios por fecha
├── <concepto>.md
└── <subdirectorio>/
    ├── index.md
    └── <concepto>.md
```

### Frontmatter de cada concepto

| Campo | Estatus | Notas |
|-------|---------|-------|
| `type` | **REQUERIDO** (el único) | Texto libre: "Cliente", "Producto", "Metric"… No hay registro central de tipos. |
| `title` | Recomendado | Nombre legible. |
| `description` | Recomendado | Resumen de una línea. |
| `resource` | Recomendado | URI del activo subyacente. |
| `tags` | Opcional | Lista YAML. |
| `timestamp` | Opcional | ISO 8601 del último cambio significativo. |
| *(otros)* | Permitidos | Los consumidores deben preservar claves desconocidas. |

### Otras reglas relevantes

- **Enlaces entre conceptos:** links Markdown estándar. Recomendado el formato
  absoluto a la raíz del bundle: `[clientes](/clientes/vdt-spa.md)`. Los consumidores
  deben tolerar enlaces rotos.
- **`index.md`:** sin frontmatter; lista de links con descripción corta ("descubrimiento
  progresivo"). Puede declarar `okf_version: "0.1"`.
- **`log.md`:** cambios agrupados por fecha ISO, **más recientes primero**.
- **Encabezados con significado convencional:** `# Schema`, `# Examples`, `# Citations`.
- **Conformidad:** un bundle es conforme si cada `.md` no reservado tiene frontmatter
  YAML parseable con `type` no vacío. Todo lo demás es orientación flexible.
- **Distribución:** repo git (recomendado), tarball, o subdirectorio de un repo mayor.

## 3. Madurez y ecosistema (a hoy, julio 2026)

| Aspecto | Estado |
|---------|--------|
| Versión | **0.1** — Google lo declara "punto de partida, no estándar terminado" |
| Edad | **4 semanas** desde el anuncio |
| Implementaciones de referencia | Producer: agente de enriquecimiento para **BigQuery**. Consumer: visualizador HTML estático (grafo interactivo, sin backend). Bundles de ejemplo (GA4, Stack Overflow, Bitcoin). |
| Integración productos Google | Knowledge Catalog (GCP) puede ingerir bundles OKF y servirlos a agentes |
| Adopción fuera de Google | Todavía ninguna significativa documentada |

**Crítica externa relevante** ([análisis de Marc Bara](https://medium.com/@marc.bara.iniesta/googles-new-format-for-agent-context-a-standard-or-just-a-folder-82fb21d92041)):
OKF estandariza el *contenedor* (carpetas + markdown + YAML), no el *significado*. Dos
bundles conformes pueden ser semánticamente incompatibles (uno escribe
`type: "BigQuery Table"`, otro `type: "table"`). No hay vocabulario común ni enlaces
tipados. Incluso el parser de referencia de Google exige 4 campos cuando la spec exige
uno. Conclusión del crítico: útil como convención práctica, insuficiente como
interoperabilidad semántica real.

Para un proyecto **mono-empresa y mono-equipo como Zigurat, esta debilidad es
irrelevante**: tú controlas productor y consumidor, así que la semántica la defines tú.
La crítica pega fuerte en escenarios multi-organización, que no es tu caso.

## 4. Comparación: tu wiki actual vs. bundle OKF conforme

Tu `wiki/` **ya es estructuralmente un bundle OKF en un ~90%**. Hasta los dos archivos
reservados de la spec (`index.md` y `log.md`) ya existen con el rol correcto.

| Elemento | Wiki actual | Spec OKF | Brecha |
|----------|-------------|----------|--------|
| Directorio de .md por concepto | ✅ `clientes/`, `conceptos/`, `indices/` | ✅ | Ninguna |
| Frontmatter YAML | ✅ `rut`, `razon_social`, `estado`, `ultima_actualizacion` | Requiere `type` | **Falta `type`** (y recomendados `title`, `description`, `timestamp`) |
| `index.md` sin frontmatter, con links descriptivos | ✅ | ✅ | Solo el formato de links |
| `log.md` agrupado por fecha | ✅ | Más recientes primero | Hoy está en orden cronológico ascendente (menor) |
| Enlaces entre conceptos | `[[wikilinks]]` estilo Obsidian | Links Markdown `[x](/ruta.md)` | **Divergencia principal** |
| Campos extra propios | ✅ | Permitidos explícitamente | Ninguna — `rut`, `estado`, etc. se quedan tal cual |
| Distribución vía git | ✅ (repo del proyecto) | Recomendado | Ninguna |

Sobre los wikilinks: **Obsidian soporta links Markdown estándar con la misma
funcionalidad** (graph view, backlinks). Convertir `[[VDT SPA]]` a
`[VDT SPA](/clientes/vdt-spa.md)` no pierde nada en Obsidian y gana conformidad OKF.
El costo es solo estético (los wikilinks son más limpios de leer en crudo). Ojo: los
links absolutos `/clientes/...` resuelven bien en Obsidian solo si la raíz del vault
es `wiki/`; si el vault es el proyecto completo, conviene usar links relativos
(`../clientes/vdt-spa.md`), que la spec también acepta.

La capa `raw/clientes/*.json` **queda fuera de OKF** (la spec es solo Markdown) y no
hay que tocarla: es tu fuente de verdad histórica, y cada ficha puede apuntarle con el
campo `resource:` si se quiere.

## 5. Análisis de conveniencia

### A favor de adoptar (modo ligero)

1. **Costo casi nulo.** La wiki se genera desde código (`wiki_update.py`); la brecha
   se cierra tocando las plantillas de frontmatter en un solo lugar y regenerando
   (la regeneración ya preserva "Notas del agente" por diseño).
2. **Interoperabilidad futura real en tu contexto.** El proyecto hermano
   `zigurat-erp` comparte la BD; un bundle OKF le da un contrato de lectura estándar
   a esa copia y a cualquier agente futuro (incluido tu plan de ecosistema Anthropic:
   OKF es vendor-neutral, no exige nada de Google).
3. **Visualizador gratis.** El consumer HTML de referencia renderiza cualquier bundle
   como grafo interactivo sin backend — una vista de tu cartera de clientes sin
   escribir código.
4. **Validación y vocabulario profesional.** Tu wiki pasa de "patrón casero estilo
   Karpathy" a "bundle conforme a un estándar abierto publicado por Google". Para tu
   objetivo de asesorar sobre agentes, poder decir y demostrar esto tiene valor.
5. **Sin lock-in.** Es solo frontmatter + links. Si OKF muere, no perdiste nada: los
   campos agregados siguen siendo útiles.

### En contra / precauciones

1. **Ganancia funcional hoy: cero.** Tu único consumidor actual (los agentes Claude
   del proyecto) ya lee la wiki perfectamente. OKF no mejora ninguna respuesta ni
   ningún pipeline existente.
2. **v0.1 con 4 semanas de vida.** Puede cambiar. Mitigación: los cambios minor son
   retrocompatibles por definición de la spec, y tu exposición es un template en un
   script.
3. **El tooling de Google es GCP-céntrico** (BigQuery, Knowledge Catalog). Nada de
   eso aplica a tu PostgreSQL local. No adoptar herramientas, solo el formato.
4. **Sin semántica estándar** (la crítica del punto 3): no esperes que un agente
   externo "entienda" tus fichas solo por ser OKF. Irrelevante mientras seas tu único
   consumidor, pero conviene saberlo.

## 6. Recomendación

**Adoptar el formato, no el ecosistema.** Concretamente, cuando toque una sesión de
trabajo sobre la wiki (no amerita sesión dedicada):

1. En `wiki_update.py`, agregar al frontmatter de las fichas de cliente:
   `type: Cliente`, `title` (razón social), `description` (una línea generada:
   estado + producto principal + deuda), `timestamp` (reemplaza o convive con
   `ultima_actualizacion`). Ídem `type: Producto` en `conceptos/productos/` y
   `type: Concepto` / `type: Indice` en el resto. Los campos actuales (`rut`,
   `estado`, …) se mantienen — la spec permite claves extra.
2. Declarar `okf_version: "0.1"` en el frontmatter del `index.md` raíz… con la
   salvedad de que la spec dice que `index.md` no lleva frontmatter para conceptos;
   la declaración de versión es la excepción prevista por la propia spec.
3. Invertir el orden de `log.md` (más reciente primero) — un cambio de una línea en
   el generador.
4. (Opcional, decisión estética tuya) Convertir `[[wikilinks]]` a links Markdown
   relativos. Obsidian no pierde graph view ni backlinks. Si prefieres mantener los
   wikilinks, la wiki queda "OKF-conforme con links no estándar" — los consumidores
   deben tolerar links rotos, así que sigue siendo un bundle válido.
5. Probar el visualizador HTML de referencia contra `wiki/` como verificación de
   conformidad (y de paso obtener la vista de grafo).
6. `wiki_lint.py` es el lugar natural para auditar la conformidad OKF a futuro
   (frontmatter parseable + `type` no vacío).

**Qué NO hacer:** no migrar `raw/` a OKF, no adoptar Knowledge Catalog ni el
enrichment agent de BigQuery, no convertir briefs/recetas/costos a OKF por ahora, y
no bloquear ningún desarrollo esperando que el estándar madure.

## 7. Fuentes

- [Anuncio oficial — Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/)
- [Especificación OKF v0.1 — GitHub](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
- [Repositorio knowledge-catalog (spec + reference implementations)](https://github.com/GoogleCloudPlatform/knowledge-catalog)
- [Análisis crítico: "A Standard, or Just a Folder?" — Marc Bara](https://medium.com/@marc.bara.iniesta/googles-new-format-for-agent-context-a-standard-or-just-a-folder-82fb21d92041)
- [Cobertura: MarkTechPost](https://www.marktechpost.com/2026/06/16/google-cloud-introduces-open-knowledge-format-okf-a-vendor-neutral-markdown-spec-for-giving-ai-agents-curated-context/) · [Search Engine Journal](https://www.searchenginejournal.com/google-cloud-announces-the-open-knowledge-format/579253/)
