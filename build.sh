#!/bin/bash
set -e

echo "Cleaning previous build..."
rm -rf build dist myToday.spec .venv

echo "Setting up build environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "Installing build dependencies..."
pip install --quiet pyinstaller
pip install --quiet -r requirements.txt

echo ""
echo "Building myToday..."
python -m PyInstaller --onedir \
  --add-data "calendar.html:." \
  --add-data "Images:Images" \
  --hidden-import msal \
  --collect-all playwright \
  --name myToday \
  server.py

echo ""
echo "Copying runtime files into dist/myToday/..."
if [ -d Images ]; then
    cp -r Images dist/myToday/Images
    echo "Copied Images/"
fi
cp config.example.py  dist/myToday/config.example.py
cp feeds.example.json dist/myToday/feeds.example.json
cp setup.sh           dist/myToday/setup.sh
chmod +x dist/myToday/setup.sh

if [ -f config.py ]; then
    cp config.py dist/myToday/config.py
    echo "Copied config.py"
else
    echo "NOTE: No config.py found — copy config.example.py to dist/myToday/config.py and fill in credentials."
fi

if [ -f feeds.json ]; then
    cp feeds.json dist/myToday/feeds.json
    echo "Copied feeds.json"
else
    echo "NOTE: No feeds.json found — copy feeds.example.json to dist/myToday/feeds.json and customize it."
fi

echo ""
echo "Done. Distribute the dist/myToday/ folder."
