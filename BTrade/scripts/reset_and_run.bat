@echo off
chcp 65001 >nul
cd /d %~dp0..
if not exist db mkdir db

echo ================================================================
echo  RESET DAILY LIMIT + START BOT
echo ================================================================
python main.py --reset-day
set EXIT=%ERRORLEVEL%
echo.
echo [%date% %time%] Bot exited. Exit code: %EXIT%
pause
