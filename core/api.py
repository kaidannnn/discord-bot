import asyncio
import copy
import re
import time
from urllib.parse import quote
import aiohttp

class APIError(ValueError): pass

def tag(value):
    value = value.strip().upper().replace('O','0').lstrip('#')
    if not re.fullmatch(r'[0289PYLQGRJCUV]{3,14}',value):
        raise ValueError('Tag invalide. Copie le tag sous ton pseudo dans Clash of Clans (ex. #LGGY2J0JY).')
    return '#'+value

def encoded(value): return quote(tag(value),safe='')

class ClashAPI:
    def __init__(self, token):
        self.token = token
        self.session = None
        self.cache = {}
        self.semaphore = asyncio.Semaphore(4)
        self.locks = [asyncio.Lock() for _ in range(64)]

    async def open(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),headers={'Authorization':f'Bearer {self.token}'})

    async def request(self, path, *, ttl=45, payload=None):
        if not self.token: raise APIError('Clé Clash absente : le propriétaire doit configurer COC_API_TOKEN.')
        async with self.locks[hash(path)%64]:
            now=time.monotonic()
            if payload is None and ttl and path in self.cache and self.cache[path][0]>now:
                return copy.deepcopy(self.cache[path][1])
            async with self.semaphore:
                for attempt in range(3):
                    try:
                        async with self.session.request('POST' if payload is not None else 'GET','https://api.clashofclans.com/v1'+path,json=payload) as r:
                            if r.status in (429,500,502,503,504):
                                if attempt<2:
                                    try: delay=min(float(r.headers.get('Retry-After','2')),10)
                                    except ValueError: delay=2
                                    await asyncio.sleep(max(delay,0.2)*(attempt+1)); continue
                                raise APIError('API Clash temporairement indisponible. Réessaie dans une minute.')
                            if r.status == 403: raise APIError('Accès Clash refusé : clé/IP non autorisée ou journal de guerre privé.')
                            if r.status == 404: raise APIError('Joueur, clan ou compétition introuvable.')
                            if r.status != 200: raise APIError(f'API Clash : erreur {r.status}. Vérifie les informations saisies.')
                            data=await r.json()
                    except (aiohttp.ClientError,asyncio.TimeoutError) as exc:
                        if attempt<2: await asyncio.sleep(attempt+1); continue
                        raise APIError('La connexion à Clash a expiré. Réessaie.') from exc
                    if payload is None and ttl:
                        if len(self.cache)>500: self.cache={k:v for k,v in self.cache.items() if v[0]>now}
                        if len(self.cache)>1000: self.cache.clear()
                        self.cache[path]=(time.monotonic()+ttl,data)
                    return copy.deepcopy(data)

    async def player(self,t): return await self.request('/players/'+encoded(t))
    async def clan(self,t): return await self.request('/clans/'+encoded(t))
    async def war(self,t): return await self.request('/clans/'+encoded(t)+'/currentwar',ttl=20)
    async def verify(self,t,token):
        result=await self.request('/players/'+encoded(t)+'/verifytoken',ttl=0,payload={'token':token.strip()})
        return result.get('status')=='ok'
    async def close(self):
        if self.session: await self.session.close()
