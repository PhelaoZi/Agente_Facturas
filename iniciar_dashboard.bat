@echo off
chcp 65001 >nul
title Zigurat - Centro de Comando
cd /d "%~dp0"
echo ============================================
echo    ZIGURAT - Centro de Comando
echo ============================================
echo.
echo  Iniciando el panel... tu navegador se abrira
echo  solo en unos segundos.
echo.
echo  Para CERRAR el panel: cierra esta ventana
echo  o presiona Ctrl + C.
echo ============================================
echo.
echo  Cerrando cualquier version anterior del panel...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8777 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
echo.
python "app\dashboard.py"
if errorlevel 1 (
  echo.
  echo  No se pudo iniciar con "python". Probando con "py"...
  py "app\dashboard.py"
)
echo.
echo  El panel se ha detenido.
pause
