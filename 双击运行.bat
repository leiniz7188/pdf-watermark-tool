@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "WM_EXIT=1"
pushd "%~dp0"
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "WM_PYTHON=py -3"
    goto python_ready
)
python -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "WM_PYTHON=python"
    goto python_ready
)
echo Python 3 was not found. Please install Python 3, then run this file again.
goto finish

:python_ready
%WM_PYTHON% -c "import fitz, PIL, tkinter" >nul 2>nul
if not errorlevel 1 goto dependencies_ready
echo Installing PDF watermark dependencies. An internet connection is required for this first setup.
%WM_PYTHON% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto finish

:dependencies_ready
%WM_PYTHON% "%~dp0pdf_watermark.py" %*
set "WM_EXIT=%ERRORLEVEL%"

:finish
echo.
if not defined PDF_WATERMARK_NO_PAUSE pause
popd
exit /b %WM_EXIT%
