# razon_social_receptor + Skill /sync-nc Formal

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Poblar `razon_social_receptor` en cada fila de `ventas` al sincronizar, y convertir `/sync-nc` en un skill que detecta y procesa automáticamente todos los XMLs pendientes en `Notas de Credito/`.

**Architecture:**
- Tarea 1: `sync_db.py` ya recibe el nombre del receptor vía `doc["cliente"]["razon_social"]`; solo falta pasarlo a `insertar_venta()` e incluirlo en el INSERT.
- Tarea 2: Crear `detectar_pendientes_nc.py` (patrón idéntico a `detectar_pendientes.py`) que compara XMLs en `Notas de Credito/` contra la BD y emite `__PENDIENTES__:...`. Actualizar `SKILL.md` para usar ese script cuando no se pase argumento.

**Tech Stack:** Python 3, psycopg2, PostgreSQL 14+

---

### Tarea 1: Guardar razon_social_receptor en ventas

**Archivos:**
- Modificar: `scripts/sync_db.py` líneas 204-240 (función `insertar_venta` + llamada en loop)

---

**Paso 1 — Agregar razon_social_receptor al INSERT en `insertar_venta()`**

Líneas 207-237 actuales no incluyen el campo. Reemplazar la función completa:

```python
def insertar_venta(cur, venta):
    """
    Inserta una venta en la tabla ventas.
    razon_social_receptor viene del campo cliente.razon_social del changes.json.
    """
    cur.execute("""
        INSERT INTO ventas (
            folio,
            tipo_documento,
            fecha,
            rut_cliente,
            razon_social_receptor,
            monto_neto,
            iva,
            impuesto_adicional,
            monto_total,
            folio_referencia,
            tipo_documento_referencia,
            razon_referencia
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        venta["folio"],
        venta["tipo_documento"],
        venta["fecha"],
        venta["rut_cliente"],
        venta.get("razon_social_receptor"),   # ← nuevo campo
        venta["monto_neto"],
        venta["iva"],
        venta.get("impuesto_adicional", 0),
        venta["monto_total"],
        venta.get("folio_referencia"),
        venta.get("tipo_documento_referencia"),
        venta.get("razon_referencia"),
    ))
```

**Paso 2 — Pasar razon_social al venta dict en el loop `sincronizar()`**

En la función `sincronizar()`, dentro del `for doc in docs_nuevos:` (línea ~291), antes de la llamada a `insertar_venta`, agregar una línea que inyecta el valor:

```python
# Inyectar razon_social_receptor desde el objeto cliente
venta["razon_social_receptor"] = cliente.get("razon_social")
```

Ubicación exacta: después de `venta = doc["venta"]` y `cliente = doc["cliente"]`, antes de `insertar_venta(cur, venta)`.

**Paso 3 — Verificar con consulta directa**

```bash
cd C:\Users\cdela\OneDrive\Escritorio\Agente_Facturas
python -X utf8 -c "
import sys; sys.path.insert(0,'scripts')
from sync_db import _load_env
import psycopg2, os
_load_env()
conn = psycopg2.connect(host=os.environ['DB_HOST'],port=os.environ['DB_PORT'],dbname=os.environ['DB_NAME'],user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD'])
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ventas WHERE razon_social_receptor IS NOT NULL AND tipo_documento != 61')
print('Facturas sin razon_social_receptor (deben ser 0 después del backfill):', cur.fetchone()[0])
conn.close()
"
```

Esperado: un número > 0 solo si hay facturas ya en la BD sin el campo (eso está bien, se poblará en futuras sincronizaciones). Para las existentes, hacer backfill en el Paso 4.

**Paso 4 — Backfill en registros existentes**

Actualizar las filas ya en la BD cruzando con `clientes`:

