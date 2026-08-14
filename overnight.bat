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
rem   --worst-matchup F  reject a team whose WORST single matchup wins less
rem                      than F of that opponent's 90 brings, e.g. 0.89 for
rem                      80/90. Checked one opponent at a time and abandoned on
rem                      the first failure, so a team with a hole costs one
rem                      matchup instead of eight.
rem   --punish-screen    drop a team whose OPENING is already lost before
rem                      spending the audit on it. Solves turn 1 as a matrix
rem                      game -- seconds per team against minutes for the
rem                      audit -- so the night is spent on plausible teams.
rem                      Optionally takes a floor: --punish-screen -200 is
rem                      stricter than the default -250.
rem   --pilot NAME       WHO PLAYS THE GAMES the record comes from.
rem                      "greedy" (default) plays our side with the real solver
rem                      and theirs with a fixed policy, which hands whichever
rem                      side is p1 a systematic advantage -- 78%% of mirrored
rem                      matchups flip (tools\measure_side_bias.py).
rem                      "equilibrium" plays both sides as a matrix game, so a
rem                      safe play is worth what it is worth and a read is
rem                      charged for being a read. ~13x slower per game.
rem                      --gen-effort thorough+ turns it on for stage 1.
rem   --evaluation NAME  "sacrifice" (default) or "legacy". Sacrifice scores
rem                      speed control and discounts a Pokemon that is one hit
rem                      from gone, so the solver sacrifices instead of
rem                      preserving something it cannot use. Measured cost:
rem                      record 80%% -> 74%%. "legacy" restores the previous
rem                      evaluation exactly.
rem   --min-winrate F    skip a generated team whose win rate is below this
rem                      before spending the audit on it. Default 0.80.
rem   --vs "Big 6"       deep-search against ONLY these opponents instead of
rem                      the whole library. THE biggest lever: 1 opponent
rem                      instead of 7 is 7x less work. Comma-separated.
rem   --brings N         audit only the N best of OUR brings per pairing
rem                      instead of the tier's default (6 at thorough). The
rem                      cheap screen has already ranked them, so this keeps
rem                      the most promising few. Cost is linear in N.
rem   --substitute N     after stage 1, take the top N rated teams and try
rem                      swapping their worst member for a better one, keeping
rem                      a swap only if the RATING improves. This is the only
rem                      stage that steers the search by the number the teams
rem                      are judged on -- the beam ranks on coverage/synergy.
rem                      Costs --sub-per-round full team ratings per round, per
rem                      team; stage 2 then searches the improved rosters.
rem   --sub-rounds N     how many swaps to attempt per team. Default 2.
rem   --sub-per-round N  candidates rated per round. Default 4.
rem   --pick "4,10,12"   deep-search ONLY these teams, by their stage 1 RANK
rem                      number. The numbering is stable and results
rem                      accumulate, so you can run --pick "6" tonight and
rem                      --pick "4,10,12" tomorrow without redoing #6. The
rem                      workbook is rebuilt each time to include everything
rem                      searched so far. Omit to search the top --keep.
rem   --list             run stage 1 only, print the ranked teams, and stop --
rem                      so you can look before spending the night on stage 2.
rem   --stage2-only      SKIP stage 1 entirely and deep-search the shortlist
rem                      that is already on disk. This is the flag for coming
rem                      back later: pair it with --pick to spend the night on
rem                      specific teams.
rem                          overnight.bat --list                 (last night)
rem                          overnight.bat --stage2-only --pick "5" --vs "Big 6"
rem                      WITHOUT it, stage 1 runs again -- and if you do not
rem                      repeat the SAME --pool-size / --candidates /
rem                      --generations you used the first time, it generates a
rem                      DIFFERENT set of teams, renumbers the shortlist, and
rem                      --pick "5" then means a different team. Stage 1 warns
rem                      when it notices that.
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
set PUNISHSCR=
set PILOT=
set EVALN=
set WORSTMU=
set PICK=
set LISTONLY=
set STAGE2ONLY=
set GENFLAGS=
set REGEN=
set VS=
set BRINGS=
set SUBST=
set SUBROUNDS=2
set SUBPER=4
set ROSTERS=shortlist.json
set SHEETS=shortlist_sheets.json

