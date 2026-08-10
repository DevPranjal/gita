@echo off
set "GITA_REAL_GIT=C:\Program Files\Git\cmd\git.EXE"
"Q:\projects\gita\.venv\Scripts\python.exe" -m gita.telemetry.shim %*
