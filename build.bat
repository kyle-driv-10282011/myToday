@echo off
echo Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist myToday.spec del myToday.spec

echo Installing build dependencies...
python -m pip install pyinstaller
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause & exit /b 1
)

echo.
echo Building myToday...
python -m PyInstaller --onedir ^
  --add-data "calendar.html;." ^
  --add-data "Images;Images" ^
  --hidden-import msal ^
  --collect-all playwright ^
  --name myToday ^
  server.py
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    pause & exit /b 1
)

echo.
echo Copying runtime files into dist\myToday\...
if exist Images (
    xcopy /E /I /Q Images dist\myToday\Images > nul
    echo Copied Images\
)
copy config.example.py  dist\myToday\config.example.py
copy feeds.example.json dist\myToday\feeds.example.json
copy setup.bat          dist\myToday\setup.bat
if exist config.py (
    copy config.py dist\myToday\config.py
    echo Copied config.py
) else (
    echo NOTE: No config.py found — copy config.example.py to dist\myToday\config.py and fill in credentials.
)
if exist feeds.json (
    copy feeds.json dist\myToday\feeds.json
    echo Copied feeds.json
) else (
    echo NOTE: No feeds.json found — copy feeds.example.json to dist\myToday\feeds.json and customize it.
)

echo.
echo Done. Distribute the dist\myToday\ folder.
pause
