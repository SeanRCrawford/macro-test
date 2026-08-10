@echo off
setlocal
cd /d "%~dp0"

rem Overnight team search. Everything after the script name is passed straight
rem through to tools\search_teams.py, so:
rem
rem   search.bat --effort thorough --batch 2 --cache overnight.json --export
rem   search.bat --effort exhaustive --batch 2 --teams "NAIC" --cache overnight.json --export
rem
rem Re-running the SAME command resumes: finished pairings come out of the
rem cache file instead of being recomputed. Ctrl+C costs you at most one batch,
rem and re-running with --export writes the workbook from whatever is cached,
rem so a half-finished run can be read without waiting for the rest.

rem OpenBLAS sizes its thread pool to the core count in EVERY worker process,
rem which on a many-core machine exhausts memory before the search even starts
rem ("OpenBLAS error: Memory Allocation still failed after 10 retries").
rem Nothing here is numerically parallel, so one BLAS thread per process costs
rem nothing. Set here as well as in Python because these are read when the
rem library loads, and belt-and-braces is cheap.
set OMP_NUM_THREADS=1
set OPENBLAS_NUM_THREADS=1
set MKL_NUM_THREADS=1
set NUMEXPR_NUM_THREADS=1
set VECLIB_MAXIMUM_THREADS=1

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
python search_teams.py %*
set RC=%ERRORLEVEL%

echo.
if %RC%==0 (
    echo Done. Results are in tools\ next to this run's --cache file.
    echo Pass --export to get the .xlsx report alongside it.
) else (
    echo Stopped early ^(exit %RC%^). Re-run the identical command to resume.
)

endlocal & exit /b %RC%
