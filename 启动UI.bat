@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem Launch the Streamlit demo page for "Analog Circuit AI Design Agent".
rem
rem Interpreter lookup order:
rem   1) project python  ..\.conda\python.exe
rem   2) virtualenv      .venv\Scripts\python.exe
rem   3) system python
rem   4) py -3 launcher
rem
rem Port selection:
rem   8501 is the Streamlit default and is very often already taken by ANOTHER
rem   project's Streamlit app. When that happens the browser silently shows
rem   somebody else's page, which is extremely confusing. So we scan
rem   8501..8520 and use the first free port, then print the exact URL.
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

rem ---- pick a free port ----------------------------------------------------
set "PORT=8501"

:findport
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>nul
if errorlevel 1 goto portok
set /a PORT+=1
if !PORT! GTR 8520 (
    echo [X] No free port in 8501..8520. Close some Streamlit windows first.
    goto :end
)
goto findport

:portok
if not "%PORT%"=="8501" (
    echo [i] Port 8501 is busy (another Streamlit app is using it).
)

echo.
echo [i] Serving on :  http://localhost:%PORT%
echo [i] Opening your browser now; if the page looks empty, refresh once.
echo [i] Close this window (or press Ctrl+C) to stop the server.
echo.

start "" http://localhost:%PORT%
%PY% -m streamlit run app.py --server.port %PORT% --server.headless true

:end
echo.
pause
