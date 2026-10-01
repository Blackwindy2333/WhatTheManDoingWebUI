@echo off
setlocal EnableExtensions
rem WhatTheManDoing WebUI - config helper (Windows)
rem Usage: config.bat [command...] | config.bat  (interactive menu)

set "SCRIPT_DIR=%~dp0"
set "PY_SCRIPT=%SCRIPT_DIR%config_cli.py"

if not exist "%PY_SCRIPT%" (
  echo error: config_cli.py not found next to this script
  exit /b 1
)

rem Prefer py launcher, then python, then system python from PATH
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo error: Python not found in PATH. Install Python 3.10+ first.
  exit /b 1
)

if "%~1"=="" (
  %PY% "%PY_SCRIPT%"
  exit /b %ERRORLEVEL%
)

%PY% "%PY_SCRIPT%" %*
exit /b %ERRORLEVEL%
