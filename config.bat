@echo off
setlocal EnableExtensions
rem WhatTheManDoing WebUI config helper (Windows)
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "PY_SCRIPT=%SCRIPT_DIR%config_cli.py"

if not exist "%PY_SCRIPT%" (
  echo 错误：未找到与本脚本同目录的 config_cli.py
  exit /b 1
)

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :have_py
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :have_py
echo 错误：PATH 中未找到 Python，请先安装 Python 3.10 及以上版本。
exit /b 1

:have_py
if "%~1"=="" (
  %PY% "%PY_SCRIPT%"
  exit /b %ERRORLEVEL%
)
%PY% "%PY_SCRIPT%" %*
exit /b %ERRORLEVEL%
