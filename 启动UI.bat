@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PROJECT_PYTHON=%PROJECT_DIR%..\.conda\python.exe"

if exist "%PROJECT_PYTHON%" (
    "%PROJECT_PYTHON%" -m streamlit run "%PROJECT_DIR%app.py"
) else (
    echo [提示] 未找到上级目录的 .conda 环境，将使用当前 Python。
    echo [提示] 若启动失败，请先阅读 docs\Spec智能体使用说明.md 的“队友首次安装”部分。
    python -m streamlit run "%PROJECT_DIR%app.py"
)

pause
