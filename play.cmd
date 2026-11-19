@echo off
rem Windows launcher: runs run.py inside WSL Ubuntu with engines cloned in .local/src
cd /d "%~dp0"
wsl.exe -d Ubuntu --cd "%~dp0" -- env MOLA_DB_SRC=.local/src/jungle-mini-dbms MOLA_REDIS_SRC=.local/src/jungle-mini-redis python3 run.py
pause
