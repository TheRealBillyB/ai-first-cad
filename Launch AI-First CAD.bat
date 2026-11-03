@echo off
setlocal EnableExtensions
title AI-First CAD
pushd "%~dp0"
if errorlevel 1 goto no_folder

set "PYTHONUNBUFFERED=1"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" goto not_installed
if not exist "%~dp0scripts\desktop_launch.py" goto missing_helper

echo.
echo Starting AI-First CAD.
echo This window stays open while the app runs.
echo To stop it, close this window or press Ctrl+C.
echo.

"%PY%" "%~dp0scripts\desktop_launch.py"
set "STATUS=%ERRORLEVEL%"
echo.
if not "%STATUS%"=="0" goto launch_failed
echo AI-First CAD has stopped.
call :pause
exit /b 0

:launch_failed
echo AI-First CAD stopped because of an error.
echo The messages above say what went wrong.
call :pause
exit /b %STATUS%

:not_installed
echo AI-First CAD is not installed in this folder yet.
echo Double-click install.bat and wait until it finishes.
echo Then double-click this file again.
call :pause
exit /b 1

:missing_helper
echo The launcher script is missing. Keep this file in the project folder.
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
