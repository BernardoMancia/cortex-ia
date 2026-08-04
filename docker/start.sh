#!/bin/bash
export WINEPREFIX=/root/.wine
export DISPLAY=:0

# Start Xvfb (Virtual Framebuffer for Headless GUI)
Xvfb :0 -screen 0 1024x768x16 &
sleep 2

# Start VNC Server
x11vnc -display :0 -nopw -forever &
sleep 2

# Start noVNC (Web UI for VNC)
websockify --web /usr/share/novnc 8080 localhost:5900 &

# Start MT5 Bridge API (in Wine)
wine python /bridge/app.py
