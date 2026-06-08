@echo off
REM Deprecated launcher — use rae.bat instead.
setlocal
call "%~dp0rae.bat" %*
exit /b %ERRORLEVEL%
