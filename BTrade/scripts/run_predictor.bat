@echo off
cd /d %~dp0..
echo ================================================================
echo  Predictor (B): NN learns price direction from the DOM/VAP.
echo  Separate process, NO real orders. Ctrl+C to stop.
echo ================================================================
:loop
python scripts\run_predictor.py
set EXIT=%ERRORLEVEL%
echo.
echo [%date% %time%] Predictor exit. Code: %EXIT%
if "%EXIT%"=="0" goto end
echo Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
:end
pause