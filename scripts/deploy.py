import os
import paramiko
from scp import SCPClient

host = os.getenv('DEPLOY_HOST', '')
port = int(os.getenv('DEPLOY_PORT', '22'))
user = os.getenv('DEPLOY_USER', '')
password = os.getenv('DEPLOY_PASSWORD', '')
remote_dir = os.getenv('DEPLOY_REMOTE_DIR', '~/cortex-ia')
local_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if not host or not user or not password:
    raise ValueError("DEPLOY_HOST, DEPLOY_USER and DEPLOY_PASSWORD environment variables are required.")

print(f"Connecting to {host}...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, user, password)

print("Starting upload via SCP...")
with SCPClient(ssh.get_transport()) as scp:
    for item in os.listdir(local_dir):
        if item in ['.git', 'venv', '.venv', '__pycache__', 'cortex.db', 'simulator_state.json', '.env', 'GIT', 'backups', 'scratch']:
            continue
        local_path = os.path.join(local_dir, item)
        print(f"Uploading {item}...")
        try:
            scp.put(local_path, recursive=True, remote_path=remote_dir)
        except Exception as e:
            print(f"Error uploading {item}: {e}")

print("Upload complete!")

print("Setting up systemd service...")
commands = [
    f"sudo -S cp {remote_dir}/scripts/cortex-ia.service /etc/systemd/system/",
    f"sudo -S systemctl daemon-reload",
    f"sudo -S systemctl enable cortex-ia.service",
    f"sudo -S systemctl restart cortex-ia.service",
    f"sudo -S systemctl status cortex-ia.service --no-pager"
]

for cmd in commands:
    print(f"Running: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    stdin.write(password + '\n')
    stdin.flush()
    print(stdout.read().decode())
    err = stderr.read().decode()
    if err:
        print("ERROR:", err)

ssh.close()
print("Deploy finished!")
