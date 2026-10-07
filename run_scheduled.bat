@echo off
rem For Windows Task Scheduler: fetch data, rebuild site, then push to GitHub.
rem Output goes to data\last_run.txt. Push is skipped if git is missing or no remote is set.
cd /d "%~dp0"
if not exist data mkdir data
python update.py > data\last_run.txt 2>&1
call "%~dp0push_site.bat" >> data\last_run.txt 2>&1
