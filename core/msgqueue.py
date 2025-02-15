import asyncio
from typing import Dict, Any, Optional


class MsgQueue:
    def __init__(self):
        self.queue: Dict[str, Any] = {}  # 存储数据的字典
        self.lock = asyncio.Lock()  # 互斥锁，确保操作是线程安全的

    async def push(self, key: str, data: Any):
        async with self.lock:
            self.queue[key] = data

    async def pop(self) -> Optional[tuple[str, Any]]:
        async with self.lock:
            if self.queue:
                key, value = self.queue.popitem()
                return key, value
            else:
                return None


msg_queue = MsgQueue()
