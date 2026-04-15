import queue
import threading
import time
from collections import defaultdict
from typing import Any, Dict, Optional

from loguru import logger


class ChatEventHub:
    """进程内聊天事件中心，按 user_id 广播聊天消息。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers = defaultdict(set)

    def subscribe(self, user_id: int, maxsize: int = 200):
        subscriber = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers[user_id].add(subscriber)
        return subscriber

    def unsubscribe(self, user_id: int, subscriber):
        with self._lock:
            subscribers = self._subscribers.get(user_id)
            if not subscribers:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscribers.pop(user_id, None)

    def publish(self, user_id: int, event: Dict[str, Any]):
        with self._lock:
            subscribers = list(self._subscribers.get(user_id, set()))

        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    logger.warning(f"聊天事件队列仍然已满，丢弃事件: user_id={user_id}")


chat_event_hub = ChatEventHub()


def publish_chat_message(cookie_id: str, message_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """发布聊天消息事件到对应的系统用户。"""
    from db_manager import db_manager

    cookie_info = db_manager.get_cookie_details(cookie_id)
    user_id = cookie_info.get('user_id') if cookie_info else None
    if user_id is None:
        return None

    event = {
        "type": "chat.message",
        "timestamp": int(time.time() * 1000),
        "cookie_id": cookie_id,
        "data": message_data,
    }
    chat_event_hub.publish(user_id, event)
    return event
