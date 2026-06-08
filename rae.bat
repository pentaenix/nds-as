@echo off
setlocal
set ROOT=%~dp0
set PYTHONPATH=%ROOT%src;%PYTHONPATH%
if "%1"=="install" goto install
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" -m rae %*
  exit /b %ERRORLEVEL%
)
:install
python -m rae %*
exit /b %ERRORLEVEL%
