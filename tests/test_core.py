import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from cryptography.fernet import Fernet
from core.db import Database
from core.api import tag
from core.ui import card,View
from cogs.wars import remaining,war_card
from scripts.restore import restore

@pytest.fixture
async def db(tmp_path):
    d=Database(tmp_path/'test.db'); await d.open()
    yield d
    await d.close()

@pytest.mark.asyncio
async def test_link_cannot_be_stolen(db):
    await db.link(1,10,'#PYL')
    with pytest.raises(ValueError): await db.link(1,11,'#PYL')
    await db.link(2,11,'#PYL')
    assert (await db.rows('SELECT user FROM links WHERE guild=1'))[0]['user']==10

@pytest.mark.asyncio
async def test_concurrent_links_main_consistency(db):
    await asyncio.gather(db.link(1,10,'#PYL'),db.link(1,10,'#QGR'))
    assert len(await db.rows('SELECT * FROM links WHERE main=1'))==1
    await db.main(1,10,'#QGR')
    await db.unlink(1,10,'#QGR')
    assert (await db.rows('SELECT * FROM links'))[0]['main']==1

@pytest.mark.asyncio
async def test_unlink_other_owner_no_effect(db):
    await db.link(1,10,'#PYL'); await db.unlink(1,11,'#PYL')
    assert len(await db.rows('SELECT * FROM links'))==1

@pytest.mark.asyncio
async def test_event_capacity_concurrent_and_scope(db):
    e=await db.execute('INSERT INTO events(guild,title,starts,capacity,creator) VALUES(?,?,?,?,?)',(1,'Test',9999999999,1,10))
    results=await asyncio.gather(db.rsvp(1,e,10,'present'),db.rsvp(1,e,11,'present'),return_exceptions=True)
    assert sum(isinstance(r,ValueError) for r in results)==1
    with pytest.raises(ValueError): await db.rsvp(2,e,12,'present')
    assert len(await db.rows("SELECT * FROM attendance WHERE status='present'"))==1

@pytest.mark.asyncio
async def test_transaction_rollback(db):
    with pytest.raises(RuntimeError):
        async with db.transaction() as c:
            await c.execute("INSERT INTO settings VALUES(1,'x','1')")
            raise RuntimeError
    assert not await db.rows('SELECT * FROM settings')

@pytest.mark.asyncio
async def test_backup_restore_and_refuse_overwrite(db,tmp_path):
    await db.link(1,10,'#PYL')
    snapshot=tmp_path/'snapshot.db'; await db.backup(snapshot)
    key=Fernet.generate_key(); encrypted=tmp_path/'backup.enc'
    encrypted.write_bytes(Fernet(key).encrypt(snapshot.read_bytes()))
    target=tmp_path/'restored.db'; restore(encrypted,target,key.decode())
    out=Database(target); await out.open()
    assert (await out.rows('SELECT * FROM links'))[0]['tag']=='#PYL'
    await out.close()
    with pytest.raises(ValueError): restore(encrypted,target,key.decode())

@pytest.mark.parametrize('bad',['../../','https://example.org','#12345','','#ABCDEF'])
def test_tag_validation(bad):
    with pytest.raises(ValueError): tag(bad)

def test_normalization(): assert tag(' lggy2joj y '.replace(' ',''))=='#LGGY2J0JY'

def test_branding_and_limits():
    c=card('a'*1000,'b'*6000)
    assert c.footer.text=='Kai-Bot'
    assert len(c.title)==256 and len(c.description)==4000

def test_remaining_cwl_and_regular():
    m={'name':'A','tag':'#PYL','attacks':[{'stars':3}]}
    assert remaining({'attacksPerMember':2,'clan':{'members':[m]}})[0][1]==1
    assert remaining({'clan':{'members':[m]}})==[]
    assert war_card({'state':'notInWar'}).footer.text=='Kai-Bot'

@pytest.mark.asyncio
async def test_view_owner_guard():
    v=View(1)
    i=SimpleNamespace(guild=True,user=SimpleNamespace(id=2),response=SimpleNamespace(is_done=lambda:False,send_message=AsyncMock()))
    assert not await v.interaction_check(i)
    i.response.send_message.assert_awaited_once()

@pytest.mark.asyncio
async def test_all_cogs_and_commands_serialize(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'bot.db'))
    monkeypatch.setenv('ENABLE_CUP','true')
    from main import KaiBot
    async with KaiBot(offline=True) as b:
        await b.setup_hook()
        commands=b.tree.get_commands()
        assert len(b.cogs)==16
        assert len({c.name for c in commands})==len(commands)
        for command in commands: command.to_dict(b.tree)
        home=b.get_cog('Navigation').view
        assert home.is_persistent()
        for x in home.children: assert x.custom_id
