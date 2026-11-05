@echo off
setlocal EnableExtensions
title AI-First CAD installer
pushd "%~dp0"
if errorlevel 1 goto no_folder

set "PYTHONUNBUFFERED=1"
set "AICAD_EXPLICIT="
echo.
echo AI-First CAD installer
echo.
echo This installs the app into a .venv folder inside this project.
echo It does not change the rest of this computer unless you agree to install Python.
echo.
echo Step 1 of 2: Looking for Python 3.11 or newer.

if not exist "%~dp0install.py" (
  echo install.py is missing. Keep this installer in the project folder.
  goto fail
)

if defined AICAD_PYTHON goto use_requested
goto search

:use_requested
set "AICAD_REQUESTED=%AICAD_PYTHON%"
set "AICAD_PYTHON="
set "AICAD_EXPLICIT=1"
call :accept_python "%AICAD_REQUESTED%"
if defined AICAD_PYTHON goto have_python
echo The Python in AICAD_PYTHON is missing or older than 3.11:
echo     %AICAD_REQUESTED%
goto fail

:search
set "AICAD_PYTHON="
call :find_python
if defined AICAD_PYTHON goto have_python
goto missing_python

:have_python
echo Using Python:
"%AICAD_PYTHON%" -c "import sys; print(sys.version.split()[0]); print(sys.executable)"
"%AICAD_PYTHON%" -c "import sys; raise SystemExit(0 if sys.maxsize > 2**32 else 1)" >nul 2>&1
if errorlevel 1 goto bit32
"%AICAD_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto not_311
goto install_app

:not_311
echo.
echo Python 3.11 is the easiest choice on Windows. The sketch solver has a ready-made package for 3.11.
echo This installer will continue with the Python above.
echo If installation stops while building the sketch solver, install Python 3.11 and run install.bat again.
goto install_app

:bit32
echo.
echo This Python is 32-bit. AI-First CAD needs 64-bit Python 3.11 or newer.
echo Install the 64-bit version from https://www.python.org/downloads/
goto fail

:install_app
echo.
echo Step 2 of 2: Installing AI-First CAD into a .venv folder in this project.
echo This can take several minutes. The geometry library is large.
echo Leave this window open.
echo.
"%AICAD_PYTHON%" "%~dp0install.py" %*
if errorlevel 1 goto install_failed
echo.
echo Install finished.
echo Next, double-click "Launch AI-First CAD.bat" to open the app.
echo Leave that window open while you use the app. Close it, or press Ctrl+C, to stop.
echo.
echo Running install.bat again is safe. After you update this folder, run it again.
call :pause
exit /b 0

:install_failed
echo.
echo Installation did not finish.
echo The messages above say what went wrong.
echo You can double-click install.bat again. Running it again is safe.
call :pause
exit /b 1

:missing_python
echo.
echo Python 3.11 or newer was not found.
echo AI-First CAD needs Python. It is free.
echo.
echo Download Python 3.11 from:
echo     https://www.python.org/downloads/
echo During setup, turn on "Add python.exe to PATH".
echo Then double-click install.bat again.
echo.
if defined AICAD_EASY_INSTALL_PATH_ONLY goto fail
if defined CI goto fail
if defined GITHUB_ACTIONS goto fail
where winget >nul 2>&1
if errorlevel 1 goto fail
echo This computer has winget, which can install Python 3.11 for you.
choice /C YN /M "Install Python 3.11 now"
if errorlevel 2 goto fail
if errorlevel 1 goto do_winget
goto fail

:do_winget
echo.
echo Installing Python 3.11. This can take a few minutes.
winget install -e --id Python.Python.3.11 --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
echo.
echo Looking for Python again.
set "AICAD_PYTHON="
call :find_python
if defined AICAD_PYTHON goto have_python
echo.
echo Python still was not found.
echo Install Python 3.11 from https://www.python.org/downloads/ and double-click install.bat again.
goto fail

:fail
echo.
echo Installation did not finish.
echo The messages above say what went wrong.
echo You can double-click install.bat again. Running it again is safe.
call :pause
exit /b 1

:no_folder
echo Could not open this project folder.
call :pause
exit /b 1

:pause
if defined CI exit /b 0
if defined GITHUB_ACTIONS exit /b 0
pause
exit /b 0

:find_python
set "AICAD_FALLBACK="
if defined AICAD_EASY_INSTALL_PATH_ONLY goto find_on_path
call :try_py 3.11
call :try_py 3.12
call :try_py 3.13
call :try_py 3.14
call :try_accept "%LocalAppData%\Programs\Python\Python311\python.exe"
call :try_accept "%LocalAppData%\Programs\Python\Python312\python.exe"
call :try_accept "%LocalAppData%\Programs\Python\Python313\python.exe"
call :try_accept "%LocalAppData%\Programs\Python\Python314\python.exe"
call :try_accept "%ProgramFiles%\Python311\python.exe"
call :try_accept "%ProgramFiles%\Python312\python.exe"
call :try_accept "%ProgramFiles%\Python313\python.exe"
call :try_accept "%ProgramFiles%\Python314\python.exe"
:find_on_path
call :try_where python
call :try_where python3
if defined AICAD_PYTHON exit /b 0
if defined AICAD_FALLBACK set "AICAD_PYTHON=%AICAD_FALLBACK%"
exit /b 0

:try_py
if defined AICAD_PYTHON exit /b 0
where py >nul 2>&1
if errorlevel 1 exit /b 0
py -%1 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "AICAD_PY_OUT="
for /f "delims=" %%P in ('py -%1 -c "import sys; print(sys.executable)"') do set "AICAD_PY_OUT=%%P"
if not defined AICAD_PY_OUT exit /b 0
call :accept_python "%AICAD_PY_OUT%"
exit /b 0

:try_where
if defined AICAD_PYTHON exit /b 0
where "%~1" >nul 2>&1
if errorlevel 1 exit /b 0
for /f "delims=" %%P in ('where "%~1" 2^>nul') do call :try_accept "%%P"
exit /b 0

:try_accept
if defined AICAD_PYTHON exit /b 0
call :accept_python "%~1"
exit /b 0

:accept_python
set "AICAD_CANDIDATE=%~1"
if not defined AICAD_CANDIDATE exit /b 1
if not exist "%AICAD_CANDIDATE%" exit /b 1
echo "%AICAD_CANDIDATE%" | findstr /I /C:"\WindowsApps\" >nul
if not errorlevel 1 exit /b 1
call "%AICAD_CANDIDATE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
if defined AICAD_EXPLICIT goto take_python
call "%AICAD_CANDIDATE%" -c "import ensurepip" >nul 2>&1
if not errorlevel 1 goto take_python
if not defined AICAD_FALLBACK set "AICAD_FALLBACK=%AICAD_CANDIDATE%"
exit /b 1
:take_python
set "AICAD_PYTHON=%AICAD_CANDIDATE%"
exit /b 0
