"""Upload immutable release; flip CURRENT only after every file is uploaded.
No remote shell, no deletion of shared data, strict SSH host-key checking.
"""
import os
import posixpath
import re
from pathlib import Path
import paramiko

ALLOWED_ROOT={'main.py','requirements.txt','requirements-dev.txt'}
ALLOWED_DIR={'core','cogs','scripts','docs'}

def mkdirs(sftp,path):
    pieces=path.split('/'); current='/' if path.startswith('/') else ''
    for piece in pieces:
        if not piece: continue
        current=posixpath.join(current,piece)
        try: sftp.stat(current)
        except FileNotFoundError: sftp.mkdir(current)

def deploy():
    sha=os.environ['GITHUB_SHA']
    if not re.fullmatch('[a-f0-9]{40}',sha): raise ValueError('Invalid commit SHA')
    root=(os.environ.get('SFTP_ROOT') or '/home/container').rstrip('/')
    if not root.startswith('/') or '..' in root.split('/'): raise ValueError('Absolute SFTP root required')
    hostkeys=Path(os.environ['SFTP_KNOWN_HOSTS_FILE'])
    client=paramiko.SSHClient(); client.load_host_keys(str(hostkeys)); client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(os.environ['SFTP_HOST'],port=int(os.environ.get('SFTP_PORT') or '2022'),username=os.environ['SFTP_USERNAME'],password=os.environ['SFTP_PASSWORD'],look_for_keys=False,allow_agent=False,timeout=20)
    try:
        with client.open_sftp() as sftp:
            release=posixpath.join(root,'releases',sha); mkdirs(sftp,release)
            for p in sorted(Path('.').rglob('*')):
                if not p.is_file() or p.is_symlink(): continue
                parts=p.parts
                if any(x.startswith('.') or x=='__pycache__' for x in parts): continue
                if not (str(p) in ALLOWED_ROOT or (parts[0] in ALLOWED_DIR and p.suffix in {'.py','.md','.txt'})): continue
                remote=posixpath.join(release,p.as_posix()); mkdirs(sftp,posixpath.dirname(remote))
                sftp.put(str(p),remote)
            with sftp.file(posixpath.join(release,'RELEASE'),'w') as f: f.write(sha+'\n')
            staging=posixpath.join(root,'CURRENT.'+sha)
            with sftp.file(staging,'w') as f: f.write(sha+'\n')
            # OpenSSH/Pterodactyl SFTP supports atomic POSIX rename. Fail closed if unsupported.
            sftp.posix_rename(staging,posixpath.join(root,'CURRENT'))
    finally: client.close()

if __name__=='__main__': deploy()
