@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem Launch the Streamlit demo page for "Analog Circuit AI Design Agent".
rem
rem Interpreter selection:
rem   Having a "python" on PATH is NOT enough - it must be a Python that can
rem   actually "import streamlit". On this machine "where python" resolves first
rem   to a managed 3.13 build WITHOUT streamlit, while the usable one is
rem   Python314 reached through "py -3". So each candidate is probed with
rem   "import streamlit" and the first one that passes wins.
rem
rem   Probe order:
rem     1) ..\.conda\python.exe            2) .venv\Scripts\python.exe
rem     3) python (PATH)                   4) py -3 (launcher)
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

if not defined PY if exist "..\.conda\python.exe" (
    "..\.conda\python.exe" -c "import streamlit" >nul 2>nul
    if not errorlevel 1 set "PY=..\.conda\python.exe"
)

if not defined PY if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import streamlit" >nul 2>nul
    if not errorlevel 1 set "PY=.venv\Scripts\python.exe"
)

if not defined PY (
    python -c "import streamlit" >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    py -3 -c "import streamlit" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)

if not defined PY goto no_streamlit

echo [i] Using Python: %PY%

rem ---- pick a free port ----------------------------------------------------
set "PORT=8501"

:findport
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>nul
if errorlevel 1 goto portok
set /a PORT+=1
if !PORT! GTR 8520 goto no_port
goto findport

:portok
if not "%PORT%"=="8501" echo [i] Port 8501 is busy (another Streamlit app is using it).

echo.
echo [i] Serving on :  http://localhost:%PORT%
echo [i] Opening your browser now; if the page looks empty, refresh once.
echo [i] Close this window (or press Ctrl+C) to stop the server.
echo.

start "" http://localhost:%PORT%
%PY% -m streamlit run app.py --server.port %PORT% --server.headless true
goto :end

:no_streamlit
echo [X] No Python with streamlit found.
echo     Tried in order:
echo         ..\.conda\python.exe
echo         .venv\Scripts\python.exe
echo         python
echo         py -3
echo.
echo     Install streamlit for the interpreter you want to use, e.g.
echo         py -3 -m pip install -r requirements.txt
echo     or
echo         python -m pip install -r requirements.txt
echo.
echo     Tip: check which Python actually has it:
echo         py -3 -c "import streamlit; print('ok')"
goto :end

:no_port
echo [X] No free port in 8501..8520. Close some Streamlit windows first.

:end
echo.
pause
