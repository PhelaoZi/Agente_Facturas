# Especificación de diseño — Backup automatizado de PostgreSQL (Zigurat ERP)

- **Fecha:** 2026-06-11
- **Autor:** Christian de la Fuente (con Claude Code)
- **Estado:** Aprobado para planificación
- **Proyecto:** Zigurat ERP — Agente Facturas

---

## 1. Contexto y objetivo

La base `dte_facturas_chile` es el activo central del proyecto y **no es
reconstruible** desde los XMLs del SII: `fecha_pago`, `conciliaciones`,
`gastos_operativos`, `cuentas_por_pagar`, recetas y costos viven solo en
PostgreSQL. Hoy no existe ningún respaldo. Si el disco del notebook muere o un
script ejecuta un UPDATE incorrecto, se pierde el historial de cobranza
completo.

El objetivo es un **backup diario automatizado, verificado y con retención**,
que se suba a la nube vía OneDrive sin intervención manual.

Datos relevantes del entorno (verificados):

| Parámetro | Valor |
|-----------|-------|
| Motor | PostgreSQL 16.11 local, puerto 5432 |
| Tamaño de la BD | ~11 MB (dump comprimido estimado: 1–2 MB) |
| `pg_dump` | `C:\Program Files\PostgreSQL\16\bin\pg_dump.exe` (no está en PATH) |
| SO | Windows 11, notebook (no siempre encendido) |
| Horario elegido | 23:00, con recuperación si el equipo estaba apagado |

---

## 2. Alcance

### v1 — incluye

- Script `scripts/backup_db.py`: dump comprimido + verificación + retención +
  log + archivo de estado.
- Script `scripts/instalar_tarea_backup.ps1`: crea la Tarea Programada de
  Windows (one-shot, idempotente).
- Carpeta destino en OneDrive **fuera del repo**: `C:\Users\cdela\OneDrive\Backups\zigurat-db\`.
- Tests pytest de la lógica pura (retención, nombres, localización de pg_dump).
- Procedimiento de restauración documentado.

### v1 — explícitamente fuera (diferido)

- Notificaciones por correo/WhatsApp si el backup falla (queda log + estado).
- Backup de otras bases de datos del cluster.
- Integración visual con el dashboard ("último backup: hace N días") — el
  archivo `_estado.json` queda listo para habilitarla después.
- PITR / backups incrementales (innecesario para 11 MB con syncs semanales).

---

## 3. Decisiones clave y su justificación

| Decisión | Elección | Por qué |
|----------|----------|---------|
| Lenguaje | **Python** (stack del proyecto) | Reusa el patrón `_load_env()` y `logs/`; testeable con pytest como `tests/` existente. PowerShell introduciría un segundo lenguaje y duplicaría la lectura del `.env`. |
| Formato de dump | **custom comprimido (`-Fc`)** | Comprime (~1–2 MB) y permite restauración selectiva por tabla con `pg_restore`, no solo completa. |
| Verificación | **`pg_restore --list` sobre cada dump** | Un backup ilegible es peor que ninguno: da falsa seguridad. Si la verificación falla, el archivo se borra y el run termina en error. |
| Destino | **OneDrive fuera del repo** | OneDrive sube a la nube automáticamente → el backup sobrevive robo/pérdida del notebook. Fuera del repo para no ensuciar git. |
| Programación | **Tarea Programada de Windows, diaria 23:00, `StartWhenAvailable`** | El usuario llega tarde a casa; si el notebook está apagado a las 23:00, la tarea corre apenas se enciende. |
| Contraseña | **Variable de entorno `PGPASSWORD` del subproceso** | Nunca en la línea de comandos (visible en el administrador de tareas) ni hardcodeada (regla del proyecto). |
| Retención | **60 días diarios + primer dump de cada mes para siempre** | Cubre errores detectados tarde; el histórico mensual cuesta ~24 MB/año. |

---

## 4. Componentes

```
scripts/
  backup_db.py              # Dump + verificación + retención + log + estado
  instalar_tarea_backup.ps1 # Crea/actualiza la Tarea Programada (idempotente)
