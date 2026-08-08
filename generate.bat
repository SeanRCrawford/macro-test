@echo off
setlocal
cd /d "%~dp0"

rem Stage 1 of the overnight pipeline: generate many teams, rate each by
rem exploitability, write a shortlist the deep search can consume.
rem
rem   generate.bat --candidates 40 --effort standard --jobs 0
rem   generate.bat --candidates 40 --effort standard --jobs 0   (resumes)
rem
rem Every rated team is written to the cache immediately, so Ctrl+C costs at
rem most the team in progress. The pair matrix is cached separately and reused.

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: python not found. Install Python 3.11+ from python.org first
    echo ^(make sure to check "Add python.exe to PATH" during install^).
    exit /b 1
)

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

cd tools
echo.
python generate_overnight.py %*
set RC=%ERRORLEVEL%

echo.
if %RC%==0 (
    echo Done. Shortlist written in tools\ -- feed it to search.bat with --rosters.
) else (
    echo Stopped early ^(exit %RC%^). Re-run the identical command to resume.
)

endlocal & exit /b %RC%
