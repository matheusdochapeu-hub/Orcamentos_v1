@echo off
title Sistema de Orcamentos Publicos - v1.0
color 0A
chcp 65001 > nul
echo.
echo  ================================================
echo    SISTEMA DE ORCAMENTOS PUBLICOS - v1.0
echo  ================================================
echo.
echo  Iniciando servidor...
echo.

set PYTHONIOENCODING=utf-8
set "PY_CMD="

:: 1. Tenta inicializador oficial py -3
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3"
    goto :PYTHON_OK
)

:: 2. Tenta comando python no PATH
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :PYTHON_OK
)

:: 3. Fallback para caminho local padrão
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    goto :PYTHON_OK
)

:PYTHON_OK
if "%PY_CMD%"=="" (
    echo  [ERRO] Python nao encontrado no sistema.
    echo  Verifique se o Python 3.10+ esta instalado e adicionado ao PATH.
    pause
    exit /b 1
)

:: Verifica dependencias
%PY_CMD% -c "import fastapi, uvicorn, aiosqlite, httpx" 2>nul
if errorlevel 1 (
    echo  [INFO] Instalando dependencias (aguarde)...
    %PY_CMD% -m pip install fastapi "uvicorn[standard]" httpx python-dotenv aiosqlite openpyxl reportlab python-multipart unidecode
    echo.
)

:: Abre o navegador automaticamente apos 3 segundos
start "" /B cmd /C "timeout /t 4 /nobreak > nul && start http://localhost:8000"

:: Inicia o servidor
echo  [OK] Servidor: http://localhost:8000
echo  [OK] Pressione CTRL+C para parar
echo.
echo  IMPORTANTE: Na primeira busca, o sistema vai baixar o catalogo
echo  CATMAT/CATSER do governo (~5 minutos). Nas proximas buscas sera instantaneo.
echo.

cd /d "%~dp0backend"
%PY_CMD% -m uvicorn main:app --host 0.0.0.0 --port 8000

pause
