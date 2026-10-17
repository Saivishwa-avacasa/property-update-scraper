@echo off
REM Daily scrape for one state. Called by Task Scheduler (through run_hidden.vbs):
REM     run_daily.cmd TN            99acres, normal day (all list pages, 60 project pages)
REM     run_daily.cmd all full      99acres full run: every state, every due project
REM     run_daily.cmd mb TN         Magicbricks MagicHomes, normal day (300 project pages)
REM     run_daily.cmd mb all full   Magicbricks full run
REM Pulls the latest code first (if this folder is a git checkout), then runs
REM the scraper. Each source/state logs to its own file:
REM     data\scheduler_<STATE>.log  and  data\scheduler_mb_<STATE>.log
REM (two tasks writing to one shared log is what silently killed MH and UK runs
REM when catch-up started two tasks in the same second).
setlocal
cd /d "%~dp0"
if not exist data mkdir data

set SCRIPT=scrape_99acres.py
set PREFIX=
if /i "%1"=="mb" (
  set SCRIPT=scrape_magicbricks.py
  set PREFIX=mb_
)
if /i "%1"=="mb" shift

set STATE=%1
if "%STATE%"=="" set STATE=all
set EXTRA=
if /i "%2"=="full" set EXTRA=--full
set LOG=data\scheduler_%PREFIX%%STATE%.log

echo ==== %date% %time% start %SCRIPT% %STATE% %EXTRA% >> %LOG%
if exist .git (
  git pull --ff-only >> %LOG% 2>&1
)

set PY=.venv\Scripts\python.exe
if not exist %PY% set PY=python

set PYTHONUTF8=1
%PY% %SCRIPT% --state %STATE% %EXTRA% >> %LOG% 2>&1
set CODE=%ERRORLEVEL%
echo ==== %date% %time% end %SCRIPT% %STATE% exit %CODE% >> %LOG%
exit /b %CODE%
