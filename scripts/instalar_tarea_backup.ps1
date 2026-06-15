# instalar_tarea_backup.ps1 - Zigurat ERP
# Crea/actualiza la Tarea Programada "Zigurat - Backup BD" (idempotente:
# re-ejecutar este script actualiza la tarea, como las migraciones del proyecto).
#
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea_backup.ps1

$ErrorActionPreference = "Stop"

$proyecto = Split-Path -Parent $PSScriptRoot
$script = Join-Path $proyecto "scripts\backup_db.py"
if (-not (Test-Path $script)) {
    throw "No se encontró $script. Ejecuta este instalador desde el repo del proyecto."
}

# Ruta absoluta de python.exe: la tarea no depende del PATH del momento de ejecución.
$python = (Get-Command python -ErrorAction Stop).Source

$accion = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $proyecto
$trigger = New-ScheduledTaskTrigger -Daily -At "23:00"
# StartWhenAvailable: si el notebook estaba apagado a las 23:00, corre al encenderlo.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "Zigurat - Backup BD" -Action $accion -Trigger $trigger `
    -Settings $settings -Description "Backup diario verificado de dte_facturas_chile a OneDrive (proyecto Agente_Facturas)" `
    -Force | Out-Null

Write-Host "Tarea 'Zigurat - Backup BD' instalada: diaria 23:00, StartWhenAvailable."
Write-Host "Python: $python"
Write-Host "Script: $script"

# Ejecución de prueba inmediata para validar la instalación.
Write-Host ""
Write-Host "Ejecutando la tarea ahora como prueba..."
Start-ScheduledTask -TaskName "Zigurat - Backup BD"
Start-Sleep -Seconds 20
$info = Get-ScheduledTaskInfo -TaskName "Zigurat - Backup BD"
Write-Host "Ultima ejecucion: $($info.LastRunTime) | Resultado: $($info.LastTaskResult) (0 = OK)"
if ($info.LastTaskResult -eq 267009) {
    Write-Host "(267009 = aun corriendo; revisa logs\backup_db.log en unos segundos)"
}
