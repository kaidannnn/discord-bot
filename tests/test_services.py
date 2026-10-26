import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
import pytest
from cryptography.fernet import Fernet
from core.api import ClashAPI,APIError
from cogs.backups import Backups
import bootstrap

class Response:
    def __init__(self,status,payload,headers=None): self.status=status; self.payload=payload; self.headers=headers or {}
    async def __aenter__(self): return self
    async def __aexit__(self,*a): pass
    async def json(self): return self.payload

@pytest.mark.asyncio
async def test_api_deduplicates_and_does_not_cache_mutations():
    api=ClashAPI('test')
    session=Mock(); session.request=Mock(side_effect=lambda *a,**k:Response(200,{'status':'ok','items':[]}))
    api.session=session
    a,b=await asyncio.gather(api.request('/test'),api.request('/test'))
    assert session.request.call_count==1
    a['items'].append(1)
    assert b['items']==[]
    await api.verify('#PYL','one'); await api.verify('#PYL','two')
    assert session.request.call_count==3

@pytest.mark.asyncio
async def test_api_retries_and_classifies_denial(monkeypatch):
    api=ClashAPI('test'); api.session=Mock()
    api.session.request=Mock(side_effect=[Response(429,{}, {'Retry-After':'0'}),Response(200,{'ok':1})])
    with patch('core.api.asyncio.sleep',new=AsyncMock()): assert await api.request('/test')=={'ok':1}
    api.session.request=Mock(return_value=Response(403,{}))
    with pytest.raises(APIError,match='refusé'): await api.request('/other')

@pytest.mark.asyncio
async def test_public_backup_repository_rejected(monkeypatch):
    monkeypatch.setenv('BACKUP_REPOSITORY','test/backup')
    monkeypatch.setenv('BACKUP_GITHUB_TOKEN','placeholder')
    monkeypatch.setenv('BACKUP_ENCRYPTION_KEY',Fernet.generate_key().decode())
    cog=Backups(SimpleNamespace()); cog.snapshot=AsyncMock()
    session=Mock(); session.get=Mock(return_value=Response(200,{'private':False})); session.put=Mock()
    manager=Mock(); manager.__aenter__=AsyncMock(return_value=session); manager.__aexit__=AsyncMock(return_value=False)
    with patch('cogs.backups.aiohttp.ClientSession',return_value=manager):
        with pytest.raises(ValueError,match='PRIVÉ'): await cog.remote()
    cog.snapshot.assert_not_awaited(); session.put.assert_not_called()

@pytest.mark.asyncio
async def test_remote_backup_encrypts_and_records_success(monkeypatch):
    key=Fernet.generate_key()
    for k,v in {'BACKUP_REPOSITORY':'test/backup','BACKUP_GITHUB_TOKEN':'placeholder','BACKUP_ENCRYPTION_KEY':key.decode()}.items(): monkeypatch.setenv(k,v)
    db=SimpleNamespace(set=AsyncMock()); cog=Backups(SimpleNamespace(db=db)); cog.snapshot=AsyncMock(return_value=b'SQLITE TEST')
    session=Mock(); session.get=Mock(side_effect=[Response(200,{'private':True}),Response(404,{})]); session.put=Mock(return_value=Response(201,{}))
    manager=Mock(); manager.__aenter__=AsyncMock(return_value=session); manager.__aexit__=AsyncMock(return_value=False)
    with patch('cogs.backups.aiohttp.ClientSession',return_value=manager): await cog.remote()
    payload=session.put.call_args.kwargs['json']
    assert Fernet(key).decrypt(base64.b64decode(payload['content']))==b'SQLITE TEST'
    assert db.set.await_count==2

def test_supervisor_rejects_pointer_traversal(tmp_path):
    p=tmp_path/'CURRENT'; p.write_text('../../secret'); assert bootstrap.read_pointer(p) is None
    p.write_text('a'*40+'\n'); assert bootstrap.read_pointer(p)=='a'*40

def test_health_must_match_release_and_current_start(tmp_path,monkeypatch):
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    (tmp_path/'health.json').write_text(json.dumps({'release':'a'*40,'ready_at':20}))
    assert bootstrap.healthy('a'*40,10)
    assert not bootstrap.healthy('a'*40,30)
    assert not bootstrap.healthy('b'*40,10)

def test_release_cleanup_preserves_data(tmp_path,monkeypatch):
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    r=tmp_path/'releases'; r.mkdir()
    for x in 'abcdef': (r/(x*40)).mkdir()
    (r/'my-files').mkdir(); (tmp_path/'data').mkdir()
    bootstrap.prune_releases({'a'*40})
    assert (r/('a'*40)).exists()
    assert (r/'my-files').exists() and (tmp_path/'data').exists()

def test_supervisor_rolls_back_failed_release(tmp_path,monkeypatch):
    bad='b'*40; good='a'*40
    (tmp_path/'CURRENT').write_text(bad); (tmp_path/'LAST_GOOD').write_text(good)
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    monkeypatch.setattr(bootstrap,'STOP',False)
    monkeypatch.setattr(bootstrap,'load_dotenv',lambda *a:None)
    monkeypatch.setattr(bootstrap.signal,'signal',lambda *a:None)
    clock=[1000]; launched=[]; backups=[]
    monkeypatch.setattr(bootstrap.time,'time',lambda:clock[0])
    def sleep(_):
        clock[0]+=20
        if len(launched)>=2: bootstrap.STOP=True
    monkeypatch.setattr(bootstrap.time,'sleep',sleep)
    class Process:
        def __init__(self,failed): self.failed=failed
        def poll(self): return 1 if self.failed else None
        def terminate(self): self.failed=True
        def wait(self,**k): return 0
    def launch(sha):
        launched.append(sha); return Process(sha==bad)
    monkeypatch.setattr(bootstrap,'launch',launch)
    monkeypatch.setattr(bootstrap,'healthy',lambda sha,started:sha==good)
    monkeypatch.setattr(bootstrap,'predeploy_backup',lambda:backups.append(1))
    monkeypatch.setattr(bootstrap,'prune_releases',lambda keep:None)
    bootstrap.main()
    assert launched==[bad,good]
    assert len(backups)==2
    assert (tmp_path/'LAST_GOOD').read_text().strip()==good
