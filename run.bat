@echo off
setlocal
cd /d "%~dp0"

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

rem First argument selects the tool:
rem   app       -^> interactive Streamlit UI (pick teams, run searches, view battles)
rem   generate  -^> build an optimal 6-mon team from the whole pool
rem   (default) -^> arrange a team you specify into the best lead/back per opponent

set CMD=%1
if "%CMD%"=="" goto search
if "%CMD%"=="app" goto app
if "%CMD%"=="generate" goto generate
goto search

:app
shift
echo.
echo Launching Streamlit app -- it should open in your browser.
echo If it doesn't, use the Local URL printed below. Ctrl+C to stop.
echo.
python -m streamlit run src\app.py %1 %2 %3 %4 %5 %6 %7 %8 %9
goto end

:generate
shift
echo.
echo Running team GENERATION...
echo.
python src\generate_team.py %1 %2 %3 %4 %5 %6 %7 %8 %9
goto end

:search
echo.
echo Running lead/back SEARCH...
echo.
python src\run_search.py %1 %2 %3 %4 %5 %6 %7 %8 %9
goto end

:end
endlocal
