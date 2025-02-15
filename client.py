import asyncio
import os

from core.client import Client

if __name__ == '__main__':
    key = os.environ.get("KEY", "123456")
    server = os.environ.get("SERVER", "192.168.2.22:12999")
    a = Client(server=server, key=key)
    asyncio.run(a.run())

