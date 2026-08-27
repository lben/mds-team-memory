"""Server-push channel for notifications.

Request handlers are synchronous, so they cannot touch a websocket directly.
They record which profiles need waking on the SQLAlchemy session, and a
single after-commit hook publishes once the transaction actually lands — a
notification that gets rolled back must never reach a browser.

The payload is only a signal; the browser then fetches /api/notifications, so
the list it renders always comes from the database rather than from whatever
happened to be in flight.
"""

import asyncio
from collections import defaultdict

from sqlalchemy import event
from sqlalchemy.orm import Session

PENDING_KEY = "live_notify_profiles"


class NotificationHub:
    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def register(self, profile_id: str, queue: asyncio.Queue) -> None:
        self._loop = asyncio.get_running_loop()
        self._queues[profile_id].add(queue)

    def unregister(self, profile_id: str, queue: asyncio.Queue) -> None:
        listeners = self._queues.get(profile_id)
        if not listeners:
            return
        listeners.discard(queue)
        if not listeners:
            self._queues.pop(profile_id, None)

    def listeners(self, profile_id: str) -> int:
        return len(self._queues.get(profile_id, ()))

    def publish(self, profile_id: str) -> None:
        """Wake a profile's open tabs. Safe to call from a worker thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for queue in list(self._queues.get(profile_id, ())):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "notifications"})
            except RuntimeError:
                pass  # loop shut down mid-publish


hub = NotificationHub()


def queue_wake(db: Session, profile_id: str) -> None:
    db.info.setdefault(PENDING_KEY, set()).add(profile_id)


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    for profile_id in session.info.pop(PENDING_KEY, set()):
        hub.publish(profile_id)


@event.listens_for(Session, "after_rollback")
def _discard_after_rollback(session: Session) -> None:
    session.info.pop(PENDING_KEY, None)
