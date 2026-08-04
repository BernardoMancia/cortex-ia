FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV WINEPREFIX=/root/.wine
ENV WINEARCH=win64
ENV DISPLAY=:0

RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    wine wine64 wine32 xvfb curl cabextract \
    x11vnc novnc websockify net-tools \
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Setup Python for Windows (via Wine)
RUN curl -L -o python-installer.exe https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe && \
    xvfb-run -a wine python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 && \
    rm python-installer.exe

# Wait for Wine to initialize
RUN xvfb-run -a wineboot --init

# Install MetaTrader5 and FastAPI in Windows Python
RUN xvfb-run -a wine pip install MetaTrader5 fastapi uvicorn pydantic

COPY bridge /bridge
COPY docker/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 5000 8080

CMD ["/start.sh"]