:parse
if "%~1"=="" goto endparse
rem GENFLAGS records "this command asked for GENERATION", which is what makes
rem `--pick "5"` on its own mean "deep-search what I already have" rather than
rem "generate a fresh 40 teams and then pick the fifth of THOSE".
if /i "%~1"=="--pool-size"    (set POOL=%~2& set GENFLAGS=1& shift & shift & goto parse)
if /i "%~1"=="--candidates"   (set CANDIDATES=%~2& set GENFLAGS=1& shift & shift & goto parse)
if /i "%~1"=="--keep"         (set KEEP=%~2& shift & shift & goto parse)
if /i "%~1"=="--generations"  (set GENS=--generations %~2& set GENFLAGS=1& shift & shift & goto parse)
if /i "%~1"=="--gen-effort"   (set GENEFFORT=%~2& set GENFLAGS=1& shift & shift & goto parse)
if /i "%~1"=="--regenerate"   (set REGEN=1& shift & goto parse)
if /i "%~1"=="--deep-effort"  (set DEEPEFFORT=%~2& shift & shift & goto parse)
if /i "%~1"=="--jobs"         (set JOBS=%~2& shift & shift & goto parse)
if /i "%~1"=="--audit-all"    (set AUDITALL=--audit-all& shift & goto parse)
if /i "%~1"=="--sample-leads" (set AUDITALL=& shift & goto parse)
if /i "%~1"=="--min-winrate"  (set MINWR=%~2& shift & shift & goto parse)
if /i "%~1"=="--optimise-sets" (set OPTSETS=--optimise-sets& set GENFLAGS=1& shift & goto parse)
if /i "%~1"=="--script-screen" (set SCRIPTSCR=--script-screen& set GENFLAGS=1& shift & goto parse)
if /i "%~1"=="--worst-matchup" (set WORSTMU=--worst-matchup %~2& set GENFLAGS=1& shift & shift & goto parse)
if /i "%~1"=="--punish-screen" (set PUNISHSCR=--punish-screen& set GENFLAGS=1& shift & goto parse)
rem WHO PLAYS THE GAMES, and WHICH EVALUATION. Both change the answer, so both
rem reach stage 1 AND stage 2 -- a record played by one pilot and deep-searched
rem by the other is two different numbers under one heading.
if /i "%~1"=="--pilot"         (set PILOT=--pilot %~2& set GENFLAGS=1& shift & shift & goto parse)
if /i "%~1"=="--evaluation"    (set EVALN=--evaluation %~2& set GENFLAGS=1& shift & shift & goto parse)
if /i "%~1"=="--punish-floor"  (set PUNISHSCR=--punish-screen %~2& set GENFLAGS=1& shift & shift & goto parse)
if /i "%~1"=="--pick"         (set PICK=--pick "%~2"& shift & shift & goto parse)
if /i "%~1"=="--list"         (set LISTONLY=1& shift & goto parse)
if /i "%~1"=="--stage2-only"  (set STAGE2ONLY=1& shift & goto parse)
if /i "%~1"=="--stage2"       (set STAGE2ONLY=1& shift & goto parse)
if /i "%~1"=="--skip-stage1"  (set STAGE2ONLY=1& shift & goto parse)
if /i "%~1"=="--vs"           (set VS=--vs "%~2"& shift & shift & goto parse)
if /i "%~1"=="--brings"       (set BRINGS=--brings %~2& shift & shift & goto parse)
if /i "%~1"=="--substitute"   (set SUBST=%~2& shift & shift & goto parse)
if /i "%~1"=="--sub-rounds"   (set SUBROUNDS=%~2& shift & shift & goto parse)
if /i "%~1"=="--sub-per-round" (set SUBPER=%~2& shift & shift & goto parse)
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

