@echo off
setlocal
title envioEVO Dashboard
color 0A

cd /d "%~dp0"

echo.
echo  =====================================================
echo    envioEVO Dashboard
echo  =====================================================
echo.
echo   Servidor:  http://127.0.0.1:8080
echo   Pasta:     %CD%
echo.
echo   Feche esta janela para parar o servidor.
echo  =====================================================
echo.

REM Abre o browser depois de 2s (em paralelo, pra dar tempo do Flask subir)
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8080"

REM Inicia o servidor no foreground (logs visiveis nesta janela)
python dash.py

echo.
echo Servidor encerrado.
pause
