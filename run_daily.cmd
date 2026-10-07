@echo off
REM Daily 99acres New Launch scrape for one state. Called by Task Scheduler:
REM     run_daily.cmd TN
REM Pulls the latest code first (if this folder is a git checkout), then runs
REM the scraper with the default limits (all listing pages, 60 detail pages).
setlocal
cd /d "%~dp0"
if not exist data mkdir data

set STATE=%1
if "%STATE%"=="" set STATE=all

echo ==== %date% %time% start %STATE% >> data\scheduler.log
if exist .git (
  git pull --ff-only >> data\scheduler.log 2>&1
)

set PY=.venv\Scripts\python.exe
if not exist %PY% set PY=python

set PYTHONUTF8=1
%PY% scrape_99acres.py --state %STATE% >> data\scheduler.log 2>&1
set CODE=%ERRORLEVEL%
echo ==== %date% %time% end %STATE% exit %CODE% >> data\scheduler.log
exit /b %CODE%
