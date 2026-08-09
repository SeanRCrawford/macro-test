@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem THE WHOLE PIPELINE, unattended. Run this and go to bed.
rem
rem   overnight.bat
rem   overnight.bat --pool-size 50 --candidates 60 --keep 6 --generations 1-5
rem   overnight.bat --deep-effort exhaustive --keep 3
rem
rem Named arguments, all optional:
rem
rem   --pool-size N      how many Pokemon are ELIGIBLE (the search space).
rem                      Default 34. This is the dial for "find a hidden gem";
rem                      50+ costs a lot more matrix time but looks wider.
rem   --candidates N     how many generated teams get RATED. Default 40.
rem   --keep N           how many go into the deep search. Default 6.
rem   --generations SPEC restrict the pool, e.g. "3" or "1-5" or "1,3,5".
rem   --gen-effort TIER  rating tier for stage 1. Default standard.
rem   --deep-effort TIER rating tier for stage 2. Default thorough.
rem   --jobs N           parallel workers. Default 0 = one per core.
rem   --optimise-sets    optimise each member's item and four moves against
rem                      the actual metagame before rating. Cheap, and it
rem                      changes what is simulated -- strongly recommended.
rem   --script-screen    drop teams with no plan against King / Hard Trick
rem                      Room / Perish Trap before the audit.
rem   --min-winrate F    skip a generated team whose win rate is below this
rem                      before spending the audit on it. Default 0.80.
rem   --pick "4,10,12"   deep-search ONLY these teams, by their stage 1 RANK
rem                      number. The numbering is stable and results
rem                      accumulate, so you can run --pick "6" tonight and
rem                      --pick "4,10,12" tomorrow without redoing #6. The
rem                      workbook is rebuilt each time to include everything
rem                      searched so far. Omit to search the top --keep.
rem   --list             run stage 1 only, print the ranked teams, and stop --
rem                      so you can look before spending the night on stage 2.
rem   --sample-leads     audit only a sample of their most plausible leads
rem                      instead of all 90 of their bring-4s. Much faster, but
rem                      the Plan sheet's record then reads "X / 4" rather than
rem                      "X / 90" -- it is no longer a total-pathing number.
rem                      Auditing all 90 is the DEFAULT because that is the
rem                      question this pipeline exists to answer.
rem
rem Stage 1 generates and rates; stage 2 re-tests the survivors deeply and
rem writes the workbook. Both are cached and resumable -- if this dies, or you
rem Ctrl+C it, just run the SAME command again and it picks up where it left.

set POOL=34
set CANDIDATES=40
set KEEP=6
set GENS=
set GENEFFORT=standard
set DEEPEFFORT=thorough
set JOBS=0
set AUDITALL=--audit-all
set MINWR=0.80
set OPTSETS=
set SCRIPTSCR=
set PICK=
set LISTONLY=

:parse
if "%~1"=="" goto endparse
if /i "%~1"=="--pool-size"    (set POOL=%~2& shift & shift & goto parse)
if /i "%~1"=="--candidates"   (set CANDIDATES=%~2& shift & shift & goto parse)
if /i "%~1"=="--keep"         (set KEEP=%~2& shift & shift & goto parse)
if /i "%~1"=="--generations"  (set GENS=--generations %~2& shift & shift & goto parse)
if /i "%~1"=="--gen-effort"   (set GENEFFORT=%~2& shift & shift & goto parse)
if /i "%~1"=="--deep-effort"  (set DEEPEFFORT=%~2& shift & shift & goto parse)
if /i "%~1"=="--jobs"         (set JOBS=%~2& shift & shift & goto parse)
if /i "%~1"=="--audit-all"    (set AUDITALL=--audit-all& shift & goto parse)
if /i "%~1"=="--sample-leads" (set AUDITALL=& shift & goto parse)
if /i "%~1"=="--min-winrate"  (set MINWR=%~2& shift & shift & goto parse)
if /i "%~1"=="--optimise-sets" (set OPTSETS=--optimise-sets& shift & goto parse)
if /i "%~1"=="--script-screen" (set SCRIPTSCR=--script-screen& shift & goto parse)
if /i "%~1"=="--pick"         (set PICK=--pick "%~2"& shift & shift & goto parse)
if /i "%~1"=="--list"         (set LISTONLY=1& shift & goto parse)
echo Unknown argument: %~1
echo Run with no arguments for defaults, or see the header of this file.
exit /b 1
:endparse

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
echo ============================================================
echo STAGE 1 of 2 -- rating %CANDIDATES% teams from a pool of %POOL%
echo ============================================================
python generate_overnight.py --pool-size %POOL% --candidates %CANDIDATES% ^
    --keep %KEEP% %GENS% --effort %GENEFFORT% --jobs %JOBS% ^
    --min-winrate %MINWR% %OPTSETS% %SCRIPTSCR% ^
    --cache overnight_gen.json --out shortlist.json
if errorlevel 1 (
    echo.
    echo Stage 1 stopped early. Re-run the same command to resume.
    exit /b 1
)

if not exist shortlist.json (
    echo ERROR: shortlist.json was not written -- stage 1 rated nothing.
    exit /b 1
)

if defined LISTONLY (
    echo.
    echo ============================================================
    echo Stage 1 done. The ranked teams are listed above.
    echo Pick the ones worth the deep search, e.g.:
    echo     overnight.bat --pick "6"
    echo     overnight.bat --pick "4,10,12"     ^(later; #6 is not redone^)
    echo ============================================================
    exit /b 0
)

echo.
echo ============================================================
echo STAGE 2 of 2 -- %DEEPEFFORT% search of the shortlisted teams
echo ============================================================
rem No --teams: with a roster file, search_teams defaults OUR side to the
rem generated teams and theirs to the library. Parsing the shortlist here and
rem passing the names back in is what broke stage 2 when the file grew a
rem wrapper for the optimised sets.
python search_teams.py --rosters shortlist.json %PICK% ^
    --effort %DEEPEFFORT% --jobs %JOBS% --batch 4 %AUDITALL% ^
    --sheets shortlist_sheets.json ^
    --cache overnight_%DEEPEFFORT%.json --export
set RC=%ERRORLEVEL%

echo.
if %RC%==0 (
    echo ============================================================
    echo DONE. Open tools\overnight_%DEEPEFFORT%.xlsx
    echo   Plan         THE ANSWER: one committed lead per opponent, X of 90
    echo   Best lines   that committed plan, line by line, with damage
    echo   Lines        EVERY audited line: their bring, result, punish
    echo   Team sheets  what each gen team is: members, items, EVs, moves
    echo   Teams        ranked by Adjusted wins ^(higher is better^)
    echo   Turns        every audited turn, and whether the line won
    echo.
    echo Add more teams later with:  overnight.bat --pick "4,10,12"
    echo Already-searched teams are not redone, and the workbook is rebuilt
    echo to include everything done so far.
    echo ============================================================
) else (
    echo Stage 2 stopped early ^(exit %RC%^). Re-run the same command to resume.
)

endlocal & exit /b %RC%