```bash
python -X utf8 -c "
import sys; sys.path.insert(0,'scripts')
from sync_db import _load_env
import psycopg2, os
_load_env()
conn = psycopg2.connect(host=os.environ['DB_HOST'],port=os.environ['DB_PORT'],dbname=os.environ['DB_NAME'],user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD'])
with conn:
    with conn.cursor() as cur:
        cur.execute('''
            UPDATE ventas v
            SET razon_social_receptor = c.razon_social
            FROM clientes c
            WHERE c.rut_cliente = v.rut_cliente
              AND v.razon_social_receptor IS NULL
        ''')
        print(f'Filas actualizadas: {cur.rowcount}')
conn.close()
"
```

Esperado: número igual al total de facturas en la BD (todas tienen NULL actualmente).

**Paso 5 — Confirmar resultado**

```bash
python -X utf8 -c "
import sys; sys.path.insert(0,'scripts')
from sync_db import _load_env
import psycopg2, os
_load_env()
conn = psycopg2.connect(host=os.environ['DB_HOST'],port=os.environ['DB_PORT'],dbname=os.environ['DB_NAME'],user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD'])
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM ventas WHERE razon_social_receptor IS NULL')
null_count = cur.fetchone()[0]
cur.execute('SELECT folio, razon_social_receptor FROM ventas WHERE tipo_documento != 61 ORDER BY folio::integer DESC LIMIT 5')
print('Ultimas 5 facturas:')
for r in cur.fetchall(): print(f'  Folio {r[0]}: {r[1]}')
print(f'Filas con NULL restantes: {null_count}')
conn.close()
"
```

Esperado: 0 filas con NULL, y los últimos 5 folios muestran su nombre de cliente.

**Paso 6 — Commit**

```bash
git add scripts/sync_db.py
git commit -m "Agrega razon_social_receptor al INSERT de ventas y backfill en registros existentes"
```

---

### Tarea 2: Skill /sync-nc con auto-detección de pendientes

**Archivos:**
- Crear: `.claude/skills/sync-nc/scripts/detectar_pendientes_nc.py`
- Modificar: `.claude/skills/sync-nc/SKILL.md`

---

**Paso 1 — Crear `detectar_pendientes_nc.py`**

Patrón idéntico a `detectar_pendientes.py` pero apuntando a `Notas de Credito/`.
Guardar en `.claude/skills/sync-nc/scripts/detectar_pendientes_nc.py`:

```python
#!/usr/bin/env python3
"""
detectar_pendientes_nc.py — Zigurat ERP
Detecta XMLs en 'Notas de Credito/' que aún no están sincronizados en la BD.
Imprime __PENDIENTES__:archivo1.xml,archivo2.xml para que el skill los procese.
"""
import os
import sys
import re
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: Falta psycopg2. Instala con: pip install psycopg2-binary")
    sys.exit(1)


def _load_env():
    env_file = Path(__file__).parent.parent.parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "dte_facturas_chile"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

# Carpeta de Notas de Crédito (relativa al raíz del proyecto)
NC_DIR = Path("Notas de Credito")


def get_folios_from_xml(xml_path):
    """Extrae pares (folio, tipo_dte) del XML sin parsear el árbol completo."""
    try:
        content = xml_path.read_text(encoding="iso-8859-1")
        folios = re.findall(r"<Folio>(\d+)</Folio>", content)
        tipos = re.findall(r"<TipoDTE>(\d+)</TipoDTE>", content)
        return list(zip(folios, tipos))
    except Exception:
        return []


def main():
    if not NC_DIR.exists():
        print(f"ERROR: No se encontró la carpeta '{NC_DIR}'")
        sys.exit(1)

    xmls = sorted(NC_DIR.glob("*.xml"))
    if not xmls:
        print("No hay archivos XML en 'Notas de Credito/'.")
        print("__PENDIENTES__:")
        return

    try:
        conn = psycopg2.connect(**DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"ERROR: No se pudo conectar a PostgreSQL: {e}")
        sys.exit(1)

    cur = conn.cursor()
    pendientes = []

    for xml_path in xmls:
        pares = get_folios_from_xml(xml_path)
        if not pares:
            continue

        # Solo verificar NCs (tipo 61)
        pares_nc = [(f, t) for f, t in pares if t == "61"]
        if not pares_nc:
            continue

        placeholders = ",".join(["(%s,%s)"] * len(pares_nc))
        valores = [v for par in pares_nc for v in par]

        cur.execute(
            f"SELECT COUNT(*) FROM ventas "
            f"WHERE (folio::integer, tipo_documento::text) IN ({placeholders})",
            valores,
        )
        encontrados = cur.fetchone()[0]

        if encontrados < len(pares_nc):
            pendientes.append(xml_path.name)
            faltantes = len(pares_nc) - encontrados
            print(f"  Pendiente: {xml_path.name} ({faltantes} NC sin sincronizar)")
        else:
            print(f"  OK: {xml_path.name} (ya sincronizado)")

    conn.close()

    if pendientes:
        print(f"\n{len(pendientes)} archivo(s) pendiente(s) de sincronizar.")
        print(f"__PENDIENTES__:{','.join(pendientes)}")
    else:
        print("\n✅ Todo sincronizado. No hay XMLs pendientes en 'Notas de Credito/'.")
        print("__PENDIENTES__:")


if __name__ == "__main__":
    main()
```

