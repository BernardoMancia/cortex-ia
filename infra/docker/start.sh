#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/wine/bin:${PATH}"
export WINEPREFIX=/root/.wine
export WINEARCH=win64
export DISPLAY=:0
export WINEDEBUG=-all
export WINEDLLOVERRIDES="mscoree,mshtml="
export MT5_PATH='C:\Program Files\MetaTrader 5\terminal64.exe'
export BRIDGE_HOST="${BRIDGE_HOST:-0.0.0.0}"
export BRIDGE_PORT="${BRIDGE_PORT:-5000}"

MT5_DIR="/root/.wine/drive_c/Program Files/MetaTrader 5"
MT5_EXE="${MT5_DIR}/terminal64.exe"
MT5_APPDATA_ROOT="/root/.wine/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal"
MT5_HASH_DIR="${MT5_APPDATA_ROOT}/D0E8209F77C8CF37AD8BF550E51FF075"
MT5_COMMON_FILES="${MT5_APPDATA_ROOT}/Common/Files"
export CORTEX_MQL5_RPC_DIR="${MT5_COMMON_FILES}/cortex_rpc"

SUPERVISOR_PID=""
BRIDGE_PID=""

cleanup_container() {
    echo "[MT5-BRIDGE] Sinal de encerramento recebido — finalizando FastAPI Bridge e Wine/MT5..."
    if [ -n "${SUPERVISOR_PID}" ]; then
        kill "${SUPERVISOR_PID}" >/dev/null 2>&1 || true
    fi
    if [ -n "${BRIDGE_PID}" ]; then
        kill -TERM "${BRIDGE_PID}" >/dev/null 2>&1 || true
    fi
    pkill -f '[t]erminal64\.exe' >/dev/null 2>&1 || true
    wineserver -k >/dev/null 2>&1 || true
    if [ -n "${BRIDGE_PID}" ]; then
        wait "${BRIDGE_PID}" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup_container TERM INT

mkdir -p "${CORTEX_MQL5_RPC_DIR}" "${MT5_APPDATA_ROOT}"
find "${CORTEX_MQL5_RPC_DIR}" -maxdepth 1 -type f \( -name "req_*.cmd" -o -name "resp_*.json" -o -name "*.tmp" \) -delete || true

rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 || true
Xvfb :0 -screen 0 1280x800x24 -ac >/dev/null 2>&1 &
sleep 2
openbox >/dev/null 2>&1 &
sleep 1

wine reg add "HKCU\\Software\\Wine" /v Version /t REG_SZ /d win10 /f >/dev/null 2>&1 || true
wine reg add "HKCU\\Software\\Wine\\WineDbg" /v ShowCrashDialog /t REG_DWORD /d 0 /f >/dev/null 2>&1 || true
wine reg delete "HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AeDebug" /f >/dev/null 2>&1 || true

if [ -n "${VNC_PASSWORD:-}" ]; then
    mkdir -p /root/.vnc
    x11vnc -storepasswd "${VNC_PASSWORD}" /root/.vnc/passwd
    chmod 600 /root/.vnc/passwd
    x11vnc -display :0 -rfbauth /root/.vnc/passwd -localhost -forever -shared >/tmp/x11vnc.log 2>&1 &
    sleep 1
    websockify --web /usr/share/novnc 6080 localhost:5900 >/tmp/novnc.log 2>&1 &
    echo "[MT5-BRIDGE] Painel diagnóstico noVNC ativo em loopback (porta 6080)."
else
    echo "[SECURITY] VNC_PASSWORD não definida — x11vnc/noVNC desativados por segurança."
fi

find_installed_terminal() {
    find "/root/.wine/drive_c/Program Files" "/root/.wine/drive_c/Program Files (x86)" \
        -maxdepth 3 -iname "terminal64.exe" 2>/dev/null | head -n 1 || true
}

run_installer_attempt() {
    local src_installer="$1"
    local label="$2"
    if [ ! -f "${src_installer}" ]; then
        return 1
    fi
    echo "[MT5-BRIDGE] Executando instalador ${label} em C:\\mt5setup.exe..."
    cp -f "${src_installer}" "/root/.wine/drive_c/mt5setup.exe"
    wine 'C:\mt5setup.exe' /auto >/tmp/mt5_install.log 2>&1 &

    local elapsed=0
    local found_exe=""
    while [ "${elapsed}" -lt 210 ]; do
        sleep 5
        elapsed=$((elapsed + 5))
        found_exe="$(find_installed_terminal)"
        if [ -n "${found_exe}" ]; then
            break
        fi
        if [ "${elapsed}" -eq 20 ] || [ "${elapsed}" -eq 45 ]; then
            xdotool key Return >/dev/null 2>&1 || true
        fi
    done

    rm -f "/root/.wine/drive_c/mt5setup.exe" || true

    if [ -n "${found_exe}" ]; then
        echo "[MT5-BRIDGE] Binário detectado em '${found_exe}' após ${elapsed}s — aguardando estabilização..."
        sleep 15
        pkill -f '[t]erminal64\.exe' >/dev/null 2>&1 || true
        pkill -f '[m]t5setup\.exe' >/dev/null 2>&1 || true
        sleep 3
        if [ "${found_exe}" != "${MT5_EXE}" ]; then
            mkdir -p "${MT5_DIR}"
            cp -a "$(dirname "${found_exe}")/." "${MT5_DIR}/"
            echo "[MT5-BRIDGE] Binários e servidores sincronizados para o volume persistente '${MT5_DIR}'."
        fi
        return 0
    fi

    pkill -f '[m]t5setup\.exe' >/dev/null 2>&1 || true
    return 1
}

sync_mt5_appdata_profiles() {
    mkdir -p "${MT5_HASH_DIR}/config" "${MT5_HASH_DIR}/MQL5/Experts"
    for prof_dir in "${MT5_APPDATA_ROOT}"/*; do
        [ -d "${prof_dir}" ] || continue
        local base_prof
        base_prof="$(basename "${prof_dir}")"
        if [ "${base_prof}" = "Common" ] || [ "${base_prof}" = "Community" ]; then
            continue
        fi
        if [ -f "${MT5_DIR}/Config/servers.dat" ]; then
            mkdir -p "${prof_dir}/config"
            if [ ! -f "${prof_dir}/config/servers.dat" ] || [ "$(stat -c%s "${MT5_DIR}/Config/servers.dat")" -gt "$(stat -c%s "${prof_dir}/config/servers.dat")" ]; then
                cp -f "${MT5_DIR}/Config/servers.dat" "${prof_dir}/config/servers.dat" || true
            fi
        fi
        if [ -f "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5" ]; then
            mkdir -p "${prof_dir}/MQL5/Experts"
            cp -f "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5" "${prof_dir}/MQL5/Experts/CortexBridgeEA.ex5" || true
            if [ -f "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.mq5" ]; then
                cp -f "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.mq5" "${prof_dir}/MQL5/Experts/CortexBridgeEA.mq5" || true
            fi
        fi
    done
}

if [ ! -f "${MT5_EXE}" ]; then
    echo "[MT5-BRIDGE] MetaTrader 5 não encontrado no volume. Iniciando instalação automática..."
    if ! run_installer_attempt "/opt/mt5/clearsetup.exe" "Clear/XP MT5"; then
        echo "[MT5-BRIDGE] Fallback para instalador oficial MetaQuotes..."
        run_installer_attempt "/opt/mt5/mt5setup.exe" "MetaQuotes MT5" || true
    fi

    if [ -f "${MT5_EXE}" ]; then
        echo "[MT5-BRIDGE] Instalação do MetaTrader 5 concluída com sucesso."
    else
        echo "[MT5-BRIDGE] ERRO: terminal64.exe não foi instalado. Verifique /tmp/mt5_install.log."
    fi
fi

if [ -f "/opt/mt5/servers.dat" ]; then
    mkdir -p "${MT5_DIR}/Config"
    if [ ! -f "${MT5_DIR}/Config/servers.dat" ] || [ "$(stat -c%s "/opt/mt5/servers.dat")" -gt "$(stat -c%s "${MT5_DIR}/Config/servers.dat")" ]; then
        cp -f "/opt/mt5/servers.dat" "${MT5_DIR}/Config/servers.dat"
        echo "[MT5-BRIDGE] Catálogo oficial B3 (Clear/XP/Rico) provisionado em '${MT5_DIR}/Config/servers.dat'."
    fi
fi

METAEDITOR_EXE="$(find "${MT5_DIR}" -maxdepth 1 -iname "metaeditor64.exe" 2>/dev/null | head -n 1 || true)"
if [ -f "/opt/mt5/CortexBridgeEA.mq5" ] && [ -n "${METAEDITOR_EXE}" ]; then
    mkdir -p "${MT5_DIR}/MQL5/Experts" "${MT5_HASH_DIR}/MQL5/Experts"
    if [ -f "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5" ]; then
        cp -f "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5" "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5.bak" || true
        rm -f "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5" || true
    fi
    cp -f "/opt/mt5/CortexBridgeEA.mq5" "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.mq5"
    rm -f "${MT5_DIR}/MQL5/Experts/compile.log" || true
    echo "[MT5-BRIDGE] Compilando CortexBridgeEA.mq5 via ${METAEDITOR_EXE}..."
    (cd "${MT5_DIR}" && wine ./MetaEditor64.exe '/compile:MQL5\Experts\CortexBridgeEA.mq5' '/log:MQL5\Experts\compile.log' >/dev/null 2>&1) || true

    compile_elapsed=0
    while [ "${compile_elapsed}" -lt 30 ]; do
        if [ -s "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5" ]; then
            break
        fi
        sleep 0.5
        compile_elapsed=$((compile_elapsed + 1))
    done

    if [ -s "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5" ]; then
        rm -f "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5.bak" || true
        echo "[MT5-BRIDGE] CortexBridgeEA.ex5 compilado com sucesso."
    else
        if [ -s "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5.bak" ]; then
            mv -f "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5.bak" "${MT5_DIR}/MQL5/Experts/CortexBridgeEA.ex5" || true
            echo "[MT5-BRIDGE] AVISO: Compilação nova não gerou .ex5 em 15s; restaurando binário anterior."
        else
            echo "[MT5-BRIDGE] AVISO: Falha ao compilar CortexBridgeEA.ex5 — verifique compile.log."
        fi
    fi
fi

sync_mt5_appdata_profiles

STARTUP_INI="/root/.wine/drive_c/mt5_startup.ini"
SERVER_NAME="${MT5_SERVER:-ClearInvestimentos-CLEAR}"
STARTUP_SYMBOL="${MT5_STARTUP_SYMBOL:-EURUSD}"
STARTUP_PERIOD="${MT5_STARTUP_PERIOD:-H1}"

{
    printf '%s\n' "[Common]"
    if [ -n "${MT5_LOGIN:-}" ] && [ -n "${MT5_PASSWORD:-}" ]; then
        printf '%s\n' "Login=${MT5_LOGIN}"
        printf '%s\n' "Password=${MT5_PASSWORD}"
        printf '%s\n' "KeepPrivate=1"
    fi
    printf '%s\n' "Server=${SERVER_NAME}"
    printf '%s\n' "NewsEnable=0"
    printf '%s\n' "CertInstall=1"
    printf '\n'
    printf '%s\n' "[Experts]"
    printf '%s\n' "AllowDllImport=1"
    printf '%s\n' "Enabled=1"
    printf '%s\n' "Account=0"
    printf '%s\n' "Profile=0"
    printf '%s\n' "Chart=0"
    printf '%s\n' "Api=0"
    printf '\n'
    printf '%s\n' "[StartUp]"
    printf '%s\n' "Expert=CortexBridgeEA"
    printf '%s\n' "Symbol=${STARTUP_SYMBOL}"
    printf '%s\n' "Period=${STARTUP_PERIOD}"
} > "${STARTUP_INI}"
chmod 600 "${STARTUP_INI}"

if [ -n "${MT5_LOGIN:-}" ] && [ -n "${MT5_PASSWORD:-}" ]; then
    echo "[MT5-BRIDGE] Perfil headless auto-login + Algo Trading + CortexBridgeEA configurado (${SERVER_NAME} / ${STARTUP_SYMBOL})."
else
    echo "[MT5-BRIDGE] Perfil headless Algo Trading + CortexBridgeEA configurado (${SERVER_NAME} / ${STARTUP_SYMBOL}); aguardando credenciais MT5."
fi

(
    while true; do
        if [ -f "${MT5_EXE}" ] && ! pgrep -f '[t]erminal64\.exe' >/dev/null 2>&1; then
            sync_mt5_appdata_profiles
            echo "[MT5-BRIDGE] Iniciando terminal64.exe em modo headless..."
            wine "${MT5_EXE}" /skipupdate '/config:C:\mt5_startup.ini' >/tmp/mt5_terminal.log 2>&1 &
        fi
        sleep 15
    done
) &
SUPERVISOR_PID=$!

echo "[MT5-BRIDGE] Iniciando API FastAPI Bridge nativa em ${BRIDGE_HOST}:${BRIDGE_PORT}..."
/opt/bridge-venv/bin/python -m infra.mt5_bridge.app &
BRIDGE_PID=$!
wait "${BRIDGE_PID}"
