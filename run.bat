@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python nao foi encontrado. Instale o Python 3.10 ou superior em https://python.org
  echo e marque a opcao "Add Python to PATH" durante a instalacao.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Primeira execucao: criando ambiente virtual e instalando dependencias...
  python -m venv .venv
  if errorlevel 1 (
    echo Falha ao criar o ambiente virtual.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo Falha ao instalar dependencias.
    pause
    exit /b 1
  )
)

echo.
echo Iniciando o sistema de financas...
echo Acesse: http://127.0.0.1:5000
echo Para encerrar, feche esta janela ou pressione Ctrl+C.
echo.
".venv\Scripts\python.exe" main.py
pause
