import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
import aiosqlite

SCHEMA = '''
CREATE TABLE IF NOT EXISTS followed_clans(guild INTEGER, tag TEXT, channel INTEGER, cwl INTEGER DEFAULT 0, PRIMARY KEY(guild,tag));
CREATE TABLE IF NOT EXISTS settings(guild INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(guild,key));
CREATE TABLE IF NOT EXISTS links(guild INTEGER NOT NULL, tag TEXT NOT NULL, user INTEGER NOT NULL, main INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild,tag));
CREATE UNIQUE INDEX IF NOT EXISTS one_main ON links(guild,user) WHERE main=1;
CREATE TABLE IF NOT EXISTS snapshots(guild INTEGER, tag TEXT, ts INTEGER, payload TEXT, PRIMARY KEY(guild,tag,ts));
CREATE INDEX IF NOT EXISTS snapshot_lookup ON snapshots(guild,tag,ts DESC);
CREATE TABLE IF NOT EXISTS wars(guild INTEGER, war_key TEXT, kind TEXT, payload TEXT, updated INTEGER, PRIMARY KEY(guild,war_key));
CREATE TABLE IF NOT EXISTS notices(guild INTEGER, key TEXT, ts INTEGER, PRIMARY KEY(guild,key));
CREATE TABLE IF NOT EXISTS suggestions(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER,user INTEGER,category TEXT,title TEXT,body TEXT,status TEXT DEFAULT 'Reçue',created INTEGER);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER,title TEXT,starts INTEGER,capacity INTEGER,creator INTEGER);
CREATE TABLE IF NOT EXISTS attendance(event INTEGER REFERENCES events(id) ON DELETE CASCADE,user INTEGER,status TEXT,PRIMARY KEY(event,user));
CREATE TABLE IF NOT EXISTS roster(guild INTEGER,user INTEGER,tag TEXT,role TEXT DEFAULT 'Joueur',PRIMARY KEY(guild,tag));
CREATE TABLE IF NOT EXISTS xp(guild INTEGER,user INTEGER,points INTEGER DEFAULT 0,last INTEGER DEFAULT 0,PRIMARY KEY(guild,user));
CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER,user INTEGER,channel INTEGER UNIQUE,status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER,actor INTEGER,action TEXT,detail TEXT,ts INTEGER);
'''

class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = asyncio.Lock()
        self.conn = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path, isolation_level=None)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute('PRAGMA journal_mode=WAL')
        await self.conn.execute('PRAGMA foreign_keys=ON')
        await self.conn.execute('PRAGMA busy_timeout=10000')
        await self.conn.executescript(SCHEMA)

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            await self.conn.execute('BEGIN IMMEDIATE')
            try:
                yield self.conn
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    async def rows(self, sql, params=()):
        async with self.lock:
            async with self.conn.execute(sql, params) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def execute(self, sql, params=()):
        async with self.lock:
            async with self.conn.execute(sql, params) as cur:
                return cur.lastrowid

    async def get(self, guild, key, default=None):
        rows = await self.rows('SELECT value FROM settings WHERE guild=? AND key=?',(guild,key))
        return json.loads(rows[0]['value']) if rows else default

    async def set(self, guild, key, value):
        await self.execute('INSERT INTO settings VALUES(?,?,?) ON CONFLICT(guild,key) DO UPDATE SET value=excluded.value',(guild,key,json.dumps(value)))

    async def link(self, guild, user, tag):
        async with self.transaction() as c:
            async with c.execute('SELECT user FROM links WHERE guild=? AND tag=?',(guild,tag)) as q:
                old = await q.fetchone()
            if old and old['user'] != user:
                raise ValueError('Ce compte est déjà lié à une autre personne. Contacte le staff.')
            async with c.execute('SELECT 1 FROM links WHERE guild=? AND user=?',(guild,user)) as q:
                has = await q.fetchone()
            async with c.execute('SELECT COUNT(*) FROM links WHERE guild=? AND user=?',(guild,user)) as q:
                count = (await q.fetchone())[0]
            if not old and count >= 25: raise ValueError('Tu as atteint la limite de 25 villages liés.')
            await c.execute('INSERT OR IGNORE INTO links VALUES(?,?,?,?)',(guild,tag,user,int(not has)))

    async def main(self, guild, user, tag):
        async with self.transaction() as c:
            async with c.execute('SELECT 1 FROM links WHERE guild=? AND user=? AND tag=?',(guild,user,tag)) as q:
                if not await q.fetchone(): raise ValueError('Ce compte ne t’appartient pas.')
            await c.execute('UPDATE links SET main=0 WHERE guild=? AND user=?',(guild,user))
            await c.execute('UPDATE links SET main=1 WHERE guild=? AND user=? AND tag=?',(guild,user,tag))

    async def unlink(self, guild, user, tag):
        async with self.transaction() as c:
            await c.execute('DELETE FROM links WHERE guild=? AND user=? AND tag=?',(guild,user,tag))
            await c.execute('UPDATE links SET main=1 WHERE guild=? AND user=? AND tag=(SELECT tag FROM links WHERE guild=? AND user=? ORDER BY main DESC,tag LIMIT 1)',(guild,user,guild,user))
            await c.execute('DELETE FROM roster WHERE guild=? AND user=? AND tag=?',(guild,user,tag))

    async def rsvp(self, guild, event, user, status):
        async with self.transaction() as c:
            async with c.execute('SELECT * FROM events WHERE id=? AND guild=?',(event,guild)) as q:
                e = await q.fetchone()
            if not e or e['starts'] < time.time(): raise ValueError('Cet événement est terminé ou introuvable.')
            if status == 'present':
                async with c.execute("SELECT COUNT(*) FROM attendance WHERE event=? AND status='present' AND user<>?",(event,user)) as q:
                    count = (await q.fetchone())[0]
                if count >= e['capacity']: raise ValueError('Toutes les places sont prises. Choisis Réserve.')
            await c.execute('INSERT INTO attendance VALUES(?,?,?) ON CONFLICT(event,user) DO UPDATE SET status=excluded.status',(event,user,status))

    async def log(self, guild, actor, action, detail=''):
        await self.execute('INSERT INTO audit(guild,actor,action,detail,ts) VALUES(?,?,?,?,?)',(guild,actor,action,detail,int(time.time())))

    async def backup(self, path):
        async with self.lock:
            async with aiosqlite.connect(path) as out:
                await self.conn.backup(out)

    async def close(self):
        if self.conn: await self.conn.close()
