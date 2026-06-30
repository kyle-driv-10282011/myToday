@echo off
echo Installing Playwright browser (one-time setup)...
myToday.exe --install
if errorlevel 1 (
    echo ERROR: Browser install failed.
    pause & exit /b 1
)

echo.
if not exist config.py (
    echo WARNING: config.py not found.
    echo   Copy config.example.py to config.py and fill in your credentials before running myToday.exe.
)
if not exist feeds.json (
    echo WARNING: feeds.json not found.
    echo   Copy feeds.example.json to feeds.json and customize it before running myToday.exe.
)

echo.
echo Setup complete. Run myToday.exe to start.
pause
