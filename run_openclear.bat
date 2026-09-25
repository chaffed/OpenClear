@echo off
rem Double-click this file in Windows Explorer to set up and run OpenClear locally.
rem This wrapper does nothing but find a Python interpreter and hand off to the real
rem (OS-agnostic) launcher -- see scripts\launcher.py for everything else.
setlocal
cd /d "%~dp0"

rem Prefer the "py" launcher (installed alongside Python on Windows since 3.3) -- it's
rem the most reliable way to find a real Python 3 install, since a bare "python" on PATH
rem can be Microsoft Store's app-execution-alias stub (which does nothing but open the
rem Store) rather than an actual interpreter. Each command line here is its own
rem top-level statement (not inside a parenthesized block), so plain %ERRORLEVEL%
rem expansion below reflects that line's own exit code, not a value frozen at parse time.
set "PYTHON_CMD="
where py >nul 2>&1 && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD where python >nul 2>&1 && set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    echo Python was not found on your PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/ and try again.
    echo IMPORTANT: check "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

%PYTHON_CMD% scripts\launcher.py
exit /b %ERRORLEVEL%
