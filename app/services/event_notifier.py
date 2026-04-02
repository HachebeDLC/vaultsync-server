import asyncio
import json
import redis.asyncio as redis
import logging
from typing import Dict, Set, Optional, NamedTuple
from ..config import REDIS_HOST, REDIS_PORT

logger = logging.getLogger("VaultSync")

class Connection(NamedTuple):
    queue: asyncio.Queue
    device_name: Optional[str]

class EventNotifier:
    def __init__(self):
        # Maps user_id (int) to a set of Connection objects
        self.user_connections: Dict[int, Set[Connection]] = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._redis: Optional[redis.Redis] = None
        self._pubsub: Optional[redis.client.PubSub] = None

    async def _ensure_listening(self):
        """Starts the Redis Pub/Sub background task if not already running."""
        if self._listen_task is None or self._listen_task.done():
            self._listen_task = asyncio.create_task(self._listen_loop())
            logger.info("📡 SSE: Redis Pub/Sub task started")

    async def _listen_loop(self):
        """Main loop for listening to Redis notifications."""
        while True:
            try:
                self._redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
                self._pubsub = self._redis.pubsub()
                
                # Subscribe to the 'vaultsync_events' channel
                await self._pubsub.subscribe('vaultsync_events')
                logger.info("✅ SSE: Successfully subscribed to Redis 'vaultsync_events' channel")
                
                async for message in self._pubsub.listen():
                    if message['type'] == 'message':
                        self._on_message(message['data'])
            except Exception as e:
                logger.warning(f"⚠️ SSE: Redis loop error: {e}. Retrying in 5s...")
                await asyncio.sleep(5)
            finally:
                if self._pubsub:
                    await self._pubsub.close()
                if self._redis:
                    await self._redis.close()

    def _on_message(self, payload_json):
        """Callback for Redis messages."""
        try:
            data = json.loads(payload_json)
            user_id = data.get("user_id")
            event_type = data.get("event")
            payload = data.get("payload")
            target_device = data.get("target_device")

            if not user_id:
                return

            # Distribute to local queues for this user
            if user_id in self.user_connections:
                queue_data = {"event": event_type, "payload": payload}
                for conn in list(self.user_connections[user_id]):
                    if target_device is None or conn.device_name == target_device:
                        conn.queue.put_nowait(queue_data)
        except Exception as e:
            logger.error(f"❌ SSE: Error processing Redis message: {e}")

    async def generator(self, user_id: int, device_name: Optional[str] = None):
        # Ensure we are listening to Redis
        await self._ensure_listening()

        q = asyncio.Queue()
        conn = Connection(q, device_name)
        
        if user_id not in self.user_connections:
            self.user_connections[user_id] = set()
        self.user_connections[user_id].add(conn)
        
        try:
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30.0)
                    event_type = data.get("event", "message")
                    payload = data.get("payload", {})
                    yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if user_id in self.user_connections:
                self.user_connections[user_id].remove(conn)
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]

    async def broadcast_to_user(self, user_id: int, payload: dict, event: str = "file_available", target_device: Optional[str] = None):
        """
        Sends an event to all devices of a user across all worker processes via Redis PUBLISH.
        """
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
            
            notification_data = {
                "user_id": user_id,
                "event": event,
                "payload": payload,
                "target_device": target_device
            }
            
            payload_str = json.dumps(notification_data)
            await r.publish('vaultsync_events', payload_str)
            await r.close()
            logger.debug(f"✅ SSE: Published to user {user_id} via Redis")
        except Exception as e:
            logger.error(f"❌ SSE: Redis publish failed: {e}")
            # Fallback to local broadcast
            data = {"event": event, "payload": payload}
            if user_id in self.user_connections:
                for conn in list(self.user_connections[user_id]):
                    if target_device is None or conn.device_name == target_device:
                        await conn.queue.put(data)

    async def broadcast_all(self, payload: dict, event: str = "file_available"):
        """
        Broadcasts to everyone via Redis.
        """
        for user_id in list(self.user_connections.keys()):
            await self.broadcast_to_user(user_id, payload, event)

event_notifier = EventNotifier()