tests/
  test_backup_db.py         # Lógica pura: retención, nombres, localización pg_dump
logs/
  backup_db.log             # Log append de cada ejecución (gitignored, ya lo está logs/)
C:\Users\cdela\OneDrive\Backups\zigurat-db\   # ← fuera del repo
  zigurat_dte_2026-06-11_2300.dump
  _estado.json              # Resultado del último intento (para dashboard futuro)
```

### 4.1 `scripts/backup_db.py`

Flujo de ejecución:

1. `_load_env()` (patrón del proyecto, sin python-dotenv).
2. Resolver configuración:
   - Conexión: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` del `.env`.
   - `BACKUP_DIR` del `.env`; default `C:\Users\cdela\OneDrive\Backups\zigurat-db`.
   - `PG_DUMP_PATH` del `.env` (opcional); si no está, autodetectar.
3. **Localizar `pg_dump.exe`** (en este orden):
   1. `PG_DUMP_PATH` del `.env`, si existe.
   2. Glob `C:\Program Files\PostgreSQL\*\bin\pg_dump.exe` → la versión más alta.
   3. `shutil.which("pg_dump")`.
   - Si no se encuentra → error claro y exit 1.
4. Crear `BACKUP_DIR` si no existe.
5. Ejecutar dump a archivo temporal `.part`:
   `pg_dump -Fc -h HOST -p PORT -U USER -d DB_NAME -f <archivo>.part`
   con `PGPASSWORD` en el entorno del subproceso. Timeout defensivo (5 min).
6. **Verificar** con `pg_restore --list <archivo>.part`. Si falla → borrar el
   `.part`, log de error, exit 1.
7. Renombrar `.part` → `zigurat_dte_YYYY-MM-DD_HHMM.dump` (el `.part` evita
   que OneDrive suba — o una restauración use — un dump a medio escribir).
8. **Retención**: borrar dumps con más de 60 días, **excepto** el primer dump
   de cada mes calendario (se conserva indefinidamente).
9. Escribir `_estado.json` y log. Exit 0.

Manejo de errores (reglas del proyecto): todo paso externo va en `try/except`
con contexto en el log; nunca un catch vacío; cualquier fallo → `_estado.json`
con `"resultado": "error"`, log y **exit code 1** (el Programador de Tareas lo
registra como "Last Run Result" ≠ 0).

### 4.2 Formato de `_estado.json`

```json
{
  "ultimo_intento": "2026-06-11T23:00:14",
  "resultado": "ok",
  "ultimo_ok": "2026-06-11T23:00:14",
  "archivo": "zigurat_dte_2026-06-11_2300.dump",
  "tamano_bytes": 1843200,
  "duracion_segundos": 4.2,
  "error": null
}
```

En caso de fallo, `resultado: "error"`, `error` con el mensaje, y `ultimo_ok`
conserva la fecha del último backup exitoso anterior.

### 4.3 `scripts/instalar_tarea_backup.ps1`

- Detecta la ruta absoluta de `python.exe` (`Get-Command python`) — la tarea
  no depende del PATH del momento de ejecución.
- `Register-ScheduledTask` con `-Force` (idempotente: re-ejecutarlo actualiza
  la tarea, como las migraciones del proyecto):
  - Nombre: `Zigurat - Backup BD`.
  - Trigger: diario 23:00.
  - Settings: `StartWhenAvailable` (corre al encender si estaba apagado),
    `-DontStopIfGoingOnBatteries`, `-AllowStartIfOnBatteries` (es un notebook).
  - Action: `python.exe <ruta absoluta>\scripts\backup_db.py`, con
    `WorkingDirectory` en la raíz del proyecto.
- Al final ejecuta la tarea una vez (`Start-ScheduledTask`) y muestra el
  resultado, para validar la instalación en el momento.

### 4.4 Política de retención (lógica pura, testeable)

