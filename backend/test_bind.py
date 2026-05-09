import requests
from requests.adapters import HTTPAdapter
import sys

class BoundAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs['source_address'] = ('127.0.0.1', 0)
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

s = requests.Session()
s.mount('http://', BoundAdapter())
s.mount('https://', BoundAdapter())

try:
    s.get('https://proof.ovh.net/', timeout=3)
    print("SUCCESS")
except Exception as e:
    print("ERROR:", e)
