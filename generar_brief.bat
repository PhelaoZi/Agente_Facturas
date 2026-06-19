@echo off
chcp 65001 >nul
title Zigurat - Brief Diario
cd /d "%~dp0"
echo ============================================
echo    ZIGURAT - Brief Diario
echo ============================================
echo.
echo  Generando el resumen del dia... espera unos segundos.
echo.
python "scripts\generar_brief.py"
if errorlevel 1 (
  echo.
  echo  No se pudo iniciar con "python". Probando con "py"...
  py "scripts\generar_brief.py"
)
echo.
echo ============================================
echo  Listo. El brief tambien quedo guardado en
echo  la carpeta  briefs\  por si lo quieres abrir
echo  o imprimir despues.
echo ============================================
echo.
echo  Puedes cerrar esta ventana.
pause
