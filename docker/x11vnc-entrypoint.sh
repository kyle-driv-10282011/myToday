#!/bin/sh
# Starts x11vnc against the Xvfb display. If VNC_PASSWORD is set, require it;
# otherwise run open (fine for a trusted LAN, matching this project's existing
# unencrypted-LAN security posture elsewhere).
set -e

if [ -n "$VNC_PASSWORD" ]; then
    exec x11vnc -display :99 -rfbport 5900 -forever -shared -passwd "$VNC_PASSWORD" -quiet
else
    exec x11vnc -display :99 -rfbport 5900 -forever -shared -nopw -quiet
fi
