import asyncio
import json
import re
import time
import base64
import hashlib
import os
import random
import secrets
import threading
from datetime import datetime
from enum import Enum
from urllib.parse import parse_qs, urlparse
from loguru import logger
import websockets
from utils.xianyu_utils import (
    decrypt, generate_mid, generate_uuid, trans_cookies,
    generate_device_id, generate_sign
)
from config import (
    WEBSOCKET_URL, HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT,
    TOKEN_REFRESH_INTERVAL, TOKEN_RETRY_INTERVAL,
    SESSION_KEEPALIVE_INTERVAL, SESSION_KEEPALIVE_RETRY_INTERVAL, COOKIES_STR,
    LOG_CONFIG, AUTO_REPLY, DEFAULT_HEADERS, WEBSOCKET_HEADERS,
    APP_CONFIG, API_ENDPOINTS, YIFAN_API, RISK_CONTROL
)
# from app.logging_config import setup_logging  # 已移除，模块不存在
import sys
import aiohttp
from collections import defaultdict, deque
from typing import Any, Dict, Optional, Tuple
from db_manager import db_manager
from utils.notification_dispatcher import (
    build_face_verify_notification,
    dispatch_account_notifications,
    format_notification_template,
    get_notification_template_text,
    guess_verification_type,
    render_notification_template,
)
from utils.item_pagination import normalize_item_list_page_size


MANUAL_VERIFICATION_CONTEXTS = {
    'manual_password_login',
    'manual_cookie_refresh',
    'manual_refresh',
}


DELIVERY_BATCH_MAX_UNITS = 10
DELIVERY_BATCH_MAX_CHARS = 1200
PROTECTED_SESSION_COOKIE_FIELDS = (
    'unb',
    'sgcookie',
    'cookie2',
    '_m_h5_tk',
    '_m_h5_tk_enc',
    't',
    'cna',
    'havana_lgc2_77',
    '_tb_token_',
)
REQUIRED_SESSION_COOKIE_FIELDS = (
    'unb',
    'sgcookie',
    'cookie2',
    '_m_h5_tk',
    '_m_h5_tk_enc',
    't',
    'cna',
)

# 滑块验证补丁已废弃，使用集成的 Playwright 登录方法
# 不再需要猴子补丁，所有功能已集成到 XianyuSliderStealth 类中


# ============ Docker环境兼容工具 ============
class _DummyChildWatcher:
    """Docker环境下的虚拟子进程监视器"""
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def is_active(self): return True
    def add_child_handler(self, *args, **kwargs): pass
    def remove_child_handler(self, *args, **kwargs): pass
    def attach_loop(self, *args, **kwargs): pass
    def close(self): pass
    def __del__(self): pass


class _DockerEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    """Docker环境下的自定义事件循环策略"""
    def get_child_watcher(self):
        return _DummyChildWatcher()


def _is_docker_env() -> bool:
    """检测是否在Docker环境中运行"""
    return bool(os.getenv('DOCKER_ENV') or os.path.exists('/.dockerenv'))


async def _start_playwright_safe(cookie_id: str = "default"):
    """安全启动Playwright，兼容Docker环境
    
    Args:
        cookie_id: 用于日志标识的账号ID
        
    Returns:
        playwright实例，失败返回None
    """
    from playwright.async_api import async_playwright
    
    is_docker = _is_docker_env()
    old_policy = None
    
    if is_docker:
        logger.warning(f"【{cookie_id}】检测到Docker环境，应用asyncio修复")
        old_policy = asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy(_DockerEventLoopPolicy())
    
    try:
        playwright = await asyncio.wait_for(
            async_playwright().start(),
            timeout=30.0
        )
        if is_docker:
            logger.warning(f"【{cookie_id}】Docker环境下Playwright启动成功")
        return playwright
    except asyncio.TimeoutError:
        logger.error(f"【{cookie_id}】Playwright启动超时")
        return None
    finally:
        if old_policy:
            asyncio.set_event_loop_policy(old_policy)


class ConnectionState(Enum):
    """WebSocket连接状态枚举"""
    DISCONNECTED = "disconnected"  # 未连接
    CONNECTING = "connecting"  # 连接中
    CONNECTED = "connected"  # 已连接
    RECONNECTING = "reconnecting"  # 重连中
    FAILED = "failed"  # 连接失败
    CLOSED = "closed"  # 已关闭


class InitAuthError(Exception):
    """WebSocket 已建立，但初始化鉴权失败。"""


class AutoReplyPauseManager:
    """自动回复暂停管理器"""
    def __init__(self):
        # 存储每个chat_id的暂停信息 {chat_id: pause_until_timestamp}
        self.paused_chats = {}

    def pause_chat(self, chat_id: str, cookie_id: str):
        """暂停指定chat_id的自动回复，使用账号特定的暂停时间"""
        # 获取账号特定的暂停时间
        try:
            from db_manager import db_manager
            pause_minutes = db_manager.get_cookie_pause_duration(cookie_id)
        except Exception as e:
            logger.error(f"获取账号 {cookie_id} 暂停时间失败: {e}，使用默认10分钟")
            pause_minutes = 10

        self.pause_chat_for(chat_id, cookie_id, pause_minutes, reason="检测到手动发出消息")

    def pause_chat_for(self, chat_id: str, cookie_id: str, pause_minutes: int, reason: str = "消息过滤规则"):
        """按指定分钟数暂停自动回复。"""
        try:
            pause_minutes = int(pause_minutes or 0)
        except (TypeError, ValueError):
            pause_minutes = 0

        if pause_minutes <= 0:
            logger.info(f"【{cookie_id}】{reason}，但暂停时间为0，不暂停自动回复")
            return

        pause_until = time.time() + pause_minutes * 60
        existing_until = self.paused_chats.get(chat_id, 0)
        if existing_until and existing_until > pause_until:
            logger.info(f"【{cookie_id}】{reason}，chat_id {chat_id} 已有更长暂停时间，保持原暂停")
            return

        self.paused_chats[chat_id] = pause_until
        end_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pause_until))
        logger.info(f"【{cookie_id}】{reason}，chat_id {chat_id} 自动回复暂停{pause_minutes}分钟，恢复时间: {end_time}")

    def is_chat_paused(self, chat_id: str) -> bool:
        """检查指定chat_id是否处于暂停状态"""
        if chat_id not in self.paused_chats:
            return False

        current_time = time.time()
        pause_until = self.paused_chats[chat_id]

        if current_time >= pause_until:
            # 暂停时间已过，移除记录
            del self.paused_chats[chat_id]
            return False

        return True

    def get_remaining_pause_time(self, chat_id: str) -> int:
        """获取指定chat_id剩余暂停时间（秒）"""
        if chat_id not in self.paused_chats:
            return 0

        current_time = time.time()
        pause_until = self.paused_chats[chat_id]
        remaining = max(0, int(pause_until - current_time))

        return remaining

    def cleanup_expired_pauses(self):
        """清理已过期的暂停记录"""
        current_time = time.time()
        expired_chats = [chat_id for chat_id, pause_until in self.paused_chats.items()
                        if current_time >= pause_until]

        for chat_id in expired_chats:
            del self.paused_chats[chat_id]


# 全局暂停管理器实例
pause_manager = AutoReplyPauseManager()

def log_captcha_event(cookie_id: str, event_type: str, success: bool = None, details: str = ""):
    """
    简单记录滑块验证事件到txt文件

    Args:
        cookie_id: 账号ID
        event_type: 事件类型 (检测到/开始处理/成功/失败)
        success: 是否成功 (None表示进行中)
        details: 详细信息
    """
    try:
        log_dir = 'logs'
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, 'captcha_verification.txt')

        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        status = "成功" if success is True else "失败" if success is False else "进行中"

        log_entry = f"[{timestamp}] 【{cookie_id}】{event_type} - {status}"
        if details:
            log_entry += f" - {details}"
        log_entry += "\n"

        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)

    except Exception as e:
        logger.error(f"记录滑块验证日志失败: {e}")

# setup_logging(LOG_CONFIG)  # 已移除，模块不存在

class XianyuLive:
    # 类级别的锁字典，为每个order_id维护一个锁（用于自动发货）
    _order_locks = defaultdict(lambda: asyncio.Lock())
    # 记录锁的最后使用时间，用于清理
    _lock_usage_times = {}
    # 记录锁的持有状态和释放时间 {lock_key: {'locked': bool, 'release_time': float, 'task': asyncio.Task}}
    _lock_hold_info = {}

    # 独立的锁字典，用于订单详情获取（不使用延迟锁机制）
    _order_detail_locks = defaultdict(lambda: asyncio.Lock())
    # 记录订单详情锁的使用时间
    _order_detail_lock_times = {}

    # 商品详情缓存（24小时有效）
    _item_detail_cache = {}  # {item_id: {'detail': str, 'timestamp': float, 'access_time': float}}
    _item_detail_cache_lock = asyncio.Lock()
    _item_detail_cache_max_size = 1000  # 最大缓存1000个商品
    _item_detail_cache_ttl = 24 * 60 * 60  # 24小时TTL

    # 类级别的实例管理字典，用于API调用
    _instances = {}  # {cookie_id: XianyuLive实例}
    _instances_lock = asyncio.Lock()
    
    # 类级别的密码登录时间记录，用于防止重复登录
    _last_password_login_time = {}  # {cookie_id: timestamp}
    _password_login_cooldown = 60  # 密码登录冷却时间：60秒
    _password_login_failure_backoff = {}  # {cookie_id: {'until': float, 'reason': str, 'seconds': int}}

    # 手动刷新状态：用于避免手动刷新与自动滑块/自动Cookie刷新互相踩踏
    _manual_refresh_state = {}  # {cookie_id: {'source': str, 'phase': str, 'started_at': float, 'previous_cookie_refresh_enabled': Optional[bool]}}
    _manual_refresh_lock = threading.Lock()
    _manual_refresh_handoff_ttl = 120  # 刷新交接恢复窗口（秒）

    # 认证恢复锁：同一账号同一时刻只允许一条密码登录恢复链路执行
    _auth_recovery_locks = {}  # {cookie_id: {'owner': str, 'acquired_at': float, 'expires_at': float}}
    _auth_recovery_lock = threading.Lock()
    _auth_recovery_lock_ttl = 240

    # 通用预热 token：用于手动刷新/恢复预检成功后的新实例首轮复用
    _auth_prewarmed_tokens = {}  # {cookie_id: {'token': str, 'timestamp': float, 'source': str}}
    _auth_prewarmed_token_ttl = 180

    # 初始化鉴权失败熔断：区分于 WebSocket 建链失败，避免重连风暴
    _init_auth_failure_state = {}  # {cookie_id: {'count': int, 'window_started_at': float, 'last_failure_at': float, 'last_reason': str, 'circuit_until': float}}
    _init_auth_failure_lock = threading.Lock()
    _init_auth_failure_window = 60
    _init_auth_failure_threshold = 3
    _init_auth_cooldown = 60

    # 扫码登录后的短期缓冲状态：首轮 token 刷新命中风控时，先做浏览器侧稳定化再决定是否上滑块
    _qr_login_grace_state = {}  # {cookie_id: {'timestamp': float, 'captcha_buffer_used': bool, 'browser_stabilized': bool}}
    _qr_login_grace_ttl = max(300, int(RISK_CONTROL.get('qr_login_grace_minutes', 15) or 15) * 60)

    @classmethod
    def _cleanup_auth_prewarmed_tokens(cls):
        """清理过期的通用预热 token 缓存。"""
        now = time.time()
        expired_cookie_ids = [
            cookie_id
            for cookie_id, token_info in cls._auth_prewarmed_tokens.items()
            if now - token_info.get('timestamp', 0) > cls._auth_prewarmed_token_ttl
        ]
        for cookie_id in expired_cookie_ids:
            cls._auth_prewarmed_tokens.pop(cookie_id, None)

    @classmethod
    def cache_auth_prewarmed_token(cls, cookie_id: str, token: str, source: str = 'generic_auth'):
        """缓存预检成功后的 token，供新实例首轮初始化复用。"""
        if not cookie_id or not token:
            return
        cls._cleanup_auth_prewarmed_tokens()
        cls._auth_prewarmed_tokens[cookie_id] = {
            'token': token,
            'timestamp': time.time(),
            'source': source,
        }

    @classmethod
    def pop_auth_prewarmed_token(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        """弹出通用预热 token，过期则忽略。"""
        if not cookie_id:
            return None
        cls._cleanup_auth_prewarmed_tokens()
        token_info = cls._auth_prewarmed_tokens.pop(cookie_id, None)
        if not token_info:
            return None
        if time.time() - token_info.get('timestamp', 0) > cls._auth_prewarmed_token_ttl:
            return None
        return token_info

    @classmethod
    def clear_auth_prewarmed_token(cls, cookie_id: str):
        if not cookie_id:
            return
        cls._auth_prewarmed_tokens.pop(cookie_id, None)

    @classmethod
    def _cleanup_manual_refresh_state(cls):
        """清理过期的刷新交接恢复状态。"""
        now = time.time()
        expired_cookie_ids = []
        with cls._manual_refresh_lock:
            for cookie_id, state in cls._manual_refresh_state.items():
                if state.get('phase') != 'handoff_recovery':
                    continue
                expires_at = state.get('expires_at', 0)
                if expires_at and now > expires_at:
                    expired_cookie_ids.append(cookie_id)

            for cookie_id in expired_cookie_ids:
                cls._manual_refresh_state.pop(cookie_id, None)

        for cookie_id in expired_cookie_ids:
            logger.warning(f"【{cookie_id}】刷新交接恢复状态已过期，自动清理")

    @classmethod
    def get_manual_refresh_state(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        if not cookie_id:
            return None
        cls._cleanup_manual_refresh_state()
        with cls._manual_refresh_lock:
            state = cls._manual_refresh_state.get(cookie_id)
            return dict(state) if state else None

    @classmethod
    def mark_manual_refresh_handoff(cls, cookie_id: str, source: str = 'manual_refresh_handoff', ttl: int = None) -> Dict[str, Any]:
        """将手动刷新状态切换为交接恢复窗口，允许新实例做初始化恢复。"""
        if not cookie_id:
            return {'updated': False, 'reason': 'empty_cookie_id'}

        live_instance = cls.get_instance(cookie_id)
        previous_cookie_refresh_enabled = None
        if live_instance is not None:
            previous_cookie_refresh_enabled = live_instance.cookie_refresh_enabled

        now = time.time()
        expires_at = now + (ttl or cls._manual_refresh_handoff_ttl)
        with cls._manual_refresh_lock:
            state = cls._manual_refresh_state.get(cookie_id) or {}
            state.update({
                'source': source,
                'phase': 'handoff_recovery',
                'started_at': state.get('started_at', now),
                'updated_at': now,
                'handoff_started_at': now,
                'expires_at': expires_at,
                'slider_failed_bypass_used': state.get('slider_failed_bypass_used', False),
                'previous_cookie_refresh_enabled': state.get('previous_cookie_refresh_enabled', previous_cookie_refresh_enabled),
            })
            cls._manual_refresh_state[cookie_id] = state

        logger.warning(
            f"【{cookie_id}】已进入刷新交接恢复窗口，允许新实例执行初始化恢复 (有效期 {int(expires_at - now)} 秒)"
        )
        return {'updated': True, 'phase': 'handoff_recovery', 'expires_at': expires_at}

    @classmethod
    def consume_manual_refresh_slider_failed_bypass(cls, cookie_id: str) -> bool:
        if not cookie_id:
            return False
        cls._cleanup_manual_refresh_state()
        with cls._manual_refresh_lock:
            state = cls._manual_refresh_state.get(cookie_id)
            if not state or state.get('phase') != 'handoff_recovery':
                return False
            if state.get('slider_failed_bypass_used'):
                return False
            state['slider_failed_bypass_used'] = True
            state['updated_at'] = time.time()
            return True

    @classmethod
    def _cleanup_auth_recovery_locks(cls):
        now = time.time()
        expired_cookie_ids = []
        with cls._auth_recovery_lock:
            for cookie_id, state in cls._auth_recovery_locks.items():
                if now > state.get('expires_at', 0):
                    expired_cookie_ids.append(cookie_id)
            for cookie_id in expired_cookie_ids:
                cls._auth_recovery_locks.pop(cookie_id, None)

    @classmethod
    def acquire_auth_recovery_lock(cls, cookie_id: str, owner: str, ttl: int = None) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if not cookie_id or not owner:
            return False, None
        cls._cleanup_auth_recovery_locks()
        now = time.time()
        expires_at = now + (ttl or cls._auth_recovery_lock_ttl)
        with cls._auth_recovery_lock:
            existing = cls._auth_recovery_locks.get(cookie_id)
            if existing and existing.get('owner') != owner and now <= existing.get('expires_at', 0):
                return False, dict(existing)
            cls._auth_recovery_locks[cookie_id] = {
                'owner': owner,
                'acquired_at': now,
                'expires_at': expires_at,
            }
        return True, None

    @classmethod
    def get_auth_recovery_lock_state(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        if not cookie_id:
            return None
        cls._cleanup_auth_recovery_locks()
        with cls._auth_recovery_lock:
            state = cls._auth_recovery_locks.get(cookie_id)
            return dict(state) if state else None

    @classmethod
    def release_auth_recovery_lock(cls, cookie_id: str, owner: str = None):
        if not cookie_id:
            return
        with cls._auth_recovery_lock:
            existing = cls._auth_recovery_locks.get(cookie_id)
            if not existing:
                return
            if owner and existing.get('owner') != owner:
                return
            cls._auth_recovery_locks.pop(cookie_id, None)

    @classmethod
    def get_init_auth_failure_state(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        if not cookie_id:
            return None
        with cls._init_auth_failure_lock:
            state = cls._init_auth_failure_state.get(cookie_id)
            if not state:
                return None
            if state.get('circuit_until') and time.time() > state.get('circuit_until', 0):
                state = {
                    'count': 0,
                    'window_started_at': 0,
                    'last_failure_at': state.get('last_failure_at', 0),
                    'last_reason': state.get('last_reason'),
                    'circuit_until': 0,
                }
                cls._init_auth_failure_state[cookie_id] = state
            return dict(state)

    @classmethod
    def record_init_auth_failure(cls, cookie_id: str, reason: str) -> Dict[str, Any]:
        now = time.time()
        with cls._init_auth_failure_lock:
            state = cls._init_auth_failure_state.get(cookie_id) or {
                'count': 0,
                'window_started_at': now,
                'last_failure_at': 0,
                'last_reason': '',
                'circuit_until': 0,
            }
            window_started_at = state.get('window_started_at', 0)
            if not window_started_at or (now - window_started_at) > cls._init_auth_failure_window:
                state['count'] = 0
                state['window_started_at'] = now
                state['circuit_until'] = 0

            state['count'] = int(state.get('count', 0)) + 1
            state['last_failure_at'] = now
            state['last_reason'] = str(reason or '')
            if state['count'] >= cls._init_auth_failure_threshold:
                state['circuit_until'] = now + cls._init_auth_cooldown

            cls._init_auth_failure_state[cookie_id] = state
            return dict(state)

    @classmethod
    def clear_init_auth_failure_state(cls, cookie_id: str):
        if not cookie_id:
            return
        with cls._init_auth_failure_lock:
            cls._init_auth_failure_state.pop(cookie_id, None)

    @classmethod
    def _cleanup_qr_login_grace_state(cls):
        """清理过期的扫码登录缓冲状态"""
        now = time.time()
        expired_cookie_ids = [
            cookie_id
            for cookie_id, state in cls._qr_login_grace_state.items()
            if now - state.get('timestamp', 0) > cls._qr_login_grace_ttl
        ]
        for cookie_id in expired_cookie_ids:
            cls._qr_login_grace_state.pop(cookie_id, None)

    @classmethod
    def mark_qr_login_grace(cls, cookie_id: str, **extra_state):
        """标记账号刚完成扫码登录，后续首轮 token 刷新可走更保守的缓冲分支"""
        if not cookie_id:
            return
        cls._cleanup_qr_login_grace_state()
        state = {
            'timestamp': time.time(),
            'captcha_buffer_used': False,
            'browser_stabilized': False,
        }
        state.update(extra_state)
        cls._qr_login_grace_state[cookie_id] = state

    @classmethod
    def get_qr_login_grace_ttl_seconds(cls) -> int:
        return max(300, int(RISK_CONTROL.get('qr_login_grace_minutes', 15) or 15) * 60)

    @classmethod
    def get_qr_login_grace(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        """获取扫码登录缓冲状态，过期则自动忽略"""
        if not cookie_id:
            return None
        cls._cleanup_qr_login_grace_state()
        state = cls._qr_login_grace_state.get(cookie_id)
        if not state:
            return None
        if time.time() - state.get('timestamp', 0) > cls._qr_login_grace_ttl:
            cls._qr_login_grace_state.pop(cookie_id, None)
            return None
        return state

    @classmethod
    def update_qr_login_grace(cls, cookie_id: str, **updates):
        """更新扫码登录缓冲状态"""
        state = cls.get_qr_login_grace(cookie_id)
        if not state:
            return None
        state.update(updates)
        cls._qr_login_grace_state[cookie_id] = state
        return state

    @classmethod
    def clear_qr_login_grace(cls, cookie_id: str):
        """清理指定账号的扫码登录缓冲状态"""
        if not cookie_id:
            return
        cls._qr_login_grace_state.pop(cookie_id, None)

    def _get_qr_login_grace_until(self) -> int:
        try:
            account_info = db_manager.get_cookie_details(self.cookie_id) or {}
            return int(account_info.get('qr_login_grace_until') or 0)
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】读取扫码稳定期截止时间失败: {self._safe_str(e)}")
            return 0

    def _get_qr_login_grace_remaining_seconds(self, current_time: Optional[float] = None) -> int:
        current_time = current_time or time.time()
        grace_until = self._get_qr_login_grace_until()
        return max(0, int(grace_until - current_time))

    def _is_in_qr_login_grace_period(self, current_time: Optional[float] = None) -> bool:
        return self._get_qr_login_grace_remaining_seconds(current_time) > 0

    def _set_qr_login_grace_until(self, grace_until: int) -> None:
        db_manager.set_cookie_qr_login_grace_until(self.cookie_id, int(grace_until or 0))

    def _clear_qr_login_grace_period(self) -> None:
        self.clear_qr_login_grace(self.cookie_id)
        self._set_qr_login_grace_until(0)

    def _enter_qr_login_grace_period(self, *, stage: str = 'qr_login_success') -> int:
        now = time.time()
        grace_until = int(now + self.get_qr_login_grace_ttl_seconds())
        self.mark_qr_login_grace(self.cookie_id, stage=stage, entered_at=now)
        self._set_qr_login_grace_until(grace_until)
        return grace_until

    def _consume_qr_login_grace_period_if_expired(self, current_time: Optional[float] = None) -> bool:
        current_time = current_time or time.time()
        grace_until = self._get_qr_login_grace_until()
        if not grace_until:
            return False
        if current_time < grace_until:
            return False
        self._clear_qr_login_grace_period()
        logger.info(f"【{self.cookie_id}】扫码登录稳定期已结束，恢复自动认证链路")
        return True

    def _should_defer_auth_recovery_for_qr_grace(self, current_time: Optional[float] = None) -> bool:
        current_time = current_time or time.time()
        self._consume_qr_login_grace_period_if_expired(current_time)
        remaining = self._get_qr_login_grace_remaining_seconds(current_time)
        if remaining <= 0:
            return False
        self.last_token_refresh_status = "qr_login_grace_wait"
        self.last_token_refresh_error_message = f"扫码登录稳定期中，剩余{remaining}秒"
        logger.warning(f"【{self.cookie_id}】扫码登录稳定期中，暂缓自动认证恢复，还需等待 {remaining} 秒")
        return True

    @classmethod
    def _cleanup_password_login_failure_backoff(cls):
        """清理已过期的密码登录失败退避状态"""
        now = time.time()
        expired_cookie_ids = [
            cookie_id
            for cookie_id, state in cls._password_login_failure_backoff.items()
            if now >= state.get('until', 0)
        ]
        for cookie_id in expired_cookie_ids:
            cls._password_login_failure_backoff.pop(cookie_id, None)

    @classmethod
    def get_password_login_failure_backoff(cls, cookie_id: str) -> Optional[Dict[str, Any]]:
        """获取当前账号的密码登录失败退避状态"""
        if not cookie_id:
            return None
        cls._cleanup_password_login_failure_backoff()
        return cls._password_login_failure_backoff.get(cookie_id)

    @classmethod
    def clear_password_login_failure_backoff(cls, cookie_id: str):
        """清理指定账号的密码登录失败退避状态"""
        if not cookie_id:
            return
        cls._password_login_failure_backoff.pop(cookie_id, None)

    @classmethod
    def set_password_login_failure_backoff(cls, cookie_id: str, reason: str, seconds: int):
        """设置密码登录失败后的退避时间"""
        if not cookie_id or seconds <= 0:
            return
        previous_state = cls._password_login_failure_backoff.get(cookie_id) or {}
        previous_reason = previous_state.get('reason')
        previous_count = int(previous_state.get('consecutive_count', 0) or 0)
        consecutive_count = previous_count + 1 if previous_reason == reason else 1
        escalation_factor = float(RISK_CONTROL.get('backoff_escalation_factor', 1.5) or 1.5)
        max_cap = max(seconds, int(RISK_CONTROL.get('backoff_max_cap_seconds', 3600) or 3600))
        actual_seconds = int(round(min(seconds * (escalation_factor ** max(0, consecutive_count - 1)), max_cap)))
        actual_seconds = max(seconds, actual_seconds)
        now = time.time()
        cls._password_login_failure_backoff[cookie_id] = {
            'until': now + actual_seconds,
            'reason': reason,
            'seconds': actual_seconds,
            'base_seconds': seconds,
            'consecutive_count': consecutive_count,
            'created_at': now,
        }

    @staticmethod
    def _is_counted_password_login_failure_reason(reason: str) -> bool:
        return str(reason or '').strip() in {'slider_failed', 'risk_control'}

    def _get_night_mode_settings(self) -> Dict[str, Any]:
        from config import config

        def _setting_value(system_key: str, config_key: str, default: Any) -> Any:
            raw_value = db_manager.get_system_setting(system_key)
            if raw_value is None:
                return RISK_CONTROL.get(config_key, config.get(f'RISK_CONTROL.{config_key}', default))
            return raw_value

        enabled_raw = _setting_value('risk_control_night_mode_enabled', 'night_mode_enabled', False)
        start_raw = _setting_value('risk_control_night_start_hour', 'night_start_hour', 1)
        end_raw = _setting_value('risk_control_night_end_hour', 'night_end_hour', 6)

        def _to_bool(value: Any, default: bool = False) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

        def _to_hour(value: Any, default: int) -> int:
            try:
                return max(0, min(23, int(value)))
            except (TypeError, ValueError):
                return default

        return {
            'enabled': _to_bool(enabled_raw, False),
            'start_hour': _to_hour(start_raw, 1),
            'end_hour': _to_hour(end_raw, 6),
        }

    def _is_in_night_mode_window(self, local_hour: Optional[int] = None) -> bool:
        settings = self._get_night_mode_settings()
        if not settings.get('enabled'):
            return False

        current_hour = datetime.now().hour if local_hour is None else int(local_hour)
        start_hour = int(settings.get('start_hour', 1))
        end_hour = int(settings.get('end_hour', 6))
        if start_hour == end_hour:
            return True
        if start_hour < end_hour:
            return start_hour <= current_hour < end_hour
        return current_hour >= start_hour or current_hour < end_hour

    def _get_effective_keepalive_interval(self) -> int:
        base_interval = max(60, int(self.session_keepalive_interval or 600))
        if not self._is_in_night_mode_window():
            return base_interval
        multiplier = max(1, int(RISK_CONTROL.get('night_keepalive_multiplier', 3) or 3))
        return base_interval * multiplier

    def _get_effective_cookie_refresh_interval(self) -> int:
        base_interval = max(60, int(self.cookie_refresh_interval or 10800))
        if not self._is_in_night_mode_window():
            return base_interval
        multiplier = max(1, int(RISK_CONTROL.get('night_cookie_refresh_multiplier', 2) or 2))
        return base_interval * multiplier

    def _compute_token_retry_wait_seconds(self, current_time: Optional[float] = None) -> int:
        current_time = current_time or time.time()
        min_wait = max(60, int(RISK_CONTROL.get('token_retry_min_wait_seconds', 180) or 180))
        backoff = self._get_active_password_login_failure_backoff(current_time)
        if backoff:
            remaining = max(0, int(backoff.get('remaining_time', 0) or 0))
            return max(min_wait, remaining + 60)
        return max(min_wait, int(self.token_retry_interval or min_wait))

    async def _protect_account_for_consecutive_failures(self, backoff_state: Optional[Dict[str, Any]] = None) -> bool:
        state = backoff_state or self._get_active_password_login_failure_backoff()
        if not state:
            return False

        reason = str(state.get('reason') or '').strip()
        if not self._is_counted_password_login_failure_reason(reason):
            return False

        threshold = max(1, int(RISK_CONTROL.get('consecutive_failure_protection_threshold', 5) or 5))
        consecutive_count = int(state.get('consecutive_count', 0) or 0)
        if consecutive_count < threshold:
            return False

        pause_reason = f"连续{consecutive_count}次{reason}"
        await self._apply_account_pause_state(
            refresh_status="consecutive_failure_protected",
            status_note="连续风控保护中",
            error_message=f"检测到{pause_reason}，已暂停账号等待人工介入",
            connection_message="连续风控失败，已自动暂停账号",
            note_error_prefix="写入连续失败保护状态文案失败",
            status_error_prefix="持久化连续失败保护状态失败",
            memory_error_prefix="更新连续失败内存状态失败",
        )
        await self.send_account_paused_notification(
            status_note="连续风控保护中",
            pause_reason=pause_reason,
            error_message=f"账号在自动恢复过程中已连续触发 {consecutive_count} 次 {reason}，系统已暂停自动恢复以避免继续放大风控。",
            verification_url='',
        )
        await self._request_stop_after_account_pause("连续风控失败触发账号保护")
        return True

    def _get_active_password_login_failure_backoff(self, current_time: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """获取仍在生效的密码登录失败退避状态，并处理可忽略的旧滑块退避。"""
        current_time = current_time or time.time()
        failure_backoff = XianyuLive.get_password_login_failure_backoff(self.cookie_id)
        if not failure_backoff:
            return None

        remaining_time = failure_backoff.get('until', 0) - current_time
        if remaining_time <= 0:
            return None

        backoff_reason = failure_backoff.get('reason', 'unknown')
        if backoff_reason == 'slider_failed' and (
            self._has_recent_slider_success() or self.consume_manual_refresh_slider_failed_bypass(self.cookie_id)
        ):
            logger.warning(
                f"【{self.cookie_id}】检测到最近刚通过滑块或处于刷新交接恢复窗口，忽略一次旧的 slider_failed 退避并继续尝试恢复"
            )
            XianyuLive.clear_password_login_failure_backoff(self.cookie_id)
            return None

        state = dict(failure_backoff)
        state['reason'] = backoff_reason
        state['remaining_time'] = remaining_time
        return state

    def _should_skip_token_refresh_for_login_backoff(self, current_time: Optional[float] = None) -> bool:
        """在需要人工介入或明确退避期间，直接跳过 token 预检，避免重复打到平台。"""
        current_time = current_time or time.time()
        failure_backoff = self._get_active_password_login_failure_backoff(current_time)
        if not failure_backoff:
            return False

        backoff_reason = failure_backoff.get('reason', 'unknown')
        if backoff_reason not in {'slider_failed', 'verification_required', 'credentials', 'risk_control', 'browser_crash'}:
            return False

        remaining_time = failure_backoff.get('remaining_time', 0.0)
        should_log = (
            self.last_token_refresh_status != "password_login_backoff_wait" or
            (current_time - getattr(self, 'last_password_login_backoff_log_time', 0.0)) >= 30
        )
        if should_log:
            logger.warning(
                f"【{self.cookie_id}】密码登录失败退避中（原因: {backoff_reason}），"
                f"直接跳过本次token刷新，还需等待 {remaining_time:.1f} 秒"
            )
            self.last_password_login_backoff_log_time = current_time

        self.last_token_refresh_status = "password_login_backoff_wait"
        self.last_token_refresh_error_message = f"密码登录失败退避中，剩余{remaining_time:.1f}秒"
        return True

    @staticmethod
    def classify_password_login_failure(error_message: str) -> Tuple[str, int]:
        """按失败类型返回(原因标签, 退避秒数)"""
        message = (error_message or "").lower()
        if any(keyword in message for keyword in ["账号密码错误", "账密错误", "用户名或密码错误", "密码错误"]):
            return "credentials", 1800
        if any(
            keyword in message for keyword in [
                "短信验证",
                "二维码验证",
                "人脸验证",
                "身份验证",
                "等待短信验证超时",
                "等待二维码验证超时",
                "等待人脸验证超时",
                "等待身份验证超时",
            ]
        ):
            return "verification_required", 900
        if any(keyword in message for keyword in ["前置滑块", "风控", "拦截", "框体错误", "点击框体重试", "账号存在风险", "闲鱼客户端登录"]):
            return "risk_control", 900
        if any(keyword in message for keyword in ["滑块验证失败", "未找到滑块容器"]):
            return "slider_failed", 600
        if any(
            keyword in message for keyword in [
                "未找到登录表单",
                "未找到登录iframe",
                "session过期且清理会话状态后未找到登录表单",
                "session验证异常且清理会话状态后未找到登录表单",
            ]
        ):
            return "login_form_missing", 90
        if any(keyword in message for keyword in [
            "页面会话已失效",
            "target page, context or browser has been closed",
            "page crashed",
            "target crashed",
            "browser has been closed",
            "browser context has been closed",
        ]):
            return "browser_crash", 180
        if any(keyword in message for keyword in ["页面会话已失效", "target page, context or browser has been closed"]):
            return "unknown", 180
        if any(keyword in message for keyword in ["网络", "timeout", "cannot connect", "连接", "dns", "ssl"]):
            return "network", 180
        return "unknown", 300

    @staticmethod
    def _is_account_risk_login_error(error_message: str) -> bool:
        """识别需要立即停账号保护的高风险登录提示。"""
        message = str(error_message or "").strip()
        if not message:
            return False
        return "账号存在风险" in message and ("闲鱼客户端登录" in message or "按提示操作" in message)

    @staticmethod
    def _is_account_pause_status(status: str) -> bool:
        return status in {"account_risk_protected", "manual_verification_required"}

    @staticmethod
    def _should_pause_for_manual_verification(verification_type: str, verification_context: str) -> bool:
        """判断人工介入提示是否应禁用账号。

        普通扫码登录页（login_page / mini_login）只是登录态丢失后的正常登录入口，
        不能当作风控/身份校验来暂停账号；真正的人脸/短信/二维码身份验证仍按自动流程保护。
        """
        if verification_context in MANUAL_VERIFICATION_CONTEXTS:
            return False
        if verification_type == 'login_page':
            return False
        return True

    async def _apply_account_pause_state(
        self,
        *,
        refresh_status: str,
        status_note: str,
        error_message: str,
        connection_message: str,
        note_error_prefix: str,
        status_error_prefix: str,
        memory_error_prefix: str,
    ) -> None:
        self.current_token = None
        self.last_token_refresh_status = refresh_status
        self.last_token_refresh_error_message = str(error_message or "").strip()
        XianyuLive.clear_password_login_failure_backoff(self.cookie_id)

        try:
            db_manager.update_cookie_status_note(self.cookie_id, status_note)
        except Exception as note_e:
            logger.error(f"【{self.cookie_id}】{note_error_prefix}: {self._safe_str(note_e)}")

        try:
            db_manager.save_cookie_status(self.cookie_id, False)
        except Exception as status_e:
            logger.error(f"【{self.cookie_id}】{status_error_prefix}: {self._safe_str(status_e)}")

        try:
            from cookie_manager import manager as cookie_manager_manager
            if cookie_manager_manager:
                cookie_manager_manager.cookie_status[self.cookie_id] = False
        except Exception as cm_e:
            logger.error(f"【{self.cookie_id}】{memory_error_prefix}: {self._safe_str(cm_e)}")

        self._set_connection_state(ConnectionState.FAILED, connection_message)

    async def _clear_account_pause_state(self, reason: str = "认证恢复成功") -> None:
        self.last_token_refresh_error_message = ""
        self._clear_qr_login_grace_period()

        try:
            db_manager.update_cookie_status_note(self.cookie_id, '')
        except Exception as note_e:
            logger.error(f"【{self.cookie_id}】清理账号状态文案失败: {self._safe_str(note_e)}")

        try:
            db_manager.save_cookie_status(self.cookie_id, True)
        except Exception as status_e:
            logger.error(f"【{self.cookie_id}】恢复账号启用状态失败: {self._safe_str(status_e)}")

        try:
            from cookie_manager import manager as cookie_manager_manager
            if cookie_manager_manager:
                cookie_manager_manager.cookie_status[self.cookie_id] = True
        except Exception as cm_e:
            logger.error(f"【{self.cookie_id}】恢复内存账号状态失败: {self._safe_str(cm_e)}")

        logger.info(f"【{self.cookie_id}】账号暂停状态已清理: {reason}")

    async def _request_stop_after_account_pause(self, reason: str) -> None:
        try:
            from cookie_manager import manager as cookie_manager_manager
            if not cookie_manager_manager:
                return

            current_task = asyncio.current_task()
            tracked_task = cookie_manager_manager.tasks.get(self.cookie_id)

            if tracked_task is current_task:
                cookie_manager_manager.tasks.pop(self.cookie_id, None)
                loop = asyncio.get_running_loop()

                def _cancel_current_task() -> None:
                    if current_task and not current_task.done():
                        current_task.cancel()

                loop.call_soon(_cancel_current_task)
                logger.info(f"【{self.cookie_id}】账号已暂停，当前任务将在本轮流程结束后停止: {reason}")
                return

            if tracked_task and not tracked_task.done():
                tracked_task.cancel()
                logger.info(f"【{self.cookie_id}】账号已暂停，已取消运行中的账号任务: {reason}")

            if tracked_task is not None:
                cookie_manager_manager.tasks.pop(self.cookie_id, None)
        except Exception as stop_e:
            logger.warning(f"【{self.cookie_id}】请求停止暂停账号任务失败: {self._safe_str(stop_e)}")

    async def _protect_account_from_risk_login_retry(self, error_message: str, status_note: str = "风控保护中") -> bool:
        """命中高风险登录提示后自动禁用账号，避免持续触发更强风控。"""
        message = str(error_message or "").strip()
        if not self._is_account_risk_login_error(message):
            return False

        await self._apply_account_pause_state(
            refresh_status="account_risk_protected",
            status_note=status_note,
            error_message=message,
            connection_message="检测到账号风控，已自动禁用",
            note_error_prefix="写入账号状态文案失败",
            status_error_prefix="持久化账号禁用状态失败",
            memory_error_prefix="更新内存账号状态失败",
        )
        logger.error(
            f"【{self.cookie_id}】检测到账号高风险登录提示，已自动禁用账号并标记为“{status_note}”，停止后续自动登录重试"
        )
        try:
            await self._force_websocket_reconnect("检测到账号风控，账号已自动禁用")
        except Exception as reconnect_e:
            logger.warning(f"【{self.cookie_id}】风控保护触发后关闭WebSocket失败: {self._safe_str(reconnect_e)}")
        return True

    async def _pause_account_for_manual_verification(
        self,
        verification_type: str = None,
        error_message: str = "",
        pause_account: bool = True,
        verification_context: str = 'auto_refresh',
        verification_url: str = '',
    ) -> bool:
        """检测到需要人工验证时，按上下文决定是否暂停账号。"""
        verification_type_names = {
            'face_verify': '人脸验证',
            'sms_verify': '短信验证',
            'qr_verify': '二维码验证',
            'login_page': '扫码登录',
            'unknown': '身份验证',
        }
        type_name = verification_type_names.get(verification_type, '身份验证')
        status_note = f"待{type_name}"
        message = str(error_message or f"检测到需要人工完成的{type_name}").strip()

        if not pause_account:
            if verification_type == 'login_page':
                logger.warning(
                    f"【{self.cookie_id}】检测到普通扫码登录入口({verification_context})，仅通知用户完成登录，不自动暂停账号"
                )
            else:
                logger.warning(
                    f"【{self.cookie_id}】检测到需要人工完成的{type_name}，但当前属于手动流程({verification_context})，不自动暂停账号"
                )
            return False

        await self._apply_account_pause_state(
            refresh_status="manual_verification_required",
            status_note=status_note,
            error_message=message,
            connection_message=f"检测到{type_name}，已自动暂停账号",
            note_error_prefix="写入人工验证状态文案失败",
            status_error_prefix="持久化人工验证暂停状态失败",
            memory_error_prefix="更新人工验证内存状态失败",
        )
        logger.warning(
            f"【{self.cookie_id}】检测到需要人工完成的{type_name}，已自动暂停账号并标记为“{status_note}”"
        )
        await self.send_account_paused_notification(
            status_note=status_note,
            pause_reason=type_name,
            error_message=message,
            verification_url=verification_url,
            action_hint='请先完成验证，再在账号管理中恢复或重新启动该账号。',
        )
        return True

    async def send_account_paused_notification(
        self,
        *,
        status_note: str,
        pause_reason: str,
        error_message: str,
        verification_url: str = '',
        action_hint: str = '',
    ) -> bool:
        message = render_notification_template(
            'account_paused',
            account_id=self.cookie_id,
            status_note=status_note or '已暂停',
            pause_reason=pause_reason or '未知原因',
            time=time.strftime('%Y-%m-%d %H:%M:%S'),
            error_message=error_message or '系统检测到账号需要人工处理',
            verification_url=verification_url or '无',
            action_hint=action_hint or '请尽快处理账号状态，避免自动任务长时间不可用。',
        )

        logger.info(f"【{self.cookie_id}】准备发送账号暂停通知")
        sent = await dispatch_account_notifications(
            self.cookie_id,
            message,
            title='闲鱼账号已暂停',
            notification_type='account_paused',
        )
        if sent:
            logger.info(f"【{self.cookie_id}】账号暂停通知发送成功")
        else:
            logger.warning(f"【{self.cookie_id}】账号暂停通知未发送成功")
        return sent
    
    def _safe_str(self, e):
        """安全地将异常转换为字符串"""
        try:
            return str(e)
        except Exception:
            try:
                return repr(e)
            except Exception:
                return "未知错误"

    def _mask_secret_value(self, value: str, head: int = 6, tail: int = 4) -> str:
        text = str(value or '')
        if not text:
            return ''
        if len(text) <= head + tail:
            return '***'
        return f"{text[:head]}***{text[-tail:]}"

    def _summarize_cookie_string(self, cookie_string: str) -> str:
        cookie_string = str(cookie_string or '').strip()
        if not cookie_string:
            return 'empty-cookie'

        segments = []
        for part in cookie_string.split(';'):
            part = part.strip()
            if not part:
                continue
            if '=' in part:
                key, value = part.split('=', 1)
                segments.append(f"{key.strip()}={self._mask_secret_value(value.strip(), head=4, tail=2)}")
            else:
                segments.append(self._mask_secret_value(part, head=4, tail=2))

        preview = '; '.join(segments[:6])
        if len(segments) > 6:
            preview += f"; ...(+{len(segments) - 6} fields)"
        return preview

    @staticmethod
    def _new_risk_session_id(prefix: str = 'risk') -> str:
        return f"{prefix}_{secrets.token_hex(8)}"

    def _normalize_risk_trigger_scene(self, trigger_reason: str = None, default: str = 'unknown') -> str:
        text = str(trigger_reason or '').strip()
        if not text:
            return default
        lower_text = text.lower()
        if 'token' in lower_text or 'session' in lower_text or '令牌' in text:
            return 'token_refresh'
        if 'password' in lower_text or '账密' in text or '登录' in text:
            return 'password_login'
        if 'cookie' in lower_text or '连接' in text or '失败' in text:
            return 'auto_cookie_refresh'
        return default

    def _sanitize_verification_meta(self, verification_url: str = None) -> Dict[str, Any]:
        text = str(verification_url or '').strip()
        if not text:
            return {}

        try:
            parsed = urlparse(text)
            if not parsed.scheme and not parsed.netloc:
                return {'verification_source': text[:120]}

            meta: Dict[str, Any] = {
                'verification_host': parsed.netloc or None,
                'verification_path': parsed.path or None,
            }
            query = parse_qs(parsed.query or '')
            x5secdata = query.get('x5secdata', [None])[0]
            if x5secdata:
                meta['verification_token_hash'] = hashlib.sha256(x5secdata.encode('utf-8')).hexdigest()[:16]
            action = query.get('action', [None])[0]
            if action:
                meta['verification_action'] = action
            step = query.get('x5step', [None])[0]
            if step:
                meta['verification_step'] = step
            return {key: value for key, value in meta.items() if value is not None}
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】解析验证链接失败: {self._safe_str(e)}")
            return {'verification_source': text[:120]}

    def _build_risk_event_meta(self, trigger_scene: str = None, verification_url: str = None, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        if trigger_scene:
            payload['trigger_scene'] = trigger_scene
        payload.update(self._sanitize_verification_meta(verification_url))
        if isinstance(extra, dict):
            payload.update({key: value for key, value in extra.items() if value is not None})
        return payload or None

    def _create_risk_log(
        self,
        event_type: str,
        event_description: str,
        processing_status: str = 'processing',
        processing_result: str = None,
        error_message: str = None,
        session_id: str = None,
        trigger_scene: str = None,
        result_code: str = None,
        captcha_engine: str = None,
        event_meta: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
    ) -> Optional[int]:
        try:
            return db_manager.add_risk_control_log(
                cookie_id=self.cookie_id,
                event_type=event_type,
                session_id=session_id,
                trigger_scene=trigger_scene,
                result_code=result_code,
                captcha_engine=captcha_engine,
                event_description=event_description,
                event_meta=event_meta,
                processing_result=processing_result,
                processing_status=processing_status,
                error_message=error_message,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error(f"【{self.cookie_id}】记录风控日志失败: {self._safe_str(e)}")
            return None

    def _update_risk_log(
        self,
        log_id: Optional[int],
        *,
        event_description: str = None,
        processing_status: str = None,
        processing_result: str = None,
        error_message: str = None,
        session_id: str = None,
        trigger_scene: str = None,
        result_code: str = None,
        captcha_engine: str = None,
        event_meta: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        if not log_id:
            return
        try:
            db_manager.update_risk_control_log(
                log_id=log_id,
                event_description=event_description,
                processing_status=processing_status,
                processing_result=processing_result,
                error_message=error_message,
                session_id=session_id,
                trigger_scene=trigger_scene,
                result_code=result_code,
                captcha_engine=captcha_engine,
                event_meta=event_meta,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error(f"【{self.cookie_id}】更新风控日志失败: {self._safe_str(e)}")

    @staticmethod
    def _extract_cookie_value(cookie_info: Optional[Dict[str, Any]]) -> str:
        """兼容不同调用方返回字段名，提取cookie字符串"""
        if not cookie_info:
            return ''
        return (
            cookie_info.get('value')
            or cookie_info.get('cookies_str')
            or cookie_info.get('cookie_value')
            or ''
        )

    def _load_proxy_config(self) -> dict:
        """从数据库加载当前账号的代理配置"""
        try:
            proxy_config = db_manager.get_cookie_proxy_config(self.cookie_id)
            return proxy_config
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】加载代理配置失败: {e}，使用默认配置（无代理）")
            return {
                'proxy_type': 'none',
                'proxy_host': '',
                'proxy_port': 0,
                'proxy_user': '',
                'proxy_pass': ''
            }

    def _get_proxy_url(self) -> str:
        """根据代理配置生成代理URL
        
        Returns:
            代理URL字符串，如果没有配置代理则返回None
        """
        if not self.proxy_config or self.proxy_config.get('proxy_type', 'none') == 'none':
            return None
        
        proxy_type = self.proxy_config.get('proxy_type', 'none')
        proxy_host = self.proxy_config.get('proxy_host', '')
        proxy_port = self.proxy_config.get('proxy_port', 0)
        proxy_user = self.proxy_config.get('proxy_user', '')
        proxy_pass = self.proxy_config.get('proxy_pass', '')
        
        if not proxy_host or not proxy_port:
            return None
        
        # 构建代理URL
        if proxy_user and proxy_pass:
            # 带认证的代理
            proxy_url = f"{proxy_type}://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
        else:
            # 无认证的代理
            proxy_url = f"{proxy_type}://{proxy_host}:{proxy_port}"
        
        return proxy_url

    def _set_connection_state(self, new_state: ConnectionState, reason: str = ""):
        """设置连接状态并记录日志"""
        if self.connection_state != new_state:
            old_state = self.connection_state
            self.connection_state = new_state
            self.last_state_change_time = time.time()
            
            # 记录状态转换
            state_msg = f"【{self.cookie_id}】连接状态: {old_state.value} → {new_state.value}"
            if reason:
                state_msg += f" ({reason})"
            
            # 根据状态严重程度选择日志级别
            if new_state == ConnectionState.FAILED:
                logger.error(state_msg)
            elif new_state == ConnectionState.RECONNECTING:
                logger.warning(state_msg)
            elif new_state == ConnectionState.CONNECTED:
                logger.success(state_msg)
            else:
                logger.info(state_msg)

    async def _interruptible_sleep(self, duration: float):
        """可中断的sleep，将长时间sleep拆分成多个短时间sleep，以便及时响应取消信号
        
        Args:
            duration: 总睡眠时间（秒）
        """
        # 将长时间sleep拆分成多个1秒的短sleep，这样可以及时响应取消信号
        chunk_size = 1.0  # 每次sleep 1秒
        remaining = duration
        
        while remaining > 0:
            sleep_time = min(chunk_size, remaining)
            try:
                await asyncio.sleep(sleep_time)
                remaining -= sleep_time
            except asyncio.CancelledError:
                # 如果收到取消信号，立即抛出
                raise

    def _reset_stream_activity_state(self, connected_at: Optional[float] = None):
        """重置当前连接的消息流活性状态。"""
        now = connected_at or time.time()
        self.last_non_heartbeat_message_time = now
        self.last_sync_package_time = 0
        self.last_user_chat_time = 0
        self.last_heartbeat_response = 0
        self.last_sent_heartbeat_mid = None
        self.pending_heartbeat_mids.clear()
        self.last_stream_watchdog_reconnect_time = 0

    def _mark_non_heartbeat_message(self, received_at: Optional[float] = None, *, is_sync_package: bool = False):
        """记录最近一次非心跳业务包时间。"""
        now = received_at or time.time()
        self.last_non_heartbeat_message_time = now
        if is_sync_package:
            self.last_sync_package_time = now
        if self.stream_watchdog_trigger_times:
            self.stream_watchdog_trigger_times.clear()

    async def _force_websocket_reconnect(self, reason: str) -> bool:
        """主动关闭当前WebSocket，让主循环重新建立业务流连接。"""
        ws = self.ws
        if not ws:
            logger.info(f"【{self.cookie_id}】{reason}，但当前没有活跃的WebSocket连接")
            return False

        if getattr(ws, "closed", False):
            logger.info(f"【{self.cookie_id}】{reason}，但当前WebSocket已关闭，等待主循环重连")
            return False

        self._set_connection_state(ConnectionState.RECONNECTING, reason)
        logger.warning(f"【{self.cookie_id}】{reason}，主动关闭当前WebSocket触发重连")
        try:
            await asyncio.wait_for(ws.close(), timeout=2.0)
            logger.warning(f"【{self.cookie_id}】当前WebSocket已关闭，主循环将使用最新状态重新连接")
            return True
        except asyncio.TimeoutError:
            logger.warning(f"【{self.cookie_id}】主动关闭WebSocket超时，等待主循环自行回收连接")
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】主动关闭WebSocket失败: {self._safe_str(e)}")
        return False

    def _record_message_stream_watchdog_trigger(self, occurred_at: Optional[float] = None) -> int:
        """记录业务流看门狗触发次数，便于识别重复假在线。"""
        now = occurred_at or time.time()
        window_seconds = max(60, int(self.message_stream_notification_window or 0))
        while self.stream_watchdog_trigger_times and now - self.stream_watchdog_trigger_times[0] > window_seconds:
            self.stream_watchdog_trigger_times.popleft()
        self.stream_watchdog_trigger_times.append(now)
        return len(self.stream_watchdog_trigger_times)

    async def _maybe_notify_message_stream_stale(self, occurred_at: float, connected_for: float, business_idle: float):
        """仅在短时间重复触发时发送业务流假在线通知，避免单次波动刷屏。"""
        trigger_count = self._record_message_stream_watchdog_trigger(occurred_at)
        if trigger_count < 2:
            return

        window_minutes = max(1, int(self.message_stream_notification_window // 60))
        sync_desc = (
            f"最近同步包距今{(occurred_at - self.last_sync_package_time):.0f}秒"
            if self.last_sync_package_time else
            "当前连接尚未收到同步包"
        )
        user_chat_desc = (
            f"最近真实买家消息距今{(occurred_at - self.last_user_chat_time):.0f}秒"
            if self.last_user_chat_time else
            "当前连接尚未收到真实买家消息"
        )
        notification_message = (
            f"业务消息流疑似假在线，最近{window_minutes}分钟内已连续触发{trigger_count}次自动重连。"
            f"已连接{connected_for:.0f}秒，最近非心跳业务包距今{business_idle:.0f}秒，"
            f"{sync_desc}，{user_chat_desc}"
        )
        await self.send_token_refresh_notification(notification_message, "message_stream_stale")

    async def message_stream_watchdog_loop(self):
        """检测“只有心跳、没有业务包”的假在线状态。"""
        heartbeat_stale_timeout = max(self.heartbeat_timeout * 2, self.heartbeat_interval * 3)
        try:
            while True:
                try:
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止业务流看门狗")
                        break

                    await self._interruptible_sleep(self.stream_watchdog_check_interval)

                    ws = self.ws
                    if not ws or getattr(ws, "closed", False):
                        continue

                    if not self.last_successful_connection:
                        continue

                    now = time.time()
                    connected_for = now - self.last_successful_connection
                    if connected_for < self.stream_watchdog_grace_period:
                        continue

                    if not self.last_heartbeat_response:
                        continue

                    heartbeat_age = now - self.last_heartbeat_response
                    if heartbeat_age > heartbeat_stale_timeout:
                        continue

                    last_business_at = self.last_non_heartbeat_message_time or self.last_successful_connection
                    business_idle = now - last_business_at
                    if business_idle < self.message_stream_watchdog_timeout:
                        continue

                    if (
                        self.last_stream_watchdog_reconnect_time
                        and now - self.last_stream_watchdog_reconnect_time < self.message_stream_watchdog_timeout / 2
                    ):
                        continue

                    self.last_stream_watchdog_reconnect_time = now
                    if self.last_sync_package_time:
                        sync_status = f"最近同步包距今{(now - self.last_sync_package_time):.0f}秒"
                    else:
                        sync_status = "当前连接尚未收到同步包"
                    if self.last_user_chat_time:
                        user_chat_status = f"，最近真实买家消息距今{(now - self.last_user_chat_time):.0f}秒"
                    else:
                        user_chat_status = "，当前连接尚未收到真实买家消息"

                    logger.warning(
                        f"【{self.cookie_id}】检测到业务流疑似假在线: "
                        f"已连接{connected_for:.0f}秒，最近非心跳业务包距今{business_idle:.0f}秒，{sync_status}{user_chat_status}"
                    )
                    await self._force_websocket_reconnect("业务消息流长时间只有心跳，疑似假在线")
                    await self._maybe_notify_message_stream_stale(now, connected_for, business_idle)
                except asyncio.CancelledError:
                    logger.info(f"【{self.cookie_id}】业务流看门狗收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】业务流看门狗异常: {self._safe_str(e)}")
                    await self._interruptible_sleep(30)
        except asyncio.CancelledError:
            logger.info(f"【{self.cookie_id}】业务流看门狗已取消，正在退出...")
            raise
        finally:
            logger.info(f"【{self.cookie_id}】业务流看门狗已退出")

    def _reset_background_tasks(self):
        """直接重置后台任务引用，不等待取消（用于快速重连）
        
        注意：只重置心跳任务，因为只有心跳任务依赖WebSocket连接。
        其他任务（会话保活、业务流看门狗、清理、Cookie刷新）不依赖WebSocket，可以继续运行。
        """
        logger.info(f"【{self.cookie_id}】准备重置后台任务引用（仅重置依赖WebSocket的任务）...")
        
        # 只处理心跳任务（依赖WebSocket，需要重启）
        if self.heartbeat_task:
            status = "已完成" if self.heartbeat_task.done() else "运行中"
            logger.info(f"【{self.cookie_id}】发现心跳任务（状态: {status}），需要重置（因为依赖WebSocket连接）")
            # 尝试取消心跳任务（但不等待）
            if not self.heartbeat_task.done():
                try:
                    self.heartbeat_task.cancel()
                    logger.debug(f"【{self.cookie_id}】已发送取消信号给心跳任务（不等待响应）")
                except Exception as e:
                    logger.warning(f"【{self.cookie_id}】取消心跳任务失败: {e}")
            # 重置心跳任务引用
            self.heartbeat_task = None
            logger.info(f"【{self.cookie_id}】心跳任务引用已重置")
        else:
            logger.info(f"【{self.cookie_id}】没有心跳任务需要重置")
        
        # 检查其他任务的状态（这些任务不依赖WebSocket，不需要重启）
        other_tasks_status = []
        if self.token_refresh_task:
            status = "已完成" if self.token_refresh_task.done() else "运行中"
            other_tasks_status.append(f"Token刷新任务({status})")
        if self.cleanup_task:
            status = "已完成" if self.cleanup_task.done() else "运行中"
            other_tasks_status.append(f"清理任务({status})")
        if self.cookie_refresh_task:
            status = "已完成" if self.cookie_refresh_task.done() else "运行中"
            other_tasks_status.append(f"Cookie刷新任务({status})")
        if self.stream_watchdog_task:
            status = "已完成" if self.stream_watchdog_task.done() else "运行中"
            other_tasks_status.append(f"业务流看门狗({status})")
        
        if other_tasks_status:
            logger.info(f"【{self.cookie_id}】其他任务继续运行（不依赖WebSocket）: {', '.join(other_tasks_status)}")
        else:
            logger.info(f"【{self.cookie_id}】没有其他任务在运行")
        
        logger.info(f"【{self.cookie_id}】任务重置完成，可以立即创建新的心跳任务")

    async def _cancel_background_tasks(self):
        """取消并清理所有后台任务（保留此方法用于程序退出时的完整清理）"""
        try:
            tasks_to_cancel = []
            
            # 收集所有需要取消的任务（只收集未完成的任务）
            if self.heartbeat_task:
                if not self.heartbeat_task.done():
                    tasks_to_cancel.append(("心跳任务", self.heartbeat_task))
                else:
                    logger.debug(f"【{self.cookie_id}】心跳任务已完成，跳过")
                    
            if self.token_refresh_task:
                if not self.token_refresh_task.done():
                    tasks_to_cancel.append(("Token刷新任务", self.token_refresh_task))
                else:
                    logger.debug(f"【{self.cookie_id}】Token刷新任务已完成，跳过")
                    
            if self.cleanup_task:
                if not self.cleanup_task.done():
                    tasks_to_cancel.append(("清理任务", self.cleanup_task))
                else:
                    logger.debug(f"【{self.cookie_id}】清理任务已完成，跳过")
                    
            if self.cookie_refresh_task:
                if not self.cookie_refresh_task.done():
                    tasks_to_cancel.append(("Cookie刷新任务", self.cookie_refresh_task))
                else:
                    logger.debug(f"【{self.cookie_id}】Cookie刷新任务已完成，跳过")
            
            if self.stream_watchdog_task:
                if not self.stream_watchdog_task.done():
                    tasks_to_cancel.append(("业务流看门狗", self.stream_watchdog_task))
                else:
                    logger.debug(f"【{self.cookie_id}】业务流看门狗已完成，跳过")
            
            if not tasks_to_cancel:
                logger.info(f"【{self.cookie_id}】没有后台任务需要取消（所有任务已完成或不存在）")
                # 立即重置任务引用
                self.heartbeat_task = None
                self.token_refresh_task = None
                self.cleanup_task = None
                self.cookie_refresh_task = None
                self.stream_watchdog_task = None
                return
            
            logger.info(f"【{self.cookie_id}】开始取消 {len(tasks_to_cancel)} 个未完成的后台任务...")
            
            # 取消所有任务
            for task_name, task in tasks_to_cancel:
                try:
                    if task.done():
                        logger.info(f"【{self.cookie_id}】任务已完成，跳过取消: {task_name}")
                    else:
                        task.cancel()
                        logger.info(f"【{self.cookie_id}】已发送取消信号: {task_name}")
                except Exception as e:
                    logger.warning(f"【{self.cookie_id}】取消任务失败 {task_name}: {e}")
            
            # 等待所有任务完成取消，使用合理的超时时间
            # 现在任务中已经添加了 await asyncio.sleep(0) 来让出控制权，应该能够响应取消信号
            tasks = [task for _, task in tasks_to_cancel]
            logger.info(f"【{self.cookie_id}】等待 {len(tasks)} 个任务响应取消信号...")
            
            wait_timeout = 5.0  # 增加超时时间到5秒，给任务更多时间响应取消信号
            
            start_time = time.time()
            try:
                # 只等待未完成的任务
                pending_tasks_list = [task for task in tasks if not task.done()]
                
                # 记录每个任务的状态
                for task_name, task in tasks_to_cancel:
                    status = "已完成" if task.done() else "运行中"
                    logger.info(f"【{self.cookie_id}】任务状态: {task_name} - {status}")
                
                if not pending_tasks_list:
                    logger.info(f"【{self.cookie_id}】所有任务已完成，无需等待")
                else:
                    logger.info(f"【{self.cookie_id}】等待 {len(pending_tasks_list)} 个未完成任务响应（超时时间: {wait_timeout}秒）...")
                    try:
                        # 使用 wait 等待任务完成，设置超时
                        logger.debug(f"【{self.cookie_id}】开始调用 asyncio.wait()...")
                        done, pending = await asyncio.wait(
                            pending_tasks_list,
                            timeout=wait_timeout,
                            return_when=asyncio.ALL_COMPLETED
                        )
                        elapsed = time.time() - start_time
                        logger.info(f"【{self.cookie_id}】asyncio.wait() 返回，耗时 {elapsed:.3f}秒，已完成: {len(done)}，未完成: {len(pending)}")
                        
                        # 检查已完成的任务，并记录详细信息
                        for task_name, task in tasks_to_cancel:
                            if task in done:
                                try:
                                    task.result()
                                    logger.warning(f"【{self.cookie_id}】⚠️ 任务正常完成（非取消）: {task_name}")
                                except asyncio.CancelledError:
                                    logger.info(f"【{self.cookie_id}】✅ 任务已成功取消: {task_name}")
                                except Exception as e:
                                    logger.warning(f"【{self.cookie_id}】⚠️ 任务取消时出现异常 {task_name}: {e}")
                        
                        if pending:
                            # 找出未完成的任务名称和详细信息
                            pending_names = []
                            for task_name, task in tasks_to_cancel:
                                if task in pending:
                                    pending_names.append(task_name)
                                    # 记录未完成任务的状态
                                    if task.done():
                                        try:
                                            task.result()
                                            logger.warning(f"【{self.cookie_id}】任务在等待期间完成: {task_name}")
                                        except asyncio.CancelledError:
                                            logger.info(f"【{self.cookie_id}】任务在等待期间被取消: {task_name}")
                                        except Exception as e:
                                            logger.warning(f"【{self.cookie_id}】任务在等待期间异常 {task_name}: {e}")
                                    else:
                                        logger.warning(f"【{self.cookie_id}】任务仍未完成: {task_name} (done={task.done()})")
                            
                            logger.warning(f"【{self.cookie_id}】等待超时 ({elapsed:.3f}秒)，以下任务可能仍在运行: {', '.join(pending_names)}")
                            
                            # 强制取消所有未完成的任务（再次尝试）
                            for task_name, task in tasks_to_cancel:
                                if task in pending and not task.done():
                                    try:
                                        task.cancel()
                                        logger.warning(f"【{self.cookie_id}】强制取消任务: {task_name}")
                                    except Exception as e:
                                        logger.warning(f"【{self.cookie_id}】强制取消任务失败 {task_name}: {e}")
                            
                            # 再等待一小段时间，看是否有任务响应
                            if pending:
                                try:
                                    done2, pending2 = await asyncio.wait(pending, timeout=1.0, return_when=asyncio.ALL_COMPLETED)
                                    for task_name, task in tasks_to_cancel:
                                        if task in done2:
                                            try:
                                                task.result()
                                            except asyncio.CancelledError:
                                                logger.info(f"【{self.cookie_id}】任务在二次等待期间被取消: {task_name}")
                                            except Exception as e:
                                                logger.warning(f"【{self.cookie_id}】任务在二次等待期间异常 {task_name}: {e}")
                                except Exception as e:
                                    logger.warning(f"【{self.cookie_id}】二次等待任务时出错: {e}")
                            
                            logger.warning(f"【{self.cookie_id}】强制继续重连流程，未完成的任务将在后台继续运行（但已标记为取消）")
                        else:
                            logger.info(f"【{self.cookie_id}】所有后台任务已取消 (耗时 {elapsed:.3f}秒)")
                            
                    except Exception as e:
                        elapsed = time.time() - start_time
                        logger.warning(f"【{self.cookie_id}】等待任务时出错 (耗时 {elapsed:.3f}秒): {e}")
                        import traceback
                        logger.warning(f"【{self.cookie_id}】等待任务异常堆栈:\n{traceback.format_exc()}")
                        
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"【{self.cookie_id}】等待任务取消时出错 (耗时 {elapsed:.3f}秒): {e}")
                import traceback
                logger.error(f"【{self.cookie_id}】等待任务取消异常堆栈:\n{traceback.format_exc()}")
            
            logger.info(f"【{self.cookie_id}】任务取消流程完成，继续重连流程")
            
            # 最后检查一次所有任务的状态
            for task_name, task in tasks_to_cancel:
                if task and not task.done():
                    logger.warning(f"【{self.cookie_id}】⚠️ 任务取消流程完成后，任务仍未完成: {task_name} (done={task.done()})")
                elif task and task.done():
                    logger.debug(f"【{self.cookie_id}】✅ 任务已完成: {task_name}")
        
        finally:
            # 使用 finally 确保无论发生什么情况都会重置任务引用
            # 这样可以保证下次重连时所有任务都会被重新创建
            self.heartbeat_task = None
            self.token_refresh_task = None
            self.cleanup_task = None
            self.cookie_refresh_task = None
            self.stream_watchdog_task = None
            logger.info(f"【{self.cookie_id}】后台任务引用已全部重置")

    def _calculate_retry_delay(self, error_msg: str) -> int:
        """根据错误类型和失败次数计算重试延迟"""
        current_time = time.time()
        if self._is_account_pause_status(getattr(self, 'last_token_refresh_status', None)):
            return max(300, self._compute_token_retry_wait_seconds(current_time))

        if self._is_in_qr_login_grace_period(current_time):
            return max(60, self._get_qr_login_grace_remaining_seconds(current_time))

        if getattr(self, 'last_token_refresh_status', None) in {"password_login_backoff_wait", "verification_pending_manual", "qr_login_grace_wait"}:
            return max(60, self._compute_token_retry_wait_seconds(current_time))

        # WebSocket意外断开 - 短延迟
        if "no close frame received or sent" in error_msg:
            return min(3 * self.connection_failures, 15)
        
        # 网络连接问题 - 长延迟
        elif "Connection refused" in error_msg or "timeout" in error_msg.lower():
            return min(10 * self.connection_failures, 60)
        
        # 其他未知错误 - 中等延迟
        else:
            return min(5 * self.connection_failures, 30)

    def _cleanup_instance_caches(self):
        """清理实例级别的缓存，防止内存泄漏"""
        try:
            current_time = time.time()
            cleaned_total = 0
            
            # 清理过期的通知记录（保留30分钟内的，从1小时优化）
            max_notification_age = 1800  # 30分钟（从3600优化）
            expired_notifications = [
                key for key, last_time in self.last_notification_time.items()
                if current_time - last_time > max_notification_age
            ]
            for key in expired_notifications:
                del self.last_notification_time[key]
            if expired_notifications:
                cleaned_total += len(expired_notifications)
                logger.warning(f"【{self.cookie_id}】清理了 {len(expired_notifications)} 个过期通知记录")
            
            # 清理过期的发货记录（保留30分钟内的）
            max_delivery_age = 1800  # 30分钟
            expired_deliveries = [
                order_id for order_id, last_time in self.last_delivery_time.items()
                if current_time - last_time > max_delivery_age
            ]
            for order_id in expired_deliveries:
                del self.last_delivery_time[order_id]
            if expired_deliveries:
                cleaned_total += len(expired_deliveries)
                logger.warning(f"【{self.cookie_id}】清理了 {len(expired_deliveries)} 个过期发货记录")
            
            # 清理过期的订单确认记录（保留30分钟内的）
            max_confirm_age = 1800  # 30分钟
            expired_confirms = [
                order_id for order_id, last_time in self.confirmed_orders.items()
                if current_time - last_time > max_confirm_age
            ]
            for order_id in expired_confirms:
                del self.confirmed_orders[order_id]
            if expired_confirms:
                cleaned_total += len(expired_confirms)
                logger.warning(f"【{self.cookie_id}】清理了 {len(expired_confirms)} 个过期订单确认记录")
            
            # 只有实际清理了内容才记录总数日志
            if cleaned_total > 0:
                logger.info(f"【{self.cookie_id}】实例缓存清理完成，共清理 {cleaned_total} 条记录")
                logger.warning(f"【{self.cookie_id}】当前缓存数量 - 通知: {len(self.last_notification_time)}, 发货: {len(self.last_delivery_time)}, 确认: {len(self.confirmed_orders)}")
        
        except Exception as e:
            logger.error(f"【{self.cookie_id}】清理实例缓存时出错: {self._safe_str(e)}")
    
    async def _cleanup_playwright_cache(self):
        """清理Playwright浏览器临时文件和缓存（Docker环境专用）"""
        try:
            import shutil
            import glob
            
            # 定义需要清理的临时目录路径
            temp_paths = [
                '/tmp/playwright-*',  # Playwright临时会话
                '/tmp/chromium-*',    # Chromium临时文件
                '/ms-playwright/chromium-*/Default/Cache',  # 浏览器缓存
                '/ms-playwright/chromium-*/Default/Code Cache',  # 代码缓存
                '/ms-playwright/chromium-*/Default/GPUCache',  # GPU缓存
            ]
            
            total_cleaned = 0
            total_size_mb = 0
            
            for pattern in temp_paths:
                try:
                    matching_paths = glob.glob(pattern)
                    for path in matching_paths:
                        try:
                            if os.path.exists(path):
                                # 计算大小
                                if os.path.isdir(path):
                                    size = sum(
                                        os.path.getsize(os.path.join(dirpath, filename))
                                        for dirpath, _, filenames in os.walk(path)
                                        for filename in filenames
                                    )
                                    shutil.rmtree(path, ignore_errors=True)
                                else:
                                    size = os.path.getsize(path)
                                    os.remove(path)
                                
                                total_size_mb += size / (1024 * 1024)
                                total_cleaned += 1
                        except Exception as e:
                            logger.warning(f"清理路径 {path} 时出错: {e}")
                except Exception as e:
                    logger.warning(f"匹配路径 {pattern} 时出错: {e}")
            
            if total_cleaned > 0:
                logger.info(f"【{self.cookie_id}】Playwright缓存清理完成: 删除了 {total_cleaned} 个文件/目录，释放 {total_size_mb:.2f} MB")
            else:
                logger.warning(f"【{self.cookie_id}】Playwright缓存清理: 没有需要清理的临时文件")
                
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】清理Playwright缓存时出错: {self._safe_str(e)}")

    async def _cleanup_old_logs(self, retention_days: int = 7):
        """清理过期的日志文件
        
        Args:
            retention_days: 保留的天数，默认7天
            
        Returns:
            清理的文件数量
        """
        try:
            import glob
            from datetime import datetime, timedelta
            
            logs_dir = "logs"
            if not os.path.exists(logs_dir):
                logger.warning(f"【{self.cookie_id}】日志目录不存在: {logs_dir}")
                return 0
            
            # 计算过期时间点
            cutoff_time = datetime.now() - timedelta(days=retention_days)
            
            # 查找所有日志文件（包括.log和.log.zip）
            log_patterns = [
                os.path.join(logs_dir, "xianyu_*.log"),
                os.path.join(logs_dir, "xianyu_*.log.zip"),
                os.path.join(logs_dir, "app_*.log"),
                os.path.join(logs_dir, "app_*.log.zip"),
            ]
            
            total_cleaned = 0
            total_size_mb = 0
            
            for pattern in log_patterns:
                log_files = glob.glob(pattern)
                for log_file in log_files:
                    try:
                        # 获取文件修改时间
                        file_mtime = datetime.fromtimestamp(os.path.getmtime(log_file))
                        
                        # 如果文件早于保留期限，则删除
                        if file_mtime < cutoff_time:
                            file_size = os.path.getsize(log_file)
                            os.remove(log_file)
                            total_size_mb += file_size / (1024 * 1024)
                            total_cleaned += 1
                            logger.debug(f"【{self.cookie_id}】删除过期日志文件: {log_file} (修改时间: {file_mtime})")
                    except Exception as e:
                        logger.warning(f"【{self.cookie_id}】删除日志文件失败 {log_file}: {self._safe_str(e)}")
            
            if total_cleaned > 0:
                logger.info(f"【{self.cookie_id}】日志清理完成: 删除了 {total_cleaned} 个日志文件，释放 {total_size_mb:.2f} MB (保留 {retention_days} 天内的日志)")
            else:
                logger.debug(f"【{self.cookie_id}】日志清理: 没有需要清理的过期日志文件 (保留 {retention_days} 天)")
            
            return total_cleaned
            
        except Exception as e:
            logger.error(f"【{self.cookie_id}】清理日志文件时出错: {self._safe_str(e)}")
            return 0

    def __init__(self, cookies_str=None, cookie_id: str = "default", user_id: int = None, *, register_instance: bool = True):
        """初始化闲鱼直播类"""
        logger.info(f"【{cookie_id}】开始初始化XianyuLive...")

        if not cookies_str:
            cookies_str = COOKIES_STR
        if not cookies_str:
            raise ValueError("未提供cookies，请在global_config.yml中配置COOKIES_STR或通过参数传入")

        # 清理从浏览器/记事本粘贴时常见的 BOM 与首尾空白，避免 trans_cookies 解析失败
        cookies_str = str(cookies_str).replace("\ufeff", "").strip()

        logger.info(f"【{cookie_id}】解析cookies...")
        self.cookies = trans_cookies(cookies_str)
        logger.info(f"【{cookie_id}】cookies解析完成，包含字段: {list(self.cookies.keys())}")

        self.cookie_id = cookie_id  # 唯一账号标识
        self.cookies_str = cookies_str  # 保存原始cookie字符串
        self.user_id = user_id  # 保存用户ID，用于token刷新时保持正确的所有者关系
        self.register_instance = bool(register_instance)
        self.base_url = WEBSOCKET_URL

        if 'unb' not in self.cookies:
            raise ValueError(f"【{cookie_id}】Cookie中缺少必需的'unb'字段，当前字段: {list(self.cookies.keys())}")

        self.myid = self.cookies['unb']
        logger.info(f"【{cookie_id}】用户ID: {self.myid}")
        self.device_id = generate_device_id(self.myid)

        # 心跳相关配置
        self.heartbeat_interval = HEARTBEAT_INTERVAL
        self.heartbeat_timeout = HEARTBEAT_TIMEOUT
        self.last_heartbeat_time = 0
        self.last_heartbeat_response = 0
        self.last_sent_heartbeat_mid = None
        self.pending_heartbeat_mids = deque(maxlen=32)
        self.lwp_response_waiters = {}
        self.heartbeat_task = None
        self.ws = None
        self.last_non_heartbeat_message_time = 0
        self.last_sync_package_time = 0
        self.last_user_chat_time = 0
        self.last_stream_watchdog_reconnect_time = 0

        # Token刷新相关配置
        self.token_refresh_interval = TOKEN_REFRESH_INTERVAL
        self.token_retry_interval = TOKEN_RETRY_INTERVAL
        self.session_keepalive_interval = SESSION_KEEPALIVE_INTERVAL
        self.session_keepalive_retry_interval = SESSION_KEEPALIVE_RETRY_INTERVAL
        self.last_token_refresh_time = 0
        self.last_session_keepalive_time = 0
        self.current_token = None
        self.token_refresh_task = None
        self.last_token_refresh_status = None  # Token刷新状态追踪
        self.last_token_refresh_error_message = None  # Token刷新失败详情，供通知文案分流
        self.last_session_keepalive_status = None
        self.last_session_keepalive_error_message = None
        self.pending_slider_success_notice = None  # 滑块成功后的延迟成功通知，避免会话未恢复时误报
        self.connection_restart_flag = False  # 连接重启标志
        self.last_init_failure_reason = None
        self.last_init_failure_type = None
        self.init_auth_failures = 0
        self.stream_watchdog_task = None
        self.stream_watchdog_check_interval = max(self.heartbeat_interval, 15)
        self.stream_watchdog_grace_period = max(self.heartbeat_interval * 4, 120)
        self.message_stream_watchdog_timeout = max(self.session_keepalive_interval * 3, 1800)
        self.stream_watchdog_trigger_times = deque(maxlen=8)
        self.message_stream_notification_window = max(self.message_stream_watchdog_timeout * 2, 3600)
        self.message_stream_notification_cooldown = max(self.message_stream_watchdog_timeout, 1800)

        prewarmed_token_info = self.pop_auth_prewarmed_token(self.cookie_id)
        if prewarmed_token_info:
            self.current_token = prewarmed_token_info.get('token')
            self.last_token_refresh_time = prewarmed_token_info.get('timestamp', time.time())
            logger.info(
                f"【{cookie_id}】已复用认证预热token，来源: {prewarmed_token_info.get('source') or 'unknown'}"
            )

        # 通知防重复机制
        self.last_notification_time = {}  # 记录每种通知类型的最后发送时间
        self.notification_cooldown = 300  # 5分钟内不重复发送相同类型的通知
        self.token_refresh_notification_cooldown = 18000  # Token刷新异常通知冷却时间：3小时
        self.notification_lock = asyncio.Lock()  # 通知防重复机制的异步锁
        self.pending_notification_keys = set()  # 记录发送中的通知，避免并发重复发送

        # 自动发货防重复机制
        self.last_delivery_time = {}  # 记录每个商品的最后发货时间
        self.delivery_cooldown = 600  # 10分钟内不重复发货

        # 自动确认发货防重复机制
        self.confirmed_orders = {}  # 记录已确认发货的订单，防止重复确认
        self.order_confirm_cooldown = 600  # 10分钟内不重复确认同一订单
        self.pending_platform_confirm_retry_lock = asyncio.Lock()  # 待补确认自动重试锁
        self.last_pending_platform_confirm_retry_time = 0.0
        self.pending_platform_confirm_retry_cooldown = 60  # 1分钟内不重复自动扫描待补确认订单
        self.last_platform_confirm_auth_recovery_time = 0.0
        self.platform_confirm_auth_recovery_cooldown = 300  # 5分钟内不重复触发确认发货失败后的认证恢复

        # 自动发货已发送订单记录
        self.delivery_sent_orders = set()  # 记录已发货的订单ID，防止重复发货

        self.session = None  # 用于API调用的aiohttp session

        # 代理配置 - 从数据库加载
        self.proxy_config = self._load_proxy_config()
        if self.proxy_config.get('proxy_type', 'none') != 'none':
            logger.info(f"【{cookie_id}】已加载代理配置: {self.proxy_config['proxy_type']}://{self.proxy_config['proxy_host']}:{self.proxy_config['proxy_port']}")

        # 启动定期清理过期暂停记录的任务
        self.cleanup_task = None

        # Cookie刷新定时任务
        self.cookie_refresh_task = None
        self.cookie_refresh_interval = 10800  # 3小时 = 10800秒
        self.last_cookie_refresh_time = 0
        self.cookie_refresh_lock = asyncio.Lock()  # 使用Lock防止重复执行Cookie刷新
        self.cookie_refresh_enabled = True  # 是否启用Cookie刷新功能

        # 扫码登录Cookie刷新标志
        self.last_qr_cookie_refresh_time = 0  # 记录上次扫码登录Cookie刷新时间
        self.qr_cookie_refresh_cooldown = 600  # 扫码登录Cookie刷新后的冷却时间：10分钟

        # 消息接收标识 - 用于控制Cookie刷新
        self.last_message_received_time = 0  # 记录上次收到消息的时间
        self.message_cookie_refresh_cooldown = 300  # 收到消息后5分钟内不执行Cookie刷新

        # 浏览器Cookie刷新成功标志
        self.browser_cookie_refreshed = False  # 标记_refresh_cookies_via_browser是否成功更新过数据库
        self.restarted_in_browser_refresh = False  # 刷新流程内部是否已触发重启（用于去重）


        # 滑块验证相关
        self.captcha_verification_count = 0  # 滑块验证次数计数器
        self.max_captcha_verification_count = 3  # 最大滑块验证次数，防止无限递归
        self.last_slider_success_at = 0.0
        self.last_slider_success_cookie_length = 0
        self.slider_success_reentry_window = 30
        self.post_slider_token_retry_delay = (
            float(RISK_CONTROL.get('post_slider_retry_delay_min', 5.0) or 5.0),
            float(RISK_CONTROL.get('post_slider_retry_delay_max', 10.0) or 10.0),
        )
        self.last_password_login_backoff_log_time = 0.0
        self.token_refresh_lock = asyncio.Lock()  # 防止多个入口并发刷新 token

        # WebSocket连接监控
        self.connection_state = ConnectionState.DISCONNECTED  # 连接状态
        self.connection_failures = 0  # 连续连接失败次数
        self.max_connection_failures = 5  # 最大连续失败次数
        self.last_successful_connection = 0  # 上次成功连接时间
        self.last_state_change_time = time.time()  # 上次状态变化时间

        # 后台任务追踪（用于清理未等待的任务）
        self.background_tasks = set()  # 追踪所有后台任务
        
        # 消息处理并发控制（防止内存泄漏）
        self.message_semaphore = asyncio.Semaphore(100)  # 最多100个并发消息处理任务
        self.active_message_tasks = 0  # 当前活跃的消息处理任务数
        
        # ============ 高性能消息队列系统 ============
        # 消息队列配置
        self.message_queue_enabled = True  # 是否启用消息队列系统
        self.message_queue_max_size = 1000  # 消息队列最大容量
        self.message_queue_workers = 5  # 消息处理工作协程数量
        self.message_expire_seconds = 60  # 消息过期时间（秒），超过此时间的消息将被丢弃
        
        # 消息优先级队列（使用优先级队列实现高优先级消息先处理）
        # 优先级: 0=最高（心跳/ACK）, 1=高（订单消息）, 2=中（聊天消息）, 3=低（其他）
        self.message_queue = asyncio.PriorityQueue(maxsize=self.message_queue_max_size)
        self.message_queue_counter = 0  # 用于保证FIFO顺序的计数器
        self.message_queue_lock = asyncio.Lock()
        
        # 工作协程管理
        self.message_workers = []  # 工作协程列表
        self.message_queue_running = False  # 队列系统运行状态
        
        # 队列监控统计
        self.queue_stats = {
            'received': 0,        # 收到的消息总数
            'processed': 0,       # 处理的消息数
            'dropped_full': 0,    # 因队列满而丢弃的消息数
            'dropped_expired': 0, # 因过期而丢弃的消息数
            'errors': 0,          # 处理错误数
            'last_stats_time': time.time(),  # 上次统计时间
        }

        # 亦凡卡劵账号充值确认流程状态管理
        self.yifan_account_waiting = {}  # 等待账号输入的订单: {chat_id: {buyer_id, rule, order_id, item_id, state, account, create_time}}
        self.yifan_account_lock = asyncio.Lock()  # 状态管理锁

        # 消息防抖管理器：用于处理用户连续发送消息的情况
        # {chat_id: {'task': asyncio.Task, 'last_message': dict, 'timer': float}}
        self.message_debounce_tasks = {}  # 存储每个chat_id的防抖任务
        self._message_debounce_delay = 3  # 防抖延迟默认值（秒），实际值通过property从数据库动态读取
        self.message_debounce_lock = asyncio.Lock()  # 防抖任务管理的锁
        
        # 消息去重机制：防止同一条消息被处理多次
        self.processed_message_ids = {}  # 存储已处理的消息ID和时间戳 {message_id: timestamp}
        self.pending_message_ids = {}  # 存储正在处理中的消息ID和时间戳 {message_id: timestamp}
        self.processed_message_ids_lock = asyncio.Lock()  # 消息ID去重的锁
        self.processed_message_ids_max_size = 10000  # 最大保存10000个消息ID，防止内存泄漏
        self.message_expire_time = 3600  # 消息过期时间（秒），默认1小时后可以重复回复
        self.pending_message_expire_time = 300  # 消息处理中保留时间（秒），避免处理中途异常导致永久卡死

        # 订单详情补抓任务：详情首次超时时，后台再补抓一次，避免整单丢失
        self.order_detail_retry_tasks = {}
        self.order_detail_force_refresh_marks = {}
        self.order_detail_force_refresh_cooldown = 5

        # 初始化订单状态处理器
        self._init_order_status_handler()

        # 只有长期运行实例才进入全局实例表，避免临时实例污染运行态诊断
        if self.register_instance:
            self._register_instance()

    @property
    def message_debounce_delay(self):
        """动态从数据库读取防抖延迟配置，修改后无需重启"""
        try:
            from db_manager import db_manager
            val = db_manager.get_system_setting('message_debounce_delay')
            return int(val) if val else self._message_debounce_delay
        except Exception:
            return self._message_debounce_delay

    def _init_order_status_handler(self):
        """初始化订单状态处理器"""
        try:
            # 直接导入订单状态处理器
            from order_status_handler import order_status_handler
            self.order_status_handler = order_status_handler
            logger.info(f"【{self.cookie_id}】订单状态处理器已启用")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】初始化订单状态处理器失败: {self._safe_str(e)}")
            self.order_status_handler = None

    def _register_instance(self):
        """注册当前实例到类级别字典"""
        try:
            # 使用同步方式注册，避免在__init__中使用async
            XianyuLive._instances[self.cookie_id] = self
            logger.warning(f"【{self.cookie_id}】实例已注册到全局字典")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】注册实例失败: {self._safe_str(e)}")

    def _unregister_instance(self):
        """从类级别字典中注销当前实例"""
        try:
            if self.cookie_id in XianyuLive._instances:
                del XianyuLive._instances[self.cookie_id]
                logger.warning(f"【{self.cookie_id}】实例已从全局字典中注销")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】注销实例失败: {self._safe_str(e)}")

    @classmethod
    def get_instance(cls, cookie_id: str):
        """获取指定cookie_id的XianyuLive实例"""
        return cls._instances.get(cookie_id)

    @classmethod
    def get_all_instances(cls):
        """获取所有活跃的XianyuLive实例"""
        return dict(cls._instances)

    @classmethod
    def get_instance_count(cls):
        """获取当前活跃实例数量"""
        return len(cls._instances)

    @classmethod
    def is_manual_refresh_active(cls, cookie_id: str, allow_handoff_recovery: bool = False) -> bool:
        """检查指定账号是否处于手动刷新保护期。"""
        if not cookie_id:
            return False
        state = cls.get_manual_refresh_state(cookie_id)
        if not state:
            return False
        phase = state.get('phase') or 'manual_refresh'
        if allow_handoff_recovery and phase == 'handoff_recovery':
            return False
        return True

    @classmethod
    def begin_manual_refresh(cls, cookie_id: str, source: str = "manual_refresh") -> Dict[str, Any]:
        """标记账号进入手动刷新保护期，并暂停自动Cookie刷新"""
        if not cookie_id:
            return {"started": False, "already_active": False, "reason": "empty_cookie_id"}

        live_instance = cls.get_instance(cookie_id)
        previous_cookie_refresh_enabled = None
        if live_instance is not None:
            previous_cookie_refresh_enabled = live_instance.cookie_refresh_enabled

        cls._cleanup_manual_refresh_state()
        with cls._manual_refresh_lock:
            existing = cls._manual_refresh_state.get(cookie_id)
            if existing:
                existing["source"] = source
                existing["phase"] = 'manual_refresh'
                existing["updated_at"] = time.time()
                existing["expires_at"] = None
                return {
                    "started": False,
                    "already_active": True,
                    "previous_cookie_refresh_enabled": existing.get("previous_cookie_refresh_enabled")
                }

            cls._manual_refresh_state[cookie_id] = {
                "source": source,
                "phase": 'manual_refresh',
                "started_at": time.time(),
                "updated_at": time.time(),
                "expires_at": None,
                "previous_cookie_refresh_enabled": previous_cookie_refresh_enabled,
            }

        if live_instance is not None and previous_cookie_refresh_enabled is not None:
            live_instance.enable_cookie_refresh(False)
            logger.warning(f"【{cookie_id}】已进入手动刷新保护期，暂停自动Cookie刷新")
        else:
            logger.warning(f"【{cookie_id}】已进入手动刷新保护期，当前无运行中的账号实例")

        return {
            "started": True,
            "already_active": False,
            "previous_cookie_refresh_enabled": previous_cookie_refresh_enabled
        }

    @classmethod
    def end_manual_refresh(cls, cookie_id: str, source: str = "manual_refresh") -> bool:
        """结束手动刷新保护期，并按原状态恢复自动Cookie刷新"""
        if not cookie_id:
            return False

        cls._cleanup_manual_refresh_state()
        with cls._manual_refresh_lock:
            state = cls._manual_refresh_state.pop(cookie_id, None)

        if state is None:
            return False

        live_instance = cls.get_instance(cookie_id)
        previous_cookie_refresh_enabled = state.get("previous_cookie_refresh_enabled")
        if live_instance is not None and previous_cookie_refresh_enabled is not None:
            live_instance.enable_cookie_refresh(previous_cookie_refresh_enabled)
            if previous_cookie_refresh_enabled:
                # 手动刷新刚结束时，避免新实例立刻再触发一轮自动Cookie刷新。
                live_instance.last_cookie_refresh_time = time.time()
            logger.warning(
                f"【{cookie_id}】手动刷新保护期已结束，恢复自动Cookie刷新: {previous_cookie_refresh_enabled}"
            )
        else:
            logger.warning(f"【{cookie_id}】手动刷新保护期已结束，当前无运行中的账号实例可恢复")

        logger.info(f"【{cookie_id}】结束手动刷新保护期，来源: {source}")
        return True

    @classmethod
    def begin_auth_recovery_session(
        cls,
        cookie_id: str,
        owner: str,
        *,
        mode: str,
        source: str,
        ttl: int = None,
        force_replace: bool = False,
    ) -> Dict[str, Any]:
        if not cookie_id or not owner:
            return {'started': False, 'reason': 'empty_cookie_id_or_owner'}

        acquired, existing = cls.acquire_auth_recovery_lock(cookie_id, owner, ttl=ttl)
        if not acquired:
            existing_owner = (existing or {}).get('owner', 'unknown')
            if not force_replace:
                return {
                    'started': False,
                    'already_active': True,
                    'active_owner': existing_owner,
                    'reason': 'auth_recovery_in_progress',
                }
            cls.release_auth_recovery_lock(cookie_id, existing_owner)
            acquired, existing = cls.acquire_auth_recovery_lock(cookie_id, owner, ttl=ttl)
            if not acquired:
                return {
                    'started': False,
                    'already_active': True,
                    'active_owner': (existing or {}).get('owner', 'unknown'),
                    'reason': 'auth_recovery_replace_failed',
                }

        return {
            'started': True,
            'already_active': False,
            'owner': owner,
            'mode': mode,
            'source': source,
        }

    @classmethod
    def end_auth_recovery_session(cls, cookie_id: str, owner: str) -> None:
        cls.release_auth_recovery_lock(cookie_id, owner)
    
    def _create_tracked_task(self, coro):
        """创建并追踪后台任务，确保异常不会被静默忽略"""
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    def _sanitize_buyer_nick(self, candidate: Any, *, source: str = "unknown",
                             message_meta: Dict[str, Any] = None, log_prefix: str = "") -> Optional[str]:
        """过滤系统/营销文案，避免污染订单买家昵称。"""
        if candidate is None:
            return None

        text = str(candidate).strip()
        if not text or text in {"未知用户", "unknown", "unknown_user"}:
            return None

        invalid_exact_titles = {
            "订单",
            "全部",
            "交易消息",
            "等待你发货",
            "买家",
            "工作台通知",
            "你人真不错，送你闲鱼小红花",
            "卖家人不错？送Ta闲鱼小红花",
            "快给ta一个评价吧～",
        }
        if text in invalid_exact_titles:
            logger.info(f"{log_prefix} 👤 忽略系统标题型买家昵称({source}): {text}")
            return None

        meta = message_meta if isinstance(message_meta, dict) else {}
        related_notice_texts = []
        for key in ("detailNotice", "reminderContent", "reminderNotice"):
            value = str(meta.get(key, "")).strip()
            if value:
                related_notice_texts.append(value)

        if text in related_notice_texts:
            logger.info(f"{log_prefix} 👤 忽略通知文案型买家昵称({source}): {text}")
            return None

        reminder_title = str(meta.get("reminderTitle", "")).strip()
        if source != "senderNick":
            invalid_keywords = (
                "小红花", "待付款", "待发货", "待刀成", "成功小刀", "闲鱼",
                "交易", "收货", "退款", "评价", "发货", "付款", "拍下",
                "确认", "关闭", "鼓励", "真不错", "全部", "订单",
            )
            if any(keyword in text for keyword in invalid_keywords):
                logger.info(f"{log_prefix} 👤 忽略系统关键词型买家昵称({source}): {text}")
                return None

            if reminder_title == text and len(text) >= 10 and any(ch in text for ch in "，,。！？?!：:～~"):
                logger.info(f"{log_prefix} 👤 忽略长句型买家昵称({source}): {text}")
                return None

        return text

    def _resolve_delivery_log_buyer_nick(self, buyer_nick: Any = None, *, order_id: str = None,
                                         buyer_id: str = None, log_prefix: str = "") -> Optional[str]:
        """为发货日志优先选择可信的买家昵称，避免写入系统卡片标题。"""
        from db_manager import db_manager

        normalized_order_id = str(order_id).strip() if order_id else None
        normalized_buyer_id = str(buyer_id).strip() if buyer_id else None

        try:
            if normalized_order_id:
                order_info = db_manager.get_order_by_id(normalized_order_id)
                if order_info:
                    order_cookie_id = str(order_info.get("cookie_id") or "").strip()
                    if not order_cookie_id or order_cookie_id == str(self.cookie_id).strip():
                        order_buyer_nick = self._sanitize_buyer_nick(
                            order_info.get("buyer_nick"),
                            source="delivery_log_order",
                            log_prefix=log_prefix,
                        )
                        if order_buyer_nick:
                            return order_buyer_nick

                    if not normalized_buyer_id:
                        normalized_buyer_id = str(order_info.get("buyer_id") or "").strip() or None

            if normalized_buyer_id:
                recent_order = db_manager.get_recent_order_by_buyer_id(
                    normalized_buyer_id,
                    cookie_id=self.cookie_id,
                    minutes=60,
                )
                if recent_order:
                    recent_buyer_nick = self._sanitize_buyer_nick(
                        recent_order.get("buyer_nick"),
                        source="delivery_log_recent_order",
                        log_prefix=log_prefix,
                    )
                    if recent_buyer_nick:
                        return recent_buyer_nick
        except Exception as resolve_error:
            logger.warning(f"{log_prefix} 发货日志买家昵称解析失败: {self._safe_str(resolve_error)}")

        return self._sanitize_buyer_nick(
            buyer_nick,
            source="delivery_log_raw",
            log_prefix=log_prefix,
        )

    def _lookup_delivery_order_by_sid(self, sid: str, *, minutes: int = 10,
                                      log_prefix: str = "") -> Dict[str, Any]:
        """根据 sid 查找简化发货对应订单，并区分是否已处理/已关闭。"""
        normalized_sid = str(sid or "").strip()
        if not normalized_sid:
            return {"match_type": "missing", "order": None}

        try:
            pending_orders = db_manager.find_recent_orders_by_match_context(
                sid=normalized_sid,
                cookie_id=self.cookie_id,
                statuses=[
                    "pending_ship",
                    "pending_delivery",
                    "partial_success",
                    "partial_pending_finalize",
                ],
                minutes=minutes,
                limit=5,
            )
        except Exception as lookup_error:
            logger.error(f"{log_prefix} sid兜底查单异常: {self._safe_str(lookup_error)}")
            return {"match_type": "error", "order": None}

        if pending_orders:
            order = pending_orders[0]
            logger.info(
                f"{log_prefix} sid兜底命中待发货订单: sid={normalized_sid}, "
                f"order_id={order.get('order_id')}, status={order.get('order_status') or 'unknown'}"
            )
            return {"match_type": "pending_ship", "order": order}

        try:
            recent_orders = db_manager.find_recent_orders_by_match_context(
                sid=normalized_sid,
                cookie_id=self.cookie_id,
                statuses=[
                    "processing",
                    "pending_payment",
                    "shipped",
                    "completed",
                    "cancelled",
                ],
                minutes=minutes,
                limit=5,
            )
        except Exception as lookup_error:
            logger.error(f"{log_prefix} sid兜底查单异常: {self._safe_str(lookup_error)}")
            return {"match_type": "error", "order": None}

        if not recent_orders:
            return {"match_type": "missing", "order": None}

        order = recent_orders[0]
        order_id = str(order.get("order_id") or "").strip()
        order_status = str(order.get("order_status") or "").strip()
        if order_status == "shipped":
            if self._has_delivery_progress_evidence(order_id):
                match_type = "already_processed"
            else:
                match_type = "suspicious_shipped"
                logger.warning(
                    f"{log_prefix} sid兜底命中可疑已发货订单，检测到无真实发货进度，继续允许纠偏: "
                    f"sid={normalized_sid}, order_id={order_id}, status={order_status}"
                )
        elif order_status == "completed":
            match_type = "already_processed"
        elif order_status == "cancelled":
            match_type = "cancelled"
        elif order_status in {"processing", "pending_payment"}:
            match_type = "not_ready"
        else:
            match_type = "other_status"

        logger.info(
            f"{log_prefix} sid兜底命中订单: sid={normalized_sid}, "
            f"order_id={order.get('order_id')}, status={order_status or 'unknown'}, match_type={match_type}"
        )
        return {"match_type": match_type, "order": order}

    async def _refresh_sid_lookup_if_needed(self, sid: str, sid_lookup: Dict[str, Any], *,
                                            item_id: str = None, buyer_id: str = None,
                                            minutes: int = 10, allow_bargain_ready: bool = False,
                                            log_prefix: str = "") -> Dict[str, Any]:
        """sid 命中未就绪订单时，强刷详情后再判定一次。"""
        recent_order = (sid_lookup or {}).get('order')
        match_type = (sid_lookup or {}).get('match_type', 'missing')

        if not recent_order or match_type not in {'not_ready', 'other_status', 'suspicious_shipped'}:
            return sid_lookup

        order_id = str(recent_order.get('order_id') or '').strip()
        if not order_id:
            return sid_lookup

        refresh_item_id = recent_order.get('item_id') or item_id
        refresh_buyer_id = recent_order.get('buyer_id') or buyer_id
        old_status = recent_order.get('order_status') or 'unknown'

        logger.info(
            f"{log_prefix} sid命中的订单状态未就绪，尝试强制刷新订单详情后重试: "
            f"order_id={order_id}, status={old_status}"
        )

        if not self._reserve_order_detail_force_refresh(
            order_id,
            reason='sid_not_ready',
            log_prefix=log_prefix,
        ):
            return sid_lookup

        try:
            await self.fetch_order_detail_info(
                order_id,
                refresh_item_id,
                refresh_buyer_id,
                sid=sid,
                force_refresh=True
            )
        except Exception as refresh_error:
            logger.warning(f"{log_prefix} sid未就绪订单强刷失败: {self._safe_str(refresh_error)}")
            return sid_lookup

        refreshed_lookup = self._lookup_delivery_order_by_sid(
            sid,
            minutes=minutes,
            log_prefix=log_prefix
        )
        refreshed_order = refreshed_lookup.get('order') or {}

        if (
            allow_bargain_ready and
            refreshed_lookup.get('match_type') == 'not_ready' and
            refreshed_order and
            str(refreshed_order.get('order_status') or '').strip() in {'processing', 'pending_payment'} and
            self._has_bargain_success_evidence(refreshed_order)
        ):
            refreshed_lookup = dict(refreshed_lookup)
            refreshed_lookup['match_type'] = 'bargain_ready'
            logger.info(
                f"{log_prefix} sid强刷后仍未进入待发货，但检测到小刀成功证据，"
                f"改用小刀兜底发货: order_id={refreshed_order.get('order_id') or order_id}, "
                f"status={refreshed_order.get('order_status') or 'unknown'}"
            )

        logger.info(
            f"{log_prefix} sid强刷后重新判定: order_id={refreshed_order.get('order_id') or order_id}, "
            f"status={refreshed_order.get('order_status') or 'unknown'}, "
            f"match_type={refreshed_lookup.get('match_type', 'missing')}"
        )
        return refreshed_lookup

    async def _ensure_item_owned_by_current_account(self, item_id: str, *,
                                                    log_prefix: str = "",
                                                    page_size: int = 50,
                                                    max_pages: int = 3) -> bool:
        """优先查本地缓存，未命中时刷新在售商品列表进行归属校验。"""
        if not item_id or item_id == "未知商品":
            return False

        existing_item = db_manager.get_item_info(self.cookie_id, item_id)
        if existing_item:
            return True

        logger.info(f"{log_prefix} 商品 {item_id} 未命中本地缓存，刷新在售商品列表后重试归属校验")
        try:
            for page_number in range(1, max_pages + 1):
                result = await self.get_item_list_info(page_number=page_number, page_size=page_size)
                if not result.get("success"):
                    logger.warning(f"{log_prefix} 刷新在售商品列表失败，停止归属校验回退: page={page_number}, result={result}")
                    break

                current_items = result.get("items", [])
                if any(str(item.get("id", "")).strip() == str(item_id).strip() for item in current_items):
                    logger.info(f"{log_prefix} 商品 {item_id} 在第 {page_number} 页在售商品列表中命中，归属校验通过")
                    return True

                if len(current_items) < page_size:
                    break
        except Exception as e:
            logger.error(f"{log_prefix} 刷新在售商品列表进行归属校验失败: {self._safe_str(e)}")

        return bool(db_manager.get_item_info(self.cookie_id, item_id))

    # 已知的无效 buyer_id 占位值
    _INVALID_BUYER_IDS = {"unknown_user", "unknown", "", "None", "null", "0", "-", "-1"}

    @classmethod
    def _normalize_buyer_id_value(cls, buyer_id) -> Optional[str]:
        if buyer_id is None:
            return None
        text = str(buyer_id).strip()
        if not text:
            return None
        if text.endswith('@goofish'):
            text = text.split('@')[0].strip()
        return text or None

    @staticmethod
    def _is_trustworthy_buyer_id(buyer_id) -> bool:
        """判断 buyer_id 是否可信，用于防串单校验。
        不可信的值（占位符等）不应参与一致性比对。"""
        normalized_buyer_id = XianyuLive._normalize_buyer_id_value(buyer_id)
        if not normalized_buyer_id:
            return False
        if normalized_buyer_id in XianyuLive._INVALID_BUYER_IDS:
            return False
        if normalized_buyer_id.isdigit() and len(normalized_buyer_id) <= 2:
            return False
        return True

    def _extract_query_value_from_url(self, url_text: Any, key: str) -> Optional[str]:
        text = str(url_text or '').strip()
        if not text:
            return None

        try:
            parsed = urlparse(text)
            query = parse_qs(parsed.query or '')
            value = query.get(key, [None])[0]
            return self._normalize_buyer_id_value(value)
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】解析链接参数失败: key={key}, error={self._safe_str(e)}")
            return None

    def _extract_buyer_id_from_message_meta(self, message_meta: dict, *, meta_label: str,
                                            log_prefix: str = "") -> Tuple[Optional[str], Optional[str]]:
        if not isinstance(message_meta, dict):
            return None, None

        biz_tag_dict = self._load_json_dict(message_meta.get('bizTag', ''))
        candidates = [
            ('reminderUrl.peerUserId', self._extract_query_value_from_url(message_meta.get('reminderUrl'), 'peerUserId')),
            ('bizTag.senderId', self._normalize_buyer_id_value(biz_tag_dict.get('senderId') or biz_tag_dict.get('sender_id'))),
            (f'{meta_label}.senderUserId', self._normalize_buyer_id_value(message_meta.get('senderUserId'))),
        ]

        low_trust_candidates = []
        for source, candidate in candidates:
            if not candidate:
                continue
            if self._is_trustworthy_buyer_id(candidate):
                return candidate, source
            low_trust_candidates.append(f'{source}={candidate}')

        if low_trust_candidates:
            logger.info(
                f"{log_prefix} 👤 检测到低可信买家ID候选，已忽略: {', '.join(low_trust_candidates[:3])}"
            )
        return None, None

    def _select_buyer_identity_for_order_write(self, order_id: str, *, incoming_buyer_id: Any = None,
                                               incoming_buyer_nick: Any = None, existing_order: Dict[str, Any] = None,
                                               buyer_id_source: str = None, buyer_nick_source: str = 'unknown',
                                               log_prefix: str = '') -> Tuple[Optional[str], Optional[str], bool]:
        incoming_buyer_id = self._normalize_buyer_id_value(incoming_buyer_id)
        incoming_buyer_nick = self._sanitize_buyer_nick(
            incoming_buyer_nick,
            source=buyer_nick_source,
            log_prefix=log_prefix,
        )

        existing_buyer_id = self._normalize_buyer_id_value((existing_order or {}).get('buyer_id'))
        existing_buyer_nick = (existing_order or {}).get('buyer_nick')
        existing_buyer_is_trustworthy = self._is_trustworthy_buyer_id(existing_buyer_id)
        incoming_buyer_is_trustworthy = self._is_trustworthy_buyer_id(incoming_buyer_id)
        source_label = buyer_id_source or 'unknown'

        if incoming_buyer_id and incoming_buyer_id == self.myid:
            if existing_order:
                preserved_buyer_id = existing_buyer_id if existing_buyer_id and existing_buyer_id != self.myid else None
                if existing_buyer_nick:
                    incoming_buyer_nick = existing_buyer_nick
                logger.info(
                    f"{log_prefix} 订单 {order_id} 命中自己买家ID保护，继续刷新并保留已有买家信息: "
                    f"incoming_buyer_id={incoming_buyer_id}, preserved_buyer_id={preserved_buyer_id}"
                )
                return preserved_buyer_id, incoming_buyer_nick, False

            logger.info(
                f"{log_prefix} 跳过疑似买家订单 {order_id} 的首次写入，buyer_id={incoming_buyer_id} 等于自己的ID"
            )
            return None, incoming_buyer_nick, True

        if existing_buyer_is_trustworthy:
            if not incoming_buyer_id:
                return existing_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

            if not incoming_buyer_is_trustworthy:
                logger.info(
                    f"{log_prefix} 忽略低可信buyer_id覆盖，保留已有买家信息: "
                    f"order_id={order_id}, incoming_buyer_id={incoming_buyer_id}, "
                    f"incoming_source={source_label}, preserved_buyer_id={existing_buyer_id}"
                )
                return existing_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

            if incoming_buyer_id != existing_buyer_id:
                logger.warning(
                    f"{log_prefix} 检测到买家ID冲突，保留已有可信买家信息: "
                    f"order_id={order_id}, incoming_buyer_id={incoming_buyer_id}, "
                    f"incoming_source={source_label}, preserved_buyer_id={existing_buyer_id}"
                )
                return existing_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

            return existing_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

        if incoming_buyer_is_trustworthy:
            return incoming_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

        if incoming_buyer_id:
            logger.info(
                f"{log_prefix} 检测到低可信buyer_id，暂不写入订单: "
                f"order_id={order_id}, incoming_buyer_id={incoming_buyer_id}, incoming_source={source_label}"
            )

        fallback_buyer_id = existing_buyer_id if existing_buyer_id and existing_buyer_id != self.myid else None
        return fallback_buyer_id, incoming_buyer_nick or existing_buyer_nick, False

    def _extract_order_message_context(self, message: dict, msg_id: str = None) -> Dict[str, Any]:
        """从订单相关消息中提取买家、会话和商品信息。"""
        buyer_id = None
        buyer_id_source = None
        buyer_nick = None
        sid = ""
        item_id = None
        log_prefix = f"【{self.cookie_id}】[{msg_id}]" if msg_id else f"【{self.cookie_id}】"

        try:
            message_1 = message.get("1")
            if isinstance(message_1, str):
                # message['1'] 是字符串，可能是 sid（如 "56226853668@goofish"）或消息ID（如 "4003914207496.PNM"）
                if '@' in message_1:
                    sid = message_1
                else:
                    # PNM 等非 sid 格式，真正的 sid 在 message['2']
                    sid = message.get("2", "") or ""
                buyer_id = None
                # 尝试从 message['4'] 提取 buyer_id（PNM 等格式的 senderUserId 在这里）
                message_4 = message.get("4")
                if isinstance(message_4, dict):
                    buyer_id, buyer_id_source = self._extract_buyer_id_from_message_meta(
                        message_4,
                        meta_label='message[4]',
                        log_prefix=log_prefix,
                    )
                    buyer_nick = self._sanitize_buyer_nick(
                        message_4.get("senderNick"),
                        source="senderNick(msg4)",
                        message_meta=message_4,
                        log_prefix=log_prefix
                    )
                    if not buyer_nick:
                        reminder_title = message_4.get("reminderTitle", "")
                        buyer_nick = self._sanitize_buyer_nick(
                            reminder_title,
                            source="reminderTitle(msg4)",
                            message_meta=message_4,
                            log_prefix=log_prefix
                        )
                        if buyer_nick:
                            logger.info(f"{log_prefix} 👤 从message[4].reminderTitle提取到买家昵称: {buyer_nick}")
                    if buyer_nick:
                        logger.info(f"{log_prefix} 👤 从message[4]提取到买家昵称: {buyer_nick}")
                logger.info(
                    f"{log_prefix} 📌 简化消息，sid: {sid}，buyer_id: {buyer_id}，"
                    f"buyer_id_source: {buyer_id_source or '-'}"
                )
            elif isinstance(message_1, dict):
                if "10" in message_1 and isinstance(message_1["10"], dict):
                    message_10 = message_1["10"]
                    buyer_id, buyer_id_source = self._extract_buyer_id_from_message_meta(
                        message_10,
                        meta_label='message[1][10]',
                        log_prefix=log_prefix,
                    )
                    buyer_nick = self._sanitize_buyer_nick(
                        message_10.get("senderNick"),
                        source="senderNick",
                        message_meta=message_10,
                        log_prefix=log_prefix
                    )
                    if not buyer_nick:
                        reminder_title = message_10.get("reminderTitle", "")
                        buyer_nick = self._sanitize_buyer_nick(
                            reminder_title,
                            source="reminderTitle",
                            message_meta=message_10,
                            log_prefix=log_prefix
                        )
                        if buyer_nick:
                            logger.info(f"{log_prefix} 👤 从reminderTitle提取到买家昵称: {buyer_nick}")
                    if buyer_nick:
                        logger.info(f"{log_prefix} 👤 提取到买家昵称: {buyer_nick}")
                sid = message_1.get("2", "")
                if sid:
                    logger.info(f"{log_prefix} 📌 提取到sid: {sid}")
        except Exception as context_e:
            logger.warning(f"{log_prefix} 提取订单上下文失败: {self._safe_str(context_e)}")

        try:
            if "1" in message and isinstance(message["1"], dict) and "10" in message["1"] and isinstance(message["1"]["10"], dict):
                url_info = message["1"]["10"].get("reminderUrl", "")
                if isinstance(url_info, str) and "itemId=" in url_info:
                    item_id = url_info.split("itemId=")[1].split("&")[0]

            # message['4'] 中也可能包含 reminderUrl（PNM 等格式）
            if not item_id and "4" in message and isinstance(message["4"], dict):
                url_info = message["4"].get("reminderUrl", "")
                if isinstance(url_info, str) and "itemId=" in url_info:
                    item_id = url_info.split("itemId=")[1].split("&")[0]

            if not item_id:
                item_id = self.extract_item_id_from_message(message)
        except Exception as item_e:
            logger.warning(f"{log_prefix} 提取商品ID失败: {self._safe_str(item_e)}")

        return {
            'buyer_id': buyer_id,
            'buyer_id_source': buyer_id_source,
            'buyer_nick': buyer_nick,
            'sid': sid,
            'item_id': item_id,
        }

    def _preload_basic_order_info(self, order_id: str, item_id: str = None, buyer_id: str = None,
                                  sid: str = None, buyer_nick: str = None,
                                  buyer_id_source: str = None) -> bool:
        """在详情抓取前先落基础订单，避免详情超时导致整单丢失。"""
        try:
            existing_order = db_manager.get_order_by_id(order_id)
            buyer_id_to_save, buyer_nick_to_save, should_skip_write = self._select_buyer_identity_for_order_write(
                order_id,
                incoming_buyer_id=buyer_id,
                incoming_buyer_nick=buyer_nick,
                existing_order=existing_order,
                buyer_id_source=buyer_id_source,
                buyer_nick_source="preload",
                log_prefix=f"【{self.cookie_id}】",
            )
            if should_skip_write:
                return False

            success = db_manager.insert_or_update_order(
                order_id=order_id,
                item_id=item_id,
                buyer_id=buyer_id_to_save,
                buyer_nick=buyer_nick_to_save,
                sid=sid,
                cookie_id=self.cookie_id,
                order_status='processing' if not existing_order else None
            )
            if success:
                action = "更新基础订单信息" if existing_order else "基础订单已预入库"
                logger.info(
                    f"【{self.cookie_id}】{action}: order_id={order_id}, item_id={item_id}, "
                    f"buyer_id={buyer_id_to_save}, sid={sid or '-'}"
                )
            else:
                logger.warning(f"【{self.cookie_id}】基础订单预入库失败: {order_id}")
            return success
        except Exception as e:
            logger.error(f"【{self.cookie_id}】基础订单预入库异常: {self._safe_str(e)}")
            return False

    async def _retry_order_detail_after_delay(self, order_id: str, item_id: str = None, buyer_id: str = None,
                                              sid: str = None, buyer_nick: str = None, delay_seconds: int = 30,
                                              buyer_id_source: str = None):
        """订单详情首次抓取失败后，后台延迟补抓一次。"""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(delay_seconds)
            logger.info(f"【{self.cookie_id}】开始延迟补抓订单详情: order_id={order_id}, delay={delay_seconds}s")
            result = await self.fetch_order_detail_info(
                order_id,
                item_id,
                buyer_id,
                sid=sid,
                buyer_nick=buyer_nick,
                buyer_id_source=buyer_id_source,
                force_refresh=True
            )
            if result:
                logger.info(f"【{self.cookie_id}】订单详情延迟补抓成功: {order_id}")
            else:
                logger.warning(f"【{self.cookie_id}】订单详情延迟补抓仍失败，保留基础订单: {order_id}")
        except asyncio.CancelledError:
            logger.info(f"【{self.cookie_id}】订单详情延迟补抓任务已取消: {order_id}")
            raise
        except Exception as e:
            logger.error(f"【{self.cookie_id}】订单详情延迟补抓异常: {order_id} - {self._safe_str(e)}")
        finally:
            existing_task = self.order_detail_retry_tasks.get(order_id)
            if existing_task is current_task:
                self.order_detail_retry_tasks.pop(order_id, None)

    def _schedule_order_detail_retry(self, order_id: str, item_id: str = None, buyer_id: str = None,
                                     sid: str = None, buyer_nick: str = None, delay_seconds: int = 30,
                                     buyer_id_source: str = None):
        """调度订单详情补抓任务，避免同一订单重复创建补抓。"""
        existing_task = self.order_detail_retry_tasks.get(order_id)
        if existing_task and not existing_task.done():
            logger.info(f"【{self.cookie_id}】订单详情补抓任务已存在，跳过重复调度: {order_id}")
            return

        task = self._create_tracked_task(
            self._retry_order_detail_after_delay(
                order_id,
                item_id=item_id,
                buyer_id=buyer_id,
                sid=sid,
                buyer_nick=buyer_nick,
                delay_seconds=delay_seconds,
                buyer_id_source=buyer_id_source,
            )
        )
        self.order_detail_retry_tasks[order_id] = task
        logger.info(f"【{self.cookie_id}】已调度订单详情补抓任务: order_id={order_id}, delay={delay_seconds}s")

    # ============ 高性能消息队列系统方法 ============
    
    def _get_message_priority(self, message_data: dict) -> int:
        """
        根据消息类型确定优先级
        
        优先级定义:
        - 0: 最高优先级（心跳响应、ACK确认）- 立即处理
        - 1: 高优先级（订单相关消息）- 优先处理
        - 2: 中优先级（普通聊天消息）- 正常处理
        - 3: 低优先级（系统通知、其他）- 延后处理
        
        Returns:
            int: 优先级值，越小优先级越高
        """
        try:
            # 检查是否是心跳响应
            if isinstance(message_data, dict):
                # 心跳响应
                if message_data.get("code") == 200 and "body" not in message_data:
                    return 0
                
                # 检查消息体
                body = message_data.get("body", {})
                
                # 同步包消息需要进一步分析
                if "syncPushPackage" in body:
                    try:
                        sync_data = body["syncPushPackage"].get("data", [])
                        if sync_data and isinstance(sync_data, list) and len(sync_data) > 0:
                            first_data = sync_data[0]
                            # 检查是否包含订单相关关键词
                            data_str = str(first_data).lower()
                            if any(kw in data_str for kw in ['orderid', 'order_id', 'bizorderid', 'paysucc', 'paid']):
                                return 1  # 订单消息 - 高优先级
                            if 'message' in data_str or 'chat' in data_str:
                                return 2  # 聊天消息 - 中优先级
                    except Exception:
                        pass
                
                # ACK确认消息
                if message_data.get("code") == 200:
                    return 0
            
            return 3  # 默认低优先级
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】解析消息优先级失败: {e}")
            return 3
    
    async def _enqueue_message(self, message_data: dict, websocket, msg_id: str = "unknown") -> bool:
        """
        将消息放入优先级队列
        
        Args:
            message_data: 消息数据
            websocket: WebSocket连接
            msg_id: 消息ID
            
        Returns:
            bool: 是否成功入队
        """
        try:
            # 获取消息优先级
            priority = self._get_message_priority(message_data)
            
            # 创建消息包装对象
            async with self.message_queue_lock:
                self.message_queue_counter += 1
                counter = self.message_queue_counter
            
            message_item = {
                'data': message_data,
                'websocket': websocket,
                'msg_id': msg_id,
                'enqueue_time': time.time(),
                'priority': priority,
            }
            
            # 尝试非阻塞入队
            try:
                self.message_queue.put_nowait((priority, counter, message_item))
                self.queue_stats['received'] += 1
                
                # 高优先级消息日志
                if priority <= 1:
                    logger.info(f"【{self.cookie_id}】📥 高优先级消息入队 [P{priority}][ID:{msg_id}] 队列大小: {self.message_queue.qsize()}")
                else:
                    logger.debug(f"【{self.cookie_id}】📥 消息入队 [P{priority}][ID:{msg_id}] 队列大小: {self.message_queue.qsize()}")
                
                return True
            except asyncio.QueueFull:
                # 队列满时，尝试丢弃最低优先级的旧消息
                self.queue_stats['dropped_full'] += 1
                logger.warning(f"【{self.cookie_id}】⚠️ 消息队列已满({self.message_queue_max_size})，消息[ID:{msg_id}]被丢弃")
                return False
                
        except Exception as e:
            logger.error(f"【{self.cookie_id}】消息入队失败: {self._safe_str(e)}")
            return False
    
    async def _message_worker(self, worker_id: int):
        """
        消息处理工作协程
        
        从队列中取出消息并处理，支持并发处理多个消息
        
        Args:
            worker_id: 工作协程ID
        """
        logger.info(f"【{self.cookie_id}】🔧 消息处理工作协程 #{worker_id} 启动")
        
        while self.message_queue_running:
            try:
                # 设置超时获取，避免无限等待
                try:
                    priority, counter, message_item = await asyncio.wait_for(
                        self.message_queue.get(), 
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    # 超时没有消息，继续循环
                    continue
                
                # 检查消息是否过期
                enqueue_time = message_item['enqueue_time']
                age = time.time() - enqueue_time
                if age > self.message_expire_seconds:
                    self.queue_stats['dropped_expired'] += 1
                    logger.warning(f"【{self.cookie_id}】⏰ 工作协程#{worker_id} 丢弃过期消息 [ID:{message_item['msg_id']}] 已等待{age:.1f}秒")
                    self.message_queue.task_done()
                    continue
                
                # 处理消息
                msg_id = message_item['msg_id']
                try:
                    logger.debug(f"【{self.cookie_id}】🔄 工作协程#{worker_id} 开始处理消息 [P{priority}][ID:{msg_id}] 等待{age:.2f}秒")
                    
                    # 使用信号量控制并发
                    async with self.message_semaphore:
                        self.active_message_tasks += 1
                        try:
                            await self.handle_message(
                                message_item['data'],
                                message_item['websocket'],
                                msg_id
                            )
                            self.queue_stats['processed'] += 1
                        finally:
                            self.active_message_tasks -= 1
                    
                    logger.debug(f"【{self.cookie_id}】✅ 工作协程#{worker_id} 完成消息处理 [ID:{msg_id}]")
                    
                except Exception as e:
                    self.queue_stats['errors'] += 1
                    logger.error(f"【{self.cookie_id}】❌ 工作协程#{worker_id} 处理消息失败 [ID:{msg_id}]: {self._safe_str(e)}")
                finally:
                    self.message_queue.task_done()
                    
            except asyncio.CancelledError:
                logger.info(f"【{self.cookie_id}】🛑 消息处理工作协程 #{worker_id} 被取消")
                break
            except Exception as e:
                logger.error(f"【{self.cookie_id}】工作协程#{worker_id} 异常: {self._safe_str(e)}")
                await asyncio.sleep(1)  # 出错后短暂休息
        
        logger.info(f"【{self.cookie_id}】🔧 消息处理工作协程 #{worker_id} 已停止")
    
    async def _start_message_queue_workers(self):
        """启动消息队列工作协程"""
        if not self.message_queue_enabled:
            logger.info(f"【{self.cookie_id}】消息队列系统已禁用，使用传统处理模式")
            return
        
        self.message_queue_running = True
        self.message_workers = []
        
        # 创建多个工作协程
        for i in range(self.message_queue_workers):
            worker_task = self._create_tracked_task(self._message_worker(i))
            self.message_workers.append(worker_task)
        
        # 启动队列监控任务
        self._create_tracked_task(self._queue_stats_monitor())
        
        logger.info(f"【{self.cookie_id}】🚀 消息队列系统已启动，{self.message_queue_workers}个工作协程")
    
    async def _stop_message_queue_workers(self):
        """停止消息队列工作协程"""
        self.message_queue_running = False
        
        # 取消所有工作协程
        for worker_task in self.message_workers:
            if not worker_task.done():
                worker_task.cancel()
        
        # 等待所有工作协程结束
        if self.message_workers:
            await asyncio.gather(*self.message_workers, return_exceptions=True)
        
        self.message_workers = []
        logger.info(f"【{self.cookie_id}】🛑 消息队列系统已停止")
    
    async def _queue_stats_monitor(self):
        """定期输出队列统计信息"""
        while self.message_queue_running:
            try:
                await asyncio.sleep(60)  # 每60秒输出一次统计
                
                if not self.message_queue_running:
                    break
                
                # 计算统计
                stats = self.queue_stats
                elapsed = time.time() - stats['last_stats_time']
                
                if stats['received'] > 0:
                    process_rate = stats['processed'] / elapsed if elapsed > 0 else 0
                    drop_rate = (stats['dropped_full'] + stats['dropped_expired']) / stats['received'] * 100
                    
                    logger.info(
                        f"【{self.cookie_id}】📊 消息队列统计 - "
                        f"队列大小: {self.message_queue.qsize()}/{self.message_queue_max_size} | "
                        f"收到: {stats['received']} | "
                        f"处理: {stats['processed']} | "
                        f"丢弃(满): {stats['dropped_full']} | "
                        f"丢弃(过期): {stats['dropped_expired']} | "
                        f"错误: {stats['errors']} | "
                        f"处理速率: {process_rate:.1f}/s | "
                        f"丢弃率: {drop_rate:.1f}%"
                    )
                    
                    # 如果丢弃率过高，发出警告
                    if drop_rate > 10:
                        logger.warning(f"【{self.cookie_id}】⚠️ 消息丢弃率过高({drop_rate:.1f}%)，建议增加工作协程数量或检查消息处理效率")
                
                # 重置统计
                stats['last_stats_time'] = time.time()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"【{self.cookie_id}】队列监控异常: {self._safe_str(e)}")

    def is_auto_confirm_enabled(self) -> bool:
        """检查当前账号是否启用自动确认发货"""
        try:
            from db_manager import db_manager
            return db_manager.get_auto_confirm(self.cookie_id)
        except Exception as e:
            logger.error(f"【{self.cookie_id}】获取自动确认发货设置失败: {self._safe_str(e)}")
            return True  # 出错时默认启用

    def is_auto_comment_enabled(self) -> bool:
        """检查当前账号是否启用自动好评"""
        try:
            from db_manager import db_manager
            return db_manager.get_auto_comment(self.cookie_id)
        except Exception as e:
            logger.error(f"【{self.cookie_id}】获取自动好评设置失败: {self._safe_str(e)}")
            return False  # 出错时默认禁用

    async def handle_auto_comment(self, message: dict, msg_time: str, msg_id: str = ""):
        """处理自动好评"""
        try:
            # 检查是否启用自动好评
            if not self.is_auto_comment_enabled():
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 未启用自动好评，跳过')
                return False
            
            # 从消息中提取订单ID
            order_id = self._extract_order_id_for_comment(message)
            if not order_id:
                logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 无法从评价消息中提取订单ID，跳过自动好评')
                return False
            
            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 检测到评价提醒，订单ID: {order_id}')
            
            # 获取激活的好评模板
            from db_manager import db_manager
            template = db_manager.get_active_comment_template(self.cookie_id)
            if not template:
                logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 未设置激活的好评模板，跳过自动好评')
                return False
            
            comment_content = template.get('content', '')
            if not comment_content:
                logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 好评模板内容为空，跳过自动好评')
                return False
            
            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 使用模板"{template.get("name", "")}"进行好评: {comment_content[:50]}...')
            
            # 调用好评接口
            result = await self._call_comment_api(order_id, comment_content)
            result_message = result.get("message", "未知错误")
            batch_id = f"realtime_{int(time.time() * 1000)}"

            try:
                order_info = db_manager.get_order_by_id(order_id) or {}
                db_manager.add_scheduled_rate_log(
                    batch_id=batch_id,
                    cookie_id=self.cookie_id,
                    order_id=order_id,
                    item_id=order_info.get('item_id'),
                    buyer_id=order_info.get('buyer_id'),
                    buyer_nick=order_info.get('buyer_nick'),
                    comment=comment_content,
                    status='success' if result.get('success') else ('cookie_expired' if result.get('session_expired') else 'failed'),
                    message=result_message,
                    raw_response=result.get('raw') or result,
                )
            except Exception as log_e:
                logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 写入自动评价日志失败: {self._safe_str(log_e)}')

            if result.get('success'):
                db_manager.mark_order_rated(order_id, True)
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 订单 {order_id} 自动好评成功')
                return True
            else:
                db_manager.mark_order_rated(order_id, False, result_message)
                logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ❌ 订单 {order_id} 自动好评失败: {result_message}')
                return False
                
        except Exception as e:
            logger.error(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 自动好评异常: {self._safe_str(e)}')
            return False

    def _extract_order_id_for_comment(self, message: dict) -> str:
        """从评价提醒消息中提取订单ID"""
        try:
            order_id = self._extract_order_id(message)
            if order_id:
                logger.info(f'【{self.cookie_id}】评价提醒消息提取到订单ID: {order_id}')
            return order_id
            
        except Exception as e:
            logger.error(f"【{self.cookie_id}】提取评价订单ID失败: {self._safe_str(e)}")
            return None

    async def _call_comment_api(self, order_id: str, comment: str) -> dict:
        """调用本地闲鱼评价接口。"""
        try:
            from utils.rate_service import RateService

            rate_service = RateService(self.cookies_str, account_id=self.cookie_id)
            result = await rate_service.rate_buyer(order_id, comment)

            # RateService 可能在令牌过期时合并 Set-Cookie 并保存到 DB；同步当前实例内存 Cookie。
            refreshed_cookie = getattr(rate_service, 'cookie_string', None)
            if refreshed_cookie and refreshed_cookie != self.cookies_str:
                self.cookies_str = refreshed_cookie
                self.cookies = trans_cookies(refreshed_cookie)
                logger.info(f"【{self.cookie_id}】自动评价后已同步刷新 Cookie 到当前实例")

            return result
        except asyncio.TimeoutError:
            logger.error(f"【{self.cookie_id}】好评接口请求超时")
            return {"success": False, "message": "请求超时"}
        except Exception as e:
            logger.error(f"【{self.cookie_id}】调用好评接口异常: {self._safe_str(e)}")
            return {"success": False, "message": str(e)}

    def can_auto_delivery(self, order_id: str) -> bool:
        """检查是否可以进行自动发货（防重复发货）- 基于订单ID"""
        if not order_id:
            # 如果没有订单ID，则不进行冷却检查，允许发货
            return True

        current_time = time.time()
        last_delivery = self.last_delivery_time.get(order_id, 0)

        if current_time - last_delivery < self.delivery_cooldown:
            logger.info(f"【{self.cookie_id}】订单 {order_id} 在冷却期内，跳过自动发货")
            return False

        return True

    def mark_delivery_sent(self, order_id: str, context: str = "自动发货完成"):
        """标记订单已发货"""
        self.delivery_sent_orders.add(order_id)
        self.last_delivery_time[order_id] = time.time()
        logger.info(f"【{self.cookie_id}】订单 {order_id} 已标记为发货")
        
        # 更新订单状态为已发货
        logger.info(f"【{self.cookie_id}】检查自动发货订单状态处理器: handler_exists={self.order_status_handler is not None}")
        if self.order_status_handler:
            logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.handle_auto_delivery_order_status: {order_id}")
            try:
                success = self.order_status_handler.handle_auto_delivery_order_status(
                    order_id=order_id,
                    cookie_id=self.cookie_id,
                    context=context
                )
                logger.info(f"【{self.cookie_id}】订单状态处理器.handle_auto_delivery_order_status返回结果: {success}")
                if success:
                    logger.info(f"【{self.cookie_id}】订单 {order_id} 状态已更新为已发货")
                else:
                    logger.warning(f"【{self.cookie_id}】订单 {order_id} 状态更新为已发货失败")
            except Exception as e:
                logger.error(f"【{self.cookie_id}】订单状态更新失败: {self._safe_str(e)}")
                import traceback
                logger.error(f"【{self.cookie_id}】详细错误信息: {traceback.format_exc()}")
        else:
            logger.warning(f"【{self.cookie_id}】订单状态处理器为None，跳过自动发货状态更新: {order_id}")

    def _activate_delivery_lock(self, lock_key: str, delay_minutes: int = 10):
        """在发货成功后激活订单延迟锁，避免重复发货。"""
        if not lock_key:
            return

        existing_lock = self._lock_hold_info.get(lock_key)
        if existing_lock and existing_lock.get('locked'):
            return

        self._lock_hold_info[lock_key] = {
            'locked': True,
            'lock_time': time.time(),
            'release_time': None,
            'task': None
        }
        delay_task = asyncio.create_task(self._delayed_lock_release(lock_key, delay_minutes=delay_minutes))
        self._lock_hold_info[lock_key]['task'] = delay_task

    def _resolve_blacklist_user_id(self) -> Optional[int]:
        """获取当前账号归属用户，用于黑名单隔离。"""
        if self.user_id:
            try:
                return int(self.user_id)
            except (TypeError, ValueError):
                pass

        try:
            cookie_details = db_manager.get_cookie_details(self.cookie_id) or {}
            user_id = cookie_details.get('user_id')
            return int(user_id) if user_id else None
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】解析黑名单用户归属失败: {self._safe_str(e)}")
            return None

    def _format_blacklist_block_reason(self, hit: Dict[str, Any], action: str = '自动动作') -> str:
        scope_label = {
            'item': '商品级',
            'account': '账号级',
            'user': '用户级',
        }.get((hit or {}).get('scope'), (hit or {}).get('scope') or '未知级别')
        reason = str((hit or {}).get('reason') or '').strip()
        reason_part = f"，原因：{reason}" if reason else ''
        buyer_id = (hit or {}).get('buyer_id') or '未知买家'
        return f"买家 {buyer_id} 命中个人黑名单 scope={scope_label}{reason_part}，跳过{action}"

    def _check_buyer_blacklist_for_action(self, buyer_id: str = None, item_id: str = None,
                                          order_id: str = None, buyer_nick: str = None,
                                          action: str = '自动动作', channel: str = 'auto',
                                          log_delivery: bool = False) -> Optional[Dict[str, Any]]:
        """检查买家黑名单，命中时可记录发货跳过日志。"""
        normalized_buyer_id = str(buyer_id or '').strip()
        if not normalized_buyer_id:
            return None

        try:
            user_id = self._resolve_blacklist_user_id()
            if not user_id:
                return None

            hit = db_manager.is_buyer_blacklisted(
                user_id=user_id,
                buyer_id=normalized_buyer_id,
                cookie_id=self.cookie_id,
                item_id=str(item_id or '').strip() or None,
            )
            if not hit:
                return None

            block_reason = self._format_blacklist_block_reason(hit, action=action)
            logger.warning(
                f"【{self.cookie_id}】买家 {normalized_buyer_id} 命中个人黑名单，"
                f"scope={hit.get('scope')}, item_id={item_id or ''}, order_id={order_id or ''}，跳过{action}"
            )

            if log_delivery:
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=normalized_buyer_id,
                    buyer_nick=buyer_nick,
                    status='skipped',
                    reason=block_reason,
                    channel=channel,
                    rule_meta={'match_mode': 'blacklist'},
                )
            return hit
        except Exception as e:
            logger.warning(
                f"【{self.cookie_id}】检查买家黑名单失败: buyer_id={normalized_buyer_id}, "
                f"item_id={item_id or ''}, error={self._safe_str(e)}"
            )
            return None

    def _record_delivery_log(self, order_id: str = None, item_id: str = None, buyer_id: str = None,
                             buyer_nick: str = None, status: str = 'failed', reason: str = None,
                             channel: str = 'auto', rule_meta: dict = None):
        """记录真实发货事件日志（成功/失败）。"""
        try:
            from db_manager import db_manager
            meta = rule_meta or {}
            log_prefix = f"【{self.cookie_id}】"
            resolved_buyer_nick = self._resolve_delivery_log_buyer_nick(
                buyer_nick,
                order_id=order_id,
                buyer_id=buyer_id,
                log_prefix=log_prefix,
            )
            normalized_status = str(status or 'failed').strip().lower()
            if normalized_status not in {'success', 'failed', 'skipped'}:
                normalized_status = 'failed'
            db_manager.create_delivery_log(
                user_id=self.user_id,
                cookie_id=self.cookie_id,
                order_id=order_id,
                item_id=item_id,
                buyer_id=buyer_id,
                buyer_nick=resolved_buyer_nick,
                rule_id=meta.get('rule_id'),
                rule_keyword=meta.get('rule_keyword'),
                card_type=meta.get('card_type'),
                match_mode=meta.get('match_mode'),
                channel=channel or 'auto',
                status=normalized_status,
                reason=self._format_delivery_log_reason(reason, meta)
            )
        except Exception as log_e:
            logger.error(f"【{self.cookie_id}】记录发货日志失败: {self._safe_str(log_e)}")

    def _format_delivery_log_reason(self, reason: str = None, rule_meta: dict = None) -> str:
        """将规格模式上下文拼接到发货日志原因中，便于后续排查。"""
        meta = rule_meta or {}
        context_parts = []

        order_spec_mode = meta.get('order_spec_mode')
        rule_spec_mode = meta.get('rule_spec_mode')
        item_config_mode = meta.get('item_config_mode')

        if order_spec_mode:
            context_parts.append(f"order_spec_mode={order_spec_mode}")
        if rule_spec_mode:
            context_parts.append(f"rule_spec_mode={rule_spec_mode}")
        if item_config_mode:
            context_parts.append(f"item_config_mode={item_config_mode}")

        reason_text = (reason or '').strip()
        if not context_parts:
            return reason_text

        if any(part.split('=')[0] + '=' in reason_text for part in context_parts):
            return reason_text

        if not reason_text:
            reason_text = '未提供发货日志原因'

        return f"{reason_text} [{', '.join(context_parts)}]"

    async def _finalize_delivery_after_send(self, delivery_meta: dict = None, order_id: str = None,
                                            item_id: str = None, skip_confirm: bool = False,
                                            force_confirm: bool = False):
        """在消息发送成功后提交发货副作用：消费卡密、更新计数、确认发货。"""
        meta = delivery_meta or {}

        if not meta.get('success'):
            return {
                'success': False,
                'error': '发货元数据无效，无法提交副作用'
            }

        from db_manager import db_manager

        consume_required = bool(meta.get('data_card_pending_consume'))
        rule_id = meta.get('rule_id')
        card_id = meta.get('card_id')
        card_type = meta.get('card_type')
        expected_line = meta.get('data_line')
        reservation_id = meta.get('data_reservation_id')
        reservation_already_finalized = False

        if consume_required:
            if reservation_id:
                finalize_state = db_manager.finalize_batch_data_reservation(reservation_id)
                if not finalize_state.get('success'):
                    return {
                        'success': False,
                        'error': '批量数据预占完成失败，已中止后续确认发货'
                    }
                reservation_already_finalized = bool(finalize_state.get('already_finalized'))
            elif not card_id or card_type != 'data':
                return {
                    'success': False,
                    'error': '批量数据卡券元数据不完整，无法提交消费'
                }
            else:
                consumed = db_manager.consume_specific_batch_data(card_id, expected_line)
                if not consumed:
                    return {
                        'success': False,
                        'error': '批量数据消费失败，已中止后续确认发货'
                    }

        if rule_id and not consume_required:
            db_manager.increment_delivery_times(rule_id)

        if order_id and not skip_confirm:
            if not force_confirm and not self.is_auto_confirm_enabled():
                if meta.get('pending_platform_confirm') or meta.get('confirm_retry_required'):
                    return {
                        'success': False,
                        'error': '自动确认发货已关闭，无法补确认平台发货状态',
                        'pending_confirm': True,
                        'platform_confirm_failed': True,
                        'confirm_retry_required': True,
                    }
                logger.info(f"自动确认发货已关闭，跳过订单 {order_id}")
            else:
                current_time = time.time()
                should_confirm = True

                if order_id in self.confirmed_orders:
                    last_confirm_time = self.confirmed_orders[order_id]
                    if current_time - last_confirm_time < self.order_confirm_cooldown:
                        logger.info(f"订单 {order_id} 已在 {self.order_confirm_cooldown} 秒内确认过，跳过重复确认")
                        should_confirm = False

                if should_confirm:
                    logger.info(f"开始自动确认发货: 订单ID={order_id}, 商品ID={item_id}")
                    confirm_result = await self.auto_confirm(order_id, item_id)
                    if confirm_result.get('success'):
                        self.confirmed_orders[order_id] = current_time
                        logger.info(f"🎉 自动确认发货成功！订单ID: {order_id}")
                    else:
                        confirm_error = confirm_result.get('error', '未知错误')
                        stop_confirm_retry = self._is_non_retryable_platform_confirm_error(confirm_error, confirm_result)
                        return {
                            'success': False,
                            'error': f"自动确认发货失败: {confirm_error}",
                            'pending_confirm': not stop_confirm_retry,
                            'platform_confirm_failed': True,
                            'confirm_retry_required': not stop_confirm_retry,
                            'non_retryable_platform_confirm': stop_confirm_retry,
                            'stop_confirm_retry': stop_confirm_retry,
                            'session_expired': bool(confirm_result.get('session_expired')),
                            'need_relogin': bool(confirm_result.get('need_relogin')),
                            'confirm_result': confirm_result,
                        }

        if rule_id and consume_required and not reservation_already_finalized:
            db_manager.increment_delivery_times(rule_id)

        return {
            'success': True
        }

    def _mark_data_reservation_sent_if_needed(self, delivery_meta: dict = None) -> bool:
        meta = delivery_meta or {}
        reservation_id = meta.get('data_reservation_id')
        if not reservation_id:
            return True

        from db_manager import db_manager
        return db_manager.mark_batch_data_reservation_sent(reservation_id)

    def _release_data_reservation_if_needed(self, delivery_meta: dict = None, error: str = None) -> bool:
        meta = delivery_meta or {}
        reservation_id = meta.get('data_reservation_id')
        if not reservation_id:
            return True

        from db_manager import db_manager
        return db_manager.release_batch_data_reservation(reservation_id, error=error)

    def _get_pending_delivery_finalization_meta(self, order_id: str, delivery_unit_index: int = 1):
        if not order_id:
            return None

        from db_manager import db_manager
        state = db_manager.get_delivery_finalization_state(order_id, delivery_unit_index)
        if not state or state.get('status') != 'sent':
            return None

        delivery_meta = state.get('delivery_meta') or {}
        delivery_meta.setdefault('success', True)
        delivery_meta.setdefault('delivery_unit_index', delivery_unit_index)
        return delivery_meta

    def _persist_delivery_finalization_state(self, order_id: str, item_id: str, buyer_id: str,
                                             delivery_meta: dict = None, channel: str = 'auto',
                                             status: str = 'sent', last_error: str = None) -> bool:
        if not order_id:
            return False

        from db_manager import db_manager
        meta = delivery_meta or {}
        unit_index = int(meta.get('delivery_unit_index') or 1)
        return db_manager.upsert_delivery_finalization_state(
            order_id=order_id,
            unit_index=unit_index,
            cookie_id=self.cookie_id,
            item_id=item_id,
            buyer_id=buyer_id,
            channel=channel,
            status=status,
            delivery_meta=meta,
            last_error=last_error,
        )

    def _get_normalized_local_order_status(self, order_id: str) -> str:
        if not order_id:
            return ''
        try:
            from db_manager import db_manager
            order = db_manager.get_order_by_id(order_id)
            if not order:
                return ''
            return db_manager._normalize_order_status(order.get('order_status'))
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】读取本地订单状态失败: order_id={order_id}, error={self._safe_str(e)}")
            return ''

    def _is_platform_confirm_terminal_status(self, status: str) -> bool:
        normalized = str(status or '').strip()
        return normalized in {
            'shipped',
            'completed',
            'cancelled',
            'refunding',
            'refund_cancelled',
        }

    def _is_non_retryable_platform_confirm_error(self, error: Any, confirm_result: Any = None) -> bool:
        error_text = str(error or '')
        if isinstance(confirm_result, dict) and (
            confirm_result.get('order_status_error')
            or confirm_result.get('non_retryable')
            or confirm_result.get('stop_confirm_retry')
        ):
            return True
        return 'ORDER_STATUS_ERROR' in error_text or '订单状态不正确' in error_text

    def _mark_delivery_platform_confirm_no_longer_required(self, order_id: str, item_id: str, buyer_id: str,
                                                           delivery_meta: dict = None, reason: str = '',
                                                           channel: str = 'auto') -> None:
        """平台已处于不可/无需补确认的终态时，清理待补确认，避免无限重试。"""
        if not order_id:
            return
        meta = dict(delivery_meta or {})
        meta.update({
            'pending_confirm': False,
            'pending_platform_confirm': False,
            'confirm_retry_required': False,
            'platform_confirm_status': 'not_required_terminal',
            'confirm_retry_stopped': True,
            'confirm_retry_stopped_reason': str(reason or '订单已处于平台终态，无需继续补确认'),
            'confirm_retry_stopped_at': datetime.now().isoformat(timespec='seconds'),
        })
        self._persist_delivery_finalization_state(
            order_id=order_id,
            item_id=item_id,
            buyer_id=buyer_id,
            delivery_meta=meta,
            channel=channel or 'auto',
            status='finalized',
            last_error=str(reason or '订单已处于平台终态，无需继续补确认'),
        )
        logger.warning(f"【{self.cookie_id}】订单 {order_id} 停止补确认重试: {reason or '订单已处于平台终态'}")

    def _is_platform_confirm_failure_error(self, error: Any) -> bool:
        """判断发送成功后的失败是否属于闲鱼平台确认发货失败。"""
        error_text = str(error or '')
        return any(keyword in error_text for keyword in (
            '自动确认发货失败',
            'FAIL_SYS_SESSION_EXPIRED',
            'Session过期',
            '令牌过期',
            'TOKEN_EXPIRED',
            'TOKEN_EXOIRED',
        ))

    def _is_platform_confirm_auth_error(self, error: Any) -> bool:
        """判断确认发货失败是否明确需要恢复 Cookie/Token 登录态。"""
        error_text = str(error or '')
        return any(keyword in error_text for keyword in (
            'FAIL_SYS_SESSION_EXPIRED',
            'Session过期',
            '令牌过期',
            'FAIL_SYS_TOKEN_EXPIRED',
            'FAIL_SYS_TOKEN_EXOIRED',
            'TOKEN_EXPIRED',
            'TOKEN_EXOIRED',
        ))

    def _schedule_auth_recovery_after_platform_confirm_failure(self, order_id: str = None, error: Any = None) -> None:
        """确认发货因登录态失败时，后台触发认证恢复；恢复成功后会自动扫待补确认。"""
        if not self._is_platform_confirm_auth_error(error):
            return

        current_time = time.time()
        if current_time - self.last_platform_confirm_auth_recovery_time < self.platform_confirm_auth_recovery_cooldown:
            logger.info(
                f"【{self.cookie_id}】确认发货认证恢复仍在冷却期内，跳过重复触发: order_id={order_id}"
            )
            return
        self.last_platform_confirm_auth_recovery_time = current_time

        try:
            self._create_tracked_task(
                self._recover_auth_after_platform_confirm_failure(order_id=order_id, error=self._safe_str(error))
            )
        except RuntimeError:
            return
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】调度确认发货认证恢复失败: {self._safe_str(e)}")

    async def _recover_auth_after_platform_confirm_failure(self, order_id: str = None, error: str = '') -> None:
        """确认发货 Session/Token 过期后的后台恢复流程。"""
        try:
            logger.warning(
                f"【{self.cookie_id}】确认发货失败触发认证恢复: order_id={order_id}, error={error}"
            )
            await asyncio.sleep(1)
            token = await self.refresh_token(captcha_retry_count=1, allow_password_login_recovery=True)
            if token:
                logger.info(f"【{self.cookie_id}】确认发货失败后的认证恢复成功，准备自动补确认: order_id={order_id}")
                self._schedule_pending_platform_confirm_retry("确认发货认证恢复成功")
            else:
                logger.warning(
                    f"【{self.cookie_id}】确认发货失败后的认证恢复未成功: "
                    f"status={self.last_token_refresh_status}, error={self.last_token_refresh_error_message}"
                )
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】确认发货失败后的认证恢复异常: {self._safe_str(e)}")

    def _mark_delivery_pending_platform_confirm(self, order_id: str, item_id: str, buyer_id: str,
                                                delivery_meta: dict = None, confirm_error: str = None,
                                                expected_quantity: int = 1,
                                                context: str = "平台确认发货失败，等待补确认",
                                                channel: str = 'auto'):
        """记录“卡券已发出、平台确认失败、等待补确认”的订单状态。"""
        if not order_id:
            return None

        error_text = str(confirm_error or '平台确认发货失败').strip()
        pending_reason = f"卡券已发出，平台确认发货失败，等待补确认: {error_text}"
        meta = dict(delivery_meta or {})
        meta.update({
            'success': True,
            'delivery_message_status': 'sent',
            'platform_confirm_status': 'failed',
            'pending_confirm': True,
            'pending_platform_confirm': True,
            'confirm_retry_required': True,
            'confirm_error': error_text,
            'confirm_failed_at': datetime.now().isoformat(timespec='seconds'),
        })

        self._persist_delivery_finalization_state(
            order_id=order_id,
            item_id=item_id,
            buyer_id=buyer_id,
            delivery_meta=meta,
            channel=channel,
            status='sent',
            last_error=pending_reason
        )
        summary = self._sync_order_delivery_progress(
            order_id=order_id,
            cookie_id=self.cookie_id,
            expected_quantity=expected_quantity,
            context=context
        )
        logger.warning(f"【{self.cookie_id}】订单 {order_id} 已记录为：卡券已发出、平台确认失败、等待补确认。原因: {error_text}")
        self._schedule_auth_recovery_after_platform_confirm_failure(order_id=order_id, error=error_text)
        return summary

    def _get_order_expected_delivery_quantity(self, order_id: str) -> int:
        """获取订单应发货单元数，回退为已记录单元最大序号。"""
        expected = 1
        try:
            from db_manager import db_manager
            order = db_manager.get_order_by_id(order_id) if order_id else None
            if order and order.get('quantity'):
                expected = max(1, int(order.get('quantity') or 1))
            states = db_manager.get_delivery_finalization_states(order_id) if order_id else []
            for state in states:
                try:
                    expected = max(expected, int(state.get('unit_index') or 1))
                except (TypeError, ValueError):
                    continue
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】获取订单应发数量失败: order_id={order_id}, error={self._safe_str(e)}")
        return expected

    async def retry_pending_platform_confirms(self, order_id: str = None, limit: int = 50,
                                              source: str = 'manual') -> Dict[str, Any]:
        """只重试平台确认发货，不重复发送卡券。"""
        from db_manager import db_manager

        if not order_id and source == 'auto':
            current_time = time.time()
            if current_time - self.last_pending_platform_confirm_retry_time < self.pending_platform_confirm_retry_cooldown:
                return {
                    'success': True,
                    'processed': 0,
                    'message': '待补确认自动扫描仍在冷却期内，已跳过'
                }

        async with self.pending_platform_confirm_retry_lock:
            if not order_id and source == 'auto':
                self.last_pending_platform_confirm_retry_time = time.time()

            states = db_manager.get_pending_platform_confirm_states(
                cookie_id=self.cookie_id,
                order_id=order_id,
                limit=limit,
            )
            if not states:
                return {
                    'success': True,
                    'processed': 0,
                    'confirmed': 0,
                    'failed': 0,
                    'message': '没有待补确认订单'
                }

            processed = 0
            confirmed = 0
            failed = 0
            results = []
            touched_orders = set()

            for state in states:
                state_order_id = state.get('order_id')
                unit_index = int(state.get('unit_index') or 1)
                state_item_id = state.get('item_id')
                state_buyer_id = state.get('buyer_id')
                meta = dict(state.get('delivery_meta') or {})
                meta.setdefault('success', True)
                meta['delivery_unit_index'] = unit_index
                processed += 1

                logger.info(
                    f"【{self.cookie_id}】开始补确认发货: order_id={state_order_id}, "
                    f"unit={unit_index}, source={source}"
                )

                local_order_status = self._get_normalized_local_order_status(state_order_id)
                if self._is_platform_confirm_terminal_status(local_order_status):
                    stop_reason = f"本地订单状态已是 {local_order_status}，无需继续补确认平台发货状态"
                    self._mark_delivery_platform_confirm_no_longer_required(
                        order_id=state_order_id,
                        item_id=state_item_id,
                        buyer_id=state_buyer_id,
                        delivery_meta=meta,
                        reason=stop_reason,
                        channel=source or 'manual',
                    )
                    self._record_delivery_log(
                        order_id=state_order_id,
                        item_id=state_item_id,
                        buyer_id=state_buyer_id,
                        status='success',
                        reason=f'停止补确认重试: {stop_reason}',
                        channel=source or 'manual',
                        rule_meta=meta,
                    )
                    confirmed += 1
                    touched_orders.add(state_order_id)
                    results.append({
                        'order_id': state_order_id,
                        'unit_index': unit_index,
                        'success': True,
                        'message': stop_reason,
                        'stopped_retry': True,
                    })
                    continue

                finalize_result = await self._finalize_delivery_after_send(
                    delivery_meta=meta,
                    order_id=state_order_id,
                    item_id=state_item_id,
                    force_confirm=True,
                )
                expected_quantity = self._get_order_expected_delivery_quantity(state_order_id)
                touched_orders.add(state_order_id)

                if finalize_result.get('success'):
                    meta.update({
                        'pending_confirm': False,
                        'pending_platform_confirm': False,
                        'confirm_retry_required': False,
                        'platform_confirm_status': 'success',
                        'confirm_success_at': datetime.now().isoformat(timespec='seconds'),
                        'confirm_retry_source': source,
                    })
                    self._persist_delivery_finalization_state(
                        order_id=state_order_id,
                        item_id=state_item_id,
                        buyer_id=state_buyer_id,
                        delivery_meta=meta,
                        channel=source or 'manual',
                        status='finalized',
                        last_error=None,
                    )
                    self._record_delivery_log(
                        order_id=state_order_id,
                        item_id=state_item_id,
                        buyer_id=state_buyer_id,
                        status='success',
                        reason='待补确认订单平台确认发货成功',
                        channel=source or 'manual',
                        rule_meta=meta,
                    )
                    confirmed += 1
                    results.append({
                        'order_id': state_order_id,
                        'unit_index': unit_index,
                        'success': True,
                        'message': '平台确认发货成功'
                    })
                else:
                    error_text = finalize_result.get('error') or '平台确认发货仍失败'
                    confirm_result = finalize_result.get('confirm_result') or {}
                    if self._is_non_retryable_platform_confirm_error(error_text, confirm_result):
                        stop_reason = f"平台返回订单状态不正确，停止补确认重试: {error_text}"
                        self._mark_delivery_platform_confirm_no_longer_required(
                            order_id=state_order_id,
                            item_id=state_item_id,
                            buyer_id=state_buyer_id,
                            delivery_meta=meta,
                            reason=stop_reason,
                            channel=source or 'manual',
                        )
                        self._record_delivery_log(
                            order_id=state_order_id,
                            item_id=state_item_id,
                            buyer_id=state_buyer_id,
                            status='success',
                            reason=f'停止补确认重试: {stop_reason}',
                            channel=source or 'manual',
                            rule_meta=meta,
                        )
                        confirmed += 1
                        results.append({
                            'order_id': state_order_id,
                            'unit_index': unit_index,
                            'success': True,
                            'message': stop_reason,
                            'stopped_retry': True,
                        })
                        continue

                    self._mark_delivery_pending_platform_confirm(
                        order_id=state_order_id,
                        item_id=state_item_id,
                        buyer_id=state_buyer_id,
                        delivery_meta=meta,
                        confirm_error=error_text,
                        expected_quantity=expected_quantity,
                        context=f"{source}补确认发货失败",
                        channel=source or 'manual',
                    )
                    self._record_delivery_log(
                        order_id=state_order_id,
                        item_id=state_item_id,
                        buyer_id=state_buyer_id,
                        status='failed',
                        reason=f'补确认发货失败: {error_text}',
                        channel=source or 'manual',
                        rule_meta=meta,
                    )
                    failed += 1
                    results.append({
                        'order_id': state_order_id,
                        'unit_index': unit_index,
                        'success': False,
                        'message': error_text
                    })

            for touched_order_id in touched_orders:
                try:
                    self._sync_order_delivery_progress(
                        order_id=touched_order_id,
                        cookie_id=self.cookie_id,
                        expected_quantity=self._get_order_expected_delivery_quantity(touched_order_id),
                        context=f"{source}补确认发货后同步状态"
                    )
                except Exception as sync_e:
                    logger.warning(f"【{self.cookie_id}】补确认后同步订单状态失败: order_id={touched_order_id}, error={self._safe_str(sync_e)}")

            message = f"补确认完成：处理 {processed} 个，成功 {confirmed} 个，失败 {failed} 个"
            logger.info(f"【{self.cookie_id}】{message}")
            return {
                'success': failed == 0,
                'processed': processed,
                'confirmed': confirmed,
                'failed': failed,
                'results': results,
                'message': message,
            }

    def _schedule_pending_platform_confirm_retry(self, reason: str = '') -> None:
        """认证恢复后异步触发待补确认扫描。"""
        try:
            if not self.background_tasks and not self.ws:
                return
            self._create_tracked_task(self._retry_pending_platform_confirms_later(reason=reason))
        except RuntimeError:
            return
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】调度待补确认扫描失败: {self._safe_str(e)}")

    async def _retry_pending_platform_confirms_later(self, reason: str = '') -> None:
        await asyncio.sleep(2)
        try:
            result = await self.retry_pending_platform_confirms(source='auto', limit=20)
            if result.get('processed'):
                logger.info(f"【{self.cookie_id}】认证恢复后自动补确认完成({reason or 'unknown'}): {result.get('message')}")
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】认证恢复后自动补确认异常: {self._safe_str(e)}")

    def _summarize_delivery_progress(self, order_id: str, expected_quantity: int = 1):
        if not order_id:
            return {
                'order_id': order_id,
                'expected_quantity': max(1, int(expected_quantity or 1)),
                'aggregate_status': 'pending_ship',
                'finalized_count': 0,
                'pending_finalize_count': 0,
                'remaining_count': max(1, int(expected_quantity or 1)),
                'finalized_unit_indexes': [],
                'pending_finalize_unit_indexes': [],
                'remaining_unit_indexes': list(range(1, max(1, int(expected_quantity or 1)) + 1)),
                'states': [],
            }

        from db_manager import db_manager
        return db_manager.get_delivery_progress_summary(order_id, expected_quantity=expected_quantity)

    def _resolve_external_order_status(self, current_status: str, incoming_status: str, source: str):
        from db_manager import db_manager

        merged_status = db_manager.resolve_external_order_status(current_status, incoming_status, source=source)
        normalized_current = db_manager._normalize_order_status(current_status)

        if merged_status and merged_status != normalized_current:
            return merged_status
        return None

    def _normalize_order_amount_text(self, value: Any):
        text = str(value or '').strip()
        if not text:
            return None
        text = text.replace('¥', '').replace('￥', '').replace(',', '')
        match = re.search(r'\d+(?:\.\d{1,2})?', text)
        if not match:
            return None
        try:
            return f"{float(match.group(0)):.2f}"
        except (TypeError, ValueError):
            return None

    def _parse_order_amount_float(self, value: Any):
        normalized = self._normalize_order_amount_text(value)
        if normalized is None:
            return None
        try:
            return float(normalized)
        except (TypeError, ValueError):
            return None

    def _has_bargain_success_evidence(self, order: dict = None) -> bool:
        order = order or {}
        return bool(order.get('bargain_success_detected'))

    def _mark_order_bargain_flow(self, order_id: str, item_id: str = None, buyer_id: str = None,
                                 sid: str = None, *, apply_configured_price: bool = False,
                                 success_detected=..., context: str = '') -> bool:
        if not order_id:
            return False

        from db_manager import db_manager

        existing_order = db_manager.get_order_by_id(order_id) or {}
        effective_item_id = item_id or existing_order.get('item_id')
        effective_buyer_id = buyer_id or existing_order.get('buyer_id')
        effective_sid = sid or existing_order.get('sid')
        amount_to_save = None

        if apply_configured_price and effective_item_id:
            item_config = db_manager.get_item_info(self.cookie_id, effective_item_id)
            configured_amount = self._normalize_order_amount_text(item_config.get('item_price') if item_config else None)
            configured_amount_value = self._parse_order_amount_float(configured_amount)
            existing_amount_value = self._parse_order_amount_float(existing_order.get('amount'))
            if configured_amount_value is not None and (
                existing_amount_value is None or configured_amount_value < existing_amount_value - 0.009
            ):
                amount_to_save = configured_amount

        success = db_manager.insert_or_update_order(
            order_id=order_id,
            item_id=effective_item_id,
            buyer_id=effective_buyer_id,
            sid=effective_sid,
            amount=amount_to_save,
            cookie_id=self.cookie_id,
            bargain_flow_detected=True,
            bargain_success_detected=success_detected,
        )

        if success:
            logger.info(
                f"【{self.cookie_id}】标记订单为小刀流程: order_id={order_id}, context={context or 'unknown'}, "
                f"apply_configured_price={apply_configured_price}, amount_override={amount_to_save or ''}, "
                f"success_detected={success_detected if success_detected is not ... else 'unchanged'}"
            )
        else:
            logger.warning(
                f"【{self.cookie_id}】标记订单小刀流程失败: order_id={order_id}, context={context or 'unknown'}"
            )
        return success

    def _apply_bargain_amount_override(self, order_id: str, item_id: str, amount: Any, amount_source: str,
                                       existing_order: dict = None, item_config: dict = None):
        existing_order = existing_order or {}
        if not existing_order.get('bargain_flow_detected'):
            return amount, amount_source

        configured_amount = self._normalize_order_amount_text(item_config.get('item_price') if item_config else None)
        configured_amount_value = self._parse_order_amount_float(configured_amount)
        if configured_amount_value is None:
            return amount, amount_source

        incoming_amount = self._normalize_order_amount_text(amount)
        incoming_amount_value = self._parse_order_amount_float(incoming_amount)

        if incoming_amount_value is None:
            logger.warning(
                f"【{self.cookie_id}】小刀订单缺少可信金额，回退为商品配置价: "
                f"order_id={order_id}, item_id={item_id}, configured_amount={configured_amount}"
            )
            return configured_amount, 'bargain_item_price_locked'

        if incoming_amount_value > configured_amount_value + 0.009:
            logger.warning(
                f"【{self.cookie_id}】检测到小刀订单仍返回原价，使用商品配置价覆盖: "
                f"order_id={order_id}, item_id={item_id}, incoming_amount={incoming_amount}, "
                f"configured_amount={configured_amount}, amount_source={amount_source}"
            )
            return configured_amount, 'bargain_item_price_locked'

        return incoming_amount, amount_source

    def _resolve_delivery_progress_order_status(self, current_status: str, aggregate_status: str):
        from db_manager import db_manager

        normalized_current = db_manager._normalize_order_status(current_status)
        normalized_aggregate = db_manager._normalize_order_status(aggregate_status)

        if not normalized_aggregate or normalized_aggregate == 'unknown':
            return None

        if not normalized_current or normalized_current == 'unknown':
            return normalized_aggregate

        if normalized_current in {'completed', 'refunding', 'cancelled'} and normalized_aggregate in {
            'pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped'
        }:
            logger.warning(
                f"【{self.cookie_id}】保留订单终态，忽略发货进度覆盖: current={normalized_current}, incoming={normalized_aggregate}"
            )
            return normalized_current

        if normalized_current == 'shipped' and normalized_aggregate in {'pending_ship', 'partial_success', 'partial_pending_finalize'}:
            logger.warning(
                f"【{self.cookie_id}】保留已发货状态，忽略较低发货进度覆盖: current={normalized_current}, incoming={normalized_aggregate}"
            )
            return normalized_current

        if normalized_current in {'partial_success', 'partial_pending_finalize'} and normalized_aggregate == 'pending_ship':
            logger.warning(
                f"【{self.cookie_id}】保留部分发货状态，忽略待发货覆盖: current={normalized_current}, incoming={normalized_aggregate}"
            )
            return normalized_current

        return normalized_aggregate

    def _sync_order_delivery_progress(self, order_id: str, cookie_id: str, expected_quantity: int = 1,
                                      context: str = "自动发货进度同步"):
        summary = self._summarize_delivery_progress(order_id, expected_quantity=expected_quantity)
        aggregate_status = summary.get('aggregate_status') or 'pending_ship'
        previous_status = None

        try:
            from db_manager import db_manager
            current_order = db_manager.get_order_by_id(order_id) if order_id else None
            previous_status = db_manager._normalize_order_status(current_order.get('order_status')) if current_order else None
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】读取订单旧状态失败: {self._safe_str(e)}")

        logger.info(
            f"【{self.cookie_id}】同步订单发货进度: order_id={order_id}, status={aggregate_status}, "
            f"finalized={summary.get('finalized_count')}/{summary.get('expected_quantity')}, "
            f"pending_finalize={summary.get('pending_finalize_count')}, remaining={summary.get('remaining_count')}"
        )

        status_to_write = self._resolve_delivery_progress_order_status(previous_status, aggregate_status)

        if aggregate_status in {'shipped', 'partial_success', 'partial_pending_finalize'}:
            self.delivery_sent_orders.add(order_id)
            self.last_delivery_time[order_id] = time.time()

        if self.order_status_handler and status_to_write == 'shipped' and previous_status != 'shipped':
            try:
                self.order_status_handler.handle_auto_delivery_order_status(
                    order_id=order_id,
                    cookie_id=cookie_id,
                    context=context
                )
            except Exception as e:
                logger.warning(f"【{self.cookie_id}】通过状态处理器同步已发货状态失败: {self._safe_str(e)}")

        try:
            from db_manager import db_manager
            success = True
            if status_to_write and status_to_write != previous_status:
                success = db_manager.insert_or_update_order(order_id=order_id, order_status=status_to_write, cookie_id=cookie_id)

            if success and status_to_write in {'partial_success', 'partial_pending_finalize'} and previous_status != status_to_write:
                try:
                    from order_event_hub import publish_order_update_event
                    publish_order_update_event(order_id, source='delivery_progress_sync')
                except Exception as publish_e:
                    logger.warning(
                        f"【{self.cookie_id}】发布部分发货实时事件失败: order_id={order_id}, error={self._safe_str(publish_e)}"
                    )
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】写入订单聚合发货状态失败: {self._safe_str(e)}")

        return summary

    async def _delayed_lock_release(self, lock_key: str, delay_minutes: int = 10):
        """
        延迟释放锁的异步任务

        Args:
            lock_key: 锁的键
            delay_minutes: 延迟时间（分钟），默认10分钟
        """
        try:
            delay_seconds = delay_minutes * 60
            logger.info(f"【{self.cookie_id}】订单锁 {lock_key} 将在 {delay_minutes} 分钟后释放")

            # 等待指定时间
            await asyncio.sleep(delay_seconds)

            # 检查锁是否仍然存在且需要释放
            if lock_key in self._lock_hold_info:
                lock_info = self._lock_hold_info[lock_key]
                if lock_info.get('locked', False):
                    # 释放锁
                    lock_info['locked'] = False
                    lock_info['release_time'] = time.time()
                    logger.info(f"【{self.cookie_id}】订单锁 {lock_key} 延迟释放完成")

                    # 清理锁信息（可选，也可以保留用于统计）
                    # del self._lock_hold_info[lock_key]

        except asyncio.CancelledError:
            logger.info(f"【{self.cookie_id}】订单锁 {lock_key} 延迟释放任务被取消")
            raise
        except Exception as e:
            logger.error(f"【{self.cookie_id}】订单锁 {lock_key} 延迟释放失败: {self._safe_str(e)}")

    def is_lock_held(self, lock_key: str) -> bool:
        """
        检查指定的锁是否仍在持有状态

        Args:
            lock_key: 锁的键

        Returns:
            bool: True表示锁仍在持有，False表示锁已释放或不存在
        """
        if lock_key not in self._lock_hold_info:
            return False

        lock_info = self._lock_hold_info[lock_key]
        return lock_info.get('locked', False)

    def cleanup_expired_locks(self, max_age_hours: int = 24):
        """
        清理过期的锁（包括自动发货锁和订单详情锁）

        Args:
            max_age_hours: 锁的最大保留时间（小时），默认24小时
        """
        try:
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600

            # 清理自动发货锁
            expired_delivery_locks = []
            for order_id, last_used in self._lock_usage_times.items():
                if current_time - last_used > max_age_seconds:
                    expired_delivery_locks.append(order_id)

            # 清理过期的自动发货锁
            for order_id in expired_delivery_locks:
                if order_id in self._order_locks:
                    del self._order_locks[order_id]
                if order_id in self._lock_usage_times:
                    del self._lock_usage_times[order_id]
                # 清理锁持有信息
                if order_id in self._lock_hold_info:
                    lock_info = self._lock_hold_info[order_id]
                    # 取消延迟释放任务
                    if 'task' in lock_info and lock_info['task']:
                        lock_info['task'].cancel()
                    del self._lock_hold_info[order_id]

            # 清理订单详情锁
            expired_detail_locks = []
            for order_id, last_used in self._order_detail_lock_times.items():
                if current_time - last_used > max_age_seconds:
                    expired_detail_locks.append(order_id)

            # 清理过期的订单详情锁
            for order_id in expired_detail_locks:
                if order_id in self._order_detail_locks:
                    del self._order_detail_locks[order_id]
                if order_id in self._order_detail_lock_times:
                    del self._order_detail_lock_times[order_id]

            expired_refresh_marks = []
            for order_id, refresh_info in self.order_detail_force_refresh_marks.items():
                refresh_timestamp = refresh_info.get('timestamp', 0) if isinstance(refresh_info, dict) else 0
                if current_time - refresh_timestamp > max_age_seconds:
                    expired_refresh_marks.append(order_id)

            for order_id in expired_refresh_marks:
                self.order_detail_force_refresh_marks.pop(order_id, None)

            total_expired = len(expired_delivery_locks) + len(expired_detail_locks) + len(expired_refresh_marks)
            if total_expired > 0:
                logger.info(
                    f"【{self.cookie_id}】清理了 {total_expired} 个过期锁/标记 "
                    f"(发货锁: {len(expired_delivery_locks)}, 详情锁: {len(expired_detail_locks)}, 刷新标记: {len(expired_refresh_marks)})"
                )
                logger.warning(f"【{self.cookie_id}】当前锁数量 - 发货锁: {len(self._order_locks)}, 详情锁: {len(self._order_detail_locks)}")

        except Exception as e:
            logger.error(f"【{self.cookie_id}】清理过期锁时发生错误: {self._safe_str(e)}")

    def _get_order_status_priority(self, status: str) -> int:
        normalized_status = db_manager._normalize_order_status(status)
        priority_map = {
            'unknown': 0,
            'processing': 10,
            'pending_payment': 15,
            'pending_ship': 20,
            'partial_success': 30,
            'partial_pending_finalize': 30,
            'shipped': 40,
            'completed': 50,
            'refunding': 60,
            'refund_cancelled': 65,
            'cancelled': 70,
        }
        return priority_map.get(normalized_status or 'unknown', 0)

    def _has_delivery_progress_evidence(self, order_id: str) -> bool:
        normalized_order_id = str(order_id or '').strip()
        if not normalized_order_id:
            return False

        try:
            summary = self._summarize_delivery_progress(normalized_order_id, expected_quantity=1) or {}
        except Exception as summary_error:
            logger.warning(
                f"【{self.cookie_id}】读取订单发货进度失败，按已有发货证据处理: "
                f"order_id={normalized_order_id}, error={self._safe_str(summary_error)}"
            )
            return True

        state_count = int(summary.get('state_count') or 0)
        finalized_count = int(summary.get('finalized_count') or 0)
        pending_finalize_count = int(summary.get('pending_finalize_count') or 0)
        return state_count > 0 or finalized_count > 0 or pending_finalize_count > 0

    def _reserve_order_detail_force_refresh(self, order_id: str, *, reason: str,
                                            log_prefix: str = "", cooldown_seconds: float = None) -> bool:
        normalized_order_id = str(order_id or '').strip()
        if not normalized_order_id:
            return False

        cooldown = float(cooldown_seconds or self.order_detail_force_refresh_cooldown or 0)
        now = time.time()
        existing = self.order_detail_force_refresh_marks.get(normalized_order_id) or {}
        last_timestamp = existing.get('timestamp', 0)
        elapsed = now - last_timestamp

        if last_timestamp and cooldown > 0 and elapsed < cooldown:
            logger.info(
                f"{log_prefix} 订单详情强刷命中冷却，跳过重复刷新: "
                f"order_id={normalized_order_id}, reason={reason}, "
                f"last_reason={existing.get('reason', 'unknown')}, remaining={round(cooldown - elapsed, 2)}s"
            )
            return False

        self.order_detail_force_refresh_marks[normalized_order_id] = {
            'timestamp': now,
            'reason': reason,
        }
        return True

    def _should_force_refresh_after_status_signal(self, status_signal: str, current_status: str,
                                                  order_id: str = None) -> bool:
        normalized_signal = db_manager._normalize_order_status(status_signal)
        normalized_current = db_manager._normalize_order_status(current_status)

        if not normalized_signal or normalized_signal == 'unknown':
            return False

        if normalized_signal == 'pending_ship':
            if normalized_current == 'shipped' and not self._has_delivery_progress_evidence(order_id):
                logger.warning(
                    f"【{self.cookie_id}】检测到可疑已发货状态，允许待发货信号继续强刷详情: "
                    f"order_id={order_id or 'unknown'}, current_status={normalized_current}, signal={normalized_signal}"
                )
                return True
            return normalized_current in {None, '', 'unknown', 'processing', 'pending_payment'}

        if normalized_signal == 'shipped':
            return normalized_current in {None, '', 'unknown', 'processing', 'pending_payment', 'pending_ship'}

        if normalized_signal in {'completed', 'cancelled', 'refunding', 'refund_cancelled'}:
            if not normalized_current or normalized_current == 'unknown':
                return True
            return self._get_order_status_priority(normalized_signal) > self._get_order_status_priority(normalized_current)

        return False

    def _should_accept_order_detail_status_correction(self, current_status: str, incoming_status: str,
                                                      incoming_source: str, *, force_refresh: bool,
                                                      order_id: str = None) -> bool:
        normalized_current = db_manager._normalize_order_status(current_status)
        normalized_incoming = db_manager._normalize_order_status(incoming_status)
        normalized_source = str(incoming_source or 'unknown').strip().lower()

        if not force_refresh:
            return False
        if normalized_current != 'shipped' or normalized_incoming != 'pending_ship':
            return False
        if normalized_source not in {'selector', 'button'}:
            return False
        if self._has_delivery_progress_evidence(order_id):
            return False
        return True

    def _should_reject_order_detail_status_update(self, current_status: str, incoming_status: str,
                                                  incoming_source: str, *, force_refresh: bool) -> bool:
        normalized_current = db_manager._normalize_order_status(current_status)
        normalized_incoming = db_manager._normalize_order_status(incoming_status)
        normalized_source = str(incoming_source or 'unknown').strip().lower()

        if normalized_incoming != 'completed' or normalized_source != 'body':
            return False

        if force_refresh and normalized_current in {'shipped', 'pending_ship', 'partial_success', 'partial_pending_finalize'}:
            return True

        return False

    async def _maybe_force_refresh_order_detail_for_signal(self, order_id: str, *, item_id: str = None,
                                                           buyer_id: str = None, sid: str = None,
                                                           buyer_nick: str = None, status_signal: str = None,
                                                           reason: str = "status_signal",
                                                           delay_seconds: float = 0,
                                                           log_prefix: str = "") -> bool:
        normalized_order_id = str(order_id or '').strip()
        if not normalized_order_id:
            return False

        current_order = db_manager.get_order_by_id(normalized_order_id) or {}
        current_status = current_order.get('order_status')
        if not self._should_force_refresh_after_status_signal(status_signal, current_status, normalized_order_id):
            logger.info(
                f"{log_prefix} 当前订单状态无需为该信号强刷详情: order_id={normalized_order_id}, "
                f"signal={status_signal or 'unknown'}, current_status={current_status or 'unknown'}"
            )
            return False

        if not self._reserve_order_detail_force_refresh(
            normalized_order_id,
            reason=reason,
            log_prefix=log_prefix,
        ):
            return False

        if delay_seconds and delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        latest_order = db_manager.get_order_by_id(normalized_order_id) or {}
        latest_status = latest_order.get('order_status')
        if not self._should_force_refresh_after_status_signal(status_signal, latest_status, normalized_order_id):
            logger.info(
                f"{log_prefix} 延迟后订单状态已更新，无需再强刷详情: order_id={normalized_order_id}, "
                f"signal={status_signal or 'unknown'}, current_status={latest_status or 'unknown'}"
            )
            return False

        refresh_item_id = item_id or latest_order.get('item_id')
        refresh_buyer_id = buyer_id or latest_order.get('buyer_id')
        logger.info(
            f"{log_prefix} 状态信号触发订单详情强刷: order_id={normalized_order_id}, "
            f"signal={status_signal or 'unknown'}, current_status={latest_status or 'unknown'}, reason={reason}"
        )

        try:
            await self.fetch_order_detail_info(
                order_id=normalized_order_id,
                item_id=refresh_item_id,
                buyer_id=refresh_buyer_id,
                sid=sid,
                buyer_nick=buyer_nick,
                force_refresh=True
            )
            return True
        except Exception as refresh_error:
            logger.error(
                f"{log_prefix} 状态信号触发订单详情强刷失败: order_id={normalized_order_id}, "
                f"reason={reason}, error={self._safe_str(refresh_error)}"
            )
            return False

    def _get_stale_unsettled_orders(self, older_than_minutes: int = 10, limit: int = 5) -> list:
        from db_manager import db_manager

        safe_minutes = max(5, int(older_than_minutes or 10))
        safe_limit = max(1, min(int(limit or 5), 20))
        with db_manager.lock:
            cursor = db_manager.conn.cursor()
            cursor.execute(
                '''
                SELECT order_id, item_id, buyer_id, buyer_nick, sid, order_status, updated_at
                FROM orders
                WHERE cookie_id = ?
                  AND order_status IN ('processing', 'pending_payment')
                  AND datetime(updated_at) <= datetime('now', ?)
                ORDER BY datetime(updated_at) ASC
                LIMIT ?
                ''',
                (self.cookie_id, f'-{safe_minutes} minutes', safe_limit)
            )
            return [
                {
                    'order_id': row[0],
                    'item_id': row[1],
                    'buyer_id': row[2],
                    'buyer_nick': row[3],
                    'sid': row[4],
                    'order_status': row[5],
                    'updated_at': row[6],
                }
                for row in cursor.fetchall()
            ]

    async def _recover_stale_unsettled_orders(self, older_than_minutes: int = 10, limit: int = 5) -> int:
        from db_manager import db_manager

        stale_orders = self._get_stale_unsettled_orders(older_than_minutes=older_than_minutes, limit=limit)
        if not stale_orders:
            return 0

        recovered_count = 0
        logger.warning(
            f"【{self.cookie_id}】检测到 {len(stale_orders)} 个超时未结算订单，开始补偿刷新详情"
        )
        for order in stale_orders:
            order_id = order.get('order_id')
            old_status = order.get('order_status')
            try:
                logger.info(
                    f"【{self.cookie_id}】补偿刷新订单详情: order_id={order_id}, "
                    f"status={old_status}, updated_at={order.get('updated_at')}"
                )
                await self.fetch_order_detail_info(
                    order_id=order_id,
                    item_id=order.get('item_id'),
                    buyer_id=order.get('buyer_id'),
                    sid=order.get('sid'),
                    buyer_nick=order.get('buyer_nick'),
                    force_refresh=True,
                )

                latest_order = db_manager.get_order_by_id(order_id) or {}
                latest_status = db_manager._normalize_order_status(latest_order.get('order_status'))
                if latest_status == 'pending_ship':
                    if await self._auto_deliver_recovered_pending_order(
                        latest_order,
                        fallback_order=order,
                        source='stale_unsettled_recovery',
                    ):
                        recovered_count += 1
                elif latest_status != old_status:
                    logger.info(
                        f"【{self.cookie_id}】补偿刷新已纠正订单状态: "
                        f"order_id={order_id}, {old_status} -> {latest_status}"
                    )
                    recovered_count += 1
                else:
                    logger.info(
                        f"【{self.cookie_id}】补偿刷新后订单状态未变化: "
                        f"order_id={order_id}, status={latest_status}"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as recover_e:
                logger.error(f"【{self.cookie_id}】补偿刷新订单失败: order_id={order_id}, error={self._safe_str(recover_e)}")

            await asyncio.sleep(0)

        return recovered_count

    def _save_recent_order_candidate(self, candidate: Dict[str, Any]) -> bool:
        from db_manager import db_manager
        from utils.order_history_sync import normalize_order_history_status

        order_id = str(candidate.get('order_id') or '').strip()
        if not order_id:
            return False

        existing_order = db_manager.get_order_by_id(order_id) or {}
        incoming_status = normalize_order_history_status(candidate.get('order_status'))
        order_status = self._resolve_external_order_status(
            existing_order.get('order_status'),
            incoming_status,
            source='recent_order_watchdog',
        )

        return db_manager.insert_or_update_order(
            order_id=order_id,
            item_id=str(candidate.get('item_id') or '').strip() or None,
            buyer_id=str(candidate.get('buyer_id') or '').strip() or None,
            buyer_nick=str(candidate.get('buyer_nick') or '').strip() or None,
            sid=str(candidate.get('sid') or '').strip() or None,
            amount=str(candidate.get('amount') or '').strip() or None,
            order_status=order_status,
            cookie_id=self.cookie_id,
            platform_created_at=str(candidate.get('platform_created_at') or '').strip() or None,
            platform_paid_at=str(candidate.get('platform_paid_at') or '').strip() or None,
            platform_completed_at=str(candidate.get('platform_completed_at') or '').strip() or None,
        )

    async def _send_recovered_delivery_without_sid(
        self,
        order: Dict[str, Any],
        *,
        order_id: str,
        item_id: str,
        buyer_id: str,
        source: str,
    ) -> bool:
        from db_manager import db_manager

        lock_key = order_id
        if not self.can_auto_delivery(order_id):
            logger.info(f"【{self.cookie_id}】{source} 订单已处理或处于冷却期，跳过补偿发货: {order_id}")
            return False
        if self.is_lock_held(lock_key):
            logger.info(f"【{self.cookie_id}】{source} 订单延迟锁持有中，跳过补偿发货: {order_id}")
            return False

        order_lock = self._order_locks[lock_key]
        self._lock_usage_times[lock_key] = time.time()

        async with order_lock:
            if self.is_lock_held(lock_key) or not self.can_auto_delivery(order_id):
                logger.info(f"【{self.cookie_id}】{source} 获取锁后发现订单已处理，跳过补偿发货: {order_id}")
                return False

            pending_finalize_meta = self._get_pending_delivery_finalization_meta(order_id, 1)
            if pending_finalize_meta:
                finalize_result = await self._finalize_delivery_after_send(
                    delivery_meta=pending_finalize_meta,
                    order_id=order_id,
                    item_id=item_id,
                )
                if not finalize_result.get('success'):
                    self._persist_delivery_finalization_state(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        delivery_meta=pending_finalize_meta,
                        channel='auto',
                        status='sent',
                        last_error=finalize_result.get('error') or '补偿发货补完成 finalize 失败',
                    )
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        status='failed',
                        reason=finalize_result.get('error') or '补偿发货检测到已发送记录，但补完成发货收尾失败',
                        channel='auto',
                        rule_meta=pending_finalize_meta,
                    )
                    return False

                self._persist_delivery_finalization_state(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    delivery_meta=pending_finalize_meta,
                    channel='auto',
                    status='finalized',
                )
                self._sync_order_delivery_progress(
                    order_id=order_id,
                    cookie_id=self.cookie_id,
                    expected_quantity=1,
                    context=f"{source} 补完成收尾成功",
                )
                self._activate_delivery_lock(lock_key, delay_minutes=10)
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    status='success',
                    reason=f'{source} 检测到发货消息已发送，本次补完成收尾成功',
                    channel='auto',
                    rule_meta=pending_finalize_meta,
                )
                return True

            delivery_result = await self._auto_delivery(
                item_id,
                "待获取商品信息",
                order_id,
                buyer_id,
                '',
                include_meta=True,
            )
            if isinstance(delivery_result, dict):
                delivery_content = delivery_result.get('content')
                delivery_steps = delivery_result.get('delivery_steps') or []
                delivery_error = delivery_result.get('error')
                delivery_meta = delivery_result
            else:
                delivery_content = delivery_result
                delivery_steps = []
                delivery_error = None
                delivery_meta = {}

            if not delivery_content:
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    status='failed',
                    reason=delivery_error or f'{source} 未匹配到发货内容',
                    channel='auto',
                    rule_meta=delivery_meta,
                )
                return False

            if not delivery_steps:
                delivery_steps = self._build_delivery_steps(
                    delivery_content,
                    delivery_meta.get('card_description', '') if isinstance(delivery_meta, dict) else '',
                )

            try:
                await self.send_delivery_steps_once(buyer_id, item_id, delivery_steps)

                if not self._mark_data_reservation_sent_if_needed(delivery_meta):
                    self._release_data_reservation_if_needed(delivery_meta, error='补偿发货发送成功后标记预占已发送失败')
                    raise Exception('批量数据预占标记已发送失败')

                self._persist_delivery_finalization_state(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    delivery_meta=delivery_meta,
                    channel='auto',
                    status='sent',
                )

                finalize_result = await self._finalize_delivery_after_send(
                    delivery_meta=delivery_meta,
                    order_id=order_id,
                    item_id=item_id,
                )
                if not finalize_result.get('success'):
                    self._persist_delivery_finalization_state(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        delivery_meta=delivery_meta,
                        channel='auto',
                        status='sent',
                        last_error=finalize_result.get('error') or '补偿发货发送成功但提交发货副作用失败',
                    )
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=buyer_id,
                        status='failed',
                        reason=finalize_result.get('error') or '补偿发货发送成功但提交发货副作用失败',
                        channel='auto',
                        rule_meta=delivery_meta,
                    )
                    return False

                self._persist_delivery_finalization_state(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    delivery_meta=delivery_meta,
                    channel='auto',
                    status='finalized',
                )
                self._sync_order_delivery_progress(
                    order_id=order_id,
                    cookie_id=self.cookie_id,
                    expected_quantity=int(order.get('quantity') or 1),
                    context=f"{source} 自动发货成功",
                )
                self._activate_delivery_lock(lock_key, delay_minutes=10)
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    status='success',
                    reason=f'{source} 自动发货步骤发送成功',
                    channel='auto',
                    rule_meta=delivery_meta,
                )
                logger.warning(f"【{self.cookie_id}】{source} 已完成补偿自动发货: order_id={order_id}")
                return True
            except Exception as send_error:
                self._release_data_reservation_if_needed(delivery_meta, error=self._safe_str(send_error))
                self._persist_delivery_finalization_state(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    delivery_meta=delivery_meta,
                    channel='auto',
                    status='failed',
                    last_error=self._safe_str(send_error),
                )
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=buyer_id,
                    status='failed',
                    reason=f'{source} 自动发货消息发送失败: {self._safe_str(send_error)}',
                    channel='auto',
                    rule_meta=delivery_meta,
                )
                return False

    async def _auto_deliver_recovered_pending_order(
        self,
        order: Dict[str, Any],
        *,
        fallback_order: Optional[Dict[str, Any]] = None,
        source: str = 'order_recovery',
    ) -> bool:
        from db_manager import db_manager

        fallback_order = fallback_order or {}
        order_id = str(order.get('order_id') or fallback_order.get('order_id') or '').strip()
        item_id = str(order.get('item_id') or fallback_order.get('item_id') or '').strip()
        buyer_id = str(order.get('buyer_id') or fallback_order.get('buyer_id') or '').strip()
        sid = str(order.get('sid') or fallback_order.get('sid') or '').strip()

        if not order_id:
            return False
        if db_manager._normalize_order_status(order.get('order_status')) != 'pending_ship':
            logger.info(f"【{self.cookie_id}】{source} 订单不是待发货，跳过自动发货: order_id={order_id}, status={order.get('order_status')}")
            return False
        if not self.is_auto_confirm_enabled():
            logger.info(f"【{self.cookie_id}】{source} 发现订单待发货，但自动发货未启用: {order_id}")
            return False
        if not item_id or not buyer_id:
            logger.warning(
                f"【{self.cookie_id}】{source} 发现订单待发货，但缺少商品或买家信息，无法补偿发货: "
                f"order_id={order_id}, item_id={item_id or '-'}, buyer_id={buyer_id or '-'}"
            )
            return False

        websocket = getattr(self, 'ws', None)
        chat_id = sid.replace('@goofish', '')
        if websocket and chat_id:
            await self._handle_simple_message_auto_delivery(
                websocket,
                order_id,
                item_id,
                buyer_id,
                chat_id,
                time.strftime('%Y-%m-%d %H:%M:%S'),
                source,
            )
            return True

        logger.warning(
            f"【{self.cookie_id}】{source} 待发货订单缺少可用会话ID，尝试通过买家和商品建立会话补偿发货: "
            f"order_id={order_id}, buyer_id={buyer_id}, item_id={item_id}"
        )
        return await self._send_recovered_delivery_without_sid(
            order,
            order_id=order_id,
            item_id=item_id,
            buyer_id=buyer_id,
            source=source,
        )

    async def _recover_recent_missing_orders(self, max_orders: int = 10) -> int:
        from db_manager import db_manager
        from utils.order_history_sync import OrderHistoryPageFetcher, OrderHistorySyncError, normalize_order_history_status

        safe_limit = max(1, min(int(max_orders or 10), 30))
        disabled_until = getattr(self, '_recent_order_watchdog_disabled_until', 0)
        if disabled_until and time.time() < disabled_until:
            remaining_minutes = int((disabled_until - time.time()) / 60)
            logger.info(f"【{self.cookie_id}】近期订单巡检处于退避中，剩余约 {remaining_minutes} 分钟")
            return 0

        history_fetcher = OrderHistoryPageFetcher(self.cookies_str, cookie_id_for_log=self.cookie_id, headless=True)
        recovered_count = 0
        try:
            fetch_result = await history_fetcher.fetch_recent_orders(max_orders=safe_limit)
            candidates = list(fetch_result.get('orders') or [])
            if not candidates:
                logger.info(f"【{self.cookie_id}】近期订单巡检未发现订单候选")
                return 0

            logger.info(
                f"【{self.cookie_id}】近期订单巡检完成: scanned={fetch_result.get('scanned_count')}, "
                f"candidates={len(candidates)}"
            )
            for candidate in candidates:
                order_id = str(candidate.get('order_id') or '').strip()
                if not order_id:
                    continue

                incoming_status = normalize_order_history_status(candidate.get('order_status'))
                existing_order = db_manager.get_order_by_id(order_id) or {}
                current_status = db_manager._normalize_order_status(existing_order.get('order_status'))

                should_refresh_detail = (
                    incoming_status == 'pending_ship' and
                    current_status not in {'shipped', 'completed', 'partial_success', 'partial_pending_finalize'}
                )

                if not existing_order:
                    if self._save_recent_order_candidate(candidate):
                        logger.warning(
                            f"【{self.cookie_id}】近期订单巡检补录漏单: "
                            f"order_id={order_id}, status={incoming_status or 'unknown'}"
                        )
                        recovered_count += 1

                if should_refresh_detail:
                    await self.fetch_order_detail_info(
                        order_id=order_id,
                        item_id=str(candidate.get('item_id') or '').strip() or None,
                        buyer_id=str(candidate.get('buyer_id') or '').strip() or None,
                        sid=str(candidate.get('sid') or '').strip() or None,
                        buyer_nick=str(candidate.get('buyer_nick') or '').strip() or None,
                        buyer_id_source='recent_order_watchdog',
                        force_refresh=True,
                    )
                    latest_order = db_manager.get_order_by_id(order_id) or {}
                    latest_status = db_manager._normalize_order_status(latest_order.get('order_status'))
                    if latest_status == 'pending_ship':
                        if await self._auto_deliver_recovered_pending_order(
                            latest_order,
                            fallback_order=candidate,
                            source='recent_order_watchdog',
                        ):
                            recovered_count += 1
                    elif latest_status and latest_status != current_status:
                        logger.info(
                            f"【{self.cookie_id}】近期订单巡检刷新后状态变化: "
                            f"order_id={order_id}, {current_status or 'missing'} -> {latest_status}"
                        )
                        recovered_count += 1

                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except OrderHistorySyncError as recent_sync_error:
            if recent_sync_error.kind in {'permission_denied', 'session_expired'}:
                self._recent_order_watchdog_disabled_until = time.time() + 6 * 3600
                logger.warning(
                    f"【{self.cookie_id}】近期订单巡检不可用，进入 6 小时退避: "
                    f"kind={recent_sync_error.kind}, error={self._safe_str(recent_sync_error)}"
                )
            else:
                logger.warning(f"【{self.cookie_id}】近期订单巡检失败: {self._safe_str(recent_sync_error)}")
        except Exception as recent_error:
            logger.warning(f"【{self.cookie_id}】近期订单巡检失败: {self._safe_str(recent_error)}")
        finally:
            await history_fetcher.close()

        return recovered_count


    def _load_json_dict(self, raw_value: Any) -> Dict[str, Any]:
        """安全解析 JSON 对象。"""
        if isinstance(raw_value, dict):
            return raw_value
        if not isinstance(raw_value, str) or not raw_value.strip():
            return {}
        try:
            parsed = json.loads(raw_value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _extract_message_card_payload(self, message_1: Any) -> Dict[str, Any]:
        """提取消息卡片 JSON 载荷。"""
        if not isinstance(message_1, dict):
            return {}

        try:
            message_6 = message_1.get('6', {})
            if not isinstance(message_6, dict):
                return {}
            message_6_3 = message_6.get('3', {})
            if not isinstance(message_6_3, dict):
                return {}
            payload = message_6_3.get('5', '')
            return self._load_json_dict(payload)
        except Exception:
            return {}

    def _extract_message_button_text(self, message_1: Any) -> str:
        """提取消息卡片按钮文本。"""
        payload = self._extract_message_card_payload(message_1)
        try:
            return str(
                payload.get('dxCard', {})
                .get('item', {})
                .get('main', {})
                .get('exContent', {})
                .get('button', {})
                .get('text', '')
            ).strip()
        except Exception:
            return ''

    def _extract_message_card_title(self, message_1: Any) -> str:
        """提取消息卡片标题。"""
        payload = self._extract_message_card_payload(message_1)
        try:
            return str(
                payload.get('dxCard', {})
                .get('item', {})
                .get('main', {})
                .get('exContent', {})
                .get('title', '')
            ).strip()
        except Exception:
            return ''

    def _classify_message_route(self, *, message: dict, message_1: dict, message_10: dict,
                                send_message: str) -> Dict[str, Any]:
        """将消息路由到订单状态、系统提示、特殊流程或真人聊天。"""
        message_direction = message_1.get('7', 0) if isinstance(message_1, dict) else 0
        content_type = 0
        try:
            message_6 = message_1.get('6', {}) if isinstance(message_1, dict) else {}
            if isinstance(message_6, dict):
                message_6_3 = message_6.get('3', {})
                if isinstance(message_6_3, dict):
                    content_type = message_6_3.get('4', 0)
        except Exception:
            content_type = 0

        biz_tag_raw = str(message_10.get('bizTag', '') or '').strip()
        biz_tag_dict = self._load_json_dict(biz_tag_raw)
        ext_json_dict = self._load_json_dict(message_10.get('extJson', ''))
        task_name = str(biz_tag_dict.get('taskName') or '').strip()
        update_key = str(ext_json_dict.get('updateKey') or '').strip()
        detail_notice = str(message_10.get('detailNotice', '') or '').strip()
        reminder_content = str(message_10.get('reminderContent', '') or send_message or '').strip()
        reminder_title = str(message_10.get('reminderTitle', '') or '').strip()
        reminder_notice = str(message_10.get('reminderNotice', '') or '').strip()
        red_reminder = ''
        if isinstance(message, dict) and isinstance(message.get('3'), dict):
            red_reminder = str(message.get('3', {}).get('redReminder', '') or '').strip()

        button_text = self._extract_message_button_text(message_1)
        card_title = self._extract_message_card_title(message_1)
        session_type = str(message_10.get('sessionType', '1') or '1').strip()
        is_group_message = session_type == '30'
        is_system_biz = bool(task_name) or 'SECURITY' in biz_tag_raw or 'taskId' in biz_tag_raw
        is_system_message = message_direction == 1 or content_type == 6 or is_system_biz

        texts = []
        for raw_text in (
            send_message,
            reminder_content,
            detail_notice,
            reminder_title,
            reminder_notice,
            red_reminder,
            task_name,
            update_key,
            button_text,
            card_title,
        ):
            normalized_text = str(raw_text or '').strip()
            if normalized_text and normalized_text not in texts:
                texts.append(normalized_text)

        special_flow_messages = {
            '[卡片消息]',
            '快给ta一个评价吧~',
            '快给ta一个评价吧～',
        }
        special_flow_titles = {
            '我已小刀，待刀成',
            '我已小刀,待刀成',
            '我已成功小刀，待发货',
            '我已成功小刀,待发货',
        }

        if send_message in special_flow_messages or card_title in special_flow_titles:
            route = 'special_flow'
            order_status_signal = None
        else:
            order_status_signal = None
            closed_markers = (
                '[你关闭了订单，钱款已原路退返]',
                '交易关闭',
                '订单关闭',
                '钱款已原路退返',
            )
            refund_markers = (
                '退款中',
                '退款成功',
                '退货退款',
                '退款关闭',
            )
            completed_markers = (
                '[买家确认收货，交易成功]',
                '[你已确认收货，交易成功]',
                '买家确认收货',
                '交易成功',
            )
            shipped_markers = (
                '[你已发货]',
                '已发货',
                '等待买家收货',
            )
            pending_ship_markers = (
                '[我已付款，等待你发货]',
                '[已付款，待发货]',
                '我已付款，等待你发货',
                '[记得及时发货]',
                '等待你发货',
                '待发货',
                '去发货',
                '付款完成待发货',
                'TRADE_PAID_DONE_SELLER',
            )
            pending_payment_markers = (
                '[我已拍下，待付款]',
                '买家已拍下，待付款',
                '待付款',
                '等待买家付款',
                '已拍下_未付款',
            )
            system_notice_markers = (
                '闲鱼小红花',
                '温馨提醒',
                '曝光卡',
                '蚂蚁森林',
                '能量可领',
                '创建合约',
                '假客服骗钱',
                '订单即将自动确认收货',
                '宝贝性价比如何，去表个态吧',
                '发来一条消息',
                '发来一条新消息',
                '已送出小红花',
                '已收下',
            )

            def _contains_any(markers) -> bool:
                return any(marker and marker in text for text in texts for marker in markers)

            if _contains_any(closed_markers):
                order_status_signal = 'cancelled'
            elif _contains_any(refund_markers):
                order_status_signal = 'refunding'
            elif _contains_any(completed_markers):
                order_status_signal = 'completed'
            elif _contains_any(shipped_markers):
                order_status_signal = 'shipped'
            elif _contains_any(pending_ship_markers):
                order_status_signal = 'pending_ship'
            elif _contains_any(pending_payment_markers):
                order_status_signal = 'pending_payment'

            if is_system_message and order_status_signal:
                route = 'order_status'
            elif _contains_any(system_notice_markers) and (is_system_message or message_direction != 2):
                route = 'system_notice'
            elif is_system_message:
                route = 'system_notice'
            else:
                route = 'user_chat'

        should_notify = False
        if not is_group_message:
            if route == 'user_chat':
                should_notify = True
            elif route == 'order_status' and order_status_signal in {'pending_ship', 'refunding', 'cancelled'}:
                should_notify = True

        return {
            'route': route,
            'order_status_signal': order_status_signal,
            'should_notify': should_notify,
            'allow_auto_reply': route == 'user_chat',
            'is_system_message': is_system_message,
            'is_group_message': is_group_message,
            'message_direction': message_direction,
            'content_type': content_type,
            'task_name': task_name,
            'button_text': button_text,
            'card_title': card_title,
            'texts': texts,
        }

    def _is_auto_delivery_trigger(self, message: str) -> bool:
        """检查消息是否为自动发货触发关键字"""
        # 定义所有自动发货触发关键字
        auto_delivery_keywords = [
            # 系统消息
            '[我已付款，等待你发货]',
            '[已付款，待发货]',
            '我已付款，等待你发货',
            '[记得及时发货]',
        ]

        # 检查消息是否包含任何触发关键字
        for keyword in auto_delivery_keywords:
            if keyword in message:
                return True

        return False

    def _extract_order_id_from_update_key(self, raw_text: Any) -> Optional[str]:
        normalized_text = str(raw_text or '').strip()
        if not normalized_text:
            return None

        direct_match_found = False
        direct_match = re.search(r'updateKey["\']?\s*[:=]\s*["\']([^"\']+)', normalized_text)
        if direct_match:
            direct_match_found = True
            normalized_text = direct_match.group(1)

        colon_parts = [part.strip().strip('"\'') for part in normalized_text.split(':')]
        long_numeric_parts = [part for part in colon_parts if part.isdigit() and len(part) >= 16]
        if long_numeric_parts:
            return long_numeric_parts[0]

        if direct_match_found:
            generic_matches = re.findall(r'\d{16,}', normalized_text)
            if generic_matches:
                return generic_matches[0]
        return None

    def _extract_order_id_from_candidate_text(self, raw_text: Any, source: str = '') -> Optional[str]:
        normalized_text = str(raw_text or '').strip()
        if not normalized_text:
            return None

        source_key = str(source or '').rsplit('.', 1)[-1].lower()
        if source_key in {'orderid', 'bizorderid', 'order_id', 'tradeid', 'trade_id'}:
            direct_numeric = re.fullmatch(r'\d{10,}', normalized_text)
            if direct_numeric:
                return direct_numeric.group(0)

        patterns = [
            r'orderId(?:=|:|%3[Dd]|\\u003[dD])\s*"?(\d{10,})',
            r'bizOrderId["\']?\s*[:=]\s*"?(\d{10,})',
            r'order[_-]?id["\']?\s*[:=]\s*"?(\d{10,})',
            r'order[_-]?detail\?(?:[^\s#]*?&)?id=(\d{10,})',
            r'order-detail\?(?:[^\s#]*?&)?orderId=(\d{10,})',
        ]

        for pattern in patterns:
            match = re.search(pattern, normalized_text)
            if match:
                return match.group(1)

        source_lower = source.lower()
        text_lower = normalized_text.lower()
        if (
            'updatekey' in source_lower
            or 'updatekey' in text_lower
            or ('trade_' in text_lower and ':' in normalized_text)
            or ('buyer_confirm' in text_lower and ':' in normalized_text)
        ):
            return self._extract_order_id_from_update_key(normalized_text)

        return None

    def _collect_order_id_candidate_texts(self, data: Any, root: str = 'message'):
        candidates = []
        seen = set()

        def add_candidate(source: str, value: Any):
            if value is None:
                return
            normalized_text = str(value).strip()
            if not normalized_text:
                return
            dedupe_key = (source, normalized_text)
            if dedupe_key in seen:
                return
            seen.add(dedupe_key)
            candidates.append((source, normalized_text))

            if normalized_text[:1] in {'{', '['}:
                try:
                    parsed_value = json.loads(normalized_text)
                except Exception:
                    return
                walk_value(parsed_value, f'{source}.json')

        def walk_value(value: Any, source: str):
            if isinstance(value, dict):
                for key, nested_value in value.items():
                    nested_source = f'{source}.{key}'
                    if isinstance(nested_value, (dict, list)):
                        walk_value(nested_value, nested_source)
                    else:
                        add_candidate(nested_source, nested_value)
            elif isinstance(value, list):
                for index, nested_value in enumerate(value[:20]):
                    walk_value(nested_value, f'{source}[{index}]')
            else:
                add_candidate(source, value)

        walk_value(data, root)
        return candidates

    def _extract_order_id(self, message: dict, raw_message_data: dict = None) -> str:
        """从消息中提取订单ID
        
        Args:
            message: 解密后的消息内容
            raw_message_data: 原始的WebSocket消息数据（用于在解密消息中找不到订单ID时进行搜索）
        """
        try:
            # 先查看消息的完整结构
            logger.warning(f"【{self.cookie_id}】🔍 完整消息结构: {message}")

            for source, candidate_text in self._collect_order_id_candidate_texts(message, root='message'):
                order_id = self._extract_order_id_from_candidate_text(candidate_text, source=source)
                if order_id:
                    logger.info(f'【{self.cookie_id}】🎯 最终提取到订单ID: {order_id} (source={source})')
                    return order_id

            if raw_message_data:
                logger.info(f'【{self.cookie_id}】🔍 尝试从原始消息数据中搜索订单ID')
                for source, candidate_text in self._collect_order_id_candidate_texts(raw_message_data, root='raw_message'):
                    order_id = self._extract_order_id_from_candidate_text(candidate_text, source=source)
                    if order_id:
                        logger.info(f'【{self.cookie_id}】🎯 从原始消息提取到订单ID: {order_id} (source={source})')
                        return order_id

                try:
                    sync_data_list = raw_message_data.get("body", {}).get("syncPushPackage", {}).get("data", [])
                    for idx, sync_data_item in enumerate(sync_data_list[:20]):
                        if not isinstance(sync_data_item, dict) or "data" not in sync_data_item:
                            continue

                        item_data = sync_data_item.get("data")
                        if item_data is None:
                            continue

                        try:
                            decoded_data = base64.b64decode(item_data).decode("utf-8")
                        except Exception:
                            decoded_data = item_data

                        for source, candidate_text in self._collect_order_id_candidate_texts(decoded_data, root=f'raw_sync[{idx}]'):
                            order_id = self._extract_order_id_from_candidate_text(candidate_text, source=source)
                            if order_id:
                                logger.info(f'【{self.cookie_id}】🎯 从syncPushPackage.data提取到订单ID: {order_id} (source={source})')
                                return order_id
                except Exception as multi_data_e:
                    logger.warning(f"遍历syncPushPackage.data时出错: {multi_data_e}")

            logger.warning(f'【{self.cookie_id}】❌ 未能从消息中提取到订单ID')
            return None

        except Exception as e:
            logger.error(f"【{self.cookie_id}】提取订单ID失败: {self._safe_str(e)}")
            return None

    async def _handle_simple_message_auto_delivery(self, websocket, order_id: str, item_id: str, 
                                                    user_id: str, chat_id: str, msg_time: str, msg_id: str):
        """处理简化结构消息的自动发货逻辑
        
        专门用于处理简化结构的发货通知消息（message['1']是字符串的情况）
        发货确认统一在 _auto_delivery 内执行，避免重复确认导致漏发
        
        Args:
            websocket: WebSocket连接
            order_id: 订单ID
            item_id: 商品ID
            user_id: 买家用户ID
            chat_id: 聊天ID
            msg_time: 消息时间
            msg_id: 消息ID
        """
        try:
            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🚀 开始处理简化消息自动发货: order_id={order_id}, item_id={item_id}')
            
            # 检查商品是否属于当前账号
            if item_id and item_id != "未知商品":
                try:
                    if not await self._ensure_item_owned_by_current_account(
                        item_id,
                        log_prefix=f'[{msg_time}] 【{self.cookie_id}】[{msg_id}]'
                    ):
                        logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ❌ 商品 {item_id} 不属于当前账号，跳过自动发货')
                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            status='failed',
                            reason='商品不属于当前账号，跳过自动发货',
                            channel='auto'
                        )
                        return
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 商品 {item_id} 归属验证通过')
                except Exception as e:
                    logger.error(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 检查商品归属失败: {self._safe_str(e)}，跳过自动发货')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        status='failed',
                        reason=f'检查商品归属失败: {self._safe_str(e)}',
                        channel='auto'
                    )
                    return
            
            if self._check_buyer_blacklist_for_action(
                buyer_id=user_id,
                item_id=item_id,
                order_id=order_id,
                action='自动发货',
                channel='auto',
                log_delivery=True,
            ):
                return

            # 检查订单是否已发货
            if not self.can_auto_delivery(order_id):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 订单 {order_id} 在冷却期内，跳过发货')
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=user_id,
                    status='skipped',
                    reason='订单在冷却期内，跳过发货',
                    channel='auto'
                )
                return
            
            # 检查延迟锁状态
            lock_key = order_id
            if self.is_lock_held(lock_key):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔒 订单 {lock_key} 延迟锁仍在持有状态，跳过发货')
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=user_id,
                    status='skipped',
                    reason='订单延迟锁持有中，跳过发货',
                    channel='auto'
                )
                return
            
            # 获取订单锁
            order_lock = self._order_locks[lock_key]
            self._lock_usage_times[lock_key] = time.time()
            
            async with order_lock:
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 获取订单锁成功: {lock_key}')
                
                # 再次检查延迟锁和冷却状态
                if self.is_lock_held(lock_key) or not self.can_auto_delivery(order_id):
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 获取锁后检查发现订单已处理，跳过发货')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        status='skipped',
                        reason='获取锁后发现订单已处理，跳过发货',
                        channel='auto'
                    )
                    return

                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 📤 开始执行自动发货内容发送（发送成功后再确认发货）')
                
                # 获取商品标题
                item_title = "待获取商品信息"

                pending_finalize_meta = self._get_pending_delivery_finalization_meta(order_id, 1)
                if pending_finalize_meta:
                    finalize_result = await self._finalize_delivery_after_send(
                        delivery_meta=pending_finalize_meta,
                        order_id=order_id,
                        item_id=item_id
                    )
                    if not finalize_result.get('success'):
                        finalize_error = finalize_result.get('error') or '补完成 finalize 失败'
                        if self._is_platform_confirm_failure_error(finalize_error):
                            self._mark_delivery_pending_platform_confirm(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=user_id,
                                delivery_meta=pending_finalize_meta,
                                confirm_error=finalize_error,
                                expected_quantity=1,
                                context="自动发货补完成收尾时平台确认失败"
                            )
                        else:
                            self._persist_delivery_finalization_state(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=user_id,
                                delivery_meta=pending_finalize_meta,
                                channel='auto',
                                status='sent',
                                last_error=finalize_error
                            )
                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            status='failed',
                            reason=finalize_error,
                            channel='auto',
                            rule_meta=pending_finalize_meta
                        )
                        await self.send_delivery_failure_notification(
                            send_user_name="买家",
                            send_user_id=user_id,
                            item_id=item_id,
                            error_message=finalize_error,
                            chat_id=chat_id,
                            order_id=order_id
                        )
                        return

                    self._persist_delivery_finalization_state(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        delivery_meta=pending_finalize_meta,
                        channel='auto',
                        status='finalized'
                    )
                    self._sync_order_delivery_progress(
                        order_id=order_id,
                        cookie_id=self.cookie_id,
                        expected_quantity=1,
                        context="自动发货补完成收尾成功"
                    )
                    self._activate_delivery_lock(lock_key, delay_minutes=10)
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        status='success',
                        reason='检测到发货消息已发送，本次补完成收尾成功',
                        channel='auto',
                        rule_meta=pending_finalize_meta
                    )
                    await self.send_delivery_failure_notification(
                        send_user_name="买家",
                        send_user_id=user_id,
                        item_id=item_id,
                        error_message="发货成功",
                        chat_id=chat_id,
                        order_id=order_id
                    )
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 简化消息自动发货补完成收尾成功')
                    return
                
                # 调用自动发货方法获取发货内容
                delivery_result = await self._auto_delivery(
                    item_id, item_title, order_id, user_id, chat_id, include_meta=True
                )
                if isinstance(delivery_result, dict):
                    delivery_content = delivery_result.get('content')
                    delivery_error = delivery_result.get('error')
                    delivery_steps = delivery_result.get('delivery_steps') or []
                    delivery_rule_meta = {
                        'rule_id': delivery_result.get('rule_id'),
                        'rule_keyword': delivery_result.get('rule_keyword'),
                        'card_type': delivery_result.get('card_type'),
                        'match_mode': delivery_result.get('match_mode'),
                        'order_spec_mode': delivery_result.get('order_spec_mode'),
                        'rule_spec_mode': delivery_result.get('rule_spec_mode'),
                        'item_config_mode': delivery_result.get('item_config_mode'),
                        'card_id': delivery_result.get('card_id'),
                        'card_description': delivery_result.get('card_description'),
                        'data_card_pending_consume': delivery_result.get('data_card_pending_consume'),
                        'data_line': delivery_result.get('data_line'),
                        'data_reservation_id': delivery_result.get('data_reservation_id'),
                        'data_reservation_status': delivery_result.get('data_reservation_status'),
                        'delivery_unit_index': delivery_result.get('delivery_unit_index')
                    }
                else:
                    delivery_content = delivery_result
                    delivery_error = None
                    delivery_steps = []
                    delivery_rule_meta = {}

                if delivery_content:
                    delivery_rule_meta.setdefault('success', True)
                    if not delivery_steps:
                        delivery_steps = self._build_delivery_steps(
                            delivery_content,
                            delivery_rule_meta.get('card_description', '')
                        )

                    # 发送发货内容
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    
                    try:
                        await self._send_delivery_steps(
                            websocket,
                            chat_id,
                            user_id,
                            delivery_steps,
                            user_url=user_url,
                            log_prefix=f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 自动发货'
                        )

                        if not self._mark_data_reservation_sent_if_needed(delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta):
                            self._release_data_reservation_if_needed(
                                delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                                error='发送成功后标记预占已发送失败'
                            )
                            raise Exception('批量数据预占标记已发送失败')

                        self._persist_delivery_finalization_state(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            delivery_meta=delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                            channel='auto',
                            status='sent'
                        )

                        finalize_result = await self._finalize_delivery_after_send(
                            delivery_meta=delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                            order_id=order_id,
                            item_id=item_id
                        )
                        if not finalize_result.get('success'):
                            finalize_error = finalize_result.get('error') or '发送成功但提交发货副作用失败'
                            if self._is_platform_confirm_failure_error(finalize_error):
                                self._mark_delivery_pending_platform_confirm(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=user_id,
                                    delivery_meta=delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                                    confirm_error=finalize_error,
                                    expected_quantity=1,
                                    context="自动发货发送成功后平台确认失败"
                                )
                            else:
                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=user_id,
                                    delivery_meta=delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                                    channel='auto',
                                    status='sent',
                                    last_error=finalize_error
                                )
                            self._record_delivery_log(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=user_id,
                                status='failed',
                                reason=finalize_error,
                                channel='auto',
                                rule_meta=delivery_rule_meta
                            )
                            await self.send_delivery_failure_notification(
                                send_user_name="买家",
                                send_user_id=user_id,
                                item_id=item_id,
                                error_message=finalize_error,
                                chat_id=chat_id,
                                order_id=order_id
                            )
                            return

                        self._persist_delivery_finalization_state(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            delivery_meta=delivery_result if isinstance(delivery_result, dict) else delivery_rule_meta,
                            channel='auto',
                            status='finalized'
                        )

                        self._sync_order_delivery_progress(
                            order_id=order_id,
                            cookie_id=self.cookie_id,
                            expected_quantity=1,
                            context="自动发货发送成功"
                        )
                        self._activate_delivery_lock(lock_key, delay_minutes=10)

                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            status='success',
                            reason='自动发货步骤发送成功',
                            channel='auto',
                            rule_meta=delivery_rule_meta
                        )
                    except Exception as send_e:
                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=user_id,
                            status='failed',
                            reason=f'自动发货消息发送失败: {self._safe_str(send_e)}',
                            channel='auto',
                            rule_meta=delivery_rule_meta
                        )
                        raise
                    
                    # 发送成功通知
                    await self.send_delivery_failure_notification(
                        send_user_name="买家",
                        send_user_id=user_id,
                        item_id=item_id,
                        error_message="发货成功",
                        chat_id=chat_id,
                        order_id=order_id
                    )
                    
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 简化消息自动发货完成')
                else:
                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ❌ 未找到匹配的发货规则或获取发货内容失败')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=user_id,
                        status='failed',
                        reason=delivery_error or '未找到匹配的发货规则或获取发货内容失败',
                        channel='auto',
                        rule_meta=delivery_rule_meta
                    )
                    await self.send_delivery_failure_notification(
                        send_user_name="买家",
                        send_user_id=user_id,
                        item_id=item_id,
                        error_message="未找到匹配的发货规则或获取发货内容失败",
                        chat_id=chat_id,
                        order_id=order_id
                    )

        except Exception as e:
            self._release_data_reservation_if_needed(
                delivery_result if 'delivery_result' in locals() and isinstance(delivery_result, dict) else delivery_rule_meta if 'delivery_rule_meta' in locals() else None,
                error=f'自动发货发送失败: {self._safe_str(e)}'
            )
            self._record_delivery_log(
                order_id=order_id,
                item_id=item_id,
                buyer_id=user_id,
                status='failed',
                reason=f'简化消息自动发货异常: {self._safe_str(e)}',
                channel='auto'
            )
            logger.error(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 简化消息自动发货异常: {self._safe_str(e)}')
            import traceback
            logger.error(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 异常堆栈: {traceback.format_exc()}')

    async def _handle_auto_delivery(self, websocket, message: dict, send_user_name: str, send_user_id: str,
                                   item_id: str, chat_id: str, msg_time: str, message_data: dict = None):
        """统一处理自动发货逻辑
        
        Args:
            message_data: 原始的WebSocket消息数据，用于提取订单ID时的备用搜索
        """
        try:
            from db_manager import db_manager

            # 检查商品是否属于当前cookies
            if item_id and item_id != "未知商品":
                try:
                    if not await self._ensure_item_owned_by_current_account(
                        item_id,
                        log_prefix=f'[{msg_time}] 【{self.cookie_id}】'
                    ):
                        logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 商品 {item_id} 不属于当前账号，跳过自动发货')
                        self._record_delivery_log(
                            item_id=item_id,
                            buyer_id=send_user_id,
                            buyer_nick=send_user_name,
                            status='failed',
                            reason='商品不属于当前账号，跳过自动发货',
                            channel='auto'
                        )
                        return
                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】✅ 商品 {item_id} 归属验证通过')
                except Exception as e:
                    logger.error(f'[{msg_time}] 【{self.cookie_id}】检查商品归属失败: {self._safe_str(e)}，跳过自动发货')
                    self._record_delivery_log(
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason=f'检查商品归属失败: {self._safe_str(e)}',
                        channel='auto'
                    )
                    return

            if self._check_buyer_blacklist_for_action(
                buyer_id=send_user_id,
                item_id=item_id,
                buyer_nick=send_user_name,
                action='自动发货',
                channel='auto',
                log_delivery=True,
            ):
                return

            # 提取订单ID（传递原始消息数据以便在解密消息中找不到时进行备用搜索）
            order_id = self._extract_order_id(message, message_data)

            # 如果order_id不存在，尝试通过sid进行兜底查单
            if not order_id:
                fallback_sid = None
                try:
                    message_1 = message.get('1', {}) if isinstance(message, dict) else {}
                    if isinstance(message_1, dict):
                        # 优先使用会话字段
                        fallback_sid = message_1.get('2', '')

                        # 备用：从reminderUrl里解析sid
                        if not fallback_sid:
                            message_10 = message_1.get('10', {})
                            if isinstance(message_10, dict):
                                reminder_url = message_10.get('reminderUrl', '') or ''
                                sid_match = re.search(r'[?&]sid=([^&]+)', reminder_url)
                                if sid_match:
                                    fallback_sid = sid_match.group(1)
                except Exception as sid_e:
                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】解析sid失败: {self._safe_str(sid_e)}')

                if fallback_sid:
                    try:
                        log_prefix = f'[{msg_time}] 【{self.cookie_id}】'
                        sid_lookup_minutes = 5
                        sid_lookup = self._lookup_delivery_order_by_sid(
                            fallback_sid,
                            minutes=sid_lookup_minutes,
                            log_prefix=log_prefix
                        )
                        sid_lookup = await self._refresh_sid_lookup_if_needed(
                            fallback_sid,
                            sid_lookup,
                            item_id=item_id,
                            buyer_id=send_user_id,
                            minutes=sid_lookup_minutes,
                            allow_bargain_ready=True,
                            log_prefix=log_prefix
                        )
                    except Exception as sid_query_e:
                        logger.error(f'[{msg_time}] 【{self.cookie_id}】sid兜底查单异常: {self._safe_str(sid_query_e)}')
                        sid_lookup = {'match_type': 'error', 'order': None}

                    recent_order = sid_lookup.get('order')
                    sid_match_type = sid_lookup.get('match_type', 'missing')

                    if recent_order and sid_match_type in {'pending_ship', 'bargain_ready'}:
                        fallback_order_id = recent_order.get('order_id')
                        fallback_item_id = recent_order.get('item_id')
                        fallback_buyer_id = recent_order.get('buyer_id')

                        # 防串单：买家不一致直接拒绝（仅当 DB 中的 buyer_id 可信时才校验）
                        if send_user_id and fallback_buyer_id and self._is_trustworthy_buyer_id(fallback_buyer_id) and str(send_user_id) != str(fallback_buyer_id):
                            logger.warning(
                                f'[{msg_time}] 【{self.cookie_id}】❌ sid兜底命中订单但买家不一致，已拒绝发货: '
                                f'send_user_id={send_user_id}, order_buyer_id={fallback_buyer_id}, sid={fallback_sid}'
                            )
                            return

                        # 防串单：商品不一致直接拒绝
                        if item_id and item_id != "未知商品" and fallback_item_id and str(item_id) != str(fallback_item_id):
                            logger.warning(
                                f'[{msg_time}] 【{self.cookie_id}】❌ sid兜底命中订单但商品不一致，已拒绝发货: '
                                f'message_item_id={item_id}, order_item_id={fallback_item_id}, sid={fallback_sid}'
                            )
                            return

                        order_id = fallback_order_id
                        if (not item_id or item_id == "未知商品") and fallback_item_id:
                            item_id = fallback_item_id

                        if sid_match_type == 'bargain_ready':
                            logger.info(
                                f'[{msg_time}] 【{self.cookie_id}】✅ 订单ID提取失败，但检测到小刀成功证据，'
                                f'使用sid兜底直接进入自动发货: sid={fallback_sid}, order_id={order_id}'
                            )

                        logger.info(
                            f'[{msg_time}] 【{self.cookie_id}】✅ 订单ID提取失败，已通过sid兜底定位订单: '
                            f'sid={fallback_sid}, order_id={order_id}, item_id={item_id}'
                        )
                    elif recent_order:
                        fallback_order_id = recent_order.get('order_id')
                        fallback_status = recent_order.get('order_status') or 'unknown'
                        if sid_match_type == 'already_processed':
                            logger.info(
                                f'[{msg_time}] 【{self.cookie_id}】ℹ️ 订单ID提取失败，但sid命中的订单已处理完成，跳过重复发货: '
                                f'sid={fallback_sid}, order_id={fallback_order_id}, status={fallback_status}'
                            )
                        elif sid_match_type == 'cancelled':
                            logger.info(
                                f'[{msg_time}] 【{self.cookie_id}】ℹ️ 订单ID提取失败，但sid命中的订单已关闭，跳过自动发货: '
                                f'sid={fallback_sid}, order_id={fallback_order_id}'
                            )
                        else:
                            logger.info(
                                f'[{msg_time}] 【{self.cookie_id}】ℹ️ 订单ID提取失败，但sid命中的订单当前状态不适合兜底发货，等待后续完整消息: '
                                f'sid={fallback_sid}, order_id={fallback_order_id}, status={fallback_status}'
                            )
                        return
                    else:
                        logger.warning(
                            f'[{msg_time}] 【{self.cookie_id}】❌ 未能提取到订单ID，sid兜底也未命中待发货订单，跳过自动发货 '
                            f'(sid={fallback_sid})'
                        )
                        self._record_delivery_log(
                            item_id=item_id,
                            buyer_id=send_user_id,
                            buyer_nick=send_user_name,
                            status='failed',
                            reason=f'未能提取订单ID且sid未命中待发货订单: sid={fallback_sid}',
                            channel='auto'
                        )
                        return
                else:
                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 未能提取到订单ID且无可用sid，跳过自动发货')
                    self._record_delivery_log(
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='未能提取到订单ID且无可用sid，跳过自动发货',
                        channel='auto'
                    )
                    return

            # 订单ID已提取，将在自动发货时进行确认发货处理
            # 防串单：对直接提取/兜底后的订单进行一致性校验
            try:
                existing_order = db_manager.get_order_by_id(order_id)
            except Exception as order_check_e:
                logger.error(f'[{msg_time}] 【{self.cookie_id}】查询订单一致性校验失败: {self._safe_str(order_check_e)}')
                existing_order = None

            if existing_order:
                existing_buyer_id = existing_order.get('buyer_id')
                existing_item_id = existing_order.get('item_id')

                if send_user_id and existing_buyer_id and self._is_trustworthy_buyer_id(existing_buyer_id) and str(send_user_id) != str(existing_buyer_id):
                    logger.warning(
                        f'[{msg_time}] 【{self.cookie_id}】❌ 订单与当前会话买家不一致，拒绝自动发货: '
                        f'order_id={order_id}, send_user_id={send_user_id}, order_buyer_id={existing_buyer_id}'
                    )
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='订单与当前会话买家不一致，拒绝自动发货',
                        channel='auto'
                    )
                    return

                if item_id and item_id != "未知商品" and existing_item_id and str(item_id) != str(existing_item_id):
                    logger.warning(
                        f'[{msg_time}] 【{self.cookie_id}】❌ 订单与当前会话商品不一致，拒绝自动发货: '
                        f'order_id={order_id}, message_item_id={item_id}, order_item_id={existing_item_id}'
                    )
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='订单与当前会话商品不一致，拒绝自动发货',
                        channel='auto'
                    )
                    return

                if (not item_id or item_id == "未知商品") and existing_item_id:
                    item_id = existing_item_id
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】订单一致性校验补全商品ID: {item_id}')

            if self._check_buyer_blacklist_for_action(
                buyer_id=send_user_id,
                item_id=item_id,
                order_id=order_id,
                buyer_nick=send_user_name,
                action='自动发货',
                channel='auto',
                log_delivery=True,
            ):
                return

            logger.info(f'[{msg_time}] 【{self.cookie_id}】提取到订单ID: {order_id}，将在自动发货时处理确认发货')

            # 使用订单ID作为锁的键
            lock_key = order_id

            # 第一重检查：延迟锁状态（在获取锁之前检查，避免不必要的等待）
            if self.is_lock_held(lock_key):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】🔒【提前检查】订单 {lock_key} 延迟锁仍在持有状态，跳过发货')
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=send_user_id,
                    buyer_nick=send_user_name,
                    status='failed',
                    reason='订单延迟锁持有中，跳过发货',
                    channel='auto'
                )
                return

            # 第二重检查：基于时间的冷却机制
            if not self.can_auto_delivery(order_id):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】订单 {order_id} 在冷却期内，跳过发货')
                self._record_delivery_log(
                    order_id=order_id,
                    item_id=item_id,
                    buyer_id=send_user_id,
                    buyer_nick=send_user_name,
                    status='failed',
                    reason='订单在冷却期内，跳过发货',
                    channel='auto'
                )
                return

            # 获取或创建该订单的锁
            order_lock = self._order_locks[lock_key]

            # 更新锁的使用时间
            self._lock_usage_times[lock_key] = time.time()

            # 使用异步锁防止同一订单的并发处理
            async with order_lock:
                logger.info(f'[{msg_time}] 【{self.cookie_id}】获取订单锁成功: {lock_key}，开始处理自动发货')

                # 第三重检查：获取锁后再次检查延迟锁状态（双重检查，防止在等待锁期间状态发生变化）
                if self.is_lock_held(lock_key):
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】订单 {lock_key} 在获取锁后检查发现延迟锁仍持有，跳过发货')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='获取锁后发现延迟锁仍持有，跳过发货',
                        channel='auto'
                    )
                    return

                # 第四重检查：获取锁后再次检查冷却状态
                if not self.can_auto_delivery(order_id):
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】订单 {order_id} 在获取锁后检查发现仍在冷却期，跳过发货')
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason='获取锁后发现订单仍在冷却期，跳过发货',
                        channel='auto'
                    )
                    return

                # 构造用户URL
                user_url = f'https://www.goofish.com/personal?userId={send_user_id}'

                # 自动发货逻辑
                try:
                    # 设置默认标题（将通过API获取真实商品信息）
                    item_title = "待获取商品信息"

                    logger.info(f"【{self.cookie_id}】准备自动发货: item_id={item_id}, item_title={item_title}")

                    # 检查是否需要多数量发货
                    from db_manager import db_manager
                    quantity_to_send = 1  # 默认发送1个

                    # 检查商品是否开启了多数量发货
                    multi_quantity_delivery = db_manager.get_item_multi_quantity_delivery_status(self.cookie_id, item_id)

                    if multi_quantity_delivery and order_id:
                        logger.info(f"商品 {item_id} 开启了多数量发货，获取订单详情...")
                        try:
                            # 使用现有方法获取订单详情
                            order_detail = await self.fetch_order_detail_info(order_id, item_id, send_user_id)
                            if order_detail and order_detail.get('quantity'):
                                try:
                                    order_quantity = int(order_detail['quantity'])
                                    if order_quantity > 1:
                                        quantity_to_send = order_quantity
                                        logger.info(f"从订单详情获取数量: {order_quantity}，将发送 {quantity_to_send} 个卡券")
                                    else:
                                        logger.info(f"订单数量为 {order_quantity}，发送单个卡券")
                                except (ValueError, TypeError):
                                    logger.warning(f"订单数量格式无效: {order_detail.get('quantity')}，发送单个卡券")
                            else:
                                logger.info(f"未获取到订单数量信息，发送单个卡券")
                        except Exception as e:
                            logger.error(f"获取订单详情失败: {self._safe_str(e)}，发送单个卡券")
                    elif not multi_quantity_delivery:
                        logger.info(f"商品 {item_id} 未开启多数量发货，发送单个卡券")
                    else:
                        logger.info(f"无订单ID，发送单个卡券")

                    successful_send_count = 0
                    last_delivery_error = None
                    prepared_units = []

                    for i in range(quantity_to_send):
                        unit_index = i + 1
                        rule_meta = {}
                        try:
                            pending_finalize_meta = self._get_pending_delivery_finalization_meta(order_id, unit_index)
                            if pending_finalize_meta:
                                finalize_result = await self._finalize_delivery_after_send(
                                    delivery_meta=pending_finalize_meta,
                                    order_id=order_id,
                                    item_id=item_id
                                )
                                if not finalize_result.get('success'):
                                    last_delivery_error = finalize_result.get('error') or f"第 {unit_index} 个卡券补完成收尾失败"
                                    if self._is_platform_confirm_failure_error(last_delivery_error):
                                        self._mark_delivery_pending_platform_confirm(
                                            order_id=order_id,
                                            item_id=item_id,
                                            buyer_id=send_user_id,
                                            delivery_meta=pending_finalize_meta,
                                            confirm_error=last_delivery_error,
                                            expected_quantity=quantity_to_send,
                                            context="多数量自动发货补完成收尾时平台确认失败"
                                        )
                                    else:
                                        self._persist_delivery_finalization_state(
                                            order_id=order_id,
                                            item_id=item_id,
                                            buyer_id=send_user_id,
                                            delivery_meta=pending_finalize_meta,
                                            channel='auto',
                                            status='sent',
                                            last_error=last_delivery_error
                                        )
                                    self._record_delivery_log(
                                        order_id=order_id,
                                        item_id=item_id,
                                        buyer_id=send_user_id,
                                        buyer_nick=send_user_name,
                                        status='failed',
                                        reason=last_delivery_error,
                                        channel='auto',
                                        rule_meta=pending_finalize_meta
                                    )
                                    logger.error(last_delivery_error)
                                    continue

                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    delivery_meta=pending_finalize_meta,
                                    channel='auto',
                                    status='finalized'
                                )
                                successful_send_count += 1

                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='success',
                                    reason='检测到发货消息已发送，本次补完成收尾成功',
                                    channel='auto',
                                    rule_meta=pending_finalize_meta
                                )
                                continue

                            delivery_result = await self._auto_delivery(
                                item_id,
                                item_title,
                                order_id,
                                send_user_id,
                                chat_id,
                                send_user_name,
                                include_meta=True,
                                delivery_unit_index=unit_index
                            )

                            if isinstance(delivery_result, dict):
                                delivery_content = delivery_result.get('content')
                                delivery_error = delivery_result.get('error')
                                delivery_steps = delivery_result.get('delivery_steps') or []
                                rule_meta = {
                                    'success': True,
                                    'rule_id': delivery_result.get('rule_id'),
                                    'rule_keyword': delivery_result.get('rule_keyword'),
                                    'card_type': delivery_result.get('card_type'),
                                    'match_mode': delivery_result.get('match_mode'),
                                    'order_spec_mode': delivery_result.get('order_spec_mode'),
                                    'rule_spec_mode': delivery_result.get('rule_spec_mode'),
                                    'item_config_mode': delivery_result.get('item_config_mode'),
                                    'card_id': delivery_result.get('card_id'),
                                    'card_description': delivery_result.get('card_description'),
                                    'data_card_pending_consume': delivery_result.get('data_card_pending_consume'),
                                    'data_line': delivery_result.get('data_line'),
                                    'data_reservation_id': delivery_result.get('data_reservation_id'),
                                    'data_reservation_status': delivery_result.get('data_reservation_status'),
                                    'delivery_unit_index': delivery_result.get('delivery_unit_index')
                                }
                            else:
                                delivery_content = delivery_result
                                delivery_error = None
                                delivery_steps = []

                            if not delivery_content:
                                failure_reason = delivery_error or f"第 {unit_index}/{quantity_to_send} 个卡券内容获取失败"
                                last_delivery_error = failure_reason
                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='failed',
                                    reason=failure_reason,
                                    channel='auto',
                                    rule_meta=rule_meta
                                )
                                logger.warning(failure_reason)
                                continue

                            if not delivery_steps:
                                delivery_steps = self._build_delivery_steps(delivery_content, rule_meta.get('card_description', ''))
                            if not delivery_steps:
                                failure_reason = f"第 {unit_index}/{quantity_to_send} 个卡券发货步骤构建失败"
                                last_delivery_error = failure_reason
                                self._release_data_reservation_if_needed(rule_meta, error=failure_reason)
                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='failed',
                                    reason=failure_reason,
                                    channel='auto',
                                    rule_meta=rule_meta
                                )
                                logger.error(failure_reason)
                                continue

                            prepared_units.append({
                                'unit_index': unit_index,
                                'delivery_steps': delivery_steps,
                                'rule_meta': rule_meta,
                                'card_type': rule_meta.get('card_type'),
                            })

                        except Exception as e:
                            self._release_data_reservation_if_needed(rule_meta, error=f'准备发货失败: {self._safe_str(e)}')
                            last_delivery_error = f"准备第 {unit_index}/{quantity_to_send} 个卡券失败: {self._safe_str(e)}"
                            self._record_delivery_log(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=send_user_id,
                                buyer_nick=send_user_name,
                                status='failed',
                                reason=last_delivery_error,
                                channel='auto',
                                rule_meta=rule_meta
                            )
                            logger.error(last_delivery_error)

                    send_groups = self._build_delivery_send_groups(prepared_units, quantity_to_send)
                    total_send_groups = len(send_groups)

                    for group_index, send_group in enumerate(send_groups, start=1):
                        group_units = send_group.get('units') or []
                        if not group_units:
                            continue

                        first_unit = group_units[0]
                        single_unit_index = first_unit.get('unit_index') or 1
                        is_batched_text_group = send_group.get('mode') == 'batched_text'

                        if is_batched_text_group:
                            group_log_prefix = (
                                f'[{msg_time}] 多数量自动发货批次 {group_index}/{total_send_groups} '
                                f'({len(group_units)}个单元, {send_group.get("char_count", 0)}字)'
                            )
                        else:
                            group_log_prefix = f'[{msg_time}] 多数量自动发货 {single_unit_index}/{quantity_to_send}'

                        try:
                            await self._send_delivery_steps(
                                websocket,
                                chat_id,
                                send_user_id,
                                send_group.get('delivery_steps') or [],
                                user_url=user_url,
                                log_prefix=group_log_prefix
                            )
                        except Exception as e:
                            group_error = self._safe_str(e)
                            for prepared_unit in group_units:
                                unit_rule_meta = prepared_unit.get('rule_meta') or {}
                                unit_index = prepared_unit.get('unit_index') or 1
                                self._release_data_reservation_if_needed(
                                    unit_rule_meta,
                                    error=f'发送失败(unit={unit_index}): {group_error}'
                                )
                                last_delivery_error = f"发送第 {unit_index}/{quantity_to_send} 个卡券失败: {group_error}"
                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='failed',
                                    reason=last_delivery_error,
                                    channel='auto',
                                    rule_meta=unit_rule_meta
                                )
                                logger.error(last_delivery_error)
                            continue

                        for prepared_unit in group_units:
                            unit_rule_meta = prepared_unit.get('rule_meta') or {}
                            unit_index = prepared_unit.get('unit_index') or 1
                            unit_delivery_steps = prepared_unit.get('delivery_steps') or []

                            try:
                                if not self._mark_data_reservation_sent_if_needed(unit_rule_meta):
                                    self._release_data_reservation_if_needed(
                                        unit_rule_meta,
                                        error=f'发送成功后标记预占已发送失败(unit={unit_index})'
                                    )
                                    last_delivery_error = f'第 {unit_index} 个卡券发送成功后标记预占已发送失败'
                                    self._record_delivery_log(
                                        order_id=order_id,
                                        item_id=item_id,
                                        buyer_id=send_user_id,
                                        buyer_nick=send_user_name,
                                        status='failed',
                                        reason=last_delivery_error,
                                        channel='auto',
                                        rule_meta=unit_rule_meta
                                    )
                                    logger.error(last_delivery_error)
                                    continue

                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    delivery_meta=unit_rule_meta,
                                    channel='auto',
                                    status='sent'
                                )

                                finalize_result = await self._finalize_delivery_after_send(
                                    delivery_meta=unit_rule_meta,
                                    order_id=order_id,
                                    item_id=item_id
                                )
                                if not finalize_result.get('success'):
                                    last_delivery_error = finalize_result.get('error') or f"第 {unit_index} 条消息发送成功但提交发货副作用失败"
                                    if self._is_platform_confirm_failure_error(last_delivery_error):
                                        self._mark_delivery_pending_platform_confirm(
                                            order_id=order_id,
                                            item_id=item_id,
                                            buyer_id=send_user_id,
                                            delivery_meta=unit_rule_meta,
                                            confirm_error=last_delivery_error,
                                            expected_quantity=quantity_to_send,
                                            context="多数量自动发货发送成功后平台确认失败"
                                        )
                                    else:
                                        self._persist_delivery_finalization_state(
                                            order_id=order_id,
                                            item_id=item_id,
                                            buyer_id=send_user_id,
                                            delivery_meta=unit_rule_meta,
                                            channel='auto',
                                            status='sent',
                                            last_error=last_delivery_error
                                        )
                                    self._record_delivery_log(
                                        order_id=order_id,
                                        item_id=item_id,
                                        buyer_id=send_user_id,
                                        buyer_nick=send_user_name,
                                        status='failed',
                                        reason=last_delivery_error,
                                        channel='auto',
                                        rule_meta=unit_rule_meta
                                    )
                                    logger.error(last_delivery_error)
                                    continue

                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    delivery_meta=unit_rule_meta,
                                    channel='auto',
                                    status='finalized'
                                )

                                successful_send_count += 1

                                has_image_step = any(step.get('type') == 'image' for step in unit_delivery_steps)
                                if has_image_step:
                                    success_reason = '自动发货图片步骤发送成功'
                                elif is_batched_text_group and len(group_units) > 1:
                                    success_reason = '自动发货文本批量合并发送成功'
                                else:
                                    success_reason = '自动发货文本发送成功'

                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='success',
                                    reason=success_reason,
                                    channel='auto',
                                    rule_meta=unit_rule_meta
                                )
                            except Exception as unit_post_error:
                                last_delivery_error = f"第 {unit_index} 个卡券消息已发送，但发送后处理异常: {self._safe_str(unit_post_error)}"
                                self._persist_delivery_finalization_state(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    delivery_meta=unit_rule_meta,
                                    channel='auto',
                                    status='sent',
                                    last_error=last_delivery_error
                                )
                                self._record_delivery_log(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    status='failed',
                                    reason=last_delivery_error,
                                    channel='auto',
                                    rule_meta=unit_rule_meta
                                )
                                logger.error(last_delivery_error)

                        if total_send_groups > 1 and group_index < total_send_groups:
                            await asyncio.sleep(1)

                    progress_summary = self._sync_order_delivery_progress(
                        order_id=order_id,
                        cookie_id=self.cookie_id,
                        expected_quantity=quantity_to_send,
                        context="自动发货进度同步"
                    ) if order_id else None

                    if progress_summary and progress_summary.get('aggregate_status') in {'partial_success', 'partial_pending_finalize', 'shipped'}:
                        self._activate_delivery_lock(lock_key, delay_minutes=10)

                    if successful_send_count > 0:
                        if progress_summary and quantity_to_send > 1:
                            aggregate_status = progress_summary.get('aggregate_status')
                            finalized_count = progress_summary.get('finalized_count', 0)
                            pending_finalize_count = progress_summary.get('pending_finalize_count', 0)
                            remaining_count = progress_summary.get('remaining_count', 0)

                            if aggregate_status == 'partial_pending_finalize':
                                notify_message = (
                                    f"多数量发货部分完成，已完成 {finalized_count}/{quantity_to_send}，"
                                    f"待收尾 {pending_finalize_count}，待补发 {remaining_count}"
                                )
                            elif aggregate_status == 'partial_success':
                                notify_message = (
                                    f"多数量发货部分成功，已完成 {finalized_count}/{quantity_to_send}，"
                                    f"待补发 {remaining_count}"
                                )
                            else:
                                notify_message = f"多数量发货成功，共完成 {finalized_count}/{quantity_to_send} 个卡券"
                            await self.send_delivery_failure_notification(send_user_name, send_user_id, item_id, notify_message, chat_id, order_id=order_id)
                        else:
                            await self.send_delivery_failure_notification(send_user_name, send_user_id, item_id, "发货成功", chat_id, order_id=order_id)
                    else:
                        logger.warning(f'[{msg_time}] 【自动发货】未找到匹配的发货规则或获取发货内容失败')
                        self._record_delivery_log(
                            order_id=order_id,
                            item_id=item_id,
                            buyer_id=send_user_id,
                            buyer_nick=send_user_name,
                            status='failed',
                            reason=last_delivery_error or "未找到匹配的发货规则或获取发货内容失败",
                            channel='auto'
                        )
                        await self.send_delivery_failure_notification(send_user_name, send_user_id, item_id, last_delivery_error or "未找到匹配的发货规则或获取发货内容失败", chat_id, order_id=order_id)

                except Exception as e:
                    self._record_delivery_log(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        buyer_nick=send_user_name,
                        status='failed',
                        reason=f"自动发货处理异常: {self._safe_str(e)}",
                        channel='auto'
                    )
                    logger.error(f"自动发货处理异常: {self._safe_str(e)}")
                    # 发送自动发货异常通知
                    await self.send_delivery_failure_notification(send_user_name, send_user_id, item_id, f"自动发货处理异常: {str(e)}", chat_id, order_id=order_id)

                logger.info(f'[{msg_time}] 【{self.cookie_id}】订单锁释放: {lock_key}，自动发货处理完成')

        except Exception as e:
            self._record_delivery_log(
                item_id=item_id,
                buyer_id=send_user_id,
                buyer_nick=send_user_name,
                status='failed',
                reason=f"统一自动发货处理异常: {self._safe_str(e)}",
                channel='auto'
            )
            logger.error(f"统一自动发货处理异常: {self._safe_str(e)}")



    def _reload_latest_cookies_from_db(self, reason: str = "") -> bool:
        """从数据库重载当前账号最新 Cookie。"""
        try:
            from db_manager import db_manager

            account_info = db_manager.get_cookie_details(self.cookie_id)
            new_cookies_str = self._extract_cookie_value(account_info)
            if new_cookies_str and new_cookies_str != self.cookies_str:
                suffix = f" ({reason})" if reason else ""
                logger.info(f"【{self.cookie_id}】检测到数据库中的cookie已更新，重新加载cookie{suffix}")
                self._set_runtime_cookie_state(cookies_str=new_cookies_str, source=f"db_reload{suffix}")
                logger.warning(f"【{self.cookie_id}】Cookie已从数据库重新加载")
                return True
        except Exception as reload_e:
            logger.warning(f"【{self.cookie_id}】从数据库重新加载cookie失败，继续使用当前cookie: {self._safe_str(reload_e)}")
        return False

    def _serialize_cookies(self, cookies_dict: Optional[Dict[str, Any]] = None) -> str:
        cookies = cookies_dict or self.cookies
        return '; '.join([f"{k}={v}" for k, v in cookies.items() if k])

    def _sync_session_cookie_header(self):
        if self.session and not self.session.closed:
            self.session.headers['cookie'] = self.cookies_str

    def _set_runtime_cookie_state(
        self,
        cookies_str: Optional[str] = None,
        cookies_dict: Optional[Dict[str, Any]] = None,
        source: str = "runtime_update",
    ) -> bool:
        normalized_cookies = dict(cookies_dict or trans_cookies(cookies_str or ""))
        if not normalized_cookies:
            logger.warning(f"【{self.cookie_id}】忽略空Cookie更新: source={source}")
            return False

        previous_cookie_string = self.cookies_str
        previous_unb = self.cookies.get('unb') if isinstance(self.cookies, dict) else None

        self.cookies = normalized_cookies
        self.cookies_str = self._serialize_cookies(normalized_cookies)

        new_unb = self.cookies.get('unb')
        if new_unb and new_unb != previous_unb:
            logger.warning(f"【{self.cookie_id}】Cookie中的unb发生变化: {previous_unb} -> {new_unb} (source={source})")
            self.myid = new_unb
            self.device_id = generate_device_id(self.myid)

        self._sync_session_cookie_header()
        return self.cookies_str != previous_cookie_string

    async def _persist_runtime_cookie_state(
        self,
        cookies_str: Optional[str] = None,
        cookies_dict: Optional[Dict[str, Any]] = None,
        source: str = "runtime_update",
    ) -> bool:
        changed = self._set_runtime_cookie_state(
            cookies_str=cookies_str,
            cookies_dict=cookies_dict,
            source=source,
        )
        if changed:
            await self.update_config_cookies()
            self._schedule_pending_platform_confirm_retry(f"Cookie更新:{source}")
        return changed

    def _extract_set_cookie_updates(self, response_headers) -> Dict[str, str]:
        if not response_headers:
            return {}

        set_cookie_values = []
        try:
            if hasattr(response_headers, 'getall') and 'set-cookie' in response_headers:
                set_cookie_values = response_headers.getall('set-cookie', [])
            elif hasattr(response_headers, 'get_all'):
                set_cookie_values = response_headers.get_all('set-cookie', [])
            elif isinstance(response_headers, dict):
                raw_value = response_headers.get('set-cookie') or response_headers.get('Set-Cookie')
                if isinstance(raw_value, list):
                    set_cookie_values = raw_value
                elif raw_value:
                    set_cookie_values = [raw_value]
        except Exception:
            set_cookie_values = []

        updates = {}
        for cookie in set_cookie_values:
            if '=' not in cookie:
                continue
            name, value = cookie.split(';')[0].split('=', 1)
            updates[name.strip()] = value.strip()
        return updates

    async def _apply_response_cookie_updates(self, response_headers, source: str) -> bool:
        updates = self._extract_set_cookie_updates(response_headers)
        if not updates:
            return False

        merged_cookies = dict(self.cookies)
        merged_cookies.update(updates)
        changed = await self._persist_runtime_cookie_state(
            cookies_dict=merged_cookies,
            source=source,
        )
        if changed:
            logger.info(f"【{self.cookie_id}】已应用 {len(updates)} 个响应Cookie更新: source={source}")
        return changed

    def _build_websocket_headers(self) -> Dict[str, str]:
        headers = WEBSOCKET_HEADERS.copy()
        headers['Cookie'] = self.cookies_str
        return headers

    def _mark_slider_success_recovery(self, cookies_str: str = ""):
        self.last_slider_success_at = time.time()
        self.last_slider_success_cookie_length = len(cookies_str or "")

    def _build_cookie_string_with_updates(self, base_cookie_string: str = None, updated_cookies: Optional[Dict[str, Any]] = None) -> str:
        merged_cookies = trans_cookies(base_cookie_string or self.cookies_str)
        for key, value in (updated_cookies or {}).items():
            if key:
                merged_cookies[str(key).strip()] = str(value)
        return self._serialize_cookies(merged_cookies)

    def _mark_pending_slider_success_notice(self, source: str = "token_refresh"):
        self.pending_slider_success_notice = {
            'source': source,
            'timestamp': time.time(),
        }

    def _consume_pending_slider_success_notice(self, max_age_seconds: int = 180) -> Optional[Dict[str, Any]]:
        notice = self.pending_slider_success_notice
        self.pending_slider_success_notice = None
        if not notice:
            return None

        notice_timestamp = float(notice.get('timestamp') or 0)
        if notice_timestamp and (time.time() - notice_timestamp) <= max_age_seconds:
            return notice

        logger.info(f"【{self.cookie_id}】检测到过期的滑块成功待发送通知，已自动丢弃")
        return None

    def _clear_pending_slider_success_notice(self, reason: str = None):
        if self.pending_slider_success_notice:
            suffix = f" ({reason})" if reason else ""
            logger.info(f"【{self.cookie_id}】已清理滑块成功待发送通知{suffix}")
        self.pending_slider_success_notice = None

    def _build_x5_cookie_snapshot(self, cookie_string: str = None, cookies_dict: dict = None) -> Dict[str, Dict[str, Any]]:
        source_dict = cookies_dict if cookies_dict is not None else trans_cookies(cookie_string or self.cookies_str)
        snapshot = {}
        for key in ('x5sec', 'x5secdata'):
            value = source_dict.get(key)
            snapshot[key] = {
                'present': bool(value),
                'length': len(str(value)) if value else 0,
                'hash': hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:12] if value else None,
            }
        return snapshot

    def _log_x5_cookie_snapshot(self, label: str, cookie_string: str = None, cookies_dict: dict = None):
        snapshot = self._build_x5_cookie_snapshot(cookie_string=cookie_string, cookies_dict=cookies_dict)
        parts = []
        for key, info in snapshot.items():
            if info.get('present'):
                parts.append(f"{key}=存在(len={info['length']}, sha={info['hash']})")
            else:
                parts.append(f"{key}=缺失")
        logger.info(f"【{self.cookie_id}】{label}: {', '.join(parts)}")

    @classmethod
    def protected_merge_cookie_dicts(cls, existing_cookies_dict, incoming_cookies_dict):
        """保护性合并 Cookie，避免不完整快照覆盖关键会话字段。"""
        existing = dict(existing_cookies_dict or {})
        incoming = dict(incoming_cookies_dict or {})
        existing_count = len(existing)
        incoming_count = len(incoming)
        existing_unb = str(existing.get('unb') or '').strip()
        incoming_unb = str(incoming.get('unb') or '').strip()
        account_switched = bool(existing_unb and incoming_unb and existing_unb != incoming_unb)

        if account_switched:
            merged = incoming.copy()
        else:
            merged = existing.copy()
            for key, value in incoming.items():
                merged[key] = value

        updated_fields = []
        changed_fields = []
        new_fields = []
        for key, value in incoming.items():
            old_value = existing.get(key)
            if old_value is None:
                updated_fields.append(f"{key}(新增)")
                new_fields.append(key)
            elif old_value != value:
                updated_fields.append(key)
                changed_fields.append(key)

        would_remove_fields = [key for key in existing.keys() if key not in incoming]
        if account_switched:
            removed_fields = list(would_remove_fields)
            preserved_fields = []
            preserved_protected_fields = []
        else:
            removed_fields = []
            preserved_fields = list(would_remove_fields)
            preserved_protected_fields = [
                key for key in would_remove_fields
                if key in PROTECTED_SESSION_COOKIE_FIELDS and existing.get(key)
            ]

        missing_protected_fields = [
            key for key in PROTECTED_SESSION_COOKIE_FIELDS
            if not merged.get(key)
        ]
        missing_required_fields = [
            key for key in REQUIRED_SESSION_COOKIE_FIELDS
            if not merged.get(key)
        ]
        incoming_missing_protected_fields = [
            key for key in PROTECTED_SESSION_COOKIE_FIELDS
            if not incoming.get(key)
        ]
        incoming_missing_required_fields = [
            key for key in REQUIRED_SESSION_COOKIE_FIELDS
            if not incoming.get(key)
        ]

        return {
            'existing_cookies_dict': existing,
            'incoming_cookies_dict': incoming,
            'merged_cookies_dict': merged,
            'existing_count': existing_count,
            'incoming_count': incoming_count,
            'merged_count': len(merged),
            'updated_fields': updated_fields,
            'changed_fields': changed_fields,
            'new_fields': new_fields,
            'would_remove_fields': would_remove_fields,
            'removed_fields': removed_fields,
            'preserved_fields': preserved_fields,
            'preserved_protected_fields': preserved_protected_fields,
            'missing_protected_fields': missing_protected_fields,
            'missing_required_fields': missing_required_fields,
            'incoming_missing_protected_fields': incoming_missing_protected_fields,
            'incoming_missing_required_fields': incoming_missing_required_fields,
            'account_switched': account_switched,
        }

    def _merge_cookie_dicts(self, incoming_cookies_dict, existing_cookies_dict=None):
        """兼容旧调用，返回保护性合并结果。"""
        merge_result = self.protected_merge_cookie_dicts(
            existing_cookies_dict if existing_cookies_dict is not None else trans_cookies(self.cookies_str),
            incoming_cookies_dict,
        )
        return (
            merge_result['existing_cookies_dict'],
            merge_result['merged_cookies_dict'],
            merge_result['updated_fields'],
            merge_result['changed_fields'],
            merge_result['new_fields'],
        )

    def _log_protected_merge_event(self, event_name: str, merge_result: Dict[str, Any]):
        """输出受保护 Cookie 合并审计日志，便于定位快照覆盖问题。"""
        if not merge_result:
            return

        protected_preserved_fields = merge_result.get('preserved_protected_fields') or []
        would_remove_fields = merge_result.get('would_remove_fields') or []
        logger.info(
            f"【{self.cookie_id}】{event_name} "
            f"incoming_count={merge_result.get('incoming_count', 0)} "
            f"existing_count={merge_result.get('existing_count', 0)} "
            f"merged_count={merge_result.get('merged_count', 0)} "
            f"protected_preserved_fields={protected_preserved_fields} "
            f"would_remove_fields={would_remove_fields} "
            f"account_switched={merge_result.get('account_switched', False)}"
        )

    def _log_cookie_merge_summary(self, merged_cookies_dict, updated_fields, changed_fields, new_fields, context: str,
                                  preserved_fields=None, preserved_protected_fields=None,
                                  would_remove_fields=None, removed_fields=None,
                                  missing_protected_fields=None, missing_required_fields=None,
                                  incoming_missing_protected_fields=None, account_switched: bool = False):
        """打印 Cookie 合并结果，重点关注会话关键字段。"""
        context_prefix = f"{context}：" if context else ""
        logger.info(f"【{self.cookie_id}】{context_prefix}合并后cookies包含 {len(merged_cookies_dict)} 个字段")

        if updated_fields:
            logger.info(f"【{self.cookie_id}】{context_prefix}更新的cookie字段: {', '.join(updated_fields)}")
        else:
            logger.info(f"【{self.cookie_id}】{context_prefix}没有cookie字段需要更新")

        if account_switched:
            logger.warning(f"【{self.cookie_id}】{context_prefix}检测到unb变化，按账号切换处理，不保留旧账号Cookie字段")

        if preserved_protected_fields:
            logger.warning(
                f"【{self.cookie_id}】{context_prefix}保护性保留关键字段 ({len(preserved_protected_fields)}个): {', '.join(preserved_protected_fields)}"
            )
        if preserved_fields:
            logger.info(
                f"【{self.cookie_id}】{context_prefix}保留旧Cookie字段 ({len(preserved_fields)}个): {', '.join(preserved_fields)}"
            )
        if would_remove_fields:
            logger.info(
                f"【{self.cookie_id}】{context_prefix}浏览器快照未返回的旧字段 ({len(would_remove_fields)}个): {', '.join(would_remove_fields)}"
            )
        if removed_fields:
            logger.warning(
                f"【{self.cookie_id}】{context_prefix}实际移除旧字段 ({len(removed_fields)}个): {', '.join(removed_fields)}"
            )
        if incoming_missing_protected_fields:
            logger.warning(
                f"【{self.cookie_id}】{context_prefix}新快照缺失的关键字段 ({len(incoming_missing_protected_fields)}个): {', '.join(incoming_missing_protected_fields)}"
            )
        if missing_protected_fields:
            logger.warning(
                f"【{self.cookie_id}】{context_prefix}合并后仍缺失的受保护字段 ({len(missing_protected_fields)}个): {', '.join(missing_protected_fields)}"
            )
        if missing_required_fields:
            logger.error(
                f"【{self.cookie_id}】{context_prefix}合并后仍缺失的核心字段 ({len(missing_required_fields)}个): {', '.join(missing_required_fields)}"
            )

        important_keys = list(PROTECTED_SESSION_COOKIE_FIELDS) + ['x5sec', 'x5secdata']
        logger.info(f"【{self.cookie_id}】{context_prefix}关键字段检查:")
        for key in important_keys:
            if key in merged_cookies_dict:
                val = merged_cookies_dict[key]
                marker = " [已变化]" if key in changed_fields else " [新增]" if key in new_fields else ""
                logger.info(f"【{self.cookie_id}】  ✅ {key}: {'存在' if val else '为空'} (长度: {len(str(val)) if val else 0}){marker}")
            else:
                logger.info(f"【{self.cookie_id}】  ❌ {key}: 缺失")

    def _has_recent_slider_success(self, window_seconds: int = None) -> bool:
        if not self.last_slider_success_at:
            return False
        window = window_seconds or self.slider_success_reentry_window
        return (time.time() - self.last_slider_success_at) <= window

    @staticmethod
    def _coerce_config_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {'1', 'true', 'yes', 'on'}:
            return True
        if text in {'0', 'false', 'no', 'off'}:
            return False
        return default

    @classmethod
    def _is_soft_auth_token_preflight_enabled(cls, source: str = '') -> bool:
        enabled = cls._coerce_config_bool(
            RISK_CONTROL.get('soft_auth_token_preflight_enabled', True),
            True,
        )
        if not enabled:
            return False

        normalized_source = str(source or '').strip().lower()
        if normalized_source.startswith('qr_login'):
            # 扫码登录当前有稳定期保护，默认不主动请求 token；需要时可通过配置显式开启。
            return cls._coerce_config_bool(
                RISK_CONTROL.get('soft_auth_token_preflight_qr_enabled', False),
                False,
            )
        return True

    @staticmethod
    def _get_soft_auth_token_preflight_timeout() -> float:
        try:
            timeout = float(RISK_CONTROL.get('soft_auth_token_preflight_timeout_seconds', 5.0) or 5.0)
        except (TypeError, ValueError):
            timeout = 5.0
        return max(2.0, min(timeout, 15.0))

    async def soft_preflight_token_after_auth(
        self,
        cookie_string: str = None,
        source: str = 'auth_success',
        proxy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """认证成功后的轻量 token 软预检。

        该方法只做观测与 token 预热：网络失败、超时、未知响应都不会阻断既有登录/刷新流程。
        默认也不会在扫码登录稳定期内主动触发，避免破坏当前风控保护策略。
        """
        normalized_source = str(source or 'auth_success').strip() or 'auth_success'
        if not self._is_soft_auth_token_preflight_enabled(normalized_source):
            return {
                'status': 'skipped',
                'source': normalized_source,
                'reason': 'disabled_by_config',
                'token_cached': False,
            }

        target_cookie_string = str(cookie_string or self.cookies_str or '').strip()
        if not target_cookie_string:
            logger.warning(f"【{self.cookie_id}】{normalized_source} 软Token预检跳过：Cookie为空")
            return {
                'status': 'skipped',
                'source': normalized_source,
                'reason': 'empty_cookie',
                'token_cached': False,
            }

        timeout = self._get_soft_auth_token_preflight_timeout()
        try:
            from utils.xianyu_slider_stealth import probe_cookie_verification_from_cookie

            logger.info(
                f"【{self.cookie_id}】开始{normalized_source}软Token预检，timeout={timeout:.1f}s（失败不阻断原流程）"
            )
            probe_result = await asyncio.to_thread(
                probe_cookie_verification_from_cookie,
                target_cookie_string,
                proxy if proxy is not None else self.proxy_config,
                timeout,
            )
        except Exception as preflight_err:
            logger.warning(
                f"【{self.cookie_id}】{normalized_source}软Token预检未完成，不阻断原流程: {self._safe_str(preflight_err)}"
            )
            return {
                'status': 'inconclusive',
                'source': normalized_source,
                'reason': self._safe_str(preflight_err),
                'token_cached': False,
            }

        status = str(probe_result.get('status') or 'unknown')
        verification_url = str(probe_result.get('verification_url') or '').strip()
        token_cached = False
        if status == 'cookie_valid':
            data_payload = (probe_result.get('payload') or {}).get('data') or {}
            access_token = str(data_payload.get('accessToken') or '').strip()
            if access_token:
                self.cache_auth_prewarmed_token(
                    self.cookie_id,
                    access_token,
                    source=f'soft_preflight:{normalized_source}',
                )
                token_cached = True
            logger.info(
                f"【{self.cookie_id}】{normalized_source}软Token预检通过"
                f"{'，已缓存预热token' if token_cached else ''}"
            )
        elif status == 'verification_required':
            logger.warning(
                f"【{self.cookie_id}】{normalized_source}软Token预检返回验证要求，不阻断原流程: {verification_url or '无URL'}"
            )
        else:
            logger.warning(
                f"【{self.cookie_id}】{normalized_source}软Token预检结果未知(status={status})，不阻断原流程"
            )

        return {
            'status': status,
            'source': normalized_source,
            'verification_url': verification_url,
            'token_cached': token_cached,
            'success_ret': bool(probe_result.get('success_ret')),
            'has_token_payload': bool(probe_result.get('has_token_payload')),
        }

    async def preflight_token_after_manual_refresh(self) -> str:
        """手动刷新成功后的 token 预检，确认新实例可直接完成初始化。

        🔧 增加重试机制：密码登录获取的 Cookie 可能需要短暂时间在服务端生效，
        首次 Token 刷新可能因 session 未就绪而失败，等待后重试可提高成功率。
        """
        logger.info(f"【{self.cookie_id}】开始执行手动刷新后的Token预检...")
        self.last_message_received_time = 0

        max_preflight_retries = 3
        for attempt in range(1, max_preflight_retries + 1):
            token = await self.refresh_token(allow_password_login_recovery=False)
            if token:
                self.cache_auth_prewarmed_token(self.cookie_id, token, source='manual_refresh_handoff')
                logger.info(f"【{self.cookie_id}】手动刷新后的Token预检成功（第{attempt}次），已缓存预热token供新实例复用")
                return token

            if attempt < max_preflight_retries:
                wait_secs = 2.0 * attempt
                logger.warning(
                    f"【{self.cookie_id}】Token预检第{attempt}次失败（状态: {self.last_token_refresh_status}），"
                    f"等待{wait_secs:.0f}秒后重试（Cookie可能尚未在服务端生效）"
                )
                await asyncio.sleep(wait_secs)

        raise InitAuthError(f"手动刷新后的Token预检失败，状态: {self.last_token_refresh_status or 'unknown'}")

    async def refresh_token(self, captcha_retry_count: int = 0, allow_password_login_recovery: bool = True):
        if self.token_refresh_lock.locked():
            logger.info(f"【{self.cookie_id}】Token刷新已有执行中任务，等待当前流程完成后复用结果")

        async with self.token_refresh_lock:
            dedup_window = max(5, int(RISK_CONTROL.get('token_refresh_dedup_window_seconds', 60) or 60))
            if (
                captcha_retry_count == 0 and
                self.current_token and
                self.last_token_refresh_status == "success" and
                (time.time() - self.last_token_refresh_time) < dedup_window
            ):
                logger.info(f"【{self.cookie_id}】最近{dedup_window}秒内已有成功的Token刷新结果，直接复用当前Token")
                return self.current_token
            if captcha_retry_count == 0 and self._should_skip_token_refresh_for_login_backoff():
                return None
            return await self._refresh_token_impl(
                captcha_retry_count,
                allow_password_login_recovery=allow_password_login_recovery,
            )

    def _is_auth_failure_ret(self, ret_value: Any) -> bool:
        if isinstance(ret_value, str):
            ret_text = ret_value
        elif isinstance(ret_value, (list, tuple)):
            ret_text = ' | '.join([str(item) for item in ret_value])
        else:
            ret_text = str(ret_value or '')

        auth_keywords = (
            '令牌过期',
            'session过期',
            'FAIL_SYS_USER_VALIDATE',
            'FAIL_SYS_TOKEN_EXPIRED',
            'FAIL_SYS_TOKEN_EXOIRED',
            'FAIL_SYS_SESSION_EXPIRED',
            'passport.goofish.com',
            'mini_login',
            'login',
        )
        ret_text_lower = ret_text.lower()
        return any(keyword.lower() in ret_text_lower for keyword in auth_keywords)

    async def keep_session_alive(self) -> bool:
        """使用 loginuser.get 轻量维持网页登录态。"""
        self.last_session_keepalive_status = "started"
        self.last_session_keepalive_error_message = None

        try:
            if not self.session:
                await self.create_session()

            self._reload_latest_cookies_from_db("轻量保活前")

            params = {
                'jsv': '2.7.2',
                'appKey': '34839810',
                't': str(int(time.time() * 1000)),
                'sign': '',
                'v': '1.0',
                'type': 'originaljson',
                'accountSite': 'xianyu',
                'dataType': 'json',
                'timeout': '20000',
                'api': 'mtop.taobao.idlemessage.pc.loginuser.get',
                'sessionOption': 'AutoLoginOnly',
                'spm_cnt': 'a21ybx.im.0.0',
                'spm_pre': 'a21ybx.item.want.1.12523da6waCtUp',
                'log_id': '12523da6waCtUp',
            }
            data_val = '{}'
            data = {'data': data_val}

            token = trans_cookies(self.cookies_str).get('_m_h5_tk', '').split('_')[0] if trans_cookies(self.cookies_str).get('_m_h5_tk') else ''
            params['sign'] = generate_sign(params['t'], token, data_val)

            headers = DEFAULT_HEADERS.copy()
            headers['content-type'] = 'application/x-www-form-urlencoded'
            headers['cookie'] = self.cookies_str

            request_kwargs = {}
            if getattr(self, '_http_proxy_url', None):
                request_kwargs['proxy'] = self._http_proxy_url

            api_url = API_ENDPOINTS.get('login_user')
            async with self.session.post(
                api_url,
                params=params,
                data=data,
                headers=headers,
                **request_kwargs,
            ) as response:
                try:
                    res_json = await response.json(content_type=None)
                except Exception:
                    response_text = await response.text()
                    self.last_session_keepalive_status = "response_parse_failed"
                    self.last_session_keepalive_error_message = response_text[:200]
                    logger.warning(f"【{self.cookie_id}】轻量保活响应解析失败: {response_text[:200]}")
                    return False

                await self._apply_response_cookie_updates(response.headers, "session_keepalive")
                ret_value = res_json.get('ret', [])
                if any('SUCCESS::调用成功' in str(ret) for ret in ret_value):
                    self.last_session_keepalive_status = "success"
                    self.last_session_keepalive_error_message = None
                    self.last_session_keepalive_time = time.time()
                    logger.info(f"【{self.cookie_id}】轻量会话保活成功")
                    self._schedule_pending_platform_confirm_retry("轻量会话保活成功")
                    return True

                error_message = ' | '.join([str(ret) for ret in ret_value]) or '未知错误'
                self.last_session_keepalive_error_message = error_message
                self.last_session_keepalive_status = "auth_failed" if self._is_auth_failure_ret(ret_value) else "api_failed"
                logger.warning(f"【{self.cookie_id}】轻量会话保活失败: {error_message}")
                return False

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.last_session_keepalive_status = "network_failed"
            self.last_session_keepalive_error_message = self._safe_str(e)
            logger.warning(f"【{self.cookie_id}】轻量会话保活网络异常: {self._safe_str(e)}")
            return False
        except Exception as e:
            self.last_session_keepalive_status = "exception"
            self.last_session_keepalive_error_message = self._safe_str(e)
            logger.error(f"【{self.cookie_id}】轻量会话保活异常: {self._safe_str(e)}")
            return False

    async def _refresh_token_impl(self, captcha_retry_count: int = 0, post_slider_session_grace_used: bool = False,
                                  allow_password_login_recovery: bool = True,
                                  manual_refresh_browser_stabilization_used: bool = False,
                                  post_slider_session_retry_count: int = 0):
        """刷新token

        Args:
            captcha_retry_count: 滑块验证重试次数，用于防止无限递归
        """
        # 初始化通知发送标志，避免重复发送通知
        notification_sent = False
        
        try:
            logger.info(f"【{self.cookie_id}】开始刷新token... (滑块验证重试次数: {captcha_retry_count})")
            # 标记本次刷新状态
            self.last_token_refresh_status = "started"
            self.last_token_refresh_error_message = None
            # 重置“刷新流程内已重启”标记，避免多次重启
            self.restarted_in_browser_refresh = False

            # 检查滑块验证重试次数，防止无限递归
            if captcha_retry_count >= self.max_captcha_verification_count:
                logger.error(f"【{self.cookie_id}】滑块验证重试次数已达上限 ({self.max_captcha_verification_count})，停止重试")
                self.last_token_refresh_status = "captcha_max_retries_exceeded"
                self._clear_pending_slider_success_notice("滑块重试次数达到上限")
                await self.send_token_refresh_notification(
                    f"滑块验证重试次数已达上限，请手动处理",
                    "captcha_max_retries_exceeded"
                )
                notification_sent = True
                return None

            # 【消息接收检查】检查是否在消息接收后的冷却时间内，与 cookie_refresh_loop 保持一致
            current_time = time.time()
            time_since_last_message = current_time - self.last_message_received_time
            if self.last_message_received_time > 0 and time_since_last_message < self.message_cookie_refresh_cooldown:
                remaining_time = self.message_cookie_refresh_cooldown - time_since_last_message
                remaining_minutes = int(remaining_time // 60)
                remaining_seconds = int(remaining_time % 60)
                logger.info(f"【{self.cookie_id}】收到消息后冷却中，放弃本次token刷新，还需等待 {remaining_minutes}分{remaining_seconds}秒")
                # 标记为因冷却而跳过（正常情况）
                self.last_token_refresh_status = "skipped_cooldown"
                return None

            if self._should_skip_token_refresh_for_login_backoff(current_time):
                return None

            # 【重要】在刷新token前，先从数据库重新加载最新的cookie
            # 这样即使用户已经手动更新了cookie，代码也会使用最新的cookie
            logger.info(f"【{self.cookie_id}】开始执行Cookie刷新任务...")
            self._reload_latest_cookies_from_db("token刷新前")

            # 生成更精确的时间戳
            timestamp = str(int(time.time() * 1000))

            params = {
                'jsv': '2.7.2',
                'appKey': '34839810',
                't': timestamp,
                'sign': '',
                'v': '1.0',
                'type': 'originaljson',
                'accountSite': 'xianyu',
                'dataType': 'json',
                'timeout': '20000',
                'api': 'mtop.taobao.idlemessage.pc.login.token',
                'sessionOption': 'AutoLoginOnly',
                'dangerouslySetWindvaneParams': '%5Bobject%20Object%5D',
                'smToken': 'token',
                'queryToken': 'sm',
                'sm': 'sm',
                'spm_cnt': 'a21ybx.im.0.0',
                'spm_pre': 'a21ybx.home.sidebar.1.4c053da6vYwnmf',
                'log_id': '4c053da6vYwnmf'
            }
            data_val = '{"appKey":"444e9908a51d1cb236a27862abc769c9","deviceId":"' + self.device_id + '"}'
            data = {
                'data': data_val,
            }

            # 获取token
            token = trans_cookies(self.cookies_str).get('_m_h5_tk', '').split('_')[0] if trans_cookies(self.cookies_str).get('_m_h5_tk') else ''

            sign = generate_sign(params['t'], token, data_val)
            params['sign'] = sign

            # 发送请求 - 使用与浏览器完全一致的请求头
            headers = {
                'accept': 'application/json',
                'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'cache-control': 'no-cache',
                'content-type': 'application/x-www-form-urlencoded',
                'pragma': 'no-cache',
                'priority': 'u=1, i',
                'sec-ch-ua': '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-site',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
                'referer': 'https://www.goofish.com/',
                'origin': 'https://www.goofish.com',
                'cookie': self.cookies_str
            }

            # 发送Token刷新请求
            api_url = API_ENDPOINTS.get('token')
            logger.info(f"【{self.cookie_id}】正在刷新Token... API: {api_url}")
            
            # 详细调试信息（仅debug级别）
            logger.debug(f"【{self.cookie_id}】Token刷新参数: timestamp={params['t']}, sign={sign[:16]}...")

            if not self.session:
                await self.create_session()
            request_kwargs = {}
            if getattr(self, '_http_proxy_url', None):
                request_kwargs['proxy'] = self._http_proxy_url
            async with self.session.post(
                    api_url,
                    params=params,
                    data=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                    **request_kwargs,
                ) as response:
                    res_json = await response.json(content_type=None)
                    # 简化日志输出
                    ret_info = res_json.get('ret', [])
                    logger.debug(f"【{self.cookie_id}】Token刷新响应: status={response.status}, ret={ret_info}")

                    response_set_cookies = self._extract_set_cookie_updates(response.headers)

                    transient_recovery_cookies_str = self.cookies_str
                    if response_set_cookies:
                        transient_recovery_cookies_str = self._build_cookie_string_with_updates(
                            self.cookies_str,
                            response_set_cookies
                        )
                        logger.info(
                            f"【{self.cookie_id}】Token预检响应携带 {len(response_set_cookies)} 个临时Cookie，"
                            f"仅用于本次恢复链路，不提前写入数据库"
                        )

                    if isinstance(res_json, dict):
                        ret_value = res_json.get('ret', [])
                        # 检查ret是否包含成功信息
                        if any('SUCCESS::调用成功' in ret for ret in ret_value):
                            if 'data' in res_json and 'accessToken' in res_json['data']:
                                if response_set_cookies:
                                    await self._apply_response_cookie_updates(response.headers, "token_refresh")
                                    logger.warning(f"【{self.cookie_id}】Token刷新成功后已更新Cookie到数据库")

                                new_token = res_json['data']['accessToken']
                                self.current_token = new_token
                                self.last_token_refresh_time = time.time()

                                # 【消息接收时间重置】Token刷新成功后重置消息接收标志，与 cookie_refresh_loop 保持一致
                                self.last_message_received_time = 0
                                logger.warning(f"【{self.cookie_id}】Token刷新成功，已重置消息接收时间标识")
                                self._clear_qr_login_grace_period()
                                self.clear_init_auth_failure_state(self.cookie_id)
                                self.last_init_failure_reason = None
                                self.last_init_failure_type = None
                                self.init_auth_failures = 0

                                logger.info(f"【{self.cookie_id}】Token刷新成功")
                                # 标记为成功
                                self.last_token_refresh_status = "success"
                                self.last_token_refresh_error_message = None
                                self._schedule_pending_platform_confirm_retry("token刷新成功")
                                if self._consume_pending_slider_success_notice():
                                    await self.send_token_refresh_notification(
                                        "滑块验证通过，账号会话已恢复",
                                        "slider_recovered_success"
                                    )
                                return new_token

                    # 检查是否需要滑块验证
                    if self._need_captcha_verification(res_json):
                        qr_login_grace = self.get_qr_login_grace(self.cookie_id)
                        if qr_login_grace and not qr_login_grace.get('captcha_buffer_used'):
                            logger.warning(f"【{self.cookie_id}】扫码登录后的首轮Token刷新命中风控，执行一次浏览器侧Cookie稳定化后进入稳定期退避，避免继续挤爆")
                            log_captcha_event(
                                self.cookie_id,
                                "扫码登录首轮Token刷新命中风控，执行浏览器侧稳定化后退避",
                                None,
                                f"触发场景: Token刷新, ret={res_json.get('ret', [])}"
                            )
                            self.update_qr_login_grace(
                                self.cookie_id,
                                captcha_buffer_used=True,
                                captcha_detected_at=time.time()
                            )
                            await asyncio.sleep(2)
                            stabilization_success = await self._refresh_cookies_via_browser_page(
                                transient_recovery_cookies_str,
                                restart_on_success=False
                            )
                            if stabilization_success:
                                self.update_qr_login_grace(
                                    self.cookie_id,
                                    browser_stabilized=True,
                                    browser_stabilized_at=time.time()
                                )
                                logger.info(f"【{self.cookie_id}】浏览器侧Cookie稳定化完成；不立即重试Token，等待扫码登录稳定期结束后再恢复")
                            else:
                                logger.warning(f"【{self.cookie_id}】浏览器侧Cookie稳定化未消除风控；不继续进入滑块验证，等待扫码登录稳定期结束后再恢复")

                            remaining = self._get_qr_login_grace_remaining_seconds()
                            self.last_token_refresh_status = "qr_login_grace_wait"
                            self.last_token_refresh_error_message = f"扫码登录后Token预检命中风控，已进入稳定期退避，剩余{remaining}秒"
                            return None

                        manual_refresh_state = self.get_manual_refresh_state(self.cookie_id)
                        is_manual_refresh_handoff = bool(
                            manual_refresh_state and manual_refresh_state.get('phase') == 'handoff_recovery'
                        )
                        if is_manual_refresh_handoff and not manual_refresh_browser_stabilization_used:
                            logger.warning(f"【{self.cookie_id}】手动刷新交接阶段首轮Token预检命中风控，先执行浏览器侧Cookie稳定化")
                            log_captcha_event(
                                self.cookie_id,
                                "手动刷新交接阶段首轮Token预检命中风控，先执行浏览器侧稳定化",
                                None,
                                f"触发场景: Token刷新, ret={res_json.get('ret', [])}"
                            )
                            before_x5_snapshot = self._build_x5_cookie_snapshot(cookie_string=transient_recovery_cookies_str)
                            self._log_x5_cookie_snapshot("手动刷新交接稳定化前的x5票据", cookie_string=transient_recovery_cookies_str)
                            self.last_token_refresh_status = "manual_refresh_browser_stabilizing"
                            stabilization_success = await self._refresh_cookies_via_browser_page(
                                transient_recovery_cookies_str,
                                restart_on_success=False
                            )
                            if stabilization_success:
                                self._reload_latest_cookies_from_db("手动刷新交接阶段浏览器稳定化")
                                after_x5_snapshot = self._build_x5_cookie_snapshot()
                                self._log_x5_cookie_snapshot("手动刷新交接稳定化后的x5票据")
                                changed_x5_fields = [
                                    key for key in ('x5sec', 'x5secdata')
                                    if before_x5_snapshot.get(key, {}).get('hash') != after_x5_snapshot.get(key, {}).get('hash')
                                ]
                                if changed_x5_fields:
                                    logger.info(
                                        f"【{self.cookie_id}】手动刷新交接阶段浏览器稳定化已更新x5票据: {', '.join(changed_x5_fields)}"
                                    )
                                else:
                                    logger.info(f"【{self.cookie_id}】手动刷新交接阶段浏览器稳定化未观察到x5票据变化，继续重试Token预检")
                                return await self._refresh_token_impl(
                                    captcha_retry_count,
                                    post_slider_session_grace_used=post_slider_session_grace_used,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=True,
                                    post_slider_session_retry_count=post_slider_session_retry_count,
                                )
                            logger.warning(f"【{self.cookie_id}】手动刷新交接阶段浏览器稳定化失败，继续进入滑块验证")

                        if self.is_manual_refresh_active(self.cookie_id, allow_handoff_recovery=True):
                            logger.warning(f"【{self.cookie_id}】检测到手动刷新进行中，跳过自动滑块处理")
                            log_captcha_event(
                                self.cookie_id,
                                "手动刷新进行中，跳过自动滑块处理",
                                None,
                                "触发场景: Token刷新"
                            )
                            self.last_token_refresh_status = "manual_refresh_active"
                            self._clear_pending_slider_success_notice("手动刷新进行中")
                            notification_sent = True
                            return None

                        logger.warning(f"【{self.cookie_id}】检测到需要滑块验证，开始处理...")

                        # 记录滑块验证检测到日志文件
                        verification_url = res_json.get('data', {}).get('url', 'Token刷新时检测')
                        log_captcha_event(self.cookie_id, "检测到滑块验证", None, f"触发场景: Token刷新, URL: {verification_url}")
                        captcha_trigger_scene = 'token_refresh'
                        captcha_session_id = self._new_risk_session_id('slider')
                        captcha_event_meta = self._build_risk_event_meta(
                            trigger_scene=captcha_trigger_scene,
                            verification_url=verification_url,
                            extra={'cookie_id': self.cookie_id}
                        )

                        # 添加风控日志记录
                        log_id = None
                        try:
                            log_id = self._create_risk_log(
                                event_type='slider_captcha',
                                session_id=captcha_session_id,
                                trigger_scene=captcha_trigger_scene,
                                result_code='slider_captcha_detected',
                                event_description='检测到滑块验证（Token刷新）',
                                processing_status='processing',
                                event_meta=captcha_event_meta,
                            )
                            if log_id:
                                logger.info(f"【{self.cookie_id}】风控日志记录成功，ID: {log_id}")
                        except Exception as log_e:
                            logger.error(f"【{self.cookie_id}】记录风控日志失败: {log_e}")

                        try:
                            # 尝试通过滑块验证获取新的cookies
                            captcha_start_time = time.time()
                            new_cookies_str = await self._handle_captcha_verification(res_json)
                            captcha_duration = time.time() - captcha_start_time

                            if new_cookies_str:
                                logger.info(f"【{self.cookie_id}】滑块验证成功，准备重启实例...")

                                # 更新风控日志为成功状态
                                if 'log_id' in locals() and log_id:
                                    self._update_risk_log(
                                        log_id,
                                        session_id=captcha_session_id,
                                        trigger_scene=captcha_trigger_scene,
                                        result_code='slider_captcha_success',
                                        captcha_engine=getattr(self, 'last_slider_captcha_engine', None),
                                        processing_result='滑块验证成功，已获取新Cookie',
                                        processing_status='success',
                                        duration_ms=max(0, int(captcha_duration * 1000)),
                                        event_meta=self._build_risk_event_meta(
                                            trigger_scene=captcha_trigger_scene,
                                            verification_url=verification_url,
                                            extra={
                                                'cookie_id': self.cookie_id,
                                                'cookie_length': len(new_cookies_str),
                                                'captcha_engine': getattr(self, 'last_slider_captcha_engine', None),
                                            },
                                        ),
                                    )

                                # 重启实例（cookies已在_handle_captcha_verification中更新到数据库）
                                # await self._restart_instance()

                                # 给浏览器回写票据与数据库落盘留一个稳定窗口，避免刚过块就立即重新命中Session过期
                                settle_delay = random.uniform(*self.post_slider_token_retry_delay)
                                logger.info(
                                    f"【{self.cookie_id}】滑块成功后进入稳定窗口 {settle_delay:.2f}s，再重新尝试Token刷新"
                                )
                                await asyncio.sleep(settle_delay)
                                self._reload_latest_cookies_from_db("滑块成功后的稳定窗口")
                                log_captcha_event(
                                    self.cookie_id,
                                    "滑块成功后重新进入Token刷新",
                                    None,
                                    f"类型: token_reentry_after_slider_success, captcha_retry_count={captcha_retry_count + 1}"
                                )

                                # 重新尝试刷新token（递归调用，但有深度限制）
                                return await self._refresh_token_impl(
                                    captcha_retry_count + 1,
                                    post_slider_session_grace_used=False,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=manual_refresh_browser_stabilization_used,
                                    post_slider_session_retry_count=0,
                                )
                            else:
                                logger.error(f"【{self.cookie_id}】滑块验证失败")
                                XianyuLive.set_password_login_failure_backoff(self.cookie_id, 'slider_failed', 600)
                                self.last_token_refresh_error_message = "滑块验证失败，未获取到新Cookie"
                                logger.warning(f"【{self.cookie_id}】已进入滑块失败退避期: slider_failed, 600秒")

                                # 更新风控日志为失败状态
                                if 'log_id' in locals() and log_id:
                                    self._update_risk_log(
                                        log_id,
                                        session_id=captcha_session_id,
                                        trigger_scene=captcha_trigger_scene,
                                        result_code='slider_captcha_failed',
                                        captcha_engine=getattr(self, 'last_slider_captcha_engine', None),
                                        processing_result=getattr(self, 'last_slider_result_message', None) or '滑块验证失败，未获取到新Cookie',
                                        processing_status='failed',
                                        error_message=getattr(self, 'last_slider_result_message', None) or '未获取到新Cookie',
                                        duration_ms=max(0, int(captcha_duration * 1000)),
                                        event_meta=self._build_risk_event_meta(
                                            trigger_scene=captcha_trigger_scene,
                                            verification_url=verification_url,
                                            extra={
                                                'cookie_id': self.cookie_id,
                                                'captcha_engine': getattr(self, 'last_slider_captcha_engine', None),
                                                'failure_message': getattr(self, 'last_slider_result_message', None),
                                            },
                                        ),
                                    )
                                
                                # 标记已处理，避免后续再发送通用失败通知
                                notification_sent = True
                        except Exception as captcha_e:
                            logger.error(f"【{self.cookie_id}】滑块验证处理异常: {self._safe_str(captcha_e)}")
                            self._clear_pending_slider_success_notice("滑块验证处理异常")
                            XianyuLive.set_password_login_failure_backoff(self.cookie_id, 'slider_failed', 600)
                            self.last_token_refresh_error_message = self._safe_str(captcha_e)
                            logger.warning(f"【{self.cookie_id}】滑块验证异常后进入退避期: slider_failed, 600秒")

                            # 更新风控日志为异常状态
                            captcha_duration = time.time() - captcha_start_time if 'captcha_start_time' in locals() else 0
                            if 'log_id' in locals() and log_id:
                                self._update_risk_log(
                                    log_id,
                                    session_id=captcha_session_id,
                                    trigger_scene=captcha_trigger_scene,
                                    result_code='slider_captcha_exception',
                                    processing_result='滑块验证处理异常',
                                    processing_status='failed',
                                    error_message=str(captcha_e)[:200],
                                    duration_ms=max(0, int(captcha_duration * 1000)),
                                    event_meta=self._build_risk_event_meta(
                                        trigger_scene=captcha_trigger_scene,
                                        verification_url=verification_url,
                                        extra={'cookie_id': self.cookie_id},
                                    ),
                                )
                            
                            # 标记已处理，避免后续再发送通用失败通知
                            notification_sent = True

                    # 检查是否包含"令牌过期"或"Session过期"
                    if isinstance(res_json, dict):
                        res_json_str = json.dumps(res_json, ensure_ascii=False, separators=(',', ':'))
                        if '令牌过期' in res_json_str or 'Session过期' in res_json_str:
                            # 记录令牌/Session过期到风控日志
                            token_expired_log_id = None
                            token_expired_session_id = self._new_risk_session_id('token')
                            token_expired_started_at = time.time()
                            token_trigger_scene = 'token_refresh'
                            expire_type = '令牌过期' if '令牌过期' in res_json_str else 'Session过期'
                            try:
                                from db_manager import db_manager
                                stale_count = db_manager.mark_stale_risk_control_logs_failed(timeout_minutes=15, cookie_id=self.cookie_id)
                                if stale_count > 0:
                                    logger.warning(f"【{self.cookie_id}】检测到{stale_count}条超时processing风控日志，已自动标记failed")
                                token_expired_log_id = self._create_risk_log(
                                    event_type='token_expired',
                                    session_id=token_expired_session_id,
                                    trigger_scene=token_trigger_scene,
                                    result_code='token_expired_detected',
                                    event_description=f"检测到{expire_type}",
                                    processing_status='processing',
                                    event_meta=self._build_risk_event_meta(
                                        trigger_scene=token_trigger_scene,
                                        extra={'expire_type': expire_type, 'cookie_id': self.cookie_id},
                                    ),
                                )
                            except Exception as log_e:
                                logger.error(f"【{self.cookie_id}】记录风控日志失败: {log_e}")

                            # 调用统一的密码登录刷新方法
                            if self.is_manual_refresh_active(self.cookie_id, allow_handoff_recovery=True):
                                logger.warning(f"【{self.cookie_id}】检测到手动刷新进行中，跳过自动密码登录刷新")
                                if token_expired_log_id:
                                    self._update_risk_log(
                                        token_expired_log_id,
                                        session_id=token_expired_session_id,
                                        trigger_scene=token_trigger_scene,
                                        result_code='manual_refresh_active',
                                        processing_status='failed',
                                        error_message='检测到手动刷新进行中，自动刷新已跳过',
                                        duration_ms=max(0, int((time.time() - token_expired_started_at) * 1000)),
                                        event_meta=self._build_risk_event_meta(
                                            trigger_scene=token_trigger_scene,
                                            extra={'cookie_id': self.cookie_id, 'expire_type': expire_type},
                                        ),
                                    )
                                self.last_token_refresh_status = "manual_refresh_active"
                                self._clear_pending_slider_success_notice("手动刷新进行中")
                                notification_sent = True
                                return None

                            recent_slider_success = self._has_recent_slider_success()
                            max_post_slider_session_retries = max(
                                0,
                                int(RISK_CONTROL.get('max_post_slider_session_retries', 1) or 1),
                            )

                            if recent_slider_success and not post_slider_session_grace_used:
                                grace_delay = random.uniform(*self.post_slider_token_retry_delay)
                                logger.warning(
                                    f"【{self.cookie_id}】检测到最近 {self.slider_success_reentry_window}s 内刚通过滑块，"
                                    f"先等待 {grace_delay:.2f}s 并重载Cookie后再试一次Token刷新"
                                )
                                log_captcha_event(
                                    self.cookie_id,
                                    "滑块成功后Session过期，优先重试Token刷新",
                                    None,
                                    f"类型: token_retry_after_recent_slider_success, expire_type={expire_type}"
                                )
                                await asyncio.sleep(grace_delay)
                                self._reload_latest_cookies_from_db("滑块成功后的Session过期缓冲")
                                return await self._refresh_token_impl(
                                    captcha_retry_count,
                                    post_slider_session_grace_used=True,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=manual_refresh_browser_stabilization_used,
                                    post_slider_session_retry_count=post_slider_session_retry_count,
                                )

                            if (
                                recent_slider_success and
                                not allow_password_login_recovery and
                                post_slider_session_retry_count < max_post_slider_session_retries
                            ):
                                settle_retry_attempt = post_slider_session_retry_count + 1
                                settle_delay = random.uniform(*self.post_slider_token_retry_delay) + ((settle_retry_attempt - 1) * 1.2)
                                logger.warning(
                                    f"【{self.cookie_id}】预检模式下滑块成功后仍返回{expire_type}，"
                                    f"执行第{settle_retry_attempt}/{max_post_slider_session_retries}次稳定重试，"
                                    f"等待 {settle_delay:.2f}s 后再次尝试Token刷新"
                                )
                                log_captcha_event(
                                    self.cookie_id,
                                    "滑块成功后Session仍未稳定，继续重试Token刷新",
                                    None,
                                    f"类型: token_settle_retry_after_slider, expire_type={expire_type}, "
                                    f"attempt={settle_retry_attempt}/{max_post_slider_session_retries}"
                                )
                                self.last_token_refresh_status = "post_slider_session_settling"
                                await asyncio.sleep(settle_delay)
                                self._reload_latest_cookies_from_db(
                                    f"滑块成功后的第{settle_retry_attempt}次Session稳定重试"
                                )
                                return await self._refresh_token_impl(
                                    captcha_retry_count,
                                    post_slider_session_grace_used=True,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=manual_refresh_browser_stabilization_used,
                                    post_slider_session_retry_count=settle_retry_attempt,
                                )

                            refresh_success = False
                            if allow_password_login_recovery:
                                refresh_success = await self._try_password_login_refresh(
                                    "令牌/Session过期",
                                    risk_session_id=token_expired_session_id,
                                    trigger_scene=token_trigger_scene,
                                    ignore_slider_failed_backoff=recent_slider_success,
                                )
                            else:
                                self.last_token_refresh_status = (
                                    "session_expired_after_slider"
                                    if recent_slider_success else
                                    "session_expired_preflight"
                                )
                                self.last_token_refresh_error_message = f"Token预检返回{expire_type}"
                                logger.warning(f"【{self.cookie_id}】当前为预检模式，跳过密码登录恢复，直接返回Token刷新失败")
                            
                            if token_expired_log_id:
                                self._update_risk_log(
                                    token_expired_log_id,
                                    session_id=token_expired_session_id,
                                    trigger_scene=token_trigger_scene,
                                    result_code='token_refresh_recovered' if refresh_success else 'token_refresh_recovery_failed',
                                    processing_status='success' if refresh_success else 'failed',
                                    processing_result='令牌/Session过期触发自动刷新成功，已进入重试流程' if refresh_success else None,
                                    error_message=None if refresh_success else '令牌/Session过期触发自动刷新失败',
                                    duration_ms=max(0, int((time.time() - token_expired_started_at) * 1000)),
                                    event_meta=self._build_risk_event_meta(
                                        trigger_scene=token_trigger_scene,
                                        extra={'cookie_id': self.cookie_id, 'expire_type': expire_type},
                                    ),
                                )
                            
                            if not refresh_success:
                                if allow_password_login_recovery and not self._is_account_pause_status(self.last_token_refresh_status):
                                    self.last_token_refresh_status = "token_expired_recovery_failed"
                                self._clear_pending_slider_success_notice("恢复流程失败")
                                # 标记已发送通知，避免重复通知
                                notification_sent = True
                                # 返回None，让调用者知道刷新失败
                                return None
                            else:
                                # 刷新成功后，重新尝试获取token
                                return await self._refresh_token_impl(
                                    captcha_retry_count,
                                    post_slider_session_grace_used=False,
                                    allow_password_login_recovery=allow_password_login_recovery,
                                    manual_refresh_browser_stabilization_used=manual_refresh_browser_stabilization_used,
                                    post_slider_session_retry_count=0,
                                )
                                
                                # 刷新失败时继续执行原有的失败处理逻辑

                    if self.last_token_refresh_status in (None, "started"):
                        self.last_token_refresh_status = "token_refresh_failed"
                    self.last_token_refresh_error_message = json.dumps(res_json, ensure_ascii=False, separators=(',', ':'))
                    self._clear_pending_slider_success_notice("Token刷新最终失败")
                    logger.error(f"【{self.cookie_id}】Token刷新失败: {res_json}")

                    # 清空当前token，确保下次重试时重新获取
                    self.current_token = None

                    # 只有在没有发送过通知的情况下才发送Token刷新失败通知
                    # 并且WebSocket未连接时才发送（已连接说明只是暂时失败）
                    if not notification_sent:
                        # 检查WebSocket连接状态
                        is_ws_connected = (
                            self.connection_state == ConnectionState.CONNECTED and 
                            self.ws and 
                            not self.ws.closed
                        )
                        
                        if is_ws_connected:
                            logger.info(f"【{self.cookie_id}】WebSocket连接正常，Token刷新失败可能是暂时的，跳过失败通知")
                        else:
                            logger.warning(f"【{self.cookie_id}】WebSocket未连接，发送Token刷新失败通知")
                            await self.send_token_refresh_notification(f"Token刷新失败: {res_json}", "token_refresh_failed")
                    else:
                        logger.info(f"【{self.cookie_id}】已发送滑块验证相关通知，跳过Token刷新失败通知")
                    return None

        except Exception as e:
            self.last_token_refresh_status = "token_refresh_exception"
            self.last_token_refresh_error_message = self._safe_str(e)
            self._clear_pending_slider_success_notice("Token刷新异常")
            logger.error(f"Token刷新异常: {self._safe_str(e)}")

            # 清空当前token，确保下次重试时重新获取
            self.current_token = None

            # 只有在没有发送过通知的情况下才发送Token刷新异常通知
            # 并且WebSocket未连接时才发送（已连接说明只是暂时失败）
            if not notification_sent:
                # 检查WebSocket连接状态
                is_ws_connected = (
                    self.connection_state == ConnectionState.CONNECTED and 
                    self.ws and 
                    not self.ws.closed
                )
                
                if is_ws_connected:
                    logger.info(f"【{self.cookie_id}】WebSocket连接正常，Token刷新异常可能是暂时的，跳过失败通知")
                else:
                    logger.warning(f"【{self.cookie_id}】WebSocket未连接，发送Token刷新异常通知")
                    await self.send_token_refresh_notification(f"Token刷新异常: {str(e)}", "token_refresh_exception")
            else:
                logger.info(f"【{self.cookie_id}】已发送滑块验证相关通知，跳过Token刷新异常通知")
            return None

    def _need_captcha_verification(self, res_json: dict) -> bool:
        """检查响应是否需要滑块验证"""
        try:
            if not isinstance(res_json, dict):
                return False

            # 记录res_json内容到日志文件
            import json
            res_json_str = json.dumps(res_json, ensure_ascii=False, separators=(',', ':'))
            log_captcha_event(self.cookie_id, "检查滑块验证响应", None, f"res_json内容: {res_json_str}")

            # 检查返回的错误信息
            ret_value = res_json.get('ret', [])
            if not ret_value:
                return False

            # 检查是否包含需要验证的关键词
            captcha_keywords = [
                'FAIL_SYS_USER_VALIDATE',  # 用户验证失败
                'RGV587_ERROR',            # 风控错误
                '哎哟喂,被挤爆啦',          # 被挤爆了
                '哎哟喂，被挤爆啦',         # 被挤爆了（中文逗号）
                '挤爆了',                  # 挤爆了
                '请稍后重试',              # 请稍后重试
                'punish?x5secdata',        # 惩罚页面
                'captcha',                 # 验证码
            ]

            error_msg = str(ret_value[0]) if ret_value else ''

            # 检查错误信息是否包含需要验证的关键词
            for keyword in captcha_keywords:
                if keyword in error_msg:
                    logger.info(f"【{self.cookie_id}】检测到需要滑块验证的关键词: {keyword}")
                    return True

            # 检查data字段中是否包含验证URL
            data = res_json.get('data', {})
            if isinstance(data, dict) and 'url' in data:
                url = data.get('url', '')
                if 'punish' in url or 'captcha' in url or 'validate' in url:
                    logger.info(f"【{self.cookie_id}】检测到验证URL: {url}")
                    return True

            return False

        except Exception as e:
            logger.error(f"【{self.cookie_id}】检查是否需要滑块验证时出错: {self._safe_str(e)}")
            return False

    async def _handle_captcha_verification(self, res_json: dict) -> str:
        """处理滑块验证，返回新的cookies字符串"""
        try:
            logger.info(f"【{self.cookie_id}】开始处理滑块验证...")

            if self.is_manual_refresh_active(self.cookie_id, allow_handoff_recovery=True):
                logger.warning(f"【{self.cookie_id}】手动刷新进行中，取消自动滑块处理")
                log_captcha_event(
                    self.cookie_id,
                    "手动刷新进行中，取消自动滑块处理",
                    None,
                    "自动滑块处理已跳过"
                )
                return None

            # 获取验证URL
            verification_url = None

            # 从data字段获取URL
            data = res_json.get('data', {})
            if isinstance(data, dict) and 'url' in data:
                verification_url = data.get('url')

            # 如果没有找到URL，使用默认的验证页面
            if not verification_url:
                logger.info(f"【{self.cookie_id}】未找到验证URL，认为不需要滑块验证，返回正常")
                return None

            logger.info(f"【{self.cookie_id}】验证URL: {verification_url}")

            # 使用滑块验证器（独立实例，解决并发冲突）
            try:
                # 使用集成的滑块验证方法（无需猴子补丁）
                from utils.xianyu_slider_stealth import XianyuSliderStealth
                from utils.slider_orchestrator import run_slider_async_with_fallback
                logger.info(f"【{self.cookie_id}】XianyuSliderStealth导入成功，使用滑块验证")

                # 读取账号配置以决定浏览器模式（默认无头）
                account_info = db_manager.get_cookie_details(self.cookie_id) or {}
                show_browser = bool(account_info.get('show_browser', False))
                # 创建独立的滑块验证实例（每个用户独立实例，避免并发冲突）
                slider_stealth = XianyuSliderStealth(
                    user_id=f"{self.cookie_id}",  # 使用唯一ID避免冲突
                    enable_learning=True,  # 启用学习功能
                    headless=not show_browser,
                    initial_cookies=self.cookies_str,
                    proxy=self.proxy_config,
                    use_account_persistent_profile=True,
                )
                # 给当前滑块实例打上 token_refresh 场景标，让滑块层在硬拒绝时尽早交还给外层走账密恢复
                slider_stealth.risk_trigger_scene = 'token_refresh'

                # 直接使用异步方法执行滑块验证（避免 ThreadPoolExecutor 导致的 Playwright 初始化问题）
                strict_result = await run_slider_async_with_fallback(slider_stealth, verification_url, engine="playwright")
                self.last_slider_captcha_engine = strict_result.engine
                self.last_slider_result_message = strict_result.message

                if strict_result.success and strict_result.cookies:
                    cookies = strict_result.cookies
                    logger.info(f"【{self.cookie_id}】滑块验证成功，获取到新的x5相关cookies")

                    current_cookies_dict = trans_cookies(self.cookies_str)
                    x5sec_cookies = strict_result.x5_cookies

                    logger.info(f"【{self.cookie_id}】找到{len(x5sec_cookies)}个x5相关cookies: {list(x5sec_cookies.keys())}")

                    merge_result = self.protected_merge_cookie_dicts(current_cookies_dict, cookies)
                    updated_cookies = merge_result['merged_cookies_dict']
                    updated_fields = merge_result['updated_fields']
                    changed_fields = merge_result['changed_fields']
                    new_fields = merge_result['new_fields']
                    removed_fields = merge_result['removed_fields']
                    preserved_fields = merge_result['preserved_fields']
                    preserved_protected_fields = merge_result['preserved_protected_fields']
                    would_remove_fields = merge_result['would_remove_fields']
                    missing_protected_fields = merge_result['missing_protected_fields']
                    missing_required_fields = merge_result['missing_required_fields']
                    incoming_missing_protected_fields = merge_result['incoming_missing_protected_fields']
                    account_switched = merge_result['account_switched']
                    cookies_str = "; ".join([f"{k}={v}" for k, v in updated_cookies.items()])
                    qr_login_grace = self.get_qr_login_grace(self.cookie_id)
                    merge_event_name = "slider_post_qr_protected_merge" if qr_login_grace else "captcha_protected_merge"
                    self._log_protected_merge_event(merge_event_name, merge_result)

                    self._log_cookie_merge_summary(
                        updated_cookies,
                        updated_fields,
                        changed_fields,
                        new_fields,
                        context="滑块验证成功后Cookie合并",
                        preserved_fields=preserved_fields,
                        preserved_protected_fields=preserved_protected_fields,
                        would_remove_fields=would_remove_fields,
                        removed_fields=removed_fields,
                        missing_protected_fields=missing_protected_fields,
                        missing_required_fields=missing_required_fields,
                        incoming_missing_protected_fields=incoming_missing_protected_fields,
                        account_switched=account_switched,
                    )

                    if missing_required_fields:
                        logger.error(f"【{self.cookie_id}】滑块验证后的Cookie仍缺失核心字段，放弃写回数据库: {', '.join(missing_required_fields)}")
                        return None

                    # 自动更新数据库中的cookie
                    try:
                        # 备份原有cookies
                        old_cookies_str = self.cookies_str
                        old_cookies_dict = self.cookies.copy()

                        # 更新当前实例的cookies（使用合并后的cookies）
                        self._set_runtime_cookie_state(
                            cookies_str=cookies_str,
                            cookies_dict=updated_cookies,
                            source="slider_success",
                        )

                        # 更新数据库中的cookies
                        await self.update_config_cookies()
                        logger.info(f"【{self.cookie_id}】滑块验证成功后，数据库cookies已自动更新")
                        self._mark_slider_success_recovery(cookies_str)
                        self._mark_pending_slider_success_notice("token_refresh")
                        XianyuLive.clear_password_login_failure_backoff(self.cookie_id)
                        logger.info(f"【{self.cookie_id}】滑块验证成功后，已清理密码登录失败退避状态")

                        # 记录成功更新到日志文件，包含关键字段变化和x5相关cookie信息
                        x5sec_cookies_str = "; ".join([f"{k}={v}" for k, v in x5sec_cookies.items()]) if x5sec_cookies else "无"
                        log_captcha_event(self.cookie_id, "滑块验证成功并自动更新数据库", True,
                            f"原有{len(current_cookies_dict)}个cookie项, 浏览器快照{len(cookies)}个, 合并后{len(updated_cookies)}个, 变更字段{len(changed_fields)}个, 新增字段{len(new_fields)}个, 保护保留{len(preserved_protected_fields)}个, 实际移除{len(removed_fields)}个, x5 cookies: {x5sec_cookies_str}")

                    except Exception as update_e:
                        logger.error(f"【{self.cookie_id}】自动更新数据库cookies失败: {self._safe_str(update_e)}")

                        # 回滚cookies
                        self._set_runtime_cookie_state(
                            cookies_str=old_cookies_str,
                            cookies_dict=old_cookies_dict,
                            source="slider_success_rollback",
                        )

                        # 记录更新失败到日志文件，包含获取到的x5 cookies
                        x5sec_cookies_str = "; ".join([f"{k}={v}" for k, v in x5sec_cookies.items()]) if x5sec_cookies else "无"
                        log_captcha_event(self.cookie_id, "滑块验证成功但数据库更新失败", False,
                            f"更新异常: {self._safe_str(update_e)[:100]}, 变更字段{len(changed_fields)}个, 新增字段{len(new_fields)}个, 保护保留{len(preserved_protected_fields)}个, 获取到的x5 cookies: {x5sec_cookies_str}")

                        # 发送更新失败通知
                        await self.send_token_refresh_notification(
                            f"滑块验证成功但数据库更新失败: {self._safe_str(update_e)}",
                            "captcha_success_db_update_failed"
                        )

                        return None

                    return cookies_str
                else:
                    failure_message = getattr(self, 'last_slider_result_message', None) or "滑块验证失败"
                    logger.error(f"【{self.cookie_id}】{failure_message}")

                    # 记录滑块验证失败到日志文件
                    log_captcha_event(self.cookie_id, failure_message, False,
                        f"engine={getattr(self, 'last_slider_captcha_engine', 'playwright')}, 环境: {'Docker' if os.getenv('DOCKER_ENV') else '本地'}")

                    # 发送通知（检查WebSocket连接状态）
                    # 只有在WebSocket未连接时才发送通知，已连接说明可能是暂时性问题
                    is_ws_connected = (
                        self.connection_state == ConnectionState.CONNECTED and 
                        self.ws and 
                        not self.ws.closed
                    )
                    
                    if is_ws_connected:
                        logger.info(f"【{self.cookie_id}】WebSocket连接正常，滑块验证失败可能是暂时的，跳过通知")
                    else:
                        logger.warning(f"【{self.cookie_id}】WebSocket未连接，发送滑块验证失败通知")
                        await self.send_token_refresh_notification(
                            f"滑块验证失败，需要手动处理。验证URL: {verification_url}",
                            "captcha_verification_failed"
                        )
                    return None

            except ImportError as import_e:
                logger.error(f"【{self.cookie_id}】XianyuSliderStealth导入失败: {import_e}")
                logger.error(f"【{self.cookie_id}】请安装Playwright库: pip install playwright")

                # 记录导入失败到日志文件
                log_captcha_event(self.cookie_id, "XianyuSliderStealth导入失败", False,
                    f"Playwright未安装, 错误: {import_e}")

                # 发送通知
                await self.send_token_refresh_notification(
                    f"滑块验证功能不可用，请安装Playwright。验证URL: {verification_url}",
                    "captcha_dependency_missing"
                )
                return None

            except Exception as stealth_e:
                logger.error(f"【{self.cookie_id}】滑块验证异常: {self._safe_str(stealth_e)}")

                # 记录异常到日志文件
                log_captcha_event(self.cookie_id, "滑块验证异常", False,
                    f"执行异常, 错误: {self._safe_str(stealth_e)[:100]}")

                # 发送通知（检查WebSocket连接状态）
                # 只有在WebSocket未连接时才发送通知，已连接说明可能是暂时性问题
                is_ws_connected = (
                    self.connection_state == ConnectionState.CONNECTED and 
                    self.ws and 
                    not self.ws.closed
                )
                
                if is_ws_connected:
                    logger.info(f"【{self.cookie_id}】WebSocket连接正常，滑块验证执行异常可能是暂时的，跳过通知")
                else:
                    logger.warning(f"【{self.cookie_id}】WebSocket未连接，发送滑块验证执行异常通知")
                    await self.send_token_refresh_notification(
                        f"滑块验证执行异常，需要手动处理。验证URL: {verification_url}",
                        "captcha_execution_error"
                    )
                return None



        except Exception as e:
            logger.error(f"【{self.cookie_id}】处理滑块验证时出错: {self._safe_str(e)}")
            return None

    async def _update_cookies_and_restart(self, new_cookies_str: str):
        """更新cookies并重启任务"""
        try:
            logger.info(f"【{self.cookie_id}】开始更新cookies并重启任务...")

            # 验证新cookies的有效性
            if not new_cookies_str or not new_cookies_str.strip():
                logger.error(f"【{self.cookie_id}】新cookies为空，无法更新")
                return False

            # 解析新cookies，确保格式正确
            try:
                new_cookies_dict = trans_cookies(new_cookies_str)
                if not new_cookies_dict:
                    logger.error(f"【{self.cookie_id}】新cookies解析失败，无法更新")
                    return False
                logger.info(f"【{self.cookie_id}】新cookies解析成功，包含 {len(new_cookies_dict)} 个字段")
            except Exception as parse_e:
                logger.error(f"【{self.cookie_id}】新cookies解析异常: {self._safe_str(parse_e)}")
                return False

            # 合并cookies：保留原有cookies，只更新新获取到的字段
            try:
                merge_result = self.protected_merge_cookie_dicts(trans_cookies(self.cookies_str), new_cookies_dict)
                merged_cookies_dict = merge_result['merged_cookies_dict']
                updated_fields = merge_result['updated_fields']
                changed_fields = merge_result['changed_fields']
                new_fields = merge_result['new_fields']
                self._log_protected_merge_event("password_refresh_protected_merge", merge_result)

                self._log_cookie_merge_summary(
                    merged_cookies_dict,
                    updated_fields,
                    changed_fields,
                    new_fields,
                    context="密码登录刷新Cookie",
                    preserved_fields=merge_result['preserved_fields'],
                    preserved_protected_fields=merge_result['preserved_protected_fields'],
                    would_remove_fields=merge_result['would_remove_fields'],
                    removed_fields=merge_result['removed_fields'],
                    missing_protected_fields=merge_result['missing_protected_fields'],
                    missing_required_fields=merge_result['missing_required_fields'],
                    incoming_missing_protected_fields=merge_result['incoming_missing_protected_fields'],
                    account_switched=merge_result['account_switched'],
                )

                if merge_result['missing_required_fields']:
                    logger.error(
                        f"【{self.cookie_id}】密码登录刷新后的Cookie仍缺失核心字段，放弃写回并重启: {', '.join(merge_result['missing_required_fields'])}"
                    )
                    return False

                # 使用合并后的cookies字符串
                new_cookies_str = '; '.join([f"{k}={v}" for k, v in merged_cookies_dict.items()])
                new_cookies_dict = merged_cookies_dict

            except Exception as merge_e:
                logger.error(f"【{self.cookie_id}】cookies合并异常: {self._safe_str(merge_e)}")
                logger.warning(f"【{self.cookie_id}】将使用原始新cookies（不合并）")
                # 如果合并失败，继续使用原始的new_cookies_str

            # 备份原有cookies，以防更新失败需要回滚
            old_cookies_str = self.cookies_str
            old_cookies_dict = self.cookies.copy()

            try:
                # 更新当前实例的cookies
                self._set_runtime_cookie_state(
                    cookies_str=new_cookies_str,
                    cookies_dict=new_cookies_dict,
                    source="password_login_refresh",
                )

                # 认证成功后的轻量 Token 软预检：只用于观测/预热，不阻断原有更新与重启流程
                await self.soft_preflight_token_after_auth(
                    new_cookies_str,
                    source="password_login_refresh",
                )

                # 更新数据库中的cookies
                await self.update_config_cookies()
                logger.info(f"【{self.cookie_id}】数据库cookies更新成功")

                # ⚠️ 在重启前完成所有需要的操作（如发送通知）
                # 因为重启触发后2秒内任务会被取消，不能再执行任何async操作
                logger.info(f"【{self.cookie_id}】cookies更新成功，准备重启任务...")
                
                # 通过CookieManager重启任务
                logger.info(f"【{self.cookie_id}】通过CookieManager触发重启...")
                await self._restart_instance()
                
                # ⚠️ _restart_instance() 已触发重启，当前任务即将被取消
                # 立即返回，不执行任何后续代码（包括发送通知）
                logger.info(f"【{self.cookie_id}】重启请求已触发，等待任务被取消...")
                return True

            except Exception as update_e:
                logger.error(f"【{self.cookie_id}】更新cookies过程中出错，尝试回滚: {self._safe_str(update_e)}")

                # 回滚cookies
                try:
                    self._set_runtime_cookie_state(
                        cookies_str=old_cookies_str,
                        cookies_dict=old_cookies_dict,
                        source="password_login_refresh_rollback",
                    )
                    await self.update_config_cookies()
                    logger.info(f"【{self.cookie_id}】cookies已回滚到原始状态")
                except Exception as rollback_e:
                    logger.error(f"【{self.cookie_id}】cookies回滚失败: {self._safe_str(rollback_e)}")

                return False

        except Exception as e:
            logger.error(f"【{self.cookie_id}】更新cookies并重启任务时出错: {self._safe_str(e)}")
            return False

    async def update_config_cookies(self):
        """更新数据库中的cookies（不会覆盖账号密码等其他字段）"""
        try:
            from db_manager import db_manager

            # 更新数据库中的Cookie
            if hasattr(self, 'cookie_id') and self.cookie_id:
                try:
                    # 获取当前Cookie的用户ID，避免在刷新时改变所有者
                    current_user_id = None
                    if hasattr(self, 'user_id') and self.user_id:
                        current_user_id = self.user_id

                    # 使用 update_cookie_account_info 避免覆盖其他字段（如 username, password, pause_duration, remark 等）
                    # 这个方法会自动处理新账号和现有账号的情况，不会覆盖账号密码
                    success = db_manager.update_cookie_account_info(
                        self.cookie_id, 
                        cookie_value=self.cookies_str,
                        user_id=current_user_id  # 如果是新账号，需要提供user_id
                    )
                    if not success:
                        # 如果更新失败，记录错误但不使用 save_cookie（避免覆盖账号密码）
                        logger.warning(f"更新Cookie到数据库失败: {self.cookie_id}，但不使用save_cookie避免覆盖账号密码")
                    else:
                        logger.warning(f"已更新Cookie到数据库: {self.cookie_id}")
                except Exception as e:
                    logger.error(f"更新数据库Cookie失败: {self._safe_str(e)}")
                    # 发送数据库更新失败通知
                    await self.send_token_refresh_notification(f"数据库Cookie更新失败: {str(e)}", "db_update_failed")
            else:
                logger.warning("Cookie ID不存在，无法更新数据库")
                # 发送Cookie ID缺失通知
                await self.send_token_refresh_notification("Cookie ID不存在，无法更新数据库", "cookie_id_missing")

        except Exception as e:
            logger.error(f"更新Cookie失败: {self._safe_str(e)}")
            # 发送Cookie更新失败通知
            await self.send_token_refresh_notification(f"Cookie更新失败: {str(e)}", "cookie_update_failed")

    async def _try_password_login_refresh(
        self,
        trigger_reason: str = "令牌/Session过期",
        risk_session_id: Optional[str] = None,
        trigger_scene: Optional[str] = None,
        ignore_slider_failed_backoff: bool = False,
    ):
        """尝试通过密码登录刷新Cookie并重启实例
        
        Args:
            trigger_reason: 触发原因，用于日志记录
            
        Returns:
            bool: 是否成功刷新Cookie
        """
        logger.warning(f"【{self.cookie_id}】检测到{trigger_reason}，准备刷新Cookie并重启实例...")
        trigger_scene = trigger_scene or self._normalize_risk_trigger_scene(trigger_reason, default='auto_cookie_refresh')
        risk_session_id = risk_session_id or self._new_risk_session_id('cookie')
        risk_log_started_at = time.time()
        base_event_meta = {'cookie_id': self.cookie_id, 'trigger_reason': trigger_reason}

        # 记录到风控日志
        refresh_risk_log_id = None
        try:
            stale_count = db_manager.mark_stale_risk_control_logs_failed(timeout_minutes=15, cookie_id=self.cookie_id)
            if stale_count > 0:
                logger.warning(f"【{self.cookie_id}】检测到{stale_count}条超时processing风控日志，已自动标记failed")
            refresh_risk_log_id = self._create_risk_log(
                event_type='cookie_refresh',
                session_id=risk_session_id,
                trigger_scene=trigger_scene,
                result_code='cookie_refresh_started',
                event_description=f"{trigger_reason}触发Cookie刷新",
                processing_status='processing',
                event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
            )
        except Exception as log_e:
            logger.error(f"【{self.cookie_id}】记录风控日志失败: {log_e}")

        if self.is_manual_refresh_active(self.cookie_id, allow_handoff_recovery=True):
            logger.warning(f"【{self.cookie_id}】手动刷新进行中，跳过自动密码登录刷新")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='manual_refresh_active',
                    processing_status='failed',
                    error_message='手动刷新进行中，自动密码登录刷新已跳过',
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False

        if self._is_account_pause_status(getattr(self, 'last_token_refresh_status', None)):
            logger.warning(f"【{self.cookie_id}】账号处于人工验证/风控暂停状态，跳过自动密码登录刷新")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='account_pause_active',
                    processing_status='failed',
                    error_message='账号处于人工验证/风控暂停状态，自动密码登录刷新已跳过',
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False

        if self._should_defer_auth_recovery_for_qr_grace():
            logger.warning(f"【{self.cookie_id}】扫码登录稳定期内，跳过自动密码登录刷新")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='qr_login_grace_active',
                    processing_status='failed',
                    error_message=self.last_token_refresh_error_message or '扫码登录稳定期内，自动密码登录刷新已跳过',
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False

        recovery_lock_owner = f"{self.cookie_id}:{trigger_scene or 'auto_cookie_refresh'}:{int(time.time() * 1000)}"
        recovery_lock_acquired = False

        # 检查是否在密码登录冷却期内，避免重复登录
        current_time = time.time()
        failure_backoff = self._get_active_password_login_failure_backoff(current_time)
        if failure_backoff:
            backoff_reason = failure_backoff.get('reason', 'unknown')
            remaining_time = failure_backoff.get('remaining_time', 0.0)
            if backoff_reason == 'slider_failed' and ignore_slider_failed_backoff:
                logger.warning(
                    f"【{self.cookie_id}】检测到最近刚通过滑块，忽略一次旧的 slider_failed 退避并继续尝试密码登录刷新"
                )
                XianyuLive.clear_password_login_failure_backoff(self.cookie_id)
                failure_backoff = None
            else:
                logger.warning(
                    f"【{self.cookie_id}】密码登录失败退避中（原因: {backoff_reason}），还需等待 {remaining_time:.1f} 秒"
                )
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='password_login_backoff',
                        processing_status='failed',
                        error_message=f"密码登录失败退避中，剩余{remaining_time:.1f}秒",
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(
                            trigger_scene=trigger_scene,
                            extra={**base_event_meta, 'backoff_reason': backoff_reason, 'backoff_seconds': failure_backoff.get('seconds')},
                        ),
                    )
                return False

        last_password_login = XianyuLive._last_password_login_time.get(self.cookie_id, 0)
        time_since_last_login = current_time - last_password_login
        
        if last_password_login > 0 and time_since_last_login < XianyuLive._password_login_cooldown:
            remaining_time = XianyuLive._password_login_cooldown - time_since_last_login
            logger.warning(f"【{self.cookie_id}】距离上次密码登录仅 {time_since_last_login:.1f} 秒，仍在冷却期内（还需等待 {remaining_time:.1f} 秒），跳过密码登录")
            logger.warning(f"【{self.cookie_id}】提示：如果新Cookie仍然无效，请检查账号状态或手动更新Cookie")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='password_login_cooldown',
                    processing_status='failed',
                    error_message=f"密码登录冷却期内，剩余{remaining_time:.1f}秒",
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False

        recovery_lock_acquired, existing_lock = XianyuLive.acquire_auth_recovery_lock(
            self.cookie_id,
            recovery_lock_owner,
        )
        if not recovery_lock_acquired:
            existing_owner = (existing_lock or {}).get('owner', 'unknown')
            logger.warning(f"【{self.cookie_id}】认证恢复流程已在执行中，跳过本次重复触发: owner={existing_owner}")
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='auth_recovery_in_progress',
                    processing_status='failed',
                    error_message='已有认证恢复流程执行中',
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(
                        trigger_scene=trigger_scene,
                        extra={**base_event_meta, 'active_owner': existing_owner},
                    ),
                )
            return False

        # 记录到日志文件
        log_captcha_event(self.cookie_id, f"{trigger_reason}触发Cookie刷新和实例重启", None,
            f"检测到{trigger_reason}，准备刷新Cookie并重启实例")

        try:
            # 从数据库获取账号登录信息
            account_info = db_manager.get_cookie_details(self.cookie_id)

            if not account_info:
                logger.error(f"【{self.cookie_id}】无法获取账号信息")
                self.last_token_refresh_error_message = "无法获取账号信息"
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='account_info_missing',
                        processing_status='failed',
                        error_message='无法获取账号信息',
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                    )
                return False

            # 【重要】先检查数据库中的cookie是否已经更新
            # 如果用户已经手动更新了cookie，就不需要触发密码登录刷新
            db_cookie_value = account_info.get('cookie_value', '')
            if db_cookie_value and db_cookie_value != self.cookies_str:
                logger.info(f"【{self.cookie_id}】检测到数据库中的cookie已更新，重新加载cookie")
                self._set_runtime_cookie_state(cookies_str=db_cookie_value, source="db_cookie_reload_before_password_login")
                logger.info(f"【{self.cookie_id}】Cookie已从数据库重新加载，跳过密码登录刷新")
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='cookie_already_updated',
                        processing_status='success',
                        processing_result='检测到数据库Cookie已更新，自动刷新流程跳过',
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                    )
                return True
            
            username = account_info.get('username', '')
            password = account_info.get('password', '')
            show_browser = account_info.get('show_browser', False)
            
            # 检查是否配置了用户名和密码
            if not username or not password:
                logger.warning(f"【{self.cookie_id}】未配置用户名或密码，跳过密码登录刷新")
                self.last_token_refresh_error_message = "未配置用户名或密码，无法自动刷新Cookie"
                await self.send_token_refresh_notification(
                    f"检测到{trigger_reason}，但未配置用户名或密码，无法自动刷新Cookie",
                    "no_credentials"
                )
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='missing_credentials',
                        processing_status='failed',
                        error_message='未配置用户名或密码，无法自动刷新Cookie',
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                    )
                return False
            
            # 使用集成的 Playwright 登录方法（无需猴子补丁）
            from utils.xianyu_slider_stealth import XianyuSliderStealth
            browser_mode = "有头" if show_browser else "无头"
            logger.info(f"【{self.cookie_id}】开始使用{browser_mode}浏览器进行密码登录刷新Cookie...")
            logger.info(f"【{self.cookie_id}】使用账号: {username}")
            
            # 创建一个通知回调包装函数，支持接收截图路径和验证链接
            async def notification_callback_wrapper(
                message: str,
                screenshot_path: str = None,
                verification_url: str = None,
                verification_type: str = None,
            ):
                """通知回调包装函数，支持接收截图路径和验证链接"""
                verification_context = 'manual_cookie_refresh' if self.is_manual_refresh_active(self.cookie_id, allow_handoff_recovery=True) else 'auto_refresh'
                should_pause_account = self._should_pause_for_manual_verification(verification_type, verification_context)
                self.last_token_refresh_status = 'verification_pending_manual' if not should_pause_account else 'manual_verification_required'
                self.last_token_refresh_error_message = str(message or '').strip()
                pause_target_loop = None
                try:
                    from cookie_manager import manager as cookie_manager_manager
                    pause_target_loop = getattr(cookie_manager_manager, 'loop', None)
                except Exception:
                    pause_target_loop = None

                current_loop = None
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None

                if pause_target_loop and pause_target_loop.is_running() and pause_target_loop is not current_loop:
                    pause_future = asyncio.run_coroutine_threadsafe(
                        self._pause_account_for_manual_verification(
                            verification_type=verification_type,
                            error_message=message,
                            pause_account=should_pause_account,
                            verification_context=verification_context,
                            verification_url=verification_url or '',
                        ),
                        pause_target_loop,
                    )
                    try:
                        pause_future.result(timeout=10)
                    except Exception as pause_e:
                        logger.warning(f"【{self.cookie_id}】跨线程暂停人工验证账号失败: {self._safe_str(pause_e)}")
                else:
                    await self._pause_account_for_manual_verification(
                        verification_type=verification_type,
                        error_message=message,
                        pause_account=should_pause_account,
                        verification_context=verification_context,
                        verification_url=verification_url or '',
                    )

                await self.send_token_refresh_notification(
                    error_message=message,
                    notification_type="token_refresh",
                    chat_id=None,
                    attachment_path=screenshot_path,
                    verification_url=verification_url,
                    verification_type=verification_type,
                )
                if should_pause_account:
                    await self._request_stop_after_account_pause(
                        f"检测到需要人工完成的{verification_type or 'manual_verification'}"
                    )
            
            # 在单独的线程中运行同步的登录方法
            import asyncio
            slider = XianyuSliderStealth(user_id=self.cookie_id, enable_learning=True, headless=not show_browser)
            slider.risk_session_id = risk_session_id
            slider.risk_trigger_scene = trigger_scene
            result = await slider._run_sync_method_on_fresh_thread(
                slider.login_with_password_playwright,
                account=username,
                password=password,
                show_browser=show_browser,
                notification_callback=notification_callback_wrapper,
                force_clean_context=True,
            )
            
            if result:
                logger.info(f"【{self.cookie_id}】密码登录成功，获取到Cookie")
                logger.info(f"【{self.cookie_id}】Cookie内容: {result}")
                XianyuLive.clear_password_login_failure_backoff(self.cookie_id)
                
                # 打印密码登录获取的Cookie字段详情
                logger.info(f"【{self.cookie_id}】========== 密码登录Cookie字段详情 ==========")
                logger.info(f"【{self.cookie_id}】Cookie字段数: {len(result)}")
                logger.info(f"【{self.cookie_id}】Cookie字段列表:")
                for i, (key, value) in enumerate(result.items(), 1):
                    if len(str(value)) > 50:
                        logger.info(f"【{self.cookie_id}】  {i:2d}. {key}: {str(value)[:30]}...{str(value)[-20:]} (长度: {len(str(value))})")
                    else:
                        logger.info(f"【{self.cookie_id}】  {i:2d}. {key}: {value}")
                
                # 检查关键字段
                important_keys = ['unb', '_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 't', 'sgcookie', 'cna']
                logger.info(f"【{self.cookie_id}】关键字段检查:")
                for key in important_keys:
                    if key in result:
                        val = result[key]
                        logger.info(f"【{self.cookie_id}】  ✅ {key}: {'存在' if val else '为空'} (长度: {len(str(val)) if val else 0})")
                    else:
                        logger.info(f"【{self.cookie_id}】  ❌ {key}: 缺失")
                logger.info(f"【{self.cookie_id}】==========================================")
                
                # 将cookie字典转换为字符串格式
                new_cookies_str = '; '.join([f"{k}={v}" for k, v in result.items()])
                logger.info(f"【{self.cookie_id}】Cookie字符串摘要: {self._summarize_cookie_string(new_cookies_str)}")
                
                # 记录密码登录时间，防止重复登录
                XianyuLive._last_password_login_time[self.cookie_id] = time.time()
                logger.warning(f"【{self.cookie_id}】已记录密码登录时间，冷却期 {XianyuLive._password_login_cooldown} 秒")
                await self._clear_account_pause_state("密码登录刷新成功")
                self.last_token_refresh_status = 'cookie_refresh_success'
                self.last_token_refresh_error_message = ''
                
                # ⚠️ 先发送通知，再更新cookies并重启任务
                # 因为重启后当前任务会被取消，不能在重启后发送通知
                try:
                    await self.send_token_refresh_notification(
                        f"账号密码登录成功，Cookie已获取，准备更新并重启",
                        "cookie_refresh_success"
                    )
                except Exception as notify_e:
                    logger.warning(f"【{self.cookie_id}】发送通知失败: {self._safe_str(notify_e)}")
                
                # 更新cookies并重启任务
                update_success = await self._update_cookies_and_restart(new_cookies_str)
                
                if update_success:
                    logger.info(f"【{self.cookie_id}】Cookie更新并重启任务成功")
                    # 更新风控日志状态为成功
                    if refresh_risk_log_id:
                        self._update_risk_log(
                            refresh_risk_log_id,
                            session_id=risk_session_id,
                            trigger_scene=trigger_scene,
                            result_code='cookie_refresh_success',
                            processing_status='success',
                            processing_result='密码登录刷新Cookie成功，实例已重启',
                            duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                            event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                        )
                    return True
                else:
                    logger.error(f"【{self.cookie_id}】Cookie更新失败")
                    if refresh_risk_log_id:
                        self._update_risk_log(
                            refresh_risk_log_id,
                            session_id=risk_session_id,
                            trigger_scene=trigger_scene,
                            result_code='cookie_save_failed',
                            processing_status='failed',
                            error_message='Cookie获取成功但更新到数据库失败',
                            duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                            event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                        )
                    return False
                    
            else:
                login_error = getattr(slider, 'last_login_error', '') or "密码登录失败，未获取到Cookie"
                self.last_token_refresh_error_message = login_error
                if await self._protect_account_from_risk_login_retry(login_error):
                    if refresh_risk_log_id:
                        self._update_risk_log(
                            refresh_risk_log_id,
                            session_id=risk_session_id,
                            trigger_scene=trigger_scene,
                            result_code='account_risk_protected',
                            processing_status='failed',
                            error_message=login_error[:200],
                            duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                            event_meta=self._build_risk_event_meta(
                                trigger_scene=trigger_scene,
                                extra={**base_event_meta, 'status_note': '风控保护中'},
                            ),
                        )
                    await self._request_stop_after_account_pause("检测到账号高风险登录提示")
                    return False
                backoff_reason, backoff_seconds = XianyuLive.classify_password_login_failure(login_error)
                XianyuLive.set_password_login_failure_backoff(self.cookie_id, backoff_reason, backoff_seconds)
                protected = await self._protect_account_for_consecutive_failures(
                    XianyuLive.get_password_login_failure_backoff(self.cookie_id)
                )
                logger.warning(f"【{self.cookie_id}】密码登录失败，未获取到Cookie: {login_error}")
                logger.warning(f"【{self.cookie_id}】已进入失败退避期: {backoff_reason}, {backoff_seconds}秒")
                if protected:
                    return False
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code=f'password_login_{backoff_reason}',
                        processing_status='failed',
                        error_message=login_error[:200],
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(
                            trigger_scene=trigger_scene,
                            extra={**base_event_meta, 'backoff_reason': backoff_reason, 'backoff_seconds': backoff_seconds},
                        ),
                    )
                return False

        except Exception as refresh_e:
            if await self._protect_account_from_risk_login_retry(str(refresh_e)):
                if refresh_risk_log_id:
                    self._update_risk_log(
                        refresh_risk_log_id,
                        session_id=risk_session_id,
                        trigger_scene=trigger_scene,
                        result_code='account_risk_protected',
                        processing_status='failed',
                        error_message=str(refresh_e)[:200],
                        duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                        event_meta=self._build_risk_event_meta(
                            trigger_scene=trigger_scene,
                            extra={**base_event_meta, 'status_note': '风控保护中'},
                        ),
                    )
                await self._request_stop_after_account_pause("检测到账号高风险登录异常")
                return False
            self.last_token_refresh_error_message = self._safe_str(refresh_e)
            backoff_reason, backoff_seconds = XianyuLive.classify_password_login_failure(str(refresh_e))
            XianyuLive.set_password_login_failure_backoff(self.cookie_id, backoff_reason, backoff_seconds)
            protected = await self._protect_account_for_consecutive_failures(
                XianyuLive.get_password_login_failure_backoff(self.cookie_id)
            )
            logger.error(f"【{self.cookie_id}】Cookie刷新或实例重启失败: {self._safe_str(refresh_e)}")
            import traceback
            logger.error(f"【{self.cookie_id}】详细堆栈:\n{traceback.format_exc()}")
            if protected:
                return False
            if refresh_risk_log_id:
                self._update_risk_log(
                    refresh_risk_log_id,
                    session_id=risk_session_id,
                    trigger_scene=trigger_scene,
                    result_code='cookie_refresh_exception',
                    processing_status='failed',
                    error_message=str(refresh_e)[:200],
                    duration_ms=max(0, int((time.time() - risk_log_started_at) * 1000)),
                    event_meta=self._build_risk_event_meta(trigger_scene=trigger_scene, extra=base_event_meta),
                )
            return False
        finally:
            if recovery_lock_acquired:
                XianyuLive.release_auth_recovery_lock(self.cookie_id, recovery_lock_owner)

    async def _verify_cookie_validity(self) -> dict:
        """验证Cookie的有效性，通过实际调用API测试
        
        Returns:
            dict: {
                'valid': bool,  # 总体是否有效
                'confirm_api': bool,  # 确认发货API是否有效
                'image_api': bool,  # 图片上传API是否有效
                'details': str  # 详细信息
            }
        """
        logger.info(f"【{self.cookie_id}】开始验证Cookie有效性（使用真实API调用）...")
        
        result = {
            'valid': True,
            'confirm_api': None,
            'web_session_api': None,
            'image_api': None,
            'details': [],
            'inconclusive': False,
            'relogin_recommended': True,
        }
        
        # 1. 测试确认发货API - 使用测试订单ID实际调用
        # try:
        #     logger.info(f"【{self.cookie_id}】测试确认发货API（使用测试数据实际调用）...")
            
        #     # 确保session存在
        #     if not self.session:
        #         import aiohttp
        #         connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
        #         timeout = aiohttp.ClientTimeout(total=30)
        #         self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            
        #     # 创建临时的确认发货实例
        #     from secure_confirm_decrypted import SecureConfirm
        #     confirm_tester = SecureConfirm(
        #         session=self.session,
        #         cookies_str=self.cookies_str,
        #         cookie_id=self.cookie_id,
        #         main_instance=self
        #     )
            
        #     # 使用一个测试订单ID（不存在的订单ID）
        #     # 如果Cookie有效，应该返回"订单不存在"类的错误
        #     # 如果Cookie无效，会返回"Session过期"错误
        #     test_order_id = "999999999999999999"  # 不存在的测试订单ID
            
        #     # 实际调用API (retry_count=3阻止重试，快速失败)
        #     response = await confirm_tester.auto_confirm(test_order_id, retry_count=3)
            
        #     # 分析响应
        #     if response and isinstance(response, dict):
        #         error_msg = str(response.get('error', ''))
        #         success = response.get('success', False)
                
        #         # 检查是否是Session过期错误
        #         if 'Session过期' in error_msg or 'SESSION_EXPIRED' in error_msg:
        #             logger.warning(f"【{self.cookie_id}】❌ 确认发货API验证失败: Session过期")
        #             result['confirm_api'] = False
        #             result['valid'] = False
        #             result['details'].append("确认发货API: Session过期")
        #         elif '令牌过期' in error_msg:
        #             logger.warning(f"【{self.cookie_id}】❌ 确认发货API验证失败: 令牌过期")
        #             result['confirm_api'] = False
        #             result['valid'] = False
        #             result['details'].append("确认发货API: 令牌过期")
        #         elif success:
        #             # 竟然成功了（不太可能，因为是测试订单ID）
        #             logger.info(f"【{self.cookie_id}】✅ 确认发货API验证通过: API调用成功")
        #             result['confirm_api'] = True
        #             result['details'].append("确认发货API: 通过验证")
        #         elif error_msg and len(error_msg) > 0:
        #             # 有其他错误信息（如订单不存在、重试次数过多等），说明Cookie是有效的
        #             logger.info(f"【{self.cookie_id}】✅ 确认发货API验证通过: Cookie有效（返回业务错误: {error_msg[:50]}）")
        #             result['confirm_api'] = True
        #             result['details'].append(f"确认发货API: 通过验证")
        #         else:
        #             # 没有明确信息，保守认为可能有问题
        #             logger.warning(f"【{self.cookie_id}】⚠️ 确认发货API验证警告: 响应不明确")
        #             result['confirm_api'] = False
        #             result['valid'] = False
        #             result['details'].append("确认发货API: 响应不明确")
        #     else:
        #         # 没有响应，可能有问题
        #         logger.warning(f"【{self.cookie_id}】⚠️ 确认发货API验证警告: 无响应")
        #         result['confirm_api'] = False
        #         result['valid'] = False
        #         result['details'].append("确认发货API: 无响应")
                    
        # except Exception as e:
        #     error_str = self._safe_str(e)
        #     # 检查异常信息中是否包含Session过期
        #     if 'Session过期' in error_str or 'SESSION_EXPIRED' in error_str:
        #         logger.warning(f"【{self.cookie_id}】❌ 确认发货API验证失败: Session过期")
        #         result['confirm_api'] = False
        #         result['valid'] = False
        #         result['details'].append("确认发货API: Session过期")
        #     else:
        #         logger.error(f"【{self.cookie_id}】确认发货API验证异常: {error_str}")
        #         # 网络异常等问题，不一定是Cookie问题，暂时标记为通过
        #         result['confirm_api'] = True
        #         result['details'].append(f"确认发货API: 调用异常(可能非Cookie问题)")
        
        # 2. 测试网页登录态 - 只读访问 IM 页面，检测是否被重定向到登录/验证页
        try:
            logger.info(f"【{self.cookie_id}】测试网页登录态（访问 IM 页面）...")

            if not self.session:
                connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
                timeout = aiohttp.ClientTimeout(total=30)
                self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)

            async with self.session.get(
                'https://www.goofish.com/im',
                headers={
                    'cookie': self.cookies_str,
                    'Referer': 'https://www.goofish.com/',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                },
                allow_redirects=True
            ) as response:
                final_url = str(response.url)
                page_text = await response.text()

                redirected_to_login = (
                    'passport.goofish.com' in final_url or
                    'mini_login' in final_url or
                    ('mini_login.htm' in page_text and 'alibaba-login-box' in page_text)
                )

                if redirected_to_login or response.status in (401, 403):
                    logger.warning(f"【{self.cookie_id}】❌ 网页登录态验证失败: 已进入登录/验证页 ({final_url})")
                    result['web_session_api'] = False
                    result['valid'] = False
                    result['details'].append("网页登录态: 已重定向到登录/验证页")
                elif response.status >= 500:
                    logger.warning(f"【{self.cookie_id}】⚠️ 网页登录态验证遇到服务端异常: HTTP {response.status}")
                    result['web_session_api'] = None
                    result['inconclusive'] = True
                    if result['valid']:
                        result['relogin_recommended'] = False
                    result['details'].append(f"网页登录态: 服务端异常，结果不确定 (HTTP {response.status})")
                elif response.status == 200:
                    logger.info(f"【{self.cookie_id}】✅ 网页登录态验证通过: {final_url}")
                    result['web_session_api'] = True
                    result['details'].append("网页登录态: 通过验证")
                else:
                    logger.warning(f"【{self.cookie_id}】⚠️ 网页登录态验证结果不明确: HTTP {response.status}, URL={final_url}")
                    result['web_session_api'] = None
                    result['inconclusive'] = True
                    if result['valid']:
                        result['relogin_recommended'] = False
                    result['details'].append(f"网页登录态: 结果不明确 (HTTP {response.status})")

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            error_str = self._safe_str(e)
            logger.warning(f"【{self.cookie_id}】⚠️ 网页登录态验证网络异常: {error_str}")
            result['web_session_api'] = None
            result['inconclusive'] = True
            if result['valid']:
                result['relogin_recommended'] = False
            result['details'].append(f"网页登录态: 网络异常，结果不确定 ({error_str[:50]})")
        except Exception as e:
            error_str = self._safe_str(e)
            logger.error(f"【{self.cookie_id}】网页登录态验证异常: {error_str}")
            result['web_session_api'] = None
            result['inconclusive'] = True
            if result['valid']:
                result['relogin_recommended'] = False
            result['details'].append(f"网页登录态: 验证异常，结果不确定 - {error_str[:50]}")

        # 3. 测试图片上传API - 创建测试图片并实际上传
        try:
            logger.info(f"【{self.cookie_id}】测试图片上传API（使用测试图片实际上传）...")
            
            # 创建一个最小的测试图片（1x1像素的PNG）
            import tempfile
            import os
            from PIL import Image
            
            # 创建临时目录
            temp_dir = tempfile.gettempdir()
            test_image_path = os.path.join(temp_dir, f'cookie_test_{self.cookie_id}.png')
            
            try:
                # 创建1x1像素的白色图片
                img = Image.new('RGB', (1, 1), color='white')
                img.save(test_image_path, 'PNG')
                logger.info(f"【{self.cookie_id}】已创建测试图片: {test_image_path}")
                
                # 创建图片上传实例
                from utils.image_uploader import ImageUploader
                uploader = ImageUploader(cookies_str=self.cookies_str)
                
                # 创建session
                await uploader.create_session()
                
                try:
                    upload_result = None
                    error_type = None
                    error_message = None

                    for attempt in range(2):
                        upload_result = await uploader.upload_image(test_image_path)
                        if upload_result:
                            break

                        error_type = getattr(uploader, 'last_error_type', None)
                        error_message = getattr(uploader, 'last_error_message', None) or "未知原因"
                        is_retryable_auth = error_type == 'auth' and error_message == '返回登录页面' and result['web_session_api'] is not False
                        if attempt == 0 and is_retryable_auth:
                            logger.warning(
                                f"【{self.cookie_id}】图片上传校验首次返回登录页，但网页登录态仍可访问，1.5秒后重试一次"
                            )
                            await asyncio.sleep(1.5)
                            continue
                        break
                finally:
                    # 确保关闭session
                    await uploader.close_session()
                
                # 分析上传结果
                if upload_result:
                    # 上传成功，Cookie有效
                    logger.info(f"【{self.cookie_id}】✅ 图片上传API验证通过: 上传成功 ({upload_result[:50]}...)")
                    result['image_api'] = True
                    result['details'].append("图片上传API: 通过验证")
                else:
                    error_type = getattr(uploader, 'last_error_type', None)
                    error_message = getattr(uploader, 'last_error_message', None) or "未知原因"
                    if error_type == 'network':
                        logger.warning(f"【{self.cookie_id}】⚠️ 图片上传API验证遇到网络异常，不判定为Cookie失效: {error_message}")
                        result['image_api'] = None
                        result['inconclusive'] = True
                        if result['valid']:
                            result['relogin_recommended'] = False
                        result['details'].append(f"图片上传API: 网络异常，结果不确定 ({error_message[:50]})")
                    elif error_type == 'http' and getattr(uploader, 'last_http_status', None) and uploader.last_http_status >= 500:
                        logger.warning(f"【{self.cookie_id}】⚠️ 图片上传API返回服务端异常，不判定为Cookie失效: HTTP {uploader.last_http_status}")
                        result['image_api'] = None
                        result['inconclusive'] = True
                        if result['valid']:
                            result['relogin_recommended'] = False
                        result['details'].append(f"图片上传API: 服务端异常，结果不确定 (HTTP {uploader.last_http_status})")
                    elif error_type == 'auth' and error_message == '返回登录页面':
                        logger.warning(
                            f"【{self.cookie_id}】❌ 图片上传接口返回登录页，按旧版严格策略判定Cookie失效"
                        )
                        result['image_api'] = False
                        result['valid'] = False
                        result['details'].append("图片上传API: 返回登录页面")
                    else:
                        # 明确认证/会话异常才视为Cookie失效
                        logger.warning(f"【{self.cookie_id}】❌ 图片上传API验证失败: {error_message}")
                        result['image_api'] = False
                        result['valid'] = False
                        result['details'].append(f"图片上传API: {error_message[:50]}")
                
            finally:
                # 清理测试图片
                if os.path.exists(test_image_path):
                    try:
                        os.remove(test_image_path)
                        logger.debug(f"【{self.cookie_id}】已删除测试图片")
                    except Exception:
                        pass
                        
        except Exception as e:
            error_str = self._safe_str(e)
            logger.error(f"【{self.cookie_id}】图片上传API验证异常: {error_str}")
            error_lower = error_str.lower()
            auth_keywords = ['返回登录页面', 'session过期', '令牌过期', 'login', 'mini_login', 'passport.goofish.com']
            if any(keyword.lower() in error_lower for keyword in auth_keywords):
                result['image_api'] = False
                result['valid'] = False
                result['details'].append(f"图片上传API: 验证异常({error_str[:50]})")
            else:
                # 上传校验异常可能是网络或环境问题，不直接判定为Cookie失效
                result['image_api'] = None
                result['inconclusive'] = True
                if result['valid']:
                    result['relogin_recommended'] = False
                result['details'].append(f"图片上传API: 验证异常，结果不确定 - {error_str[:50]}")
        
        if result['image_api'] is False:
            result['valid'] = False
        elif result['web_session_api'] is False and result['image_api'] is not True:
            result['valid'] = False
        elif result['web_session_api'] is False and result['image_api'] is True:
            logger.warning(f"【{self.cookie_id}】❌ 网页登录态与图片上传校验结果不一致，按严格策略判定Cookie失效")
            result['valid'] = False
            result['details'].append("校验结果: 网页登录态与图片上传结果不一致")

        # 汇总结果
        if result['valid']:
            if result['inconclusive']:
                logger.warning(f"【{self.cookie_id}】⚠️ Cookie验证结果不确定: 未发现明确失效证据，但部分校验存在波动或结果矛盾")
            else:
                logger.info(f"【{self.cookie_id}】✅ Cookie验证通过: 所有关键API均可用")
        else:
            logger.warning(f"【{self.cookie_id}】❌ Cookie验证失败:")
            for detail in result['details']:
                logger.warning(f"【{self.cookie_id}】  - {detail}")
        
        result['details'] = '; '.join(result['details'])
        return result

    async def _restart_instance(self):
        """重启XianyuLive实例
        
        ⚠️ 注意：此方法会触发当前任务被取消！
        调用此方法后，当前任务会立即被 CookieManager 取消，
        因此不要在此方法后执行任何重要操作。
        """
        try:
            logger.info(f"【{self.cookie_id}】准备重启实例...")

            # 导入CookieManager
            from cookie_manager import manager as cookie_manager

            if cookie_manager:
                # 通过CookieManager重启实例
                logger.info(f"【{self.cookie_id}】通过CookieManager重启实例...")
                
                # ⚠️ 重要：不要等待重启完成！
                # cookie_manager.update_cookie() 会立即取消当前任务
                # 如果我们等待它完成，会导致 CancelledError 中断等待
                # 正确的做法是：触发重启后立即返回，让任务自然退出
                
                import threading
                
                def trigger_restart():
                    """在后台线程中触发重启，不阻塞当前任务"""
                    try:
                        # 给当前任务足够时间完成清理和退出（避免竞态条件）
                        # 增加到2秒，确保任务有足够时间处理返回和清理
                        import time
                        time.sleep(2.0)
                        
                        # save_to_db=False 因为 update_config_cookies 已经保存过了
                        cookie_manager.update_cookie(self.cookie_id, self.cookies_str, save_to_db=False)
                        logger.info(f"【{self.cookie_id}】实例重启请求已触发")
                    except Exception as e:
                        logger.error(f"【{self.cookie_id}】触发实例重启失败: {e}")
                        import traceback
                        logger.error(f"【{self.cookie_id}】重启失败详情:\n{traceback.format_exc()}")

                # 在后台线程中触发重启
                restart_thread = threading.Thread(target=trigger_restart, daemon=True)
                restart_thread.start()
                
                logger.info(f"【{self.cookie_id}】实例重启已触发，当前任务即将退出...")
                logger.warning(f"【{self.cookie_id}】注意：重启请求已发送，CookieManager将在2秒后取消当前任务并启动新实例")
                    
            else:
                logger.warning(f"【{self.cookie_id}】CookieManager不可用，无法重启实例")

        except Exception as e:
            logger.error(f"【{self.cookie_id}】重启实例失败: {self._safe_str(e)}")
            import traceback
            logger.error(f"【{self.cookie_id}】重启失败堆栈:\n{traceback.format_exc()}")
            # 发送重启失败通知
            try:
                await self.send_token_refresh_notification(f"实例重启失败: {str(e)}", "instance_restart_failed")
            except Exception as notify_e:
                logger.error(f"【{self.cookie_id}】发送重启失败通知时出错: {self._safe_str(notify_e)}")

    async def save_item_info_to_db(self, item_id: str, item_detail: str = None, item_title: str = None):
        """保存商品信息到数据库

        Args:
            item_id: 商品ID
            item_detail: 商品详情内容（可以是任意格式的文本）
            item_title: 商品标题
        """
        try:
            # 跳过以 auto_ 开头的商品ID
            if item_id and item_id.startswith('auto_'):
                logger.warning(f"跳过保存自动生成的商品ID: {item_id}")
                return

            # 验证：如果只有商品ID，没有商品标题和商品详情，则不插入数据库
            if not item_title and not item_detail:
                logger.warning(f"跳过保存商品信息：缺少商品标题和详情 - {item_id}")
                return

            # 如果有商品标题但没有详情，也跳过（根据需求，需要同时有标题和详情）
            if not item_title or not item_detail:
                logger.warning(f"跳过保存商品信息：商品标题或详情不完整 - {item_id}")
                return

            from db_manager import db_manager

            # 直接使用传入的详情内容
            item_data = item_detail

            # 保存到数据库
            success = db_manager.save_item_info(self.cookie_id, item_id, item_data)
            if success:
                logger.info(f"商品信息已保存到数据库: {item_id}")
            else:
                logger.warning(f"保存商品信息到数据库失败: {item_id}")

        except Exception as e:
            logger.error(f"保存商品信息到数据库异常: {self._safe_str(e)}")

    async def save_item_detail_only(self, item_id, item_detail):
        """仅保存商品详情（不影响标题等基本信息）"""
        try:
            from db_manager import db_manager

            # 使用专门的详情更新方法
            success = db_manager.update_item_detail(self.cookie_id, item_id, item_detail)

            if success:
                logger.info(f"商品详情已更新: {item_id}")
            else:
                logger.warning(f"更新商品详情失败: {item_id}")

            return success

        except Exception as e:
            logger.error(f"更新商品详情异常: {self._safe_str(e)}")
            return False

    async def fetch_item_detail_from_api(self, item_id: str, force_refresh: bool = False) -> str:
        """获取商品详情（使用浏览器获取，支持24小时缓存）

        Args:
            item_id: 商品ID
            force_refresh: 是否绕过缓存强制拉取最新详情

        Returns:
            str: 商品详情文本，获取失败返回空字符串
        """
        try:
            # 检查是否启用自动获取功能
            from config import config
            auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})

            if not auto_fetch_config.get('enabled', True):
                logger.warning(f"自动获取商品详情功能已禁用: {item_id}")
                return ""

            # 1. 首先检查缓存（24小时有效）
            if not force_refresh:
                async with self._item_detail_cache_lock:
                    if item_id in self._item_detail_cache:
                        cache_data = self._item_detail_cache[item_id]
                        cache_time = cache_data['timestamp']
                        current_time = time.time()

                        # 检查缓存是否在24小时内
                        if current_time - cache_time < self._item_detail_cache_ttl:
                            # 更新访问时间（用于LRU）
                            cache_data['access_time'] = current_time
                            logger.info(f"从缓存获取商品详情: {item_id}")
                            return cache_data['detail']
                        else:
                            # 缓存过期，删除
                            del self._item_detail_cache[item_id]
                            logger.warning(f"缓存已过期，删除: {item_id}")
            else:
                logger.info(f"强制刷新商品详情，跳过缓存: {item_id}")

            # 2. 尝试使用浏览器获取商品详情
            detail_from_browser = await self._fetch_item_detail_from_browser(item_id)
            if detail_from_browser:
                # 保存到缓存（带大小限制）
                await self._add_to_item_cache(item_id, detail_from_browser)
                logger.info(f"成功通过浏览器获取商品详情: {item_id}, 长度: {len(detail_from_browser)}")
                return detail_from_browser

            # 浏览器获取失败
            logger.warning(f"浏览器获取商品详情失败: {item_id}")
            return ""

        except Exception as e:
            logger.error(f"获取商品详情异常: {item_id}, 错误: {self._safe_str(e)}")
            return ""

    async def _add_to_item_cache(self, item_id: str, detail: str):
        """添加商品详情到缓存，实现LRU策略和大小限制
        
        Args:
            item_id: 商品ID
            detail: 商品详情
        """
        async with self._item_detail_cache_lock:
            current_time = time.time()
            
            # 检查缓存大小，如果超过限制则清理
            if len(self._item_detail_cache) >= self._item_detail_cache_max_size:
                # 使用LRU策略删除最久未访问的项
                if self._item_detail_cache:
                    # 找到最久未访问的项
                    oldest_item = min(
                        self._item_detail_cache.items(),
                        key=lambda x: x[1].get('access_time', x[1]['timestamp'])
                    )
                    oldest_item_id = oldest_item[0]
                    del self._item_detail_cache[oldest_item_id]
                    logger.warning(f"缓存已满，删除最旧项: {oldest_item_id}")
            
            # 添加新项到缓存
            self._item_detail_cache[item_id] = {
                'detail': detail,
                'timestamp': current_time,
                'access_time': current_time
            }
            logger.warning(f"添加商品详情到缓存: {item_id}, 当前缓存大小: {len(self._item_detail_cache)}")

    @classmethod
    async def _cleanup_item_cache(cls):
        """清理过期的商品详情缓存"""
        try:
            async with cls._item_detail_cache_lock:
                # 在持有锁时也要能响应取消信号
                await asyncio.sleep(0)
                
                current_time = time.time()
                expired_items = []
                
                # 找出所有过期的项
                for item_id, cache_data in cls._item_detail_cache.items():
                    # 在循环中也要能响应取消信号
                    await asyncio.sleep(0)
                    if current_time - cache_data['timestamp'] >= cls._item_detail_cache_ttl:
                        expired_items.append(item_id)
                
                # 删除过期项
                for item_id in expired_items:
                    await asyncio.sleep(0)  # 让出控制权
                    del cls._item_detail_cache[item_id]
                
                if expired_items:
                    logger.info(f"清理了 {len(expired_items)} 个过期的商品详情缓存")
                
                return len(expired_items)
        except asyncio.CancelledError:
            # 如果被取消，确保锁能正确释放
            raise

    async def _fetch_item_detail_from_browser(self, item_id: str) -> str:
        """使用浏览器获取商品详情"""
        playwright = None
        browser = None
        try:
            from playwright.async_api import async_playwright

            logger.info(f"开始使用浏览器获取商品详情: {item_id}")

            playwright = await async_playwright().start()

            # 启动浏览器（参照order_detail_fetcher的配置）
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-features=TranslateUI',
                '--disable-ipc-flooding-protection',
                '--disable-extensions',
                '--disable-default-apps',
                '--disable-sync',
                '--disable-translate',
                '--hide-scrollbars',
                '--mute-audio',
                '--no-default-browser-check',
                '--no-pings'
            ]

            # 在Docker环境中添加额外参数
            if os.getenv('DOCKER_ENV'):
                browser_args.extend([
                    # '--single-process',  # 注释掉，避免多用户并发时的进程冲突和资源泄漏
                    '--disable-background-networking',
                    '--disable-client-side-phishing-detection',
                    '--disable-hang-monitor',
                    '--disable-popup-blocking',
                    '--disable-prompt-on-repost',
                    '--disable-web-resources',
                    '--metrics-recording-only',
                    '--safebrowsing-disable-auto-update',
                    '--enable-automation',
                    '--password-store=basic',
                    '--use-mock-keychain'
                ])

            browser = await playwright.chromium.launch(
                headless=True,  # 移动模式使用无头模式
                args=browser_args
            )

            # 创建移动设备浏览器上下文（模拟iPhone）
            context = await browser.new_context(
                viewport={'width': 375, 'height': 812},  # iPhone X/11/12 尺寸
                user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 AliApp(TB/11.15.0)',
                device_scale_factor=3,  # iPhone 的屏幕缩放比例
                is_mobile=True,
                has_touch=True
            )

            # 设置Cookie
            cookies = []
            for cookie_pair in self.cookies_str.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })

            await context.add_cookies(cookies)
            logger.info(f"已设置 {len(cookies)} 个Cookie（移动模式）")

            # 创建页面
            page = await context.new_page()

            # 构造移动版商品详情页面URL
            item_url = f"https://h5.m.goofish.com/item?id={item_id}"
            logger.info(f"访问移动版商品页面: {item_url}")

            # 访问页面
            await page.goto(item_url, wait_until='networkidle', timeout=30000)

            # 等待页面完全加载
            await asyncio.sleep(2)

            # 获取商品详情内容
            detail_text = ""
            try:
                # 移动版页面选择器列表（按优先级排序）
                selectors = [
                    '.detailDesc--descText--1FMDTCm',  # 移动版商品详情主选择器
                    'span.rax-text-v2.detailDesc--descText--1FMDTCm',  # 完整选择器
                    '[class*="detailDesc--descText"]',  # 匹配包含detailDesc--descText的类名
                    '[class*="descText"]',  # 匹配包含descText的类名
                    '.desc--GaIUKUQY',  # PC版选择器（备用）
                    '.detail-desc',     # 常见的详情选择器
                    '.item-desc',       # 商品描述
                    '[class*="desc"]',  # 包含desc的类名
                ]
                
                for selector in selectors:
                    try:
                        # 尝试等待元素出现（短超时）
                        await page.wait_for_selector(selector, timeout=3000)
                        detail_element = await page.query_selector(selector)
                        if detail_element:
                            detail_text = await detail_element.inner_text()
                            if detail_text and len(detail_text.strip()) > 0:
                                logger.info(f"成功获取商品详情（选择器: {selector}）: {item_id}, 长度: {len(detail_text)}")
                                return detail_text.strip()
                    except Exception as e:
                        logger.debug(f"选择器 {selector} 未找到: {self._safe_str(e)}")
                        continue
                
                # 如果所有选择器都失败，尝试获取整个页面的文本内容
                logger.warning(f"未找到特定详情元素，尝试获取整个页面内容: {item_id}")
                body_text = await page.inner_text('body')
                if body_text:
                    logger.info(f"获取到页面整体内容: {item_id}, 长度: {len(body_text)}")
                    return body_text.strip()
                else:
                    logger.warning(f"未找到商品详情元素: {item_id}")

            except Exception as e:
                logger.warning(f"获取商品详情元素失败: {item_id}, 错误: {self._safe_str(e)}")

            return ""

        except Exception as e:
            logger.error(f"浏览器获取商品详情异常: {item_id}, 错误: {self._safe_str(e)}")
            return ""
        finally:
            # 确保资源被正确清理
            try:
                if browser:
                    await browser.close()
                    logger.warning(f"Browser已关闭: {item_id}")
            except Exception as e:
                logger.warning(f"关闭browser时出错: {self._safe_str(e)}")
            
            try:
                if playwright:
                    await playwright.stop()
                    logger.warning(f"Playwright已停止: {item_id}")
            except Exception as e:
                logger.warning(f"停止playwright时出错: {self._safe_str(e)}")


    async def save_items_list_to_db(self, items_list, sync_item_details=False):
        """批量保存商品列表信息到数据库（并发安全）

        Args:
            items_list: 从get_item_list_info获取的商品列表
            sync_item_details: 是否同步已存在商品的最新详情
        """
        try:
            from db_manager import db_manager

            # 准备批量数据，区分新商品和需要更新的商品
            batch_new_data = []  # 新商品，保存所有信息
            batch_update_data = []  # 已有商品，只更新标题和价格
            items_need_detail = []  # 需要获取或同步详情的商品列表

            for item in items_list:
                item_id = item.get('id')
                if not item_id or item_id.startswith('auto_'):
                    continue

                # 构造商品详情数据
                item_detail = {
                    'title': item.get('title', ''),
                    'price': item.get('price', ''),
                    'price_text': item.get('price_text', ''),
                    'category_id': item.get('category_id', ''),
                    'auction_type': item.get('auction_type', ''),
                    'item_status': item.get('item_status', 0),
                    'detail_url': item.get('detail_url', ''),
                    'pic_info': item.get('pic_info', {}),
                    'detail_params': item.get('detail_params', {}),
                    'track_params': item.get('track_params', {}),
                    'item_label_data': item.get('item_label_data', {}),
                    'card_type': item.get('card_type', 0)
                }

                # 检查数据库中是否已有该商品
                existing_item = db_manager.get_item_info(self.cookie_id, item_id)
                
                if existing_item:
                    # 商品已存在，先更新标题和价格；商品详情按同步模式单独处理
                    batch_update_data.append({
                        'cookie_id': self.cookie_id,
                        'item_id': item_id,
                        'item_title': item.get('title', ''),
                        'item_price': item.get('price_text', ''),
                        'item_category': str(item.get('category_id', ''))
                    })
                    if sync_item_details:
                        items_need_detail.append({
                            'item_id': item_id,
                            'item_title': item.get('title', '')
                        })
                    logger.debug(f"商品 {item_id} 已存在，将更新标题和价格")
                else:
                    # 新商品，保存所有信息
                    batch_new_data.append({
                        'cookie_id': self.cookie_id,
                        'item_id': item_id,
                        'item_title': item.get('title', ''),
                        'item_description': '',  # 暂时为空
                        'item_category': str(item.get('category_id', '')),
                        'item_price': item.get('price_text', ''),
                        'item_detail': json.dumps(item_detail, ensure_ascii=False)
                    })
                    
                    # 新商品需要获取详情
                    items_need_detail.append({
                        'item_id': item_id,
                        'item_title': item.get('title', '')
                    })
                    logger.debug(f"商品 {item_id} 是新商品，将保存完整信息")

            saved_count = 0
            
            # 保存新商品
            if batch_new_data:
                new_count = db_manager.batch_save_item_basic_info(batch_new_data)
                logger.info(f"新增商品信息: {new_count}/{len(batch_new_data)} 个")
                saved_count += new_count
            
            # 更新已有商品的标题和价格
            if batch_update_data:
                update_count = db_manager.batch_update_item_title_price(batch_update_data)
                logger.info(f"更新商品标题和价格: {update_count}/{len(batch_update_data)} 个")
                saved_count += update_count

            # 异步获取商品详情
            if items_need_detail:
                from config import config
                auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})

                if auto_fetch_config.get('enabled', True):
                    action_text = '同步最新详情' if sync_item_details else '获取缺失详情'
                    logger.info(f"准备为 {len(items_need_detail)} 个商品{action_text}...")
                    detail_success_count = await self._fetch_item_details(
                        items_need_detail,
                        force_refresh=sync_item_details,
                    )
                    logger.info(f"成功为 {detail_success_count}/{len(items_need_detail)} 个商品{action_text}")
                else:
                    logger.info(f"有 {len(items_need_detail)} 个商品需要获取详情，但自动获取功能已禁用")

            return saved_count

        except Exception as e:
            logger.error(f"批量保存商品信息异常: {self._safe_str(e)}")
            return 0

    async def _fetch_item_details(self, items_need_detail, force_refresh=False):
        """批量获取或同步商品详情

        Args:
            items_need_detail: 需要获取详情的商品列表
            force_refresh: 是否绕过缓存强制拉取最新详情

        Returns:
            int: 成功获取详情的商品数量
        """
        success_count = 0

        try:
            from db_manager import db_manager
            from config import config

            # 从配置获取并发数量和延迟时间
            auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})
            max_concurrent = auto_fetch_config.get('max_concurrent', 3)
            retry_delay = auto_fetch_config.get('retry_delay', 0.5)

            # 限制并发数量，避免对API服务器造成压力
            semaphore = asyncio.Semaphore(max_concurrent)

            async def fetch_single_item_detail(item_info):
                async with semaphore:
                    try:
                        item_id = item_info['item_id']
                        item_title = item_info['item_title']

                        # 获取商品详情
                        item_detail_text = await self.fetch_item_detail_from_api(
                            item_id,
                            force_refresh=force_refresh,
                        )

                        if item_detail_text:
                            # 保存详情到数据库
                            success = await self.save_item_detail_only(item_id, item_detail_text)
                            if success:
                                logger.info(f"✅ 成功获取并保存商品详情: {item_id} - {item_title}")
                                return 1
                            else:
                                logger.warning(f"❌ 获取详情成功但保存失败: {item_id}")
                        else:
                            logger.warning(f"❌ 未能获取商品详情: {item_id} - {item_title}")

                        # 添加延迟，避免请求过于频繁
                        await asyncio.sleep(retry_delay)
                        return 0

                    except Exception as e:
                        logger.error(f"获取单个商品详情异常: {item_info.get('item_id', 'unknown')}, 错误: {self._safe_str(e)}")
                        return 0

            # 并发获取所有商品详情
            tasks = [fetch_single_item_detail(item_info) for item_info in items_need_detail]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 统计成功数量
            for result in results:
                if isinstance(result, int):
                    success_count += result
                elif isinstance(result, Exception):
                    logger.error(f"获取商品详情任务异常: {result}")

            return success_count

        except Exception as e:
            logger.error(f"批量获取商品详情异常: {self._safe_str(e)}")
            return success_count

    async def get_item_info(self, item_id, retry_count=0):
        """获取商品信息，自动处理token失效的情况"""
        if retry_count >= 4:  # 最多重试3次
            logger.error("获取商品信息失败，重试次数过多")
            return {"error": "获取商品信息失败，重试次数过多"}

        # 确保session已创建
        if not self.session:
            await self.create_session()

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.taobao.idle.pc.detail',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }

        data_val = '{"itemId":"' + item_id + '"}'
        data = {
            'data': data_val,
        }

        # 始终从最新的cookies中获取_m_h5_tk token（刷新后cookies会被更新）
        token = trans_cookies(self.cookies_str).get('_m_h5_tk', '').split('_')[0] if trans_cookies(self.cookies_str).get('_m_h5_tk') else ''

        if token:
            logger.warning(f"使用cookies中的_m_h5_tk token: {self._mask_secret_value(token, head=6, tail=4)}")
        else:
            logger.warning("cookies中没有找到_m_h5_tk token")

        from utils.xianyu_utils import generate_sign
        sign = generate_sign(params['t'], token, data_val)
        params['sign'] = sign

        try:
            async with self.session.post(
                'https://h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail/1.0/',
                params=params,
                data=data
            ) as response:
                res_json = await response.json()

                if await self._apply_response_cookie_updates(response.headers, "item_detail"):
                    logger.warning("已更新Cookie到数据库")

                logger.warning(f"商品信息获取成功: {res_json}")
                # 检查返回状态
                if isinstance(res_json, dict):
                    ret_value = res_json.get('ret', [])
                    # 检查ret是否包含成功信息
                    if not any('SUCCESS::调用成功' in ret for ret in ret_value):
                        logger.warning(f"商品信息API调用失败，错误信息: {ret_value}")

                        await asyncio.sleep(0.5)
                        return await self.get_item_info(item_id, retry_count + 1)
                    else:
                        logger.warning(f"商品信息获取成功: {item_id}")
                        return res_json
                else:
                    logger.error(f"商品信息API返回格式异常: {res_json}")
                    return await self.get_item_info(item_id, retry_count + 1)

        except Exception as e:
            logger.error(f"商品信息API请求异常: {self._safe_str(e)}")
            await asyncio.sleep(0.5)
            return await self.get_item_info(item_id, retry_count + 1)

    async def _is_item_owned_by_self(self, item_id: str):
        """判断商品是否属于本账号，用于区分本账号在会话中的买家/卖家身份。

        Returns:
            True: 本账号的商品（自己是卖家）
            False: 别人的商品（本账号主动咨询/购买，自己是买家）
            None: 无法判断（缺商品ID或接口失败）
        判断顺序：内存缓存 -> 本地 item_info 表（只存本账号商品）-> 商品详情API比对卖家ID。
        """
        if not item_id:
            return None

        cache = getattr(self, '_item_ownership_cache', None)
        if cache is None:
            cache = self._item_ownership_cache = {}
        if item_id in cache:
            return cache[item_id]

        # 本地 item_info 表只存本账号的商品，命中即认定为自己的
        try:
            from db_manager import db_manager
            if db_manager.get_item_info(self.cookie_id, item_id):
                cache[item_id] = True
                return True
        except Exception as db_e:
            logger.debug(f"【{self.cookie_id}】查询本地商品归属失败: {self._safe_str(db_e)}")

        # 本地没有：调商品详情接口比对卖家ID（结果缓存，避免重复请求）
        try:
            detail = await self.get_item_info(item_id)
            data = (detail or {}).get('data') or {}
            seller = data.get('sellerDO') or {}
            seller_id = str(
                seller.get('sellerId')
                or seller.get('userId')
                or (data.get('itemDO') or {}).get('sellerId')
                or ''
            ).strip()
            if seller_id:
                owned = seller_id == str(self.myid)
                cache[item_id] = owned
                if not owned:
                    logger.info(
                        f"【{self.cookie_id}】商品 {item_id} 属于卖家 {seller_id}，"
                        f"本账号({self.myid})在该会话中是买家身份"
                    )
                return owned
        except Exception as api_e:
            logger.warning(f"【{self.cookie_id}】判断商品归属失败（默认放行）: {self._safe_str(api_e)}")

        return None

    def extract_item_id_from_message(self, message):
        """从消息中提取商品ID的辅助方法"""
        try:
            # 注意: message["1"] 是会话ID(chat_id/cid)，格式如 "56226853668@goofish"
            # 不能从中提取商品ID，否则会把chat_id误当作item_id

            # 方法1: 从message["3"]中提取
            message_3 = message.get('3', {})
            if isinstance(message_3, dict):

                # 从extension中提取
                if 'extension' in message_3:
                    extension = message_3['extension']
                    if isinstance(extension, dict):
                        item_id = extension.get('itemId') or extension.get('item_id')
                        if item_id:
                            logger.info(f"从extension中提取商品ID: {item_id}")
                            return item_id

                # 从bizData中提取
                if 'bizData' in message_3:
                    biz_data = message_3['bizData']
                    if isinstance(biz_data, dict):
                        item_id = biz_data.get('itemId') or biz_data.get('item_id')
                        if item_id:
                            logger.info(f"从bizData中提取商品ID: {item_id}")
                            return item_id

                # 从其他可能的字段中提取
                for key, value in message_3.items():
                    if isinstance(value, dict):
                        item_id = value.get('itemId') or value.get('item_id')
                        if item_id:
                            logger.info(f"从{key}字段中提取商品ID: {item_id}")
                            return item_id

                # 从消息内容中提取数字ID
                content = message_3.get('content', '')
                if isinstance(content, str) and content:
                    id_match = re.search(r'(\d{10,})', content)
                    if id_match:
                        logger.info(f"【{self.cookie_id}】从消息内容中提取商品ID: {id_match.group(1)}")
                        return id_match.group(1)

            # 方法2: 遍历整个消息结构查找可能的商品ID
            # 跳过的字段: "1" 是会话ID(chat_id/cid)，不包含商品ID
            # 跳过可能包含非商品ID的字段
            skip_keys = {'1', 'tradeId', 'trade_id', 'bizId', 'biz_id', 'orderId', 'order_id',
                        'userId', 'user_id', 'senderId', 'sender_id', 'receiverId', 'receiver_id',
                        'chatId', 'chat_id', 'conversationId', 'conversation_id', 'msgId', 'msg_id'}

            def find_item_id_recursive(obj, path=""):
                if isinstance(obj, dict):
                    # 只查找明确命名为 itemId 的字段（不查找通用的 'id' 字段，避免误提取 tradeId 等）
                    for key in ['itemId', 'item_id']:
                        if key in obj and isinstance(obj[key], (str, int)):
                            value = str(obj[key])
                            if len(value) >= 10 and value.isdigit():
                                logger.info(f"从{path}.{key}中提取商品ID: {value}")
                                return value

                    # 递归查找（跳过chat_id和其他非商品ID字段）
                    for key, value in obj.items():
                        if key in skip_keys:
                            continue
                        result = find_item_id_recursive(value, f"{path}.{key}" if path else key)
                        if result:
                            return result

                elif isinstance(obj, str):
                    # 跳过chat_id格式的字符串（如 "56226853668@goofish"）
                    if '@goofish' in obj or '@xianyu' in obj:
                        return None
                    # 只从URL中提取itemId参数，不从普通字符串中提取数字（避免误提取）
                    if 'itemId=' in obj:
                        id_match = re.search(r'itemId=(\d{10,})', obj)
                        if id_match:
                            logger.info(f"从{path}的URL参数中提取商品ID: {id_match.group(1)}")
                            return id_match.group(1)

                return None

            result = find_item_id_recursive(message)
            if result:
                return result

            logger.warning("所有方法都未能提取到商品ID")
            return None

        except Exception as e:
            logger.error(f"提取商品ID失败: {self._safe_str(e)}")
            return None

    def debug_message_structure(self, message, context=""):
        """调试消息结构的辅助方法"""
        try:
            logger.warning(f"[{context}] 消息结构调试:")
            logger.warning(f"  消息类型: {type(message)}")

            if isinstance(message, dict):
                for key, value in message.items():
                    logger.warning(f"  键 '{key}': {type(value)} - {str(value)[:100]}...")

                    # 特别关注可能包含商品ID的字段
                    if key in ["1", "3"] and isinstance(value, dict):
                        logger.warning(f"    详细结构 '{key}':")
                        for sub_key, sub_value in value.items():
                            logger.warning(f"      '{sub_key}': {type(sub_value)} - {str(sub_value)[:50]}...")
            else:
                logger.warning(f"  消息内容: {str(message)[:200]}...")

        except Exception as e:
            logger.error(f"调试消息结构时发生错误: {self._safe_str(e)}")

    async def get_item_specific_reply(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str = None) -> str:
        """获取指定商品回复内容"""
        if not item_id:
            return None

        try:
            from db_manager import db_manager

            item_reply = db_manager.get_item_reply(self.cookie_id, item_id)
            if not item_reply or not item_reply.get('reply_content'):
                return None

            reply_content = item_reply['reply_content']
            logger.info(f"【{self.cookie_id}】使用指定商品回复: 商品ID={item_id}")

            try:
                formatted_reply = reply_content.format(
                    send_user_name=send_user_name,
                    send_user_id=send_user_id,
                    send_message=send_message,
                    item_id=item_id
                )
                logger.info(f"【{self.cookie_id}】指定商品回复内容: {formatted_reply}")
                return formatted_reply
            except Exception as format_error:
                logger.error(f"指定商品回复变量替换失败: {self._safe_str(format_error)}")
                return reply_content

        except Exception as e:
            logger.error(f"获取指定商品回复失败: {self._safe_str(e)}")
            return None

    async def get_default_reply(self, send_user_name: str, send_user_id: str, send_message: str, chat_id: str, item_id: str = None) -> str:
        """获取默认回复内容，支持变量替换和只回复一次功能"""
        try:
            from db_manager import db_manager

            # 获取当前账号的默认回复设置
            default_reply_settings = db_manager.get_default_reply(self.cookie_id)

            if not default_reply_settings or not default_reply_settings.get('enabled', False):
                logger.warning(f"账号 {self.cookie_id} 未启用默认回复")
                return None

            # 检查"只回复一次"功能
            if default_reply_settings.get('reply_once', False) and chat_id:
                # 检查是否已经回复过这个chat_id
                if db_manager.has_default_reply_record(self.cookie_id, chat_id):
                    logger.info(f"【{self.cookie_id}】chat_id {chat_id} 已使用过默认回复，跳过（只回复一次）")
                    return "SKIP_REPLY"

            reply_content = default_reply_settings.get('reply_content', '')
            if not reply_content or (reply_content and reply_content.strip() == ''):
                logger.info(f"账号 {self.cookie_id} 默认回复内容为空，不进行回复")
                return "EMPTY_REPLY"  # 返回特殊标记表示不回复

            # 进行变量替换
            try:
                formatted_reply = reply_content.format(
                    send_user_name=send_user_name,
                    send_user_id=send_user_id,
                    send_message=send_message
                )

                # 如果开启了"只回复一次"功能，记录这次回复
                if default_reply_settings.get('reply_once', False) and chat_id:
                    db_manager.add_default_reply_record(self.cookie_id, chat_id)
                    logger.info(f"【{self.cookie_id}】记录默认回复: chat_id={chat_id}")

                logger.info(f"【{self.cookie_id}】使用默认回复: {formatted_reply}")
                return formatted_reply
            except Exception as format_error:
                logger.error(f"默认回复变量替换失败: {self._safe_str(format_error)}")
                # 如果变量替换失败，返回原始内容
                return reply_content

        except Exception as e:
            logger.error(f"获取默认回复失败: {self._safe_str(e)}")
            return None

    async def get_keyword_reply(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str = None) -> str:
        """获取关键词匹配回复（支持商品ID优先匹配和图片类型）"""
        try:
            from db_manager import db_manager

            # 获取当前账号的关键词列表（包含类型信息）
            keywords = db_manager.get_keywords_with_type(self.cookie_id)

            if not keywords:
                logger.warning(f"账号 {self.cookie_id} 没有配置关键词")
                return None

            # 1. 如果有商品ID，优先匹配该商品ID对应的关键词
            if item_id:
                for keyword_data in keywords:
                    keyword = keyword_data['keyword']
                    reply = keyword_data['reply']
                    keyword_item_id = keyword_data['item_id']
                    keyword_type = keyword_data.get('type', 'text')
                    image_url = keyword_data.get('image_url')

                    if keyword_item_id == item_id and keyword.lower() in send_message.lower():
                        logger.info(f"商品ID关键词匹配成功: 商品{item_id} '{keyword}' (类型: {keyword_type})")

                        # 根据关键词类型处理
                        if keyword_type == 'image' and image_url:
                            # 图片类型关键词，发送图片
                            return await self._handle_image_keyword(keyword, image_url, send_user_name, send_user_id, send_message)
                        else:
                            # 文本类型关键词，检查回复内容是否为空
                            if not reply or (reply and reply.strip() == ''):
                                logger.info(f"商品ID关键词 '{keyword}' 回复内容为空，不进行回复")
                                return "EMPTY_REPLY"  # 返回特殊标记表示匹配到但不回复

                            # 进行变量替换
                            try:
                                formatted_reply = reply.format(
                                    send_user_name=send_user_name,
                                    send_user_id=send_user_id,
                                    send_message=send_message
                                )
                                logger.info(f"商品ID文本关键词回复: {formatted_reply}")
                                return formatted_reply
                            except Exception as format_error:
                                logger.error(f"关键词回复变量替换失败: {self._safe_str(format_error)}")
                                # 如果变量替换失败，返回原始内容
                                return reply

            # 2. 如果商品ID匹配失败或没有商品ID，匹配没有商品ID的通用关键词
            for keyword_data in keywords:
                keyword = keyword_data['keyword']
                reply = keyword_data['reply']
                keyword_item_id = keyword_data['item_id']
                keyword_type = keyword_data.get('type', 'text')
                image_url = keyword_data.get('image_url')

                if not keyword_item_id and keyword.lower() in send_message.lower():
                    logger.info(f"通用关键词匹配成功: '{keyword}' (类型: {keyword_type})")

                    # 根据关键词类型处理
                    if keyword_type == 'image' and image_url:
                        # 图片类型关键词，发送图片
                        return await self._handle_image_keyword(keyword, image_url, send_user_name, send_user_id, send_message)
                    else:
                        # 文本类型关键词，检查回复内容是否为空
                        if not reply or (reply and reply.strip() == ''):
                            logger.info(f"通用关键词 '{keyword}' 回复内容为空，不进行回复")
                            return "EMPTY_REPLY"  # 返回特殊标记表示匹配到但不回复

                        # 进行变量替换
                        try:
                            formatted_reply = reply.format(
                                send_user_name=send_user_name,
                                send_user_id=send_user_id,
                                send_message=send_message
                            )
                            logger.info(f"通用文本关键词回复: {formatted_reply}")
                            return formatted_reply
                        except Exception as format_error:
                            logger.error(f"关键词回复变量替换失败: {self._safe_str(format_error)}")
                            # 如果变量替换失败，返回原始内容
                            return reply

            logger.warning(f"未找到匹配的关键词: {send_message}")
            return None

        except Exception as e:
            logger.error(f"获取关键词回复失败: {self._safe_str(e)}")
            return None

    async def _handle_image_keyword(self, keyword: str, image_url: str, send_user_name: str, send_user_id: str, send_message: str) -> str:
        """处理图片类型关键词"""
        try:
            # 检查图片URL类型
            if self._is_cdn_url(image_url):
                # 已经是CDN链接，直接使用
                logger.info(f"使用已有的CDN图片链接: {image_url}")
                return f"__IMAGE_SEND__{image_url}"

            elif image_url.startswith('/static/uploads/') or image_url.startswith('static/uploads/'):
                # 本地图片，需要上传到闲鱼CDN
                local_image_path = image_url.replace('/static/uploads/', 'static/uploads/')
                if os.path.exists(local_image_path):
                    logger.info(f"准备上传本地图片到闲鱼CDN: {local_image_path}")

                    # 使用图片上传器上传到闲鱼CDN
                    from utils.image_uploader import ImageUploader
                    uploader = ImageUploader(self.cookies_str)

                    async with uploader:
                        cdn_url = await uploader.upload_image(local_image_path)
                        if cdn_url:
                            logger.info(f"图片上传成功，CDN URL: {cdn_url}")
                            # 更新数据库中的图片URL为CDN URL
                            await self._update_keyword_image_url(keyword, cdn_url)
                            image_url = cdn_url
                        else:
                            logger.error(f"图片上传失败: {local_image_path}")
                            logger.error(f"❌ Cookie可能已失效！请检查配置并更新Cookie")
                            return f"抱歉，图片发送失败（Cookie可能已失效，请检查日志）"
                else:
                    logger.error(f"本地图片文件不存在: {local_image_path}")
                    return f"抱歉，图片文件不存在。"

            else:
                # 其他类型的URL（可能是外部链接），直接使用
                logger.info(f"使用外部图片链接: {image_url}")

            # 发送图片（这里返回特殊标记，在调用处处理实际发送）
            return f"__IMAGE_SEND__{image_url}"

        except Exception as e:
            logger.error(f"处理图片关键词失败: {e}")
            return f"抱歉，图片发送失败: {str(e)}"

    def _is_cdn_url(self, url: str) -> bool:
        """检查URL是否是闲鱼CDN链接"""
        if not url:
            return False

        # 闲鱼CDN域名列表
        cdn_domains = [
            'gw.alicdn.com',
            'img.alicdn.com',
            'cloud.goofish.com',
            'goofish.com',
            'taobaocdn.com',
            'tbcdn.cn',
            'aliimg.com'
        ]

        # 检查是否包含CDN域名
        url_lower = url.lower()
        for domain in cdn_domains:
            if domain in url_lower:
                return True

        # 检查是否是HTTPS链接且包含图片特征
        if url_lower.startswith('https://') and any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
            return True

        return False

    async def _update_keyword_image_url(self, keyword: str, new_image_url: str):
        """更新关键词的图片URL"""
        try:
            from db_manager import db_manager
            success = db_manager.update_keyword_image_url(self.cookie_id, keyword, new_image_url)
            if success:
                logger.info(f"图片URL已更新: {keyword} -> {new_image_url}")
            else:
                logger.warning(f"图片URL更新失败: {keyword}")
        except Exception as e:
            logger.error(f"更新关键词图片URL失败: {e}")

    async def _update_card_image_url(self, card_id: int, new_image_url: str):
        """更新卡券的图片URL"""
        try:
            from db_manager import db_manager
            success = db_manager.update_card_image_url(card_id, new_image_url)
            if success:
                logger.info(f"卡券图片URL已更新: 卡券ID={card_id} -> {new_image_url}")
            else:
                logger.warning(f"卡券图片URL更新失败: 卡券ID={card_id}")
        except Exception as e:
            logger.error(f"更新卡券图片URL失败: {e}")

    async def get_ai_reply(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str, chat_id: str):
        """获取AI回复"""
        try:
            if self._check_buyer_blacklist_for_action(
                buyer_id=send_user_id,
                item_id=item_id,
                buyer_nick=send_user_name,
                action='AI回复',
                log_delivery=False,
            ):
                return None

            from ai_reply_engine import ai_reply_engine

            # 检查是否启用AI回复
            if not ai_reply_engine.is_ai_enabled(self.cookie_id):
                logger.warning(f"账号 {self.cookie_id} 未启用AI回复")
                return None

            # 从数据库获取商品信息
            from db_manager import db_manager
            item_info_raw = db_manager.get_item_info(self.cookie_id, item_id)

            if not item_info_raw:
                logger.warning(f"数据库中无商品信息: {item_id}")
                # 使用默认商品信息
                item_info = {
                    'title': '商品信息获取失败',
                    'price': 0,
                    'desc': '暂无商品描述'
                }
            else:
                # 解析数据库中的商品信息
                item_info = {
                    'title': item_info_raw.get('item_title', '未知商品'),
                    'price': self._parse_price(item_info_raw.get('item_price', '0')),
                    'desc': item_info_raw.get('item_detail', '暂无商品描述')
                }

            # 生成AI回复
            # 由于外部已实现防抖机制，跳过内部等待（skip_wait=True）
            reply = ai_reply_engine.generate_reply(
                message=send_message,
                item_info=item_info,
                chat_id=chat_id,
                cookie_id=self.cookie_id,
                user_id=send_user_id,
                item_id=item_id,
                skip_wait=True  # 跳过内部等待，因为外部已实现防抖
            )

            if reply:
                logger.info(f"【{self.cookie_id}】AI回复生成成功: {reply}")
                return reply
            else:
                logger.warning(f"AI回复生成失败")
                return None

        except Exception as e:
            logger.error(f"获取AI回复失败: {self._safe_str(e)}")
            return None

    def _parse_price(self, price_str: str) -> float:
        """解析价格字符串为数字"""
        try:
            if not price_str:
                return 0.0
            # 移除非数字字符，保留小数点
            price_clean = re.sub(r'[^\d.]', '', str(price_str))
            return float(price_clean) if price_clean else 0.0
        except Exception:
            return 0.0

    def _get_notification_template(self, template_type: str) -> str:
        """获取通知模板，如果没有自定义模板则返回默认模板"""
        return get_notification_template_text(template_type)

    def _format_template(self, template: str, **kwargs) -> str:
        """格式化模板，将变量替换为实际值"""
        return format_notification_template(template, **kwargs)

    async def send_notification(self, send_user_name: str, send_user_id: str, send_message: str, item_id: str = None, chat_id: str = None):
        """发送消息通知"""
        try:
            import hashlib

            # 过滤系统默认消息，不发送通知
            system_messages = [
                '发来一条消息',
                '发来一条新消息'
            ]

            if send_message in system_messages:
                logger.warning(f"📱 系统消息不发送通知: {send_message}")
                return

            # 生成通知的唯一标识（基于消息内容、chat_id、send_user_id）
            # 用于防重复发送
            notification_key = f"{chat_id or 'unknown'}_{send_user_id}_{send_message}"
            notification_hash = hashlib.md5(notification_key.encode('utf-8')).hexdigest()
            reservation_key = f"msg:{notification_hash}"
            
            # 使用异步锁保护防重复检查，确保并发安全
            async with self.notification_lock:
                # 检查是否在冷却时间内已发送过相同的通知
                current_time = time.time()
                if notification_hash in self.last_notification_time:
                    time_since_last = current_time - self.last_notification_time[notification_hash]
                    if time_since_last < self.notification_cooldown:
                        remaining_seconds = int(self.notification_cooldown - time_since_last)
                        logger.warning(f"📱 通知在冷却期内（剩余 {remaining_seconds} 秒），跳过重复发送 - 账号: {self.cookie_id}, 买家: {send_user_name}, 消息: {send_message[:30]}...")
                        return
                if reservation_key in self.pending_notification_keys:
                    logger.warning(f"📱 相同消息通知正在发送中，跳过重复发送 - 账号: {self.cookie_id}, 买家: {send_user_name}")
                    return
                self.pending_notification_keys.add(reservation_key)

            try:
                logger.info(f"📱 开始发送消息通知 - 账号: {self.cookie_id}, 买家: {send_user_name}")

                notification_msg = render_notification_template(
                    'message',
                    account_id=self.cookie_id,
                    buyer_name=send_user_name,
                    buyer_id=send_user_id,
                    item_id=item_id or '未知',
                    chat_id=chat_id or '未知',
                    message=send_message,
                    time=time.strftime('%Y-%m-%d %H:%M:%S')
                )

                notification_sent = await dispatch_account_notifications(
                    self.cookie_id,
                    notification_msg,
                    title='接收消息通知',
                    notification_type='message',
                )

                if not notification_sent:
                    logger.warning(f"📱 消息通知未发送成功，不进入冷却 - 账号: {self.cookie_id}, 买家: {send_user_name}")
                    return

                async with self.notification_lock:
                    sent_time = time.time()
                    self.last_notification_time[notification_hash] = sent_time
                    expired_keys = [
                        key for key, timestamp in self.last_notification_time.items()
                        if sent_time - timestamp > 3600
                    ]
                    for key in expired_keys:
                        del self.last_notification_time[key]
            finally:
                async with self.notification_lock:
                    self.pending_notification_keys.discard(reservation_key)

        except Exception as e:
            logger.error(f"📱 处理消息通知失败: {self._safe_str(e)}")
            import traceback
            logger.error(f"📱 详细错误信息: {traceback.format_exc()}")

    def _parse_notification_config(self, config: str) -> dict:
        """解析通知配置数据"""
        try:
            import json
            # 尝试解析JSON格式的配置
            return json.loads(config)
        except (json.JSONDecodeError, TypeError):
            # 兼容旧格式（直接字符串）
            return {"config": config}

    async def _send_qq_notification(self, config_data: dict, message: str):
        """发送QQ通知"""
        try:
            import aiohttp

            logger.info(f"📱 QQ通知 - 开始处理配置数据: {config_data}")

            # 解析配置（QQ号码）
            qq_number = config_data.get('qq_number') or config_data.get('config', '')
            qq_number = qq_number.strip() if qq_number else ''

            logger.info(f"📱 QQ通知 - 解析到QQ号码: {qq_number}")

            if not qq_number:
                logger.warning("📱 QQ通知 - QQ号码配置为空，无法发送通知")
                return False

            # 构建请求URL
            api_url = "http://36.111.68.231:3000/sendPrivateMsg"
            params = {
                'qq': qq_number,
                'msg': message
            }

            logger.info(f"📱 QQ通知 - 请求URL: {api_url}")
            logger.info(f"📱 QQ通知 - 请求参数: qq={qq_number}, msg长度={len(message)}")

            # 发送GET请求
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, timeout=10) as response:
                    response_text = await response.text()
                    logger.info(f"📱 QQ通知 - 响应状态: {response.status}")

                    # 需求：502 视为成功，且不打印返回内容
                    if response.status == 502:
                        logger.info(f"📱 QQ通知发送成功: {qq_number} (状态码: {response.status})")
                        return True
                    elif response.status == 200:
                        logger.info(f"📱 QQ通知发送成功: {qq_number} (状态码: {response.status})")
                        logger.warning(f"📱 QQ通知 - 响应内容: {response_text}")
                        return True
                    else:
                        logger.warning(f"📱 QQ通知发送失败: HTTP {response.status}")
                        logger.warning(f"📱 QQ通知 - 响应内容: {response_text}")
                        return False

        except Exception as e:
            logger.error(f"📱 发送QQ通知异常: {self._safe_str(e)}")
            import traceback
            logger.error(f"📱 QQ通知异常详情: {traceback.format_exc()}")
            return False

    async def _send_dingtalk_notification(self, config_data: dict, message: str):
        """发送钉钉通知"""
        try:
            import aiohttp
            import json
            import hmac
            import hashlib
            import base64
            import time

            # 解析配置
            webhook_url = config_data.get('webhook_url') or config_data.get('config', '')
            secret = config_data.get('secret', '')

            webhook_url = webhook_url.strip() if webhook_url else ''
            if not webhook_url:
                logger.warning("钉钉通知配置为空")
                return False

            # 如果有加签密钥，生成签名
            if secret:
                timestamp = str(round(time.time() * 1000))
                secret_enc = secret.encode('utf-8')
                string_to_sign = f'{timestamp}\n{secret}'
                string_to_sign_enc = string_to_sign.encode('utf-8')
                hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
                sign = base64.b64encode(hmac_code).decode('utf-8')
                webhook_url += f'&timestamp={timestamp}&sign={sign}'

            data = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "闲鱼管理系统通知",
                    "text": message
                }
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data, timeout=10) as response:
                    if response.status == 200:
                        logger.info(f"钉钉通知发送成功")
                        return True
                    else:
                        logger.warning(f"钉钉通知发送失败: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"发送钉钉通知异常: {self._safe_str(e)}")
            return False

    async def _send_feishu_notification(self, config_data: dict, message: str):
        """发送飞书通知"""
        try:
            import aiohttp
            import json
            import hmac
            import hashlib
            import base64

            logger.info(f"📱 飞书通知 - 开始处理配置数据: {config_data}")

            # 解析配置
            webhook_url = config_data.get('webhook_url', '')
            secret = config_data.get('secret', '')

            logger.info(f"📱 飞书通知 - Webhook URL: {webhook_url[:50]}...")
            logger.info(f"📱 飞书通知 - 是否有签名密钥: {'是' if secret else '否'}")

            if not webhook_url:
                logger.warning("📱 飞书通知 - Webhook URL配置为空，无法发送通知")
                return False

            # 如果有加签密钥，生成签名
            timestamp = str(int(time.time()))
            sign = ""

            if secret:
                string_to_sign = f'{timestamp}\n{secret}'
                hmac_code = hmac.new(
                    string_to_sign.encode('utf-8'),
                    ''.encode('utf-8'),
                    digestmod=hashlib.sha256
                ).digest()
                sign = base64.b64encode(hmac_code).decode('utf-8')
                logger.info(f"📱 飞书通知 - 已生成签名")

            # 构建请求数据
            data = {
                "msg_type": "text",
                "content": {
                    "text": message
                },
                "timestamp": timestamp
            }

            # 如果有签名，添加到请求数据中
            if sign:
                data["sign"] = sign

            logger.info(f"📱 飞书通知 - 请求数据构建完成")

            # 发送POST请求
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data, timeout=10) as response:
                    response_text = await response.text()
                    logger.info(f"📱 飞书通知 - 响应状态: {response.status}")
                    logger.info(f"📱 飞书通知 - 响应内容: {response_text}")

                    if response.status == 200:
                        try:
                            response_json = json.loads(response_text)
                            if response_json.get('code') == 0:
                                logger.info(f"📱 飞书通知发送成功")
                                return True
                            else:
                                logger.warning(f"📱 飞书通知发送失败: {response_json.get('msg', '未知错误')}")
                                return False
                        except json.JSONDecodeError:
                            logger.info(f"📱 飞书通知发送成功（响应格式异常）")
                            return True
                    else:
                        logger.warning(f"📱 飞书通知发送失败: HTTP {response.status}, 响应: {response_text}")
                        return False

        except Exception as e:
            logger.error(f"📱 发送飞书通知异常: {self._safe_str(e)}")
            import traceback
            logger.error(f"📱 飞书通知异常详情: {traceback.format_exc()}")
            return False

    async def _send_bark_notification(self, config_data: dict, message: str):
        """发送Bark通知"""
        try:
            import aiohttp
            import json
            from urllib.parse import quote

            logger.info(f"📱 Bark通知 - 开始处理配置数据: {config_data}")

            # 解析配置
            server_url = config_data.get('server_url', 'https://api.day.app').rstrip('/')
            device_key = config_data.get('device_key', '')
            title = config_data.get('title', '闲鱼管理系统通知')
            sound = config_data.get('sound', 'default')
            icon = config_data.get('icon', '')
            group = config_data.get('group', 'xianyu')
            url = config_data.get('url', '')

            logger.info(f"📱 Bark通知 - 服务器: {server_url}")
            logger.info(f"📱 Bark通知 - 设备密钥: {device_key[:10]}..." if device_key else "📱 Bark通知 - 设备密钥: 未设置")
            logger.info(f"📱 Bark通知 - 标题: {title}")

            if not device_key:
                logger.warning("📱 Bark通知 - 设备密钥配置为空，无法发送通知")
                return False

            # 构建请求URL和数据
            # Bark支持两种方式：URL路径方式和POST JSON方式
            # 这里使用POST JSON方式，更灵活且支持更多参数

            api_url = f"{server_url}/push"

            # 构建请求数据
            data = {
                "device_key": device_key,
                "title": title,
                "body": message,
                "sound": sound,
                "group": group
            }

            # 可选参数
            if icon:
                data["icon"] = icon
            if url:
                data["url"] = url

            logger.info(f"📱 Bark通知 - API地址: {api_url}")
            logger.info(f"📱 Bark通知 - 请求数据构建完成")

            # 发送POST请求
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json=data, timeout=10) as response:
                    response_text = await response.text()
                    logger.info(f"📱 Bark通知 - 响应状态: {response.status}")
                    logger.info(f"📱 Bark通知 - 响应内容: {response_text}")

                    if response.status == 200:
                        try:
                            response_json = json.loads(response_text)
                            if response_json.get('code') == 200:
                                logger.info(f"📱 Bark通知发送成功")
                                return True
                            else:
                                logger.warning(f"📱 Bark通知发送失败: {response_json.get('message', '未知错误')}")
                                return False
                        except json.JSONDecodeError:
                            # 某些Bark服务器可能返回纯文本
                            if 'success' in response_text.lower() or 'ok' in response_text.lower():
                                logger.info(f"📱 Bark通知发送成功")
                                return True
                            else:
                                logger.warning(f"📱 Bark通知响应格式异常: {response_text}")
                                return False
                    else:
                        logger.warning(f"📱 Bark通知发送失败: HTTP {response.status}, 响应: {response_text}")
                        return False

        except Exception as e:
            logger.error(f"📱 发送Bark通知异常: {self._safe_str(e)}")
            import traceback
            logger.error(f"📱 Bark通知异常详情: {traceback.format_exc()}")
            return False

    async def _send_email_notification(self, config_data: dict, message: str, attachment_path: str = None):
        """发送邮件通知（支持附件）
        
        Args:
            config_data: 邮件配置
            message: 邮件正文
            attachment_path: 附件文件路径（可选）
        """
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from email.mime.image import MIMEImage
            import os

            # 解析配置
            smtp_server = config_data.get('smtp_server', '')
            smtp_port = int(config_data.get('smtp_port', 587))
            email_user = config_data.get('email_user', '')
            email_password = config_data.get('email_password', '')
            recipient_email = config_data.get('recipient_email', '')
            smtp_use_tls = config_data.get('smtp_use_tls', smtp_port == 587)  # 修复：添加变量定义

            if not all([smtp_server, email_user, email_password, recipient_email]):
                logger.warning("邮件通知配置不完整")
                return False

            # 创建邮件
            msg = MIMEMultipart()
            msg['From'] = email_user
            msg['To'] = recipient_email
            msg['Subject'] = "闲鱼管理系统通知"

            # 添加邮件正文
            msg.attach(MIMEText(message, 'plain', 'utf-8'))

            # 添加附件（如果有）
            if attachment_path and os.path.exists(attachment_path):
                try:
                    with open(attachment_path, 'rb') as f:
                        img_data = f.read()
                    
                    # 根据文件扩展名判断MIME类型
                    filename = os.path.basename(attachment_path)
                    if attachment_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        img = MIMEImage(img_data)
                        img.add_header('Content-Disposition', 'attachment', filename=filename)
                        msg.attach(img)
                        logger.info(f"已添加图片附件: {filename}")
                    else:
                        from email.mime.application import MIMEApplication
                        attach = MIMEApplication(img_data)
                        attach.add_header('Content-Disposition', 'attachment', filename=filename)
                        msg.attach(attach)
                        logger.info(f"已添加附件: {filename}")
                except Exception as attach_error:
                    logger.error(f"添加邮件附件失败: {self._safe_str(attach_error)}")

            # 发送邮件
            server = None
            try:
                if smtp_port == 465:
                    # 使用SSL连接（端口465）
                    server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
                else:
                    # 使用普通连接，然后升级到TLS（端口587）
                    server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
                    if smtp_use_tls:
                        server.starttls()
                
                # 尝试登录
                try:
                    server.login(email_user, email_password)
                except smtplib.SMTPAuthenticationError as auth_error:
                    error_code = auth_error.smtp_code if hasattr(auth_error, 'smtp_code') else None
                    error_msg = str(auth_error)
                    
                    # 提供详细的错误提示
                    logger.error(f"邮件SMTP认证失败 (错误码: {error_code})")
                    logger.error(f"邮箱地址: {email_user}")
                    logger.error(f"SMTP服务器: {smtp_server}:{smtp_port}")
                    logger.error(f"错误详情: {error_msg}")
                    
                    # 根据常见错误提供解决建议
                    suggestions = []
                    if 'qq.com' in email_user.lower() or 'qq' in smtp_server.lower():
                        suggestions.append("QQ邮箱需要使用授权码而不是登录密码")
                        suggestions.append("请到QQ邮箱设置 -> 账户 -> 开启SMTP服务 -> 生成授权码")
                    elif 'gmail.com' in email_user.lower() or 'gmail' in smtp_server.lower():
                        suggestions.append("Gmail需要使用应用专用密码")
                        suggestions.append("请到Google账户 -> 安全性 -> 两步验证 -> 应用专用密码")
                        suggestions.append("或启用'允许不够安全的应用访问'（不推荐）")
                    elif '163.com' in email_user.lower() or '126.com' in email_user.lower() or 'yeah.net' in email_user.lower():
                        suggestions.append("网易邮箱需要使用授权码")
                        suggestions.append("请到邮箱设置 -> POP3/SMTP/IMAP -> 开启SMTP服务 -> 生成授权码")
                    else:
                        suggestions.append("请检查邮箱密码/授权码是否正确")
                        suggestions.append("某些邮箱服务商需要使用授权码而不是登录密码")
                        suggestions.append("请查看邮箱服务商的SMTP设置说明")
                    
                    if suggestions:
                        logger.error("解决建议:")
                        for i, suggestion in enumerate(suggestions, 1):
                            logger.error(f"  {i}. {suggestion}")
                    
                    raise  # 重新抛出异常
                
                server.send_message(msg)
                logger.info(f"邮件通知发送成功: {recipient_email}")
                return True

            finally:
                # 确保关闭连接
                if server:
                    try:
                        server.quit()
                    except Exception:
                        try:
                            server.close()
                        except Exception:
                            pass

        except smtplib.SMTPAuthenticationError:
            # 认证错误已在上面处理，这里不再重复记录
            return False
        except smtplib.SMTPException as smtp_error:
            logger.error(f"SMTP协议错误: {self._safe_str(smtp_error)}")
            logger.error(f"SMTP服务器: {smtp_server}:{smtp_port}")
            logger.error(f"请检查SMTP服务器地址和端口配置是否正确")
            return False
        except Exception as e:
            logger.error(f"发送邮件通知异常: {self._safe_str(e)}")
            import traceback
            logger.error(f"邮件发送详细错误: {traceback.format_exc()}")
            return False

    async def _send_webhook_notification(self, config_data: dict, message: str):
        """发送Webhook通知"""
        try:
            import aiohttp
            import json

            # 解析配置
            webhook_url = config_data.get('webhook_url', '')
            http_method = config_data.get('http_method', 'POST').upper()
            headers_str = config_data.get('headers', '{}')

            if not webhook_url:
                logger.warning("Webhook通知配置为空")
                return False

            # 解析自定义请求头
            try:
                custom_headers = json.loads(headers_str) if headers_str else {}
            except json.JSONDecodeError:
                custom_headers = {}

            # 设置默认请求头
            headers = {'Content-Type': 'application/json'}
            headers.update(custom_headers)

            # 构建请求数据
            data = {
                'message': message,
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'source': 'xianyu-auto-reply'
            }

            async with aiohttp.ClientSession() as session:
                if http_method == 'POST':
                    async with session.post(webhook_url, json=data, headers=headers, timeout=10) as response:
                        if response.status == 200:
                            logger.info(f"Webhook通知发送成功")
                            return True
                        else:
                            logger.warning(f"Webhook通知发送失败: {response.status}")
                            return False
                elif http_method == 'PUT':
                    async with session.put(webhook_url, json=data, headers=headers, timeout=10) as response:
                        if response.status == 200:
                            logger.info(f"Webhook通知发送成功")
                            return True
                        else:
                            logger.warning(f"Webhook通知发送失败: {response.status}")
                            return False
                else:
                    logger.warning(f"不支持的HTTP方法: {http_method}")
                    return False

        except Exception as e:
            logger.error(f"发送Webhook通知异常: {self._safe_str(e)}")
            return False

    async def _send_wechat_notification(self, config_data: dict, message: str):
        """发送微信通知"""
        try:
            import aiohttp
            import json

            # 解析配置
            webhook_url = config_data.get('webhook_url', '')

            if not webhook_url:
                logger.warning("微信通知配置为空")
                return False

            data = {
                "msgtype": "text",
                "text": {
                    "content": message
                }
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=data, timeout=10) as response:
                    if response.status == 200:
                        logger.info(f"微信通知发送成功")
                        return True
                    else:
                        logger.warning(f"微信通知发送失败: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"发送微信通知异常: {self._safe_str(e)}")
            return False

    async def _send_telegram_notification(self, config_data: dict, message: str):
        """发送Telegram通知"""
        try:
            import aiohttp

            # 解析配置
            bot_token = config_data.get('bot_token', '')
            chat_id = config_data.get('chat_id', '')

            if not all([bot_token, chat_id]):
                logger.warning("Telegram通知配置不完整")
                return False

            # 构建API URL
            api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

            data = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json=data, timeout=10) as response:
                    if response.status == 200:
                        logger.info(f"Telegram通知发送成功")
                        return True
                    else:
                        logger.warning(f"Telegram通知发送失败: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"发送Telegram通知异常: {self._safe_str(e)}")
            return False

    async def send_token_refresh_notification(
        self,
        error_message: str,
        notification_type: str = "token_refresh",
        chat_id: str = None,
        attachment_path: str = None,
        verification_url: str = None,
        verification_type: str = None,
    ):
        """发送Token刷新异常通知（带防重复机制，支持附件）
        
        Args:
            error_message: 错误消息
            notification_type: 通知类型
            chat_id: 聊天ID（可选）
            attachment_path: 附件路径（可选，用于发送截图）
            verification_type: 验证类型（可选，优先使用调用方已识别的真实类型）
        """
        try:
            # 检查是否是正常的令牌过期，这种情况不需要发送通知
            if notification_type != "token_scheduled_refresh_failed" and self._is_normal_token_expiry(error_message):
                logger.warning(f"检测到正常的令牌过期，跳过通知: {error_message}")
                return

            notification_key = f"token:{notification_type}"

            # 为Token刷新异常通知使用特殊的3小时冷却时间
            # 基于错误消息内容判断是否为Token相关异常
            if notification_type == "message_stream_stale":
                cooldown_time = self.message_stream_notification_cooldown
                cooldown_desc = f"{max(1, int(cooldown_time // 60))}分钟"
            elif self._is_token_related_error(error_message):
                cooldown_time = self.token_refresh_notification_cooldown
                cooldown_desc = "3小时"
            else:
                cooldown_time = self.notification_cooldown
                cooldown_desc = f"{self.notification_cooldown // 60}分钟"

            async with self.notification_lock:
                current_time = time.time()
                last_time = self.last_notification_time.get(notification_key, 0)
                if notification_key in self.pending_notification_keys:
                    logger.warning(f"Token刷新通知正在发送中，跳过重复发送: {notification_type}")
                    return
                if current_time - last_time < cooldown_time:
                    remaining_time = cooldown_time - (current_time - last_time)
                    remaining_hours = int(remaining_time // 3600)
                    remaining_minutes = int((remaining_time % 3600) // 60)
                    remaining_seconds = int(remaining_time % 60)

                    if remaining_hours > 0:
                        time_desc = f"{remaining_hours}小时{remaining_minutes}分钟"
                    elif remaining_minutes > 0:
                        time_desc = f"{remaining_minutes}分钟{remaining_seconds}秒"
                    else:
                        time_desc = f"{remaining_seconds}秒"

                    logger.warning(f"Token刷新通知在冷却期内，跳过发送: {notification_type} (还需等待 {time_desc})")
                    return
                self.pending_notification_keys.add(notification_key)

            # 构造通知消息（使用模板）
            if notification_type in ("slider_success", "slider_recovered_success"):
                slider_status_text = (
                    "账号会话已恢复"
                    if notification_type == "slider_recovered_success"
                    else "cookies已自动更新到数据库"
                )
                notification_msg = render_notification_template(
                    'slider_success',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    status_text=slider_status_text
                )
            elif "密码登录成功" in error_message or notification_type == "password_login_success":
                notification_msg = render_notification_template(
                    'password_login_success',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    cookie_count='已获取'
                )
            elif "刷新Cookie成功" in error_message or notification_type == "cookie_refresh_success":
                notification_msg = render_notification_template(
                    'cookie_refresh_success',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    cookie_count='已获取'
                )
            elif "人脸验证" in error_message or "短信验证" in error_message or "二维码验证" in error_message or "身份验证" in error_message or (verification_url and "passport" in verification_url):
                notification_msg = build_face_verify_notification(
                    account_id=self.cookie_id,
                    time_text=time.strftime('%Y-%m-%d %H:%M:%S'),
                    verification_type=verification_type or guess_verification_type(error_message, verification_url),
                    verification_url=verification_url or '',
                    error_message=error_message,
                    has_screenshot=bool(attachment_path),
                )
            elif verification_url:
                notification_msg = render_notification_template(
                    'token_refresh',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    error_message=error_message,
                    verification_url=verification_url
                )
            else:
                notification_msg = render_notification_template(
                    'token_refresh',
                    account_id=self.cookie_id,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'),
                    error_message=error_message,
                    verification_url='无'
                )

            logger.info(f"准备发送Token刷新异常通知: {self.cookie_id}")

            notification_sent = await dispatch_account_notifications(
                self.cookie_id,
                notification_msg,
                title='闲鱼管理系统通知',
                notification_type=notification_type,
                attachment_path=attachment_path,
            )

            # 如果成功发送了通知，更新最后发送时间
            if notification_sent:
                current_time = time.time()
                async with self.notification_lock:
                    self.last_notification_time[notification_key] = current_time

                # 根据错误消息内容使用不同的冷却时间
                if notification_type == "message_stream_stale":
                    next_send_time = current_time + self.message_stream_notification_cooldown
                    cooldown_desc = f"{max(1, int(self.message_stream_notification_cooldown // 60))}分钟"
                elif self._is_token_related_error(error_message):
                    next_send_time = current_time + self.token_refresh_notification_cooldown
                    cooldown_desc = "3小时"
                else:
                    next_send_time = current_time + self.notification_cooldown
                    cooldown_desc = f"{self.notification_cooldown // 60}分钟"

                next_send_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(next_send_time))
                logger.info(f"Token刷新通知已发送，下次可发送时间: {next_send_time_str} (冷却时间: {cooldown_desc})")
            else:
                logger.warning(f"【{self.cookie_id}】Token刷新通知未发送成功，不进入冷却: {notification_type}")

        except Exception as e:
            logger.error(f"处理Token刷新通知失败: {self._safe_str(e)}")
        finally:
            async with self.notification_lock:
                self.pending_notification_keys.discard(f"token:{notification_type}")

    def _is_normal_token_expiry(self, error_message: str) -> bool:
        """检查是否是正常的令牌过期或其他不需要通知的情况"""
        # 不需要发送通知的关键词
        no_notification_keywords = [
            # 正常的令牌过期
            'FAIL_SYS_TOKEN_EXOIRED::令牌过期',
            'FAIL_SYS_TOKEN_EXPIRED::令牌过期',
            'FAIL_SYS_TOKEN_EXOIRED',
            'FAIL_SYS_TOKEN_EXPIRED',
            '令牌过期',
            # Session过期（正常情况）
            'FAIL_SYS_SESSION_EXPIRED::Session过期',
            'FAIL_SYS_SESSION_EXPIRED',
            'Session过期',
            # Token定时刷新失败（会自动重试）
            'Token定时刷新失败，将自动重试',
            'Token定时刷新失败'
        ]

        # 检查错误消息是否包含不需要通知的关键词
        for keyword in no_notification_keywords:
            if keyword in error_message:
                return True

        return False

    def _is_token_related_error(self, error_message: str) -> bool:
        """检查是否是Token相关的错误，需要使用3小时冷却时间"""
        # Token相关错误的关键词
        token_error_keywords = [
            # Token刷新失败相关
            'Token刷新失败',
            'Token刷新异常',
            'token刷新失败',
            'token刷新异常',
            'TOKEN刷新失败',
            'TOKEN刷新异常',
            # 具体的Token错误信息
            'FAIL_SYS_USER_VALIDATE',
            'RGV587_ERROR',
            '哎哟喂,被挤爆啦',
            '请稍后重试',
            'punish?x5secdata',
            'captcha',
            # Token获取失败
            '无法获取有效token',
            '无法获取有效Token',
            'Token获取失败',
            'token获取失败',
            'TOKEN获取失败',
            # Token定时刷新失败
            'Token定时刷新失败',
            'token定时刷新失败',
            'TOKEN定时刷新失败',
            # 初始化Token失败
            '初始化时无法获取有效Token',
            '初始化时无法获取有效token',
            # 其他Token相关错误
            'accessToken',
            'access_token',
            '_m_h5_tk',
            'mtop.taobao.idlemessage.pc.login.token'
        ]

        # 检查错误消息是否包含Token相关的关键词
        error_message_lower = error_message.lower()
        for keyword in token_error_keywords:
            if keyword.lower() in error_message_lower:
                return True

        return False

    def _build_scheduled_token_refresh_error_message(self, last_refresh_status: str) -> str:
        """为定时Token刷新失败选择更准确的通知文案。"""
        if last_refresh_status == "account_risk_protected":
            return "检测到账号风控，系统已停止自动登录重试，请前往闲鱼APP处理后再手动启用账号"

        if last_refresh_status == "manual_verification_required":
            return "检测到需要人工验证，系统已自动暂停账号，请完成验证后再手动启用账号"

        if last_refresh_status in {"session_expired_after_slider", "session_expired_preflight"}:
            return "Session已过期，系统自动恢复失败，请重新登录"

        if last_refresh_status == "token_expired_recovery_failed":
            detail = (self.last_token_refresh_error_message or "").lower()
            if "session过期" in detail or "页面会话已失效" in detail:
                return "Session已过期，系统自动恢复失败，请重新登录"

        return "Token定时刷新失败，将自动重试"

    def _resolve_delivery_notification_buyer_name(
        self,
        buyer_name: Any = None,
        *,
        buyer_id: str = None,
        chat_id: str = None,
        order_id: str = None,
        log_prefix: str = "",
    ) -> str:
        """为自动发货通知解析可信买家昵称，避免使用“等待你发货”等系统标题。"""
        normalized_buyer_id = self._normalize_buyer_id_value(buyer_id)
        normalized_chat_id = str(chat_id or '').strip()

        try:
            if order_id:
                order_info = db_manager.get_order_by_id(str(order_id).strip())
                if order_info:
                    order_cookie_id = str(order_info.get('cookie_id') or '').strip()
                    if not order_cookie_id or order_cookie_id == str(self.cookie_id).strip():
                        order_buyer_nick = self._sanitize_buyer_nick(
                            order_info.get('buyer_nick'),
                            source='delivery_notification_order',
                            log_prefix=log_prefix,
                        )
                        if order_buyer_nick:
                            return order_buyer_nick

                        if not normalized_buyer_id:
                            normalized_buyer_id = self._normalize_buyer_id_value(order_info.get('buyer_id'))

                        if not normalized_chat_id:
                            sid = str(order_info.get('sid') or '').strip()
                            normalized_chat_id = sid.split('@')[0].strip() if sid else ''

            if normalized_chat_id:
                chat_messages = db_manager.get_chat_messages(self.cookie_id, normalized_chat_id, limit=80)
                for chat_message in reversed(chat_messages or []):
                    if int(chat_message.get('direction') or 0) != 2:
                        continue

                    sender_id = self._normalize_buyer_id_value(chat_message.get('sender_id'))
                    if sender_id and sender_id == self.myid:
                        continue
                    if normalized_buyer_id and sender_id and sender_id != normalized_buyer_id:
                        continue

                    chat_buyer_nick = self._sanitize_buyer_nick(
                        chat_message.get('sender_name'),
                        source='delivery_notification_chat',
                        log_prefix=log_prefix,
                    )
                    if chat_buyer_nick:
                        return chat_buyer_nick

            if normalized_buyer_id:
                recent_order = db_manager.get_recent_order_by_buyer_id(
                    normalized_buyer_id,
                    cookie_id=self.cookie_id,
                    minutes=24 * 60,
                )
                if recent_order:
                    recent_buyer_nick = self._sanitize_buyer_nick(
                        recent_order.get('buyer_nick'),
                        source='delivery_notification_recent_order',
                        log_prefix=log_prefix,
                    )
                    if recent_buyer_nick:
                        return recent_buyer_nick
        except Exception as resolve_error:
            logger.warning(f"{log_prefix} 自动发货通知买家昵称解析失败: {self._safe_str(resolve_error)}")

        fallback_buyer_name = self._sanitize_buyer_nick(
            buyer_name,
            source='delivery_notification_raw',
            log_prefix=log_prefix,
        )
        return fallback_buyer_name or '买家'

    async def send_delivery_failure_notification(
        self,
        send_user_name: str,
        send_user_id: str,
        item_id: str,
        error_message: str,
        chat_id: str = None,
        order_id: str = None,
    ):
        """发送自动发货通知。"""
        try:
            resolved_buyer_name = self._resolve_delivery_notification_buyer_name(
                send_user_name,
                buyer_id=send_user_id,
                chat_id=chat_id,
                order_id=order_id,
                log_prefix=f"【{self.cookie_id}】",
            )
            notification_message = render_notification_template(
                'delivery',
                account_id=self.cookie_id,
                buyer_name=resolved_buyer_name,
                buyer_id=send_user_id,
                item_id=item_id,
                chat_id=chat_id or '未知',
                result=error_message,
                time=time.strftime('%Y-%m-%d %H:%M:%S')
            )

            notification_sent = await dispatch_account_notifications(
                self.cookie_id,
                notification_message,
                title='自动发货通知',
                notification_type='delivery',
            )
            if not notification_sent:
                logger.warning(f"【{self.cookie_id}】自动发货通知未发送成功")

        except Exception as e:
            logger.error(f"发送自动发货通知异常: {self._safe_str(e)}")

    async def auto_confirm(self, order_id, item_id=None, retry_count=0):
        """自动确认发货 - 使用加密模块，不包含延时处理（延时已在_auto_delivery中处理）"""
        try:
            logger.warning(f"【{self.cookie_id}】开始确认发货，订单ID: {order_id}")

            # 导入解密后的确认发货模块
            from secure_confirm_decrypted import SecureConfirm

            # 创建确认实例，传入主界面类实例
            secure_confirm = SecureConfirm(self.session, self.cookies_str, self.cookie_id, self)

            # 传递必要的属性
            secure_confirm.current_token = self.current_token
            secure_confirm.last_token_refresh_time = self.last_token_refresh_time
            secure_confirm.token_refresh_interval = self.token_refresh_interval

            # 调用确认方法，传入item_id用于token刷新
            result = await secure_confirm.auto_confirm(order_id, item_id, retry_count)

            # 同步更新后的cookies和token
            if secure_confirm.cookies_str != self.cookies_str:
                self._set_runtime_cookie_state(
                    cookies_str=secure_confirm.cookies_str,
                    cookies_dict=secure_confirm.cookies,
                    source="secure_confirm_sync",
                )
                logger.warning(f"【{self.cookie_id}】已同步确认发货模块更新的cookies")

            if secure_confirm.current_token != self.current_token:
                self.current_token = secure_confirm.current_token
                self.last_token_refresh_time = secure_confirm.last_token_refresh_time
                logger.warning(f"【{self.cookie_id}】已同步确认发货模块更新的token")

            return result

        except Exception as e:
            logger.error(f"【{self.cookie_id}】加密确认模块调用失败: {self._safe_str(e)}")
            return {"error": f"加密确认模块调用失败: {self._safe_str(e)}", "order_id": order_id}

    async def auto_freeshipping(self, order_id, item_id, buyer_id, retry_count=0):
        """自动免拼发货 - 使用解密模块"""
        try:
            logger.warning(f"【{self.cookie_id}】开始免拼发货，订单ID: {order_id}")

            # 导入解密后的免拼发货模块
            from secure_freeshipping_decrypted import SecureFreeshipping

            # 创建免拼发货实例
            secure_freeshipping = SecureFreeshipping(self.session, self.cookies_str, self.cookie_id)

            # 传递必要的属性
            secure_freeshipping.current_token = self.current_token
            secure_freeshipping.last_token_refresh_time = self.last_token_refresh_time
            secure_freeshipping.token_refresh_interval = self.token_refresh_interval

            # 调用免拼发货方法
            result = await secure_freeshipping.auto_freeshipping(order_id, item_id, buyer_id, retry_count)

            if secure_freeshipping.cookies_str != self.cookies_str:
                self._set_runtime_cookie_state(
                    cookies_str=secure_freeshipping.cookies_str,
                    cookies_dict=secure_freeshipping.cookies,
                    source="secure_freeshipping_sync",
                )
                logger.warning(f"【{self.cookie_id}】已同步免拼发货模块更新的cookies")

            if secure_freeshipping.current_token != self.current_token:
                self.current_token = secure_freeshipping.current_token
                self.last_token_refresh_time = secure_freeshipping.last_token_refresh_time
                logger.warning(f"【{self.cookie_id}】已同步免拼发货模块更新的token")

            return result

        except Exception as e:
            logger.error(f"【{self.cookie_id}】免拼发货模块调用失败: {self._safe_str(e)}")
            return {"error": f"免拼发货模块调用失败: {self._safe_str(e)}", "order_id": order_id}

    async def fetch_order_detail_info(self, order_id: str, item_id: str = None, buyer_id: str = None, debug_headless: bool = None, sid: str = None, force_refresh: bool = False, buyer_nick: str = None, buyer_id_source: str = None):
        """获取订单详情信息（使用独立的锁机制，不受延迟锁影响）

        Args:
            order_id: 订单ID
            item_id: 商品ID
            buyer_id: 买家ID
            debug_headless: 是否使用有头模式调试
            sid: 会话ID（如 56226853668@goofish），用于简化消息匹配订单
            force_refresh: 是否强制刷新（跳过缓存直接从闲鱼获取）
            buyer_nick: 买家昵称（从下单消息中提取）
        """
        # 使用独立的订单详情锁，不与自动发货锁冲突
        order_detail_lock = self._order_detail_locks[order_id]

        # 如果锁绑定了不同的事件循环（如从 Web API 调用），创建新锁
        try:
            current_loop = asyncio.get_running_loop()
            lock_loop = getattr(order_detail_lock, '_loop', None)
            if lock_loop is not None and lock_loop is not current_loop:
                order_detail_lock = asyncio.Lock()
                self._order_detail_locks[order_id] = order_detail_lock
                logger.info(f"【{self.cookie_id}】订单详情锁 {order_id} 事件循环不匹配，已重建")
        except RuntimeError:
            pass

        # 记录订单详情锁的使用时间
        self._order_detail_lock_times[order_id] = time.time()

        async with order_detail_lock:
            logger.info(f"🔍 【{self.cookie_id}】获取订单详情锁 {order_id}，开始处理...")
            
            try:
                logger.info(f"【{self.cookie_id}】开始获取订单详情: {order_id}, sid={sid}")

                # 导入订单详情获取器
                from utils.order_detail_fetcher import fetch_order_detail_simple
                from db_manager import db_manager

                # 获取当前账号的cookie字符串
                cookie_string = self.cookies_str
                logger.warning(f"【{self.cookie_id}】使用Cookie长度: {len(cookie_string) if cookie_string else 0}")

                # 确定是否使用有头模式（调试用）
                headless_mode = True if debug_headless is None else debug_headless
                if not headless_mode:
                    logger.info(f"【{self.cookie_id}】🖥️ 启用有头模式进行调试")

                # 异步获取订单详情（使用当前账号的cookie）
                result = await fetch_order_detail_simple(
                    order_id,
                    cookie_string,
                    headless=headless_mode,
                    force_refresh=force_refresh,
                    cookie_id_for_log=self.cookie_id
                )

                if result:
                    retry_task = self.order_detail_retry_tasks.get(order_id)
                    current_task = asyncio.current_task()
                    if retry_task and retry_task is not current_task and not retry_task.done():
                        retry_task.cancel()
                        self.order_detail_retry_tasks.pop(order_id, None)
                        logger.info(f"【{self.cookie_id}】订单详情已成功获取，取消待执行的补抓任务: {order_id}")

                    logger.info(f"【{self.cookie_id}】订单详情获取成功: {order_id}")
                    logger.info(f"【{self.cookie_id}】页面标题: {result.get('title', '未知')}")

                    def _normalize_optional_text(value):
                        if value is None:
                            return None
                        text = str(value).strip()
                        return text if text else None

                    def _normalize_amount_text(value):
                        text = _normalize_optional_text(value)
                        if not text:
                            return None
                        # 避免将无数字的异常文本写入金额字段
                        if not re.search(r'\d', text):
                            return None
                        return text

                    def _parse_amount_float(value):
                        text = _normalize_amount_text(value)
                        if not text:
                            return None
                        try:
                            return float(text)
                        except (TypeError, ValueError):
                            return None

                    # 获取解析后的规格信息
                    spec_parse_mode = str(result.get('spec_parse_mode') or '').strip() or 'no_spec'
                    spec_name = _normalize_optional_text(result.get('spec_name'))
                    spec_value = _normalize_optional_text(result.get('spec_value'))
                    spec_name_2 = _normalize_optional_text(result.get('spec_name_2'))
                    spec_value_2 = _normalize_optional_text(result.get('spec_value_2'))
                    quantity = _normalize_optional_text(result.get('quantity'))
                    amount = _normalize_amount_text(result.get('amount'))
                    amount_source = _normalize_optional_text(result.get('amount_source')) or 'unknown'
                    platform_created_at = _normalize_optional_text(result.get('platform_created_at'))
                    platform_paid_at = _normalize_optional_text(result.get('platform_paid_at'))
                    platform_completed_at = _normalize_optional_text(result.get('platform_completed_at'))
                    item_config = db_manager.get_item_info(self.cookie_id, item_id) if item_id else None
                    item_config_multi_spec = bool(item_config and item_config.get('is_multi_spec'))
                    item_config_detail = _normalize_optional_text(item_config.get('item_detail')) if item_config else None
                    is_coin_deduction_item = bool(item_config_detail and '闲鱼币抵扣' in item_config_detail)
                    configured_item_amount = _normalize_amount_text(item_config.get('item_price')) if item_config else None
                    configured_item_amount_value = _parse_amount_float(configured_item_amount)

                    if item_config is not None and not item_config_multi_spec and any(
                        [spec_name, spec_value, spec_name_2, spec_value_2]
                    ):
                        logger.warning(
                            f"【{self.cookie_id}】商品配置为无规格，刷新订单详情时忽略解析到的规格信息: "
                            f"order_id={order_id}, item_id={item_id}, "
                            f"spec={spec_name or ''}:{spec_value or ''}, spec2={spec_name_2 or ''}:{spec_value_2 or ''}"
                        )
                        spec_name = None
                        spec_value = None
                        spec_name_2 = None
                        spec_value_2 = None

                    if spec_parse_mode == 'one_spec' and spec_name and spec_value and not (spec_name_2 or spec_value_2):
                        spec_name_2 = ''
                        spec_value_2 = ''
                        logger.info(
                            f"【{self.cookie_id}】订单详情明确解析为单规格，允许清空历史残留的第二规格字段: "
                            f"order_id={order_id}, item_id={item_id}, spec={spec_name}:{spec_value}"
                        )

                    # 获取订单状态（从闲鱼页面解析）
                    raw_order_status = _normalize_optional_text(result.get('order_status'))
                    order_status_source = _normalize_optional_text(result.get('order_status_source')) or 'unknown'
                    # unknown 视为解析失败，不覆盖已有状态
                    order_status = raw_order_status if raw_order_status and raw_order_status.lower() != 'unknown' else None
                    if order_status:
                        logger.info(f"【{self.cookie_id}】📊 订单状态: {order_status} (source={order_status_source})")
                    elif raw_order_status and raw_order_status.lower() == 'unknown':
                        logger.warning(f"【{self.cookie_id}】订单状态解析为unknown，跳过状态字段写库")

                    if spec_name and spec_value:
                        logger.info(f"【{self.cookie_id}】📋 规格名称: {spec_name}")
                        logger.info(f"【{self.cookie_id}】📝 规格值: {spec_value}")
                        if spec_name_2 and spec_value_2:
                            logger.info(f"【{self.cookie_id}】📋 规格2名称: {spec_name_2}")
                            logger.info(f"【{self.cookie_id}】📝 规格2值: {spec_value_2}")
                            print(f"🛍️ 【{self.cookie_id}】订单 {order_id} 规格信息: {spec_name} -> {spec_value}, {spec_name_2} -> {spec_value_2}")
                        else:
                            print(f"🛍️ 【{self.cookie_id}】订单 {order_id} 规格信息: {spec_name} -> {spec_value}")
                    else:
                        logger.warning(f"【{self.cookie_id}】未获取到有效的规格信息")
                        print(f"⚠️ 【{self.cookie_id}】订单 {order_id} 规格信息获取失败")

                    if amount:
                        logger.info(f"【{self.cookie_id}】💰 订单金额: {amount} (source={amount_source})")

                    # 插入或更新订单信息到数据库
                    try:
                        # 对于系统消息误识别出的“自己是买家”场景，保留已有买家信息并继续刷新订单字段
                        existing_order = db_manager.get_order_by_id(order_id)
                        current_order_status = existing_order.get('order_status') if existing_order else None
                        existing_amount = existing_order.get('amount') if existing_order else None
                        existing_amount_value = _parse_amount_float(existing_amount)
                        amount, amount_source = self._apply_bargain_amount_override(
                            order_id,
                            item_id,
                            amount,
                            amount_source,
                            existing_order=existing_order,
                            item_config=item_config,
                        )
                        incoming_amount_value = _parse_amount_float(amount)
                        has_valid_spec = bool(spec_name and spec_value)
                        low_confidence_amount_sources = {
                            'selector_direct',
                            'selector_currency',
                            'text_currency',
                            'unknown',
                        }

                        if (
                            is_coin_deduction_item and existing_amount_value is not None and incoming_amount_value is not None and
                            configured_item_amount_value is not None and existing_amount_value + 0.009 < configured_item_amount_value and
                            abs(incoming_amount_value - configured_item_amount_value) <= 0.009
                        ):
                            logger.warning(
                                f"【{self.cookie_id}】闲鱼币抵扣订单返回原价，保留已有实付金额: "
                                f"order_id={order_id}, existing_amount={existing_amount}, incoming_amount={amount}, "
                                f"configured_amount={configured_item_amount}, amount_source={amount_source}"
                            )
                            amount = _normalize_amount_text(existing_amount)
                            amount_source = 'coin_deduction_preserved_existing'
                            incoming_amount_value = _parse_amount_float(amount)

                        if amount and amount_source in low_confidence_amount_sources and not has_valid_spec and not order_status:
                            if existing_amount_value is not None:
                                logger.warning(
                                    f"【{self.cookie_id}】订单详情返回低置信度金额，保留已有金额: "
                                    f"order_id={order_id}, existing_amount={existing_amount}, incoming_amount={amount}, "
                                    f"amount_source={amount_source}"
                                )
                                amount = _normalize_amount_text(existing_amount)
                                amount_source = 'preserved_existing'
                            else:
                                logger.warning(
                                    f"【{self.cookie_id}】订单详情返回低置信度金额，且缺少规格/状态佐证，跳过写库: "
                                    f"order_id={order_id}, incoming_amount={amount}, amount_source={amount_source}"
                                )
                                amount = None

                        elif (
                            amount and existing_amount_value is not None and incoming_amount_value is not None and
                            abs(existing_amount_value - incoming_amount_value) > 0.009 and
                            not has_valid_spec and not order_status and
                            amount_source not in {'selector_keyword_high', 'selector_keyword_low', 'text_keyword_high', 'text_keyword_low', 'cache'}
                        ):
                            logger.warning(
                                f"【{self.cookie_id}】订单详情金额跳变且缺少规格/状态佐证，保留已有金额: "
                                f"order_id={order_id}, existing_amount={existing_amount}, incoming_amount={amount}, "
                                f"amount_source={amount_source}"
                            )
                            amount = _normalize_amount_text(existing_amount)
                            amount_source = 'preserved_existing'

                        if self._should_reject_order_detail_status_update(
                            current_status=current_order_status,
                            incoming_status=order_status,
                            incoming_source=order_status_source,
                            force_refresh=force_refresh,
                        ):
                            logger.warning(
                                f"【{self.cookie_id}】强制刷新结果仅来自正文，拒绝将订单状态更新为completed: "
                                f"order_id={order_id}, current={current_order_status}, incoming={order_status}, "
                                f"source={order_status_source}"
                            )
                            order_status = None

                        normalized_current_order_status = db_manager._normalize_order_status(current_order_status)
                        normalized_incoming_order_status = db_manager._normalize_order_status(order_status)
                        if self._should_accept_order_detail_status_correction(
                            current_order_status,
                            order_status,
                            order_status_source,
                            force_refresh=force_refresh,
                            order_id=order_id,
                        ):
                            order_status_to_save = normalized_incoming_order_status
                            logger.warning(
                                f"【{self.cookie_id}】检测到可疑已发货状态，允许强刷后的结构化待发货结果纠偏: "
                                f"order_id={order_id}, current={current_order_status}, incoming={order_status}, "
                                f"source={order_status_source}"
                            )
                        else:
                            order_status_to_save = self._resolve_external_order_status(
                                current_order_status,
                                order_status,
                                source='order_detail_refresh'
                            )

                        if (
                            order_status and existing_order and order_status_to_save is None and
                            normalized_current_order_status != normalized_incoming_order_status
                        ):
                            logger.info(
                                f"【{self.cookie_id}】保留订单现有状态，跳过详情页覆盖: "
                                f"order_id={order_id}, current={current_order_status}, incoming={order_status}"
                            )

                        buyer_id_to_save, buyer_nick_to_save, should_skip_write = self._select_buyer_identity_for_order_write(
                            order_id,
                            incoming_buyer_id=buyer_id,
                            incoming_buyer_nick=buyer_nick,
                            existing_order=existing_order,
                            buyer_id_source=buyer_id_source,
                            buyer_nick_source="order_detail",
                            log_prefix=f"【{self.cookie_id}】",
                        )
                        if should_skip_write:
                            return result

                        # 检查cookie_id是否在cookies表中存在
                        cookie_info = db_manager.get_cookie_by_id(self.cookie_id)
                        if not cookie_info:
                            logger.warning(f"Cookie ID {self.cookie_id} 不存在于cookies表中，丢弃订单 {order_id}")
                        else:
                            # 先保存订单基本信息（包含sid和buyer_nick用于简化消息匹配）
                            success = db_manager.insert_or_update_order(
                                order_id=order_id,
                                item_id=item_id,
                                buyer_id=buyer_id_to_save,
                                buyer_nick=buyer_nick_to_save,  # 传递买家昵称
                                sid=sid,
                                spec_name=spec_name,
                                spec_value=spec_value,
                                spec_name_2=spec_name_2,
                                spec_value_2=spec_value_2,
                                quantity=quantity,
                                amount=amount,
                                cookie_id=self.cookie_id,
                                order_status=order_status_to_save,  # 外部详情状态仅在不会回退内部状态时写库
                                platform_created_at=platform_created_at,
                                platform_paid_at=platform_paid_at,
                                platform_completed_at=platform_completed_at
                            )
                            
                            # 使用订单状态处理器设置状态
                            logger.info(f"【{self.cookie_id}】检查订单状态处理器调用条件: success={success}, handler_exists={self.order_status_handler is not None}")
                            if success and self.order_status_handler:
                                logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.handle_order_detail_fetched_status: {order_id}")
                                try:
                                    handler_result = self.order_status_handler.handle_order_detail_fetched_status(
                                        order_id=order_id,
                                        cookie_id=self.cookie_id,
                                        context="订单详情已拉取"
                                    )
                                    logger.info(f"【{self.cookie_id}】订单状态处理器.handle_order_detail_fetched_status返回结果: {handler_result}")
                                    
                                    # 处理待处理队列
                                    logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.on_order_details_fetched: {order_id}")
                                    self.order_status_handler.on_order_details_fetched(order_id)
                                    logger.info(f"【{self.cookie_id}】订单状态处理器.on_order_details_fetched调用成功: {order_id}")
                                except Exception as e:
                                    logger.error(f"【{self.cookie_id}】订单状态处理器调用失败: {self._safe_str(e)}")
                                    import traceback
                                    logger.error(f"【{self.cookie_id}】详细错误信息: {traceback.format_exc()}")
                            else:
                                logger.warning(f"【{self.cookie_id}】订单状态处理器调用条件不满足: success={success}, handler_exists={self.order_status_handler is not None}")

                            if success:
                                logger.info(f"【{self.cookie_id}】订单信息已保存到数据库: {order_id}")
                                print(f"💾 【{self.cookie_id}】订单 {order_id} 信息已保存到数据库")
                            else:
                                logger.warning(f"【{self.cookie_id}】订单信息保存失败: {order_id}")

                    except Exception as db_e:
                        logger.error(f"【{self.cookie_id}】保存订单信息到数据库失败: {self._safe_str(db_e)}")

                    return result
                else:
                    logger.warning(f"【{self.cookie_id}】订单详情获取失败: {order_id}")
                    return None

            except Exception as e:
                logger.error(f"【{self.cookie_id}】获取订单详情异常: {self._safe_str(e)}")
                return None

    async def _auto_delivery(self, item_id: str, item_title: str = None, order_id: str = None, send_user_id: str = None,
                             chat_id: str = None, send_user_name: str = None, include_meta: bool = False,
                             data_preview_index: int = 0, delivery_unit_index: int = 1):
        """自动发货功能 - 匹配规则并准备发货内容，不直接提交副作用。"""
        try:
            matched_rule_context = None
            match_mode_context = None

            def build_result(success: bool, content: str = None, error: str = None, matched_rule: dict = None,
                             match_mode_value: str = None, delivery_steps_value: list = None):
                order_spec_mode_value = 'no_spec'
                item_config_mode_value = 'no_spec'
                rule_spec_mode_value = None

                try:
                    order_spec_mode_value = _get_order_spec_mode()
                except Exception:
                    pass

                try:
                    rule_spec_mode_value = _get_rule_spec_mode(matched_rule) if matched_rule else None
                except Exception:
                    pass

                try:
                    item_config_mode_value = 'spec_enabled' if item_config_multi_spec else 'no_spec'
                except Exception:
                    pass

                if include_meta:
                    return {
                        "success": bool(success),
                        "content": content if success else None,
                        "error": error if not success else None,
                        "rule_id": matched_rule.get('id') if matched_rule else None,
                        "rule_keyword": matched_rule.get('keyword') if matched_rule else None,
                        "card_type": matched_rule.get('card_type') if matched_rule else None,
                        "match_mode": match_mode_value,
                        "order_spec_mode": order_spec_mode_value,
                        "rule_spec_mode": rule_spec_mode_value,
                        "item_config_mode": item_config_mode_value,
                        "card_id": matched_rule.get('card_id') if matched_rule else None,
                        "card_description": matched_rule.get('card_description') if matched_rule else None,
                        "delivery_steps": delivery_steps_value or [],
                        "data_card_pending_consume": False,
                        "data_line": None,
                        "data_reservation_id": None,
                        "data_reservation_status": None,
                        "delivery_unit_index": delivery_unit_index
                    }
                return content if success else None

            from db_manager import db_manager

            logger.info(f"开始自动发货检查: 商品ID={item_id}")

            # 获取商品详细信息
            item_info = None
            search_text = item_title  # 默认使用传入的标题

            if item_id and item_id != "未知商品":
                # 直接从数据库获取商品信息（发货时不再调用API）
                try:
                    logger.info(f"从数据库获取商品信息: {item_id}")
                    db_item_info = db_manager.get_item_info(self.cookie_id, item_id)
                    if db_item_info:
                        item_info = db_item_info
                        # 拼接商品标题和详情作为搜索文本
                        item_title_db = db_item_info.get('item_title', '') or ''
                        item_detail_db = db_item_info.get('item_detail', '') or ''

                        # 如果数据库中没有详情，尝试自动获取
                        if not item_detail_db.strip():
                            from config import config
                            auto_fetch_config = config.get('ITEM_DETAIL', {}).get('auto_fetch', {})

                            if auto_fetch_config.get('enabled', True):
                                logger.info(f"数据库中商品详情为空，尝试自动获取: {item_id}")
                                try:
                                    fetched_detail = await self.fetch_item_detail_from_api(item_id)
                                    if fetched_detail:
                                        # 保存获取到的详情
                                        await self.save_item_detail_only(item_id, fetched_detail)
                                        item_detail_db = fetched_detail
                                        logger.info(f"成功获取并保存商品详情: {item_id}")
                                    else:
                                        logger.warning(f"未能获取到商品详情: {item_id}")
                                except Exception as api_e:
                                    logger.warning(f"获取商品详情失败: {item_id}, 错误: {self._safe_str(api_e)}")
                            else:
                                logger.warning(f"自动获取商品详情功能已禁用，跳过: {item_id}")

                        # 组合搜索文本：商品标题 + 商品详情
                        search_parts = []
                        if item_title_db.strip():
                            search_parts.append(item_title_db.strip())
                        if item_detail_db.strip():
                            search_parts.append(item_detail_db.strip())

                        if search_parts:
                            search_text = ' '.join(search_parts)
                            logger.info(f"使用数据库商品标题+详情作为搜索文本: 标题='{item_title_db}', 详情长度={len(item_detail_db)}")
                            logger.warning(f"完整搜索文本: {search_text[:200]}...")
                        else:
                            logger.warning(f"数据库中商品标题和详情都为空: {item_id}")
                            search_text = item_title or item_id
                    else:
                        logger.warning(f"数据库中未找到商品信息: {item_id}")
                        search_text = item_title or item_id

                except Exception as db_e:
                    logger.warning(f"从数据库获取商品信息失败: {self._safe_str(db_e)}")
                    search_text = item_title or item_id

            if not search_text:
                search_text = item_id or "未知商品"

            logger.info(f"使用搜索文本匹配发货规则: {search_text[:100]}...")

            item_config_multi_spec = db_manager.get_item_multi_spec_status(self.cookie_id, item_id)
            spec_name = ''
            spec_value = ''
            spec_name_2 = ''
            spec_value_2 = ''

            def _apply_spec_from_order_detail(order_detail_data) -> bool:
                nonlocal spec_name, spec_value, spec_name_2, spec_value_2
                if not order_detail_data or not isinstance(order_detail_data, dict):
                    return False
                spec_name = (order_detail_data.get('spec_name') or '').strip()
                spec_value = (order_detail_data.get('spec_value') or '').strip()
                spec_name_2 = (order_detail_data.get('spec_name_2') or '').strip()
                spec_value_2 = (order_detail_data.get('spec_value_2') or '').strip()
                return bool(spec_name and spec_value)

            def _get_order_spec_mode() -> str:
                has_first_spec = bool(spec_name and spec_value)
                has_second_spec = bool(spec_name_2 and spec_value_2)

                if has_first_spec and has_second_spec:
                    return 'two_spec'
                if has_first_spec:
                    return 'one_spec'
                return 'no_spec'

            def _get_rule_spec_mode(rule: dict) -> str:
                if not rule:
                    return 'no_spec'

                rule_spec_name = (rule.get('spec_name') or '').strip()
                rule_spec_value = (rule.get('spec_value') or '').strip()
                rule_spec_name_2 = (rule.get('spec_name_2') or '').strip()
                rule_spec_value_2 = (rule.get('spec_value_2') or '').strip()

                if rule_spec_name and rule_spec_value and rule_spec_name_2 and rule_spec_value_2:
                    return 'two_spec'
                if rule_spec_name and rule_spec_value:
                    return 'one_spec'
                return 'no_spec'

            # 只要有订单ID就尝试拉取订单详情；规格商品缺失规格时自动重试，提升精确命中率
            if order_id:
                logger.info(f"检测到订单ID，获取订单详情用于规则匹配: {order_id}")
                max_detail_attempts = 3 if item_config_multi_spec else 1
                for attempt in range(1, max_detail_attempts + 1):
                    try:
                        force_refresh = attempt > 1
                        if force_refresh:
                            logger.info(f"订单规格信息缺失，开始强刷重试 ({attempt}/{max_detail_attempts}): {order_id}")

                        order_detail = await self.fetch_order_detail_info(
                            order_id,
                            item_id,
                            send_user_id,
                            force_refresh=force_refresh
                        )

                        if _apply_spec_from_order_detail(order_detail):
                            logger.info(f"获取到规格信息: {spec_name} = {spec_value}")
                            if spec_name_2 and spec_value_2:
                                logger.info(f"获取到规格2信息: {spec_name_2} = {spec_value_2}")
                            break

                        if item_config_multi_spec:
                            logger.warning(
                                f"订单详情已获取但未解析到有效规格信息 (尝试 {attempt}/{max_detail_attempts})"
                            )
                        else:
                            logger.info("无规格商品未解析到规格信息，按普通规则继续")
                    except Exception as e:
                        logger.error(
                            f"获取订单详情失败 (尝试 {attempt}/{max_detail_attempts}): {self._safe_str(e)}"
                        )

                    if attempt < max_detail_attempts:
                        await asyncio.sleep(0.6)

                if _get_order_spec_mode() == 'no_spec':
                    try:
                        cached_order = db_manager.get_order_by_id(order_id)
                        if cached_order and _apply_spec_from_order_detail(cached_order):
                            logger.warning(
                                f"订单 {order_id} 从数据库缓存恢复规格成功: "
                                f"{spec_name}:{spec_value}"
                            )
                    except Exception as cache_e:
                        logger.warning(f"订单缓存规格恢复失败: {self._safe_str(cache_e)}")
            else:
                logger.warning("当前无订单ID，跳过订单详情拉取，将仅基于商品文本匹配规则")

            order_spec_mode = _get_order_spec_mode()
            item_config_mode = 'spec_enabled' if item_config_multi_spec else 'no_spec'

            if order_spec_mode != 'no_spec' and item_info is not None and not item_config_multi_spec:
                logger.warning(
                    f"商品已配置为无规格，忽略订单解析到的规格并按普通规则匹配: "
                    f"order_spec_mode={order_spec_mode}, item_id={item_id or 'unknown'}, "
                    f"order_id={order_id or 'unknown'}, spec={spec_name}:{spec_value}"
                )
                spec_name = ''
                spec_value = ''
                spec_name_2 = ''
                spec_value_2 = ''
                order_spec_mode = _get_order_spec_mode()
            elif order_spec_mode == 'no_spec' and item_config_multi_spec:
                block_reason = (
                    f"商品已开启规格匹配，但订单未解析到有效规格信息，已阻断自动发货: "
                    f"order_id={order_id or 'unknown'}, item_id={item_id or 'unknown'}"
                )
                logger.error(block_reason)
                return build_result(False, error=block_reason, match_mode_value='blocked_no_spec_parsed')

            logger.info(
                f"规格模式判定完成: order_spec_mode={order_spec_mode}, "
                f"item_config_mode={item_config_mode}"
            )

            delivery_rules = []
            if order_spec_mode == 'two_spec':
                match_mode = 'two_spec_exact'
                match_mode_context = match_mode
                logger.info(
                    f"尝试精确匹配两组规格发货规则: {search_text[:50]}... "
                    f"[{spec_name}:{spec_value}, {spec_name_2}:{spec_value_2}]"
                )
                delivery_rules = db_manager.get_delivery_rules_by_keyword_and_spec(
                    search_text,
                    spec_name,
                    spec_value,
                    spec_name_2,
                    spec_value_2,
                    user_id=self.user_id,
                    expected_mode='two_spec'
                )
                if not delivery_rules:
                    error_message = "两组规格订单未找到匹配的发货规则"
                    logger.warning(f"{error_message}: {search_text[:50]}...")
                    return build_result(False, error=error_message, match_mode_value='blocked_no_rule')
            elif order_spec_mode == 'one_spec':
                match_mode = 'one_spec_exact'
                match_mode_context = match_mode
                logger.info(
                    f"尝试精确匹配一组规格发货规则: {search_text[:50]}... "
                    f"[{spec_name}:{spec_value}]"
                )
                delivery_rules = db_manager.get_delivery_rules_by_keyword_and_spec(
                    search_text,
                    spec_name,
                    spec_value,
                    spec_name_2,
                    spec_value_2,
                    user_id=self.user_id,
                    expected_mode='one_spec'
                )
                if not delivery_rules:
                    logger.warning(
                        f"一组规格订单未找到精确规格规则，尝试降级匹配普通发货规则: {search_text[:50]}..."
                    )
                    fallback_rules = db_manager.get_delivery_rules_by_keyword(
                        search_text,
                        user_id=self.user_id,
                        only_non_multi_spec=True
                    )
                    if not fallback_rules:
                        error_message = "一组规格订单未找到匹配的发货规则"
                        logger.warning(f"{error_message}: {search_text[:50]}...")
                        return build_result(False, error=error_message, match_mode_value='blocked_no_rule')
                    if len(fallback_rules) != 1:
                        block_reason = (
                            f"一组规格订单精确匹配失败后，普通规则兜底匹配到{len(fallback_rules)}条，"
                            f"已阻断自动发货以避免错发: order_id={order_id or 'unknown'}, "
                            f"item_id={item_id or 'unknown'}"
                        )
                        logger.error(block_reason)
                        return build_result(False, error=block_reason, match_mode_value='blocked_multiple_no_spec_rules')
                    delivery_rules = fallback_rules
                    match_mode = 'one_spec_fallback_no_spec'
                    match_mode_context = match_mode
                    logger.warning(
                        f"一组规格订单已降级命中唯一普通规则: order_id={order_id or 'unknown'}, "
                        f"item_id={item_id or 'unknown'}, rule_id={delivery_rules[0].get('id')}"
                    )
            else:
                match_mode = 'no_spec_match'
                match_mode_context = match_mode
                logger.info(f"无规格订单，尝试匹配普通发货规则: {search_text[:50]}...")
                delivery_rules = db_manager.get_delivery_rules_by_keyword(
                    search_text,
                    user_id=self.user_id,
                    only_non_multi_spec=True
                )
                if not delivery_rules:
                    error_message = "无规格订单未找到匹配的普通发货规则"
                    logger.warning(f"{error_message}: {search_text[:50]}...")
                    return build_result(False, error=error_message, match_mode_value='blocked_no_rule')
                if len(delivery_rules) != 1:
                    block_reason = (
                        f"无规格订单匹配到{len(delivery_rules)}条普通规则，已阻断自动发货以避免错发: "
                        f"order_id={order_id or 'unknown'}, item_id={item_id or 'unknown'}"
                    )
                    logger.error(block_reason)
                    return build_result(False, error=block_reason, match_mode_value='blocked_multiple_no_spec_rules')

            # 使用第一个匹配的规则（按关键字长度降序排列，优先匹配更精确的规则）
            rule = delivery_rules[0]
            matched_rule_context = rule
            rule_spec_mode = _get_rule_spec_mode(rule)

            logger.info(
                f"规则模式判定完成: order_spec_mode={order_spec_mode}, rule_spec_mode={rule_spec_mode}, "
                f"match_mode={match_mode}, rule_id={rule.get('id')}"
            )

            allow_one_spec_fallback = (
                match_mode == 'one_spec_fallback_no_spec'
                and order_spec_mode == 'one_spec'
                and rule_spec_mode == 'no_spec'
            )

            if rule_spec_mode != order_spec_mode and not allow_one_spec_fallback:
                block_reason = (
                    f"订单规格模式与命中规则模式不一致，已阻断自动发货: "
                    f"order_spec_mode={order_spec_mode}, rule_spec_mode={rule_spec_mode}, "
                    f"order_id={order_id or 'unknown'}, item_id={item_id or 'unknown'}, rule_id={rule.get('id')}"
                )
                logger.error(block_reason)
                return build_result(False, error=block_reason, matched_rule=rule, match_mode_value='blocked_rule_mode_mismatch')

            # 注释掉自动发货时的商品信息保存逻辑，避免重复保存导致item_detail字段内容累积
            # 商品信息应该在商品列表获取、订单详情获取等其他环节已经保存过了
            # 保存商品信息到数据库（需要有商品标题才保存）
            # # 尝试获取商品标题
            # item_title_for_save = None
            # try:
            #     from db_manager import db_manager
            #     db_item_info = db_manager.get_item_info(self.cookie_id, item_id)
            #     if db_item_info:
            #         item_title_for_save = db_item_info.get('item_title', '').strip()
            # except:
            #     pass
            # 
            # # 如果有商品标题，则保存商品信息
            # if item_title_for_save:
            #     await self.save_item_info_to_db(item_id, search_text, item_title_for_save)
            # else:
            #     logger.warning(f"跳过保存商品信息：缺少商品标题 - {item_id}")

            # 详细的匹配结果日志
            if order_spec_mode == 'two_spec':
                rule_spec_info = f"{rule['spec_name']}:{rule['spec_value']}, {rule['spec_name_2']}:{rule['spec_value_2']}"
                order_spec_info = f"{spec_name}:{spec_value}, {spec_name_2}:{spec_value_2}"
                logger.info(f"🎯 精确匹配两组规格发货规则: {rule['keyword']} -> {rule['card_name']} [{rule_spec_info}]")
                logger.info(f"📋 订单规格: {order_spec_info} ✅ 匹配卡券规格: {rule_spec_info}")
            elif match_mode == 'one_spec_fallback_no_spec':
                order_spec_info = f"{spec_name}:{spec_value}"
                logger.warning(
                    f"⚠️ 单规格订单降级匹配普通发货规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})"
                )
                logger.warning(f"📋 订单规格: {order_spec_info}，精确规格未命中，已降级到普通规则")
            elif order_spec_mode == 'one_spec':
                rule_spec_info = f"{rule['spec_name']}:{rule['spec_value']}"
                order_spec_info = f"{spec_name}:{spec_value}"
                logger.info(f"🎯 精确匹配一组规格发货规则: {rule['keyword']} -> {rule['card_name']} [{rule_spec_info}]")
                logger.info(f"📋 订单规格: {order_spec_info} ✅ 匹配卡券规格: {rule_spec_info}")
            else:
                logger.info(f"✅ 匹配无规格发货规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})")

            # 获取延时设置
            delay_seconds = rule.get('card_delay_seconds', 0)

            # 执行延时（只准备内容，不执行确认发货）
            if delay_seconds and delay_seconds > 0:
                logger.info(f"检测到发货延时设置: {delay_seconds}秒，开始延时...")
                await asyncio.sleep(delay_seconds)
                logger.info(f"延时完成")

            # 检查是否存在订单ID，只有存在订单ID才处理发货内容
            if order_id:
                # 保存订单基本信息到数据库（如果还没有详细信息）
                try:
                    from db_manager import db_manager

                    # 过滤掉买家订单（如果send_user_id是自己，说明是自己购买的订单）
                    if send_user_id and send_user_id == self.myid:
                        logger.info(f"【{self.cookie_id}】跳过买家订单 {order_id}，buyer_id={send_user_id} 等于自己的ID")
                        # 不保存买家订单，但继续返回发货内容（如果有的话）
                    else:
                        # 检查cookie_id是否在cookies表中存在
                        cookie_info = db_manager.get_cookie_by_id(self.cookie_id)
                        if not cookie_info:
                            logger.warning(f"Cookie ID {self.cookie_id} 不存在于cookies表中，丢弃订单 {order_id}")
                        else:
                            existing_order = db_manager.get_order_by_id(order_id)
                            if not existing_order:
                                # 插入基本订单信息
                                success = db_manager.insert_or_update_order(
                                    order_id=order_id,
                                    item_id=item_id,
                                    buyer_id=send_user_id,
                                    buyer_nick=send_user_name,
                                    cookie_id=self.cookie_id
                                )

                                # 使用订单状态处理器设置状态
                                if success and self.order_status_handler:
                                    try:
                                        self.order_status_handler.handle_order_basic_info_status(
                                            order_id=order_id,
                                            cookie_id=self.cookie_id,
                                            context="自动发货-基本信息"
                                        )
                                    except Exception as e:
                                        logger.error(f"【{self.cookie_id}】订单状态处理器调用失败: {self._safe_str(e)}")

                                if success:
                                    logger.info(f"保存基本订单信息到数据库: {order_id}")
                except Exception as db_e:
                    logger.error(f"保存基本订单信息失败: {self._safe_str(db_e)}")

                # 开始处理发货内容
                logger.info(f"开始处理发货内容，规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})")

                delivery_content = None
                data_line = None
                data_reservation = None

                # 根据卡券类型处理发货内容
                if rule['card_type'] == 'api':
                    # API类型：调用API获取内容，传入订单和商品信息用于动态参数替换
                    delivery_content = await self._get_api_card_content(rule, order_id, item_id, send_user_id, spec_name, spec_value)

                elif rule['card_type'] == 'yifan_api':
                    # 亦凡卡劵API类型：调用亦凡API获取内容
                    delivery_content = await self._get_yifan_api_card_content(rule, order_id, item_id, send_user_id, chat_id)

                elif rule['card_type'] == 'text':
                    # 固定文字类型：直接使用文字内容
                    delivery_content = rule['text_content']

                elif rule['card_type'] == 'data':
                    # 批量数据类型：先原子预占，再发送，避免并发订单拿到同一条卡密
                    data_reservation = db_manager.reserve_batch_data(
                        card_id=rule['card_id'],
                        order_id=order_id,
                        unit_index=delivery_unit_index,
                        cookie_id=self.cookie_id,
                        buyer_id=send_user_id,
                    )
                    if data_reservation:
                        data_line = data_reservation.get('reserved_content')
                        delivery_content = data_line
                    else:
                        delivery_content = None

                elif rule['card_type'] == 'image':
                    # 图片类型：返回图片发送标记，包含卡券ID
                    image_url = rule.get('image_url')
                    if image_url:
                        delivery_content = f"__IMAGE_SEND__{rule['card_id']}|{image_url}"
                        logger.info(f"准备发送图片: {image_url} (卡券ID: {rule['card_id']})")
                    else:
                        logger.error(f"图片卡券缺少图片URL: 卡券ID={rule['card_id']}")
                        delivery_content = None

                if delivery_content:
                    delivery_steps = self._build_delivery_steps(delivery_content, rule.get('card_description', ''))
                    if not delivery_steps:
                        logger.warning(f"发货步骤构建失败: 规则ID={rule['id']}")
                        return build_result(False, error=f"发货步骤构建失败: 规则ID={rule['id']}", matched_rule=rule, match_mode_value=match_mode)

                    if len(delivery_steps) == 1 and delivery_steps[0].get('type') == 'text':
                        final_content = delivery_steps[0].get('content') or ''
                    else:
                        final_content = delivery_content

                    logger.info(f"自动发货内容准备成功: 规则ID={rule['id']}, 步骤数={len(delivery_steps)}")

                    result = build_result(
                        True,
                        content=final_content,
                        matched_rule=rule,
                        match_mode_value=match_mode,
                        delivery_steps_value=delivery_steps
                    )
                    if include_meta and isinstance(result, dict):
                        result['card_id'] = rule.get('card_id')
                        result['data_card_pending_consume'] = bool(rule['card_type'] == 'data')
                        result['data_line'] = data_line
                        result['data_reservation_id'] = data_reservation.get('id') if data_reservation else None
                        result['data_reservation_status'] = data_reservation.get('status') if data_reservation else None
                        result['delivery_unit_index'] = delivery_unit_index
                    return result
                else:
                    logger.warning(f"获取发货内容失败: 规则ID={rule['id']}")
                    return build_result(False, error=f"获取发货内容失败: 规则ID={rule['id']}", matched_rule=rule, match_mode_value=match_mode)
            else:
                # 没有订单ID，记录日志但不处理发货内容
                logger.info(f"⚠️ 未检测到订单ID，跳过发货内容处理。规则: {rule['keyword']} -> {rule['card_name']} ({rule['card_type']})")
                return build_result(False, error="未检测到订单ID，跳过发货内容处理", matched_rule=rule, match_mode_value=match_mode)

        except Exception as e:
            error_text = self._safe_str(e)
            if matched_rule_context:
                rule_label = matched_rule_context.get('keyword') or f"规则ID={matched_rule_context.get('id')}"
                card_type = matched_rule_context.get('card_type') or 'unknown'
                error_message = f"规则已命中({rule_label})，但{card_type}发货处理异常: {error_text}"
            else:
                error_message = f"自动发货异常: {error_text}"
            logger.error(error_message)
            return build_result(
                False,
                error=error_message,
                matched_rule=matched_rule_context,
                match_mode_value=match_mode_context
            )



    def _process_delivery_content_with_description(self, delivery_content: str, card_description: str) -> str:
        """处理发货内容和备注信息，实现变量替换"""
        try:
            # 如果没有备注信息，直接返回发货内容
            if not card_description or not card_description.strip():
                return delivery_content

            # 替换备注中的变量
            processed_description = card_description.replace('{DELIVERY_CONTENT}', delivery_content)

            # 如果备注中包含变量替换，返回处理后的备注
            if '{DELIVERY_CONTENT}' in card_description:
                return processed_description
            else:
                # 如果备注中没有变量，将备注和发货内容组合
                return f"{processed_description}\n\n{delivery_content}"

        except Exception as e:
            logger.error(f"处理备注信息失败: {e}")
            # 出错时返回原始发货内容
            return delivery_content

    def _build_delivery_steps(self, delivery_content: str, card_description: str):
        """构建发货步骤，确保图片卡券和备注按正确顺序发送。"""
        try:
            raw_content = delivery_content if isinstance(delivery_content, str) else str(delivery_content or '')
            description = (card_description or '').strip()
            steps = []

            if raw_content and not raw_content.startswith("__IMAGE_SEND__"):
                final_text = self._process_delivery_content_with_description(raw_content, description)
                return [{'type': 'text', 'content': final_text}] if final_text else []

            def append_text_step(text: str):
                text = (text or '').strip()
                if text:
                    steps.append({'type': 'text', 'content': text})

            def append_payload_step(payload: str):
                payload = (payload or '').strip()
                if payload:
                    if payload.startswith("__IMAGE_SEND__"):
                        steps.append({'type': 'image', 'content': payload})
                    else:
                        steps.append({'type': 'text', 'content': payload})

            if not description:
                append_payload_step(raw_content)
                return steps

            if '{DELIVERY_CONTENT}' in description:
                placeholder = '{DELIVERY_CONTENT}'
                segments = description.split(placeholder)
                for index, segment in enumerate(segments):
                    append_text_step(segment)
                    if index < len(segments) - 1:
                        append_payload_step(raw_content)
                return steps

            append_text_step(description)
            append_payload_step(raw_content)
            return steps
        except Exception as e:
            logger.error(f"构建发货步骤失败: {e}")
            fallback_content = delivery_content if isinstance(delivery_content, str) else str(delivery_content or '')
            if fallback_content:
                return [{'type': 'image' if fallback_content.startswith("__IMAGE_SEND__") else 'text', 'content': fallback_content}]
            return []

    def _can_batch_text_delivery(self, delivery_steps, card_type: str = None) -> bool:
        """仅将 text/data/api 的单条纯文本步骤纳入批量合并发送。"""
        normalized_card_type = str(card_type or '').strip().lower()
        if normalized_card_type not in {'text', 'data', 'api'}:
            return False

        steps = delivery_steps or []
        if len(steps) != 1:
            return False

        step = steps[0] or {}
        if step.get('type') != 'text':
            return False

        return bool((step.get('content') or '').strip())

    def _format_delivery_unit_text(self, text: str, unit_index: int, total_units: int) -> str:
        """为批量发货文本添加全局连续序号。"""
        safe_total_units = max(1, int(total_units or 1))
        safe_unit_index = max(1, int(unit_index or 1))
        prefix = f"【{safe_unit_index}/{safe_total_units}】"
        content = (text or '').strip()
        return f"{prefix}{content}" if content else prefix

    def _apply_delivery_unit_numbering(self, delivery_steps, unit_index: int, total_units: int, card_type: str = None):
        """为多数量订单中的 text/data/api 步骤补充序号。"""
        if max(1, int(total_units or 1)) <= 1:
            return delivery_steps or []

        normalized_card_type = str(card_type or '').strip().lower()
        if normalized_card_type not in {'text', 'data', 'api'}:
            return delivery_steps or []

        steps = [dict(step or {}) for step in (delivery_steps or [])]
        prefix = f"【{max(1, int(unit_index or 1))}/{max(1, int(total_units or 1))}】"

        for step in steps:
            if step.get('type') == 'text':
                step['content'] = f"{prefix}{(step.get('content') or '').strip()}"
                return steps

        return [{'type': 'text', 'content': prefix}] + steps

    def _build_delivery_send_groups(self, prepared_units, total_units: int,
                                    max_units_per_message: int = DELIVERY_BATCH_MAX_UNITS,
                                    max_chars_per_message: int = DELIVERY_BATCH_MAX_CHARS):
        """按数量和字符数双阈值生成发货发送批次。"""
        if max(1, int(total_units or 1)) <= 1:
            return [{
                'mode': 'single',
                'units': [prepared_unit],
                'delivery_steps': prepared_unit.get('delivery_steps') or [],
                'unit_count': 1,
                'char_count': 0,
            } for prepared_unit in sorted(prepared_units or [], key=lambda unit: int(unit.get('unit_index') or 0))]

        groups = []
        current_batch_units = []
        current_batch_chars = 0

        def flush_current_batch():
            nonlocal current_batch_units, current_batch_chars
            if not current_batch_units:
                return

            batched_text = '\n\n'.join(unit['batched_text'] for unit in current_batch_units)
            groups.append({
                'mode': 'batched_text',
                'units': list(current_batch_units),
                'delivery_steps': [{'type': 'text', 'content': batched_text}],
                'unit_count': len(current_batch_units),
                'char_count': len(batched_text),
            })
            current_batch_units = []
            current_batch_chars = 0

        for prepared_unit in sorted(prepared_units or [], key=lambda unit: int(unit.get('unit_index') or 0)):
            delivery_steps = prepared_unit.get('delivery_steps') or []
            rule_meta = prepared_unit.get('rule_meta') or {}
            card_type = prepared_unit.get('card_type') or rule_meta.get('card_type')

            if not self._can_batch_text_delivery(delivery_steps, card_type):
                flush_current_batch()
                numbered_steps = self._apply_delivery_unit_numbering(
                    delivery_steps,
                    prepared_unit.get('unit_index') or 1,
                    total_units,
                    card_type,
                )
                groups.append({
                    'mode': 'single',
                    'units': [prepared_unit],
                    'delivery_steps': numbered_steps,
                    'unit_count': 1,
                    'char_count': 0,
                })
                continue

            numbered_text = self._format_delivery_unit_text(
                delivery_steps[0].get('content') or '',
                prepared_unit.get('unit_index') or 1,
                total_units,
            )

            if len(numbered_text) > max_chars_per_message:
                flush_current_batch()
                logger.warning(
                    f"【{self.cookie_id}】发货单元 {prepared_unit.get('unit_index')} 文本长度 {len(numbered_text)} 超过批量阈值 {max_chars_per_message}，回退为单条发送"
                )
                groups.append({
                    'mode': 'single',
                    'units': [prepared_unit],
                    'delivery_steps': [{'type': 'text', 'content': numbered_text}],
                    'unit_count': 1,
                    'char_count': len(numbered_text),
                })
                continue

            separator_chars = 2 if current_batch_units else 0
            exceeds_unit_limit = len(current_batch_units) >= max_units_per_message
            exceeds_char_limit = current_batch_units and (
                current_batch_chars + separator_chars + len(numbered_text) > max_chars_per_message
            )

            if exceeds_unit_limit or exceeds_char_limit:
                flush_current_batch()

            prepared_unit_with_text = dict(prepared_unit)
            prepared_unit_with_text['batched_text'] = numbered_text
            current_batch_units.append(prepared_unit_with_text)
            current_batch_chars += (2 if len(current_batch_units) > 1 else 0) + len(numbered_text)

        flush_current_batch()
        return groups

    async def _send_delivery_steps(self, websocket, chat_id: str, user_id: str, delivery_steps, user_url: str = None,
                                   log_prefix: str = "自动发货", card_id: int = None):
        """按顺序发送发货步骤，支持文本与图片混排。"""
        steps = delivery_steps or []
        if not steps:
            raise ValueError("发货步骤为空")

        total_steps = len(steps)
        user_url = user_url or f'https://www.goofish.com/personal?userId={user_id}'

        for index, step in enumerate(steps, start=1):
            step_type = step.get('type')
            step_content = step.get('content') or ''

            if step_type == 'image':
                image_data = step_content.replace("__IMAGE_SEND__", "", 1)
                image_card_id = card_id
                image_url = image_data
                if "|" in image_data:
                    card_id_str, image_url = image_data.split("|", 1)
                    try:
                        image_card_id = int(card_id_str)
                    except ValueError:
                        logger.error(f"无效的卡券ID: {card_id_str}")
                        image_card_id = card_id

                await self.send_image_msg(websocket, chat_id, user_id, image_url, card_id=image_card_id)
                logger.info(
                    f"【{log_prefix}】步骤 {index}/{total_steps} 已向 {user_url} 发送图片: {image_url}"
                )
            else:
                await self.send_msg(websocket, chat_id, user_id, step_content)
                logger.info(
                    f"【{log_prefix}】步骤 {index}/{total_steps} 已向 {user_url} 发送文本内容"
                )

            if total_steps > 1 and index < total_steps:
                await asyncio.sleep(0.3)

    async def _get_api_card_content(self, rule, order_id=None, item_id=None, buyer_id=None, spec_name=None, spec_value=None, retry_count=0):
        """调用API获取卡券内容，支持动态参数替换和重试机制"""
        max_retries = 4

        if retry_count >= max_retries:
            logger.error(f"API调用失败，已达到最大重试次数({max_retries})")
            return None

        try:
            import aiohttp
            import json

            api_config = rule.get('api_config')
            if not api_config:
                logger.error(f"API配置为空，规则ID: {rule.get('id')}, 卡券名称: {rule.get('card_name')}")
                logger.warning(f"规则详情: {rule}")
                return None

            # 解析API配置
            if isinstance(api_config, str):
                api_config = json.loads(api_config)

            url = api_config.get('url')
            method = api_config.get('method', 'GET').upper()
            timeout = api_config.get('timeout', 10)
            headers = api_config.get('headers', '{}')
            params = api_config.get('params', '{}')

            # 解析headers和params
            if isinstance(headers, str):
                headers = json.loads(headers)
            if isinstance(params, str):
                params = json.loads(params)

            # 有动态参数时进行替换（GET 的 query 参数同样需要，
            # 否则 {order_id} 等占位符会原样发给对方接口）
            if params:
                params = await self._replace_api_dynamic_params(params, order_id, item_id, buyer_id, spec_name, spec_value)

            retry_info = f" (重试 {retry_count + 1}/{max_retries})" if retry_count > 0 else ""
            logger.info(f"调用API获取卡券: {method} {url}{retry_info}")
            if method == 'POST' and params:
                logger.warning(f"POST请求参数: {json.dumps(params, ensure_ascii=False)}")

            # 确保session存在
            if not self.session:
                await self.create_session()

            # 发起HTTP请求
            timeout_obj = aiohttp.ClientTimeout(total=timeout)

            if method == 'GET':
                async with self.session.get(url, headers=headers, params=params, timeout=timeout_obj) as response:
                    status_code = response.status
                    response_text = await response.text()
            elif method == 'POST':
                async with self.session.post(url, headers=headers, json=params, timeout=timeout_obj) as response:
                    status_code = response.status
                    response_text = await response.text()
            else:
                logger.error(f"不支持的HTTP方法: {method}")
                return None

            if status_code == 200:
                # 尝试解析JSON响应，如果失败则使用原始文本
                try:
                    result = json.loads(response_text)
                    # 如果返回的是对象，尝试提取常见的内容字段
                    if isinstance(result, dict):
                        content = result.get('data') or result.get('content') or result.get('card') or str(result)
                    else:
                        content = str(result)
                except Exception:
                    content = response_text

                logger.info(f"API调用成功，返回内容长度: {len(content)}")
                return content
            else:
                logger.warning(f"API调用失败: {status_code} - {response_text[:200]}...")

                # 如果是服务器错误(5xx)或请求超时，进行重试
                if status_code >= 500 or status_code == 408:
                    if retry_count < max_retries - 1:
                        wait_time = (retry_count + 1) * 2  # 递增等待时间: 2s, 4s, 6s
                        logger.info(f"等待 {wait_time} 秒后重试...")
                        await asyncio.sleep(wait_time)
                        return await self._get_api_card_content(rule, order_id, item_id, buyer_id, spec_name, spec_value, retry_count + 1)

                return None

        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"API调用网络异常: {self._safe_str(e)}")

            # 网络异常也进行重试
            if retry_count < max_retries - 1:
                wait_time = (retry_count + 1) * 2  # 递增等待时间
                logger.info(f"等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)
                return await self._get_api_card_content(rule, order_id, item_id, buyer_id, spec_name, spec_value, retry_count + 1)
            else:
                logger.error(f"API调用网络异常，已达到最大重试次数: {self._safe_str(e)}")
                return None

        except Exception as e:
            logger.error(f"API调用异常: {self._safe_str(e)}")
            return None

    async def _get_yifan_api_card_content(self, rule, order_id=None, item_id=None, buyer_id=None, chat_id=None):
        """调用亦凡卡劵API获取内容"""
        try:
            import hashlib
            import time
            import aiohttp
            import json
            from urllib.parse import urlencode

            # 获取API配置（存储在api_config字段中）
            api_config = rule.get('api_config')
            if not api_config:
                logger.error(f"亦凡API配置为空，规则ID: {rule.get('id')}, 卡券名称: {rule.get('card_name')}")
                return None

            # 解析API配置
            if isinstance(api_config, str):
                api_config = json.loads(api_config)

            # 亦凡API配置直接存储在api_config字段中
            user_id = api_config.get('user_id')
            user_key = api_config.get('user_key')
            goods_id = api_config.get('goods_id')
            # 回调地址：优先使用卡券配置中的，如果没有则从全局配置读取，最后使用默认地址
            callback_url = (api_config.get('callback_url') or '').strip() or (YIFAN_API.get('callback_url') or '').strip() or 'http://116.196.116.76/yifan.php'
            require_account = api_config.get('require_account', False)

            if not user_id or not user_key or not goods_id:
                logger.error(f"亦凡API配置不完整，规则ID: {rule.get('id')}")
                return None

            # 如果需要充值账号，先进行账号询问和确认流程
            recharge_account = None
            if require_account:
                logger.info(f"亦凡API需要充值账号，开始询问流程")
                recharge_account = await self._ask_for_recharge_account(chat_id, buyer_id, rule, order_id, item_id)
                if recharge_account == "__WAITING_ACCOUNT__":
                    # 已设置等待状态，暂时中断发货流程
                    logger.info(f"已设置等待账号输入状态，暂停发货流程")
                    return None
                elif not recharge_account:
                    logger.error(f"获取充值账号失败，取消发货")
                    return None
                logger.info(f"获取到充值账号: {recharge_account}")

            # 构建API请求参数（所有值都转换为字符串，避免空格问题）
            timestamp = str(int(time.time()))
            params = {
                'userid': str(user_id),
                'timestamp': timestamp,
                'goodsid': str(goods_id),
                'buynum': '1',
            }

            # 如果有回调地址，添加到参数中（签名之前添加）
            if callback_url and callback_url.strip():
                params['callbackurl'] = str(callback_url).strip()

            # 如果有充值账号，添加到参数中
            if recharge_account:
                params['attach'] = str(recharge_account).strip()

            # 生成签名（确保参数值没有空格）
            # 1. 按照key的ascii码从小到大排序
            # 2. 空值不参与签名
            # 3. 使用QueryString格式拼接
            # 4. 尾部追加商户KEY
            # 5. MD5后转成32位小写
            sign_params = {k: str(v).strip() for k, v in params.items() if v is not None and str(v).strip() != ''}
            sorted_keys = sorted(sign_params.keys())
            sign_string = '&'.join([f"{key}={sign_params[key]}" for key in sorted_keys])
            sign_string += user_key
            
            logger.info(f"亦凡API签名字符串: {sign_string}")
            
            sign = hashlib.md5(sign_string.encode('utf-8')).hexdigest().lower()
            params['sign'] = sign

            logger.info(f"调用亦凡API: 商户ID={user_id}, 商品ID={goods_id}, 充值账号={recharge_account}, 回调URL={callback_url if callback_url else '无'}")

            # 确保session存在
            if not self.session:
                await self.create_session()

            # 发起API请求（使用data而不是json，发送form格式）
            api_url = "http://price.78shuk.top/dockapiv3/order/create"
            
            timeout_obj = aiohttp.ClientTimeout(total=30)
            async with self.session.post(api_url, data=params, timeout=timeout_obj) as response:
                status_code = response.status
                response_text = await response.text()

                logger.info(f"亦凡API返回状态码: {status_code}, 响应: {response_text}")

                if status_code == 200:
                    try:
                        result = json.loads(response_text)
                        # 根据亦凡API的返回格式处理：code为1表示成功
                        if result.get('code') == 1:
                            # 提取订单信息
                            data = result.get('data', {})
                            order_no = data.get('orderno', '')
                            us_order_no = data.get('usorderno', '')
                            
                            # 构建成功消息
                            success_msg = f"✅ 自动发货订单已提交成功\n\n"
                            success_msg += f"📋 订单信息：\n"
                            success_msg += f"平台订单号: {order_no}\n"
                            if us_order_no:
                                success_msg += f"商家订单号: {us_order_no}\n"
                            
                            # 添加查询地址（从全局配置读取）
                            query_url = YIFAN_API.get('query_url', 'http://116.196.116.76/yifan.php')
                            success_msg += f"\n🔍 查询卡密：\n"
                            success_msg += f"{query_url}\n"
                            success_msg += f"(输入订单号查询)\n"
                            
                            # 添加提示信息
                            success_msg += f"\n⏰ 温馨提示：\n"
                            success_msg += f"订单处理需要一定时间，请耐心等待。\n"
                            success_msg += f"如果1小时后仍未看到卡密信息，\n"
                            success_msg += f"请联系客服处理。"
                            
                            logger.info(f"亦凡API调用成功: order_no={order_no}")
                            
                            # 将亦凡订单号记录到数据库（用于后续回调匹配）
                            if order_id and order_no:
                                try:
                                    from db_manager import db_manager
                                    # 更新订单的亦凡订单号和chat_id
                                    db_manager.update_order_yifan_status(
                                        order_id=order_id,
                                        yifan_orderno=order_no,
                                        delivery_status='processing'
                                    )
                                    if chat_id:
                                        db_manager.update_order_chat_id(order_id, chat_id)
                                    logger.info(f"已记录亦凡订单信息: order_id={order_id}, yifan_orderno={order_no}")
                                except Exception as e:
                                    logger.error(f"记录亦凡订单信息失败: {e}")
                            
                            return success_msg
                        else:
                            # code不为1，下单失败，需要通知用户
                            error_msg = result.get('msg', '未知错误')
                            logger.error(f"亦凡API调用失败: code={result.get('code')}, msg={error_msg}")
                            
                            # 发送通知给用户
                            if chat_id and buyer_id:
                                from db_manager import db_manager
                                notification_msg = f"❌ 自动发货失败\n错误信息: {error_msg}\n请联系客服处理"
                                await self.send_notification("系统", buyer_id, notification_msg, item_id or "unknown", chat_id)
                            
                            return None
                    except Exception as e:
                        logger.error(f"解析亦凡API返回失败: {self._safe_str(e)}")
                        return None
                else:
                    logger.error(f"亦凡API调用失败: HTTP {status_code} - {response_text[:200]}")
                    return None

        except Exception as e:
            logger.error(f"亦凡API调用异常: {self._safe_str(e)}")
            return None

    async def _call_yifan_api_with_account(self, rule, account, order_id=None, item_id=None, buyer_id=None, chat_id=None):
        """使用确认的账号调用亦凡API"""
        try:
            import hashlib
            import time
            import aiohttp
            import json

            # 获取API配置
            api_config = rule.get('api_config')
            if not api_config:
                logger.error(f"亦凡API配置为空")
                return None

            # 解析API配置
            if isinstance(api_config, str):
                api_config = json.loads(api_config)

            # 亦凡API配置直接存储在api_config字段中
            user_id = api_config.get('user_id')
            user_key = api_config.get('user_key')
            goods_id = api_config.get('goods_id')
            callback_url = api_config.get('callback_url', '')

            if not user_id or not user_key or not goods_id:
                logger.error(f"亦凡API配置不完整")
                return None

            # 构建API请求参数（所有值都转换为字符串，避免空格问题）
            timestamp = str(int(time.time()))
            params = {
                'userid': str(user_id),
                'timestamp': timestamp,
                'goodsid': str(goods_id),
                'buynum': '1',
                'attach': str(account).strip()  # 充值账号，去除首尾空格
            }

            # 如果有回调地址，添加到参数中（签名之前添加）
            if callback_url and callback_url.strip():
                params['callbackurl'] = str(callback_url).strip()

            # 生成签名（确保参数值没有空格）
            sign_params = {k: str(v).strip() for k, v in params.items() if v is not None and str(v).strip() != ''}
            sorted_keys = sorted(sign_params.keys())
            sign_string = '&'.join([f"{key}={sign_params[key]}" for key in sorted_keys])
            sign_string += user_key
            
            logger.info(f"亦凡API签名字符串: {sign_string}")
            
            sign = hashlib.md5(sign_string.encode('utf-8')).hexdigest().lower()
            params['sign'] = sign

            logger.info(f"调用亦凡API: 商户ID={user_id}, 商品ID={goods_id}, 充值账号={account}, 回调URL={callback_url if callback_url else '无'}")

            # 确保session存在
            if not self.session:
                await self.create_session()

            # 发起API请求（使用data而不是json，发送form格式）
            api_url = "http://price.78shuk.top/dockapiv3/order/create"
            
            timeout_obj = aiohttp.ClientTimeout(total=30)
            async with self.session.post(api_url, data=params, timeout=timeout_obj) as response:
                status_code = response.status
                response_text = await response.text()

                logger.info(f"亦凡API返回状态码: {status_code}, 响应: {response_text}")

                if status_code == 200:
                    try:
                        result = json.loads(response_text)
                        if result.get('code') == 1:
                            # 下单成功
                            data = result.get('data', {})
                            order_no = data.get('orderno', '')
                            us_order_no = data.get('usorderno', '')
                            
                            success_msg = f"✅ 下单成功\n"
                            success_msg += f"订单号: {order_no}\n"
                            if us_order_no:
                                success_msg += f"用户订单号: {us_order_no}\n"
                            success_msg += f"充值账号: {account}\n"
                            success_msg += f"返回信息: {result.get('msg', '提交成功')}\n"
                            success_msg += f"有任何问题，请及时联系客服处理。"
                            
                            logger.info(f"亦凡API调用成功: {success_msg}")
                            return success_msg
                        else:
                            # 下单失败
                            error_msg = result.get('msg', '未知错误')
                            logger.error(f"亦凡API调用失败: code={result.get('code')}, msg={error_msg}")
                            
                            # 发送通知给用户
                            if chat_id and buyer_id:
                                from db_manager import db_manager
                                notification_msg = f"❌ 自动发货失败\n错误信息: {error_msg}\n请联系客服处理"
                                await self.send_notification("系统", buyer_id, notification_msg, item_id or "unknown", chat_id)
                            
                            return None
                    except Exception as e:
                        logger.error(f"解析亦凡API返回失败: {self._safe_str(e)}")
                        return None
                else:
                    logger.error(f"亦凡API调用失败: HTTP {status_code} - {response_text[:200]}")
                    return None

        except Exception as e:
            logger.error(f"亦凡API调用异常: {self._safe_str(e)}")
            return None

    async def _ask_for_recharge_account(self, chat_id, buyer_id, rule, order_id=None, item_id=None):
        """询问客户充值账号并设置等待状态（不阻塞）"""
        try:
            async with self.yifan_account_lock:
                # 设置等待状态
                self.yifan_account_waiting[chat_id] = {
                    'buyer_id': buyer_id,
                    'rule': rule,
                    'order_id': order_id,
                    'item_id': item_id,
                    'state': 'waiting_account',  # waiting_account 或 waiting_confirm
                    'account': None,
                    'create_time': time.time(),
                    'retry_count': 0
                }
            
            # 发送询问消息
            ask_message = "请单独发送您的充值账号，不要有任何其他的文字。如果因为您输错的原因导致错误下单，概不退款。"
            await self.send_msg(self.ws, chat_id, buyer_id, ask_message)
            logger.info(f"已发送充值账号询问消息，等待用户回复")
            
            # 返回特殊标记，表示需要等待用户输入
            return "__WAITING_ACCOUNT__"

        except Exception as e:
            logger.error(f"询问充值账号异常: {self._safe_str(e)}")
            return None

    async def _replace_api_dynamic_params(self, params, order_id=None, item_id=None, buyer_id=None, spec_name=None, spec_value=None):
        """替换API请求参数中的动态参数"""
        try:
            if not params or not isinstance(params, dict):
                return params

            # 获取订单和商品信息
            order_info = None
            item_info = None

            # 如果有订单ID，获取订单信息
            if order_id:
                try:
                    from db_manager import db_manager
                    # 尝试从数据库获取订单信息
                    order_info = db_manager.get_order_by_id(order_id)
                    if not order_info:
                        # 如果数据库中没有，尝试通过API获取
                        order_detail = await self.fetch_order_detail_info(order_id, item_id, buyer_id)
                        if order_detail:
                            order_info = order_detail
                            logger.warning(f"通过API获取到订单信息: {order_id}")
                        else:
                            logger.warning(f"无法获取订单信息: {order_id}")
                    else:
                        logger.warning(f"从数据库获取到订单信息: {order_id}")
                except Exception as e:
                    logger.warning(f"获取订单信息失败: {self._safe_str(e)}")

            # 如果有商品ID，获取商品信息
            if item_id:
                try:
                    from db_manager import db_manager
                    item_info = db_manager.get_item_info(self.cookie_id, item_id)
                    if item_info:
                        logger.warning(f"从数据库获取到商品信息: {item_id}")
                    else:
                        logger.warning(f"无法获取商品信息: {item_id}")
                except Exception as e:
                    logger.warning(f"获取商品信息失败: {self._safe_str(e)}")

            # 构建参数映射
            param_mapping = {
                'order_id': order_id or '',
                'item_id': item_id or '',
                'buyer_id': buyer_id or '',
                'cookie_id': self.cookie_id or '',
                'spec_name': spec_name or '',
                'spec_value': spec_value or '',
                'timestamp': str(int(time.time())),
            }

            # 从订单信息中提取参数
            if order_info:
                param_mapping.update({
                    'order_amount': str(order_info.get('amount', '')),
                    'order_quantity': str(order_info.get('quantity', '')),
                })

            # 从商品信息中提取参数
            if item_info:
                # 处理商品详情，如果是JSON字符串则提取detail字段
                item_detail = item_info.get('item_detail', '')
                if item_detail:
                    try:
                        # 尝试解析JSON
                        import json
                        detail_data = json.loads(item_detail)
                        if isinstance(detail_data, dict) and 'detail' in detail_data:
                            item_detail = detail_data['detail']
                    except (json.JSONDecodeError, TypeError):
                        # 如果不是JSON或解析失败，使用原始字符串
                        pass

                param_mapping.update({
                    'item_detail': item_detail,
                })

            # 递归替换参数
            replaced_params = self._recursive_replace_params(params, param_mapping)

            # 记录替换的参数
            replaced_keys = []
            for key, value in replaced_params.items():
                if isinstance(value, str) and '{' in str(params.get(key, '')):
                    replaced_keys.append(key)

            if replaced_keys:
                logger.info(f"API动态参数替换完成，替换的参数: {replaced_keys}")
                logger.warning(f"参数映射: {param_mapping}")

            return replaced_params

        except Exception as e:
            logger.error(f"替换API动态参数失败: {self._safe_str(e)}")
            return params

    def _recursive_replace_params(self, obj, param_mapping):
        """递归替换参数中的占位符"""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                result[key] = self._recursive_replace_params(value, param_mapping)
            return result
        elif isinstance(obj, list):
            return [self._recursive_replace_params(item, param_mapping) for item in obj]
        elif isinstance(obj, str):
            # 替换字符串中的占位符
            result = obj
            for param_key, param_value in param_mapping.items():
                placeholder = f"{{{param_key}}}"
                if placeholder in result:
                    result = result.replace(placeholder, str(param_value))
            return result
        else:
            return obj

    async def token_refresh_loop(self):
        """会话保活循环。轻量保活优先，重型恢复兜底。"""
        try:
            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止Token刷新循环")
                        break

                    current_time = time.time()
                    if self._is_account_pause_status(getattr(self, 'last_token_refresh_status', None)):
                        logger.warning(f"【{self.cookie_id}】账号处于人工验证/风控暂停状态，暂停会话保活循环")
                        await self._interruptible_sleep(300)
                        continue

                    if self._should_defer_auth_recovery_for_qr_grace(current_time):
                        await self._interruptible_sleep(max(60, self._get_qr_login_grace_remaining_seconds(current_time)))
                        continue

                    effective_keepalive_interval = self._get_effective_keepalive_interval()
                    if current_time - self.last_session_keepalive_time >= effective_keepalive_interval:
                        logger.info(f"【{self.cookie_id}】开始执行轻量会话保活...")
                        keepalive_ok = await self.keep_session_alive()
                        if keepalive_ok:
                            await self._interruptible_sleep(60)
                            continue

                        keepalive_status = getattr(self, 'last_session_keepalive_status', None)
                        if keepalive_status == "auth_failed":
                            logger.warning(f"【{self.cookie_id}】轻量保活鉴权失败，尝试执行重型Token恢复流程")
                            new_token = await self.refresh_token()
                            if new_token:
                                self.last_session_keepalive_time = time.time()
                                logger.info(f"【{self.cookie_id}】重型Token恢复成功，主动关闭旧WebSocket以使用新Token重连")
                                await self._force_websocket_reconnect("重型Token恢复成功，准备使用新Token重连")
                                break

                            last_refresh_status = getattr(self, 'last_token_refresh_status', None)
                            benign_refresh_statuses = ("skipped_cooldown", "restarted_after_cookie_refresh")
                            if last_refresh_status not in benign_refresh_statuses:
                                scheduled_error_message = self._build_scheduled_token_refresh_error_message(last_refresh_status)
                                await self.send_token_refresh_notification(
                                    scheduled_error_message,
                                    "token_scheduled_refresh_failed"
                                )
                            logger.warning(
                                f"【{self.cookie_id}】重型Token恢复失败(status={last_refresh_status})，"
                                f"{self._compute_token_retry_wait_seconds(current_time)} 秒后重试"
                            )
                            await self._interruptible_sleep(self._compute_token_retry_wait_seconds(current_time))
                        else:
                            logger.warning(
                                f"【{self.cookie_id}】轻量保活失败(status={keepalive_status})，"
                                f"{self.session_keepalive_retry_interval} 秒后重试"
                            )
                            await self._interruptible_sleep(self.session_keepalive_retry_interval)
                        continue
                    await self._interruptible_sleep(60)
                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】Token刷新循环收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"Token刷新循环出错: {self._safe_str(e)}")
                    # 出错后也等待1分钟再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(60)
                    except asyncio.CancelledError:
                        logger.info(f"【{self.cookie_id}】Token刷新循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】Token刷新循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】Token刷新循环已退出")

    async def create_chat(self, ws, toid, item_id='891198795482'):
        msg = {
            "lwp": "/r/SingleChatConversation/create",
            "headers": {
                "mid": generate_mid()
            },
            "body": [
                {
                    "pairFirst": f"{toid}@goofish",
                    "pairSecond": f"{self.myid}@goofish",
                    "bizType": "1",
                    "extension": {
                        "itemId": item_id
                    },
                    "ctx": {
                        "appVersion": "1.0",
                        "platform": "web"
                    }
                }
            ]
        }
        await ws.send(json.dumps(msg))

    async def send_msg(self, ws, cid, toid, text):
        text = {
            "contentType": 1,
            "text": {
                "text": text
            }
        }
        text_base64 = str(base64.b64encode(json.dumps(text).encode('utf-8')), 'utf-8')
        msg = {
            "lwp": "/r/MessageSend/sendByReceiverScope",
            "headers": {
                "mid": generate_mid()
            },
            "body": [
                {
                    "uuid": generate_uuid(),
                    "cid": f"{cid}@goofish",
                    "conversationType": 1,
                    "content": {
                        "contentType": 101,
                        "custom": {
                            "type": 1,
                            "data": text_base64
                        }
                    },
                    "redPointPolicy": 0,
                    "extension": {
                        "extJson": "{}"
                    },
                    "ctx": {
                        "appVersion": "1.0",
                        "platform": "web"
                    },
                    "mtags": {},
                    "msgReadStatusSetting": 1
                },
                {
                    "actualReceivers": [
                        f"{toid}@goofish",
                        f"{self.myid}@goofish"
                    ]
                }
            ]
        }
        await ws.send(json.dumps(msg))

    async def init(self, ws):
        # 如果没有token或者token过期，获取新token
        token_refresh_attempted = False
        if not self.current_token or (time.time() - self.last_token_refresh_time) >= self.token_refresh_interval:
            if self._should_defer_auth_recovery_for_qr_grace():
                raise InitAuthError(self.last_token_refresh_error_message or "扫码登录稳定期中，暂缓初始化Token预检")

            logger.info(f"【{self.cookie_id}】获取初始token...")
            token_refresh_attempted = True

            await self.refresh_token()

        if not self.current_token:
            self.last_init_failure_type = 'init_auth_failed'
            self.last_init_failure_reason = self.last_token_refresh_status or 'token_missing_after_refresh'
            logger.error(f"【{self.cookie_id}】无法获取有效token，初始化鉴权失败")
            # 只有在没有尝试刷新token的情况下才发送通知，避免与refresh_token中的通知重复
            if not token_refresh_attempted:
                await self.send_token_refresh_notification("初始化时无法获取有效Token", "token_init_failed")
            else:
                logger.info(f"【{self.cookie_id}】由于刚刚尝试过token刷新，跳过重复的初始化失败通知")
            raise InitAuthError(f"Token获取失败(status={self.last_init_failure_reason})")

        self.last_init_failure_type = None
        self.last_init_failure_reason = None
        self.clear_init_auth_failure_state(self.cookie_id)
        self.init_auth_failures = 0

        msg = {
            "lwp": "/reg",
            "headers": {
                "cache-header": "app-key token ua wv",
                "app-key": APP_CONFIG.get('app_key'),
                "token": self.current_token,
                "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 DingTalk(2.1.5) OS(Windows/10) Browser(Chrome/133.0.0.0) DingWeb/2.1.5 IMPaaS DingWeb/2.1.5",
                "dt": "j",
                "wv": "im:3,au:3,sy:6",
                "sync": "0,0;0;0;",
                "did": self.device_id,
                "mid": generate_mid()
            }
        }
        await ws.send(json.dumps(msg))
        await asyncio.sleep(1)
        current_time = int(time.time() * 1000)
        msg = {
            "lwp": "/r/SyncStatus/ackDiff",
            "headers": {"mid": generate_mid()},
            "body": [
                {
                    "pipeline": "sync",
                    "tooLong2Tag": "PNM,1",
                    "channel": "sync",
                    "topic": "sync",
                    "highPts": 0,
                    "pts": current_time * 1000,
                    "seq": 0,
                    "timestamp": current_time
                }
            ]
        }
        await ws.send(json.dumps(msg))
        logger.info(f'【{self.cookie_id}】连接注册完成')

    async def _ack_lwp_message(self, ws, message: dict) -> None:
        try:
            headers = message.get("headers", {}) if isinstance(message, dict) else {}
            ack = {
                "code": 200,
                "headers": {
                    "mid": headers.get("mid", generate_mid()),
                    "sid": headers.get("sid", ""),
                }
            }
            if 'app-key' in headers:
                ack["headers"]["app-key"] = headers["app-key"]
            if 'ua' in headers:
                ack["headers"]["ua"] = headers["ua"]
            if 'dt' in headers:
                ack["headers"]["dt"] = headers["dt"]
            await ws.send(json.dumps(ack))
        except Exception:
            pass

    def _resolve_lwp_response_waiter(self, message_data: dict) -> bool:
        try:
            headers = message_data.get("headers", {}) if isinstance(message_data, dict) else {}
            mid = str(headers.get("mid") or "")
            if not mid:
                return False
            future = self.lwp_response_waiters.pop(mid, None)
            if not future:
                return False
            if not future.done():
                future.set_result(message_data)
            return True
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】分发LWP响应失败: {self._safe_str(e)}")
            return False

    async def _request_lwp_on_current_ws(self, lwp: str, body: list, timeout: int = 10):
        ws = self.ws
        if not ws or getattr(ws, 'closed', False):
            return {"code": "not_connected", "reason": "账号WebSocket未连接"}

        mid = generate_mid()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.lwp_response_waiters[mid] = future
        try:
            await ws.send(json.dumps({
                "lwp": lwp,
                "headers": {"mid": mid},
                "body": body,
            }))
            response = await asyncio.wait_for(future, timeout=max(1, int(timeout or 10)))
            return response.get("body", {}) if isinstance(response, dict) else {}
        except asyncio.TimeoutError:
            return {"code": "timeout", "reason": "IM请求超时"}
        except Exception as e:
            return {"code": "request_failed", "reason": self._safe_str(e)}
        finally:
            current = self.lwp_response_waiters.pop(mid, None)
            if current and not current.done():
                current.cancel()

    async def list_all_conversations(self, cid: str, page_size: int = 20):
        """拉取指定会话的历史消息。"""
        logger.info(f"【{self.cookie_id}】开始通过独立临时连接拉取历史消息: chat_id={cid}, page_size={page_size}")
        headers = self._build_websocket_headers()
        async with await self._create_websocket_connection(headers) as websocket:
            await self.init(websocket)
            send_mid = generate_mid()
            request_msg = {
                "lwp": "/r/MessageManager/listUserMessages",
                "headers": {
                    "mid": send_mid
                },
                "body": [
                    f"{cid}@goofish",
                    False,
                    9007199254740991,
                    page_size,
                    False
                ]
            }
            history_messages = []
            response_timeout = 10

            await websocket.send(json.dumps(request_msg))

            while True:
                try:
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=response_timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"【{self.cookie_id}】历史消息拉取等待响应超时: chat_id={cid}, "
                        f"fetched={len(history_messages)}, timeout={response_timeout}s"
                    )
                    return history_messages
                except Exception as recv_exc:
                    logger.warning(
                        f"【{self.cookie_id}】历史消息连接提前结束: chat_id={cid}, "
                        f"fetched={len(history_messages)}, error={self._safe_str(recv_exc)}"
                    )
                    return history_messages

                try:
                    message = json.loads(raw_message)
                except Exception:
                    continue

                try:
                    ack = {
                        "code": 200,
                        "headers": {
                            "mid": message.get("headers", {}).get("mid", generate_mid()),
                            "sid": message.get("headers", {}).get("sid", ""),
                        }
                    }
                    if 'app-key' in message.get("headers", {}):
                        ack["headers"]["app-key"] = message["headers"]["app-key"]
                    if 'ua' in message.get("headers", {}):
                        ack["headers"]["ua"] = message["headers"]["ua"]
                    if 'dt' in message.get("headers", {}):
                        ack["headers"]["dt"] = message["headers"]["dt"]
                    await websocket.send(json.dumps(ack))
                except Exception:
                    pass
                
                try:
                    if message.get('lwp') == "/s/vulcan":
                        continue

                    recv_mid = message.get("headers", {}).get("mid", "")
                    if recv_mid != send_mid:
                        continue

                    body = message.get("body", {})
                    has_more = body.get("hasMore") == 1
                    next_cursor = body.get("nextCursor")
                    for user_message in body.get("userMessageModels", []):
                        extension = user_message.get("message", {}).get("extension", {})
                        custom_content = user_message.get("message", {}).get("content", {}).get("custom", {})
                        send_message_base64 = custom_content.get("data", "")
                        parsed_message = None
                        if send_message_base64:
                            try:
                                parsed_message = json.loads(base64.b64decode(send_message_base64).decode('utf-8'))
                            except Exception:
                                parsed_message = {"raw": send_message_base64}

                        created_at = None
                        for candidate in (
                            user_message.get("createTime"),
                            user_message.get("gmtCreate"),
                            user_message.get("createdAt"),
                            user_message.get("messageTime"),
                            user_message.get("sendTime"),
                            user_message.get("timestamp"),
                            extension.get("createTime") if isinstance(extension, dict) else None,
                        ):
                            if candidate not in (None, "", 0, "0"):
                                created_at = candidate
                                break

                        history_messages.insert(0, {
                            "send_user_id": extension.get("senderUserId", ""),
                            "send_user_name": extension.get("senderNick") or extension.get("reminderTitle", ""),
                            "message": parsed_message,
                            "message_extension": extension,
                            "created_at": created_at,
                        })

                    if has_more:
                        send_mid = generate_mid()
                        request_msg["headers"]["mid"] = send_mid
                        request_msg["body"][2] = next_cursor
                        await websocket.send(json.dumps(request_msg))
                    else:
                        logger.info(f"【{self.cookie_id}】历史消息拉取完成: chat_id={cid}, fetched={len(history_messages)}")
                        return history_messages
                except Exception as e:
                    logger.warning(f"【{self.cookie_id}】拉取历史消息时发生异常: {self._safe_str(e)}")
                    return history_messages

        return []

    async def list_newest_conversations(self, start_timestamp: int = None, limit: int = 20):
        """通过当前主WebSocket拉取最近会话列表，避免临时连接挤掉主监听。"""
        if start_timestamp in (None, '', 0, '0'):
            start_timestamp = 9007199254740991

        try:
            start_timestamp = int(start_timestamp)
        except (TypeError, ValueError):
            start_timestamp = 9007199254740991

        try:
            limit = max(1, min(int(limit or 20), 100))
        except (TypeError, ValueError):
            limit = 20

        logger.info(
            f"【{self.cookie_id}】开始通过主连接拉取最近会话: "
            f"cursor={start_timestamp}, limit={limit}"
        )

        last_body = {}
        for attempt in range(3):
            body = await self._request_lwp_on_current_ws(
                "/r/Conversation/listNewestPagination",
                [start_timestamp, limit],
                timeout=10,
            )
            last_body = body if isinstance(body, dict) else {}
            if isinstance(last_body, dict) and last_body.get("code") == "400600001" and attempt < 2:
                wait_seconds = (attempt + 1) * 2
                logger.warning(
                    f"【{self.cookie_id}】最近会话拉取被流控，{wait_seconds}秒后重试 "
                    f"({attempt + 1}/3)"
                )
                await asyncio.sleep(wait_seconds)
                continue
            logger.info(
                f"【{self.cookie_id}】最近会话拉取完成: "
                f"count={len(last_body.get('userConvs', [])) if isinstance(last_body, dict) else 0}"
            )
            return last_body

        return last_body

    async def list_conversation_messages_page(self, cid: str, start_timestamp: int = None, limit: int = 20):
        """通过当前主WebSocket分页拉取指定会话消息，避免临时连接挤掉主监听。"""
        normalized_cid = str(cid or '').strip()
        if not normalized_cid:
            return {"code": "missing_cid", "reason": "缺少会话ID"}
        full_cid = normalized_cid if '@goofish' in normalized_cid else f"{normalized_cid}@goofish"

        if start_timestamp in (None, '', 0, '0'):
            start_timestamp = 9007199254740991

        try:
            start_timestamp = int(start_timestamp)
        except (TypeError, ValueError):
            start_timestamp = 9007199254740991

        try:
            limit = max(1, min(int(limit or 20), 100))
        except (TypeError, ValueError):
            limit = 20

        logger.info(
            f"【{self.cookie_id}】开始通过主连接分页拉取消息: "
            f"chat_id={normalized_cid}, cursor={start_timestamp}, limit={limit}"
        )
        body = await self._request_lwp_on_current_ws(
            "/r/MessageManager/listUserMessages",
            [full_cid, False, start_timestamp, limit, False],
            timeout=10,
        )
        logger.info(
            f"【{self.cookie_id}】分页消息拉取完成: "
            f"chat_id={normalized_cid}, count={len(body.get('userMessageModels', [])) if isinstance(body, dict) else 0}"
        )
        return body if isinstance(body, dict) else {}

    async def fetch_conversation_history_once(self, cid: str, page_size: int = 20):
        """使用独立临时实例拉取历史消息，避免影响主连接状态。"""
        isolated_live = XianyuLive(
            cookies_str=self.cookies_str,
            cookie_id=self.cookie_id,
            user_id=self.user_id,
            register_instance=False,
        )
        isolated_live.current_token = self.current_token
        isolated_live.last_token_refresh_time = self.last_token_refresh_time
        isolated_live.proxy_config = dict(self.proxy_config or {})
        isolated_live.base_url = self.base_url
        logger.info(f"【{self.cookie_id}】已创建独立历史拉取实例: chat_id={cid}, page_size={page_size}")
        return await isolated_live.list_all_conversations(cid, page_size=page_size)

    async def fetch_conversation_history_with_fallback(self, cid: str, page_size: int = 20, isolated_timeout: int = 12):
        """优先使用独立临时实例拉取历史，超时后回退到主实例方式。"""
        try:
            return await asyncio.wait_for(
                self.fetch_conversation_history_once(cid, page_size=page_size),
                timeout=max(3, isolated_timeout),
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"【{self.cookie_id}】独立历史拉取超时，回退主实例方式: chat_id={cid}, "
                f"page_size={page_size}, timeout={isolated_timeout}s"
            )
        except Exception as isolated_exc:
            logger.warning(
                f"【{self.cookie_id}】独立历史拉取失败，回退主实例方式: chat_id={cid}, "
                f"error={self._safe_str(isolated_exc)}"
            )

        return await self.list_all_conversations(cid, page_size=page_size)

    def _extract_image_url_from_message(self, message: dict) -> Optional[str]:
        """从消息结构中提取图片URL"""
        try:
            message_1 = message.get('1', {})
            if not isinstance(message_1, dict):
                return None
            message_6 = message_1.get('6', {})
            if not isinstance(message_6, dict):
                return None
            message_6_3 = message_6.get('3', {})
            if not isinstance(message_6_3, dict):
                return None
            content_json_str = message_6_3.get('5', '')
            if content_json_str:
                import json as _json
                content_obj = _json.loads(content_json_str)
                pics = content_obj.get('image', {}).get('pics', [])
                if pics:
                    return pics[0].get('url', '')
        except Exception:
            pass
        return None

    async def send_heartbeat(self, ws):
        """发送心跳包"""
        # 检查WebSocket连接状态，如果已关闭则不发送
        if ws.closed:
            raise ConnectionError("WebSocket连接已关闭，无法发送心跳")
        
        heartbeat_mid = generate_mid()
        msg = {
            "lwp": "/!",
            "headers": {
                "mid": heartbeat_mid
            }
        }
        # 添加超时保护，避免在WebSocket关闭时阻塞
        try:
            self.last_sent_heartbeat_mid = heartbeat_mid
            self.pending_heartbeat_mids.append(heartbeat_mid)
            await asyncio.wait_for(ws.send(json.dumps(msg)), timeout=2.0)
            self.last_heartbeat_time = time.time()
            logger.warning(f"【{self.cookie_id}】心跳包已发送 [ID:{heartbeat_mid}]")
        except asyncio.TimeoutError:
            raise ConnectionError("心跳发送超时，WebSocket可能已断开")
        except asyncio.CancelledError:
            # 如果被取消，立即重新抛出，不执行后续操作
            raise

    async def heartbeat_loop(self, ws):
        """心跳循环"""
        consecutive_failures = 0
        max_failures = 3  # 连续失败3次后停止心跳

        try:
            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止心跳循环")
                        break

                    # 检查WebSocket连接状态
                    if ws.closed:
                        logger.warning(f"【{self.cookie_id}】WebSocket连接已关闭，停止心跳循环")
                        break

                    await self.send_heartbeat(ws)
                    consecutive_failures = 0  # 重置失败计数

                    await self._interruptible_sleep(self.heartbeat_interval)

                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】心跳循环收到取消信号，准备退出")
                    raise  # 重新抛出，让任务正常结束
                except Exception as e:
                    consecutive_failures += 1
                    logger.error(f"心跳发送失败 ({consecutive_failures}/{max_failures}): {self._safe_str(e)}")

                    if consecutive_failures >= max_failures:
                        logger.error(f"【{self.cookie_id}】心跳连续失败{max_failures}次，停止心跳循环")
                        break

                    # 失败后短暂等待再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(5)
                    except asyncio.CancelledError:
                        # 在等待重试时收到取消信号，立即退出
                        logger.info(f"【{self.cookie_id}】心跳循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】心跳循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】心跳循环已退出")

    async def handle_heartbeat_response(self, message_data):
        """处理心跳响应"""
        try:
            if not isinstance(message_data, dict):
                return False

            if message_data.get("code") != 200:
                return False

            if self.is_sync_package(message_data):
                return False

            headers = message_data.get("headers")
            if not isinstance(headers, dict):
                return False

            response_mid = str(headers.get("mid") or "")
            if not response_mid or response_mid not in self.pending_heartbeat_mids:
                return False

            self.last_heartbeat_response = time.time()
            try:
                self.pending_heartbeat_mids.remove(response_mid)
            except ValueError:
                pass
            logger.warning(f"【{self.cookie_id}】心跳响应正常 [ID:{response_mid}]")
            return True
        except Exception as e:
            logger.error(f"处理心跳响应出错: {self._safe_str(e)}")
        return False

    async def pause_cleanup_loop(self):
        """定期清理过期的暂停记录、锁和缓存"""
        try:
            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止清理循环")
                        break

                    # 清理过期的暂停记录
                    pause_manager.cleanup_expired_pauses()
                    await asyncio.sleep(0)  # 让出控制权，允许检查取消信号

                    # 清理过期的锁（每5分钟清理一次，保留24小时内的锁）
                    self.cleanup_expired_locks(max_age_hours=24)
                    await asyncio.sleep(0)  # 让出控制权，允许检查取消信号

                    # 清理过期的商品详情缓存
                    try:
                        cleaned_count = await self._cleanup_item_cache()
                        if cleaned_count > 0:
                            logger.info(f"【{self.cookie_id}】清理了 {cleaned_count} 个过期的商品详情缓存")
                    except asyncio.CancelledError:
                        raise
                    except Exception as cache_clean_e:
                        logger.warning(f"【{self.cookie_id}】清理商品详情缓存时出错: {cache_clean_e}")

                    # 清理过期的通知、发货和订单确认记录（防止内存泄漏）
                    self._cleanup_instance_caches()
                    await asyncio.sleep(0)  # 让出控制权，允许检查取消信号

                    # 清理QR登录过期会话（每5分钟检查一次）
                    try:
                        from utils.qr_login import qr_login_manager
                        qr_login_manager.cleanup_expired_sessions()
                        await asyncio.sleep(0)  # 让出控制权，允许检查取消信号
                    except asyncio.CancelledError:
                        raise
                    except Exception as qr_clean_e:
                        logger.warning(f"【{self.cookie_id}】清理QR登录会话时出错: {qr_clean_e}")
                    
                    # 清理Playwright浏览器临时文件和缓存（每5分钟检查一次）
                    try:
                        await self._cleanup_playwright_cache()
                    except asyncio.CancelledError:
                        raise
                    except Exception as pw_clean_e:
                        logger.warning(f"【{self.cookie_id}】清理Playwright缓存时出错: {pw_clean_e}")
                    
                    # 清理过期的日志文件（每5分钟检查一次，保留7天）
                    try:
                        cleaned_logs = await self._cleanup_old_logs(retention_days=7)
                        await asyncio.sleep(0)  # 让出控制权，允许检查取消信号
                    except asyncio.CancelledError:
                        raise
                    except Exception as log_clean_e:
                        logger.warning(f"【{self.cookie_id}】清理日志文件时出错: {log_clean_e}")
                    
                    # 清理超时仍处于processing的风控日志（每10分钟一次）
                    # 为避免所有实例同时执行，只让第一个实例执行
                    try:
                        if hasattr(self.__class__, '_last_risk_log_cleanup_time'):
                            last_risk_cleanup = self.__class__._last_risk_log_cleanup_time
                        else:
                            self.__class__._last_risk_log_cleanup_time = 0
                            last_risk_cleanup = 0

                        current_time = time.time()
                        if current_time - last_risk_cleanup > 600:
                            try:
                                cleaned_count = await asyncio.to_thread(
                                    db_manager.mark_stale_risk_control_logs_failed,
                                    timeout_minutes=15
                                )
                                if cleaned_count > 0:
                                    logger.warning(f"【{self.cookie_id}】风控日志超时兜底清理完成，自动关闭 {cleaned_count} 条processing记录")
                                self.__class__._last_risk_log_cleanup_time = current_time
                            except asyncio.CancelledError:
                                logger.warning(f"【{self.cookie_id}】风控日志超时兜底清理被取消")
                                raise
                    except asyncio.CancelledError:
                        raise
                    except Exception as risk_clean_e:
                        logger.error(f"【{self.cookie_id}】清理超时风控日志时出错: {risk_clean_e}")

                    # 补偿刷新超时仍停留在 processing/pending_payment 的订单。
                    # 只在平台详情确认待发货时才触发自动发货，避免未付款误发。
                    try:
                        if hasattr(self.__class__, '_last_unsettled_order_recovery_time'):
                            last_order_recovery = self.__class__._last_unsettled_order_recovery_time
                        else:
                            self.__class__._last_unsettled_order_recovery_time = 0
                            last_order_recovery = 0

                        current_time = time.time()
                        if current_time - last_order_recovery > 600:
                            recovered_count = await self._recover_stale_unsettled_orders(
                                older_than_minutes=10,
                                limit=5
                            )
                            if recovered_count > 0:
                                logger.warning(f"【{self.cookie_id}】超时未结算订单补偿处理完成，处理 {recovered_count} 个订单")
                            self.__class__._last_unsettled_order_recovery_time = current_time
                    except asyncio.CancelledError:
                        raise
                    except Exception as order_recovery_e:
                        logger.error(f"【{self.cookie_id}】补偿刷新超时订单时出错: {self._safe_str(order_recovery_e)}")

                    # 近期订单巡检：补偿 WebSocket 漏消息导致的“平台有订单、本地没入库”。
                    # 只扫描少量最新订单，且只有详情确认待发货时才推进自动发货。
                    try:
                        last_recent_order_recovery = getattr(self, '_last_recent_order_recovery_time', 0)
                        current_time = time.time()
                        if current_time - last_recent_order_recovery > 900:
                            recent_recovered_count = await self._recover_recent_missing_orders(max_orders=10)
                            if recent_recovered_count > 0:
                                logger.warning(
                                    f"【{self.cookie_id}】近期订单巡检补偿完成，处理 {recent_recovered_count} 个订单"
                                )
                            self._last_recent_order_recovery_time = current_time
                    except asyncio.CancelledError:
                        raise
                    except Exception as recent_order_recovery_e:
                        logger.error(f"【{self.cookie_id}】近期订单巡检补偿出错: {self._safe_str(recent_order_recovery_e)}")

                    # 清理数据库历史数据（每天一次，保留90天数据）
                    # 为避免所有实例同时执行，只让第一个实例执行
                    try:
                        if hasattr(self.__class__, '_last_db_cleanup_time'):
                            last_cleanup = self.__class__._last_db_cleanup_time
                        else:
                            self.__class__._last_db_cleanup_time = 0
                            last_cleanup = 0
                        
                        current_time = time.time()
                        # 每24小时清理一次
                        if current_time - last_cleanup > 86400:
                            logger.info(f"【{self.cookie_id}】开始执行数据库历史数据清理...")
                            # 数据库清理可能很耗时，使用线程池执行，避免阻塞事件循环
                            # 这样即使清理操作很慢，也能响应取消信号
                            try:
                                stats = await asyncio.to_thread(db_manager.cleanup_old_data, days=90)
                                if 'error' not in stats:
                                    logger.info(f"【{self.cookie_id}】数据库清理完成: {stats}")
                                    self.__class__._last_db_cleanup_time = current_time
                                else:
                                    logger.error(f"【{self.cookie_id}】数据库清理失败: {stats['error']}")
                            except asyncio.CancelledError:
                                logger.warning(f"【{self.cookie_id}】数据库清理被取消")
                                raise
                    except asyncio.CancelledError:
                        raise  # 重新抛出取消信号
                    except Exception as db_clean_e:
                        logger.error(f"【{self.cookie_id}】清理数据库历史数据时出错: {db_clean_e}")

                    # 每5分钟清理一次
                    await self._interruptible_sleep(300)
                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】清理循环收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】清理任务失败: {self._safe_str(e)}")
                    # 出错后也等待5分钟再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(300)
                    except asyncio.CancelledError:
                        logger.info(f"【{self.cookie_id}】清理循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】清理循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】清理循环已退出")


    async def cookie_refresh_loop(self):
        """Cookie刷新定时任务 - 每小时执行一次"""
        try:
            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止Cookie刷新循环")
                        break

                    # 检查Cookie刷新功能是否启用
                    if not self.cookie_refresh_enabled:
                        logger.warning(f"【{self.cookie_id}】Cookie刷新功能已禁用，跳过执行")
                        await self._interruptible_sleep(300)  # 5分钟后再检查
                        continue

                    if self.is_manual_refresh_active(self.cookie_id):
                        logger.warning(f"【{self.cookie_id}】手动刷新进行中，跳过自动Cookie刷新")
                        await self._interruptible_sleep(60)
                        continue

                    current_time = time.time()
                    if self._is_account_pause_status(getattr(self, 'last_token_refresh_status', None)):
                        logger.warning(f"【{self.cookie_id}】账号处于人工验证/风控暂停状态，跳过自动Cookie刷新")
                        await self._interruptible_sleep(300)
                        continue

                    if self._should_defer_auth_recovery_for_qr_grace(current_time):
                        await self._interruptible_sleep(max(60, self._get_qr_login_grace_remaining_seconds(current_time)))
                        continue

                    if self._should_skip_token_refresh_for_login_backoff(current_time):
                        logger.info(f"【{self.cookie_id}】当前处于密码登录退避期，跳过自动Cookie刷新")
                        await self._interruptible_sleep(60)
                        continue

                    effective_cookie_refresh_interval = self._get_effective_cookie_refresh_interval()
                    if current_time - self.last_cookie_refresh_time >= effective_cookie_refresh_interval:
                        # 检查是否在消息接收后的冷却时间内
                        time_since_last_message = current_time - self.last_message_received_time
                        if time_since_last_message < self.message_cookie_refresh_cooldown:
                            remaining_time = self.message_cookie_refresh_cooldown - time_since_last_message
                            remaining_minutes = int(remaining_time // 60)
                            remaining_seconds = int(remaining_time % 60)
                            logger.warning(f"【{self.cookie_id}】收到消息后冷却中，还需等待 {remaining_minutes}分{remaining_seconds}秒 才能执行Cookie刷新")
                        # 检查是否已有Cookie刷新任务在执行
                        elif self.cookie_refresh_lock.locked():
                            logger.warning(f"【{self.cookie_id}】Cookie刷新任务已在执行中，跳过本次触发")
                        else:
                            logger.info(f"【{self.cookie_id}】开始执行Cookie刷新任务...")
                            # 在独立的任务中执行Cookie刷新，避免阻塞主循环
                            asyncio.create_task(self._execute_cookie_refresh(current_time))

                    # 每分钟检查一次是否需要执行
                    await self._interruptible_sleep(60)
                except asyncio.CancelledError:
                    # 收到取消信号，立即退出循环
                    logger.info(f"【{self.cookie_id}】Cookie刷新循环收到取消信号，准备退出")
                    raise
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】Cookie刷新循环失败: {self._safe_str(e)}")
                    # 出错后也等待1分钟再重试，使用可中断的sleep
                    try:
                        await self._interruptible_sleep(60)
                    except asyncio.CancelledError:
                        logger.info(f"【{self.cookie_id}】Cookie刷新循环在重试等待时收到取消信号，准备退出")
                        raise
        except asyncio.CancelledError:
            # 确保CancelledError被正确传播
            logger.info(f"【{self.cookie_id}】Cookie刷新循环已取消，正在退出...")
            raise
        finally:
            # 确保任务能正常结束
            logger.info(f"【{self.cookie_id}】Cookie刷新循环已退出")

    async def _execute_cookie_refresh(self, current_time):
        """独立执行Cookie刷新任务，避免阻塞主循环"""

        # 使用Lock确保原子性，防止重复执行
        async with self.cookie_refresh_lock:
            try:
                clear_message_received_flag = False
                if self.is_manual_refresh_active(self.cookie_id):
                    logger.warning(f"【{self.cookie_id}】手动刷新进行中，取消当前自动Cookie刷新任务")
                    return

                logger.info(f"【{self.cookie_id}】开始Cookie刷新任务，暂时暂停心跳以避免连接冲突...")

                # 暂时暂停心跳任务，避免与浏览器操作冲突
                heartbeat_was_running = False
                if self.heartbeat_task and not self.heartbeat_task.done():
                    heartbeat_was_running = True
                    self.heartbeat_task.cancel()
                    logger.warning(f"【{self.cookie_id}】已暂停心跳任务")

                # 为整个Cookie刷新任务添加超时保护（3分钟，缩短时间减少影响）
                success = await asyncio.wait_for(
                    self._refresh_cookies_via_browser(),
                    timeout=180.0  # 3分钟超时，减少对WebSocket的影响
                )

                # 重新启动心跳任务
                if heartbeat_was_running and self.ws and not self.ws.closed:
                    logger.warning(f"【{self.cookie_id}】重新启动心跳任务")
                    self.heartbeat_task = asyncio.create_task(self.heartbeat_loop(self.ws))

                if success:
                    self.last_cookie_refresh_time = current_time
                    logger.info(f"【{self.cookie_id}】Cookie刷新任务完成，心跳已恢复")
                    
                    # 刷新成功后，验证Cookie有效性
                    logger.info(f"【{self.cookie_id}】开始验证刷新后的Cookie有效性...")
                    try:
                        validation_result = await self._verify_cookie_validity()
                        
                        if not validation_result['valid']:
                            logger.warning(f"【{self.cookie_id}】❌ Cookie验证失败: {validation_result['details']}")
                            if validation_result.get('relogin_recommended', True):
                                logger.warning(f"【{self.cookie_id}】检测到Cookie可能无法用于关键API，尝试通过密码登录重新获取...")
                                
                                # 触发密码登录刷新
                                password_refresh_success = await self._try_password_login_refresh("Cookie验证失败(关键API不可用)")
                                
                                if password_refresh_success:
                                    logger.info(f"【{self.cookie_id}】✅ 密码登录刷新成功，Cookie已更新")
                                    clear_message_received_flag = True
                                else:
                                    logger.warning(f"【{self.cookie_id}】⚠️ 密码登录刷新失败，Cookie可能仍然无效")
                                    # 发送通知
                                    await self.send_token_refresh_notification(
                                        f"Cookie验证失败且密码登录刷新也失败\n验证详情: {validation_result['details']}",
                                        "cookie_validation_failed"
                                    )
                            else:
                                logger.warning(f"【{self.cookie_id}】Cookie验证失败，但当前错误更像网络/环境问题，跳过密码登录刷新")
                        else:
                            if validation_result.get('inconclusive'):
                                logger.warning(f"【{self.cookie_id}】⚠️ Cookie验证结果不确定，保留当前消息冷却标志，等待后续保活再次确认: {validation_result['details']}")
                            else:
                                logger.info(f"【{self.cookie_id}】✅ Cookie验证通过: {validation_result['details']}")
                                clear_message_received_flag = True
                            
                    except Exception as verify_e:
                        logger.error(f"【{self.cookie_id}】Cookie验证过程异常: {self._safe_str(verify_e)}")
                        import traceback
                        logger.error(f"【{self.cookie_id}】详细堆栈:\n{traceback.format_exc()}")
                else:
                    logger.warning(f"【{self.cookie_id}】Cookie刷新任务失败")
                    # 即使失败也要更新时间，避免频繁重试
                    self.last_cookie_refresh_time = current_time

            except asyncio.TimeoutError:
                # 超时也要更新时间，避免频繁重试
                self.last_cookie_refresh_time = current_time
            except Exception as e:
                logger.error(f"【{self.cookie_id}】执行Cookie刷新任务异常: {self._safe_str(e)}")
                # 异常也要更新时间，避免频繁重试
                self.last_cookie_refresh_time = current_time
            finally:
                # 确保心跳任务恢复（如果WebSocket仍然连接）
                if (self.ws and not self.ws.closed and
                    (not self.heartbeat_task or self.heartbeat_task.done())):
                    logger.info(f"【{self.cookie_id}】Cookie刷新完成，心跳任务正常运行")
                    self.heartbeat_task = asyncio.create_task(self.heartbeat_loop(self.ws))

                if clear_message_received_flag:
                    # 仅在刷新链路确认恢复可用后，才清空消息接收标志。
                    self.last_message_received_time = 0
                    logger.warning(f"【{self.cookie_id}】Cookie刷新完成，已清空消息接收标志")
                else:
                    logger.warning(f"【{self.cookie_id}】Cookie刷新未确认恢复可用，保留消息接收标志")



    def enable_cookie_refresh(self, enabled: bool = True):
        """启用或禁用Cookie刷新功能"""
        self.cookie_refresh_enabled = enabled
        status = "启用" if enabled else "禁用"
        logger.info(f"【{self.cookie_id}】Cookie刷新功能已{status}")


    async def refresh_cookies_from_qr_login(self, qr_cookies_str: str, cookie_id: str = None, user_id: int = None):
        """使用扫码登录获取的cookie访问指定界面获取真实cookie并存入数据库

        Args:
            qr_cookies_str: 扫码登录获取的cookie字符串
            cookie_id: 可选的cookie ID，如果不提供则使用当前实例的cookie_id
            user_id: 可选的用户ID，如果不提供则使用当前实例的user_id

        Returns:
            bool: 成功返回True，失败返回False
        """
        playwright = None
        browser = None
        target_cookie_id = cookie_id or self.cookie_id
        target_user_id = user_id or self.user_id

        try:
            import asyncio
            from playwright.async_api import async_playwright
            from utils.xianyu_utils import trans_cookies

            logger.info(f"【{target_cookie_id}】开始使用扫码登录cookie获取真实cookie...")
            logger.info(f"【{target_cookie_id}】扫码cookie长度: {len(qr_cookies_str)}")

            # 解析扫码登录的cookie
            qr_cookies_dict = trans_cookies(qr_cookies_str)
            logger.info(f"【{target_cookie_id}】扫码cookie字段数: {len(qr_cookies_dict)}")

            # 使用统一的Playwright启动方法
            playwright = await _start_playwright_safe(target_cookie_id)
            if not playwright:
                return False

            # 启动浏览器（参照商品搜索的配置）
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-features=TranslateUI',
                '--disable-ipc-flooding-protection',
                '--disable-extensions',
                '--disable-default-apps',
                '--disable-sync',
                '--disable-translate',
                '--hide-scrollbars',
                '--mute-audio',
                '--no-default-browser-check',
                '--no-pings'
            ]

            # 在Docker环境中添加额外参数
            if os.getenv('DOCKER_ENV'):
                browser_args.extend([
                    # '--single-process',  # 注释掉，避免多用户并发时的进程冲突和资源泄漏
                    '--disable-background-networking',
                    '--disable-client-side-phishing-detection',
                    '--disable-hang-monitor',
                    '--disable-popup-blocking',
                    '--disable-prompt-on-repost',
                    '--disable-web-resources',
                    '--metrics-recording-only',
                    '--safebrowsing-disable-auto-update',
                    '--enable-automation',
                    '--password-store=basic',
                    '--use-mock-keychain'
                ])

            # 使用无头浏览器
            browser = await playwright.chromium.launch(
                headless=True,  # 改回无头模式
                args=browser_args
            )

            # 创建浏览器上下文
            context_options = {
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            }

            # 使用标准窗口大小
            context_options['viewport'] = {'width': 1920, 'height': 1080}

            context = await browser.new_context(**context_options)

            # 设置扫码登录获取的Cookie
            cookies = []
            for cookie_pair in qr_cookies_str.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })

            await context.add_cookies(cookies)
            logger.info(f"【{target_cookie_id}】已设置 {len(cookies)} 个扫码Cookie到浏览器")

            # 打印设置的扫码Cookie详情
            logger.info(f"【{target_cookie_id}】=== 设置到浏览器的扫码Cookie ===")
            for i, cookie in enumerate(cookies, 1):
                logger.info(f"【{target_cookie_id}】{i:2d}. {cookie['name']}: {cookie['value'][:50]}{'...' if len(cookie['value']) > 50 else ''}")

            # 创建页面
            page = await context.new_page()

            # 等待页面准备
            await asyncio.sleep(0.1)

            # 访问指定页面获取真实cookie
            target_url = "https://www.goofish.com/im"
            logger.info(f"【{target_cookie_id}】访问页面获取真实cookie: {target_url}")

            # 使用更灵活的页面访问策略
            try:
                # 首先尝试较短超时
                await page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
                logger.info(f"【{target_cookie_id}】页面访问成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{target_cookie_id}】页面访问超时，尝试降级策略...")
                    try:
                        # 降级策略：只等待基本加载
                        await page.goto(target_url, wait_until='load', timeout=20000)
                        logger.info(f"【{target_cookie_id}】页面访问成功（降级策略）")
                    except Exception as e2:
                        logger.warning(f"【{target_cookie_id}】降级策略也失败，尝试最基本访问...")
                        # 最后尝试：不等待任何加载完成
                        await page.goto(target_url, timeout=25000)
                        logger.info(f"【{target_cookie_id}】页面访问成功（最基本策略）")
                else:
                    raise e

            # 等待页面完全加载并获取真实cookie
            logger.info(f"【{target_cookie_id}】页面加载完成，等待获取真实cookie...")
            await asyncio.sleep(2)

            # 执行一次刷新以确保获取最新的cookie
            logger.info(f"【{target_cookie_id}】执行页面刷新获取最新cookie...")
            try:
                await page.reload(wait_until='domcontentloaded', timeout=12000)
                logger.info(f"【{target_cookie_id}】页面刷新成功")
            except Exception as e:
                error_text = str(e).lower()
                if 'net::err_aborted' in error_text or 'frame was detached' in error_text:
                    logger.warning(f"【{target_cookie_id}】页面刷新被中断，继续直接读取当前上下文Cookie: {self._safe_str(e)}")
                elif 'timeout' in error_text:
                    logger.warning(f"【{target_cookie_id}】页面刷新超时，使用降级策略...")
                    await page.reload(wait_until='load', timeout=15000)
                    logger.info(f"【{target_cookie_id}】页面刷新成功（降级策略）")
                else:
                    raise e
            await asyncio.sleep(1)

            # 获取更新后的真实Cookie
            logger.info(f"【{target_cookie_id}】获取真实Cookie...")
            updated_cookies = await context.cookies()

            # 构造新的Cookie字典
            real_cookies_dict = {}
            for cookie in updated_cookies:
                real_cookies_dict[cookie['name']] = cookie['value']

            # 现有账号不要直接整包覆盖旧Cookie，保留扫码前已经存在但本次页面未返回的字段
            from db_manager import db_manager
            existing_cookie = db_manager.get_cookie_details(target_cookie_id)
            existing_cookie_value = self._extract_cookie_value(existing_cookie)
            existing_cookies_dict = {}
            if existing_cookie_value:
                try:
                    existing_cookies_dict = trans_cookies(existing_cookie_value) or {}
                except Exception as merge_e:
                    logger.warning(f"【{target_cookie_id}】解析现有账号Cookie失败，按空基线继续: {self._safe_str(merge_e)}")

            # 扫码登录代表一个新的可信登录会话。x5 系票据与具体风控挑战/API 强绑定，
            # 如果扫码后的浏览器快照没有返回新的 x5，继续沿用旧 x5sec/x5secdata 反而容易让
            # 首轮 Token 预检命中 FAIL_SYS_USER_VALIDATE / RGV587_ERROR。
            stale_x5_fields = []
            for x5_key in ('x5sec', 'x5secdata', 'x5sectag'):
                if x5_key in existing_cookies_dict and x5_key not in real_cookies_dict:
                    existing_cookies_dict.pop(x5_key, None)
                    stale_x5_fields.append(x5_key)
            if stale_x5_fields:
                logger.warning(
                    f"【{target_cookie_id}】扫码登录快照未返回新的x5票据，已丢弃旧会话x5字段: "
                    f"{', '.join(stale_x5_fields)}"
                )

            merge_result = self.protected_merge_cookie_dicts(existing_cookies_dict, real_cookies_dict)
            real_cookies_dict = merge_result['merged_cookies_dict']
            if target_cookie_id == self.cookie_id:
                self._log_protected_merge_event("qr_login_protected_merge", merge_result)
            else:
                logger.info(
                    f"【{target_cookie_id}】qr_login_protected_merge "
                    f"incoming_count={merge_result.get('incoming_count', 0)} "
                    f"existing_count={merge_result.get('existing_count', 0)} "
                    f"merged_count={merge_result.get('merged_count', 0)} "
                    f"protected_preserved_fields={merge_result.get('preserved_protected_fields') or []} "
                    f"would_remove_fields={merge_result.get('would_remove_fields') or []} "
                    f"account_switched={merge_result.get('account_switched', False)}"
                )
            if merge_result['updated_fields']:
                logger.info(f"【{target_cookie_id}】扫码登录合并更新Cookie字段: {', '.join(merge_result['updated_fields'])}")
            if merge_result['preserved_fields']:
                logger.info(f"【{target_cookie_id}】扫码登录保留现有Cookie字段 ({len(merge_result['preserved_fields'])}个): {', '.join(merge_result['preserved_fields'])}")
            if merge_result['preserved_protected_fields']:
                logger.warning(f"【{target_cookie_id}】扫码登录保护性保留关键字段: {', '.join(merge_result['preserved_protected_fields'])}")
            if merge_result['account_switched']:
                logger.warning(f"【{target_cookie_id}】扫码登录检测到unb变化，按账号切换处理，不保留旧账号Cookie字段")

            missing_required_fields = merge_result['missing_required_fields']
            if missing_required_fields:
                logger.error(f"【{target_cookie_id}】扫码登录真实Cookie仍缺失核心字段，放弃保存: {', '.join(missing_required_fields)}")
                return False

            # 生成真实cookie字符串
            real_cookies_str = '; '.join([f"{k}={v}" for k, v in real_cookies_dict.items()])

            logger.info(f"【{target_cookie_id}】真实Cookie已获取，包含 {len(real_cookies_dict)} 个字段")
            
            # 打印扫码登录获取的真实Cookie字段详情
            logger.info(f"【{target_cookie_id}】========== 扫码登录真实Cookie字段详情 ==========")
            logger.info(f"【{target_cookie_id}】Cookie字段数: {len(real_cookies_dict)}")
            logger.info(f"【{target_cookie_id}】Cookie字段列表:")
            for i, (key, value) in enumerate(real_cookies_dict.items(), 1):
                if len(str(value)) > 50:
                    logger.info(f"【{target_cookie_id}】  {i:2d}. {key}: {str(value)[:30]}...{str(value)[-20:]} (长度: {len(str(value))})")
                else:
                    logger.info(f"【{target_cookie_id}】  {i:2d}. {key}: {value}")
            
            # 检查关键字段
            important_keys = ['unb', '_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 't', 'sgcookie', 'cna']
            logger.info(f"【{target_cookie_id}】关键字段检查:")
            for key in important_keys:
                if key in real_cookies_dict:
                    val = real_cookies_dict[key]
                    logger.info(f"【{target_cookie_id}】  ✅ {key}: {'存在' if val else '为空'} (长度: {len(str(val)) if val else 0})")
                else:
                    logger.info(f"【{target_cookie_id}】  ❌ {key}: 缺失")
            logger.info(f"【{target_cookie_id}】==========================================")

            # 打印完整的真实Cookie内容
            logger.info(f"【{target_cookie_id}】=== 真实Cookie摘要 ===")
            logger.info(f"【{target_cookie_id}】Cookie字符串长度: {len(real_cookies_str)}")
            logger.info(f"【{target_cookie_id}】Cookie摘要: {self._summarize_cookie_string(real_cookies_str)}")

            # 打印所有Cookie字段的详细信息
            logger.info(f"【{target_cookie_id}】=== Cookie字段详细信息 ===")
            for i, (name, value) in enumerate(real_cookies_dict.items(), 1):
                # 对于长值，显示前后部分
                if len(value) > 50:
                    display_value = f"{value[:20]}...{value[-20:]}"
                else:
                    display_value = value
                logger.info(f"【{target_cookie_id}】{i:2d}. {name}: {display_value}")

            # 打印原始扫码Cookie对比
            logger.info(f"【{target_cookie_id}】=== 扫码Cookie对比 ===")
            logger.info(f"【{target_cookie_id}】扫码Cookie长度: {len(qr_cookies_str)}")
            logger.info(f"【{target_cookie_id}】扫码Cookie字段数: {len(qr_cookies_dict)}")
            logger.info(f"【{target_cookie_id}】真实Cookie长度: {len(real_cookies_str)}")
            logger.info(f"【{target_cookie_id}】真实Cookie字段数: {len(real_cookies_dict)}")
            logger.info(f"【{target_cookie_id}】长度增加: {len(real_cookies_str) - len(qr_cookies_str)} 字符")
            logger.info(f"【{target_cookie_id}】字段增加: {len(real_cookies_dict) - len(qr_cookies_dict)} 个")

            # 检查Cookie变化
            changed_cookies = []
            new_cookies = []
            for name, new_value in real_cookies_dict.items():
                old_value = qr_cookies_dict.get(name)
                if old_value is None:
                    new_cookies.append(name)
                elif old_value != new_value:
                    changed_cookies.append(name)

            # 显示Cookie变化统计
            if changed_cookies:
                logger.info(f"【{target_cookie_id}】发生变化的Cookie字段 ({len(changed_cookies)}个): {', '.join(changed_cookies)}")
            if new_cookies:
                logger.info(f"【{target_cookie_id}】新增的Cookie字段 ({len(new_cookies)}个): {', '.join(new_cookies)}")
            if not changed_cookies and not new_cookies:
                logger.info(f"【{target_cookie_id}】Cookie无变化")

            # 打印重要Cookie字段的完整详情
            important_cookies = ['_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 't', 'sgcookie', 'unb', 'uc1', 'uc3', 'uc4']
            logger.info(f"【{target_cookie_id}】=== 重要Cookie字段完整详情 ===")
            for cookie_name in important_cookies:
                if cookie_name in real_cookies_dict:
                    cookie_value = real_cookies_dict[cookie_name]

                    # 标记是否发生了变化
                    change_mark = " [已变化]" if cookie_name in changed_cookies else " [新增]" if cookie_name in new_cookies else " [无变化]"

                    # 显示完整的cookie值
                    logger.info(f"【{target_cookie_id}】{cookie_name}{change_mark}:")
                    logger.info(f"【{target_cookie_id}】  值: {self._mask_secret_value(cookie_value, head=8, tail=6)}")
                    logger.info(f"【{target_cookie_id}】  长度: {len(cookie_value)}")

                    # 如果有对应的扫码cookie值，显示对比
                    if cookie_name in qr_cookies_dict:
                        old_value = qr_cookies_dict[cookie_name]
                        if old_value != cookie_value:
                            logger.info(f"【{target_cookie_id}】  原值: {self._mask_secret_value(old_value, head=8, tail=6)}")
                            logger.info(f"【{target_cookie_id}】  原长度: {len(old_value)}")
                    logger.info(f"【{target_cookie_id}】  ---")
                else:
                    logger.info(f"【{target_cookie_id}】{cookie_name}: [不存在]")

            # 保存真实Cookie到数据库
            # 检查是否为新账号
            existing_cookie = db_manager.get_cookie_details(target_cookie_id)
            if existing_cookie:
                # 现有账号，使用 update_cookie_account_info 避免覆盖其他字段（如 pause_duration, remark 等）
                success = db_manager.update_cookie_account_info(target_cookie_id, cookie_value=real_cookies_str)
            else:
                # 新账号，使用 save_cookie
                success = db_manager.save_cookie(target_cookie_id, real_cookies_str, target_user_id)

            if success:
                logger.info(f"【{target_cookie_id}】真实Cookie已成功保存到数据库")

                # 如果当前实例的cookie_id匹配，更新实例的cookie信息
                if target_cookie_id == self.cookie_id:
                    self._set_runtime_cookie_state(
                        cookies_str=real_cookies_str,
                        cookies_dict=real_cookies_dict,
                        source="qr_login_refresh",
                    )
                    logger.info(f"【{target_cookie_id}】已更新当前实例的Cookie信息")

                # 更新扫码登录Cookie刷新时间标志
                self.last_qr_cookie_refresh_time = time.time()
                logger.info(f"【{target_cookie_id}】已更新扫码登录Cookie刷新时间标志，_refresh_cookies_via_browser将等待{self.qr_cookie_refresh_cooldown//60}分钟后执行")

                return True
            else:
                logger.error(f"【{target_cookie_id}】保存真实Cookie到数据库失败")
                return False

        except Exception as e:
            logger.error(f"【{target_cookie_id}】使用扫码cookie获取真实cookie失败: {self._safe_str(e)}")
            return False
        finally:
            # 确保资源清理
            try:
                # 先关闭浏览器，再关闭Playwright（顺序很重要）
                if browser:
                    try:
                        await asyncio.wait_for(browser.close(), timeout=5.0)
                        logger.warning(f"【{target_cookie_id}】浏览器关闭完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{target_cookie_id}】浏览器关闭超时（5秒），资源可能未完全释放")
                        # 尝试取消浏览器相关的任务
                        try:
                            if hasattr(browser, '_connection'):
                                browser._connection = None
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"【{target_cookie_id}】关闭浏览器时出错: {self._safe_str(e)}")
                
                # Playwright关闭：使用更短的超时，超时后立即放弃
                if playwright:
                    try:
                        logger.warning(f"【{target_cookie_id}】正在关闭Playwright...")
                        await asyncio.wait_for(playwright.stop(), timeout=2.0)
                        logger.warning(f"【{target_cookie_id}】Playwright关闭完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{target_cookie_id}】Playwright关闭超时（2秒），进程可能仍在运行")
                        logger.warning(f"【{target_cookie_id}】提示：如果后续Playwright启动失败，可能需要手动清理残留进程")
                        # 尝试清理Playwright的内部状态
                        try:
                            # 取消可能正在运行的Playwright任务
                            if hasattr(playwright, '_transport'):
                                playwright._transport = None
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"【{target_cookie_id}】关闭Playwright时出错: {self._safe_str(e)}")
            except Exception as cleanup_e:
                logger.warning(f"【{target_cookie_id}】清理浏览器资源时出错: {self._safe_str(cleanup_e)}")

    async def _refresh_cookies_via_browser_page(self, current_cookies_str: str, restart_on_success: bool = True):
        """使用当前cookie访问指定页面获取真实cookie并更新
        
        这是令牌过期时的备用刷新方案，类似于refresh_cookies_from_qr_login，
        但使用当前的cookie而不是扫码登录的cookie。

        Args:
            current_cookies_str: 当前的cookie字符串
            restart_on_success: 成功后是否立即重启任务。扫码登录后的首轮缓冲只需要稳定 Cookie，不应直接重启。

        Returns:
            bool: 成功返回True，失败返回False
        """
        playwright = None
        browser = None

        try:
            import asyncio
            from playwright.async_api import async_playwright
            from utils.xianyu_utils import trans_cookies

            logger.info(f"【{self.cookie_id}】开始使用当前cookie访问指定页面获取真实cookie...")
            logger.info(f"【{self.cookie_id}】当前cookie长度: {len(current_cookies_str)}")

            # 解析当前的cookie
            current_cookies_dict = trans_cookies(current_cookies_str)
            logger.info(f"【{self.cookie_id}】当前cookie字段数: {len(current_cookies_dict)}")

            # 使用统一的Playwright启动方法
            playwright = await _start_playwright_safe(self.cookie_id)
            if not playwright:
                return False

            # 启动浏览器（参照商品搜索的配置）
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-features=TranslateUI',
                '--disable-ipc-flooding-protection',
                '--disable-extensions',
                '--disable-default-apps',
                '--disable-sync',
                '--disable-translate',
                '--hide-scrollbars',
                '--mute-audio',
                '--no-default-browser-check',
                '--no-pings'
            ]

            # 在Docker环境中添加额外参数
            if os.getenv('DOCKER_ENV'):
                browser_args.extend([
                    '--disable-background-networking',
                    '--disable-client-side-phishing-detection',
                    '--disable-hang-monitor',
                    '--disable-popup-blocking',
                    '--disable-prompt-on-repost',
                    '--disable-web-resources',
                    '--metrics-recording-only',
                    '--safebrowsing-disable-auto-update',
                    '--enable-automation',
                    '--password-store=basic',
                    '--use-mock-keychain'
                ])

            # 读取账号配置以决定浏览器模式（默认无头）
            account_info = db_manager.get_cookie_details(self.cookie_id) or {}
            show_browser = bool(account_info.get('show_browser', False))
            browser = await playwright.chromium.launch(
                headless=not show_browser,
                args=browser_args
            )

            # 创建浏览器上下文
            context_options = {
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            }

            # 使用标准窗口大小
            context_options['viewport'] = {'width': 1920, 'height': 1080}

            context = await browser.new_context(**context_options)

            # 设置当前的Cookie
            cookies = []
            for cookie_pair in current_cookies_str.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })

            await context.add_cookies(cookies)
            logger.info(f"【{self.cookie_id}】已设置 {len(cookies)} 个当前Cookie到浏览器")

            # 创建页面
            page = await context.new_page()

            # 等待页面准备
            await asyncio.sleep(0.1)

            # 访问指定页面获取真实cookie
            target_url = "https://www.goofish.com/im"
            logger.info(f"【{self.cookie_id}】访问页面获取真实cookie: {target_url}")

            # 使用更灵活的页面访问策略
            try:
                # 首先尝试较短超时
                await page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
                logger.info(f"【{self.cookie_id}】页面访问成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】页面访问超时，尝试降级策略...")
                    try:
                        # 降级策略：只等待基本加载
                        await page.goto(target_url, wait_until='load', timeout=20000)
                        logger.info(f"【{self.cookie_id}】页面访问成功（降级策略）")
                    except Exception as e2:
                        logger.warning(f"【{self.cookie_id}】降级策略也失败，尝试最基本访问...")
                        # 最后尝试：不等待任何加载完成
                        await page.goto(target_url, timeout=25000)
                        logger.info(f"【{self.cookie_id}】页面访问成功（最基本策略）")
                else:
                    raise e

            # 等待页面完全加载并获取真实cookie
            logger.info(f"【{self.cookie_id}】页面加载完成，等待获取真实cookie...")
            await asyncio.sleep(2)

            # 执行一次刷新以确保获取最新的cookie
            logger.info(f"【{self.cookie_id}】执行页面刷新获取最新cookie...")
            try:
                await page.reload(wait_until='domcontentloaded', timeout=12000)
                logger.info(f"【{self.cookie_id}】页面刷新成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】页面刷新超时，使用降级策略...")
                    await page.reload(wait_until='load', timeout=15000)
                    logger.info(f"【{self.cookie_id}】页面刷新成功（降级策略）")
                else:
                    raise e
            await asyncio.sleep(1)

            # 获取更新后的真实Cookie
            logger.info(f"【{self.cookie_id}】获取真实Cookie...")
            updated_cookies = await context.cookies()

            # 构造新的Cookie字典
            real_cookies_dict = {}
            for cookie in updated_cookies:
                real_cookies_dict[cookie['name']] = cookie['value']

            merge_result = self.protected_merge_cookie_dicts(current_cookies_dict, real_cookies_dict)
            real_cookies_dict = merge_result['merged_cookies_dict']
            self._log_protected_merge_event("browser_stabilization_protected_merge", merge_result)

            # 生成真实cookie字符串
            real_cookies_str = '; '.join([f"{k}={v}" for k, v in real_cookies_dict.items()])

            logger.info(f"【{self.cookie_id}】真实Cookie已获取，包含 {len(real_cookies_dict)} 个字段")
            logger.info(f"【{self.cookie_id}】真实Cookie摘要: {self._summarize_cookie_string(real_cookies_str)}")

            self._log_cookie_merge_summary(
                real_cookies_dict,
                merge_result['updated_fields'],
                merge_result['changed_fields'],
                merge_result['new_fields'],
                context="浏览器稳定化Cookie",
                preserved_fields=merge_result['preserved_fields'],
                preserved_protected_fields=merge_result['preserved_protected_fields'],
                would_remove_fields=merge_result['would_remove_fields'],
                removed_fields=merge_result['removed_fields'],
                missing_protected_fields=merge_result['missing_protected_fields'],
                missing_required_fields=merge_result['missing_required_fields'],
                incoming_missing_protected_fields=merge_result['incoming_missing_protected_fields'],
                account_switched=merge_result['account_switched'],
            )

            if merge_result['missing_required_fields']:
                logger.error(f"【{self.cookie_id}】浏览器稳定化后的Cookie仍缺失核心字段，放弃写回数据库: {', '.join(merge_result['missing_required_fields'])}")
                return False

            # 检查Cookie是否有有效更新
            changed_cookies = []
            new_cookies = []
            for name, new_value in real_cookies_dict.items():
                old_value = current_cookies_dict.get(name)
                if old_value is None:
                    new_cookies.append(name)
                elif old_value != new_value:
                    changed_cookies.append(name)

            if not changed_cookies and not new_cookies:
                if restart_on_success:
                    logger.warning(f"【{self.cookie_id}】Cookie无变化，可能当前cookie已失效")
                    return False
                logger.info(f"【{self.cookie_id}】Cookie字段无变化，但浏览器稳定化访问已完成")

            logger.info(f"【{self.cookie_id}】发生变化的Cookie字段 ({len(changed_cookies)}个): {', '.join(changed_cookies[:10])}")
            if new_cookies:
                logger.info(f"【{self.cookie_id}】新增的Cookie字段 ({len(new_cookies)}个): {', '.join(new_cookies[:10])}")

            if restart_on_success:
                # 更新Cookie并重启任务
                logger.info(f"【{self.cookie_id}】开始更新Cookie并重启任务...")
                update_success = await self._update_cookies_and_restart(real_cookies_str)

                if update_success:
                    logger.info(f"【{self.cookie_id}】通过访问指定页面成功更新Cookie并重启任务")
                    return True
                else:
                    logger.error(f"【{self.cookie_id}】更新Cookie或重启任务失败")
                    return False

            old_cookies_str = self.cookies_str
            old_cookies_dict = self.cookies.copy()
            try:
                self._set_runtime_cookie_state(
                    cookies_str=real_cookies_str,
                    cookies_dict=real_cookies_dict,
                    source="stabilize_cookie_snapshot",
                )
                await self.update_config_cookies()
                logger.info(f"【{self.cookie_id}】通过访问指定页面成功稳定当前Cookie（不重启任务）")
                return True
            except Exception as update_e:
                self._set_runtime_cookie_state(
                    cookies_str=old_cookies_str,
                    cookies_dict=old_cookies_dict,
                    source="stabilize_cookie_snapshot_rollback",
                )
                logger.error(f"【{self.cookie_id}】稳定Cookie时更新数据库失败: {self._safe_str(update_e)}")
                return False

        except Exception as e:
            logger.error(f"【{self.cookie_id}】使用当前cookie访问指定页面获取真实cookie失败: {self._safe_str(e)}")
            return False
        finally:
            # 确保资源清理
            try:
                # 先关闭浏览器，再关闭Playwright（顺序很重要）
                if browser:
                    try:
                        await asyncio.wait_for(browser.close(), timeout=5.0)
                        logger.warning(f"【{self.cookie_id}】浏览器关闭完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{self.cookie_id}】浏览器关闭超时（5秒），资源可能未完全释放")
                    except Exception as e:
                        logger.warning(f"【{self.cookie_id}】关闭浏览器时出错: {self._safe_str(e)}")
                
                # Playwright关闭：使用更短的超时，超时后立即放弃
                if playwright:
                    try:
                        logger.warning(f"【{self.cookie_id}】正在关闭Playwright...")
                        await asyncio.wait_for(playwright.stop(), timeout=2.0)
                        logger.warning(f"【{self.cookie_id}】Playwright关闭完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{self.cookie_id}】Playwright关闭超时（2秒），进程可能仍在运行")
                    except Exception as e:
                        logger.warning(f"【{self.cookie_id}】关闭Playwright时出错: {self._safe_str(e)}")
            except Exception as cleanup_e:
                logger.warning(f"【{self.cookie_id}】清理浏览器资源时出错: {self._safe_str(cleanup_e)}")

    def reset_qr_cookie_refresh_flag(self):
        """重置扫码登录Cookie刷新标志，允许立即执行_refresh_cookies_via_browser"""
        self.last_qr_cookie_refresh_time = 0
        logger.info(f"【{self.cookie_id}】已重置扫码登录Cookie刷新标志")

    def get_qr_cookie_refresh_remaining_time(self) -> int:
        """获取扫码登录Cookie刷新剩余冷却时间（秒）"""
        current_time = time.time()
        time_since_qr_refresh = current_time - self.last_qr_cookie_refresh_time
        remaining_time = max(0, self.qr_cookie_refresh_cooldown - time_since_qr_refresh)
        return int(remaining_time)

    async def _refresh_cookies_via_browser(self, triggered_by_refresh_token: bool = False):
        """通过浏览器访问指定页面刷新Cookie

        Args:
            triggered_by_refresh_token: 是否由refresh_token方法触发，如果是True则设置browser_cookie_refreshed标志
        """


        playwright = None
        browser = None
        try:
            import asyncio
            from playwright.async_api import async_playwright

            # 检查是否需要等待扫码登录Cookie刷新的冷却时间
            current_time = time.time()
            time_since_qr_refresh = current_time - self.last_qr_cookie_refresh_time

            if time_since_qr_refresh < self.qr_cookie_refresh_cooldown:
                remaining_time = self.qr_cookie_refresh_cooldown - time_since_qr_refresh
                remaining_minutes = int(remaining_time // 60)
                remaining_seconds = int(remaining_time % 60)

                logger.info(f"【{self.cookie_id}】扫码登录Cookie刷新冷却中，还需等待 {remaining_minutes}分{remaining_seconds}秒")
                logger.info(f"【{self.cookie_id}】跳过本次浏览器Cookie刷新")
                return False

            logger.info(f"【{self.cookie_id}】开始通过浏览器刷新Cookie...")
            logger.info(f"【{self.cookie_id}】刷新前Cookie长度: {len(self.cookies_str)}")
            logger.info(f"【{self.cookie_id}】刷新前Cookie字段数: {len(self.cookies)}")

            # 使用统一的Playwright启动方法
            playwright = await _start_playwright_safe(self.cookie_id)
            if not playwright:
                return False

            # 启动浏览器（参照商品搜索的配置）
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-features=TranslateUI',
                '--disable-ipc-flooding-protection',
                '--disable-extensions',
                '--disable-default-apps',
                '--disable-sync',
                '--disable-translate',
                '--hide-scrollbars',
                '--mute-audio',
                '--no-default-browser-check',
                '--no-pings'
            ]

            # 在Docker环境中添加额外参数
            if os.getenv('DOCKER_ENV'):
                browser_args.extend([
                    # '--single-process',  # 注释掉，避免多用户并发时的进程冲突和资源泄漏
                    '--disable-background-networking',
                    '--disable-client-side-phishing-detection',
                    '--disable-hang-monitor',
                    '--disable-popup-blocking',
                    '--disable-prompt-on-repost',
                    '--disable-web-resources',
                    '--metrics-recording-only',
                    '--safebrowsing-disable-auto-update',
                    '--enable-automation',
                    '--password-store=basic',
                    '--use-mock-keychain'
                ])

            # Cookie刷新模式：读取账号配置以决定浏览器模式（默认无头）
            account_info = db_manager.get_cookie_details(self.cookie_id) or {}
            show_browser = bool(account_info.get('show_browser', False))
            browser = await playwright.chromium.launch(
                headless=not show_browser,
                args=browser_args
            )

            # 创建浏览器上下文
            context_options = {
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            }

            # 使用标准窗口大小
            context_options['viewport'] = {'width': 1920, 'height': 1080}

            context = await browser.new_context(**context_options)

            # 设置当前Cookie
            cookies = []
            for cookie_pair in self.cookies_str.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })

            await context.add_cookies(cookies)
            logger.info(f"【{self.cookie_id}】已设置 {len(cookies)} 个Cookie到浏览器")

            # 创建页面
            page = await context.new_page()

            # 等待页面准备
            await asyncio.sleep(0.1)

            # 访问指定页面
            target_url = "https://www.goofish.com/im"
            logger.info(f"【{self.cookie_id}】访问页面: {target_url}")

            # 使用更灵活的页面访问策略
            try:
                # 首先尝试较短超时
                await page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
                logger.info(f"【{self.cookie_id}】页面访问成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】页面访问超时，尝试降级策略...")
                    try:
                        # 降级策略：只等待基本加载
                        await page.goto(target_url, wait_until='load', timeout=20000)
                        logger.info(f"【{self.cookie_id}】页面访问成功（降级策略）")
                    except Exception as e2:
                        logger.warning(f"【{self.cookie_id}】降级策略也失败，尝试最基本访问...")
                        # 最后尝试：不等待任何加载完成
                        await page.goto(target_url, timeout=25000)
                        logger.info(f"【{self.cookie_id}】页面访问成功（最基本策略）")
                else:
                    raise e

            # Cookie刷新模式：执行两次刷新
            logger.info(f"【{self.cookie_id}】页面加载完成，开始刷新...")
            await asyncio.sleep(1)

            # 第一次刷新 - 带重试机制
            logger.info(f"【{self.cookie_id}】执行第一次刷新...")
            try:
                await page.reload(wait_until='domcontentloaded', timeout=12000)
                logger.info(f"【{self.cookie_id}】第一次刷新成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】第一次刷新超时，使用降级策略...")
                    await page.reload(wait_until='load', timeout=15000)
                    logger.info(f"【{self.cookie_id}】第一次刷新成功（降级策略）")
                else:
                    raise e
            await asyncio.sleep(1)

            # 第二次刷新 - 带重试机制
            logger.info(f"【{self.cookie_id}】执行第二次刷新...")
            try:
                await page.reload(wait_until='domcontentloaded', timeout=12000)
                logger.info(f"【{self.cookie_id}】第二次刷新成功")
            except Exception as e:
                if 'timeout' in str(e).lower():
                    logger.warning(f"【{self.cookie_id}】第二次刷新超时，使用降级策略...")
                    await page.reload(wait_until='load', timeout=15000)
                    logger.info(f"【{self.cookie_id}】第二次刷新成功（降级策略）")
                else:
                    raise e
            await asyncio.sleep(1)

            # Cookie刷新模式：正常更新Cookie
            logger.info(f"【{self.cookie_id}】获取更新后的Cookie...")
            updated_cookies = await context.cookies()
            
            # 获取并打印当前页面标题
            page_title = await page.title()
            logger.info(f"【{self.cookie_id}】当前页面标题: {page_title}")

            # 构造新的Cookie字典
            new_cookies_dict = {}
            for cookie in updated_cookies:
                new_cookies_dict[cookie['name']] = cookie['value']

            # 检查Cookie变化
            changed_cookies = []
            new_cookies = []
            for name, new_value in new_cookies_dict.items():
                old_value = self.cookies.get(name)
                if old_value is None:
                    new_cookies.append(name)
                elif old_value != new_value:
                    changed_cookies.append(name)

            merge_result = self.protected_merge_cookie_dicts(self.cookies, new_cookies_dict)
            merged_cookies_dict = merge_result['merged_cookies_dict']
            self._log_protected_merge_event("browser_refresh_protected_merge", merge_result)

            self._log_cookie_merge_summary(
                merged_cookies_dict,
                merge_result['updated_fields'],
                merge_result['changed_fields'],
                merge_result['new_fields'],
                context="浏览器刷新Cookie",
                preserved_fields=merge_result['preserved_fields'],
                preserved_protected_fields=merge_result['preserved_protected_fields'],
                would_remove_fields=merge_result['would_remove_fields'],
                removed_fields=merge_result['removed_fields'],
                missing_protected_fields=merge_result['missing_protected_fields'],
                missing_required_fields=merge_result['missing_required_fields'],
                incoming_missing_protected_fields=merge_result['incoming_missing_protected_fields'],
                account_switched=merge_result['account_switched'],
            )

            if merge_result['missing_required_fields']:
                logger.error(
                    f"【{self.cookie_id}】浏览器刷新后的Cookie仍缺失核心字段，放弃覆盖当前Cookie: {', '.join(merge_result['missing_required_fields'])}"
                )
                return False

            # 更新self.cookies和cookies_str
            self._set_runtime_cookie_state(
                cookies_dict=merged_cookies_dict,
                source="browser_cookie_refresh",
            )

            logger.info(f"【{self.cookie_id}】Cookie已更新，包含 {len(new_cookies_dict)} 个字段")

            # 显示Cookie变化统计
            if changed_cookies:
                logger.info(f"【{self.cookie_id}】发生变化的Cookie字段 ({len(changed_cookies)}个): {', '.join(changed_cookies)}")
            if new_cookies:
                logger.info(f"【{self.cookie_id}】新增的Cookie字段 ({len(new_cookies)}个): {', '.join(new_cookies)}")
            if not changed_cookies and not new_cookies:
                logger.info(f"【{self.cookie_id}】Cookie无变化")

            # 打印完整的更新后Cookie（可选择性启用）
            logger.info(f"【{self.cookie_id}】更新后的Cookie摘要: {self._summarize_cookie_string(self.cookies_str)}")

            # 打印主要的Cookie字段详情
            important_cookies = ['_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 't', 'sgcookie', 'unb', 'uc1', 'uc3', 'uc4']
            logger.info(f"【{self.cookie_id}】重要Cookie字段详情:")
            for cookie_name in important_cookies:
                if cookie_name in new_cookies_dict:
                    cookie_value = new_cookies_dict[cookie_name]
                    # 对于敏感信息，只显示前后几位
                    if len(cookie_value) > 20:
                        display_value = f"{cookie_value[:8]}...{cookie_value[-8:]}"
                    else:
                        display_value = cookie_value

                    # 标记是否发生了变化
                    change_mark = " [已变化]" if cookie_name in changed_cookies else " [新增]" if cookie_name in new_cookies else ""
                    logger.info(f"【{self.cookie_id}】  {cookie_name}: {display_value}{change_mark}")

            # 更新数据库中的Cookie
            await self.update_config_cookies()

            # 只有当由refresh_token触发时才设置浏览器Cookie刷新成功标志
            if triggered_by_refresh_token:
                self.browser_cookie_refreshed = True
                logger.info(f"【{self.cookie_id}】由refresh_token触发，浏览器Cookie刷新成功标志已设置为True")

                # 兜底：直接在此处触发实例重启，避免外层协程在返回后被取消导致未重启
                try:
                    # 标记"刷新流程内已触发重启"，供外层去重
                    self.restarted_in_browser_refresh = True

                    logger.info(f"【{self.cookie_id}】Cookie刷新成功，准备重启实例...(via _refresh_cookies_via_browser)")
                    await self._restart_instance()
                    
                    # ⚠️ _restart_instance() 已触发重启，当前任务即将被取消
                    # 不要等待或执行耗时操作
                    logger.info(f"【{self.cookie_id}】重启请求已触发(via _refresh_cookies_via_browser)")
                    
                    # 标记重启标志（无需主动关闭WS，重启由管理器处理）
                    self.connection_restart_flag = True
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】兜底重启失败: {self._safe_str(e)}")
            else:
                logger.info(f"【{self.cookie_id}】由定时任务触发，不设置浏览器Cookie刷新成功标志")

            logger.info(f"【{self.cookie_id}】Cookie刷新完成")
            return True

        except Exception as e:
            logger.error(f"【{self.cookie_id}】通过浏览器刷新Cookie失败: {self._safe_str(e)}")
            return False
        finally:
            # 异步关闭浏览器：创建清理任务并等待完成，确保资源正确释放
            close_task = None
            try:
                if browser or playwright:
                    # 创建关闭任务
                    close_task = asyncio.create_task(
                        self._async_close_browser(browser, playwright)
                    )
                    logger.info(f"【{self.cookie_id}】浏览器异步关闭任务已启动")
                    
                    # 等待关闭任务完成，但设置超时避免阻塞太久
                    try:
                        await asyncio.wait_for(close_task, timeout=15.0)
                        logger.info(f"【{self.cookie_id}】浏览器关闭任务已完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"【{self.cookie_id}】浏览器关闭任务超时（15秒），强制继续")
                        # 取消任务，避免资源泄漏
                        if not close_task.done():
                            close_task.cancel()
                            try:
                                await close_task
                            except (asyncio.CancelledError, Exception):
                                pass
                    except Exception as wait_e:
                        logger.warning(f"【{self.cookie_id}】等待浏览器关闭任务时出错: {self._safe_str(wait_e)}")
                        # 确保任务被取消
                        if close_task and not close_task.done():
                            close_task.cancel()
                            try:
                                await close_task
                            except (asyncio.CancelledError, Exception):
                                pass
            except Exception as cleanup_e:
                logger.warning(f"【{self.cookie_id}】创建浏览器关闭任务时出错: {self._safe_str(cleanup_e)}")
                # 如果创建任务失败，尝试直接关闭
                if browser or playwright:
                    try:
                        await self._force_close_resources(browser, playwright)
                    except Exception:
                        pass

    async def _async_close_browser(self, browser, playwright):
        """异步关闭：正常关闭，超时后强制关闭"""
        try:
            logger.info(f"【{self.cookie_id}】开始异步关闭浏览器...")  # 改为info级别
            
            # 正常关闭，设置超时
            await asyncio.wait_for(
                self._normal_close_resources(browser, playwright),
                timeout=10.0
            )
            logger.info(f"【{self.cookie_id}】浏览器正常关闭完成")  # 改为info级别
            
        except asyncio.TimeoutError:
            logger.warning(f"【{self.cookie_id}】正常关闭超时，开始强制关闭...")
            await self._force_close_resources(browser, playwright)
            
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】异步关闭时出错，强制关闭: {self._safe_str(e)}")
            await self._force_close_resources(browser, playwright)

    async def _normal_close_resources(self, browser, playwright):
        """正常关闭资源：浏览器+Playwright短超时关闭"""
        try:
            # 先关闭浏览器，再关闭Playwright
            if browser:
                try:
                    # 关闭浏览器，设置超时
                    await asyncio.wait_for(browser.close(), timeout=5.0)
                    logger.info(f"【{self.cookie_id}】浏览器关闭完成")
                except asyncio.TimeoutError:
                    logger.warning(f"【{self.cookie_id}】浏览器关闭超时，尝试强制关闭")
                    try:
                        # 尝试强制关闭
                        if hasattr(browser, '_connection'):
                            browser._connection.dispose()
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"【{self.cookie_id}】关闭浏览器时出错: {e}")
            
            # 关闭Playwright：使用短超时，如果超时就放弃
            if playwright:
                try:
                    logger.info(f"【{self.cookie_id}】正在关闭Playwright...")
                    # 增加超时时间，确保Playwright有足够时间清理资源
                    await asyncio.wait_for(playwright.stop(), timeout=5.0)
                    logger.info(f"【{self.cookie_id}】Playwright关闭完成")
                except asyncio.TimeoutError:
                    logger.warning(f"【{self.cookie_id}】Playwright关闭超时，将自动清理")
                    # 尝试强制清理Playwright的内部连接
                    try:
                        if hasattr(playwright, '_connection'):
                            playwright._connection.dispose()
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"【{self.cookie_id}】关闭Playwright时出错: {e}")
                
        except Exception as e:
            logger.error(f"【{self.cookie_id}】正常关闭时出现异常: {e}")
            raise

    
    async def _force_close_resources(self, browser, playwright):
        """强制关闭资源：强制关闭浏览器+Playwright超时等待"""
        try:
            logger.warning(f"【{self.cookie_id}】开始强制关闭资源...")
            
            # 强制关闭浏览器+Playwright，设置短超时
            force_tasks = []
            if browser:
                force_tasks.append(asyncio.wait_for(browser.close(), timeout=3.0))
            if playwright:
                force_tasks.append(asyncio.wait_for(playwright.stop(), timeout=3.0))
            
            if force_tasks:
                # 使用gather执行，所有失败都会被忽略
                results = await asyncio.gather(*force_tasks, return_exceptions=True)
                
                # 检查是否有超时或异常，尝试强制清理
                for i, result in enumerate(results):
                    if isinstance(result, (asyncio.TimeoutError, Exception)):
                        resource_name = "浏览器" if i == 0 and browser else "Playwright"
                        logger.warning(f"【{self.cookie_id}】{resource_name}强制关闭失败，尝试直接清理连接")
                        try:
                            if i == 0 and browser and hasattr(browser, '_connection'):
                                browser._connection.dispose()
                            elif playwright and hasattr(playwright, '_connection'):
                                playwright._connection.dispose()
                        except Exception:
                            pass
                
                logger.info(f"【{self.cookie_id}】强制关闭完成")
            else:
                logger.info(f"【{self.cookie_id}】没有需要强制关闭的资源")
            
        except Exception as e:
            logger.warning(f"【{self.cookie_id}】强制关闭时出现异常（已忽略）: {e}")

    async def send_msg_once(self, toid, item_id, text):
        """单次发送消息（创建新的WebSocket连接）"""
        headers = self._build_websocket_headers()

        logger.info(f"【{self.cookie_id}】开始单次发送消息: toid={toid}, item_id={item_id}")

        # 兼容不同版本的websockets库
        try:
            async with websockets.connect(
                self.base_url,
                extra_headers=headers,
                close_timeout=5  # 添加关闭超时
            ) as websocket:
                result = await self._handle_websocket_connection(websocket, toid, item_id, text)
                if result:
                    logger.info(f"【{self.cookie_id}】单次发送消息成功")
                else:
                    raise Exception("消息发送失败")
        except TypeError as e:
            # 安全地检查异常信息
            error_msg = self._safe_str(e)

            if "extra_headers" in error_msg:
                logger.warning("websockets库不支持extra_headers参数，使用兼容模式")
                # 使用兼容模式
                async with websockets.connect(
                    self.base_url,
                    additional_headers=headers,
                    close_timeout=5
                ) as websocket:
                    result = await self._handle_websocket_connection(websocket, toid, item_id, text)
                    if result:
                        logger.info(f"【{self.cookie_id}】单次发送消息成功(兼容模式)")
                    else:
                        raise Exception("消息发送失败")
            else:
                raise
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"【{self.cookie_id}】WebSocket连接关闭: {self._safe_str(e)}")
            # 连接关闭但消息可能已发送，不抛出异常
        except Exception as e:
            logger.error(f"【{self.cookie_id}】单次发送消息异常: {self._safe_str(e)}")
            raise

    async def send_delivery_steps_once(self, toid, item_id, delivery_steps):
        """单次发送发货步骤（创建新的WebSocket连接）。"""
        headers = self._build_websocket_headers()

        logger.info(f"【{self.cookie_id}】开始单次发送发货步骤: toid={toid}, item_id={item_id}, steps={len(delivery_steps or [])}")

        try:
            async with websockets.connect(
                self.base_url,
                extra_headers=headers,
                close_timeout=5
            ) as websocket:
                result = await self._handle_websocket_connection_steps(websocket, toid, item_id, delivery_steps)
                if result:
                    logger.info(f"【{self.cookie_id}】单次发送发货步骤成功")
                else:
                    raise Exception("发货步骤发送失败")
        except TypeError as e:
            error_msg = self._safe_str(e)

            if "extra_headers" in error_msg:
                logger.warning("websockets库不支持extra_headers参数，使用兼容模式发送发货步骤")
                async with websockets.connect(
                    self.base_url,
                    additional_headers=headers,
                    close_timeout=5
                ) as websocket:
                    result = await self._handle_websocket_connection_steps(websocket, toid, item_id, delivery_steps)
                    if result:
                        logger.info(f"【{self.cookie_id}】单次发送发货步骤成功(兼容模式)")
                    else:
                        raise Exception("发货步骤发送失败")
            else:
                raise
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"【{self.cookie_id}】WebSocket连接关闭: {self._safe_str(e)}")
        except Exception as e:
            logger.error(f"【{self.cookie_id}】单次发送发货步骤异常: {self._safe_str(e)}")
            raise

    async def _handle_websocket_connection_steps(self, websocket, toid, item_id, delivery_steps):
        """处理WebSocket连接的发货步骤发送逻辑。"""
        try:
            await self.init(websocket)
            await self.create_chat(websocket, toid, item_id)

            timeout = 30
            start_time = time.time()

            async for message in websocket:
                try:
                    if time.time() - start_time > timeout:
                        logger.warning(f"【{self.cookie_id}】WebSocket消息等待超时")
                        break

                    logger.info(f"【{self.cookie_id}】message: {message}")
                    message = json.loads(message)
                    cid = message["body"]["singleChatConversation"]["cid"]
                    cid = cid.split('@')[0]
                    await self._send_delivery_steps(
                        websocket,
                        cid,
                        toid,
                        delivery_steps,
                        log_prefix="单次手动发货"
                    )
                    logger.info(f'【{self.cookie_id}】send delivery steps success')
                    return True
                except KeyError:
                    continue
                except Exception as e:
                    logger.warning(f"【{self.cookie_id}】处理消息异常: {self._safe_str(e)}")
                    continue

            logger.warning(f"【{self.cookie_id}】WebSocket连接关闭，未能发送发货步骤")
            return False
        except Exception as e:
            logger.error(f"【{self.cookie_id}】WebSocket发货步骤处理异常: {self._safe_str(e)}")
            return False

    async def _create_websocket_connection(self, headers):
        """创建WebSocket连接，兼容不同版本的websockets库，支持代理配置"""
        import websockets

        # 获取websockets版本用于调试
        websockets_version = getattr(websockets, '__version__', '未知')
        logger.info(f"【{self.cookie_id}】websockets库版本: {websockets_version}")

        # 检查是否需要使用代理
        proxy_url = self._get_proxy_url()
        proxy_sock = None
        
        if proxy_url:
            proxy_type = self.proxy_config.get('proxy_type', 'none')
            logger.info(f"【{self.cookie_id}】WebSocket将通过代理连接: {proxy_type}://{self.proxy_config.get('proxy_host')}:{self.proxy_config.get('proxy_port')}")
            
            try:
                from python_socks.async_.asyncio.v2 import Proxy
                from python_socks import ProxyType as SocksProxyType
                import ssl
                
                # 确定代理类型
                if proxy_type == 'socks5':
                    socks_type = SocksProxyType.SOCKS5
                elif proxy_type == 'socks4':
                    socks_type = SocksProxyType.SOCKS4
                elif proxy_type in ['http', 'https']:
                    socks_type = SocksProxyType.HTTP
                else:
                    socks_type = None
                
                if socks_type:
                    # 解析WebSocket URL获取目标主机和端口
                    import urllib.parse
                    parsed_url = urllib.parse.urlparse(self.base_url)
                    dest_host = parsed_url.hostname
                    dest_port = parsed_url.port or (443 if parsed_url.scheme == 'wss' else 80)
                    
                    # 创建代理连接
                    proxy = Proxy(
                        proxy_type=socks_type,
                        host=self.proxy_config.get('proxy_host'),
                        port=self.proxy_config.get('proxy_port'),
                        username=self.proxy_config.get('proxy_user') or None,
                        password=self.proxy_config.get('proxy_pass') or None
                    )
                    
                    # 通过代理连接到目标服务器
                    proxy_sock = await proxy.connect(
                        dest_host=dest_host,
                        dest_port=dest_port
                    )
                    
                    # 如果是wss，需要升级为SSL
                    if parsed_url.scheme == 'wss':
                        ssl_context = ssl.create_default_context()
                        proxy_sock = ssl_context.wrap_socket(
                            proxy_sock,
                            server_hostname=dest_host
                        )
                    
                    logger.info(f"【{self.cookie_id}】代理连接建立成功")
                    
            except ImportError as e:
                logger.warning(f"【{self.cookie_id}】代理连接需要安装 python-socks: pip install python-socks[asyncio]")
                logger.warning(f"【{self.cookie_id}】将尝试不使用代理进行WebSocket连接")
                proxy_sock = None
            except Exception as e:
                logger.error(f"【{self.cookie_id}】通过代理建立连接失败: {self._safe_str(e)}")
                logger.warning(f"【{self.cookie_id}】将尝试不使用代理进行WebSocket连接")
                proxy_sock = None

        try:
            # 尝试使用extra_headers参数
            connect_kwargs = {
                'extra_headers': headers
            }
            if proxy_sock:
                connect_kwargs['sock'] = proxy_sock
                
            return websockets.connect(
                self.base_url,
                **connect_kwargs
            )
        except Exception as e:
            # 捕获所有异常类型，不仅仅是TypeError
            error_msg = self._safe_str(e)
            logger.warning(f"【{self.cookie_id}】extra_headers参数失败: {error_msg}")

            if "extra_headers" in error_msg or "unexpected keyword argument" in error_msg:
                logger.warning(f"【{self.cookie_id}】websockets库不支持extra_headers参数，尝试additional_headers")
                # 使用additional_headers参数（较新版本）
                try:
                    connect_kwargs = {
                        'additional_headers': headers
                    }
                    if proxy_sock:
                        connect_kwargs['sock'] = proxy_sock
                        
                    return websockets.connect(
                        self.base_url,
                        **connect_kwargs
                    )
                except Exception as e2:
                    error_msg2 = self._safe_str(e2)
                    logger.warning(f"【{self.cookie_id}】additional_headers参数失败: {error_msg2}")

                    if "additional_headers" in error_msg2 or "unexpected keyword argument" in error_msg2:
                        raise RuntimeError(
                            f"当前websockets库不支持header参数，无法安全建立鉴权连接: {error_msg2}"
                        )
                    else:
                        raise e2
            else:
                raise e

    async def _handle_websocket_connection(self, websocket, toid, item_id, text):
        """处理WebSocket连接的具体逻辑"""
        try:
            await self.init(websocket)
            await self.create_chat(websocket, toid, item_id)

            # 添加超时处理，最多等待30秒
            timeout = 30
            start_time = time.time()

            async for message in websocket:
                try:
                    # 检查是否超时
                    if time.time() - start_time > timeout:
                        logger.warning(f"【{self.cookie_id}】WebSocket消息等待超时")
                        break

                    logger.info(f"【{self.cookie_id}】message: {message}")
                    message = json.loads(message)
                    cid = message["body"]["singleChatConversation"]["cid"]
                    cid = cid.split('@')[0]
                    await self.send_msg(websocket, cid, toid, text)
                    logger.info(f'【{self.cookie_id}】send message success')
                    return True
                except KeyError:
                    # 消息格式不符合预期，继续等待
                    continue
                except Exception as e:
                    logger.warning(f"【{self.cookie_id}】处理消息异常: {self._safe_str(e)}")
                    continue

            logger.warning(f"【{self.cookie_id}】WebSocket连接关闭，未能发送消息")
            return False
        except Exception as e:
            logger.error(f"【{self.cookie_id}】WebSocket连接处理异常: {self._safe_str(e)}")
            return False

    def is_chat_message(self, message):
        """判断是否为用户聊天消息"""
        try:
            return (
                isinstance(message, dict)
                and "1" in message
                and isinstance(message["1"], dict)
                and "10" in message["1"]
                and isinstance(message["1"]["10"], dict)
                and "reminderContent" in message["1"]["10"]
            )
        except Exception:
            return False

    def is_sync_package(self, message_data):
        """判断是否为同步包消息"""
        try:
            return (
                isinstance(message_data, dict)
                and "body" in message_data
                and "syncPushPackage" in message_data["body"]
                and "data" in message_data["body"]["syncPushPackage"]
                and len(message_data["body"]["syncPushPackage"]["data"]) > 0
            )
        except Exception:
            return False

    async def create_session(self):
        """创建aiohttp session，支持代理配置"""
        if not self.session:
            # 创建带有cookies和headers的session
            headers = DEFAULT_HEADERS.copy()

            proxy_url = self._get_proxy_url()
            connector = None
            
            if proxy_url:
                proxy_type = self.proxy_config.get('proxy_type', 'none')
                logger.info(f"【{self.cookie_id}】创建带代理的Session: {proxy_type}://{self.proxy_config.get('proxy_host')}:{self.proxy_config.get('proxy_port')}")
                
                if proxy_type == 'socks5':
                    # SOCKS5 代理使用 aiohttp_socks
                    try:
                        from aiohttp_socks import ProxyConnector, ProxyType
                        connector = ProxyConnector(
                            proxy_type=ProxyType.SOCKS5,
                            host=self.proxy_config.get('proxy_host'),
                            port=self.proxy_config.get('proxy_port'),
                            username=self.proxy_config.get('proxy_user') or None,
                            password=self.proxy_config.get('proxy_pass') or None,
                            rdns=True  # 使用代理服务器解析DNS
                        )
                    except ImportError:
                        logger.error(f"【{self.cookie_id}】SOCKS5代理需要安装 aiohttp-socks: pip install aiohttp-socks")
                        connector = None
                else:
                    # HTTP/HTTPS 代理使用 aiohttp 内置支持（通过 trust_env 或在请求时指定）
                    # 注意：aiohttp 的 TCPConnector 不直接支持 proxy 参数
                    # 代理将在每次请求时通过 proxy 参数指定
                    connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
            else:
                connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)

            self.session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
                connector=connector
            )
            self._sync_session_cookie_header()
            
            # 保存代理URL供后续请求使用（HTTP/HTTPS代理）
            self._http_proxy_url = proxy_url if proxy_url and self.proxy_config.get('proxy_type') in ['http', 'https'] else None

    async def close_session(self):
        """关闭aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None

    def _get_mtop_token(self) -> str:
        token_value = trans_cookies(self.cookies_str).get('_m_h5_tk', '')
        return token_value.split('_')[0] if token_value else ''

    async def _post_mtop_api(self, api_name: str, version: str, data: Dict[str, Any], *,
                             data_type: str = 'json', response_content_type: str = None,
                             extra_params: Dict[str, Any] = None, source: str = 'mtop_api') -> Dict[str, Any]:
        """发送通用的闲鱼 mtop POST 请求。"""
        if not self.session:
            await self.create_session()

        self._reload_latest_cookies_from_db(f"{api_name}调用前")

        timestamp = str(int(time.time() * 1000))
        data_val = json.dumps(data, separators=(',', ':'))
        token = self._get_mtop_token()

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': timestamp,
            'sign': generate_sign(timestamp, token, data_val),
            'v': version,
            'type': 'originaljson' if data_type == 'json' else data_type,
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': api_name,
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }
        if extra_params:
            params.update({k: v for k, v in extra_params.items() if v is not None})

        headers = DEFAULT_HEADERS.copy()
        headers['content-type'] = 'application/x-www-form-urlencoded'
        headers['cookie'] = self.cookies_str

        request_kwargs = {}
        if getattr(self, '_http_proxy_url', None):
            request_kwargs['proxy'] = self._http_proxy_url

        api_url = f'https://h5api.m.goofish.com/h5/{api_name}/{version}/'
        async with self.session.post(
            api_url,
            params=params,
            data={'data': data_val},
            headers=headers,
            **request_kwargs,
        ) as response:
            try:
                res_json = await response.json(content_type=response_content_type)
            except Exception:
                response_text = await response.text()
                logger.warning(f"【{self.cookie_id}】{api_name} 响应解析失败: {response_text[:300]}")
                return {'ret': ['FAIL_SYS_RESPONSE_PARSE::响应解析失败'], 'raw_text': response_text}

            await self._apply_response_cookie_updates(response.headers, source)
            return res_json if isinstance(res_json, dict) else {'ret': ['FAIL_SYS_RESPONSE_INVALID::响应格式异常']}

    async def fetch_im_user_info(self, session_id: str, session_type: int = 1,
                                 is_owner: bool = False, message_id: str = None) -> Dict[str, Any]:
        payload = {
            'type': 0,
            'sessionType': int(session_type or 1),
            'sessionId': str(session_id),
            'isOwner': bool(is_owner),
        }
        if message_id:
            payload['messageId'] = str(message_id)

        result = await self._post_mtop_api(
            'mtop.taobao.idlemessage.pc.user.query',
            '4.0',
            payload,
            source='im_user_query',
        )
        if any('SUCCESS::调用成功' in str(ret) for ret in (result.get('ret') or [])):
            return result.get('data', {}) or {}
        logger.warning(f"【{self.cookie_id}】获取IM用户信息失败: session_id={session_id}, ret={result.get('ret')}")
        return {}

    async def fetch_im_head_info(self, session_id: str, item_id: str, session_type: int = 1) -> Dict[str, Any]:
        if not item_id:
            return {}

        result = await self._post_mtop_api(
            'mtop.idle.trade.pc.message.headinfo',
            '1.0',
            {
                'itemId': int(item_id) if str(item_id).isdigit() else str(item_id),
                'sessionId': int(session_id) if str(session_id).isdigit() else str(session_id),
                'sessionType': int(session_type or 1),
            },
            data_type='json',
            response_content_type=None,
            extra_params={'valueType': 'string'},
            source='im_headinfo_query',
        )
        if any('SUCCESS::调用成功' in str(ret) for ret in (result.get('ret') or [])):
            return result.get('data', {}) or {}
        logger.warning(f"【{self.cookie_id}】获取IM会话头信息失败: session_id={session_id}, item_id={item_id}, ret={result.get('ret')}")
        return {}

    async def fetch_im_blacklist_status(self, session_id: str) -> Dict[str, Any]:
        result = await self._post_mtop_api(
            'mtop.taobao.idlemessage.pc.blacklist.query',
            '1.0',
            {'sessionId': str(session_id)},
            source='im_blacklist_query',
        )
        if any('SUCCESS::调用成功' in str(ret) for ret in (result.get('ret') or [])):
            return result.get('data', {}) or {}
        logger.warning(f"【{self.cookie_id}】获取IM黑名单状态失败: session_id={session_id}, ret={result.get('ret')}")
        return {}

    async def get_api_reply(self, msg_time, user_url, send_user_id, send_user_name, item_id, send_message, chat_id):
        """调用API获取回复消息"""
        try:
            if not self.session:
                await self.create_session()

            api_config = AUTO_REPLY.get('api', {})
            timeout = aiohttp.ClientTimeout(total=api_config.get('timeout', 10))

            payload = {
                "cookie_id": self.cookie_id,
                "msg_time": msg_time,
                "user_url": user_url,
                "send_user_id": send_user_id,
                "send_user_name": send_user_name,
                "item_id": item_id,
                "send_message": send_message,
                "chat_id": chat_id
            }

            async with self.session.post(
                api_config.get('url', 'http://localhost:8080/xianyu/reply'),
                json=payload,
                timeout=timeout
            ) as response:
                result = await response.json()

                # 将code转换为字符串进行比较，或者直接用数字比较
                if str(result.get('code')) == '200' or result.get('code') == 200:
                    send_msg = result.get('data', {}).get('send_msg')
                    if send_msg:
                        # 格式化消息中的占位符
                        return send_msg.format(
                            send_user_id=payload['send_user_id'],
                            send_user_name=payload['send_user_name'],
                            send_message=payload['send_message']
                        )
                    else:
                        logger.warning("API返回成功但无回复消息")
                        return None
                else:
                    logger.warning(f"API返回错误: {result.get('msg', '未知错误')}")
                    return None

        except asyncio.TimeoutError:
            logger.error("API调用超时")
            return None
        except Exception as e:
            logger.error(f"调用API出错: {self._safe_str(e)}")
            return None

    async def _handle_message_with_semaphore(self, message_data, websocket, msg_id="unknown"):
        """带信号量的消息处理包装器，防止并发任务过多"""
        async with self.message_semaphore:
            self.active_message_tasks += 1
            try:
                await self.handle_message(message_data, websocket, msg_id)
            finally:
                self.active_message_tasks -= 1
                # 定期记录活跃任务数（每100个任务记录一次）
                if self.active_message_tasks % 100 == 0 and self.active_message_tasks > 0:
                    logger.info(f"【{self.cookie_id}】当前活跃消息处理任务数: {self.active_message_tasks}")

    def _unwrap_message_for_dedupe(self, message_data: dict) -> Optional[dict]:
        """把同步包还原成内部消息结构，让 messageId / createTime 提取走统一路径。

        - 如果 message_data 已是内部结构（包含 key '1'），原样返回
        - 如果是 syncPushPackage 同步包，先 base64 + json 解第一条 data 段返回
        - 其它情况返回 None，让调用方走兜底标识
        """
        if not isinstance(message_data, dict):
            return None
        if "1" in message_data:
            return message_data

        try:
            if not self.is_sync_package(message_data):
                return None
            sync_entries = (
                ((message_data.get("body") or {}).get("syncPushPackage") or {}).get("data") or []
            )
            if not sync_entries:
                return None
            payload = sync_entries[0].get("data")
            if not payload:
                return None
            decoded = base64.b64decode(payload).decode("utf-8")
            inner = json.loads(decoded)
            return inner if isinstance(inner, dict) else None
        except Exception as exc:
            logger.debug(f"【{self.cookie_id}】解析同步包消息用于去重时失败: {self._safe_str(exc)}")
            return None

    def _extract_message_id(self, message_data: dict) -> str:
        """
        从消息数据中提取消息ID，用于去重
        
        Args:
            message_data: 原始消息数据
            
        Returns:
            消息ID字符串，如果无法提取则返回None
        """
        try:
            # 同步包消息要先还原到内部结构，否则下面的 message['1']['10']['bizTag'] 路径取不到
            normalized_message = self._unwrap_message_for_dedupe(message_data)

            # 尝试从 message['1']['10']['bizTag'] 中提取 messageId
            if isinstance(normalized_message, dict) and "1" in normalized_message:
                message_1 = normalized_message.get("1")
                if isinstance(message_1, dict) and "10" in message_1:
                    message_10 = message_1.get("10")
                    if isinstance(message_10, dict) and "bizTag" in message_10:
                        biz_tag = message_10.get("bizTag", "")
                        if isinstance(biz_tag, str):
                            # bizTag 是 JSON 字符串，格式如: '{"sourceId":"S:1","messageId":"984f323c719d4cd0a7b993a0769a33b6"}'
                            try:
                                import json
                                biz_tag_dict = json.loads(biz_tag)
                                if isinstance(biz_tag_dict, dict) and "messageId" in biz_tag_dict:
                                    return biz_tag_dict.get("messageId")
                            except (json.JSONDecodeError, TypeError):
                                pass
                        
                        # 如果 bizTag 解析失败，尝试从 extJson 中提取
                        if "extJson" in message_10:
                            ext_json = message_10.get("extJson", "")
                            if isinstance(ext_json, str):
                                try:
                                    import json
                                    ext_json_dict = json.loads(ext_json)
                                    if isinstance(ext_json_dict, dict) and "messageId" in ext_json_dict:
                                        return ext_json_dict.get("messageId")
                                except (json.JSONDecodeError, TypeError):
                                    pass
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】提取消息ID失败: {self._safe_str(e)}")

        return None

    def _extract_message_id_from_chat_payload(self, message_1: dict, message_10: dict) -> str:
        """从已解出的聊天消息结构里直接提取 messageId，避免重复解同步包。"""
        try:
            if not isinstance(message_1, dict) or not isinstance(message_10, dict):
                return None

            biz_tag = message_10.get("bizTag", "")
            if isinstance(biz_tag, str) and biz_tag:
                try:
                    biz_tag_dict = json.loads(biz_tag)
                    if isinstance(biz_tag_dict, dict) and biz_tag_dict.get("messageId"):
                        return str(biz_tag_dict["messageId"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            ext_json = message_10.get("extJson", "")
            if isinstance(ext_json, str) and ext_json:
                try:
                    ext_json_dict = json.loads(ext_json)
                    if isinstance(ext_json_dict, dict) and ext_json_dict.get("messageId"):
                        return str(ext_json_dict["messageId"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
        except Exception as e:
            logger.debug(f"【{self.cookie_id}】从聊天消息结构提取messageId失败: {self._safe_str(e)}")

        return None

    def _cleanup_message_reply_state(self, current_time: float):
        """清理过期的已处理/处理中消息状态。"""
        expired_processed_ids = [
            msg_id for msg_id, timestamp in self.processed_message_ids.items()
            if current_time - timestamp > self.message_expire_time
        ]
        for msg_id in expired_processed_ids:
            del self.processed_message_ids[msg_id]

        expired_pending_ids = [
            msg_id for msg_id, timestamp in self.pending_message_ids.items()
            if current_time - timestamp > self.pending_message_expire_time
        ]
        for msg_id in expired_pending_ids:
            del self.pending_message_ids[msg_id]

        if expired_processed_ids:
            logger.info(f"【{self.cookie_id}】已清理 {len(expired_processed_ids)} 个过期消息ID")
        if expired_pending_ids:
            logger.warning(f"【{self.cookie_id}】已清理 {len(expired_pending_ids)} 个超时未完成的消息预占")

        if len(self.processed_message_ids) > self.processed_message_ids_max_size:
            sorted_ids = sorted(self.processed_message_ids.items(), key=lambda x: x[1])
            remove_count = len(sorted_ids) // 2
            for msg_id, _ in sorted_ids[:remove_count]:
                del self.processed_message_ids[msg_id]
            logger.info(f"【{self.cookie_id}】消息ID去重字典过大，已清理 {remove_count} 个最旧记录")

    async def _reserve_message_reply(self, message_id: str) -> bool:
        """为消息创建处理预占，防止并发重复回复。"""
        async with self.processed_message_ids_lock:
            current_time = time.time()
            self._cleanup_message_reply_state(current_time)

            if message_id in self.processed_message_ids:
                last_process_time = self.processed_message_ids[message_id]
                time_elapsed = current_time - last_process_time
                remaining_time = int(max(0, self.message_expire_time - time_elapsed))
                logger.warning(f"【{self.cookie_id}】消息ID {message_id[:50]}... 已处理过，距离可重复回复还需 {remaining_time} 秒")
                return False

            if message_id in self.pending_message_ids:
                time_elapsed = current_time - self.pending_message_ids[message_id]
                remaining_time = int(max(0, self.pending_message_expire_time - time_elapsed))
                logger.warning(f"【{self.cookie_id}】消息ID {message_id[:50]}... 正在处理中，预占剩余约 {remaining_time} 秒")
                return False

            self.pending_message_ids[message_id] = current_time
            return True

    async def _finalize_message_reply(self, message_id: str, reason: str = ""):
        """将消息从处理中转为已完成，后续重复包不再回复。"""
        async with self.processed_message_ids_lock:
            current_time = time.time()
            self.pending_message_ids.pop(message_id, None)
            self.processed_message_ids[message_id] = current_time
            self._cleanup_message_reply_state(current_time)

        if reason:
            logger.info(f"【{self.cookie_id}】消息ID {message_id[:50]}... 已完成处理: {reason}")

    async def _release_message_reply(self, message_id: str, reason: str = ""):
        """释放消息处理预占，允许后续重试。"""
        async with self.processed_message_ids_lock:
            released = self.pending_message_ids.pop(message_id, None)

        if released is not None:
            logger.warning(f"【{self.cookie_id}】消息ID {message_id[:50]}... 已释放预占，允许重试: {reason or 'unknown'}")

    async def _apply_message_filters(
        self,
        send_user_name: str,
        send_user_id: str,
        send_message: str,
        item_id: str,
        chat_id: str,
        msg_time: str,
        message_source: str = 'user',
        execute_actions: bool = True,
    ) -> dict:
        """匹配消息过滤规则，并按需执行通知/暂停动作。"""
        empty_result = {
            'matched': False,
            'rules': [],
            'skip_auto_reply': False,
            'skip_ai_reply': False,
            'pause_minutes': 0,
            'notify_enabled': False,
        }
        try:
            from message_filter_service import message_filter_service
            result = message_filter_service.match_by_cookie(
                cookie_id=self.cookie_id,
                message=send_message,
                item_id=item_id,
                message_source=message_source,
            )
        except Exception as e:
            logger.error(f"[{msg_time}] 【{self.cookie_id}】消息过滤规则匹配失败: {self._safe_str(e)}")
            return empty_result

        if not result.get('matched'):
            return result

        matched_rules = result.get('rules') or []
        rule_names = [str(rule.get('name') or rule.get('id') or '').strip() for rule in matched_rules]
        rule_label = '、'.join([name for name in rule_names if name]) or '未命名规则'
        logger.info(
            f"[{msg_time}] 【{self.cookie_id}】命中消息过滤规则: {rule_label}，"
            f"skip_auto={bool(result.get('skip_auto_reply'))}, "
            f"skip_ai={bool(result.get('skip_ai_reply'))}, "
            f"pause={int(result.get('pause_minutes') or 0)}分钟, "
            f"notify={bool(result.get('notify_enabled'))}"
        )

        if not execute_actions:
            return result

        pause_minutes = int(result.get('pause_minutes') or 0)
        if pause_minutes > 0:
            pause_manager.pause_chat_for(
                chat_id,
                self.cookie_id,
                pause_minutes,
                reason=f"命中消息过滤规则「{rule_label}」"
            )

        if result.get('notify_enabled'):
            try:
                notify_message = f"[消息过滤] 命中规则「{rule_label}」：{send_message}"
                await self.send_notification(send_user_name, send_user_id, notify_message, item_id, chat_id)
            except Exception as notify_error:
                logger.error(f"[{msg_time}] 【{self.cookie_id}】消息过滤通知发送失败: {self._safe_str(notify_error)}")

        return result

    async def _schedule_debounced_reply(self, chat_id: str, message_data: dict, websocket,
                                       send_user_name: str, send_user_id: str, send_message: str,
                                       item_id: str, msg_time: str, dedupe_message_id: str = None,
                                       dedupe_create_time: int = 0):
        """
        调度防抖回复：如果用户连续发送消息，等待用户停止发送后再回复最后一条消息
        
        Args:
            chat_id: 聊天ID
            message_data: 原始消息数据
            websocket: WebSocket连接
            send_user_name: 发送者用户名
            send_user_id: 发送者用户ID
            send_message: 消息内容
            item_id: 商品ID
            msg_time: 消息时间
        """
        # 提取消息ID并检查是否已处理（优先使用调用链已解出的 messageId，避免重复解同步包）
        message_id = str(dedupe_message_id).strip() if dedupe_message_id else self._extract_message_id(message_data)
        # 如果没有 messageId，使用备用标识（chat_id + send_user_id + send_message + 时间戳）
        if not message_id:
            try:
                # 同步包消息要先还原到内部结构再取 createTime
                normalized_message = self._unwrap_message_for_dedupe(message_data) or {}
                # 优先使用调用链里已提取出的 create_time，避免退化成 _0 后缀
                create_time = int(dedupe_create_time or 0)
                if isinstance(normalized_message, dict) and "1" in normalized_message:
                    message_1 = normalized_message.get("1")
                    if isinstance(message_1, dict):
                        create_time = int(message_1.get("5", create_time) or create_time or 0)
                if not create_time:
                    create_time = int(time.time() * 1000)
                # 使用更稳的组合键作为备用标识（带 send_user_id 减少不同人同文本撞车）
                message_id = f"{chat_id}_{send_user_id}_{send_message}_{create_time}"
            except Exception:
                # 如果提取失败，使用当前时间戳
                message_id = f"{chat_id}_{send_user_id}_{send_message}_{int(time.time() * 1000)}"

        # in-flight 锁：原子地检查"已处理 / 正在处理"两个状态，预占后才进入防抖
        # （替代原来的 inline check-and-set，修复同消息并发时被多次回复的问题）
        if not await self._reserve_message_reply(message_id):
            return

        async with self.message_debounce_lock:
            # 如果该chat_id已有防抖任务，取消它
            if chat_id in self.message_debounce_tasks:
                old_task = self.message_debounce_tasks[chat_id].get('task')
                if old_task and not old_task.done():
                    old_task.cancel()
                    logger.warning(f"【{self.cookie_id}】取消chat_id {chat_id} 的旧防抖任务")

            # 更新最后一条消息信息
            current_timer = time.time()
            self.message_debounce_tasks[chat_id] = {
                'last_message': {
                    'message_id': message_id,
                    'message_data': message_data,
                    'websocket': websocket,
                    'send_user_name': send_user_name,
                    'send_user_id': send_user_id,
                    'send_message': send_message,
                    'item_id': item_id,
                    'msg_time': msg_time
                },
                'timer': current_timer
            }
            
            # 创建新的防抖任务
            async def debounce_task():
                saved_timer = current_timer  # 保存创建任务时的时间戳
                try:
                    # 等待防抖延迟时间
                    await asyncio.sleep(self.message_debounce_delay)
                    
                    # 检查是否仍然是最新的消息（防止在等待期间有新消息）
                    async with self.message_debounce_lock:
                        if chat_id not in self.message_debounce_tasks:
                            return
                        
                        debounce_info = self.message_debounce_tasks[chat_id]
                        # 检查时间戳是否匹配（确保这是最新的消息）
                        if saved_timer != debounce_info['timer']:
                            logger.warning(f"【{self.cookie_id}】chat_id {chat_id} 在防抖期间有新消息，跳过旧消息处理")
                            return
                        
                        # 获取最后一条消息
                        last_msg = debounce_info['last_message']
                        
                        # 从防抖任务中移除
                        del self.message_debounce_tasks[chat_id]
                    
                    # 处理最后一条消息
                    logger.info(f"【{self.cookie_id}】防抖延迟结束，开始处理chat_id {chat_id} 的最后一条消息: {last_msg['send_message'][:30]}...")
                    await self._process_chat_message_reply(
                        last_msg['message_data'],
                        last_msg['websocket'],
                        last_msg['send_user_name'],
                        last_msg['send_user_id'],
                        last_msg['send_message'],
                        last_msg['item_id'],
                        chat_id,
                        last_msg['msg_time']
                    )
                    # 无异常即视为已收口，把 in-flight 预占转成已处理（防止短时间重复入队）
                    await self._finalize_message_reply(last_msg['message_id'], reason="回复链处理完成")

                except asyncio.CancelledError:
                    logger.warning(f"【{self.cookie_id}】chat_id {chat_id} 的防抖任务被取消")
                    try:
                        await self._release_message_reply(message_id, reason="防抖任务取消")
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】处理防抖回复时发生错误: {self._safe_str(e)}")
                    try:
                        await self._release_message_reply(message_id, reason=f"防抖任务异常: {self._safe_str(e)}")
                    except Exception:
                        pass
                    # 确保从防抖任务中移除
                    async with self.message_debounce_lock:
                        if chat_id in self.message_debounce_tasks:
                            del self.message_debounce_tasks[chat_id]
            
            task = self._create_tracked_task(debounce_task())
            self.message_debounce_tasks[chat_id]['task'] = task
            logger.warning(f"【{self.cookie_id}】为chat_id {chat_id} 创建防抖任务，延迟 {self.message_debounce_delay} 秒")

    async def _process_chat_message_reply(self, message_data: dict, websocket, send_user_name: str,
                                         send_user_id: str, send_message: str, item_id: str,
                                         chat_id: str, msg_time: str):
        """
        处理聊天消息的回复逻辑（从handle_message中提取出来的核心回复逻辑）
        
        Args:
            message_data: 原始消息数据
            websocket: WebSocket连接
            send_user_name: 发送者用户名
            send_user_id: 发送者用户ID
            send_message: 消息内容
            item_id: 商品ID
            chat_id: 聊天ID
            msg_time: 消息时间
        """
        try:
            # 自动回复消息
            if not AUTO_REPLY.get('enabled', True):
                logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】自动回复已禁用")
                return

            # 检查该chat_id是否处于暂停状态
            if pause_manager.is_chat_paused(chat_id):
                remaining_time = pause_manager.get_remaining_pause_time(chat_id)
                remaining_minutes = remaining_time // 60
                remaining_seconds = remaining_time % 60
                logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】chat_id {chat_id} 自动回复已暂停，剩余时间: {remaining_minutes}分{remaining_seconds}秒")
                return

            blacklist_hit = self._check_buyer_blacklist_for_action(
                buyer_id=send_user_id,
                item_id=item_id,
                buyer_nick=send_user_name,
                action='自动回复',
                log_delivery=False,
            )
            if blacklist_hit:
                return

            message_filter_result = await self._apply_message_filters(
                send_user_name=send_user_name,
                send_user_id=send_user_id,
                send_message=send_message,
                item_id=item_id,
                chat_id=chat_id,
                msg_time=msg_time,
                message_source='user',
                execute_actions=False,
            )
            if message_filter_result.get('skip_auto_reply'):
                logger.info(f"[{msg_time}] 【{self.cookie_id}】命中消息过滤规则，跳过自动回复")
                return
            skip_ai_reply = bool(message_filter_result.get('skip_ai_reply'))

            reply = None
            reply_source = None

            # 按 README 定义的优先级处理：
            # 指定商品回复 > 商品专用关键词 > 通用关键词 > 默认回复 > AI回复
            reply = await self.get_item_specific_reply(send_user_name, send_user_id, send_message, item_id)
            if reply:
                reply_source = '指定商品'
            else:
                # 1. 尝试关键词匹配（内部已区分商品专用关键词和通用关键词）
                reply = await self.get_keyword_reply(send_user_name, send_user_id, send_message, item_id)
                if reply == "EMPTY_REPLY":
                    # 匹配到关键词但回复内容为空，不进行任何回复
                    logger.info(f"[{msg_time}] 【{self.cookie_id}】匹配到空回复关键词，跳过自动回复")
                    return
                elif reply:
                    reply_source = '关键词'  # 标记为关键词回复
                else:
                    # 2. 关键词匹配失败后，使用默认回复兜底
                    reply = await self.get_default_reply(send_user_name, send_user_id, send_message, chat_id, item_id)
                    if reply == "EMPTY_REPLY":
                        logger.info(f"[{msg_time}] 【{self.cookie_id}】默认回复内容为空，跳过自动回复")
                        return
                    elif reply == "SKIP_REPLY":
                        logger.info(f"[{msg_time}] 【{self.cookie_id}】默认回复已命中过当前会话，跳过自动回复")
                        return
                    elif reply:
                        reply_source = '默认'
                    else:
                        # 3. 最后尝试AI回复
                        if skip_ai_reply:
                            logger.info(f"[{msg_time}] 【{self.cookie_id}】命中消息过滤规则，跳过AI回复")
                        else:
                            reply = await self.get_ai_reply(send_user_name, send_user_id, send_message, item_id, chat_id)
                            if reply:
                                reply_source = 'AI'

            # 注意：这里只有商品ID，没有标题和详情，根据新的规则不保存到数据库
            # 商品信息会在其他有完整信息的地方保存（如发货规则匹配时）
            # 消息通知已在收到消息时立即发送，此处不再重复发送

            # 如果有回复内容，发送消息
            if reply:
                # 检查是否是图片发送标记
                if reply.startswith("__IMAGE_SEND__"):
                    # 提取图片URL（关键词回复不包含卡券ID）
                    image_url = reply.replace("__IMAGE_SEND__", "")
                    # 发送图片消息
                    try:
                        await self.send_image_msg(websocket, chat_id, send_user_id, image_url)
                        # 记录发出的图片消息
                        msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                        logger.info(f"[{msg_time}] 【{reply_source}图片发出】用户: {send_user_name} (ID: {send_user_id}), 商品({item_id}): 图片 {image_url}")
                    except Exception as e:
                        # 图片发送失败，发送错误提示
                        logger.error(f"图片发送失败: {self._safe_str(e)}")
                        await self.send_msg(websocket, chat_id, send_user_id, "抱歉，图片发送失败，请稍后重试。")
                        msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                        logger.error(f"[{msg_time}] 【{reply_source}图片发送失败】用户: {send_user_name} (ID: {send_user_id}), 商品({item_id})")
                else:
                    # 普通文本消息
                    await self.send_msg(websocket, chat_id, send_user_id, reply)
                    # 记录发出的消息
                    msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    logger.info(f"[{msg_time}] 【{reply_source}发出】用户: {send_user_name} (ID: {send_user_id}), 商品({item_id}): {reply}")
                    try:
                        from db_manager import db_manager as _db
                        from chat_event_hub import publish_chat_message
                        image_url = None
                        media_url = None
                        link_url = None
                        extra_json = None
                        _msg_id_db = _db.save_chat_message(
                            cookie_id=self.cookie_id, chat_id=chat_id,
                            sender_id=self.myid, sender_name=self.cookie_id,
                            content=reply, content_type=1,
                            image_url=image_url,
                            item_id=item_id, direction=1, reply_source=reply_source,
                            media_url=media_url, link_url=link_url, extra_json=extra_json,
                        )
                        publish_chat_message(self.cookie_id, {
                            'msg_id': _msg_id_db, 'chat_id': chat_id,
                            'sender_id': self.myid, 'sender_name': self.cookie_id,
                            'content': reply, 'content_type': 1,
                            'image_url': image_url,
                            'item_id': item_id, 'direction': 1, 'reply_source': reply_source,
                            'media_url': media_url, 'link_url': link_url, 'extra_json': extra_json,
                        })
                    except Exception as _e:
                        logger.debug(f"保存/推送发出消息失败: {self._safe_str(_e)}")
            else:
                msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                logger.info(f"[{msg_time}] 【{self.cookie_id}】【系统】未找到匹配的回复规则，不回复")
        except Exception as e:
            logger.error(f"处理聊天消息回复时发生错误: {self._safe_str(e)}")

    async def handle_message(self, message_data, websocket, msg_id="unknown"):
        """处理所有类型的消息"""
        # 获取消息大小用于追踪
        msg_size = len(json.dumps(message_data)) if message_data else 0
        logger.info(f"【{self.cookie_id}】[{msg_id}] 🚀 开始处理消息 ({msg_size}字节)")
        
        try:
            # 检查账号是否启用
            from cookie_manager import manager as cookie_manager
            if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                logger.warning(f"【{self.cookie_id}】[{msg_id}] ⏹️ 账号已禁用，消息处理结束")
                return

            # 发送确认消息
            try:
                message = message_data
                ack = {
                    "code": 200,
                    "headers": {
                        "mid": message["headers"]["mid"] if "mid" in message["headers"] else generate_mid(),
                        "sid": message["headers"]["sid"] if "sid" in message["headers"] else '',
                    }
                }
                if 'app-key' in message["headers"]:
                    ack["headers"]["app-key"] = message["headers"]["app-key"]
                if 'ua' in message["headers"]:
                    ack["headers"]["ua"] = message["headers"]["ua"]
                if 'dt' in message["headers"]:
                    ack["headers"]["dt"] = message["headers"]["dt"]
                await websocket.send(json.dumps(ack))
            except Exception as e:
                logger.debug(f"【{self.cookie_id}】[{msg_id}] 发送ACK失败: {e}")

            # 如果不是同步包消息，直接返回
            if not self.is_sync_package(message_data):
                logger.debug(f"【{self.cookie_id}】[{msg_id}] ⏹️ 非同步包消息，处理结束")
                return

            # 获取并解密数据
            sync_data = message_data["body"]["syncPushPackage"]["data"][0]

            # 检查是否有必要的字段
            if "data" not in sync_data:
                logger.warning(f"【{self.cookie_id}】[{msg_id}] ⚠️ 同步包中无data字段，消息内容: {sync_data}")
                logger.warning(f"【{self.cookie_id}】[{msg_id}] ⏹️ 消息处理结束（缺少data字段）")
                return

            # 解密数据
            message = None
            try:
                data = sync_data["data"]
                logger.debug(f"【{self.cookie_id}】[{msg_id}] 开始解密同步包数据...")
                try:
                    data = base64.b64decode(data).decode("utf-8")
                    parsed_data = json.loads(data)
                    # 处理未加密的消息（如系统提示等）
                    msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    if isinstance(parsed_data, dict) and 'chatType' in parsed_data:
                        logger.warning(f"【{self.cookie_id}】[{msg_id}] ⚠️ 检测到chatType消息，完整内容: {parsed_data}")
                        if 'operation' in parsed_data and 'content' in parsed_data['operation']:
                            content = parsed_data['operation']['content']
                            if 'sessionArouse' in content:
                                # 处理系统引导消息
                                logger.info(f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】小闲鱼智能提示:")
                                if 'arouseChatScriptInfo' in content['sessionArouse']:
                                    for qa in content['sessionArouse']['arouseChatScriptInfo']:
                                        logger.info(f"  - {qa['chatScrip']}")
                                logger.info(f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⏹️ 系统引导消息处理完成")
                                return
                            elif 'contentType' in content:
                                # 其他类型的未加密消息
                                logger.warning(f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】其他类型消息: {content}")
                        # ⚠️ 修复：不能直接return，应该继续处理这条消息
                        # 因为付款消息可能也包含chatType字段
                        logger.warning(f"【{self.cookie_id}】[{msg_id}] ⚠️ chatType消息但不是引导消息，继续处理...")
                        message = parsed_data
                    else:
                        # 如果不是系统消息，将解析的数据作为message
                        logger.debug(f"【{self.cookie_id}】[{msg_id}] 解密成功，正常消息")
                        message = parsed_data
                except Exception as e:
                    # 如果JSON解析失败，尝试解密
                    logger.debug(f"【{self.cookie_id}】[{msg_id}] JSON解析失败，尝试解密...")
                    decrypted_data = decrypt(data)
                    message = json.loads(decrypted_data)
                    logger.debug(f"【{self.cookie_id}】[{msg_id}] 解密成功")
            except Exception as e:
                # ⚠️ 关键：对于解密失败的大消息，记录完整信息
                logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息解密失败: {self._safe_str(e)}")
                if msg_size > 3000:
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ⚠️⚠️⚠️ 大消息({msg_size}字节)解密失败，完整sync_data: {sync_data}")
                    # 尝试记录base64数据的前后部分
                    try:
                        raw_data = sync_data.get("data", "")
                        logger.error(f"【{self.cookie_id}】[{msg_id}] Base64数据长度: {len(raw_data)}")
                        logger.error(f"【{self.cookie_id}】[{msg_id}] Base64前100字符: {raw_data[:100]}")
                        logger.error(f"【{self.cookie_id}】[{msg_id}] Base64后100字符: {raw_data[-100:]}")
                    except Exception:
                        pass
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 消息处理结束（解密失败）")
                return

            # 确保message不为空
            if message is None:
                logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息解析后为空")
                if msg_size > 3000:
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ⚠️⚠️⚠️ 大消息({msg_size}字节)解析后为空！")
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 消息处理结束（解析后为空）")
                return

            # 确保message是字典类型
            if not isinstance(message, dict):
                logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息格式错误，期望字典但得到: {type(message)}")
                logger.warning(f"【{self.cookie_id}】[{msg_id}] 消息内容: {message}")
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 消息处理结束（格式错误）")
                return

            # 【消息接收标识】记录收到消息的时间，用于控制Cookie刷新
            self.last_message_received_time = time.time()
            logger.warning(f"【{self.cookie_id}】[{msg_id}] ✅ 开始处理消息")

            # 【优先处理】尝试获取订单ID并获取订单详情
            order_id = None
            try:
                logger.info(f"【{self.cookie_id}】[{msg_id}] 🔍 开始提取订单ID，消息类型: {type(message)}")
                order_id = self._extract_order_id(message, message_data)
                if order_id:
                    msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 检测到订单ID: {order_id}，开始获取订单详情')

                    order_context = self._extract_order_message_context(message, msg_id=msg_id)
                    temp_user_id = order_context.get('buyer_id')
                    temp_user_id_source = order_context.get('buyer_id_source')
                    temp_item_id = order_context.get('item_id')
                    temp_sid = order_context.get('sid')
                    temp_buyer_nick = order_context.get('buyer_nick')

                    # 通知订单状态处理器订单ID已提取
                    if self.order_status_handler:
                        logger.info(f"【{self.cookie_id}】准备调用订单状态处理器.on_order_id_extracted: {order_id}")
                        try:
                            self.order_status_handler.on_order_id_extracted(
                                order_id,
                                self.cookie_id,
                                message,
                                match_context={
                                    'sid': temp_sid,
                                    'buyer_id': temp_user_id,
                                    'item_id': temp_item_id,
                                }
                            )
                            logger.info(f"【{self.cookie_id}】订单状态处理器.on_order_id_extracted调用成功: {order_id}")
                        except Exception as e:
                            logger.error(f"【{self.cookie_id}】通知订单状态处理器订单ID提取失败: {self._safe_str(e)}")
                            import traceback
                            logger.error(f"【{self.cookie_id}】详细错误信息: {traceback.format_exc()}")
                    else:
                        logger.warning(f"【{self.cookie_id}】订单状态处理器为None，跳过订单ID提取通知: {order_id}")

                    basic_order_saved = self._preload_basic_order_info(
                        order_id,
                        item_id=temp_item_id,
                        buyer_id=temp_user_id,
                        sid=temp_sid,
                        buyer_nick=temp_buyer_nick,
                        buyer_id_source=temp_user_id_source,
                    )

                    # 立即获取订单详情信息
                    try:
                        # 调用订单详情获取方法（传入sid和buyer_nick用于保存到数据库）
                        order_detail = await self.fetch_order_detail_info(
                            order_id,
                            temp_item_id,
                            temp_user_id,
                            sid=temp_sid,
                            buyer_nick=temp_buyer_nick,
                            buyer_id_source=temp_user_id_source,
                        )
                        if order_detail:
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】✅ 订单详情获取成功: {order_id}')
                        else:
                            logger.warning(f'[{msg_time}] 【{self.cookie_id}】⚠️ 订单详情获取失败: {order_id}')
                            if basic_order_saved:
                                self._schedule_order_detail_retry(
                                    order_id,
                                    item_id=temp_item_id,
                                    buyer_id=temp_user_id,
                                    sid=temp_sid,
                                    buyer_nick=temp_buyer_nick,
                                    delay_seconds=30,
                                    buyer_id_source=temp_user_id_source,
                                )

                    except Exception as detail_e:
                        logger.error(f'[{msg_time}] 【{self.cookie_id}】❌ 获取订单详情异常: {self._safe_str(detail_e)}')
                        if basic_order_saved:
                            self._schedule_order_detail_retry(
                                order_id,
                                item_id=temp_item_id,
                                buyer_id=temp_user_id,
                                sid=temp_sid,
                                buyer_nick=temp_buyer_nick,
                                delay_seconds=30,
                                buyer_id_source=temp_user_id_source,
                            )
                else:
                    logger.warning(f"【{self.cookie_id}】[{msg_id}] 未检测到订单ID")
            except Exception as e:
                logger.error(f"【{self.cookie_id}】[{msg_id}] 提取订单ID失败: {self._safe_str(e)}")

            # 安全地获取用户ID
            user_id = None
            try:
                message_1 = message.get("1")
                if isinstance(message_1, str):
                    # message['1'] 是字符串（sid 或 PNM 等），尝试从 message['4'] 提取 buyer_id
                    message_4 = message.get("4")
                    if isinstance(message_4, dict):
                        user_id = message_4.get("senderUserId") or None
                elif isinstance(message_1, dict):
                    # 如果message['1']是字典，从message["1"]["10"]["senderUserId"]中提取user_id
                    if "10" in message_1 and isinstance(message_1["10"], dict):
                        user_id = message_1["10"].get("senderUserId") or None
                    else:
                        user_id = None
                else:
                    user_id = None
            except Exception as e:
                logger.warning(f"提取用户ID失败: {self._safe_str(e)}")
                user_id = None



            # 安全地提取商品ID
            item_id = None
            try:
                if "1" in message and isinstance(message["1"], dict) and "10" in message["1"] and isinstance(message["1"]["10"], dict):
                    url_info = message["1"]["10"].get("reminderUrl", "")
                    if isinstance(url_info, str) and "itemId=" in url_info:
                        item_id = url_info.split("itemId=")[1].split("&")[0]

                # 如果没有提取到，使用辅助方法
                if not item_id:
                    item_id = self.extract_item_id_from_message(message)

                if not item_id:
                    item_id = f"auto_{user_id}_{int(time.time())}"
                    logger.warning(f"无法提取商品ID，使用默认值: {item_id}")

            except Exception as e:
                logger.error(f"提取商品ID时发生错误: {self._safe_str(e)}")
                item_id = f"auto_{user_id}_{int(time.time())}"
            # 处理订单状态消息
            try:
                logger.info(f"【{self.cookie_id}】[{msg_id}] 消息内容: {message}")
                msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

                # 安全地检查订单状态
                red_reminder = None
                if isinstance(message, dict) and "3" in message and isinstance(message["3"], dict):
                    red_reminder = message["3"].get("redReminder")

                if red_reminder == '等待买家付款':
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】等待买家 {user_url} 付款')
                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（等待买家付款）")
                    return
                elif red_reminder == '交易关闭':
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】买家 {user_url} 交易关闭')

                    # 【修复】更新订单状态到数据库
                    if self.order_status_handler:
                        try:
                            self.order_status_handler.handle_red_reminder_order_status(
                                red_reminder=red_reminder,
                                message=message,
                                user_id=user_id,
                                cookie_id=self.cookie_id,
                                msg_time=msg_time,
                                match_context={
                                    'sid': None,
                                    'buyer_id': user_id,
                                    'item_id': item_id,
                                }
                            )
                        except Exception as e:
                            logger.error(f"【{self.cookie_id}】更新交易关闭订单状态失败: {self._safe_str(e)}")

                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（交易关闭）")
                    return
                elif red_reminder == '等待卖家发货':
                    user_url = f'https://www.goofish.com/personal?userId={user_id}'
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【系统】交易成功 {user_url} 等待卖家发货')
                    
                    # 【关键修复】对于简化结构的消息（message['1']是字符串），根据sid查找订单信息后触发自动发货
                    # 简化消息结构: {'1': '56226853668@goofish', '2': 1, '3': {'redReminder': '等待卖家发货', ...}}
                    # message['1'] 就是 sid（会话ID）
                    # 【优化】只使用简化消息触发自动发货，完整付款消息已注释
                    if isinstance(message.get('1'), str):
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔔 检测到简化结构的发货通知消息，延迟处理')
                        await asyncio.sleep(30)
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔔 延迟30秒后处理简化发货')
                        # 检查是否启用自动确认发货
                        if self.is_auto_confirm_enabled():
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 自动确认发货已启用，开始处理')
                            
                            # 从简化消息中提取sid（会话ID），如 "56226853668@goofish"
                            simple_sid = message.get('1', '')
                            # 提取纯数字部分作为session_id_str
                            session_id_str = simple_sid.split('@')[0] if '@' in str(simple_sid) else simple_sid
                            
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔍 简化消息解析: sid={simple_sid}, session_id={session_id_str}')
                            
                            log_prefix = f'[{msg_time}] 【{self.cookie_id}】[{msg_id}]'
                            sid_lookup_minutes = 5
                            sid_lookup = self._lookup_delivery_order_by_sid(
                                simple_sid,
                                minutes=sid_lookup_minutes,
                                log_prefix=log_prefix
                            )
                            sid_lookup = await self._refresh_sid_lookup_if_needed(
                                simple_sid,
                                sid_lookup,
                                item_id=item_id,
                                buyer_id=user_id,
                                minutes=sid_lookup_minutes,
                                allow_bargain_ready=True,
                                log_prefix=log_prefix
                            )
                            recent_order = sid_lookup.get('order')
                            sid_match_type = sid_lookup.get('match_type', 'missing')
                            
                            if recent_order and sid_match_type in {'pending_ship', 'bargain_ready'}:
                                order_id = recent_order.get('order_id')
                                real_item_id = recent_order.get('item_id')
                                simple_user_id = recent_order.get('buyer_id', user_id)  # 从订单中获取buyer_id
                                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 通过sid从数据库找到订单: order_id={order_id}, item_id={real_item_id}, buyer_id={simple_user_id}')

                                if sid_match_type == 'bargain_ready':
                                    logger.info(
                                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ✅ 小刀订单缺少完整待发货卡片，'
                                        f'使用sid+小刀成功证据兜底进入自动发货: order_id={order_id}'
                                    )
                                
                                # 【防重复检查】先检查该订单是否已经在冷却期内（说明完整消息已经处理过）
                                if not self.can_auto_delivery(order_id):
                                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔒 订单 {order_id} 已在冷却期内（可能完整消息已处理），跳过简化消息发货')
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单已处理）")
                                    return
                                
                                # 【防重复检查】检查延迟锁状态
                                if self.is_lock_held(order_id):
                                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🔒 订单 {order_id} 延迟锁已被持有（可能完整消息正在处理），跳过简化消息发货')
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单正在处理）")
                                    return
                                
                                # 使用正确的商品ID和订单ID调用自动发货
                                simple_chat_id = session_id_str  # 使用会话ID作为chat_id
                                
                                # 调用自动发货处理（使用简化消息专用方法）
                                await self._handle_simple_message_auto_delivery(
                                    websocket=websocket,
                                    order_id=order_id,
                                    item_id=real_item_id,
                                    user_id=simple_user_id,
                                    chat_id=simple_chat_id,
                                    msg_time=msg_time,
                                    msg_id=msg_id
                                )
                                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（简化消息自动发货）")
                                return
                            elif recent_order:
                                order_id = recent_order.get('order_id')
                                order_status = recent_order.get('order_status') or 'unknown'
                                if sid_match_type == 'already_processed':
                                    logger.info(
                                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ℹ️ sid命中的订单已处理完成，跳过重复发货: '
                                        f'order_id={order_id}, status={order_status}'
                                    )
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单已处理）")
                                elif sid_match_type == 'cancelled':
                                    logger.info(
                                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ℹ️ sid命中的订单已关闭，跳过自动发货: '
                                        f'order_id={order_id}'
                                    )
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单已关闭）")
                                else:
                                    logger.info(
                                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ℹ️ sid命中的订单当前状态不适合简化消息兜底发货，等待后续完整消息: '
                                        f'order_id={order_id}, status={order_status}'
                                    )
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（订单状态未就绪）")
                                return
                            else:
                                logger.warning(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ❌ 未找到sid {simple_sid} 的最近订单，跳过自动发货')
                                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（未找到订单）")
                                return
                        else:
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⚠️ 未启用自动确认发货，跳过')
                            logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（未启用自动发货）")
                            return
                    # 如果不是简化结构，继续走正常流程
            except Exception:
                pass

            # 判断是否为聊天消息
            if not self.is_chat_message(message):
                logger.warning(f"【{self.cookie_id}】[{msg_id}] ⏹️ 非聊天消息，处理结束")
                return

            # 处理聊天消息
            try:
                # 安全地提取聊天消息信息
                if not (isinstance(message, dict) and "1" in message and isinstance(message["1"], dict)):
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息格式错误：缺少必要的字段结构")
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（格式错误）")
                    return

                message_1 = message["1"]
                if not isinstance(message_1.get("10"), dict):
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 消息格式错误：缺少消息详情字段")
                    logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（缺少详情字段）")
                    return

                create_time = int(message_1.get("5", 0))
                message_10 = message_1["10"]
                send_user_id = message_10.get("senderUserId", "unknown")

                chat_id_raw = message_1.get("2", "")
                chat_id = chat_id_raw.split('@')[0] if '@' in str(chat_id_raw) else str(chat_id_raw)

                sender_nick_raw = str(message_10.get("senderNick") or '').strip()
                if sender_nick_raw:
                    send_user_name = sender_nick_raw
                else:
                    # senderNick 缺失时仅使用 reminderTitle 兜底，且必须过滤系统文案
                    # （例如 "买家已拍下，待付款"、"等待你发货"、"工作台通知" 等订单状态/卡片标题），
                    # 否则会被当作买家昵称写入 chat_messages.sender_name 并污染会话列表与通知。
                    reminder_title_raw = str(message_10.get("reminderTitle") or '').strip()
                    sanitized_reminder = self._sanitize_buyer_nick(
                        reminder_title_raw,
                        source="reminderTitle",
                        message_meta=message_10,
                        log_prefix=f"【{self.cookie_id}】[{msg_id}]"
                    ) if reminder_title_raw else None
                    if not sanitized_reminder and send_user_id and send_user_id != "unknown":
                        # 兜底：从本地历史聊天记录里找一个干净的买家昵称
                        try:
                            from db_manager import db_manager as _db_lookup
                            recovered_nick = _db_lookup._lookup_buyer_nick_from_chat_messages(
                                self.cookie_id, chat_id_raw or chat_id, send_user_id
                            )
                            if recovered_nick:
                                sanitized_reminder = recovered_nick
                        except Exception as _lookup_err:
                            logger.debug(
                                f"【{self.cookie_id}】[{msg_id}] 历史买家昵称兜底查询失败: {self._safe_str(_lookup_err)}"
                            )
                    send_user_name = sanitized_reminder or "未知用户"
                send_message = message_10.get("reminderContent", "")
                # 直接从已解出的 chat payload 拿 messageId，传给 dedupe 链路避免重复解同步包
                dedupe_message_id = self._extract_message_id_from_chat_payload(message_1, message_10)

            except Exception as e:
                logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 提取聊天消息信息失败: {self._safe_str(e)}")
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（提取信息失败）")
                return

            # 格式化消息时间
            msg_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(create_time/1000))



            message_route_info = self._classify_message_route(
                message=message,
                message_1=message_1,
                message_10=message_10,
                send_message=send_message,
            )
            message_route = message_route_info.get('route', 'user_chat')
            order_status_signal = message_route_info.get('order_status_signal')
            should_notify_message = bool(message_route_info.get('should_notify'))
            allow_auto_reply = bool(message_route_info.get('allow_auto_reply'))
            is_system_message = bool(message_route_info.get('is_system_message'))
            is_group_message = bool(message_route_info.get('is_group_message'))
            message_direction = message_route_info.get('message_direction', 0)
            content_type = message_route_info.get('content_type', 0)
            card_title = str(message_route_info.get('card_title') or '').strip()
            special_flow_card_titles = {
                '我已小刀，待刀成',
                '我已小刀,待刀成',
                '我已成功小刀，待发货',
                '我已成功小刀,待发货',
            }

            logger.info(
                f"【{self.cookie_id}】[{msg_id}] 消息分类: route={message_route}, "
                f"status_signal={order_status_signal or 'none'}, notify={should_notify_message}, "
                f"auto_reply={allow_auto_reply}, system={is_system_message}, "
                f"direction={message_direction}, contentType={content_type}"
            )

            if send_user_id == self.myid and not is_system_message:
                logger.info(f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] 【手动发出】 商品({item_id}): {send_message}")

                # Web /api/chat/send 已经做过落库+publish，如果命中去重标记
                # 说明这是闲鱼对同一条消息的回推，直接跳过避免前端看到两条。
                try:
                    from chat_event_hub import self_send_dedup
                    if self_send_dedup.consume(self.cookie_id, chat_id, str(self.myid), send_message):
                        pause_manager.pause_chat(chat_id, self.cookie_id)
                        logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（Web 自发回推已去重）")
                        return
                except Exception as _e:
                    logger.debug(f"自发消息去重检查失败: {self._safe_str(_e)}")

                try:
                    from db_manager import db_manager as _db
                    from chat_event_hub import publish_chat_message
                    image_url = self._extract_image_url_from_message(message) if content_type == 2 else None
                    media_url = None
                    link_url = None
                    extra_json = None
                    _msg_id_db = _db.save_chat_message(
                        cookie_id=self.cookie_id, chat_id=chat_id,
                        sender_id=self.myid, sender_name=self.cookie_id,
                        content=send_message, content_type=content_type,
                        image_url=image_url,
                        item_id=item_id, direction=1, reply_source='手动',
                        media_url=media_url, link_url=link_url, extra_json=extra_json,
                    )
                    publish_chat_message(self.cookie_id, {
                        'msg_id': _msg_id_db, 'chat_id': chat_id,
                        'sender_id': self.myid, 'sender_name': self.cookie_id,
                        'content': send_message, 'content_type': content_type,
                        'image_url': image_url,
                        'item_id': item_id, 'direction': 1, 'reply_source': '手动',
                        'media_url': media_url, 'link_url': link_url, 'extra_json': extra_json,
                    })
                except Exception as _e:
                    logger.debug(f"保存/推送手动消息失败: {self._safe_str(_e)}")

                # 暂停该chat_id的自动回复10分钟
                pause_manager.pause_chat(chat_id, self.cookie_id)

                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（手动发出消息）")
                return
            elif send_user_id == self.myid and is_system_message:
                logger.info(
                    f"[{msg_time}] 【{self.cookie_id}】[{msg_id}] 检测到系统消息(sender=自己ID)，继续执行状态处理 "
                    f"(direction={message_direction}, contentType={content_type})"
                )
            else:
                logger.info(f"[{msg_time}] 【收到】用户: {send_user_name} (ID: {send_user_id}), 商品({item_id}): {send_message}")
                try:
                    from db_manager import db_manager as _db
                    from chat_event_hub import publish_chat_message
                    image_url = self._extract_image_url_from_message(message) if content_type == 2 else None
                    media_url = None
                    link_url = None
                    extra_json = None
                    _msg_id_db = _db.save_chat_message(
                        cookie_id=self.cookie_id, chat_id=chat_id,
                        sender_id=send_user_id, sender_name=send_user_name,
                        content=send_message, content_type=content_type,
                        image_url=image_url,
                        item_id=item_id, direction=2,
                        media_url=media_url, link_url=link_url, extra_json=extra_json,
                    )
                    publish_chat_message(self.cookie_id, {
                        'msg_id': _msg_id_db, 'chat_id': chat_id,
                        'sender_id': send_user_id, 'sender_name': send_user_name,
                        'content': send_message, 'content_type': content_type,
                        'image_url': image_url,
                        'item_id': item_id, 'direction': 2,
                        'media_url': media_url, 'link_url': link_url, 'extra_json': extra_json,
                    })
                except Exception as _e:
                    logger.debug(f"保存/推送聊天消息失败: {self._safe_str(_e)}")

                if message_route == 'user_chat':
                    self.last_user_chat_time = time.time()

                # 【优先处理】检查是否正在等待亦凡卡劵账号输入
                async with self.yifan_account_lock:
                    if chat_id in self.yifan_account_waiting:
                        waiting_info = self.yifan_account_waiting[chat_id]
                        
                        # 检查超时（30分钟）
                        if time.time() - waiting_info['create_time'] > 1800:
                            logger.warning(f"账号输入等待超时，清除等待状态")
                            del self.yifan_account_waiting[chat_id]
                        elif waiting_info['buyer_id'] == send_user_id:
                            # 检查是否为客户真实消息（过滤系统消息）
                            # 真实客户消息: message['1']['7'] = 2, contentType = 1
                            # 系统消息: message['1']['7'] = 1, contentType = 6 (textCard)
                            message_1 = message.get('1', {})
                            message_direction = message_1.get('7', 0) if isinstance(message_1, dict) else 0
                            
                            # 获取contentType
                            content_type = 0
                            try:
                                message_6 = message_1.get('6', {})
                                if isinstance(message_6, dict):
                                    message_6_3 = message_6.get('3', {})
                                    if isinstance(message_6_3, dict):
                                        content_type = message_6_3.get('4', 0)
                            except Exception:
                                pass
                            
                            # 检查bizTag是否包含系统消息标识
                            is_system_msg = False
                            try:
                                message_10 = message_1.get('10', {})
                                if isinstance(message_10, dict):
                                    biz_tag = message_10.get('bizTag', '')
                                    if biz_tag and ('SECURITY' in biz_tag or 'taskName' in biz_tag or 'taskId' in biz_tag):
                                        is_system_msg = True
                            except Exception:
                                pass
                            
                            # 过滤非真实客户消息：
                            # 1. message['1']['7'] != 2 表示不是接收的消息
                            # 2. contentType = 6 表示系统卡片消息
                            # 3. bizTag包含系统标识
                            if message_direction != 2 or content_type == 6 or is_system_msg:
                                logger.info(f"【{self.cookie_id}】[{msg_id}] 收到系统消息，跳过账号确认处理（direction={message_direction}, contentType={content_type}, isSystem={is_system_msg}）")
                                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（系统消息）")
                                return
                            
                            # 是同一个用户的真实回复
                            if waiting_info['state'] == 'waiting_account':
                                # 等待账号输入阶段
                                account = send_message.strip()
                                if account:
                                    # 保存账号并发送确认消息
                                    waiting_info['account'] = account
                                    waiting_info['state'] = 'waiting_confirm'
                                    
                                    confirm_msg = f"{account}\n这是您要充值的账号，请回答\"是\"，进行确认下单，如果账号不对，请重新输入正确的账号，如果因为您账号输错，导致错误下单，概不退款。"
                                    await self.send_msg(self.ws, chat_id, send_user_id, confirm_msg)
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] 已保存充值账号: {account}，等待用户确认")
                                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（等待账号确认）")
                                    return  # 处理完毕，不再继续其他流程
                                    
                            elif waiting_info['state'] == 'waiting_confirm':
                                # 等待确认阶段
                                user_reply = send_message.strip()
                                
                                if user_reply == '是':
                                    # 用户确认，继续发货流程
                                    logger.info(f"用户确认账号，继续亦凡API发货流程")
                                    account = waiting_info['account']
                                    rule = waiting_info['rule']
                                    order_id_saved = waiting_info.get('order_id')
                                    item_id_saved = waiting_info.get('item_id')
                                    
                                    # 清除等待状态
                                    del self.yifan_account_waiting[chat_id]
                                    
                                    # 继续执行亦凡API调用（带账号）
                                    try:
                                        if self._check_buyer_blacklist_for_action(
                                            buyer_id=send_user_id,
                                            item_id=item_id_saved,
                                            order_id=order_id_saved,
                                            buyer_nick=send_user_name,
                                            action='亦凡账号确认自动发货',
                                            channel='auto',
                                            log_delivery=True,
                                        ):
                                            logger.info(f"【{self.cookie_id}】[{msg_id}] 亦凡账号确认发货被黑名单拦截")
                                            return

                                        # 直接调用亦凡API下单
                                        delivery_content = await self._call_yifan_api_with_account(
                                            rule, account, order_id_saved, item_id_saved, send_user_id, chat_id
                                        )
                                        
                                        if delivery_content:
                                            delivery_steps = self._build_delivery_steps(
                                                delivery_content,
                                                rule.get('card_description', '')
                                            )
                                            await self._send_delivery_steps(
                                                self.ws,
                                                chat_id,
                                                send_user_id,
                                                delivery_steps,
                                                log_prefix=f"亦凡账号确认发货 order_id={order_id_saved or 'unknown'}"
                                            )

                                            finalize_result = await self._finalize_delivery_after_send(
                                                delivery_meta={
                                                    'success': True,
                                                    'rule_id': rule.get('id'),
                                                    'card_id': rule.get('card_id'),
                                                    'card_type': rule.get('card_type'),
                                                    'order_spec_mode': None,
                                                    'rule_spec_mode': None,
                                                    'item_config_mode': None,
                                                    'data_card_pending_consume': False,
                                                    'data_line': None
                                                },
                                                order_id=order_id_saved,
                                                item_id=item_id_saved
                                            )
                                            if not finalize_result.get('success'):
                                                self._record_delivery_log(
                                                    order_id=order_id_saved,
                                                    item_id=item_id_saved,
                                                    buyer_id=send_user_id,
                                                    status='failed',
                                                    reason=finalize_result.get('error') or '亦凡账号确认发货发送成功但提交副作用失败',
                                                    channel='auto',
                                                    rule_meta={
                                                        'rule_id': rule.get('id'),
                                                        'rule_keyword': rule.get('keyword'),
                                                        'card_type': rule.get('card_type')
                                                    }
                                                )
                                                await self.send_msg(self.ws, chat_id, send_user_id, "发货消息已发送，但确认发货失败，请稍后刷新订单状态。")
                                                logger.error(f"亦凡API自动发货副作用提交失败: {finalize_result.get('error')}")
                                                return

                                            if order_id_saved:
                                                self.mark_delivery_sent(order_id_saved, context="亦凡账号确认发货发送成功")
                                                self._activate_delivery_lock(order_id_saved, delay_minutes=10)

                                            self._record_delivery_log(
                                                order_id=order_id_saved,
                                                item_id=item_id_saved,
                                                buyer_id=send_user_id,
                                                status='success',
                                                reason='亦凡账号确认发货发送成功',
                                                channel='auto',
                                                rule_meta={
                                                    'rule_id': rule.get('id'),
                                                    'rule_keyword': rule.get('keyword'),
                                                    'card_type': rule.get('card_type')
                                                }
                                            )
                                            logger.info(f"亦凡API自动发货成功")
                                        else:
                                            # 发货失败通知
                                            await self.send_msg(self.ws, chat_id, send_user_id, "抱歉，自动发货失败，请联系客服处理。")
                                    except Exception as e:
                                        logger.error(f"亦凡API发货异常: {self._safe_str(e)}")
                                        await self.send_msg(self.ws, chat_id, send_user_id, "系统异常，请联系客服处理。")
                                    
                                    return  # 处理完毕
                                    
                                else:
                                    # 用户输入的不是"是"，认为是重新输入账号
                                    new_account = user_reply
                                    if new_account:
                                        waiting_info['account'] = new_account
                                        waiting_info['retry_count'] += 1
                                        
                                        # 检查重试次数
                                        if waiting_info['retry_count'] >= 5:
                                            logger.warning(f"【{self.cookie_id}】[{msg_id}] 账号确认重试次数过多，取消发货")
                                            del self.yifan_account_waiting[chat_id]
                                            await self.send_msg(self.ws, chat_id, send_user_id, "账号确认失败次数过多，已取消发货，请重新下单。")
                                            logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（重试次数过多）")
                                            return
                                        
                                        confirm_msg = f"{new_account}\n这是您要充值的账号，请回答\"是\"，进行确认下单，如果账号不对，请重新输入正确的账号，如果因为您账号输错，导致错误下单，概不退款。"
                                        await self.send_msg(self.ws, chat_id, send_user_id, confirm_msg)
                                        logger.info(f"【{self.cookie_id}】[{msg_id}] 用户重新输入账号: {new_account}，再次等待确认")
                                        logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（等待账号重新确认）")
                                        return

                try:
                    if is_group_message:
                        logger.info(f"📱 检测到群组消息（sessionType=30），跳过消息通知")
                    elif should_notify_message:
                        await self.send_notification(send_user_name, send_user_id, send_message, item_id, chat_id)
                    else:
                        logger.info(
                            f"📱 当前消息不发送通知: route={message_route}, "
                            f"status_signal={order_status_signal or 'none'}, message={send_message}"
                        )
                except Exception as notify_error:
                    logger.error(f"📱 发送消息通知失败: {self._safe_str(notify_error)}")




            # 【优先处理】使用订单状态处理器处理系统消息
            if self.order_status_handler:
                try:
                    # 处理系统消息的订单状态更新
                    try:
                        handled = self.order_status_handler.handle_system_message(
                            message=message,
                            send_message=send_message,
                            cookie_id=self.cookie_id,
                            msg_time=msg_time,
                            match_context={
                                'sid': message_1.get('2', '') if isinstance(message_1, dict) else None,
                                'buyer_id': send_user_id,
                                'item_id': item_id,
                            }
                        )
                    except Exception as e:
                        logger.error(f"【{self.cookie_id}】处理系统消息失败: {self._safe_str(e)}")
                        handled = False
                    
                    # 处理红色提醒消息
                    if not handled:
                        try:
                            if isinstance(message, dict) and "3" in message and isinstance(message["3"], dict):
                                red_reminder = message["3"].get("redReminder")
                                user_id = message["3"].get("userId", "unknown")
                                
                                if red_reminder:
                                    try:
                                        self.order_status_handler.handle_red_reminder_message(
                                            message=message,
                                            red_reminder=red_reminder,
                                            user_id=user_id,
                                            cookie_id=self.cookie_id,
                                            msg_time=msg_time,
                                            match_context={
                                                'sid': message_1.get('2', '') if isinstance(message_1, dict) else None,
                                                'buyer_id': send_user_id,
                                                'item_id': item_id,
                                            }
                                        )
                                    except Exception as e:
                                        logger.error(f"【{self.cookie_id}】处理红色提醒消息失败: {self._safe_str(e)}")
                        except Exception as red_e:
                            logger.warning(f"处理红色提醒消息失败: {self._safe_str(red_e)}")
                            
                except Exception as e:
                    logger.error(f"订单状态处理失败: {self._safe_str(e)}")

            # 关键状态消息到达时，按需补刷一次订单详情，避免缓存把状态留在旧值
            if order_id and order_status_signal in {'pending_ship', 'shipped', 'completed', 'cancelled', 'refunding'}:
                try:
                    refresh_sid = ''
                    if isinstance(message_1, dict):
                        refresh_sid = message_1.get("2", "")

                    await self._maybe_force_refresh_order_detail_for_signal(
                        order_id=order_id,
                        item_id=item_id,
                        buyer_id=send_user_id,
                        sid=refresh_sid,
                        buyer_nick=send_user_name,
                        status_signal=order_status_signal,
                        reason=f'message_signal_{order_status_signal}',
                        delay_seconds=1 if order_status_signal == 'pending_ship' else 0,
                        log_prefix=f"【{self.cookie_id}】[{msg_id}]"
                    )
                except Exception as refresh_e:
                    logger.error(
                        f"【{self.cookie_id}】[{msg_id}] 状态消息触发订单详情补刷失败: {self._safe_str(refresh_e)}"
                    )

            # 【优先处理】检查系统消息和自动发货触发消息（不受人工接入暂停影响）
            fallback_ignore_keywords = [
                '不想宝贝被砍价',
                'AI正在帮你回复',
                '发来一条',
                '小心假客服骗钱',
                '蚂蚁森林能量',
                '恭喜你拿到曝光卡',
                '订单即将自动确认收货',
                '温馨提醒：商品信息近期有过变更',
            ]
            if send_message == '[我已拍下，待付款]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 系统消息不处理')
                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（系统消息：待付款）")
                return
            elif send_message == '[你关闭了订单，钱款已原路退返]':
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 系统消息不处理')
                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（系统消息：订单关闭）")
                return
            elif send_message in [
                '快给ta一个评价吧~',
                '快给ta一个评价吧～',
            ]:
                # 检测到评价提醒消息，尝试自动好评
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 🌟 检测到评价提醒消息: {send_message}')
                await self.handle_auto_comment(message, msg_time, msg_id)
                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（评价提醒消息）")
                return
            elif message_route == 'system_notice' or any(keyword in send_message for keyword in fallback_ignore_keywords):
                logger.info(
                    f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⏹️ 系统提示消息不处理: '
                    f'route={message_route}, message={send_message}'
                )
                return
            # 简化消息通过 sid 查找订单，更可靠
            elif message_route == 'order_status' and self._is_auto_delivery_trigger(send_message):
                logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 检测到自动发货触发消息: {send_message}')

                # 只允许系统消息触发自动发货，防止买家手动输入关键字触发
                if not is_system_message:
                    logger.warning(
                        f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⚠️ 自动发货关键字来自非系统消息，已忽略 '
                        f'(direction={message_direction}, contentType={content_type})'
                    )
                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（非系统触发）")
                    return

                # 检查是否启用自动确认发货
                if not self.is_auto_confirm_enabled():
                    logger.info(f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] 未启用自动确认发货，跳过自动发货')
                    logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（未启用自动发货）")
                    return
                # 使用统一的自动发货处理方法（传递message_data以便提取订单ID）
                await self._handle_auto_delivery(websocket, message, send_user_name, send_user_id,
                                               item_id, chat_id, msg_time, message_data)
                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 处理结束（自动发货完成）")
                return
            # 【重要】检查小刀流程卡片消息 - 即使在人工接入暂停期间也要处理
            elif send_message == '[卡片消息]' or card_title in special_flow_card_titles:
                # 检查是否为小刀相关卡片消息
                try:
                    # 从消息中提取卡片内容
                    card_title = card_title or None
                    card_message_1 = message.get("1", {}) if isinstance(message, dict) else {}
                    if not card_title and isinstance(card_message_1, dict):
                        if "6" in card_message_1 and isinstance(card_message_1["6"], dict):
                            message_6 = card_message_1["6"]
                            if "3" in message_6 and isinstance(message_6["3"], dict):
                                message_6_3 = message_6["3"]
                                if "5" in message_6_3:
                                    # 解析JSON内容
                                    try:
                                        card_content = json.loads(message_6_3["5"])
                                        if "dxCard" in card_content and "item" in card_content["dxCard"]:
                                            card_item = card_content["dxCard"]["item"]
                                            if "main" in card_item and "exContent" in card_item["main"]:
                                                ex_content = card_item["main"]["exContent"]
                                                card_title = ex_content.get("title", "")
                                    except (json.JSONDecodeError, KeyError) as e:
                                        logger.warning(f"解析卡片消息失败: {e}")

                    # 卡片流程仅接受系统消息，避免伪造卡片触发
                    card_message_direction = card_message_1.get('7', 0) if isinstance(card_message_1, dict) else 0
                    card_content_type = 0
                    card_is_system_biz = False
                    try:
                        card_message_6 = card_message_1.get('6', {}) if isinstance(card_message_1, dict) else {}
                        if isinstance(card_message_6, dict):
                            card_message_6_3 = card_message_6.get('3', {})
                            if isinstance(card_message_6_3, dict):
                                card_content_type = card_message_6_3.get('4', 0)
                    except Exception:
                        pass

                    try:
                        card_message_10 = card_message_1.get('10', {}) if isinstance(card_message_1, dict) else {}
                        if isinstance(card_message_10, dict):
                            biz_tag = card_message_10.get('bizTag', '')
                            if biz_tag and ('SECURITY' in biz_tag or 'taskName' in biz_tag or 'taskId' in biz_tag):
                                card_is_system_biz = True
                    except Exception:
                        pass

                    is_system_card_message = card_message_direction == 1 or card_content_type == 6 or card_is_system_biz
                    if not is_system_card_message:
                        logger.warning(
                            f'[{msg_time}] 【{self.cookie_id}】[{msg_id}] ⚠️ 非系统卡片消息，忽略小刀流程 '
                            f'(direction={card_message_direction}, contentType={card_content_type}, isSystemBiz={card_is_system_biz})'
                        )
                        return

                    waiting_bargain_titles = {"我已小刀，待刀成", "我已小刀,待刀成"}
                    ready_to_ship_titles = {"我已成功小刀，待发货", "我已成功小刀,待发货"}

                    # 第一阶段：待刀成，仅执行免拼，不直接发货
                    if card_title in waiting_bargain_titles:
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】【系统】检测到"{card_title}"，执行免拼流程')
                        
                        # 检查是否启用自动确认发货
                        if not self.is_auto_confirm_enabled():
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】未启用自动确认发货，跳过自动小刀和自动发货')
                            return

                        # 检查商品是否属于当前cookies
                        if item_id and item_id != "未知商品":
                            try:
                                if not await self._ensure_item_owned_by_current_account(
                                    item_id,
                                    log_prefix=f'[{msg_time}] 【{self.cookie_id}】'
                                ):
                                    logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 商品 {item_id} 不属于当前账号，跳过免拼发货')
                                    return
                                logger.warning(f'[{msg_time}] 【{self.cookie_id}】✅ 商品 {item_id} 归属验证通过')
                            except Exception as e:
                                logger.error(f'[{msg_time}] 【{self.cookie_id}】检查商品归属失败: {self._safe_str(e)}，跳过免拼发货')
                                return

                        # 提取订单ID（传递原始消息数据以便在解密消息中找不到时进行备用搜索）
                        order_id = self._extract_order_id(message, message_data)
                        if not order_id:
                            logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 未能提取到订单ID，无法执行免拼发货')
                            return

                        self._mark_order_bargain_flow(
                            order_id,
                            item_id=item_id,
                            buyer_id=send_user_id,
                            context=card_title or 'waiting_bargain',
                        )

                        # 延迟2秒后执行免拼发货
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】延迟2秒后执行免拼发货...')
                        await asyncio.sleep(2)
                        # 调用自动免拼发货方法
                        result = await self.auto_freeshipping(order_id, item_id, send_user_id)
                        if result.get('success'):
                            self._mark_order_bargain_flow(
                                order_id,
                                item_id=item_id,
                                buyer_id=send_user_id,
                                apply_configured_price=True,
                                success_detected=True,
                                context=f'{card_title or "waiting_bargain"}_success',
                            )
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】✅ 自动免拼发货成功')
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】⏳ 已完成免拼，等待"我已成功小刀，待发货"卡片后再自动发货')
                            return
                        else:
                            logger.warning(f'[{msg_time}] 【{self.cookie_id}】❌ 自动免拼发货失败: {result.get("error", "未知错误")}')
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】⏹️ 免拼失败，不执行自动发货')
                            return

                    # 第二阶段：成功小刀待发货，触发自动发货
                    elif card_title in ready_to_ship_titles:
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】【系统】检测到"{card_title}"，开始自动发货')

                        order_id = self._extract_order_id(message, message_data)
                        if order_id:
                            self._mark_order_bargain_flow(
                                order_id,
                                item_id=item_id,
                                buyer_id=send_user_id,
                                apply_configured_price=True,
                                success_detected=True,
                                context=card_title,
                            )

                        # 检查是否启用自动确认发货
                        if not self.is_auto_confirm_enabled():
                            logger.info(f'[{msg_time}] 【{self.cookie_id}】未启用自动确认发货，跳过自动发货')
                            return

                        await self._handle_auto_delivery(
                            websocket, message, send_user_name, send_user_id,
                            item_id, chat_id, msg_time, message_data
                        )
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】⏹️ 小刀成功待发货卡片处理完成')
                        return
                    else:
                        logger.info(f'[{msg_time}] 【{self.cookie_id}】收到卡片消息，标题: {card_title or "未知"}')
                        # 如果不是目标卡片消息，继续正常处理流程（会受到暂停影响）

                except Exception as e:
                    logger.error(f"处理卡片消息异常: {self._safe_str(e)}")
                    # 如果处理异常，继续正常处理流程（会受到暂停影响）

            # 自动更新买家昵称（补全历史订单的昵称信息）
            # 需要过滤掉系统提示文本，避免将"买家已拍下，待付款"等写入昵称
            if send_user_id and send_user_name:
                valid_buyer_nick = self._sanitize_buyer_nick(
                    send_user_name,
                    source="message_sender",
                    message_meta=message_10 if isinstance(message_10, dict) else None,
                    log_prefix=f"【{self.cookie_id}】[{msg_id}]"
                )
                if valid_buyer_nick:
                    try:
                        from db_manager import db_manager
                        db_manager.update_buyer_nick_by_buyer_id(send_user_id, valid_buyer_nick, self.cookie_id)
                    except Exception as e:
                        logger.debug(f"更新买家昵称失败: {self._safe_str(e)}")

            message_filter_result = await self._apply_message_filters(
                send_user_name=send_user_name,
                send_user_id=send_user_id,
                send_message=send_message,
                item_id=item_id,
                chat_id=chat_id,
                msg_time=msg_time,
                message_source='system' if is_system_message else 'user',
                execute_actions=True,
            )

            if not allow_auto_reply:
                logger.info(
                    f"【{self.cookie_id}】[{msg_id}] ⏹️ 当前消息不进入自动回复链: "
                    f"route={message_route}, status_signal={order_status_signal or 'none'}"
                )
                return

            if message_filter_result.get('skip_auto_reply'):
                rule_names = '、'.join([
                    str(rule.get('name') or rule.get('id') or '').strip()
                    for rule in (message_filter_result.get('rules') or [])
                    if str(rule.get('name') or rule.get('id') or '').strip()
                ]) or '消息过滤规则'
                logger.info(f"【{self.cookie_id}】[{msg_id}] ⏹️ 命中{rule_names}，跳过自动回复")
                return

            # 身份判断：本账号主动去别人商品下咨询/购买时，自己是买家身份，
            # 对方（卖家）发来的消息不应触发本账号的自动/AI回复
            if await self._is_item_owned_by_self(item_id) is False:
                logger.info(
                    f"【{self.cookie_id}】[{msg_id}] ⏹️ 商品 {item_id} 非本账号所有"
                    f"（本账号为买家身份），跳过自动回复"
                )
                return

            # 使用防抖机制处理聊天消息回复
            # 如果用户连续发送消息，等待用户停止发送后再回复最后一条消息
            await self._schedule_debounced_reply(
                chat_id=chat_id,
                message_data=message_data,
                websocket=websocket,
                send_user_name=send_user_name,
                send_user_id=send_user_id,
                send_message=send_message,
                item_id=item_id,
                msg_time=msg_time,
                dedupe_message_id=dedupe_message_id,
                dedupe_create_time=create_time,
            )

        except Exception as e:
            logger.error(f"【{self.cookie_id}】[{msg_id}] ❌ 处理消息时发生异常: {self._safe_str(e)}")
            if msg_size > 3000:
                logger.error(f"【{self.cookie_id}】[{msg_id}] ⚠️⚠️⚠️ 大消息({msg_size}字节)处理异常！")
            logger.warning(f"【{self.cookie_id}】[{msg_id}] 原始消息: {message_data}")
            import traceback
            logger.error(f"【{self.cookie_id}】[{msg_id}] 异常堆栈: {traceback.format_exc()}")
        finally:
            # 确保每条消息都有明确的处理结束标记
            logger.info(f"【{self.cookie_id}】[{msg_id}] 🏁 消息处理完成 ({msg_size}字节)")

    async def main(self):
        """主程序入口"""
        try:
            logger.info(f"【{self.cookie_id}】开始启动XianyuLive主程序...")
            await self.create_session()  # 创建session
            logger.info(f"【{self.cookie_id}】Session创建完成，开始WebSocket连接循环...")

            while True:
                try:
                    # 检查账号是否启用
                    from cookie_manager import manager as cookie_manager
                    if cookie_manager and not cookie_manager.get_cookie_status(self.cookie_id):
                        logger.info(f"【{self.cookie_id}】账号已禁用，停止主循环")
                        break

                    init_auth_state = self.get_init_auth_failure_state(self.cookie_id) or {}
                    circuit_until = init_auth_state.get('circuit_until', 0)
                    if circuit_until and time.time() < circuit_until:
                        remaining_seconds = max(1, int(circuit_until - time.time()))
                        self._set_connection_state(ConnectionState.RECONNECTING, f"初始化鉴权冷静期剩余{remaining_seconds}秒")
                        logger.warning(
                            f"【{self.cookie_id}】初始化鉴权失败熔断中，暂停发起新的WebSocket连接，剩余 {remaining_seconds} 秒"
                        )
                        await self._interruptible_sleep(remaining_seconds)
                        continue

                    headers = self._build_websocket_headers()

                    # 更新连接状态为连接中
                    self._set_connection_state(ConnectionState.CONNECTING, "准备建立WebSocket连接")
                    logger.info(f"【{self.cookie_id}】WebSocket目标地址: {self.base_url}")

                    # 兼容不同版本的websockets库
                    async with await self._create_websocket_connection(headers) as websocket:
                        self.ws = websocket
                        logger.info(f"【{self.cookie_id}】WebSocket连接建立成功，开始初始化...")

                        try:
                            # 开始初始化
                            await self.init(websocket)
                            logger.info(f"【{self.cookie_id}】WebSocket初始化完成！")

                            # 初始化完成后才设置为已连接状态
                            self._set_connection_state(ConnectionState.CONNECTED, "初始化完成，连接就绪")
                            self.connection_failures = 0
                            self.last_successful_connection = time.time()
                            self._reset_stream_activity_state(self.last_successful_connection)

                            # 记录后台任务启动前的状态
                            logger.warning(f"【{self.cookie_id}】准备启动后台任务 - 当前状态: heartbeat={self.heartbeat_task}, token_refresh={self.token_refresh_task}, cleanup={self.cleanup_task}, cookie_refresh={self.cookie_refresh_task}, stream_watchdog={self.stream_watchdog_task}")
                            
                            # 如果存在心跳任务引用，先清理（心跳任务依赖WebSocket，必须重启）
                            if self.heartbeat_task:
                                logger.warning(f"【{self.cookie_id}】检测到旧心跳任务引用，先清理...")
                                self._reset_background_tasks()

                            # 启动心跳任务（依赖WebSocket，每次重连都需要重启）
                            logger.info(f"【{self.cookie_id}】启动心跳任务...")
                            self.heartbeat_task = asyncio.create_task(self.heartbeat_loop(websocket))

                            # 启动其他后台任务（不依赖WebSocket，只在首次连接时启动）
                            tasks_started = []
                            
                            if not self.token_refresh_task or self.token_refresh_task.done():
                                logger.info(f"【{self.cookie_id}】启动会话保活任务...")
                                self.token_refresh_task = asyncio.create_task(self.token_refresh_loop())
                                tasks_started.append("会话保活")
                            else:
                                logger.info(f"【{self.cookie_id}】Token刷新任务已在运行，跳过启动")

                            if not self.cleanup_task or self.cleanup_task.done():
                                logger.info(f"【{self.cookie_id}】启动暂停记录清理任务...")
                                self.cleanup_task = asyncio.create_task(self.pause_cleanup_loop())
                                tasks_started.append("暂停清理")
                            else:
                                logger.info(f"【{self.cookie_id}】暂停记录清理任务已在运行，跳过启动")

                            if not self.cookie_refresh_task or self.cookie_refresh_task.done():
                                logger.info(f"【{self.cookie_id}】启动Cookie刷新任务...")
                                self.cookie_refresh_task = asyncio.create_task(self.cookie_refresh_loop())
                                tasks_started.append("Cookie刷新")
                            else:
                                logger.info(f"【{self.cookie_id}】Cookie刷新任务已在运行，跳过启动")

                            if not self.stream_watchdog_task or self.stream_watchdog_task.done():
                                logger.info(f"【{self.cookie_id}】启动业务流看门狗任务...")
                                self.stream_watchdog_task = asyncio.create_task(self.message_stream_watchdog_loop())
                                tasks_started.append("业务流看门狗")
                            else:
                                logger.info(f"【{self.cookie_id}】业务流看门狗任务已在运行，跳过启动")

                            # 启动消息队列工作协程（高性能消息处理）
                            if self.message_queue_enabled:
                                await self._start_message_queue_workers()
                                tasks_started.append("消息队列")

                            # 记录所有后台任务状态
                            if tasks_started:
                                logger.info(f"【{self.cookie_id}】✅ 新启动的任务: {', '.join(tasks_started)}")
                            logger.info(f"【{self.cookie_id}】✅ 所有后台任务状态: 心跳(已启动), 会话保活({'运行中' if self.token_refresh_task and not self.token_refresh_task.done() else '已启动'}), 暂停清理({'运行中' if self.cleanup_task and not self.cleanup_task.done() else '已启动'}), Cookie刷新({'运行中' if self.cookie_refresh_task and not self.cookie_refresh_task.done() else '已启动'}), 业务流看门狗({'运行中' if self.stream_watchdog_task and not self.stream_watchdog_task.done() else '已启动'})")
                            
                            logger.info(f"【{self.cookie_id}】开始监听WebSocket消息...")
                            logger.info(f"【{self.cookie_id}】WebSocket连接状态正常，等待服务器消息...")
                            logger.info(f"【{self.cookie_id}】准备进入消息循环...")

                            async for message in websocket:
                                try:
                                    message_data = json.loads(message)

                                    if self._resolve_lwp_response_waiter(message_data):
                                        await self._ack_lwp_message(websocket, message_data)
                                        continue
                                    
                                    # 提取消息标识用于日志追踪（防止异步处理导致日志混乱）
                                    msg_id = "unknown"
                                    msg_preview = ""
                                    try:
                                        # 尝试从headers中提取mid
                                        if isinstance(message_data, dict) and "headers" in message_data:
                                            msg_id = message_data["headers"].get("mid", "unknown")
                                        # 尝试提取消息预览（用于区分不同类型的消息）
                                        if isinstance(message_data, dict) and "body" in message_data:
                                            if "syncPushPackage" in message_data["body"]:
                                                msg_preview = "[同步包]"
                                            elif "ack" in str(message_data["body"]).lower():
                                                msg_preview = "[确认]"
                                    except Exception:
                                        pass
                                    
                                    logger.info(f"【{self.cookie_id}】📨 收到消息 [ID:{msg_id}] {msg_preview} {len(message) if message else 0}字节")

                                    # 处理心跳响应（高优先级，直接处理）
                                    if await self.handle_heartbeat_response(message_data):
                                        continue

                                    is_sync_package = self.is_sync_package(message_data)
                                    self._mark_non_heartbeat_message(time.time(), is_sync_package=is_sync_package)

                                    # 处理其他消息
                                    # 使用高性能消息队列系统处理消息，解决消息阻塞问题
                                    if self.message_queue_enabled and self.message_queue_running:
                                        # 消息队列模式：快速入队，由工作协程异步处理
                                        await self._enqueue_message(message_data, websocket, msg_id)
                                    else:
                                        # 传统模式：使用追踪的异步任务处理消息
                                        self._create_tracked_task(self._handle_message_with_semaphore(message_data, websocket, msg_id))

                                except Exception as e:
                                    logger.error(f"处理消息出错: {self._safe_str(e)}")
                                    continue
                        finally:
                            # 停止消息队列工作协程
                            if self.message_queue_enabled and self.message_queue_running:
                                logger.info(f"【{self.cookie_id}】正在停止消息队列工作协程...")
                                await self._stop_message_queue_workers()
                            
                            # 确保在退出 async with 块时清理 WebSocket 引用
                            # 注意：async with 会自动关闭 WebSocket，但我们需要清理引用
                            if self.ws == websocket:
                                self.ws = None
                                logger.info(f"【{self.cookie_id}】WebSocket连接已退出，引用已清理")

                except InitAuthError as e:
                    error_msg = self._safe_str(e)
                    self.current_token = None
                    self.connection_failures = 0
                    init_auth_state = self.record_init_auth_failure(self.cookie_id, error_msg)
                    self.init_auth_failures = int(init_auth_state.get('count', 0))
                    self._set_connection_state(ConnectionState.RECONNECTING, f"初始化鉴权失败第{self.init_auth_failures}次")
                    logger.error(f"【{self.cookie_id}】初始化鉴权失败 ({self.init_auth_failures}/{self._init_auth_failure_threshold})")
                    logger.error(f"【{self.cookie_id}】初始化失败原因: {error_msg}")

                    retry_delay = self._calculate_retry_delay(error_msg)
                    circuit_until = init_auth_state.get('circuit_until', 0)
                    if circuit_until and time.time() < circuit_until:
                        circuit_wait = max(1, int(circuit_until - time.time()))
                        retry_delay = max(retry_delay, circuit_wait)
                        logger.warning(
                            f"【{self.cookie_id}】初始化鉴权失败已达到阈值，进入冷静期 {circuit_wait} 秒后再重试"
                        )
                    else:
                        logger.warning(f"【{self.cookie_id}】将在 {retry_delay} 秒后重试初始化鉴权...")

                    self._reset_background_tasks()
                    await self._interruptible_sleep(retry_delay)
                    logger.info(f"【{self.cookie_id}】初始化鉴权重试等待完成，准备重新建立连接...")
                    continue

                except Exception as e:
                    error_msg = self._safe_str(e)
                    import traceback
                    error_type = type(e).__name__
                    
                    # 检查是否是 ConnectionClosedError（正常的连接关闭）
                    is_connection_closed = (
                        'ConnectionClosedError' in error_type or 
                        'ConnectionClosed' in error_type or
                        'no close frame received or sent' in error_msg or
                        'IncompleteReadError' in error_type
                    )
                    
                    # 对于连接关闭错误，使用警告级别而不是错误级别
                    if is_connection_closed:
                        logger.warning(f"【{self.cookie_id}】WebSocket连接已关闭 ({self.connection_failures + 1}/{self.max_connection_failures})")
                        logger.warning(f"【{self.cookie_id}】关闭原因: {error_msg}")
                    else:
                        self.connection_failures += 1
                    # 更新连接状态为重连中
                    self._set_connection_state(ConnectionState.RECONNECTING, f"第{self.connection_failures}次失败")
                    logger.error(f"【{self.cookie_id}】WebSocket连接异常 ({self.connection_failures}/{self.max_connection_failures})")
                    logger.error(f"【{self.cookie_id}】异常类型: {error_type}")
                    logger.error(f"【{self.cookie_id}】异常信息: {error_msg}")
                    logger.warning(f"【{self.cookie_id}】异常堆栈:\n{traceback.format_exc()}")
                    
                    # 确保清理 WebSocket 引用
                    if self.ws:
                        try:
                            # 检查 WebSocket 是否仍然打开
                            if hasattr(self.ws, 'close_code') and self.ws.close_code is None:
                                # WebSocket 可能仍然打开，尝试关闭
                                try:
                                    await asyncio.wait_for(self.ws.close(), timeout=2.0)
                                except (asyncio.TimeoutError, Exception):
                                    pass
                        except Exception:
                            pass
                        finally:
                            self.ws = None
                            logger.info(f"【{self.cookie_id}】WebSocket引用已清理")
                    
                    # 对于连接关闭错误，也增加失败计数
                    if is_connection_closed:
                        self.connection_failures += 1
                        # 更新连接状态为重连中
                        self._set_connection_state(ConnectionState.RECONNECTING, f"连接关闭，第{self.connection_failures}次重连")

                    # 检查是否超过最大失败次数
                    if self.connection_failures >= self.max_connection_failures:
                        self._set_connection_state(ConnectionState.FAILED, f"连续失败{self.max_connection_failures}次")
                        logger.warning(f"【{self.cookie_id}】连续失败{self.max_connection_failures}次，尝试通过密码登录刷新Cookie...")
                        
                        try:
                            # 调用统一的密码登录刷新方法
                            refresh_success = await self._try_password_login_refresh(
                                f"连续失败{self.max_connection_failures}次",
                                ignore_slider_failed_backoff=self._has_recent_slider_success(),
                            )
                            
                            if refresh_success:
                                logger.info(f"【{self.cookie_id}】✅ 密码登录刷新成功，将重置失败计数并继续重连")
                                # 重置失败计数，因为已经刷新了Cookie
                                self.connection_failures = 0
                                # 更新连接状态
                                self._set_connection_state(ConnectionState.RECONNECTING, "Cookie已刷新，准备重连")
                                # 短暂等待后继续重连循环
                                await asyncio.sleep(2)
                                continue
                            else:
                                logger.warning(f"【{self.cookie_id}】❌ 密码登录刷新失败，将重启实例...")
                        except Exception as refresh_e:
                            logger.error(f"【{self.cookie_id}】密码登录刷新过程异常: {self._safe_str(refresh_e)}")
                            logger.warning(f"【{self.cookie_id}】将重启实例...")
                        
                        # 如果密码登录刷新失败或异常，则重启实例
                        logger.error(f"【{self.cookie_id}】准备重启实例...")
                        self.connection_failures = 0  # 重置失败计数
                        
                        # 先清理后台任务，避免与重启过程冲突
                        logger.info(f"【{self.cookie_id}】重启前先清理后台任务...")
                        try:
                            await asyncio.wait_for(
                                self._cancel_background_tasks(),
                                timeout=8.0  # 给足够时间让任务响应
                            )
                            logger.info(f"【{self.cookie_id}】后台任务已清理完成")
                        except asyncio.TimeoutError:
                            logger.warning(f"【{self.cookie_id}】后台任务清理超时，强制继续重启")
                        except Exception as cleanup_e:
                            logger.error(f"【{self.cookie_id}】后台任务清理失败: {self._safe_str(cleanup_e)}")
                        
                        # 触发重启（不等待完成）
                        await self._restart_instance()
                        
                        # ⚠️ 重要：_restart_instance() 已触发重启，2秒后当前任务会被取消
                        # 不要在这里等待或执行其他操作，让任务自然退出
                        logger.info(f"【{self.cookie_id}】重启请求已触发，主程序即将退出，新实例将自动启动")
                        return  # 退出当前连接循环，等待被取消

                    # 计算重试延迟
                    retry_delay = self._calculate_retry_delay(error_msg)
                    logger.warning(f"【{self.cookie_id}】将在 {retry_delay} 秒后重试连接...")

                    try:
                        # 清空当前token，确保重新连接时会重新获取
                        if self.current_token:
                            logger.warning(f"【{self.cookie_id}】清空当前token，重新连接时将重新获取")
                            self.current_token = None

                        # 直接重置任务引用，不等待取消（快速重连方案）
                        # 这样可以避免等待任务取消导致的阻塞问题
                        logger.info(f"【{self.cookie_id}】准备重置后台任务引用（快速重连模式）...")
                        self._reset_background_tasks()
                        logger.info(f"【{self.cookie_id}】后台任务引用已重置，可以立即重连")

                        # 等待后重试 - 使用可中断的sleep，并定期输出日志证明进程还活着
                        logger.info(f"【{self.cookie_id}】开始等待 {retry_delay} 秒...")
                        # 强制刷新日志缓冲区，确保日志被写入
                        try:
                            sys.stdout.flush()
                        except Exception:
                            pass
                        
                        # 使用可中断的sleep，每5秒输出一次心跳日志
                        chunk_size = 5.0  # 每5秒输出一次日志
                        remaining = retry_delay
                        start_time = time.time()
                        
                        while remaining > 0:
                            sleep_time = min(chunk_size, remaining)
                            try:
                                await asyncio.sleep(sleep_time)
                                remaining -= sleep_time
                                elapsed = time.time() - start_time
                                if remaining > 0:
                                    logger.info(f"【{self.cookie_id}】等待中... 已等待 {elapsed:.1f} 秒，剩余 {remaining:.1f} 秒")
                                    # 定期刷新日志
                                    try:
                                        sys.stdout.flush()
                                    except Exception:
                                        pass
                            except asyncio.CancelledError:
                                logger.warning(f"【{self.cookie_id}】等待期间收到取消信号")
                                raise
                            except Exception as sleep_error:
                                logger.error(f"【{self.cookie_id}】等待期间发生异常: {self._safe_str(sleep_error)}")
                                logger.warning(f"【{self.cookie_id}】等待异常堆栈:\n{traceback.format_exc()}")
                                # 即使出错也继续等待剩余时间
                                if remaining > 0:
                                    await asyncio.sleep(remaining)
                                break
                        
                        logger.info(f"【{self.cookie_id}】等待完成（总耗时 {time.time() - start_time:.1f} 秒），准备重新连接...")
                        # 再次强制刷新日志
                        try:
                            sys.stdout.flush()
                        except Exception:
                            pass
                        
                    except Exception as cleanup_error:
                        logger.error(f"【{self.cookie_id}】清理过程出错: {self._safe_str(cleanup_error)}")
                        logger.warning(f"【{self.cookie_id}】清理异常堆栈:\n{traceback.format_exc()}")
                        # 即使清理失败，也要重置任务引用并等待后重试
                        self.heartbeat_task = None
                        self.token_refresh_task = None
                        self.cleanup_task = None
                        self.cookie_refresh_task = None
                        self.stream_watchdog_task = None
                        logger.warning(f"【{self.cookie_id}】清理失败，已强制重置所有任务引用")
                        # 使用可中断的sleep，并定期输出日志
                        logger.info(f"【{self.cookie_id}】清理失败后开始等待 {retry_delay} 秒...")
                        chunk_size = 5.0
                        remaining = retry_delay
                        start_time = time.time()
                        
                        while remaining > 0:
                            sleep_time = min(chunk_size, remaining)
                            try:
                                await asyncio.sleep(sleep_time)
                                remaining -= sleep_time
                                if remaining > 0:
                                    logger.info(f"【{self.cookie_id}】清理失败后等待中... 剩余 {remaining:.1f} 秒")
                            except asyncio.CancelledError:
                                logger.warning(f"【{self.cookie_id}】清理失败后等待期间收到取消信号")
                                raise
                            except Exception as sleep_error:
                                logger.error(f"【{self.cookie_id}】清理失败后等待期间发生异常: {self._safe_str(sleep_error)}")
                                if remaining > 0:
                                    await asyncio.sleep(remaining)
                                break
                        
                        logger.info(f"【{self.cookie_id}】清理失败后等待完成（总耗时 {time.time() - start_time:.1f} 秒）")
                    
                    # 继续下一次循环
                    logger.info(f"【{self.cookie_id}】开始新一轮WebSocket连接尝试...")
                    continue
        finally:
            # 更新连接状态为已关闭
            self._set_connection_state(ConnectionState.CLOSED, "程序退出")
            
            # 清空当前token
            if self.current_token:
                logger.info(f"【{self.cookie_id}】程序退出，清空当前token")
                self.current_token = None

            # 检查是否还有未取消的后台任务，如果有才执行清理
            has_pending_tasks = any([
                self.heartbeat_task and not self.heartbeat_task.done(),
                self.token_refresh_task and not self.token_refresh_task.done(),
                self.cleanup_task and not self.cleanup_task.done(),
                self.cookie_refresh_task and not self.cookie_refresh_task.done(),
                self.stream_watchdog_task and not self.stream_watchdog_task.done()
            ])
            
            if has_pending_tasks:
                logger.info(f"【{self.cookie_id}】检测到未完成的后台任务，执行清理...")
                # 使用统一的任务清理方法，添加超时保护
                try:
                    await asyncio.wait_for(
                        self._cancel_background_tasks(),
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    logger.error(f"【{self.cookie_id}】程序退出时任务取消超时，强制继续")
                except Exception as e:
                    logger.error(f"【{self.cookie_id}】程序退出时任务取消失败: {self._safe_str(e)}")
                finally:
                    # 确保任务引用被重置
                    self.heartbeat_task = None
                    self.token_refresh_task = None
                    self.cleanup_task = None
                    self.cookie_refresh_task = None
                    self.stream_watchdog_task = None
            else:
                logger.info(f"【{self.cookie_id}】所有后台任务已清理完成，跳过重复清理")
                # 确保任务引用被重置
                self.heartbeat_task = None
                self.token_refresh_task = None
                self.cleanup_task = None
                self.cookie_refresh_task = None
                self.stream_watchdog_task = None
            
            # 清理所有后台任务
            if self.background_tasks:
                logger.info(f"【{self.cookie_id}】等待 {len(self.background_tasks)} 个后台任务完成...")
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*self.background_tasks, return_exceptions=True),
                        timeout=10.0  # 10秒超时
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"【{self.cookie_id}】后台任务清理超时，强制继续")
            
            # 确保关闭session
            await self.close_session()

            # 从全局实例字典中注销当前实例
            self._unregister_instance()
            logger.info(f"【{self.cookie_id}】XianyuLive主程序已完全退出")

    async def get_item_list_info(self, page_number=1, page_size=20, retry_count=0, sync_item_details=False):
        """获取商品信息，自动处理token失效的情况

        Args:
            page_number (int): 页码，从1开始
            page_size (int): 每页数量，默认20
            retry_count (int): 重试次数，内部使用
            sync_item_details (bool): 是否同步已存在商品的最新详情
        """
        if retry_count >= 4:  # 最多重试3次
            logger.error("获取商品信息失败，重试次数过多")
            return {"error": "获取商品信息失败，重试次数过多"}

        requested_page_size = page_size
        page_size = normalize_item_list_page_size(page_size)
        if str(requested_page_size) != str(page_size):
            logger.warning(f"商品列表每页数量已限制为 {page_size}: requested={requested_page_size}")

        # 确保session已创建
        if not self.session:
            await self.create_session()

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.idle.web.xyh.item.list',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
            'spm_pre': 'a21ybx.collection.menu.1.272b5141NafCNK'
        }

        data = {
            'needGroupInfo': False,
            'pageNumber': page_number,
            'pageSize': page_size,
            'groupName': '在售',
            'groupId': '58877261',
            'defaultGroup': True,
            "userId": self.myid
        }

        # 始终从最新的cookies中获取_m_h5_tk token（刷新后cookies会被更新）
        token = trans_cookies(self.cookies_str).get('_m_h5_tk', '').split('_')[0] if trans_cookies(self.cookies_str).get('_m_h5_tk') else ''

        logger.warning(f"准备获取商品列表，token: {token}")
        if token:
            logger.warning(f"使用cookies中的_m_h5_tk token: {self._mask_secret_value(token, head=6, tail=4)}")
        else:
            logger.warning("cookies中没有找到_m_h5_tk token")

        # 生成签名
        data_val = json.dumps(data, separators=(',', ':'))
        sign = generate_sign(params['t'], token, data_val)
        params['sign'] = sign

        try:
            async with self.session.post(
                'https://h5api.m.goofish.com/h5/mtop.idle.web.xyh.item.list/1.0/',
                params=params,
                data={'data': data_val}
            ) as response:
                res_json = await response.json()

                if await self._apply_response_cookie_updates(response.headers, "item_list"):
                    logger.warning("已更新Cookie到数据库")

                logger.info(f"商品信息获取响应: {res_json}")

                # 检查响应是否成功
                if res_json.get('ret') and res_json['ret'][0] == 'SUCCESS::调用成功':
                    items_data = res_json.get('data', {})
                    # 从cardList中提取商品信息
                    card_list = items_data.get('cardList', [])

                    # 解析cardList中的商品信息
                    items_list = []
                    for card in card_list:
                        card_data = card.get('cardData', {})
                        if card_data:
                            # 提取商品基本信息
                            item_info = {
                                'id': card_data.get('id', ''),
                                'title': card_data.get('title', ''),
                                'price': card_data.get('priceInfo', {}).get('price', ''),
                                'price_text': card_data.get('priceInfo', {}).get('preText', '') + card_data.get('priceInfo', {}).get('price', ''),
                                'category_id': card_data.get('categoryId', ''),
                                'auction_type': card_data.get('auctionType', ''),
                                'item_status': card_data.get('itemStatus', 0),
                                'detail_url': card_data.get('detailUrl', ''),
                                'pic_info': card_data.get('picInfo', {}),
                                'detail_params': card_data.get('detailParams', {}),
                                'track_params': card_data.get('trackParams', {}),
                                'item_label_data': card_data.get('itemLabelDataVO', {}),
                                'card_type': card.get('cardType', 0)
                            }
                            items_list.append(item_info)

                    logger.info(f"成功获取到 {len(items_list)} 个商品")

                    # 打印商品详细信息到控制台
                    print("\n" + "="*80)
                    print(f"📦 账号 {self.myid} 的商品列表 (第{page_number}页，{len(items_list)} 个商品)")
                    print("="*80)

                    for i, item in enumerate(items_list, 1):
                        print(f"\n🔸 商品 {i}:")
                        print(f"   商品ID: {item.get('id', 'N/A')}")
                        print(f"   商品标题: {item.get('title', 'N/A')}")
                        print(f"   价格: {item.get('price_text', 'N/A')}")
                        print(f"   分类ID: {item.get('category_id', 'N/A')}")
                        print(f"   商品状态: {item.get('item_status', 'N/A')}")
                        print(f"   拍卖类型: {item.get('auction_type', 'N/A')}")
                        print(f"   详情链接: {item.get('detail_url', 'N/A')}")
                        if item.get('pic_info'):
                            pic_info = item['pic_info']
                            print(f"   图片信息: {pic_info.get('width', 'N/A')}x{pic_info.get('height', 'N/A')}")
                            print(f"   图片链接: {pic_info.get('picUrl', 'N/A')}")
                        print(f"   完整信息: {json.dumps(item, ensure_ascii=False, indent=2)}")

                    print("\n" + "="*80)
                    print("✅ 商品列表获取完成")
                    print("="*80)

                    # 自动保存商品信息到数据库
                    if items_list:
                        saved_count = await self.save_items_list_to_db(
                            items_list,
                            sync_item_details=sync_item_details,
                        )
                        logger.info(f"已将 {saved_count} 个商品信息保存到数据库")

                    return {
                        "success": True,
                        "page_number": page_number,
                        "page_size": page_size,
                        "current_count": len(items_list),
                        "items": items_list,
                        "saved_count": saved_count if items_list else 0,
                        "raw_data": items_data  # 保留原始数据以备调试
                    }
                else:
                    # 检查是否是token失效
                    error_msg = res_json.get('ret', [''])[0] if res_json.get('ret') else ''
                    if 'FAIL_SYS_TOKEN_EXOIRED' in error_msg or 'token' in error_msg.lower():
                        logger.warning(f"Token失效，准备重试: {error_msg}")
                        await asyncio.sleep(0.5)
                        return await self.get_item_list_info(
                            page_number,
                            page_size,
                            retry_count + 1,
                            sync_item_details=sync_item_details,
                        )
                    else:
                        logger.error(f"获取商品信息失败: {res_json}")
                        return {"error": f"获取商品信息失败: {error_msg}"}

        except Exception as e:
            logger.error(f"商品信息API请求异常: {self._safe_str(e)}")
            await asyncio.sleep(0.5)
            return await self.get_item_list_info(
                page_number,
                page_size,
                retry_count + 1,
                sync_item_details=sync_item_details,
            )

    async def get_all_items(self, page_size=20, max_pages=None, sync_item_details=False):
        """获取所有商品信息（自动分页）

        Args:
            page_size (int): 每页数量，默认20
            max_pages (int): 最大页数限制，None表示无限制
            sync_item_details (bool): 是否同步已存在商品的最新详情

        Returns:
            dict: 包含所有商品信息的字典
        """
        page_size = normalize_item_list_page_size(page_size)
        all_items = []
        page_number = 1
        total_saved = 0

        logger.info(f"开始获取所有商品信息，每页{page_size}条")

        while True:
            if max_pages and page_number > max_pages:
                logger.info(f"达到最大页数限制 {max_pages}，停止获取")
                break

            logger.info(f"正在获取第 {page_number} 页...")
            result = await self.get_item_list_info(
                page_number,
                page_size,
                sync_item_details=sync_item_details,
            )

            if not result.get("success"):
                logger.error(f"获取第 {page_number} 页失败: {result}")
                break

            current_items = result.get("items", [])
            if not current_items:
                logger.info(f"第 {page_number} 页没有数据，获取完成")
                break

            all_items.extend(current_items)
            total_saved += result.get("saved_count", 0)

            logger.info(f"第 {page_number} 页获取到 {len(current_items)} 个商品")

            # 如果当前页商品数量少于页面大小，说明已经是最后一页
            if len(current_items) < page_size:
                logger.info(f"第 {page_number} 页商品数量({len(current_items)})少于页面大小({page_size})，获取完成")
                break

            page_number += 1

            # 添加延迟避免请求过快
            await asyncio.sleep(1)

        logger.info(f"所有商品获取完成，共 {len(all_items)} 个商品，保存了 {total_saved} 个")

        return {
            "success": True,
            "total_pages": page_number,
            "total_count": len(all_items),
            "total_saved": total_saved,
            "items": all_items
        }

    def _get_item_polish_module(self):
        from item_polish_module import ItemPolishModule

        return ItemPolishModule(self)

    async def polish_item(self, item_id, retry_count=0):
        """擦亮单个商品。"""
        return await self._get_item_polish_module().polish_item(item_id, retry_count)

    async def _polish_item_backup(self, item_id):
        """使用备用API擦亮商品。"""
        return await self._get_item_polish_module()._polish_item_backup(item_id)

    async def polish_all_items(self):
        """擦亮所有在售商品。"""
        return await self._get_item_polish_module().polish_all_items()

    async def send_image_msg(self, ws, cid, toid, image_url, width=800, height=600, card_id=None):
        """发送图片消息"""
        try:
            # 检查图片URL是否需要上传到CDN
            original_url = image_url

            if self._is_cdn_url(image_url):
                # 已经是CDN链接，直接使用
                logger.info(f"【{self.cookie_id}】使用已有的CDN图片链接: {image_url}")
            elif image_url.startswith('/static/uploads/') or image_url.startswith('static/uploads/'):
                # 本地图片，需要上传到闲鱼CDN
                local_image_path = image_url.replace('/static/uploads/', 'static/uploads/')
                if os.path.exists(local_image_path):
                    logger.info(f"【{self.cookie_id}】准备上传本地图片到闲鱼CDN: {local_image_path}")

                    # 使用图片上传器上传到闲鱼CDN
                    from utils.image_uploader import ImageUploader
                    uploader = ImageUploader(self.cookies_str)

                    async with uploader:
                        cdn_url = await uploader.upload_image(local_image_path)
                        if cdn_url:
                            logger.info(f"【{self.cookie_id}】图片上传成功，CDN URL: {cdn_url}")
                            image_url = cdn_url

                            # 如果是卡券图片，更新数据库中的图片URL
                            if card_id is not None:
                                await self._update_card_image_url(card_id, cdn_url)

                            # 获取实际图片尺寸
                            from utils.image_utils import image_manager
                            try:
                                actual_width, actual_height = image_manager.get_image_size(local_image_path)
                                if actual_width and actual_height:
                                    width, height = actual_width, actual_height
                                    logger.info(f"【{self.cookie_id}】获取到实际图片尺寸: {width}x{height}")
                            except Exception as e:
                                logger.warning(f"【{self.cookie_id}】获取图片尺寸失败，使用默认尺寸: {e}")
                        else:
                            logger.error(f"【{self.cookie_id}】图片上传失败: {local_image_path}")
                            logger.error(f"【{self.cookie_id}】❌ Cookie可能已失效！请检查配置并更新Cookie")
                            raise Exception(f"图片上传失败（Cookie可能已失效）: {local_image_path}")
                else:
                    logger.error(f"【{self.cookie_id}】本地图片文件不存在: {local_image_path}")
                    raise Exception(f"本地图片文件不存在: {local_image_path}")
            else:
                logger.warning(f"【{self.cookie_id}】未知的图片URL格式: {image_url}")

            # 记录详细的图片信息
            logger.info(f"【{self.cookie_id}】准备发送图片消息:")
            logger.info(f"  - 原始URL: {original_url}")
            logger.info(f"  - CDN URL: {image_url}")
            logger.info(f"  - 图片尺寸: {width}x{height}")
            logger.info(f"  - 聊天ID: {cid}")
            logger.info(f"  - 接收者ID: {toid}")

            # 构造图片消息内容 - 使用正确的闲鱼格式
            image_content = {
                "contentType": 2,  # 图片消息类型
                "image": {
                    "pics": [
                        {
                            "height": int(height),
                            "type": 0,
                            "url": image_url,
                            "width": int(width)
                        }
                    ]
                }
            }

            # Base64编码
            content_json = json.dumps(image_content, ensure_ascii=False)
            content_base64 = str(base64.b64encode(content_json.encode('utf-8')), 'utf-8')

            logger.info(f"【{self.cookie_id}】图片内容JSON: {content_json}")
            logger.info(f"【{self.cookie_id}】Base64编码长度: {len(content_base64)}")

            # 构造WebSocket消息（完全参考send_msg的格式）
            msg = {
                "lwp": "/r/MessageSend/sendByReceiverScope",
                "headers": {
                    "mid": generate_mid()
                },
                "body": [
                    {
                        "uuid": generate_uuid(),
                        "cid": f"{cid}@goofish",
                        "conversationType": 1,
                        "content": {
                            "contentType": 101,
                            "custom": {
                                "type": 1,
                                "data": content_base64
                            }
                        },
                        "redPointPolicy": 0,
                        "extension": {
                            "extJson": "{}"
                        },
                        "ctx": {
                            "appVersion": "1.0",
                            "platform": "web"
                        },
                        "mtags": {},
                        "msgReadStatusSetting": 1
                    },
                    {
                        "actualReceivers": [
                            f"{toid}@goofish",
                            f"{self.myid}@goofish"
                        ]
                    }
                ]
            }

            await ws.send(json.dumps(msg))
            logger.info(f"【{self.cookie_id}】图片消息发送成功: {image_url}")

        except Exception as e:
            logger.error(f"【{self.cookie_id}】发送图片消息失败: {self._safe_str(e)}")
            raise

    async def send_image_from_file(self, ws, cid, toid, image_path):
        """从本地文件发送图片"""
        try:
            # 上传图片到闲鱼CDN
            logger.info(f"【{self.cookie_id}】开始上传图片: {image_path}")

            from utils.image_uploader import ImageUploader
            uploader = ImageUploader(self.cookies_str)

            async with uploader:
                image_url = await uploader.upload_image(image_path)

            if image_url:
                # 获取图片信息
                from utils.image_utils import image_manager
                try:
                    from PIL import Image
                    with Image.open(image_path) as img:
                        width, height = img.size
                except Exception as e:
                    logger.warning(f"无法获取图片尺寸，使用默认值: {e}")
                    width, height = 800, 600

                # 发送图片消息
                await self.send_image_msg(ws, cid, toid, image_url, width, height)
                logger.info(f"【{self.cookie_id}】图片发送完成: {image_path} -> {image_url}")
                return True
            else:
                logger.error(f"【{self.cookie_id}】图片上传失败: {image_path}")
                logger.error(f"【{self.cookie_id}】❌ Cookie可能已失效！请检查配置并更新Cookie")
                return False

        except Exception as e:
            logger.error(f"【{self.cookie_id}】从文件发送图片失败: {self._safe_str(e)}")
            return False

if __name__ == '__main__':
    cookies_str = os.getenv('COOKIES_STR')
    xianyuLive = XianyuLive(cookies_str)
    asyncio.run(xianyuLive.main())
