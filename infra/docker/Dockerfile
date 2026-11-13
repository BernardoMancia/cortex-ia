FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/wine/bin:${PATH}"
ENV WINEPREFIX=/root/.wine
ENV WINEARCH=win64
ENV DISPLAY=:0
ENV WINEDEBUG=-all
ENV WINEDLLOVERRIDES="mscoree,mshtml="
ENV PYTHONPATH=/app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        xz-utils \
        python3 \
        python3-venv \
        python3-pip \
        xvfb \
        openbox \
        cabextract \
        winbind \
        gnutls-bin \
        libgnutls30t64 \
        libfreetype6 \
        libfontconfig1 \
        libx11-6 \
        libxext6 \
        libxrender1 \
        libxrandr2 \
        libxi6 \
        libxcursor1 \
        libxcomposite1 \
        libxfixes3 \
        libxinerama1 \
        libxkbcommon0 \
        libgl1 \
        libegl1 \
        libasound2t64 \
        libpulse0 \
        libdbus-1-3 \
        x11vnc \
        novnc \
        websockify \
        xdotool \
        x11-utils \
        procps \
        net-tools \
        tzdata && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/bridge-venv && \
    /opt/bridge-venv/bin/pip install --no-cache-dir \
        fastapi==0.115.6 \
        uvicorn==0.32.1 \
        pydantic==2.10.3 \
        python-dotenv==1.0.1

RUN mkdir -p /opt/wine && \
    curl -fsSL -o /tmp/wine.tar.xz \
        "https://github.com/Kron4ek/Wine-Builds/releases/download/11.21/wine-11.21-staging-tkg-amd64-wow64.tar.xz" && \
    tar -xJf /tmp/wine.tar.xz -C /opt/wine --strip-components=1 && \
    rm -f /tmp/wine.tar.xz && \
    wine --version

RUN sh -c 'Xvfb :99 -screen 0 1024x768x24 -ac >/dev/null 2>&1 </dev/null & \
    sleep 1 && \
    DISPLAY=:99 wineboot --init >/dev/null 2>&1 && \
    DISPLAY=:99 wine reg add "HKCU\\Software\\Wine" /v Version /t REG_SZ /d win10 /f >/dev/null 2>&1 && \
    DISPLAY=:99 wine reg add "HKCU\\Software\\Wine\\WineDbg" /v ShowCrashDialog /t REG_DWORD /d 0 /f >/dev/null 2>&1 && \
    (DISPLAY=:99 wine reg delete "HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AeDebug" /f >/dev/null 2>&1 || true) && \
    wineserver -k >/dev/null 2>&1 || true; \
    pkill Xvfb >/dev/null 2>&1 || true'

RUN mkdir -p /opt/mt5 && \
    (curl -fsSL -A "Mozilla/5.0" -o /opt/mt5/clearsetup.exe \
        "https://download.mql5.com/cdn/web/xp.investimentos.cctvm/mt5/clearinvestimentos5setup.exe" || true) && \
    curl -fsSL -A "Mozilla/5.0" -o /opt/mt5/mt5setup.exe \
        "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"

COPY infra/docker/servers.dat /opt/mt5/servers.dat
COPY infra/docker/CortexBridgeEA.mq5 /opt/mt5/CortexBridgeEA.mq5
COPY config /app/config
COPY domain /app/domain
COPY infra /app/infra
COPY infra/docker/start.sh /start.sh
RUN sed -i 's/\r$//' /start.sh && chmod +x /start.sh

EXPOSE 5000 6080

CMD ["/start.sh"]
