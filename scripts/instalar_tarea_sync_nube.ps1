# instalar_tarea_sync_nube.ps1 - Zigurat ERP
# Crea/actualiza la Tarea Programada "Zigurat - Sync Nube" (idempotente:
# re-ejecutar este script actualiza la tarea).
#
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\instalar_tarea_sync_nube.ps1

$ErrorActionPreference = "Stop"

$proyecto = Split-Path -Parent $PSScriptRoot
$script = Join-Path $proyecto "scripts\sync_nube.py"
if (-not (Test-Path $script)) {
    throw "No se encontró $script. Ejecuta este instalador desde el repo del proyecto."
}

# Ruta absoluta de python.exe: la tarea no depende del PATH del momento de ejecución.
$python = (Get-Command python -ErrorAction Stop).Source

$accion = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $proyecto
$trigger = New-ScheduledTaskTrigger -Daily -At "08:15"
# StartWhenAvailable: si el notebook estaba apagado a las 08:15, corre al encenderlo.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "Zigurat - Sync Nube" -Action $accion -Trigger $trigger `
    -Settings $settings -Description "Replica diaria a la nube (InsForge) de Zigurat (proyecto Agente_Facturas)" `
    -Force | Out-Null

Write-Host "Tarea 'Zigurat - Sync Nube' instalada: diaria 08:15, StartWhenAvailable."
Write-Host "Python: $python"
Write-Host "Script: $script"

# Ejecución de prueba inmediata para validar la instalación.
Write-Host ""
Write-Host "Ejecutando la tarea ahora como prueba..."
Start-ScheduledTask -TaskName "Zigurat - Sync Nube"
Start-Sleep -Seconds 15
$info = Get-ScheduledTaskInfo -TaskName "Zigurat - Sync Nube"
Write-Host "Ultima ejecucion: $($info.LastRunTime) | Resultado: $($info.LastTaskResult) (0 = OK)"
