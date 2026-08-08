@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem THE WHOLE PIPELINE, unattended. Run this and go to bed.
rem
rem   overnight.bat              defaults: 40 candidates, 8 shortlisted
rem   overnight.bat 60 10        60 candidates, top 10 into the deep search
rem
rem Stage 1  generate many teams and rate each by exploitability (standard)
rem Stage 2  take the top few and re-test them deeply against every library
rem          team (thorough), then export the workbook
rem
rem Both stages are resumable and cached. Re-running after a crash, a reboot,
rem or Ctrl+C skips everything already finished, so this is safe to just run
rem again in the morning if it did not get through.

set CANDIDATES=%~1
if "%CANDIDATES%"=="" set CANDIDATES=40
set KEEP=%~2
if "%KEEP%"=="" set KEEP=8

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
echo ============================================================
echo STAGE 1 of 2 -- generating and rating %CANDIDATES% teams
echo ============================================================
python generate_overnight.py --candidates %CANDIDATES% --keep %KEEP% ^
    --effort standard --jobs 0 --cache overnight_gen.json ^
    --out shortlist.json
if errorlevel 1 (
    echo.
    echo Stage 1 stopped early. Re-run overnight.bat to resume from the cache.
    exit /b 1
)

rem The shortlist names its teams gen01..genNN. Build the comma-separated list
rem the deep search wants, limited to however many were actually kept.
set TEAMLIST=
for /f "usebackq delims=" %%N in (`python -c "import json;print(','.join(json.load(open('shortlist.json'))))"`) do set TEAMLIST=%%N
if "!TEAMLIST!"=="" (
    echo ERROR: shortlist.json is empty -- stage 1 rated nothing.
    exit /b 1
)

echo.
echo ============================================================
echo STAGE 2 of 2 -- deep search for: !TEAMLIST!
echo ============================================================
python search_teams.py --rosters shortlist.json --teams "!TEAMLIST!" ^
    --effort thorough --jobs 0 --batch 4 --cache overnight.json --export
set RC=%ERRORLEVEL%

echo.
if %RC%==0 (
    echo ============================================================
    echo DONE. Open tools\overnight.xlsx
    echo   Teams sheet      which shortlisted team is least punishable
    echo   Turns sheet      the exact turns a good player would punish
    echo Read exploitability next to the win count -- a LOST position
    echo also rates as unpunishable. See docs\WORKFLOW.md.
    echo ============================================================
) else (
    echo Stage 2 stopped early ^(exit %RC%^). Re-run overnight.bat to resume.
)

endlocal & exit /b %RC%
