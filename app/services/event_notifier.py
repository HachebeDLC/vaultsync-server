import asyncio
import json
from typing import Dict, Set, Optional, NamedTuple

class Connection(NamedTuple):
    queue: asyncio.Queue
    device_name: Optional[str]

class EventNotifier:
    def __init__(self):
        # Maps user_id (int) to a set of Connection objects
        self.user_connections: Dict[int, Set[Connection]] = {}

    async def generator(self, user_id: int, device_name: Optional[str] = None):
        q = asyncio.Queue()
        conn = Connection(q, device_name)
        
        if user_id not in self.user_connections:
            self.user_connections[user_id] = set()
        self.user_connections[user_id].add(conn)
        
        try:
            while True:
                data = await q.get()
                # data is expected to be a dict with {"event": str, "payload": dict}
                event_type = data.get("event", "message")
                payload = data.get("payload", {})
                yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if user_id in self.user_connections:
                self.user_connections[user_id].remove(conn)
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]

    async def broadcast_to_user(self, user_id: int, payload: dict, event: str = "file_available", target_device: Optional[str] = None):
        """
        Sends an event to all devices of a user, or a specific device if target_device is provided.
        """
        if user_id not in self.user_connections:
            return

        data = {"event": event, "payload": payload}
        for conn in self.user_connections[user_id]:
            if target_device is None or conn.device_name == target_device:
                await conn.queue.put(data)

    async def broadcast_all(self, payload: dict, event: str = "file_available"):
        """
        DEPRECATED/LEGACY: Broadcasts to literally everyone (used for original file_available global-ish check).
        Actually, we should probably always target users.
        """
        data = {"event": event, "payload": payload}
        for user_id in list(self.user_connections.keys()):
            for conn in self.user_connections[user_id]:
                await conn.queue.put(data)

event_notifier = EventNotifier()