rem `--pick N` refers to a numbering a PREVIOUS stage 1 produced. If this
rem command asked for no generation of its own, then re-generating is never
rem what was meant -- it would renumber the very thing being picked. Measured
rem on the real dataset: pool 50/60 and the default 34/40 share ZERO of their
rem top five teams, so "--pick 5" after dropping the flags is a team you have
rem never seen.
if defined PICK if not defined GENFLAGS if not defined REGEN if not defined LISTONLY (
    if exist shortlist.json set STAGE2ONLY=1
)

if defined STAGE2ONLY (
    if not exist shortlist.json (
        echo ERROR: --stage2-only needs a shortlist.json from an earlier run,
        echo and there is none in %CD%. Run stage 1 first, e.g.
        echo     overnight.bat --pool-size 50 --candidates 60 --optimise-sets --list
        exit /b 1
    )
    echo.
    echo ============================================================
    echo STAGE 1 SKIPPED -- deep-searching the shortlist already on disk
    echo ============================================================
    echo Using shortlist.json as written by the last stage 1 run, so --pick
    echo numbers mean exactly what they meant then.
    echo ^(Pass --regenerate if you actually wanted to build new teams.^)
    goto stage2
)

echo.
echo ============================================================
echo STAGE 1 of 2 -- rating %CANDIDATES% teams from a pool of %POOL%
echo ============================================================
python generate_overnight.py --pool-size %POOL% --candidates %CANDIDATES% ^
    --keep %KEEP% %GENS% --effort %GENEFFORT% --jobs %JOBS% ^
    --min-winrate %MINWR% %OPTSETS% %SCRIPTSCR% %PUNISHSCR% %WORSTMU% ^
    %PILOT% %EVALN% ^
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
    echo Pick the ones worth the deep search. Use --stage2-only, or stage 1
    echo runs all over again:
    echo     overnight.bat --stage2-only --pick "6"
    echo     overnight.bat --stage2-only --pick "4,10,12"   ^(#6 is not redone^)
    echo     overnight.bat --stage2-only --pick "6" --vs "Big 6"
    echo ============================================================
    exit /b 0
)

if defined SUBST (
    echo.
    echo ============================================================
    echo STAGE 1b -- swapping the worst member of the top %SUBST% team^(s^)
    echo ============================================================
    rem Hill climbing on the rating itself: propose a swap with the cheap
    rem screener, then rate the candidate exactly the way stage 1 rates a team
    rem and keep it only if the rating improves. Shares stage 1's cache, so a
    rem roster either stage has already rated is never rated twice.
    if exist substituted.json del substituted.json
    for /L %%i in (1,1,%SUBST%) do (
        echo.
        echo --- team %%i ---
        python substitute.py --rosters shortlist.json --team %%i --append ^
            --rounds %SUBROUNDS% --per-round %SUBPER% --pool-size %POOL% ^
            %GENS% --effort %GENEFFORT% --jobs %JOBS% %OPTSETS% %PILOT% %EVALN% ^
            --cache overnight_gen.json --out substituted.json
        if errorlevel 1 (
            echo.
            echo Stage 1b stopped early. Re-run the same command to resume.
            exit /b 1
        )
    )
    if exist substituted.json (
        set ROSTERS=substituted.json
        set SHEETS=substituted_sheets.json
    )
)

:stage2
echo.
echo ============================================================
echo STAGE 2 of 2 -- %DEEPEFFORT% search of !ROSTERS!
echo ============================================================
rem No --teams: with a roster file, search_teams defaults OUR side to the
rem generated teams and theirs to the library. Parsing the shortlist here and
rem passing the names back in is what broke stage 2 when the file grew a
rem wrapper for the optimised sets.
python search_teams.py --rosters !ROSTERS! %PICK% %VS% %BRINGS% ^
    --effort %DEEPEFFORT% --jobs %JOBS% --batch 4 %AUDITALL% %PILOT% %EVALN% ^
    --sheets !SHEETS! ^
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
