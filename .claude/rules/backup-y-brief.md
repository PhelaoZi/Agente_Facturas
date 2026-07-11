---
paths:
  - "scripts/backup_db.py"
  - "scripts/generar_brief.py"
  - "scripts/instalar_tarea_*.ps1"
  - "app/briefing/**"
  - "tests/test_backup_db.py"
  - "tests/test_briefing_data.py"
---

# Backup de la base de datos

Backup diario automatizado (Tarea Programada de Windows "Zigurat - Backup BD",
23:00, corre al encender si el notebook estaba apagado):

- **Cuándo corre:** la tarea es `LogonType: Interactive` + `StartWhenAvailable`.
  Corre a las 23:00 si hay sesión iniciada; si el notebook estaba apagado o sin
  sesión, corre apenas inicias sesión ese día. Es decir: **basta con que
  prendas el notebook e inicies sesión en el día para tener backup.** No corre
  con la sesión cerrada (aceptable para un equipo mono-usuario).
- **Script:** `scripts/backup_db.py` — pg_dump formato custom comprimido,
  verificado con `pg_restore --list` (lee el header/TOC: detecta un dump
  truncado o sin cabecera, no corrupción interna de bloques) antes de quedar
  firme. La garantía real de restaurabilidad se validó restaurando a una BD
  temporal y comparando conteos (ver el plan).
- **Destino:** `C:\Users\cdela\OneDrive\Backups\zigurat-db\` (OneDrive lo sube
  a la nube). `_estado.json` ahí mismo registra el último intento y último OK.
- **Retención:** 60 días de dumps diarios + el primer dump de cada mes para
  siempre.
- **Log:** `logs/backup_db.log`.
- **Restaurar:** procedimiento completo en el docstring de `backup_db.py`
  (createdb + pg_restore; selectivo por tabla con `-t`).
- **Reinstalar la tarea** (cambio de hora o de ruta del proyecto):
  `powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea_backup.ps1`.

El spec completo está en `docs/superpowers/specs/2026-06-11-backup-bd-design.md`.

# Brief diario automático

Reporte de negocio generado cada mañana (Tarea Programada de Windows
"Zigurat - Brief Diario", 08:00, `StartWhenAvailable` igual que el backup):

- **Qué incluye:** deuda total con desglose por antigüedad, top 5 deudores,
  facturas vencidas (+30 días), cobrado y ventas de los últimos 7 días,
  clientes inactivos (+60 días).
- **Solo lectura:** no modifica la BD. Reutiliza las reglas canónicas de
  cobranza (`fecha_pago IS NULL`, excluye NC e `incobrable`).
- **Capa de datos:** `app/briefing/data.py` (funciones reutilizables, testeadas
  con cursor falso en `tests/test_briefing_data.py`). Render en
  `app/briefing/render.py`.
- **Salida:** `briefs/YYYY-MM-DD.md` (historial committeable del negocio).
- **Generar manualmente:** `python scripts/generar_brief.py`
- **Reinstalar la tarea:**
  `powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea_brief.ps1`
