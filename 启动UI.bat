@echo off
setlocal
cd /d "%~dp0"

if not exist "..\.conda\python.exe" goto :missing_python
"..\.conda\python.exe" -m streamlit run app.py
goto :end

:missing_python
echo Project Python was not found: ..\.conda\python.exe
echo Read the setup guide in the docs folder for teammate setup steps.

:end
pause
