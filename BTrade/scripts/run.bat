@echo off
chcp 65001 >nul
cd /d %~dp0..
if not exist db mkdir db

echo ================================================================
echo  Trading bot (REAL MONEY on Binance Futures). Auto-restart on crash.
echo  To stop cleanly: press Ctrl+C in the bot window.
echo ================================================================

:loop
echo.
echo [%date% %time%] Starting bot...
python main.py
set EXIT=%ERRORLEVEL%
echo %EXIT% > db\last_exit.txt
echo.
echo [%date% %time%] Bot exited. Exit code: %EXIT%
echo [%date% %time%] Code 0 = clean exit (Ctrl+C). Other = crash or killed process.

if "%EXIT%"=="0" goto end
if "%EXIT%"=="9009" (
    echo ERROR: python not found in PATH.
    goto end
)
echo Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop

:end
pause
