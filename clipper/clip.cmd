@echo off
rem clip launcher (Windows): runs the clipper inside its own private Python env.
setlocal
set "ENVDIR=%USERPROFILE%\.clipper\venv"
if not exist "%ENVDIR%\Scripts\python.exe" (
  echo Not set up yet. Run:  python "%~dp0scripts\setup.py" 1>&2
  exit /b 1
)
set "PATH=%ENVDIR%\Scripts;%PATH%"
set "PYTHONUTF8=1"
"%ENVDIR%\Scripts\python.exe" "%~dp0scripts\run.py" %*
