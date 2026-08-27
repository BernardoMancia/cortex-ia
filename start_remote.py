import paramiko
import time
import os

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

_host = os.getenv("DEPLOY_HOST", "82.112.245.99")
_user = os.getenv("DEPLOY_USER", "servico")
_password = os.getenv("DEPLOY_PASSWORD", "")
if not _password:
    raise ValueError("DEPLOY_PASSWORD environment variable is required.")
ssh.connect(_host, 22, _user, _password)
rd = '/home/servico/cortex-ia'

# Kill any existing processes
print("Killing existing processes...")
ssh.exec_command('pkill -f "python.*main.py" 2>/dev/null || true')
ssh.exec_command('fuser -k 8003/tcp 2>/dev/null || true')
time.sleep(2)

# Start dashboard
print("Starting dashboard on port 8003...")
ssh.exec_command(
    f'cd {rd} && source .venv/bin/activate && '
    f'nohup python -m uvicorn dashboard.app:app --host 0.0.0.0 --port 8003 '
    f'> logs/dashboard.log 2>&1 &'
)
time.sleep(2)

# Start engine
print("Starting engine in simulation mode...")
ssh.exec_command(
    f'cd {rd} && source .venv/bin/activate && '
    f'nohup python main.py --simulation '
    f'> logs/engine.log 2>&1 &'
)
time.sleep(5)

# Check logs
stdin, stdout, stderr = ssh.exec_command(f'tail -40 {rd}/logs/engine.log')
log = stdout.read().decode('utf-8', errors='replace')
with open('engine_log.txt', 'w', encoding='utf-8') as f:
    f.write(log)

stdin, stdout, stderr = ssh.exec_command(f'tail -10 {rd}/logs/dashboard.log')
dlog = stdout.read().decode('utf-8', errors='replace')
with open('dashboard_log.txt', 'w', encoding='utf-8') as f:
    f.write(dlog)

# Check PIDs
stdin, stdout, stderr = ssh.exec_command('pgrep -af "python.*main.py" || echo NO_ENGINE')
eng = stdout.read().decode('utf-8', errors='replace').strip()
stdin, stdout, stderr = ssh.exec_command('lsof -i :8003 2>/dev/null | head -3 || echo NO_DASHBOARD')
dash = stdout.read().decode('utf-8', errors='replace').strip()

with open('status.txt', 'w', encoding='utf-8') as f:
    f.write(f'ENGINE: {eng}\nDASHBOARD: {dash}\n')

ssh.close()
print("Done - check status.txt, engine_log.txt, dashboard_log.txt")
