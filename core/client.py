import asyncio
import json

import websockets
from nvitop import Device, host
from nvitop.api.host import hostname


class Client:

    def __init__(self, key="123456", server="localhost:12999"):

        self.server = f"ws://{server}/ws?key={key}"
        self._ws = None

        self.lock = asyncio.Lock()  # 互斥锁

    @staticmethod
    def collect():
        cpu_info = dict(
            cpu_percent=host.cpu_percent(),
            cpu_count=host.cpu_count(),
        )

        gpu_info = []

        for device in Device.all():
            gpu_info.append(dict(
                index=device.index,
                fan_speed=device.fan_speed(),
                temperature=device.temperature(),
                gpu_utilization=device.gpu_utilization(),
                memory_total=device.memory_total(),
                memory_used=device.memory_used(),
                memory_free=device.memory_free(),
            ))

        host_info = dict(
            host_name=hostname(),
            # ip=host.ip(), ip 由服务端获取
        )
        return host_info, cpu_info, gpu_info

    @property
    async def ws(self):
        """确保只创建一个 WebSocket 连接"""
        async with self.lock:  # 保证只有一个任务在创建连接
            if self._ws is None:
                await self._connect_ws()
        return self._ws

    async def _connect_ws(self):
        """异步连接 WebSocket"""
        try:
            print(f"Connecting to WebSocket server: {self.server}")
            self._ws = await websockets.connect(self.server)
        except Exception as e:
            print(f"Failed to connect to WebSocket server: {e}")
            self._ws = None

    async def receive(self):
        """异步接收服务器消息"""
        while True:
            ws = await self.ws  # 获取 WebSocket 实例
            if ws:
                try:
                    # 设置 30s 超时
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    print(f"Received message: {msg}")
                except asyncio.TimeoutError:
                    print("Timeout: No message received for 30 seconds, reconnecting...")
                    self._ws = None  # 置空连接，触发重连
                    await asyncio.sleep(1)  # 等待 1s 避免频繁重连
                except Exception as e:
                    print(f"Receive error: {e}")
                    self._ws = None
                    break

    async def send(self):
        while True:
            host_info, cpu_info, gpu_info = self.collect()
            ws = await self.ws  # 获取 WebSocket 实例
            if ws:
                try:
                    data = dict(
                        host_info=host_info,
                        cpu_info=cpu_info,
                        gpu_info=gpu_info,
                    )
                    await ws.send(json.dumps(data))
                    print(f"Sent data: {data}")
                except Exception as e:
                    print(f"WebSocket error: {e}")
                    self._ws = None  # 置空连接，触发重连
            await asyncio.sleep(0.2)


    async def run(self):
        """同时运行 发送 和 接收"""
        await asyncio.gather(self.send(), self.receive())