**Paso 2 — Verificar que el script funciona**

```bash
cd C:\Users\cdela\OneDrive\Escritorio\Agente_Facturas
python -X utf8 .claude/skills/sync-nc/scripts/detectar_pendientes_nc.py
```

Esperado: lista de XMLs con estado OK/Pendiente y línea `__PENDIENTES__:...` al final.

**Paso 3 — Actualizar `SKILL.md` de sync-nc**

Reemplazar contenido completo con versión que tiene dos modos:
- **Sin argumento**: detecta automáticamente y procesa todos los pendientes
- **Con argumento**: procesa el archivo específico (comportamiento actual)

```markdown
---
name: sync-nc
description: Sincroniza Notas de Crédito DTE (tipo 61) desde XMLs del SII a PostgreSQL (Zigurat ERP). Sin argumento detecta y procesa todos los XMLs pendientes en "Notas de Credito/". Con argumento procesa ese archivo específico. Usar cuando se mencione sincronizar notas de crédito, procesar NC, cargar DTE tipo 61, o cuando se quiera saber si hay NCs pendientes.
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

## Modo automático (sin argumento)

### Paso A1 — Detectar XMLs pendientes

```bash
python .claude/skills/sync-nc/scripts/detectar_pendientes_nc.py
```

- Si la línea `__PENDIENTES__:` viene vacía → reportar "Todo sincronizado" y detener.
- Si viene con archivos → continuar con cada uno en orden.

### Paso A2 — Procesar cada pendiente

Para cada archivo en `__PENDIENTES__`, ejecutar los 3 pasos en secuencia:

```bash
python -X utf8 scripts/parse_dte.py "Notas de Credito/ARCHIVO.xml"
```
Si falla → reportar error y pasar al siguiente.

```bash
python -X utf8 scripts/validate_changes.py changes.json
```
Si falla → reportar errores y pasar al siguiente.

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
```

**Paso 4 — Probar modo automático**

```bash
cd C:\Users\cdela\OneDrive\Escritorio\Agente_Facturas
python -X utf8 .claude/skills/sync-nc/scripts/detectar_pendientes_nc.py
```

Esperado con la BD actual (todos ya sincronizados): `✅ Todo sincronizado. __PENDIENTES__:` vacío.

**Paso 5 — Commit**

```bash
git add .claude/skills/sync-nc/
git commit -m "Skill sync-nc: agrega auto-deteccion de pendientes en Notas de Credito/"
```

---

## Orden de ejecución

1. Tarea 1 completa (pasos 1-6) → `razon_social_receptor` funcional
2. Tarea 2 completa (pasos 1-5) → skill `/sync-nc` formal con auto-detección
