#!/bin/bash
export WINEPREFIX=/root/.wine
export DISPLAY=:0

Xvfb :0 -screen 0 1024x768x16 &
sleep 2

x11vnc -display :0 -nopw -forever &
sleep 2

websockify --web /usr/share/novnc 8080 localhost:5900 &

wine python /bridge/app.py
