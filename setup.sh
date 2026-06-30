#!/bin/bash
set -e

echo "Installing Playwright browser (one-time setup)..."
./myToday --install
if [ $? -ne 0 ]; then
    echo "ERROR: Browser install failed."
    exit 1
fi

echo ""
if [ ! -f config.py ]; then
    echo "WARNING: config.py not found."
    echo "  Copy config.example.py to config.py and fill in your credentials before running myToday."
fi
if [ ! -f feeds.json ]; then
    echo "WARNING: feeds.json not found."
    echo "  Copy feeds.example.json to feeds.json and customize it before running myToday."
fi

echo ""
echo "Setup complete. Run ./myToday to start."
