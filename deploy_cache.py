import paramiko
from scp import SCPClient
import os

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('82.112.245.99', 22, 'servico', r'JKyRd<^Wg$)&D:Z3')

files_to_upload = [
    ('config/settings.py', '/home/servico/cortex-ia/config/settings.py'),
    ('analysis/sentiment.py', '/home/servico/cortex-ia/analysis/sentiment.py'),
    ('analysis/decision.py', '/home/servico/cortex-ia/analysis/decision.py'),
]

with SCPClient(ssh.get_transport()) as scp:
    for local, remote in files_to_upload:
        local_path = os.path.join(r'f:\Projetos\cortex-ia', local)
        print(f'Uploading {local}...')
        scp.put(local_path, remote_path=remote)

print('Files uploaded. Restarting service...')

stdin, stdout, stderr = ssh.exec_command('sudo -S systemctl restart cortex-ia.service')
stdin.write(r'JKyRd<^Wg$)&D:Z3' + '\n')
stdin.flush()
stdout.read()

import time
time.sleep(3)

stdin2, stdout2, stderr2 = ssh.exec_command('sudo -S systemctl status cortex-ia.service --no-pager')
stdin2.write(r'JKyRd<^Wg$)&D:Z3' + '\n')
stdin2.flush()
status_out = stdout2.read().decode('utf-8', errors='replace')
print('=== SERVICE STATUS ===\n', status_out)

ssh.close()
print('Deploy complete!')
