import os
import tarfile
import paramiko
from scp import SCPClient

def create_tarball (source_dir ,output_filename ):
    print (f"Creating {output_filename }...")
    exclude_dirs ={'.git','__pycache__','.pytest_cache','.venv','node_modules'}
    exclude_files ={'cortex-ia.tar.gz','deploy.py','run_pip.py','upload_sh.py',
    'fix.py','fix_decision.py','fix_test_decision2.py'}
    with tarfile .open (output_filename ,"w:gz")as tar :
        for root ,dirs ,files in os .walk (source_dir ):

            dirs [:]=[d for d in dirs if d not in exclude_dirs ]
            for file in files :
                if file .endswith ('.pyc')or file in exclude_files :
                    continue
                file_path =os .path .join (root ,file )
                arcname =os .path .relpath (file_path ,source_dir )
                tar .add (file_path ,arcname =arcname )
    print (f"{output_filename } created.")

def deploy (host ,port ,user ,password ,local_file ,remote_dir ):
    print (f"Connecting to {user }@{host }:{port }...")
    ssh =paramiko .SSHClient ()
    ssh .set_missing_host_key_policy (paramiko .AutoAddPolicy ())
    ssh .connect (hostname =host ,port =port ,username =user ,password =password )

    print (f"Cleaning remote directory {remote_dir }...")

    stdin ,stdout ,stderr =ssh .exec_command (
    f"cd {remote_dir } && find . -maxdepth 1 "
    f"! -name '.' ! -name '.venv' ! -name 'data' ! -name '.env' ! -name 'logs' "
    f"-exec rm -rf {{}} +"
    )
    stdout .channel .recv_exit_status ()

    print (f"Uploading {local_file }...")
    with SCPClient (ssh .get_transport ())as scp :
        scp .put (local_file ,remote_dir )

    print ("Extracting files on remote server...")
    stdin ,stdout ,stderr =ssh .exec_command (
    f"cd {remote_dir } && tar -xzf {local_file } && rm {local_file }"
    )
    stdout .channel .recv_exit_status ()

    print ("Fixing line endings for shell scripts...")
    stdin ,stdout ,stderr =ssh .exec_command (
    f"cd {remote_dir } && find . -name '*.sh' -exec sed -i 's/\\r$//' {{}} +"
    )
    stdout .channel .recv_exit_status ()

    print ("Setting executable permissions...")
    stdin ,stdout ,stderr =ssh .exec_command (
    f"cd {remote_dir } && chmod +x start_cortex.sh deploy/setup.sh"
    )
    stdout .channel .recv_exit_status ()

    print ("Installing/updating requirements...")
    stdin ,stdout ,stderr =ssh .exec_command (
    f"cd {remote_dir } && source .venv/bin/activate && pip install --prefer-binary -q -r requirements.txt"
    )
    exit_status =stdout .channel .recv_exit_status ()
    print (f"Pip exit status: {exit_status }")

    print ("Creating logs directory...")
    stdin ,stdout ,stderr =ssh .exec_command (f"mkdir -p {remote_dir }/logs")
    stdout .channel .recv_exit_status ()

    print ("Creating LOGS-PROJETOS directory...")
    stdin ,stdout ,stderr =ssh .exec_command (f"mkdir -p /LOGS-PROJETOS/cortex-ia")
    stdout .channel .recv_exit_status ()

    ssh .close ()
    print ("Deployment complete!")

if __name__ =="__main__":
    host = os.getenv("DEPLOY_HOST", "")
    port = int(os.getenv("DEPLOY_PORT", "22"))
    user = os.getenv("DEPLOY_USER", "")
    password = os.getenv("DEPLOY_PASSWORD", "")
    remote_dir = os.getenv("DEPLOY_REMOTE_DIR", "~/cortex-ia")
    if not host or not password or not user:
        raise ValueError("DEPLOY_HOST, DEPLOY_USER and DEPLOY_PASSWORD environment variables are required.")
    tar_filename ="cortex-ia.tar.gz"

    create_tarball (".",tar_filename )
    try :
        deploy (host ,port ,user ,password ,tar_filename ,remote_dir )
    finally :
        if os .path .exists (tar_filename ):
            os .remove (tar_filename )
