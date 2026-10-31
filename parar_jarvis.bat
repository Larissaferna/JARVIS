@echo off
set "PASTA=%~dp0"

if not exist "%PASTA%jarvis.pid" (
  echo O JARVIS nao parece estar rodando.
  pause
  exit /b 0
)

set /p PID=<"%PASTA%jarvis.pid"
taskkill /PID %PID% /F
del "%PASTA%jarvis.pid"
pause
