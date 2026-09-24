@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem Launch the Streamlit demo page.
rem
rem Interpreter lookup order:
rem   1) project python  ..\.conda\python.exe
rem   2) virtualenv      .venv\Scripts\python.exe
rem   3) system python
rem   4) py -3 launcher
rem
rem The old version hard-coded ..\.conda\python.exe, which does not exist on
rem most machines, so double-clicking always failed with
rem "Project Python was not found". This version probes for an interpreter and
rem prints the exact install command when streamlit is missing.
rem
rem NOTE: keep this file pure ASCII - cmd.exe may decode it as GBK.
rem ---------------------------------------------------------------------------

set "PY="

if exist "..\.conda\python.exe" (
    set "PY=..\.conda\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)

if not defined PY (
    echo [X] No Python found.
    echo     Install Python 3.9+ with "Add to PATH" checked, then run this again.
    goto :end
)

echo [i] Using Python: %PY%
%PY% -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo [X] streamlit is not installed for this Python.
    echo     Run this first:
    echo         %PY% -m pip install -r requirements.txt
    goto :end
)

echo [i] Starting the page; your browser will open automatically.
echo [i] Close this window to stop the server.
%PY% -m streamlit run app.py

:end
echo.
pause
