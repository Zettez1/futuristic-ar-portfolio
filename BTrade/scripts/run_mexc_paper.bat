@echo off
chcp 65001 >nul
cd /d %~dp0..

echo ================================================================
echo  MEXC PAPER bot (simulation). Uses .env.mexc-paper, port 47777.
echo  Binance REAL bot can run separately with its own port.
echo  To stop cleanly: press Ctrl+C in the bot window.
echo ================================================================

set DOTENV=.env.mexc-paper

:loop
echo.
echo [%date% %time%] Starting MEXC paper bot...
python main.py
set EXIT=%ERRORLEVEL%
echo %EXIT% > db\last_exit_mexc.txt
echo.
echo [%date% %time%] Bot exited. Exit code: %EXIT%

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