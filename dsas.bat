@echo off
setlocal
set ROOT=%~dp0
set PYTHONPATH=%ROOT%src;%PYTHONPATH%
if "%1"=="install" goto install
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" -m dsm %*
  exit /b %ERRORLEVEL%
)
:install
python -m dsm %*
exit /b %ERRORLEVEL%
