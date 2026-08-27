import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('82.112.245.99', 22, 'servico', r'JKyRd<^Wg$)&D:Z3')

cmds = [
    'sudo -S systemctl status cortex-ia.service --no-pager',
    'sudo -S journalctl -u cortex-ia.service -n 50 --no-pager',
    'tail -30 /home/servico/cortex-ia/logs/analysis_decision.log 2>/dev/null || echo NO_FILE',
    'tail -30 /home/servico/cortex-ia/logs/analysis_sentiment.log 2>/dev/null || echo NO_FILE',
    "sqlite3 /home/servico/cortex-ia/data/cortex.db 'SELECT COUNT(*) as total_decisions FROM ai_decisions;'",
    "sqlite3 /home/servico/cortex-ia/data/cortex.db 'SELECT COUNT(*) as total_trades FROM trades;'",
    "sqlite3 /home/servico/cortex-ia/data/cortex.db 'SELECT * FROM trades ORDER BY id DESC LIMIT 5;'",
    "sqlite3 /home/servico/cortex-ia/data/cortex.db 'SELECT * FROM daily_reports ORDER BY id DESC LIMIT 5;'",
    "cat /home/servico/cortex-ia/data/simulator_state.json 2>/dev/null || echo NO_STATE"
]

with open('vps_audit_result.txt', 'w', encoding='utf-8') as f:
    for cmd in cmds:
        stdin, stdout, stderr = ssh.exec_command(cmd)
        if 'sudo' in cmd:
            stdin.write(r'JKyRd<^Wg$)&D:Z3' + '\n')
            stdin.flush()
        f.write(f'\n===== {cmd} =====\n')
        f.write(stdout.read().decode('utf-8', errors='replace'))
        err = stderr.read().decode('utf-8', errors='replace')
        if err.strip() and 'password' not in err.lower():
            f.write(f'STDERR: {err}\n')

ssh.close()
print('Audit query finished successfully')
