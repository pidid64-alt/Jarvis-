"""Шина событий: ядро сообщает интерфейсам, что происходит.

GUI/CLI подписываются и получают: смену состояния (слушает/думает/отвечает),
новые реплики, изменения в навыках, ошибки. Ядро при этом ничего не знает
о том, кто именно слушает, — это и позволяет менять интерфейс без правок ядра.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

Subscriber = Callable[["Event"], None]


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "ts": self.ts, "seq": self.seq, **self.payload}


class EventBus:
    """Потокобезопасная шина с историей последних событий."""

    def __init__(self, history: int = 200):
        self._subscribers: list[Subscriber] = []
        self._history: deque[Event] = deque(maxlen=history)
        self._lock = threading.RLock()
        self._seq = 0

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)
        return lambda: self.unsubscribe(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def publish(self, event_type: str, **payload: Any) -> Event:
        with self._lock:
            self._seq += 1
            event = Event(type=event_type, payload=payload, seq=self._seq)
            self._history.append(event)
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:  # подписчик не должен ломать ядро
                continue
        return event

    def recent(self, after_seq: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = [event for event in self._history if event.seq > after_seq]
        return [event.to_dict() for event in items[-limit:]]
