from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from core.db import Database
from cogs.messaging import Messaging
from cogs.roles import Roles
from cogs.resources import validate_link

@pytest.fixture
async def db(tmp_path):
    db=Database(tmp_path/'test.db'); await db.open()
    yield db
    await db.close()

@pytest.mark.parametrize('url',['https://evil.example/?action=OpenLayout&id=x','http://link.clashofclans.com/?action=OpenLayout&id=x','https://link.clashofclans.com@evil.example/?action=OpenLayout&id=x','https://link.clashofclans.com/?action=CopyArmy&army=u1x1'])
def test_base_links_reject_bad_hosts_and_actions(url):
    with pytest.raises(ValueError): validate_link(url,'base')

def test_valid_copy_links():
    assert validate_link('https://link.clashofclans.com/fr?action=OpenLayout&id=test','base')
    assert validate_link('https://link.clashofclans.com/fr?action=CopyArmy&army=u1x1','army')

@pytest.mark.asyncio
async def test_birthday_awards_only_once_per_year(db):
    guild=SimpleNamespace(id=1,get_role=lambda _:None)
    bot=SimpleNamespace(db=db,offline=True)
    cog=Roles(bot); await cog.cog_load()
    await db.execute('INSERT INTO birthdays VALUES(1,7,26,10)')
    now=datetime(2026,10,26,12,tzinfo=ZoneInfo('Europe/Paris'))
    await cog.birthdays(guild,now); await cog.birthdays(guild,now)
    assert (await db.rows('SELECT points FROM xp'))[0]['points']==300
    await cog.birthdays(guild,now.replace(year=2027))
    assert (await db.rows('SELECT points FROM xp'))[0]['points']==600

@pytest.mark.asyncio
async def test_messaging_optout_skips_delivery(db):
    member=SimpleNamespace(id=7,roles=[SimpleNamespace(id=2)],bot=False,send=AsyncMock())
    guild=SimpleNamespace(id=1,get_member=lambda _:member)
    bot=SimpleNamespace(db=db,offline=True,get_guild=lambda _:guild)
    cog=Messaging(bot); await cog.cog_load()
    job=await db.execute("INSERT INTO dm_jobs(guild,creator,role,title,body,created) VALUES(1,1,2,'T','B',0)")
    await db.execute('INSERT INTO dm_queue(job,user) VALUES(?,7)',(job,))
    await cog.deliver_one()
    member.send.assert_not_awaited()
    assert (await db.rows('SELECT state FROM dm_queue'))[0]['state']=='skipped'

@pytest.mark.asyncio
async def test_messaging_sends_only_once_and_recovers_ambiguous(db):
    member=SimpleNamespace(id=7,roles=[SimpleNamespace(id=2)],bot=False,send=AsyncMock())
    guild=SimpleNamespace(id=1,get_member=lambda _:member)
    bot=SimpleNamespace(db=db,offline=True,get_guild=lambda _:guild)
    cog=Messaging(bot); await cog.cog_load()
    await db.execute('INSERT INTO dm_optin VALUES(1,7)')
    job=await db.execute("INSERT INTO dm_jobs(guild,creator,role,title,body,created) VALUES(1,1,2,'T','B',0)")
    await db.execute('INSERT INTO dm_queue(job,user) VALUES(?,7)',(job,))
    await cog.deliver_one(); await cog.deliver_one()
    member.send.assert_awaited_once()
    await db.execute("UPDATE dm_queue SET state='sending'")
    await cog.cog_load(); await cog.deliver_one()
    assert (await db.rows('SELECT state FROM dm_queue'))[0]['state']=='unknown'
    assert member.send.await_count==1


def test_invites_unknown_or_concurrent_not_fabricated():
    from cogs.welcome import attribution
    before={'x':{'uses':2,'inviter':7}}
    assert attribution(before,{'x':{'uses':3,'inviter':7}})==7
    assert attribution(before,{'x':{'uses':4,'inviter':7}}) is None
    assert attribution(None,{'x':{'uses':3,'inviter':7}}) is None
    assert attribution({}, {'new':{'uses':1,'inviter':7}}) is None

def test_reminders_use_nearest_threshold_without_catchup_spam():
    from cogs.wars import reminder_stage,timestamp
    w={'state':'inWar','endTime':'20261026T200000.000Z'}
    end=timestamp(w['endTime'])
    assert reminder_stage(w,end-4*60)=='5'
    assert reminder_stage(w,end-25*60)=='30'
    assert reminder_stage(w,end-13*3600) is None
    assert reminder_stage(w,end+1) is None

@pytest.mark.asyncio
async def test_changed_birthday_cannot_reissue_expired_role(db):
    get_role=Mock(return_value=None)
    guild=SimpleNamespace(id=1,get_role=get_role)
    cog=Roles(SimpleNamespace(db=db,offline=True)); await cog.cog_load()
    await db.set(1,'birthday_role',100)
    await db.execute('INSERT INTO birthdays VALUES(1,7,26,10)')
    now=datetime(2026,10,26,12,tzinfo=ZoneInfo('Europe/Paris'))
    await cog.birthdays(guild,now)
    await db.set(1,'birthday_role',200)
    await db.execute('UPDATE birthdays SET day=27')
    get_role.reset_mock()
    await cog.birthdays(guild,now.replace(day=27))
    assert all(call.args!=(200,) for call in get_role.call_args_list)
    assert (await db.rows('SELECT points FROM xp'))[0]['points']==300
