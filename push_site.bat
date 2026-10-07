@echo off
rem Commit today's data and the rebuilt site, then push. Safe to run by hand.
cd /d "%~dp0"
set "PATH=%PATH%;C:\Program Files\Git\cmd"
where git >nul 2>&1 || (echo [push] git not installed, skipped & exit /b 0)
if not exist .git (echo [push] not a git repo yet, skipped & exit /b 0)
git remote get-url origin >nul 2>&1 || (echo [push] no remote "origin", skipped & exit /b 0)
git add -A
git diff --cached --quiet && (echo [push] nothing changed & exit /b 0)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH:mm"') do set D=%%i
git commit -m "update %D%" >nul
git push origin HEAD
if errorlevel 1 (echo [push] push FAILED) else (echo [push] pushed)