Función `archivos_a_borrar(archivos: list[str], hoy: date) -> list[str]`:

- Entrada: nombres `zigurat_dte_YYYY-MM-DD_HHMM.dump` (la fecha se parsea del
  nombre, no del mtime — OneDrive puede alterar mtimes).
- Se borra un archivo si: `hoy - fecha > 60 días` **y no es** el dump más
  antiguo de su mes calendario.
- Archivos con nombre no reconocido **nunca se borran** (defensivo).

---

## 5. Restauración (procedimiento documentado)

Restauración completa (disco nuevo / BD corrupta):

```powershell
# 1. Crear la BD vacía si no existe
& "C:\Program Files\PostgreSQL\16\bin\createdb.exe" -h localhost -U postgres dte_facturas_chile

# 2. Restaurar el dump (pide la contraseña del .env)
& "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" `
    --clean --if-exists -h localhost -U postgres `
    -d dte_facturas_chile "C:\Users\cdela\OneDrive\Backups\zigurat-db\zigurat_dte_2026-06-11_2300.dump"
```

Restauración selectiva de una tabla: agregar `-t ventas` al `pg_restore`.

Este procedimiento queda también en el docstring de `backup_db.py`.

---

## 6. Estrategia de testing

- **TDD sobre la lógica pura** (`tests/test_backup_db.py`, sin BD ni pg_dump):
  - `archivos_a_borrar()`: reciente se conserva; >60 días se borra; >60 días
    pero primero de su mes se conserva; nombre desconocido nunca se borra.
  - Generación de nombre de archivo a partir de un datetime.
  - Localización de pg_dump: prioridad `.env` > glob > PATH (con `monkeypatch`
    y `tmp_path`).
- **Verificación de integración (manual, una vez al implementar):**
  1. Ejecutar `python scripts/backup_db.py` real.
  2. Restaurar el dump a una BD temporal `dte_zigurat_restore_test`.
  3. Comparar `COUNT(*)` de `ventas` entre original y restaurada.
  4. Borrar la BD temporal.
  - Esto valida el ciclo completo backup→restore, que es la única garantía
    real de que el backup sirve.

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Notebook apagado a las 23:00 | `StartWhenAvailable`: la tarea corre al encender el equipo. |
| PostgreSQL no está corriendo al ejecutar | Error logueado con contexto, exit 1, `_estado.json` en error; el dump del día siguiente lo cubre. |
| Dump corrupto / parcial | Escritura a `.part` + verificación `pg_restore --list` antes de renombrar; si falla, se borra. |
| OneDrive sube un archivo a medio escribir | El `.part` se renombra solo al final; OneDrive sube la versión final. |
| Cambio de versión de PostgreSQL | Autodetección por glob elige la versión más alta; `PG_DUMP_PATH` en `.env` como override manual. |
| Fallos silenciosos prolongados | `_estado.json` con `ultimo_ok` consultable; integración con dashboard queda diferida pero el dato ya existe. |
| Python no está en el PATH cuando corre la tarea | El instalador fija la **ruta absoluta** de python.exe en la tarea. |

---

## 8. Criterios de aceptación (v1)

1. `python scripts/backup_db.py` genera un `.dump` verificado en
   `C:\Users\cdela\OneDrive\Backups\zigurat-db\` y registra el run en
   `logs/backup_db.log` y `_estado.json`.
2. El dump restaurado en una BD temporal tiene el mismo número de filas en
   `ventas` que la BD original.
3. La retención conserva dumps recientes y el primero de cada mes, y borra el
   resto de los antiguos (verificado por tests).
4. `instalar_tarea_backup.ps1` deja la tarea `Zigurat - Backup BD` visible en
   el Programador de Tareas, diaria 23:00 con `StartWhenAvailable`, y una
   ejecución inmediata de prueba termina con resultado 0.
5. Todos los tests de `tests/test_backup_db.py` pasan.
6. Ningún secreto queda hardcodeado en el código ni visible en la línea de
   comandos de la tarea.
