import asyncio
import json
from typing import Set

class EventNotifier:
    def __init__(self):
        self.connections: Set[asyncio.Queue] = set()

    async def generator(self):
        q = asyncio.Queue()
        self.connections.add(q)
        try:
            while True:
                data = await q.get()
                yield f"event: file_available\ndata: {json.dumps(data)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self.connections.remove(q)

    async def broadcast(self, data: dict):
        for q in self.connections:
            await q.put(data)

event_notifier = EventNotifier()
