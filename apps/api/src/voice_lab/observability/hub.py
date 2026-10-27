import asyncio
from collections import defaultdict
from typing import Any


class EventHub:
    """In-process fan-out of live events to WebSocket subscribers, keyed by call id.

    Single-process by design (this is a lab). A multi-worker deployment would
    replace this with Postgres LISTEN/NOTIFY or Redis pub/sub behind the same API.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, key: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers[key].add(queue)
        return queue

    def unsubscribe(self, key: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers[key].discard(queue)
        if not self._subscribers[key]:
            del self._subscribers[key]

    def publish(self, key: str, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(key, ())):
            if queue.full():
                queue.get_nowait()  # drop oldest rather than block the audio loop
            queue.put_nowait(message)


hub = EventHub()
