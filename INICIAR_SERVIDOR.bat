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

set PYTHON=C:\Users\matheus.ribeiro\AppData\Local\Programs\Python\Python314\python.exe
set PYTHONIOENCODING=utf-8

:: Verifica se Python existe
if not exist "%PYTHON%" (
    echo  [ERRO] Python nao encontrado em: %PYTHON%
    echo  Verifique a instalacao do Python 3.14.
    pause
    exit /b 1
)

:: Verifica dependencias
"%PYTHON%" -c "import fastapi, uvicorn, aiosqlite, httpx" 2>nul
if errorlevel 1 (
    echo  [INFO] Instalando dependencias (aguarde)...
    "%PYTHON%" -m pip install fastapi "uvicorn[standard]" httpx python-dotenv aiosqlite openpyxl reportlab python-multipart Levenshtein unidecode
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
"%PYTHON%" -m uvicorn main:app --host 0.0.0.0 --port 8000

pause
