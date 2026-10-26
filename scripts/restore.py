"""Decrypt a downloaded backup into a NEW file. Never overwrite a live database."""
import argparse
import os
import sqlite3
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet

def restore(source,target,key):
    target=Path(target)
    if target.exists(): raise ValueError('Destination déjà existante. Arrête le bot et choisis un nouveau fichier.')
    raw=Fernet(key.encode()).decrypt(Path(source).read_bytes())
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'check.db'; p.write_bytes(raw)
        with sqlite3.connect(p) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Sauvegarde SQLite invalide.')
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone(): raise ValueError('Base non reconnue.')
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as f: f.write(raw)
    os.chmod(target,0o600)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('source'); parser.add_argument('destination')
    a=parser.parse_args()
    key=os.getenv('BACKUP_ENCRYPTION_KEY')
    if not key: raise SystemExit('Définis BACKUP_ENCRYPTION_KEY dans ton environnement privé.')
    restore(a.source,a.destination,key)
    print('Sauvegarde vérifiée et restaurée. Configure DATABASE_PATH vers ce fichier, puis redémarre.')
