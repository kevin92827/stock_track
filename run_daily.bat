@echo off
rem Manual update: fetch latest holdings, rebuild site, push to GitHub.
cd /d "%~dp0"
python update.py
call push_site.bat
pause
