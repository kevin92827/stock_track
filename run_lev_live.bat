@echo off
rem For Windows Task Scheduler (weekdays 13:31): estimate leveraged ETF close rebalance from live TX price, rebuild site, push.
rem Output goes to data\lev_live_log.txt.
cd /d "%~dp0"
python lev_live.py > data\lev_live_log.txt 2>&1
call "%~dp0push_site.bat" >> data\lev_live_log.txt 2>&1
