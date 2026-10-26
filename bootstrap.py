"""Katabump entrypoint: polls the locally deployed CURRENT pointer.
Runs releases in separate directories, rolls back unsuccessful startups.
Upload this file ONCE to /home/container and set PY FILE=bootstrap.py.
"""
import hashlib
import json
import os
import re
import signal
import sqlite3
import shutil
import subprocess
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent
STOP=False

def stop(*_):
    global STOP
    STOP=True

def read_pointer(path):
    try: value=path.read_text().strip()
    except FileNotFoundError: return None
    return value if re.fullmatch('[a-f0-9]{40}',value) else None

def terminate(child):
    if child and child.poll() is None:
        child.terminate()
        try: child.wait(timeout=20)
        except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=10)

def predeploy_backup():
    db=ROOT/'data'/'kai_bot.db'
    if not db.exists(): return
    folder=ROOT/'backups'; folder.mkdir(exist_ok=True)
    target=folder/f'predeploy-{int(time.time())}.db'
    with sqlite3.connect(db) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
    for old in sorted(folder.glob('predeploy-*.db'))[:-3]: old.unlink()

def prune_releases(keep):
    # Delete only directories named by a full SHA, never shared data or user files.
    folder=ROOT/'releases'
    candidates=sorted((p for p in folder.iterdir() if p.is_dir() and re.fullmatch('[a-f0-9]{40}',p.name)),key=lambda p:p.stat().st_mtime,reverse=True)
    retained=set(keep)|{p.name for p in candidates[:3]}
    for p in candidates:
        if p.name not in retained: shutil.rmtree(p)

def launch(sha):
    release=ROOT/'releases'/sha
    if not (release/'main.py').is_file() or (release/'RELEASE').read_text().strip()!=sha:
        raise ValueError('Release incomplete')
    req=release/'requirements.txt'
    digest=hashlib.sha256(req.read_bytes()).hexdigest()[:16]
    deps=ROOT/'dependencies'/digest
    if not (deps/'READY').exists():
        deps.mkdir(parents=True,exist_ok=True)
        subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--target',str(deps),'-r',str(req)],check=True,timeout=240)
        (deps/'READY').write_text('ok')
    env=os.environ.copy()
    env['DATABASE_PATH']=str(ROOT/'data'/'kai_bot.db')
    env['KAI_HEALTH_FILE']=str(ROOT/'health.json')
    env['KAI_RELEASE']=sha
    env['PYTHONPATH']=str(deps)
    # Explicit one-time sync request; remove marker only after successful startup.
    args=[sys.executable,str(release/'main.py')]
    if (ROOT/'SYNC_ONCE').exists(): args.append('--sync')
    return subprocess.Popen(args,cwd=release,env=env)

def healthy(sha,started):
    try:
        data=json.loads((ROOT/'health.json').read_text())
        return data['release']==sha and data['ready_at']>=started
    except (OSError,ValueError,KeyError): return False

def main():
    load_dotenv(ROOT/'.env')
    for s in (signal.SIGTERM,signal.SIGINT): signal.signal(s,stop)
    current=None; child=None; started=0; ready=False; rejected=set(); retry_after=0
    previous=read_pointer(ROOT/'LAST_GOOD')
    try:
        while not STOP:
            wanted=read_pointer(ROOT/'CURRENT')
            if not wanted: time.sleep(3); continue
            if wanted in rejected: wanted=previous
            if wanted and wanted!=current and time.time()>=retry_after:
                terminate(child); child=None
                predeploy_backup()
                current=wanted; ready=False; started=time.time()
                try: child=launch(current)
                except Exception as exc:
                    print('Kai-Bot: démarrage refusé',type(exc).__name__,flush=True)
                    rejected.add(current); current=None; retry_after=time.time()+15; continue
            if child:
                if not ready and healthy(current,started):
                    ready=True; previous=current
                    (ROOT/'LAST_GOOD').write_text(current+'\n')
                    (ROOT/'SYNC_ONCE').unlink(missing_ok=True)
                    print('Kai-Bot: version connectée',current,flush=True)
                    try: prune_releases({current})
                    except OSError: pass
                failed=child.poll() is not None or (not ready and time.time()-started>360)
                if failed:
                    terminate(child); child=None
                    if not ready: rejected.add(current)
                    current=None; retry_after=time.time()+15
            time.sleep(3)
    finally: terminate(child)

if __name__=='__main__': main()
