#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
闲鱼滑块验证 - 增强反检测版本
基于最新的反检测技术，专门针对闲鱼、淘宝、阿里平台的滑块验证
"""

import time
import random
import json
import glob
import hashlib
import os
import math
import threading
import tempfile
import shutil
import subprocess
import re
import sys
import socket
import signal
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import sync_playwright as playwright_sync_playwright, ElementHandle
try:
    from patchright.sync_api import sync_playwright as patchright_sync_playwright
except ImportError:
    patchright_sync_playwright = None
from playwright.async_api import async_playwright
import asyncio
from typing import Optional, Tuple, List, Dict, Any, Callable
from loguru import logger
from collections import defaultdict

_PLAYWRIGHT_BROWSER_INSTALL_LOCK = threading.Lock()


# ============================================================================
# 1D Perlin 噪声实现（纯 Python，无外部依赖）
# 用于生成连续平滑的非周期性随机序列，替代 sin 叠加
# ============================================================================
def _perlin_fade(t):
    """Perlin 缓动函数: 6t^5 - 15t^4 + 10t^3"""
    return t * t * t * (t * (t * 6 - 15) + 10)


def _perlin_lerp(a, b, t):
    """线性插值"""
    return a + t * (b - a)


def _perlin_grad_1d(hash_val, x):
    """1D 梯度：根据 hash 值决定方向"""
    return x if (hash_val & 1) == 0 else -x


# 使用固定排列表（经典 Perlin 实现）
_PERLIN_PERM = list(range(256))
random.shuffle(_PERLIN_PERM)
_PERLIN_PERM = _PERLIN_PERM + _PERLIN_PERM  # 扩展到 512


def perlin_noise_1d(x, seed_offset=0):
    """1D Perlin 噪声，返回 [-1, 1] 范围的值

    Args:
        x: 采样坐标（连续浮点数）
        seed_offset: 种子偏移量，用于生成不同的噪声序列
    """
    xi = int(math.floor(x)) & 255
    xf = x - math.floor(x)
    u = _perlin_fade(xf)

    idx = (xi + int(seed_offset)) & 255
    a = _PERLIN_PERM[idx]
    b = _PERLIN_PERM[idx + 1]

    return _perlin_lerp(
        _perlin_grad_1d(a, xf),
        _perlin_grad_1d(b, xf - 1),
        u
    )


def perlin_octaves_1d(x, octaves=2, persistence=0.5, seed_offset=0):
    """多八度叠加的 1D Perlin 噪声（更丰富的细节）

    Args:
        x: 采样坐标
        octaves: 八度数（叠加层数）
        persistence: 每层振幅衰减比
        seed_offset: 种子偏移
    Returns:
        [-1, 1] 范围的噪声值
    """
    total = 0.0
    amplitude = 1.0
    frequency = 1.0
    max_amplitude = 0.0

    for _ in range(octaves):
        total += perlin_noise_1d(x * frequency, seed_offset) * amplitude
        max_amplitude += amplitude
        amplitude *= persistence
        frequency *= 2.0

    return total / max_amplitude if max_amplitude > 0 else 0.0


def parse_cookie_string(cookie_text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for part in str(cookie_text or "").replace("\ufeff", "").split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def generate_cookie_verification_device_id(user_id: str) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    buffer: List[str] = []
    for idx in range(36):
        if idx in (8, 13, 18, 23):
            buffer.append("-")
        elif idx == 14:
            buffer.append("4")
        else:
            rand_val = int(16 * random.random())
            if idx == 19:
                buffer.append(chars[(rand_val & 0x3) | 0x8])
            else:
                buffer.append(chars[rand_val])
    return "".join(buffer) + f"-{user_id}"


def build_cookie_verification_sign(ts: str, token: str, data: str) -> str:
    return hashlib.md5(f"{token}&{ts}&34839810&{data}".encode("utf-8")).hexdigest()


def probe_cookie_verification_from_cookie(
    cookie_text: str,
    proxy: Optional[Dict[str, Any]] = None,
    timeout: float = 30,
) -> Dict[str, Any]:
    import requests

    cookies = parse_cookie_string(cookie_text)
    user_id = cookies.get("unb", "")
    token = cookies.get("_m_h5_tk", "").split("_")[0]
    if not user_id or not token:
        raise ValueError("Cookie 缺少 unb 或 _m_h5_tk，无法获取最新 verification_url")

    session = requests.Session()
    session.headers.update({
        "accept": "application/json",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "origin": "https://www.goofish.com",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://www.goofish.com/",
        "sec-ch-ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        ),
    })
    session.cookies.update(cookies)

    proxies = None
    proxy_config = dict(proxy or {})
    proxy_type = str(proxy_config.get("proxy_type") or "").strip().lower()
    proxy_host = str(proxy_config.get("proxy_host") or "").strip()
    proxy_port = proxy_config.get("proxy_port")
    if proxy_type not in {"", "none"} and proxy_host and proxy_port:
        auth = ""
        if proxy_config.get("proxy_user"):
            auth = str(proxy_config["proxy_user"])
            if proxy_config.get("proxy_pass"):
                auth += f":{proxy_config['proxy_pass']}"
            auth += "@"
        proxy_url = f"{proxy_type}://{auth}{proxy_host}:{proxy_port}"
        proxies = {"http": proxy_url, "https": proxy_url}

    device_id = generate_cookie_verification_device_id(user_id)
    ts = str(int(time.time()) * 1000)
    data_val = (
        '{"appKey":"444e9908a51d1cb236a27862abc769c9",'
        f'"deviceId":"{device_id}"'
        "}"
    )
    params = {
        "jsv": "2.7.2",
        "appKey": "34839810",
        "t": ts,
        "sign": build_cookie_verification_sign(ts, token, data_val),
        "v": "1.0",
        "type": "originaljson",
        "accountSite": "xianyu",
        "dataType": "json",
        "timeout": "20000",
        "api": "mtop.taobao.idlemessage.pc.login.token",
        "sessionOption": "AutoLoginOnly",
        "spm_cnt": "a21ybx.im.0.0",
    }
    response = session.post(
        "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/",
        params=params,
        data={"data": data_val},
        proxies=proxies,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    data_payload = payload.get("data") or {}
    verification_url = str(data_payload.get("url") or "").strip() or None
    ret_value = payload.get("ret") or []
    success_ret = any("SUCCESS::调用成功" in str(ret) for ret in ret_value)
    has_token_payload = any(
        str(data_payload.get(field) or "").strip()
        for field in ("accessToken", "refreshToken")
    )

    status = "unknown"
    if verification_url:
        status = "verification_required"
    elif success_ret and has_token_payload:
        status = "cookie_valid"

    session_cookies = {}
    try:
        session_cookies = dict(session.cookies.get_dict())
    except Exception:
        session_cookies = dict(cookies)

    return {
        "status": status,
        "verification_url": verification_url,
        "payload": payload,
        "session_cookies": session_cookies,
        "success_ret": success_ret,
        "has_token_payload": has_token_payload,
    }


def resolve_verification_url_from_cookie(cookie_text: str, proxy: Optional[Dict[str, Any]] = None) -> str:
    probe_result = probe_cookie_verification_from_cookie(cookie_text, proxy=proxy)
    verification_url = probe_result.get("verification_url")
    if verification_url:
        return verification_url
    if probe_result.get("status") == "cookie_valid":
        raise RuntimeError(f"Cookie 已直接有效，无需 verification_url: {probe_result.get('payload')}")
    raise RuntimeError(f"未拿到最新 verification_url: {probe_result.get('payload')}")


class PasswordLoginVerificationError(Exception):
    """账号密码登录流程中的可识别验证错误。"""


class VerificationFrameWrapper:
    def __init__(self, original_frame, verification_type='unknown', verify_url=None, screenshot_path=None):
        self._original_frame = original_frame
        self.verification_type = verification_type
        self.verify_url = verify_url
        self.screenshot_path = screenshot_path

    def __getattr__(self, name):
        return getattr(self._original_frame, name)

# 导入配置
try:
    from config import SLIDER_VERIFICATION
    SLIDER_MAX_CONCURRENT = SLIDER_VERIFICATION.get('max_concurrent', 3)
    SLIDER_WAIT_TIMEOUT = SLIDER_VERIFICATION.get('wait_timeout', 60)
except ImportError:
    # 如果无法导入配置，使用默认值
    SLIDER_MAX_CONCURRENT = 3
    SLIDER_WAIT_TIMEOUT = 60

# ============================================================================
# 🏆 黄金参数配置（基于成功案例分析）
# 分析来源：trajectory_history/*.json 成功记录
# 分析时间：2026-01-28 优化版本
# ============================================================================
GOLDEN_PARAMS = {
    # 轨迹生成参数 - 🔧 2026-01-28 扩大随机范围，降低被检测概率
    "trajectory": {
        "overshoot_ratio": (1.02, 1.15),      # 🔧 改为真实超调比例2-15%（原1.93-2.05太极端）
        "steps": (18, 35),                     # 🔧 增加步数范围（原6-8太少）
        "base_delay": (0.004, 0.015),         # 🔧 增加延迟范围（原0.0003-0.0006太快）
        "acceleration_curve": (1.3, 2.2),     # 🔧 扩大曲线范围（原1.4-1.65）
        "y_jitter_max": (1.0, 3.5),           # 🔧 扩大Y轴抖动范围（原1.5-2.5）
    },
    # 滑动行为参数（🔧 2026-01-28 增加随机性）
    "slide_behavior": {
        "approach_offset_x": (-30, -15),       # 🔧 扩大范围（原-25到-20）
        "approach_offset_y": (8, 22),          # 🔧 扩大范围（原12到18）
        "approach_steps": (6, 12),             # 🔧 扩大范围（原8-10）
        "approach_pause": (0.03, 0.18),        # 🔧 扩大范围
        "precision_steps": (6, 12),            # 🔧 扩大范围（原8-10）
        "precision_pause": (0.05, 0.15),       # 🔧 扩大范围
        "skip_hover_rate": 0.25,               # 🔧 增加跳过率，增加随机性
        "pre_down_pause": (0.08, 0.20),        # 🔧 扩大范围
        "post_down_pause": (0.08, 0.20),       # 🔧 扩大范围
        "pre_up_pause": (0.02, 0.08),          # 🔧 扩大范围
        "post_up_pause": (0.01, 0.06),         # 🔧 扩大范围
    },
    # 时间控制
    "timing": {
        "total_elapsed_time": (0.8, 2.0),      # 🔧 扩大耗时范围（原0.9-1.55）
        "page_wait": (0.05, 0.30),             # 🔧 扩大等待范围
    },
    # 重试策略 - 🔧 2026-01-28 增加冷却时间
    "retry": {
        "perturbation_factor_increment": 0.12, # 🔧 增大扰动递增（原0.08）
        "base_retry_delay": 1.5,               # 🔧 增加基础延迟（原0.4）- 给服务器冷却时间
        "retry_delay_increment": 1.0,          # 🔧 增加延迟递增（原0.2）
    }
}

# ============================================================================
# 🎰 机器学习策略配置（探索-利用平衡）
# 🔧 2026-01-28 更新：扩大参数范围，增加随机性，降低被检测概率
# ============================================================================
ML_STRATEGY_CONFIG = {
    # 🔧 2026-01-28：降低探索率，更多使用已验证有效的参数
    "exploration_rate": 0.06,  # 进一步降低探索率，优先复用已验证有效的参数

    # 连续失败后切换慢速兜底的阈值基线
    "force_explore_after_failures": 2,  # 第3次尝试会进入慢速兜底

    # 多策略模式配置 - 🔧 2026-01-28 扩大所有参数范围
    "strategies": {
        # 保守策略：较小超调，模拟谨慎用户
        "conservative": {
            "overshoot_ratio": (1.01, 1.06),   # 1-6%超调
            "steps": (28, 40),                  # 🔧 增加步数，更自然
            "base_delay": (0.010, 0.020),      # 🔧 增加延迟（10-20ms）
            "acceleration_curve": (1.8, 2.4),  # 更平滑的ease-out
            "y_jitter_max": (0.8, 2.0),        # 较小Y抖动
            "weight": 0.08,                    # 🔧 从0.18降到0.08，历史成功率仅12%
        },
        # 标准策略：中等超调，模拟普通用户
        "standard": {
            "overshoot_ratio": (1.03, 1.10),   # 3-10%超调
            "steps": (22, 35),                  # 🔧 增加步数范围
            "base_delay": (0.006, 0.015),      # 6-15ms延迟
            "acceleration_curve": (1.5, 2.1),
            "y_jitter_max": (1.2, 2.8),
            "weight": 0.57,                    # 🔧 从0.47提高到0.57，吸收conservative释放的权重
        },
        # 激进策略：较大超调，模拟快速用户
        "aggressive": {
            "overshoot_ratio": (1.06, 1.15),   # 6-15%超调
            "steps": (18, 30),
            "base_delay": (0.004, 0.012),      # 4-12ms延迟
            "acceleration_curve": (1.3, 1.9),  # 更陡的加速曲线
            "y_jitter_max": (1.5, 3.2),
            "weight": 0.35,
        },
    },

    # 参数抖动范围 - 🔧 增加抖动幅度
    "param_jitter": {
        "overshoot_ratio_jitter": 0.05,  # 🔧 从±3%增加到±5%
        "delay_jitter": 0.20,             # 🔧 从±12%增加到±20%
        "curve_jitter": 0.12,             # 🔧 从±8%增加到±12%
    },

    # 学习参数边界 - 🔧 扩大边界
    "learning_bounds": {
        "max_overshoot_ratio": 1.18,      # 🔧 从1.15增加到1.18
        "min_overshoot_ratio": 1.01,
        "max_y_jitter": 3.5,              # 🔧 从3.0增加到3.5
        "min_y_jitter": 0.8,              # 🔧 从1.0降到0.8
        "max_acceleration_curve": 2.6,    # 🔧 从2.5增加到2.6
        "min_acceleration_curve": 1.2,    # 🔧 从1.3降到1.2
    },

    # 🔄 自动权重调整配置
    "auto_weight_adjustment": {
        "enabled": True,
        "min_samples": 3,                  # 🔧 从5降到3，更快开始调整
        "smoothing_factor": 0.4,           # 🔧 从0.3增加到0.4，更快响应
        "min_weight": 0.05,                # 🔧 从0.15降到0.05，允许低效策略被进一步压低
        "max_weight": 0.55,                # 🔧 从0.60降到0.55
    },

    # 🧹 自动数据清理配置
    "auto_data_cleanup": {
        "enabled": True,
        "min_success_rate": 0.20,          # 🔧 从0.15增加到0.20
        "check_window": 15,                # 🔧 从20降到15，更快响应
        "cleanup_threshold": 0.12,         # 🔧 从0.10增加到0.12
        "max_history_age_days": 5,         # 🔧 从7天降到5天，更新更快
    }
}


# ============================================================================
# 🤖 自适应策略管理器（自动调整权重+自动清理数据）
# ============================================================================
class AdaptiveStrategyManager:
    """自适应策略管理器 - 基于多臂老虎机算法动态调整策略权重"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.stats_lock = threading.Lock()
            # 策略统计：{strategy_name: {"success": count, "fail": count, "total": count}}
            self.strategy_stats = {
                "conservative": {"success": 0, "fail": 0, "total": 0},
                "standard": {"success": 0, "fail": 0, "total": 0},
                "aggressive": {"success": 0, "fail": 0, "total": 0},
                "learned_with_jitter": {"success": 0, "fail": 0, "total": 0},
            }
            # 动态权重（与 ML_STRATEGY_CONFIG 初始权重一致）
            self.dynamic_weights = {
                "conservative": 0.08,
                "standard": 0.57,
                "aggressive": 0.35,
            }
            # 统计文件路径
            self.stats_file = "trajectory_history/adaptive_strategy_stats.json"
            # 加载历史统计
            self._load_stats()
            self._initialized = True
            logger.info("🤖 自适应策略管理器初始化完成")
    
    # 已废弃的策略名称，加载时自动清理
    _DEPRECATED_STRATEGIES = {"slow_fallback"}

    def _load_stats(self):
        """加载历史统计数据"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.strategy_stats.update(data.get("strategy_stats", {}))
                    self.dynamic_weights.update(data.get("dynamic_weights", {}))
                # 清理已废弃策略的残留数据
                cleaned = False
                for dep in self._DEPRECATED_STRATEGIES:
                    if dep in self.strategy_stats:
                        del self.strategy_stats[dep]
                        cleaned = True
                    if dep in self.dynamic_weights:
                        del self.dynamic_weights[dep]
                        cleaned = True
                if cleaned:
                    logger.info(f"🤖 已清理废弃策略统计: {self._DEPRECATED_STRATEGIES}")
                    self._save_stats()
                logger.info(f"🤖 加载历史策略统计: {self.stats_file}")
        except Exception as e:
            logger.warning(f"🤖 加载策略统计失败: {e}")
    
    def _save_stats(self):
        """保存统计数据"""
        try:
            os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "strategy_stats": self.strategy_stats,
                    "dynamic_weights": self.dynamic_weights,
                    "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"🤖 保存策略统计失败: {e}")
    
    def record_result(self, strategy_name: str, success: bool):
        """记录策略使用结果
        
        Args:
            strategy_name: 策略名称 (conservative/standard/aggressive/learned_with_jitter)
            success: 是否成功
        """
        with self.stats_lock:
            if strategy_name not in self.strategy_stats:
                self.strategy_stats[strategy_name] = {"success": 0, "fail": 0, "total": 0}
            
            stats = self.strategy_stats[strategy_name]
            stats["total"] += 1
            if success:
                stats["success"] += 1
            else:
                stats["fail"] += 1
            
            # 计算成功率
            success_rate = stats["success"] / stats["total"] if stats["total"] > 0 else 0
            
            logger.info(f"🤖 策略[{strategy_name}]记录: {'✅成功' if success else '❌失败'} "
                       f"(成功率: {success_rate*100:.1f}%, 总计: {stats['total']}次)")
            
            # 自动调整权重
            self._auto_adjust_weights()
            
            # 保存统计
            self._save_stats()
    
    def _auto_adjust_weights(self):
        """自动调整策略权重（基于成功率）"""
        config = ML_STRATEGY_CONFIG.get("auto_weight_adjustment", {})
        if not config.get("enabled", True):
            return
        
        min_samples = config.get("min_samples", 5)
        smoothing = config.get("smoothing_factor", 0.3)
        min_weight = config.get("min_weight", 0.10)
        max_weight = config.get("max_weight", 0.60)
        
        # 只调整三个主要策略的权重
        main_strategies = ["conservative", "standard", "aggressive"]
        
        # 检查是否有足够的样本
        total_samples = sum(
            self.strategy_stats.get(s, {}).get("total", 0) 
            for s in main_strategies
        )
        
        if total_samples < min_samples * len(main_strategies):
            return  # 样本不足，不调整
        
        # 计算每个策略的成功率
        success_rates = {}
        for strategy in main_strategies:
            stats = self.strategy_stats.get(strategy, {})
            total = stats.get("total", 0)
            success = stats.get("success", 0)
            if total >= min_samples:
                success_rates[strategy] = success / total
            else:
                success_rates[strategy] = 0.33  # 默认成功率
        
        # 计算新权重（基于成功率的softmax）
        total_rate = sum(success_rates.values())
        if total_rate > 0:
            new_weights = {}
            for strategy in main_strategies:
                # 使用指数加权，成功率高的策略权重更高
                raw_weight = success_rates[strategy] / total_rate
                # 应用边界限制
                new_weights[strategy] = max(min_weight, min(max_weight, raw_weight))
            
            # 归一化确保权重和为1
            weight_sum = sum(new_weights.values())
            for strategy in main_strategies:
                new_weights[strategy] /= weight_sum
            
            # 平滑更新（避免剧烈变化）
            for strategy in main_strategies:
                old_weight = self.dynamic_weights.get(strategy, 0.33)
                self.dynamic_weights[strategy] = (
                    old_weight * (1 - smoothing) + new_weights[strategy] * smoothing
                )
            
            logger.info(f"🤖 自动调整权重: "
                       f"保守={self.dynamic_weights['conservative']*100:.1f}%, "
                       f"标准={self.dynamic_weights['standard']*100:.1f}%, "
                       f"激进={self.dynamic_weights['aggressive']*100:.1f}%")
    
    def get_dynamic_weights(self, attempt: int = 1) -> dict:
        """获取动态权重（结合尝试次数调整）
        
        Args:
            attempt: 当前尝试次数
            
        Returns:
            dict: {strategy_name: weight}
        """
        with self.stats_lock:
            # 基础权重
            weights = self.dynamic_weights.copy()

            # 固定给低成功率策略一个更低上限，避免无头链路过度分配到保守分支
            weights["conservative"] = min(0.22, max(0.12, weights.get("conservative", 0.18)))
            weights["standard"] = max(0.40, weights.get("standard", 0.47))
            weights["aggressive"] = max(0.28, weights.get("aggressive", 0.35))

            total = sum(weights.values())
            if total > 0:
                for strategy in list(weights.keys()):
                    weights[strategy] = weights[strategy] / total
            
            # 根据尝试次数微调
            if attempt >= 3:
                # 第3次尝试优先走更果断的轨迹，不再依赖低收益慢速分支
                weights["aggressive"] = min(0.55, weights.get("aggressive", 0.35) + 0.12)
                # 相应减少其他策略
                total_other = weights.get("conservative", 0.18) + weights.get("standard", 0.47)
                if total_other > 0:
                    factor = (1 - weights["aggressive"]) / total_other
                    weights["conservative"] = weights.get("conservative", 0.18) * factor
                    weights["standard"] = weights.get("standard", 0.47) * factor
            
            return weights
    
    def check_and_cleanup_history(self, user_id: str, history_file: str) -> bool:
        """检查并自动清理历史数据
        
        Args:
            user_id: 用户ID
            history_file: 历史文件路径
            
        Returns:
            bool: 是否执行了清理
        """
        config = ML_STRATEGY_CONFIG.get("auto_data_cleanup", {})
        if not config.get("enabled", True):
            return False
        
        min_success_rate = config.get("min_success_rate", 0.15)
        check_window = config.get("check_window", 20)
        cleanup_threshold = config.get("cleanup_threshold", 0.10)
        max_age_days = config.get("max_history_age_days", 7)
        
        try:
            if not os.path.exists(history_file):
                return False
            
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
            
            if len(history) < check_window:
                return False  # 数据不足，不检查
            
            # 检查1：最近N条记录的成功率
            recent_records = history[-check_window:]
            # 注意：历史记录都是成功的，所以这里检查的是整体趋势
            # 我们通过检查记录的时间分布来判断
            
            # 检查2：清理过期数据
            current_time = time.time()
            max_age_seconds = max_age_days * 24 * 3600
            
            # 过滤掉过期的记录
            valid_records = [
                r for r in history 
                if current_time - r.get("timestamp", 0) < max_age_seconds
            ]
            
            if len(valid_records) < len(history):
                # 有过期记录，执行清理
                removed_count = len(history) - len(valid_records)
                logger.warning(f"🧹 【{user_id}】自动清理{removed_count}条过期历史记录"
                              f"（超过{max_age_days}天）")
                
                with open(history_file, 'w', encoding='utf-8') as f:
                    json.dump(valid_records, f, indent=2, ensure_ascii=False)
                
                return True
            
            # 检查3：如果历史记录中的参数明显偏离最优范围，清理部分记录
            bounds = ML_STRATEGY_CONFIG.get("learning_bounds", {})
            max_overshoot = bounds.get("max_overshoot_ratio", 2.12)
            
            # 检查最近记录的超调比例
            recent_overshoots = [
                r.get("overshoot_ratio", 0) 
                for r in recent_records 
                if r.get("overshoot_ratio", 0) > 0
            ]
            
            if recent_overshoots:
                avg_overshoot = sum(recent_overshoots) / len(recent_overshoots)
                if avg_overshoot > max_overshoot:
                    # 超调比例偏高，清理一半的历史记录
                    logger.warning(f"🧹 【{user_id}】检测到历史数据超调比例偏高"
                                  f"（平均{avg_overshoot:.2f}），清理一半历史记录")
                    
                    # 保留较新的一半记录
                    half_count = len(history) // 2
                    new_history = history[half_count:]
                    
                    with open(history_file, 'w', encoding='utf-8') as f:
                        json.dump(new_history, f, indent=2, ensure_ascii=False)
                    
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"🧹 检查历史数据时出错: {e}")
            return False
    
    def get_stats_summary(self) -> str:
        """获取统计摘要"""
        with self.stats_lock:
            lines = ["=" * 60]
            lines.append("🤖 自适应策略统计")
            lines.append("=" * 60)
            
            for strategy, stats in self.strategy_stats.items():
                total = stats.get("total", 0)
                success = stats.get("success", 0)
                rate = success / total * 100 if total > 0 else 0
                weight = self.dynamic_weights.get(strategy, 0) * 100
                lines.append(f"{strategy:25} | 成功率: {rate:5.1f}% | "
                           f"样本: {total:4} | 权重: {weight:5.1f}%")
            
            lines.append("=" * 60)
            return "\n".join(lines)


# 全局自适应策略管理器实例
adaptive_strategy_manager = AdaptiveStrategyManager()

# 使用loguru日志库，与主程序保持一致

# 全局并发控制
class SliderConcurrencyManager:
    """滑块验证并发管理器"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.max_concurrent = SLIDER_MAX_CONCURRENT  # 从配置文件读取最大并发数
            self.wait_timeout = SLIDER_WAIT_TIMEOUT  # 从配置文件读取等待超时时间
            self.active_instances = {}  # 活跃实例
            self.waiting_queue = []  # 等待队列
            self.instance_lock = threading.Lock()
            self._initialized = True
            logger.info(f"滑块验证并发管理器初始化: 最大并发数={self.max_concurrent}, 等待超时={self.wait_timeout}秒")
    
    def can_start_instance(self, user_id: str) -> bool:
        """检查是否可以启动新实例"""
        with self.instance_lock:
            return self._can_start_locked(user_id)

    def _find_same_account_active_locked(self, user_id: str):
        """查找同账号的活跃实例，避免同账号并发滑块互相踩踏"""
        pure_user_id = self._extract_pure_user_id(user_id)
        for active_user_id in self.active_instances:
            if self._extract_pure_user_id(active_user_id) == pure_user_id:
                return active_user_id
        return None

    def _can_start_locked(self, user_id: str) -> bool:
        """在持锁状态下检查是否允许启动实例"""
        same_account_active = self._find_same_account_active_locked(user_id)
        return len(self.active_instances) < self.max_concurrent and same_account_active is None
    
    def wait_for_slot(self, user_id: str, timeout: int = None) -> bool:
        """等待可用槽位"""
        if timeout is None:
            timeout = self.wait_timeout
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            with self.instance_lock:
                same_account_active = self._find_same_account_active_locked(user_id)
                if len(self.active_instances) < self.max_concurrent and same_account_active is None:
                    return True
            
            # 检查是否在等待队列中
            with self.instance_lock:
                if user_id not in self.waiting_queue:
                    self.waiting_queue.append(user_id)
                    # 提取纯用户ID用于日志显示
                    pure_user_id = self._extract_pure_user_id(user_id)
                    same_account_active = self._find_same_account_active_locked(user_id)
                    if same_account_active:
                        logger.warning(
                            f"【{pure_user_id}】同账号滑块任务正在执行({same_account_active})，进入等待队列，当前队列长度: {len(self.waiting_queue)}"
                        )
                    else:
                        logger.info(f"【{pure_user_id}】进入等待队列，当前队列长度: {len(self.waiting_queue)}")
            
            # 等待1秒后重试
            time.sleep(1)
        
        # 超时后从队列中移除
        with self.instance_lock:
            if user_id in self.waiting_queue:
                self.waiting_queue.remove(user_id)
                # 提取纯用户ID用于日志显示
                pure_user_id = self._extract_pure_user_id(user_id)
                logger.warning(f"【{pure_user_id}】等待超时，从队列中移除")
        
        return False
    
    def register_instance(self, user_id: str, instance):
        """注册实例"""
        with self.instance_lock:
            if not self._can_start_locked(user_id):
                return False
            self.active_instances[user_id] = {
                'instance': instance,
                'start_time': time.time()
            }
            # 从等待队列中移除
            if user_id in self.waiting_queue:
                self.waiting_queue.remove(user_id)
            return True
    
    def unregister_instance(self, user_id: str, instance=None):
        """注销实例；如果提供 instance，则仅在实例归属匹配时释放。"""
        with self.instance_lock:
            active_entry = self.active_instances.get(user_id)
            if not active_entry:
                return False

            if instance is not None and active_entry.get('instance') is not instance:
                pure_user_id = self._extract_pure_user_id(user_id)
                logger.debug(f"【{pure_user_id}】跳过注销实例：当前活跃实例已切换，避免误释放新槽位")
                return False

            del self.active_instances[user_id]
            # 提取纯用户ID用于日志显示
            pure_user_id = self._extract_pure_user_id(user_id)
            logger.info(f"【{pure_user_id}】实例已注销，当前活跃: {len(self.active_instances)}")
            return True
    
    def _extract_pure_user_id(self, user_id: str) -> str:
        """提取纯用户ID（移除时间戳部分）"""
        if '_' in user_id:
            # 检查最后一部分是否为数字（时间戳）
            parts = user_id.split('_')
            if len(parts) >= 2 and parts[-1].isdigit() and len(parts[-1]) >= 10:
                # 最后一部分是时间戳，移除它
                return '_'.join(parts[:-1])
            else:
                # 不是时间戳格式，使用原始ID
                return user_id
        else:
            # 没有下划线，直接使用
            return user_id
    
    def get_stats(self):
        """获取统计信息"""
        with self.instance_lock:
            return {
                'active_count': len(self.active_instances),
                'max_concurrent': self.max_concurrent,
                'available_slots': self.max_concurrent - len(self.active_instances),
                'queue_length': len(self.waiting_queue),
                'waiting_users': self.waiting_queue.copy()
            }

# 全局并发管理器实例
concurrency_manager = SliderConcurrencyManager()

# 策略统计管理器
class RetryStrategyStats:
    """重试策略成功率统计管理器"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.stats_lock = threading.Lock()
            self.strategy_stats = {
                'attempt_1_default': {'total': 0, 'success': 0, 'fail': 0},
                'attempt_2_cautious': {'total': 0, 'success': 0, 'fail': 0},
                'attempt_3_fast': {'total': 0, 'success': 0, 'fail': 0},
                'attempt_3_slow': {'total': 0, 'success': 0, 'fail': 0},
            }
            self.stats_file = 'trajectory_history/strategy_stats.json'
            self._load_stats()
            self._initialized = True
            logger.info("策略统计管理器初始化完成")
    
    def _load_stats(self):
        """从文件加载统计数据"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    loaded_stats = json.load(f)
                    self.strategy_stats.update(loaded_stats)
                logger.info(f"已加载历史策略统计数据: {self.stats_file}")
        except Exception as e:
            logger.warning(f"加载策略统计数据失败: {e}")
    
    def _save_stats(self):
        """保存统计数据到文件"""
        try:
            os.makedirs(os.path.dirname(self.stats_file), exist_ok=True)
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(self.strategy_stats, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存策略统计数据失败: {e}")
    
    def record_attempt(self, attempt: int, strategy_type: str, success: bool):
        """记录一次尝试结果
        
        Args:
            attempt: 尝试次数 (1, 2, 3)
            strategy_type: 策略类型 ('default', 'cautious', 'fast', 'slow')
            success: 是否成功
        """
        with self.stats_lock:
            key = f'attempt_{attempt}_{strategy_type}'
            if key not in self.strategy_stats:
                self.strategy_stats[key] = {'total': 0, 'success': 0, 'fail': 0}
            
            self.strategy_stats[key]['total'] += 1
            if success:
                self.strategy_stats[key]['success'] += 1
            else:
                self.strategy_stats[key]['fail'] += 1
            
            # 每次记录后保存
            self._save_stats()
    
    def get_stats_summary(self):
        """获取统计摘要"""
        with self.stats_lock:
            summary = {}
            for key, stats in self.strategy_stats.items():
                if stats['total'] > 0:
                    success_rate = (stats['success'] / stats['total']) * 100
                    summary[key] = {
                        'total': stats['total'],
                        'success': stats['success'],
                        'fail': stats['fail'],
                        'success_rate': f"{success_rate:.2f}%"
                    }
            return summary
    
    def log_summary(self):
        """输出统计摘要到日志"""
        summary = self.get_stats_summary()
        if summary:
            logger.info("=" * 60)
            logger.info("📊 重试策略成功率统计")
            logger.info("=" * 60)
            for key, stats in summary.items():
                logger.info(f"{key:25s} | 总计:{stats['total']:4d} | 成功:{stats['success']:4d} | 失败:{stats['fail']:4d} | 成功率:{stats['success_rate']}")
            logger.info("=" * 60)

# 全局策略统计实例
strategy_stats = RetryStrategyStats()

class XianyuSliderStealth:
    _verification_notification_lock = threading.Lock()
    _verification_notification_cache: Dict[Tuple[str, str, str], float] = {}
    _verification_notification_dedup_seconds = 180
    
    def __init__(self, user_id: str = "default", enable_learning: bool = True, headless: bool = True,
                 initial_cookies: Optional[str] = None, proxy: Optional[Dict[str, Any]] = None,
                 browser_channel: Optional[str] = None, executable_path: Optional[str] = None,
                 slider_max_retries: int = 3, use_account_persistent_profile: bool = False,
                 account_persistent_profile_dir: Optional[str] = None):
        self.user_id = user_id
        self.enable_learning = enable_learning
        self.headless = headless  # 是否使用无头模式
        self.initial_cookies = str(initial_cookies or "").replace("\ufeff", "").strip()
        self.proxy_config = dict(proxy or {})
        self.browser_channel = browser_channel or os.environ.get("XY_SLIDER_BROWSER_CHANNEL", "").strip() or None
        self.executable_path = executable_path or os.environ.get("XY_SLIDER_BROWSER_PATH", "").strip() or None
        self.slider_max_retries = max(1, min(int(slider_max_retries or 3), 4))
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.playwright_browser_name = os.environ.get("XY_SLIDER_PLAYWRIGHT_BROWSER", "chromium").strip() or "chromium"
        existing_playwright_cache_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
        is_docker_env = os.environ.get("DOCKER_ENV", "").strip().lower() in {"1", "true", "yes", "on"}
        self.is_docker_env = is_docker_env
        if existing_playwright_cache_dir and existing_playwright_cache_dir != "0":
            self.playwright_browser_cache_dir = existing_playwright_cache_dir
        elif is_docker_env and os.path.isdir("/ms-playwright"):
            self.playwright_browser_cache_dir = "/ms-playwright"
        else:
            self.playwright_browser_cache_dir = os.path.join(self.project_root, ".playwright-browsers")

        default_download_proxy = "" if is_docker_env else "http://127.0.0.1:1081"
        self.playwright_download_proxy = (
            os.environ.get("XY_SLIDER_DOWNLOAD_PROXY", "").strip() or
            os.environ.get("XY_DOWNLOAD_PROXY", "").strip() or
            default_download_proxy
        )
        verification_wait_timeout_text = os.environ.get("XY_VERIFICATION_WAIT_TIMEOUT", "").strip()
        try:
            self.verification_wait_timeout = max(5, int(verification_wait_timeout_text)) if verification_wait_timeout_text else 450
        except ValueError:
            self.verification_wait_timeout = 450
        self.keep_verification_screenshots = (
            os.environ.get("XY_KEEP_VERIFICATION_SCREENSHOT", "").strip().lower() in {"1", "true", "yes", "on"}
        )
        self.disable_headless_warmup = (
            os.environ.get("XY_SLIDER_HEADLESS_WARMUP", "").strip().lower() not in {"1", "true", "yes", "on"}
        )
        backend_env = os.environ.get("XY_SLIDER_AUTOMATION_BACKEND", "").strip().lower()
        if backend_env in {"patchright", "playwright"}:
            self.automation_backend = backend_env
        else:
            self.automation_backend = "playwright"
        self.stealth_mode_override = os.environ.get("XY_SLIDER_STEALTH_MODE", "").strip().lower()
        self.active_stealth_mode = "auto"
        self.browser = None
        self.page = None
        self.context = None
        self.local_browser_info = {}
        try:
            self.browser_cookie_warmup_probe_timeout_ms = max(
                1000,
                int(os.environ.get("XY_BROWSER_COOKIE_WARMUP_TIMEOUT_MS", "5000") or 5000),
            )
        except Exception:
            self.browser_cookie_warmup_probe_timeout_ms = 5000
        if not self.browser_channel and not self.executable_path:
            detected_browser = self._detect_local_browser_info()
            if detected_browser:
                self.local_browser_info = dict(detected_browser)
                detected_path = str(detected_browser.get("path") or "").strip()
                detected_channel = str(detected_browser.get("channel") or "").strip()
                if os.name == 'nt' and detected_channel:
                    self.browser_channel = detected_channel
                elif detected_path:
                    self.executable_path = detected_path
                elif detected_channel:
                    self.browser_channel = detected_channel
        self.playwright = None
        self._playwright_thread_id: Optional[int] = None
        self._browser_pid: Optional[int] = None
        # 内层 _detect_qr_code_verification 滑块自救成功后的兜底回流标记，由 run() 入口重置
        self._post_recovery_success: bool = False
        self._post_recovery_cookies = None
        self._concurrency_slot_registered = False
        
        # 提取纯用户ID（移除时间戳部分）
        self.pure_user_id = concurrency_manager._extract_pure_user_id(user_id)
        
        # 检查日期限制
        if not self._check_date_validity():
            raise Exception(f"【{self.pure_user_id}】日期验证失败，功能已过期")
        
        # 为每个实例创建独立的临时目录
        self.temp_dir = tempfile.mkdtemp(prefix=f"slider_{user_id}_")
        logger.debug(f"【{self.pure_user_id}】创建临时目录: {self.temp_dir}")
        
        # 等待可用槽位（排队机制）
        logger.info(f"【{self.pure_user_id}】检查并发限制...")
        if not concurrency_manager.wait_for_slot(self.user_id):
            stats = concurrency_manager.get_stats()
            logger.error(f"【{self.pure_user_id}】等待槽位超时，当前活跃: {stats['active_count']}/{stats['max_concurrent']}")
            raise Exception(f"滑块验证等待槽位超时，请稍后重试")
        
        # 注册实例
        if not concurrency_manager.register_instance(self.user_id, self):
            raise Exception(f"【{self.pure_user_id}】同账号已有滑块任务正在执行，请稍后重试")
        self._concurrency_slot_registered = True
        stats = concurrency_manager.get_stats()
        logger.info(f"【{self.pure_user_id}】实例已注册，当前并发: {stats['active_count']}/{stats['max_concurrent']}")
        
        # 轨迹学习相关属性
        
        self.success_history_file = f"trajectory_history/{self.pure_user_id}_success.json"
        self.failure_history_file = f"trajectory_history/{self.pure_user_id}_failure.json"
        self.browser_profile_file = f"trajectory_history/{self.pure_user_id}_browser_profile.json"
        self.last_verification_feedback = {}
        self.last_login_error = ""
        self.last_browser_cookie_warmup_verification_hint = None
        self.last_browser_cookie_warmup_session_unready = False
        self._slider_refresh_mode = False
        self.risk_session_id = None
        self.risk_trigger_scene = None
        self._password_slider_runtime_hardened = False
        self.browser_features = {}
        self.browser_identity = {}
        self.profile_id = "unassigned"
        self.use_account_persistent_profile = bool(use_account_persistent_profile)
        self.account_persistent_profile_dir = str(account_persistent_profile_dir or "").strip() or None
        self.trajectory_params = {
            "total_steps_range": [5, 8],  # 极速：5-8步（超快滑动）
            "base_delay_range": [0.0002, 0.0005],  # 极速：0.2-0.5ms延迟
            "jitter_x_range": [0, 1],  # 极小抖动
            "jitter_y_range": [0, 1],  # 极小抖动
            "slow_factor_range": [10, 15],  # 极快加速因子
            "acceleration_phase": 1.0,  # 全程加速
            "fast_phase": 1.0,  # 无慢速
            "slow_start_ratio_base": 2.0,  # 确保超调100%
            "completion_usage_rate": 0.05,  # 极少补全使用率
            "avg_completion_steps": 1.0,  # 极少补全步数
            "trajectory_length_stats": [],
            "learning_enabled": False
        }
        
        # 保存最后一次使用的轨迹参数（用于分析优化）
        self.last_trajectory_params = {}

        self.local_browser_info = {}
        if self.executable_path:
            version_text = self._read_local_browser_version(self.executable_path)
            self.local_browser_info = {
                "path": self.executable_path,
                "version": version_text,
                "major_version": (version_text.split(".", 1)[0] if version_text else ""),
                "family": self._get_browser_family(),
            }

    def _fail_login(self, message: str):
        self.last_login_error = message
        return None

    def _build_risk_event_meta(self, verification_url: str = None, extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        trigger_scene = getattr(self, 'risk_trigger_scene', None)
        if trigger_scene:
            payload['trigger_scene'] = trigger_scene

        text = str(verification_url or '').strip()
        if text:
            try:
                parsed = urlparse(text)
                if parsed.scheme or parsed.netloc:
                    if parsed.netloc:
                        payload['verification_host'] = parsed.netloc
                    if parsed.path:
                        payload['verification_path'] = parsed.path
                    query = parse_qs(parsed.query or '')
                    x5secdata = query.get('x5secdata', [None])[0]
                    if x5secdata:
                        payload['verification_token_hash'] = hashlib.sha256(x5secdata.encode('utf-8')).hexdigest()[:16]
                    action = query.get('action', [None])[0]
                    if action:
                        payload['verification_action'] = action
                else:
                    payload['verification_source'] = text[:120]
            except Exception:
                payload['verification_source'] = text[:120]

        if isinstance(extra, dict):
            payload.update({key: value for key, value in extra.items() if value is not None})
        return payload or None

    def _resolve_slider_risk_context(self) -> Tuple[str, str]:
        trigger_scene = getattr(self, 'risk_trigger_scene', None)
        if not trigger_scene:
            trigger_scene = 'manual_password_refresh' if getattr(self, '_slider_refresh_mode', False) else 'password_login'

        if trigger_scene == 'manual_password_refresh':
            flow_label = '手动刷新Cookie'
        elif trigger_scene == 'password_login':
            flow_label = '账号密码登录'
        elif trigger_scene == 'auto_cookie_refresh':
            flow_label = '自动Cookie刷新'
        else:
            flow_label = '密码登录流程'

        return trigger_scene, flow_label

    def _start_password_login_slider_risk_log(self, verification_url: str = None,
                                              detection_phase: str = None) -> Optional[Dict[str, Any]]:
        try:
            from db_manager import db_manager

            trigger_scene, flow_label = self._resolve_slider_risk_context()
            event_meta = self._build_risk_event_meta(
                verification_url=verification_url,
                extra={
                    'account_id': self.pure_user_id,
                    'source': 'password_login_flow',
                    'refresh_mode': bool(getattr(self, '_slider_refresh_mode', False)),
                    'detection_phase': detection_phase,
                },
            )
            log_id = db_manager.add_risk_control_log(
                cookie_id=self.pure_user_id,
                event_type='slider_captcha',
                session_id=getattr(self, 'risk_session_id', None),
                trigger_scene=trigger_scene,
                result_code='password_login_slider_detected',
                event_description=f'{flow_label}检测到滑块验证',
                event_meta=event_meta,
                processing_status='processing',
                error_message='检测到滑块验证，正在自动处理',
            )
            if log_id:
                logger.info(f"【{self.pure_user_id}】已记录密码登录滑块风控日志: {log_id}")
                return {
                    'log_id': log_id,
                    'started_at': time.time(),
                    'verification_url': verification_url,
                    'event_meta': event_meta,
                    'trigger_scene': trigger_scene,
                    'flow_label': flow_label,
                }
        except Exception as log_err:
            logger.warning(f"【{self.pure_user_id}】记录密码登录滑块风控日志失败: {log_err}")
        return None

    def _finish_password_login_slider_risk_log(self, slider_risk_log: Optional[Dict[str, Any]], *,
                                               success: bool, verification_url: str = None,
                                               processing_result: str = None, error_message: str = None,
                                               extra_meta: Optional[Dict[str, Any]] = None):
        if not slider_risk_log or not slider_risk_log.get('log_id'):
            return

        try:
            from db_manager import db_manager

            trigger_scene = slider_risk_log.get('trigger_scene') or self._resolve_slider_risk_context()[0]
            flow_label = slider_risk_log.get('flow_label') or self._resolve_slider_risk_context()[1]
            final_verification_url = verification_url or slider_risk_log.get('verification_url')
            merged_event_meta = dict(slider_risk_log.get('event_meta') or {})
            if isinstance(extra_meta, dict):
                merged_event_meta.update({key: value for key, value in extra_meta.items() if value is not None})

            final_event_meta = self._build_risk_event_meta(
                verification_url=final_verification_url,
                extra=merged_event_meta,
            )

            result_code = 'password_login_slider_success' if success else 'password_login_slider_failed'
            if success:
                final_processing_result = processing_result or f'{flow_label}中的滑块验证成功'
                final_error_message = None
                event_description = f'{flow_label}中的滑块验证已自动处理成功'
            else:
                final_processing_result = processing_result or f'{flow_label}中的滑块验证失败'
                final_error_message = error_message or '滑块验证失败，请稍后重试'
                event_description = f'{flow_label}中的滑块验证自动处理失败'

            duration_ms = None
            started_at = slider_risk_log.get('started_at')
            if started_at:
                duration_ms = max(0, int((time.time() - float(started_at)) * 1000))

            db_manager.update_risk_control_log(
                log_id=slider_risk_log['log_id'],
                event_description=event_description,
                processing_result=final_processing_result,
                processing_status='success' if success else 'failed',
                error_message=final_error_message,
                session_id=getattr(self, 'risk_session_id', None),
                trigger_scene=trigger_scene,
                result_code=result_code,
                event_meta=final_event_meta,
                duration_ms=duration_ms,
            )
        except Exception as log_err:
            logger.warning(f"【{self.pure_user_id}】更新密码登录滑块风控日志失败: {log_err}")

    def _get_slider_failure_message(self, default_message: str) -> str:
        feedback = self.last_verification_feedback or {}
        feedback_message = str(feedback.get("message") or "").strip()
        feedback_source = str(feedback.get("source") or "").strip()
        if feedback_source in {"punish_captcha", "feedback_block"} and feedback_message:
            return feedback_message
        if feedback_message:
            return f"滑块验证失败：{feedback_message}"
        return default_message

    def _should_abort_token_refresh_slider_flow_after_failure(self) -> Tuple[bool, str]:
        """识别 token_refresh 场景下的已知硬拒绝，尽快交给外层走账密恢复。"""
        if getattr(self, "risk_trigger_scene", None) != "token_refresh":
            return False, ""

        feedback = self.last_verification_feedback or {}
        fail_code = str(feedback.get("fail_code") or "").strip().lower()
        message_parts = [
            str(feedback.get("message") or "").strip(),
            str(feedback.get("dom_error_text") or "").strip(),
        ]
        message_text = " ".join(part for part in message_parts if part)

        has_retry_failure_message = "验证失败，点击框体重试" in message_text
        has_error_code = bool(fail_code) or ("error:" in message_text.lower())
        if has_retry_failure_message and has_error_code:
            fail_code_note = fail_code or "unknown"
            return True, f"token_refresh 场景命中已知 hard reject({fail_code_note})，提前结束当前滑块流程"

        return False, ""

    def _should_abort_slider_retry_after_failure(self) -> Tuple[bool, str]:
        return self._should_abort_token_refresh_slider_flow_after_failure()

    def _capture_verification_screenshot(self, page, frame=None, iframe_selector: Optional[str] = None) -> Optional[str]:
        """截取验证页面截图，多种方式逐级回退"""
        try:
            import glob

            screenshots_dir = "static/uploads/images"
            os.makedirs(screenshots_dir, exist_ok=True)

            existing_screenshots = glob.glob(
                os.path.join(screenshots_dir, f"face_verify_{self.pure_user_id}_*.jpg")
            )
            existing_screenshots += glob.glob(
                os.path.join(screenshots_dir, f"face_verify_{self.pure_user_id}_*.png")
            )

            detection_text = ""
            try:
                if frame is not None:
                    detection_text = self._read_frame_text_for_detection(frame)
                if not detection_text and page is not None:
                    detection_text = self._collect_page_text_for_detection(page)
            except Exception:
                detection_text = ""

            if self._is_timed_out_verification_text(detection_text) and existing_screenshots:
                latest_existing = max(existing_screenshots, key=os.path.getmtime)
                reusable_path = latest_existing.replace("\\", "/")
                logger.warning(
                    f"【{self.pure_user_id}】当前验证页已进入超时/失效态，"
                    f"保留上一张可用验证截图，不覆盖为超时页: {reusable_path}"
                )
                return reusable_path

            # 等待验证页面渲染（无头模式下 iframe 渲染需要时间）
            time.sleep(1.5)

            screenshot_bytes = None

            # 方式1：通过 frame.frame_element() 截取 iframe 元素
            if frame is not None and screenshot_bytes is None:
                try:
                    frame_element = frame.frame_element()
                    if frame_element:
                        screenshot_bytes = frame_element.screenshot(timeout=5000)
                        logger.info(f"【{self.pure_user_id}】方式1: 截取验证iframe元素成功")
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】方式1失败(frame_element): {e}")

            # 方式2：通过 iframe 选择器截取
            if screenshot_bytes is None and iframe_selector:
                try:
                    iframe_element = page.query_selector(iframe_selector)
                    if iframe_element:
                        screenshot_bytes = iframe_element.screenshot(timeout=5000)
                        logger.info(f"【{self.pure_user_id}】方式2: 按选择器截取iframe成功")
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】方式2失败(selector): {e}")

            # 方式3：通过 alibaba-login-box 选择器（常见的人脸验证 iframe）
            if screenshot_bytes is None:
                try:
                    login_box = page.query_selector('iframe#alibaba-login-box')
                    if login_box:
                        screenshot_bytes = login_box.screenshot(timeout=5000)
                        logger.info(f"【{self.pure_user_id}】方式3: 截取alibaba-login-box成功")
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】方式3失败(alibaba-login-box): {e}")

            # 方式4：截取整个页面可见区域
            if screenshot_bytes is None:
                try:
                    screenshot_bytes = page.screenshot(full_page=False, timeout=10000)
                    logger.info(f"【{self.pure_user_id}】方式4: 截取整页面成功")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】方式4失败(full_page): {e}")

            # 方式5：截取整个页面（含滚动区域）
            if screenshot_bytes is None:
                try:
                    screenshot_bytes = page.screenshot(full_page=True, timeout=10000)
                    logger.info(f"【{self.pure_user_id}】方式5: 截取完整页面成功")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】方式5失败(full_page=True): {e}")

            if screenshot_bytes is None:
                logger.error(f"【{self.pure_user_id}】所有截图方式均失败")
                return None

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"face_verify_{self.pure_user_id}_{timestamp}.jpg"
            file_path = os.path.join(screenshots_dir, filename)

            with open(file_path, 'wb') as f:
                f.write(screenshot_bytes)

            screenshot_path = file_path.replace('\\', '/')
            logger.info(f"【{self.pure_user_id}】✅ 验证截图已保存: {screenshot_path} ({len(screenshot_bytes)} bytes)")
            return screenshot_path
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】截取验证截图时出错: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None
    
    def _check_date_validity(self) -> bool:
        """保留接口兼容，但不再做日期限制。"""
        logger.info(f"【{self.pure_user_id}】日期校验已禁用，直接放行")
        return True

    def _stable_number(self, namespace: str) -> int:
        digest = hashlib.sha256(f"{self.pure_user_id}:{namespace}".encode("utf-8")).hexdigest()
        return int(digest[:12], 16)

    def _load_or_create_browser_identity(self, profile_count: int, language_count: int,
                                         profile_version: int = 2) -> Dict[str, Any]:
        if self.browser_identity:
            return self.browser_identity

        identity = None
        try:
            if os.path.exists(self.browser_profile_file):
                with open(self.browser_profile_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    if int(loaded.get("profile_version", 0)) != int(profile_version):
                        loaded = None
                    if not loaded:
                        raise ValueError("browser profile version changed")
                    profile_index = int(loaded.get("profile_index", -1))
                    language_index = int(loaded.get("language_index", -1))
                    if 0 <= profile_index < profile_count and 0 <= language_index < language_count:
                        identity = loaded
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】加载浏览器画像失败，重新生成: {e}")

        if identity is None:
            identity = {
                "profile_version": int(profile_version),
                "profile_index": self._stable_number("browser_profile") % max(1, profile_count),
                "language_index": self._stable_number("browser_language") % max(1, language_count),
                "color_scheme": ["light", "no-preference"][self._stable_number("color_scheme") % 2],
                "plugin_count": 4 + (self._stable_number("plugin_count") % 3),
                "notification_permission": ["default", "denied"][self._stable_number("notification_permission") % 2],
                "do_not_track": ["0", "1", "unspecified"][self._stable_number("do_not_track") % 3],
                "battery_charging": bool(self._stable_number("battery_charging") % 2),
                "battery_level": round(0.45 + (self._stable_number("battery_level") % 45) / 100, 2),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            try:
                os.makedirs(os.path.dirname(self.browser_profile_file), exist_ok=True)
                with open(self.browser_profile_file, "w", encoding="utf-8") as f:
                    json.dump(identity, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】保存浏览器画像失败: {e}")

        self.browser_identity = identity
        return identity

    def _extract_profile_window_size(self, profile_id: Optional[str]) -> Optional[str]:
        match = re.search(r'_(\d+)x(\d+)$', str(profile_id or '').strip())
        if not match:
            return None
        return f"{match.group(1)},{match.group(2)}"

    def _extract_relaxed_learning_profile_group(self, profile_id: Optional[str]) -> Optional[str]:
        normalized = str(profile_id or "").strip().lower()
        match = re.match(r'^(win_chrome)_(\d+)_(\d+)x(\d+)$', normalized)
        if not match:
            return None
        if match.group(3) != "1600" or match.group(4) != "900":
            return None
        return f"{match.group(1)}_{match.group(3)}x{match.group(4)}"

    def _canonical_learning_profile_id(self, profile_id: Optional[str]) -> str:
        normalized = str(profile_id or "").strip()
        if not normalized:
            return ""
        if self.headless and self._is_password_login_scene() and self._use_headless_stable_profile():
            relaxed_group = self._extract_relaxed_learning_profile_group(normalized)
            if relaxed_group:
                return relaxed_group
        return normalized

    def _is_learning_profile_compatible(self, record_profile_id: Optional[str]) -> bool:
        if not self.profile_id:
            return True
        current_profile = self._canonical_learning_profile_id(self.profile_id)
        target_profile = self._canonical_learning_profile_id(record_profile_id)
        if not target_profile:
            return True
        return current_profile == target_profile

    def _allow_small_sample_learning(self, history: List[Dict[str, Any]],
                                     reference_distance: Optional[float] = None) -> bool:
        if len(history) < 2:
            return False

        profile_ids = set()
        canonical_profile_ids = set()
        distances = []
        for record in history:
            if not isinstance(record, dict) or not record.get("success"):
                return False

            verification_result = record.get("verification_result", {}) or {}
            record_profile_id = str(
                record.get("profile_id")
                or verification_result.get("profile_id")
                or ""
            ).strip()
            if record_profile_id:
                profile_ids.add(record_profile_id)
                canonical_profile_ids.add(self._canonical_learning_profile_id(record_profile_id))

            distance_value = record.get("distance")
            if isinstance(distance_value, (int, float)):
                distances.append(float(distance_value))

        if self.profile_id and profile_ids and any(
            not self._is_learning_profile_compatible(profile_id) for profile_id in profile_ids
        ):
            return False
        if len({profile_id for profile_id in canonical_profile_ids if profile_id}) > 1:
            return False
        if reference_distance is None:
            return False
        if not distances or any(abs(distance - float(reference_distance)) > 12.0 for distance in distances):
            return False

        return True

    def _select_preferred_browser_profile(self, browser_profiles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        stable_profile = next(
            (item for item in browser_profiles if item.get('window_size') == '1600,900'),
            None,
        )

        # 无头滑块已经验证过 1600x900 更稳，别再让画像乱飘。
        if self.headless and stable_profile:
            return stable_profile

        if self.risk_trigger_scene not in {'password_login', 'manual_password_refresh'}:
            return None

        history = self._load_success_history()
        for record in reversed(history):
            if not isinstance(record, dict) or not record.get("success"):
                continue

            verification_result = record.get("verification_result", {}) or {}
            record_profile_id = str(
                record.get("profile_id")
                or verification_result.get("profile_id")
                or ""
            ).strip()
            preferred_window_size = self._extract_profile_window_size(record_profile_id)
            if not preferred_window_size:
                continue

            matched_profile = next(
                (item for item in browser_profiles if item.get('window_size') == preferred_window_size),
                None,
            )
            if matched_profile:
                logger.info(
                    f"【{self.pure_user_id}】密码登录优先复用成功画像: {record_profile_id}"
                )
                return matched_profile

        if stable_profile:
            logger.info(f"【{self.pure_user_id}】密码登录未命中成功画像，回退到 1600x900 稳定画像")
            return stable_profile
        return None

    def _update_current_result_meta(
        self,
        status: str,
        attempt: Optional[int] = None,
        cookie_refresh_confirmed: Optional[bool] = None,
        soft_success: bool = False,
        note: Optional[str] = None,
    ):
        if not hasattr(self, "current_trajectory_data"):
            return

        result = self.current_trajectory_data.setdefault("verification_result", {})
        result.update({
            "status": status,
            "attempt": attempt,
            "soft_success": soft_success,
            "cookie_refresh_confirmed": cookie_refresh_confirmed,
            "feedback": dict(self.last_verification_feedback or {}),
            "profile_id": self.profile_id,
            "headless": self.headless,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        if note:
            result["note"] = note

    def _should_accept_soft_success_without_cookie_refresh(
        self,
        current_cookies: Dict[str, str],
        fallback_page=None,
    ) -> Tuple[bool, str]:
        feedback = self.last_verification_feedback or {}
        feedback_source = str(feedback.get("source") or "")
        accepted_sources = {
            "frame_detached",
            "container_missing",
            "page_changed",
            "login_element_detected",
            "context_login_confirmed",
        }

        monitor_page = fallback_page or self.page
        if self.context:
            monitor_page = self._select_monitor_page(self.context, monitor_page)

        if not monitor_page:
            return False, ""

        try:
            if self._check_login_success_by_element(monitor_page):
                return True, "登录成功元素已出现，接受无 Cookie 变更的软成功"
        except Exception:
            pass

        monitor_url = self._safe_page_url(monitor_page)
        page_has_slider = self._page_has_slider(monitor_page)
        page_looks_verify = self._page_looks_like_verification(monitor_page)

        if feedback_source in accepted_sources and not page_has_slider and not page_looks_verify:
            return True, f"页面已脱离验证态({feedback_source})，接受软成功"

        if self._has_completed_login_cookies(current_cookies) and not page_has_slider:
            if not page_looks_verify or self._is_logged_in_url(monitor_url):
                return True, "关键登录 Cookie 已完整，且页面已脱离滑块态"

        return False, ""

    def _detect_local_browser_info(self) -> Dict[str, Any]:
        if os.name != 'nt':
            return {}

        browser_candidates = [
            {
                "family": "edge",
                "channel": "msedge",
                "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            },
            {
                "family": "edge",
                "channel": "msedge",
                "path": r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            },
            {
                "family": "chrome",
                "channel": "chrome",
                "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            },
            {
                "family": "chrome",
                "channel": "chrome",
                "path": r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            },
        ]

        for candidate in browser_candidates:
            browser_path = candidate["path"]
            if not os.path.exists(browser_path):
                continue

            info = dict(candidate)
            version_text = self._read_local_browser_version(browser_path)
            if version_text:
                info["version"] = version_text
                version_match = re.search(r"(\d+)(?:\.\d+){0,3}", version_text)
                if version_match:
                    info["major_version"] = version_match.group(1)
            return info

        return {}

    def _configure_playwright_browser_env(self, env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        target_env = env if env is not None else os.environ
        target_env["PLAYWRIGHT_BROWSERS_PATH"] = self.playwright_browser_cache_dir
        return target_env

    def _find_project_browser_executable(self, browser_name: Optional[str] = None) -> Optional[str]:
        browser_name = str(browser_name or self.playwright_browser_name or "chromium").strip().lower()
        browser_root = self.playwright_browser_cache_dir
        if not os.path.isdir(browser_root):
            return None

        if sys.platform.startswith("win"):
            search_rules = {
                "chromium": [
                    ("chromium-*", os.path.join("chrome-win64", "chrome.exe")),
                    ("chromium-*", os.path.join("chrome-win", "chrome.exe")),
                ],
                "chrome": [
                    ("chrome-*", os.path.join("chrome-win64", "chrome.exe")),
                    ("chrome-*", os.path.join("chrome-win", "chrome.exe")),
                ],
                "msedge": [("msedge-*", os.path.join("msedge-win", "msedge.exe"))],
                "firefox": [("firefox-*", os.path.join("firefox", "firefox.exe"))],
                "webkit": [("webkit-*", os.path.join("Playwright.exe"))],
            }
        elif sys.platform.startswith("linux"):
            search_rules = {
                "chromium": [
                    ("chromium-*", os.path.join("chrome-linux", "chrome")),
                    ("chromium-*", os.path.join("chrome-linux", "headless_shell")),
                ],
                "chrome": [
                    ("chrome-*", os.path.join("chrome-linux", "chrome")),
                ],
                "msedge": [("msedge-*", os.path.join("msedge-linux", "msedge"))],
                "firefox": [("firefox-*", os.path.join("firefox", "firefox"))],
                "webkit": [("webkit-*", os.path.join("pw_run.sh"))],
            }
        else:
            search_rules = {
                "chromium": [("chromium-*", os.path.join("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"))],
                "chrome": [("chrome-*", os.path.join("chrome-mac", "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing"))],
                "msedge": [("msedge-*", os.path.join("msedge-mac", "Microsoft Edge.app", "Contents", "MacOS", "Microsoft Edge"))],
                "firefox": [("firefox-*", os.path.join("firefox", "Nightly.app", "Contents", "MacOS", "firefox"))],
                "webkit": [("webkit-*", os.path.join("pw_run.sh"))],
            }

        for folder_pattern, relative_binary in search_rules.get(browser_name, []):
            for folder_name in sorted(os.listdir(browser_root), reverse=True):
                if not re.fullmatch(folder_pattern.replace("*", ".*"), folder_name):
                    continue
                candidate = os.path.join(browser_root, folder_name, relative_binary)
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                    return candidate
        return None

    def _apply_project_browser_runtime_info(self, executable_path: str, browser_name: Optional[str] = None) -> Optional[str]:
        browser_name = str(browser_name or self.playwright_browser_name or "chromium").strip().lower()
        version_text = self._read_local_browser_version(executable_path)
        browser_family = "edge" if browser_name == "msedge" else "chrome"
        self.executable_path = executable_path
        self.browser_channel = None
        self.local_browser_info = {
            "path": executable_path,
            "version": version_text,
            "major_version": (version_text.split(".", 1)[0] if version_text else ""),
            "family": browser_family,
            "source": "project_playwright_cache",
        }
        return version_text

    def _summarize_subprocess_output(self, text: str, limit: int = 600) -> str:
        cleaned = (text or "").strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[-limit:]

    def _ensure_project_playwright_browser(self) -> Optional[str]:
        browser_name = str(self.playwright_browser_name or "chromium").strip().lower()
        self._configure_playwright_browser_env()
        os.makedirs(self.playwright_browser_cache_dir, exist_ok=True)

        existing_executable = self._find_project_browser_executable(browser_name)
        if existing_executable:
            self._apply_project_browser_runtime_info(existing_executable, browser_name)
            logger.info(f"【{self.pure_user_id}】复用项目内 Playwright 浏览器: {existing_executable}")
            return existing_executable

        with _PLAYWRIGHT_BROWSER_INSTALL_LOCK:
            existing_executable = self._find_project_browser_executable(browser_name)
            if existing_executable:
                self._apply_project_browser_runtime_info(existing_executable, browser_name)
                logger.info(f"【{self.pure_user_id}】复用已下载的 Playwright 浏览器: {existing_executable}")
                return existing_executable

            try:
                has_cached_entries = any(os.scandir(self.playwright_browser_cache_dir))
            except Exception:
                has_cached_entries = False
            if has_cached_entries:
                logger.warning(
                    f"【{self.pure_user_id}】Playwright 浏览器目录已存在但未直接解析到可执行文件，"
                    f"保留 Playwright 默认查找逻辑: {self.playwright_browser_cache_dir}"
                )
                return None

            install_env = self._configure_playwright_browser_env(os.environ.copy())
            proxy_url = str(self.playwright_download_proxy or "").strip()
            if proxy_url:
                install_env.setdefault("HTTP_PROXY", proxy_url)
                install_env.setdefault("HTTPS_PROXY", proxy_url)
                install_env.setdefault("ALL_PROXY", proxy_url)

            install_cmd = [sys.executable, "-m", "playwright", "install", browser_name]
            logger.info(
                f"【{self.pure_user_id}】项目内未发现 Playwright 浏览器，开始自动下载: "
                f"{browser_name}, cache={self.playwright_browser_cache_dir}, proxy={proxy_url or 'none'}"
            )
            install_result = subprocess.run(
                install_cmd,
                env=install_env,
                timeout=900,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if install_result.returncode != 0:
                stdout_text = self._summarize_subprocess_output(install_result.stdout)
                stderr_text = self._summarize_subprocess_output(install_result.stderr)
                logger.error(f"【{self.pure_user_id}】Playwright 浏览器自动下载失败，stdout: {stdout_text}")
                logger.error(f"【{self.pure_user_id}】Playwright 浏览器自动下载失败，stderr: {stderr_text}")
                raise RuntimeError(f"Playwright 浏览器自动下载失败: {browser_name}")

            existing_executable = self._find_project_browser_executable(browser_name)
            if not existing_executable:
                raise RuntimeError(f"Playwright 浏览器下载完成但未找到可执行文件: {browser_name}")

            self._apply_project_browser_runtime_info(existing_executable, browser_name)
            logger.info(f"【{self.pure_user_id}】Playwright 浏览器下载完成: {existing_executable}")
            return existing_executable

    def _read_local_browser_version(self, browser_path: str) -> Optional[str]:
        if not browser_path or not os.path.exists(browser_path):
            return None

        if os.name == 'nt':
            try:
                output = subprocess.check_output(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"(Get-Item -LiteralPath '{browser_path}').VersionInfo.ProductVersion",
                    ],
                    timeout=3,
                    encoding="utf-8",
                    errors="ignore",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                ).strip()
                version_match = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
                version_text = version_match.group(1) if version_match else ""
                if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version_text):
                    return version_text
            except Exception:
                pass

            try:
                escaped_browser_path = browser_path.replace("\\", "\\\\")
                output = subprocess.check_output(
                    [
                        "cmd",
                        "/c",
                        "wmic",
                        "datafile",
                        "where",
                        f"name='{escaped_browser_path}'",
                        "get",
                        "Version",
                        "/value",
                    ],
                    timeout=3,
                    encoding="utf-8",
                    errors="ignore",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                ).strip()
                version_match = re.search(r"Version=(\d+\.\d+\.\d+\.\d+)", output)
                if version_match:
                    return version_match.group(1)
            except Exception:
                pass

        try:
            output = subprocess.check_output(
                [browser_path, "--version"],
                timeout=3,
                encoding="utf-8",
                errors="ignore",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).strip()
            match = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
            return match.group(1) if match else None
        except Exception:
            return None

    def _get_browser_family(self) -> str:
        local_browser_info = getattr(self, "local_browser_info", None) or {}
        if local_browser_info.get("family") in {"edge", "chrome"}:
            return str(local_browser_info.get("family"))
        if self.browser_channel == "msedge":
            return "edge"
        if self.browser_channel == "chrome":
            return "chrome"
        path_text = str(self.executable_path or "").lower()
        if "msedge" in path_text:
            return "edge"
        return "chrome"

    def _get_sync_playwright_factory(self):
        if self.automation_backend == "patchright" and patchright_sync_playwright is not None:
            return patchright_sync_playwright
        return playwright_sync_playwright

    def _resolve_stealth_mode(self) -> str:
        override = str(self.stealth_mode_override or "").strip().lower()
        if override in {"off", "lite", "full"}:
            return override

        # Patchright 的 init_script 是通过路由注入的，额外脚本越多越容易把自己暴露出去。
        if self.headless and self.automation_backend == "patchright":
            return "off"

        return "full"

    def _use_headless_stable_profile(self) -> bool:
        return bool(
            self.headless
            and str(self.profile_id or "").startswith("win_chrome_147_1600x900")
        )

    def _should_prefer_docker_conservative_profile(self, has_learning: bool) -> bool:
        if has_learning:
            return False
        if not (self.headless and self.is_docker_env and self.automation_backend == "playwright"):
            return False
        if not self._use_headless_stable_profile():
            return False
        local_browser_info = getattr(self, "local_browser_info", None) or {}
        return bool(
            str(local_browser_info.get("source") or "") == "project_playwright_cache"
            or bool(local_browser_info.get("version"))
            or bool(self.executable_path)
        )

    def _should_force_docker_cold_start_conservative(self, attempt: int, has_learning: bool) -> bool:
        return attempt == 1 and self._should_prefer_docker_conservative_profile(has_learning)

    def _get_light_stealth_script(self, browser_features: Dict[str, Any]) -> str:
        locale = json.dumps(browser_features.get("locale") or "zh-CN", ensure_ascii=False)
        platform = json.dumps(browser_features.get("platform") or "Win32", ensure_ascii=False)
        vendor = json.dumps(browser_features.get("vendor") or "Google Inc.", ensure_ascii=False)
        user_agent = json.dumps(browser_features.get("user_agent") or "", ensure_ascii=False)

        return f"""
            (() => {{
                const defineGetter = (target, key, getter) => {{
                    try {{
                        Object.defineProperty(target, key, {{
                            get: getter,
                            configurable: true
                        }});
                    }} catch (e) {{}}
                }};

                const languages = [{locale}, 'zh', 'en'];
                defineGetter(Navigator.prototype, 'languages', () => languages);
                defineGetter(Navigator.prototype, 'platform', () => {platform});
                defineGetter(Navigator.prototype, 'vendor', () => {vendor});
                defineGetter(Navigator.prototype, 'userAgent', () => {user_agent});

                if (!window.chrome) {{
                    window.chrome = {{}};
                }}
                window.chrome.runtime = window.chrome.runtime || {{}};
            }})();
        """

    def _install_stealth_init_script(self, page, browser_features: Dict[str, Any], mode_override: Optional[str] = None):
        mode = str(mode_override or "").strip().lower() or self._resolve_stealth_mode()
        self.active_stealth_mode = mode

        if mode == "off":
            logger.info(
                f"【{self.pure_user_id}】跳过自定义 init_script：backend={self.automation_backend}, "
                f"headless={self.headless}, mode={mode}"
            )
            return

        script = self._get_stealth_script(browser_features)
        if mode == "lite":
            script = self._get_light_stealth_script(browser_features)

        page.add_init_script(script)
        logger.info(
            f"【{self.pure_user_id}】已注入 {mode} 级别反检测脚本："
            f"backend={self.automation_backend}, headless={self.headless}"
        )

    def _collect_runtime_debug_info(self, search_target=None) -> Dict[str, Any]:
        runtime_targets = []
        if search_target is not None:
            runtime_targets.append(("target", search_target))
        if self.page is not None and self.page is not search_target:
            runtime_targets.append(("page", self.page))

        if not runtime_targets:
            return {}

        script = """
            () => {
                const pickText = (selector) => {
                    const node = document.querySelector(selector);
                    if (!node) {
                        return '';
                    }
                    return (node.innerText || node.textContent || '').trim();
                };

                const brands = navigator.userAgentData && Array.isArray(navigator.userAgentData.brands)
                    ? navigator.userAgentData.brands
                    : [];

                return {
                    href: location.href,
                    title: document.title,
                    readyState: document.readyState,
                    userAgent: navigator.userAgent,
                    webdriver: navigator.webdriver,
                    languages: Array.from(navigator.languages || []),
                    platform: navigator.platform,
                    vendor: navigator.vendor,
                    brands,
                    hasNocaptcha: !!document.querySelector('#nocaptcha'),
                    hasSliderButton: !!document.querySelector('#nc_1_n1z'),
                    hasSliderTrack: !!document.querySelector('#nc_1_n1t'),
                    errorText: pickText('.errloading')
                        || pickText('.sm-btn-fail')
                        || pickText('.captcha-tips')
                        || pickText('#nc_1__scale_text'),
                    ncFailCode: window.ncFailCode || '',
                    ncFailCodeList: Array.isArray(window.ncFailCodeList) ? window.ncFailCodeList.slice(-5) : [],
                    hasAWSC: !!window.AWSC,
                    hasAwscEt: !!window.__awsc_et__,
                    hasNC: !!window.nc,
                };
            }
        """

        debug_info: Dict[str, Any] = {}
        for target_name, runtime_target in runtime_targets:
            try:
                runtime_info = runtime_target.evaluate(script)
                if isinstance(runtime_info, dict):
                    debug_info[target_name] = runtime_info
            except Exception as e:
                debug_info[target_name] = {"error": str(e)}

        return debug_info

    def _merge_runtime_feedback(self, search_target=None):
        feedback = dict(self.last_verification_feedback or {})
        runtime_debug = self._collect_runtime_debug_info(search_target)
        target_debug = runtime_debug.get("target") or runtime_debug.get("page") or {}

        fail_code = str(target_debug.get("ncFailCode") or "").strip()
        error_text = str(target_debug.get("errorText") or "").strip()
        if fail_code:
            feedback["fail_code"] = fail_code
        if error_text:
            feedback["dom_error_text"] = error_text

        self.last_verification_feedback = feedback

    def _harden_password_slider_runtime(self, search_target=None) -> None:
        if getattr(self, "_password_slider_runtime_hardened", False):
            return

        targets = []
        if search_target is not None:
            targets.append(("slider", search_target))
        if self.page is not None and self.page is not search_target:
            targets.append(("page", self.page))

        if not targets:
            self._password_slider_runtime_hardened = True
            return

        harden_script = """
            () => {
                const defineGetter = (target, prop, getter) => {
                    try {
                        Object.defineProperty(target, prop, {
                            get: getter,
                            configurable: true
                        });
                    } catch (e) {}
                };

                try {
                    defineGetter(Navigator.prototype, 'webdriver', () => undefined);
                } catch (e) {}
                try {
                    defineGetter(Navigator.prototype, 'languages', () => ['zh-CN', 'zh', 'en']);
                } catch (e) {}
                try {
                    defineGetter(Navigator.prototype, 'plugins', () => [1, 2, 3, 4, 5]);
                } catch (e) {}
                try {
                    window.chrome = window.chrome || {};
                    window.chrome.runtime = window.chrome.runtime || {};
                } catch (e) {}
                return true;
            }
        """

        applied = False
        for target_name, target in targets:
            try:
                target.evaluate(harden_script)
                applied = True
                logger.info(f"【{self.pure_user_id}】已加固密码登录滑块运行时: {target_name}")
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】加固密码登录滑块运行时失败({target_name}): {e}")

        self._password_slider_runtime_hardened = True
        if not applied:
            logger.debug(f"【{self.pure_user_id}】密码登录滑块运行时加固未命中可执行目标")

    def _apply_runtime_browser_profile(self, browser_features: Dict[str, Any]) -> Dict[str, Any]:
        features = dict(browser_features)

        if os.name == 'nt':
            features['platform'] = 'Win32'
            features['timezone_id'] = 'Asia/Shanghai'

        local_browser_info = getattr(self, "local_browser_info", None) or {}
        full_version = str(local_browser_info.get("version") or "").strip()
        major_version = str(local_browser_info.get("major_version") or "").strip()
        if not full_version:
            ua_match = re.search(r"Chrome/(\d+\.\d+\.\d+\.\d+)", str(features.get("user_agent") or ""))
            if ua_match:
                full_version = ua_match.group(1)
                major_version = full_version.split(".", 1)[0]

        if not full_version:
            return features

        browser_family = self._get_browser_family()
        if browser_family == "edge":
            features['user_agent'] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{full_version} Safari/537.36 Edg/{full_version}"
            )
            features['profile_id'] = (
                f"win_edge_{major_version}_{features.get('viewport_width', 1600)}x"
                f"{features.get('viewport_height', 900)}"
            )
        else:
            features['user_agent'] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{full_version} Safari/537.36"
            )
            features['profile_id'] = (
                f"win_chrome_{major_version}_{features.get('viewport_width', 1600)}x"
                f"{features.get('viewport_height', 900)}"
            )

        features['browser_version'] = full_version
        features['browser_major_version'] = major_version
        return features

    def _warmup_slider_context(self, target_url: Optional[str] = None):
        if not self.page:
            return

        warmup_urls = [
            "https://www.goofish.com",
            "https://www.goofish.com/im",
        ]

        for warmup_url in warmup_urls:
            if target_url and warmup_url == target_url:
                continue
            try:
                logger.info(f"【{self.pure_user_id}】预热访问: {warmup_url}")
                self.page.goto(warmup_url, wait_until="domcontentloaded", timeout=15000)
                time.sleep(random.uniform(0.8, 1.6))
                self.page.mouse.move(random.randint(260, 980), random.randint(180, 620))
                time.sleep(random.uniform(0.05, 0.12))
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】预热访问失败({warmup_url}): {e}")

    def _build_playwright_proxy_settings(self) -> Optional[Dict[str, str]]:
        proxy_type = str(self.proxy_config.get("proxy_type") or "").strip().lower()
        proxy_host = str(self.proxy_config.get("proxy_host") or "").strip()
        proxy_port = self.proxy_config.get("proxy_port")
        if proxy_type in {"", "none"} or not proxy_host or not proxy_port:
            return None

        proxy_settings: Dict[str, str] = {
            "server": f"{proxy_type}://{proxy_host}:{proxy_port}"
        }
        proxy_user = str(self.proxy_config.get("proxy_user") or "").strip()
        proxy_pass = str(self.proxy_config.get("proxy_pass") or "").strip()
        if proxy_user:
            proxy_settings["username"] = proxy_user
        if proxy_pass:
            proxy_settings["password"] = proxy_pass
        return proxy_settings

    def _should_use_account_persistent_profile(self) -> bool:
        return bool(getattr(self, "use_account_persistent_profile", False))

    def _resolve_account_persistent_profile_dir(self) -> str:
        profile_dir = str(getattr(self, "account_persistent_profile_dir", None) or "").strip()
        if not profile_dir:
            profile_dir = os.path.join(os.getcwd(), 'browser_data', f'user_{self.pure_user_id}')
        os.makedirs(profile_dir, exist_ok=True)
        return profile_dir

    def _build_playwright_context_options(self, browser_features: Dict[str, Any]) -> Dict[str, Any]:
        context_options: Dict[str, Any] = {
            'user_agent': browser_features['user_agent'],
            'locale': browser_features['locale'],
            'timezone_id': browser_features['timezone_id'],
            'color_scheme': browser_features['color_scheme'],
            'extra_http_headers': {
                'Accept-Language': browser_features['accept_lang']
            },
        }
        if not self.headless:
            context_options['no_viewport'] = True
        else:
            context_options.update({
                'viewport': {'width': browser_features['viewport_width'], 'height': browser_features['viewport_height']},
                'screen': {'width': browser_features['viewport_width'], 'height': browser_features['viewport_height']},
                'device_scale_factor': browser_features['device_scale_factor'],
                'is_mobile': browser_features['is_mobile'],
                'has_touch': browser_features['has_touch'],
            })
        return context_options

    def _build_initial_cookie_payload(self) -> List[Dict[str, Any]]:
        if not self.initial_cookies:
            return []

        cookies: List[Dict[str, Any]] = []
        for cookie_pair in self.initial_cookies.split(";"):
            cookie_pair = cookie_pair.strip()
            if not cookie_pair or "=" not in cookie_pair:
                continue
            name, value = cookie_pair.split("=", 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            cookies.append({
                "name": name,
                "value": value,
                "domain": ".goofish.com",
                "path": "/",
            })
        return cookies

    def _try_reset_slider_error_state(self, search_root, slider_container=None) -> bool:
        """阿里系 nocaptcha 常先落在“验证失败，点击框体重试”态，先点一下把真滑块唤出来。"""
        try:
            candidate_selectors = [
                "#nocaptcha .errloading",
                ".nc_wrapper .errloading",
                "[id*='refresh']",
                ".errloading",
            ]

            clicked = False
            for selector in candidate_selectors:
                try:
                    element = search_root.query_selector(selector)
                    if not element:
                        continue
                    try:
                        text = (element.inner_text() or "").strip()
                    except Exception:
                        text = ""
                    if text and ("点击框体重试" not in text and "验证失败" not in text):
                        continue
                    element.click(timeout=1500)
                    logger.info(f"【{self.pure_user_id}】检测到滑块错误态，已点击重试元素: {selector}")
                    clicked = True
                    break
                except Exception as selector_error:
                    _mark_detached_runtime(selector_error)
                    continue

            if not clicked and slider_container:
                try:
                    slider_container.click(timeout=1500)
                    logger.info(f"【{self.pure_user_id}】未命中重试元素，已点击滑块容器尝试唤起真实滑块")
                    clicked = True
                except Exception:
                    pass

            if clicked:
                time.sleep(1.2)
                return True
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】重置滑块错误态失败: {e}")
        return False

    def init_browser(self):
        """初始化浏览器 - 增强反检测版本"""
        try:
            if not self.browser_channel and not self.executable_path:
                self._ensure_project_playwright_browser()

            # 启动 Playwright
            playwright_factory = self._get_sync_playwright_factory()
            logger.info(f"【{self.pure_user_id}】启动浏览器自动化后端: {self.automation_backend}")
            self.playwright = playwright_factory().start()
            self._playwright_thread_id = threading.get_ident()
            logger.info(f"【{self.pure_user_id}】{self.automation_backend} 启动成功")
            
            # 为账号加载稳定浏览器画像
            browser_features = self._get_random_browser_features()
            self.browser_features = browser_features
            self.profile_id = browser_features.get("profile_id", "unknown")
            
            # 启动浏览器，使用稳定特征
            logger.info(
                f"【{self.pure_user_id}】启动浏览器，headless模式: {self.headless}, "
                f"画像: {self.profile_id}, UA: {browser_features['user_agent']}"
            )
            launch_options: Dict[str, Any] = {
                "headless": self.headless,
                "ignore_default_args": ["--enable-automation"],
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    f"--window-size={browser_features['window_size']}",
                    f"--lang={browser_features['lang']}",
                    f"--accept-lang={browser_features['accept_lang']}",
                    "--disable-blink-features=AutomationControlled",
                    "--mute-audio",
                    "--no-default-browser-check",
                    "--force-color-profile=srgb",
                    "--password-store=basic",
                    "--use-mock-keychain",
                ],
            }
            proxy_settings = self._build_playwright_proxy_settings()
            if proxy_settings:
                launch_options["proxy"] = proxy_settings
                logger.info(f"【{self.pure_user_id}】滑块浏览器启用代理: {proxy_settings['server']}")
            if self.browser_channel:
                launch_options["channel"] = self.browser_channel
            if self.executable_path:
                launch_options["executable_path"] = self.executable_path
                logger.info(f"【{self.pure_user_id}】滑块浏览器使用本机可执行文件: {self.executable_path}")
            context_options = self._build_playwright_context_options(browser_features)
            launched_with_persistent_profile = False

            if self._should_use_account_persistent_profile():
                user_data_dir = self._resolve_account_persistent_profile_dir()
                persistent_launch_options = dict(launch_options)
                persistent_launch_options.update(context_options)
                persistent_launch_options.update({
                    'accept_downloads': True,
                    'ignore_https_errors': True,
                })
                logger.info(f"【{self.pure_user_id}】token_refresh滑块优先复用账号级浏览器目录: {user_data_dir}")
                try:
                    self.context = self.playwright.chromium.launch_persistent_context(
                        user_data_dir,
                        **persistent_launch_options,
                    )
                    launched_with_persistent_profile = True
                    self.browser = None
                    self._browser_pid = self._extract_browser_pid(self.context)
                except Exception as persistent_launch_error:
                    if not self._is_profile_in_use_launch_error(persistent_launch_error):
                        raise
                    cleaned_stale_lock = self._try_cleanup_stale_chromium_singleton_lock(user_data_dir)
                    if cleaned_stale_lock:
                        logger.warning(
                            f"【{self.pure_user_id}】检测到账号级 profile 疑似残留 stale Chromium 锁，"
                            f"已清理并重试 persistent context: {user_data_dir}"
                        )
                        try:
                            self.context = self.playwright.chromium.launch_persistent_context(
                                user_data_dir,
                                **persistent_launch_options,
                            )
                            launched_with_persistent_profile = True
                            self.browser = None
                        except Exception as retry_launch_error:
                            if not self._is_profile_in_use_launch_error(retry_launch_error):
                                raise
                            logger.warning(
                                f"【{self.pure_user_id}】清理 stale Chromium 锁后仍提示 profile 被占用，"
                                f"回退临时上下文链路: {retry_launch_error}"
                            )
                    else:
                        logger.warning(
                            f"【{self.pure_user_id}】账号级浏览器目录被占用，且无法证明是 stale Chromium 锁，"
                            f"回退临时上下文链路: {persistent_launch_error}"
                        )

            if not launched_with_persistent_profile:
                try:
                    self.browser = self.playwright.chromium.launch(**launch_options)
                    self._browser_pid = self._extract_browser_pid(self.browser)
                except Exception as launch_error:
                    if self.headless and (launch_options.get("executable_path") or launch_options.get("channel")):
                        fallback_options = dict(launch_options)
                        fallback_options.pop("executable_path", None)
                        fallback_options.pop("channel", None)
                        logger.warning(
                            f"【{self.pure_user_id}】指定浏览器无头启动失败，回退到 Playwright Chromium: {launch_error}"
                        )
                        self.browser = self.playwright.chromium.launch(**fallback_options)
                        self._browser_pid = self._extract_browser_pid(self.browser)
                    else:
                        raise
            
            if launched_with_persistent_profile:
                logger.info(f"【{self.pure_user_id}】账号级 persistent browser context 启动成功")
            else:
                # 验证浏览器已启动
                if not self.browser or not self.browser.is_connected():
                    raise Exception("浏览器启动失败或连接已断开")
                logger.info(f"【{self.pure_user_id}】浏览器启动成功，已连接: {self.browser.is_connected()}")
                
                # 创建上下文，使用随机特征
                logger.info(f"【{self.pure_user_id}】创建浏览器上下文...")
                self.context = self.browser.new_context(**context_options)
            
            # 验证上下文已创建
            if not self.context:
                raise Exception("浏览器上下文创建失败")
            logger.info(f"【{self.pure_user_id}】浏览器上下文创建成功")

            initial_cookie_payload = self._build_initial_cookie_payload()
            if initial_cookie_payload:
                self.context.add_cookies(initial_cookie_payload)
                logger.info(f"【{self.pure_user_id}】已向滑块上下文注入 {len(initial_cookie_payload)} 个初始Cookie")
            
            # 创建新页面
            logger.info(f"【{self.pure_user_id}】创建新页面...")
            self.page = self.context.new_page()
            
            # 验证页面已创建
            if not self.page:
                raise Exception("页面创建失败")
            logger.info(f"【{self.pure_user_id}】页面创建成功（{'最大化窗口模式' if not self.headless else '无头模式'}）")
            
            # 添加增强反检测脚本
            logger.info(f"【{self.pure_user_id}】添加反检测脚本...")
            self._install_stealth_init_script(self.page, browser_features)
            logger.info(f"【{self.pure_user_id}】浏览器初始化完成")
            
            return self.page
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】初始化浏览器失败: {e}")
            import traceback
            logger.error(f"【{self.pure_user_id}】详细错误堆栈: {traceback.format_exc()}")
            # 确保在异常时也清理已创建的资源
            self._cleanup_on_init_failure()
            raise
    
    def _cleanup_on_init_failure(self):
        """初始化失败时的清理"""
        try:
            if hasattr(self, 'page') and self.page:
                self.page.close()
                self.page = None
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】清理页面时出错: {e}")
        
        try:
            if hasattr(self, 'context') and self.context:
                self.context.close()
                self.context = None
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】清理上下文时出错: {e}")
        
        try:
            if hasattr(self, 'browser') and self.browser:
                self.browser.close()
                self.browser = None
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】清理浏览器时出错: {e}")

        self._force_kill_browser_process_tree("init_failure_cleanup")
        
        try:
            if hasattr(self, 'playwright') and self.playwright:
                self.playwright.stop()
                self.playwright = None
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】清理Playwright时出错: {e}")
    
    def _load_success_history(self) -> List[Dict[str, Any]]:
        """加载历史成功数据（带自动清理）"""
        try:
            if not os.path.exists(self.success_history_file):
                return []
            
            # 🧹 自动检查并清理历史数据
            try:
                cleaned = adaptive_strategy_manager.check_and_cleanup_history(
                    self.pure_user_id, 
                    self.success_history_file
                )
                if cleaned:
                    logger.info(f"【{self.pure_user_id}】🧹 历史数据已自动清理")
            except Exception as cleanup_e:
                logger.debug(f"【{self.pure_user_id}】清理检查跳过: {cleanup_e}")
            
            with open(self.success_history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
                logger.info(f"【{self.pure_user_id}】加载历史成功数据: {len(history)}条记录")
                return history
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】加载历史数据失败: {e}")
            return []
    
    def _get_learning_history_with_fallback(self, reference_distance: Optional[float] = None,
                                            limit: int = 24) -> List[Dict[str, Any]]:
        """Cold-start fallback: reuse recent success samples from the same headless profile."""
        history = self._load_success_history()
        if len(history) >= 3 or not self._use_headless_stable_profile():
            return history

        try:
            history_dir = os.path.dirname(self.success_history_file) or "trajectory_history"
            current_file = os.path.abspath(self.success_history_file)
            matched_records = []
            relaxed_records = []
            distance_tolerance = 3.0 if self._is_password_login_scene() else 12.0

            for history_path in glob.glob(os.path.join(history_dir, "*_success.json")):
                try:
                    if os.path.abspath(history_path) == current_file:
                        continue

                    with open(history_path, 'r', encoding='utf-8') as f:
                        raw_records = json.load(f)

                    if isinstance(raw_records, dict):
                        raw_records = [raw_records]
                    if not isinstance(raw_records, list):
                        continue

                    for record in raw_records:
                        if not isinstance(record, dict) or not record.get("success"):
                            continue
                        if not self._is_learning_sample_scene_compatible(history_path, record):
                            continue

                        verification_result = record.get("verification_result", {}) or {}
                        record_headless = bool(record.get("headless", verification_result.get("headless", self.headless)))
                        if record_headless != bool(self.headless):
                            continue

                        record_profile_id = str(
                            record.get("profile_id")
                            or verification_result.get("profile_id")
                            or ""
                        ).strip()
                        if self.profile_id and record_profile_id and not self._is_learning_profile_compatible(record_profile_id):
                            continue

                        distance_value = record.get("distance")
                        if reference_distance is not None and isinstance(distance_value, (int, float)):
                            if abs(float(distance_value) - float(reference_distance)) <= distance_tolerance:
                                matched_records.append(record)
                            else:
                                relaxed_records.append(record)
                        else:
                            matched_records.append(record)
                except Exception as history_err:
                    logger.debug(f"【{self.pure_user_id}】读取全局成功样本失败 {history_path}: {history_err}")

            matched_records.sort(key=lambda item: item.get("timestamp", 0), reverse=True)
            relaxed_records.sort(key=lambda item: item.get("timestamp", 0), reverse=True)

            if reference_distance is not None and len(matched_records) < 3 and not self._is_password_login_scene():
                matched_records.extend(relaxed_records)

            if limit > 0:
                matched_records = matched_records[:limit]

            if matched_records:
                needed = max(0, limit - len(history))
                injected_records = matched_records[:needed]
                history = list(history) + injected_records
                logger.info(
                    f"【{self.pure_user_id}】本地成功记录不足，补充加载 {len(injected_records)} 条同画像全局成功样本"
                )
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】加载全局成功样本失败: {e}")

        return history

    def _has_recent_failure_pressure(self, reference_distance: Optional[float] = None,
                                     window_seconds: int = 24 * 3600) -> bool:
        """Return True when recent failed drags make learned replay risky."""
        try:
            if not os.path.exists(self.failure_history_file):
                return False

            with open(self.failure_history_file, 'r', encoding='utf-8') as f:
                failures = json.load(f)
            if not isinstance(failures, list):
                return False

            now = time.time()
            current_scene = self._normalize_learning_scene()
            recent_failures = []
            for record in failures:
                if not isinstance(record, dict):
                    continue

                ts = record.get("timestamp")
                if isinstance(ts, (int, float)) and now - float(ts) > window_seconds:
                    continue

                record_scene = self._normalize_learning_scene(
                    record.get("trigger_scene") or record.get("risk_trigger_scene")
                )
                if record_scene != "generic" and current_scene != "generic" and record_scene != current_scene:
                    continue

                distance_value = record.get("distance") or record.get("slide_distance")
                if reference_distance is not None and isinstance(distance_value, (int, float)):
                    if abs(float(distance_value) - float(reference_distance)) > 6.0:
                        continue

                feedback = record.get("verification_feedback") or {}
                message = str(feedback.get("message") or feedback.get("dom_error_text") or "")
                if feedback.get("status") == "failure" or "验证失败" in message or "点击框体重试" in message:
                    recent_failures.append(record)

            if len(recent_failures) < 6:
                return False

            success_history = self._load_success_history()
            recent_success_count = 0
            for record in success_history:
                if not isinstance(record, dict) or not record.get("success"):
                    continue
                ts = record.get("timestamp")
                if isinstance(ts, (int, float)) and now - float(ts) > window_seconds:
                    continue
                distance_value = record.get("distance")
                if reference_distance is not None and isinstance(distance_value, (int, float)):
                    if abs(float(distance_value) - float(reference_distance)) > 6.0:
                        continue
                recent_success_count += 1

            pressure = len(recent_failures) >= max(6, recent_success_count * 3)
            if pressure:
                logger.warning(
                    f"【{self.pure_user_id}】近期滑块失败压力过高: "
                    f"失败{len(recent_failures)}次 / 成功{recent_success_count}次，临时禁用学习轨迹复用"
                )
            return pressure
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】统计近期滑块失败压力失败: {e}")
            return False

    def _normalize_learning_scene(self, trigger_scene: Optional[str] = None) -> str:
        scene = str(trigger_scene or getattr(self, "risk_trigger_scene", None) or "").strip().lower()
        if scene in {"password_login", "manual_password_refresh"}:
            return "password"
        if scene == "token_refresh":
            return "token_refresh"
        if scene == "auto_cookie_refresh":
            return "cookie"
        return scene or "generic"

    def _infer_success_sample_scene(self, history_path: str, record: Optional[Dict[str, Any]] = None) -> str:
        explicit_scene = ""
        if isinstance(record, dict):
            explicit_scene = str(
                record.get("trigger_scene")
                or record.get("risk_trigger_scene")
                or ""
            ).strip().lower()
        normalized_explicit_scene = self._normalize_learning_scene(explicit_scene) if explicit_scene else ""
        if normalized_explicit_scene and normalized_explicit_scene != "generic":
            return normalized_explicit_scene

        parts = [os.path.basename(str(history_path or ""))]
        if isinstance(record, dict):
            parts.extend(
                [
                    str(record.get("user_id") or ""),
                    str(record.get("page_url") or ""),
                    str(record.get("page_title") or ""),
                ]
            )

        sample_text = " ".join(parts).lower()
        if not sample_text:
            return "generic"

        token_refresh_tokens = (
            "token_refresh",
            "keepalive",
            "session_keepalive",
            "captcha_verification_failed",
        )
        if any(token in sample_text for token in token_refresh_tokens):
            return "token_refresh"

        if any(token in sample_text for token in ("password", "pwd")):
            return "password"

        cookie_tokens = (
            "ui_cookie",
            "import_user_cookie",
            "manual_cookie",
            "cookie_import",
            "manual_import",
            "cookie_flow",
            "cookie_run",
            "cookie_headless",
        )
        if any(token in sample_text for token in cookie_tokens):
            return "cookie"

        if "refresh" in sample_text and "password" not in sample_text and "pwd" not in sample_text:
            return "token_refresh"

        return "generic"

    def _is_learning_sample_scene_compatible(self, history_path: str, record: Optional[Dict[str, Any]] = None) -> bool:
        current_scene = self._normalize_learning_scene()
        if current_scene == "generic":
            return self._is_password_scene_success_sample(history_path, record)

        sample_scene = self._infer_success_sample_scene(history_path, record)
        if sample_scene == "generic":
            return False
        return sample_scene == current_scene

    def _get_password_scene_final_retry_template(self, effective_ranges: Dict[str, Tuple[float, float]],
                                                 bounds: Dict[str, Any]) -> Dict[str, Tuple[float, float]]:
        def clamp_range(source_range: Tuple[float, float], hard_range: Tuple[float, float], fallback: Tuple[float, float]):
            lower = max(source_range[0], hard_range[0])
            upper = min(source_range[1], hard_range[1])
            if lower > upper:
                lower, upper = fallback
            return (lower, upper)

        overshoot = clamp_range(
            effective_ranges["overshoot"],
            (1.028, min(bounds.get("max_overshoot_ratio", 1.18), 1.055)),
            (1.032, 1.050),
        )
        delay = clamp_range(effective_ranges["delay"], (0.0108, 0.0128), (0.0110, 0.0124))
        curve = clamp_range(effective_ranges["curve"], (1.76, 1.86), (1.78, 1.84))
        jitter = clamp_range(
            effective_ranges["jitter"],
            (max(bounds.get("min_y_jitter", 0.8), 1.55), min(bounds.get("max_y_jitter", 3.5), 2.35)),
            (1.70, 2.20),
        )

        step_min = max(30, effective_ranges["steps"][0], 32)
        step_max = min(36, max(step_min, effective_ranges["steps"][1]))
        if step_min > step_max:
            step_min, step_max = 32, 35

        return {
            "overshoot": overshoot,
            "delay": delay,
            "curve": curve,
            "jitter": jitter,
            "steps": (step_min, step_max),
        }

    def _save_success_record(self, trajectory_data: Dict[str, Any]):
        """保存成功记录（增强版 - 记录所有随机参数用于学习优化）"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.success_history_file), exist_ok=True)
            
            # 加载现有历史
            history = self._load_success_history()
            
            # 获取随机参数
            random_params = trajectory_data.get("random_params", {})
            slide_behavior = trajectory_data.get("slide_behavior", {})
            verification_result = trajectory_data.get("verification_result", {})
            
            # 添加新记录 - 保存完整的随机参数用于学习
            record = {
                "timestamp": time.time(),
                "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user_id": self.pure_user_id,
                "trigger_scene": getattr(self, "risk_trigger_scene", None),
                "distance": trajectory_data.get("distance", 0),
                "total_steps": trajectory_data.get("total_steps", 0),
                "model": trajectory_data.get("model", "unknown"),
                # 新增：保存所有轨迹生成的随机参数
                "overshoot_ratio": random_params.get("overshoot_ratio", 0),
                "base_delay": random_params.get("base_delay", 0),
                "acceleration_curve": random_params.get("acceleration_curve", 0),
                "y_jitter_max": random_params.get("y_jitter_max", 0),
                "random_state_snapshot": random_params.get("random_state_snapshot", []),
                # 新增：保存所有滑动行为的随机参数（18个随机因素）
                "slide_behavior": {
                    "approach_offset_x": slide_behavior.get("approach_offset_x", 0),
                    "approach_offset_y": slide_behavior.get("approach_offset_y", 0),
                    "approach_steps": slide_behavior.get("approach_steps", 0),
                    "approach_pause": slide_behavior.get("approach_pause", 0),
                    "precision_steps": slide_behavior.get("precision_steps", 0),
                    "precision_pause": slide_behavior.get("precision_pause", 0),
                    "skip_hover": slide_behavior.get("skip_hover", False),
                    "hover_pause": slide_behavior.get("hover_pause", 0),
                    "pre_down_pause": slide_behavior.get("pre_down_pause", 0),
                    "post_down_pause": slide_behavior.get("post_down_pause", 0),
                    "move_steps_range": slide_behavior.get("move_steps_range", (1, 3)),
                    "delay_variation": slide_behavior.get("delay_variation", (0.9, 1.1)),
                    "pre_up_pause": slide_behavior.get("pre_up_pause", 0),
                    "post_up_pause": slide_behavior.get("post_up_pause", 0),
                    "server_judge_wait": slide_behavior.get("server_judge_wait", 0),
                    "total_elapsed_time": slide_behavior.get("total_elapsed_time", 0),
                },
                # 保留旧字段以兼容旧版本
                "base_delay_old": trajectory_data.get("base_delay", 0),
                "jitter_x_range": trajectory_data.get("jitter_x_range", [0, 0]),
                "jitter_y_range": trajectory_data.get("jitter_y_range", [0, 0]),
                "slow_factor": trajectory_data.get("slow_factor", 0),
                "acceleration_phase": trajectory_data.get("acceleration_phase", 0),
                "fast_phase": trajectory_data.get("fast_phase", 0),
                "slow_start_ratio": trajectory_data.get("slow_start_ratio", 0),
                # 【优化】不再保存完整轨迹点，节省 90% 存储空间
                # "trajectory_points": trajectory_data.get("trajectory_points", []),
                "trajectory_point_count": len(trajectory_data.get("trajectory_points", [])),  # 只记录数量
                "final_left_px": trajectory_data.get("final_left_px", 0),
                "completion_used": trajectory_data.get("completion_used", False),
                "completion_steps": trajectory_data.get("completion_steps", 0),
                "profile_id": verification_result.get("profile_id", self.profile_id),
                "headless": verification_result.get("headless", self.headless),
                "verification_result": verification_result,
                "success": True
            }
            
            history.append(record)
            
            # 只保留最近100条成功记录
            if len(history) > 100:
                history = history[-100:]
            
            # 保存到文件
            with open(self.success_history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            
            # 统计滑动行为参数数量
            behavior_params_count = len([k for k in slide_behavior.keys() if not k.startswith('hesitation_at_')])
            
            logger.info(f"【{self.pure_user_id}】✅ 保存成功记录: "
                       f"距离{record['distance']:.1f}px, 步数{record['total_steps']}, "
                       f"超调{record['overshoot_ratio']:.2f}x, 加速^{record['acceleration_curve']:.2f}, "
                       f"行为参数{behavior_params_count}个")
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】保存成功记录失败: {e}")

    def _save_failure_record(self, trajectory_data: Dict[str, Any], failure_info: Dict[str, Any]):
        """保存失败记录，便于分析最近失败样本"""
        try:
            os.makedirs(os.path.dirname(self.failure_history_file), exist_ok=True)

            history = []
            if os.path.exists(self.failure_history_file):
                with open(self.failure_history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)

            random_params = trajectory_data.get("random_params", {})
            slide_behavior = trajectory_data.get("slide_behavior", {})
            verification_feedback = failure_info.get("verification_feedback", {})
            verification_result = trajectory_data.get("verification_result", {})

            try:
                page_url = self.page.url if self.page else ""
            except Exception:
                page_url = ""

            try:
                page_title = self.page.title() if self.page else ""
            except Exception:
                page_title = ""

            record = {
                "timestamp": time.time(),
                "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "user_id": self.pure_user_id,
                "trigger_scene": getattr(self, "risk_trigger_scene", None),
                "attempt": failure_info.get("attempt", 0),
                "distance": trajectory_data.get("distance", 0),
                "slide_distance": failure_info.get("slide_distance", 0),
                "total_steps": trajectory_data.get("total_steps", 0),
                "model": trajectory_data.get("model", "unknown"),
                "overshoot_ratio": random_params.get("overshoot_ratio", 0),
                "requested_steps": random_params.get("steps", 0),
                "base_delay": random_params.get("base_delay", 0),
                "acceleration_curve": random_params.get("acceleration_curve", 0),
                "y_jitter_max": random_params.get("y_jitter_max", 0),
                "strategy": random_params.get("strategy", "unknown"),
                "profile": random_params.get("profile", "unknown"),
                "use_exploration": random_params.get("use_exploration", False),
                "final_left_px": trajectory_data.get("final_left_px", 0),
                "trajectory_point_count": len(trajectory_data.get("trajectory_points", [])),
                "slide_behavior": {
                    "approach_offset_x": slide_behavior.get("approach_offset_x", 0),
                    "approach_offset_y": slide_behavior.get("approach_offset_y", 0),
                    "approach_steps": slide_behavior.get("approach_steps", 0),
                    "approach_pause": slide_behavior.get("approach_pause", 0),
                    "precision_steps": slide_behavior.get("precision_steps", 0),
                    "precision_pause": slide_behavior.get("precision_pause", 0),
                    "skip_hover": slide_behavior.get("skip_hover", False),
                    "hover_pause": slide_behavior.get("hover_pause", 0),
                    "pre_down_pause": slide_behavior.get("pre_down_pause", 0),
                    "post_down_pause": slide_behavior.get("post_down_pause", 0),
                    "pre_up_pause": slide_behavior.get("pre_up_pause", 0),
                    "post_up_pause": slide_behavior.get("post_up_pause", 0),
                    "delay_variation": slide_behavior.get("delay_variation", (0.9, 1.1)),
                    "server_judge_wait": slide_behavior.get("server_judge_wait", 0),
                    "total_elapsed_time": slide_behavior.get("total_elapsed_time", 0),
                },
                "verification_feedback": verification_feedback,
                "verification_result": verification_result,
                "profile_id": verification_result.get("profile_id", self.profile_id),
                "headless": verification_result.get("headless", self.headless),
                "page_url": page_url,
                "page_title": page_title,
                "success": False
            }

            history.append(record)
            if len(history) > 200:
                history = history[-200:]

            with open(self.failure_history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)

            logger.info(
                f"【{self.pure_user_id}】📝 保存失败记录: 第{record['attempt']}次, "
                f"策略={record['strategy']}/{record['profile']}, "
                f"距离{record['slide_distance']:.1f}px, 步数{record['total_steps']}"
            )

        except Exception as e:
            logger.error(f"【{self.pure_user_id}】保存失败记录失败: {e}")

    def _save_debug_snapshot(self, reason: str, search_target=None):
        """保存失败现场，方便比对页面状态和风控返回。"""
        try:
            debug_dir = os.path.join("logs", "slider_debug")
            os.makedirs(debug_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_reason = "".join(
                ch if ch.isalnum() or ch in "._-" else "_"
                for ch in str(reason or "snapshot")
            ).strip("._") or "snapshot"
            base_name = f"{self.pure_user_id}_{safe_reason}_{timestamp}"

            page_url = ""
            page_title = ""
            try:
                if self.page:
                    page_url = self.page.url or ""
                    page_title = self.page.title() or ""
            except Exception:
                pass

            frame_url = ""
            try:
                if search_target is not None and hasattr(search_target, "url"):
                    frame_url = getattr(search_target, "url", "") or ""
            except Exception:
                pass

            if self.page:
                screenshot_path = os.path.join(debug_dir, f"{base_name}.png")
                try:
                    self.page.screenshot(path=screenshot_path, full_page=True, timeout=10000)
                except Exception:
                    self.page.screenshot(path=screenshot_path, full_page=False, timeout=10000)

                page_html_path = os.path.join(debug_dir, f"{base_name}.html")
                with open(page_html_path, "w", encoding="utf-8") as f:
                    f.write(self.page.content())

            if search_target is not None and search_target is not self.page:
                try:
                    frame_html = search_target.content()
                    frame_html_path = os.path.join(debug_dir, f"{base_name}__frame.html")
                    with open(frame_html_path, "w", encoding="utf-8") as f:
                        f.write(frame_html)
                except Exception as frame_err:
                    logger.debug(f"【{self.pure_user_id}】保存Frame HTML失败: {frame_err}")

            runtime_debug = self._collect_runtime_debug_info(search_target)
            meta = {
                "user_id": self.pure_user_id,
                "reason": reason,
                "page_url": page_url,
                "page_title": page_title,
                "frame_url": frame_url,
                "feedback": dict(self.last_verification_feedback or {}),
                "runtime_debug": runtime_debug,
                "profile_id": self.profile_id,
                "headless": self.headless,
                "automation_backend": self.automation_backend,
                "stealth_mode": self.active_stealth_mode,
                "local_browser_info": dict(self.local_browser_info or {}),
                "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            meta_path = os.path.join(debug_dir, f"{base_name}.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            logger.info(f"【{self.pure_user_id}】已保存调试快照: {os.path.join(debug_dir, base_name)}")
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】保存调试快照失败: {e}")
    
    def _optimize_trajectory_params(self, reference_distance: Optional[float] = None) -> Dict[str, Any]:
        """基于历史成功数据优化轨迹参数（增强版 - 智能学习）"""
        try:
            if not self.enable_learning:
                return self.trajectory_params
            
            history = self._get_learning_history_with_fallback(reference_distance=reference_distance)
            required_history_count = 2 if self._allow_small_sample_learning(history, reference_distance) else 3
            if len(history) < required_history_count:
                logger.info(f"【{self.pure_user_id}】历史成功数据不足({len(history)}条)，使用默认参数")
                return self.trajectory_params
            if required_history_count == 2:
                logger.info(f"【{self.pure_user_id}】成功样本虽仅2条，但同画像且同距离区间，直接启用学习参数")
            
            # 🎯 新版参数学习：基于新的随机参数结构
            # 收集新版参数（overshoot_ratio, acceleration_curve等）
            overshoot_ratios = [record.get("overshoot_ratio", 2.0) for record in history if record.get("overshoot_ratio")]
            base_delays = [record.get("base_delay", 0.0004) for record in history if record.get("base_delay")]
            acceleration_curves = [record.get("acceleration_curve", 1.5) for record in history if record.get("acceleration_curve")]
            y_jitter_maxs = [record.get("y_jitter_max", 2.0) for record in history if record.get("y_jitter_max")]
            total_steps_list = [record.get("total_steps", 6) for record in history]
            
            # 计算平均值和标准差
            def safe_avg(values):
                return sum(values) / len(values) if values else 0
            
            def safe_std(values):
                if len(values) < 2:
                    return 0
                avg = safe_avg(values)
                variance = sum((x - avg) ** 2 for x in values) / len(values)
                return variance ** 0.5
            
            def safe_percentile(values, percentile):
                """计算百分位数"""
                if not values:
                    return 0
                sorted_values = sorted(values)
                index = int(len(sorted_values) * percentile)
                return sorted_values[min(index, len(sorted_values) - 1)]
            
            # 🧠 智能学习策略（优化版 - 避免过度收敛）：
            # 1. 使用成功记录的中位数作为中心值（更稳定）
            # 2. 使用标准差的0.5倍作为范围（保持随机性）
            # 3. 🔧 应用边界限制，防止学习到极端值
            # 4. 🔧 强制最小范围宽度，保持探索能力
            
            # 获取边界限制
            bounds = ML_STRATEGY_CONFIG.get("learning_bounds", {})
            min_overshoot = bounds.get("min_overshoot_ratio", 1.75)
            max_overshoot = bounds.get("max_overshoot_ratio", 2.12)
            min_y_jitter = bounds.get("min_y_jitter", 0.8)
            max_y_jitter = bounds.get("max_y_jitter", 3.0)
            
            # 学习超调比例（关键参数）
            # 🔧 2025-12-25：适配新的贝塞尔曲线轨迹，超调比例改为真实百分比（1.01-1.15）
            if overshoot_ratios:
                overshoot_median = safe_percentile(overshoot_ratios, 0.5)
                overshoot_std = safe_std(overshoot_ratios)
                
                # 🔧 关键修复：如果中位数超过上限，强制拉回到合理范围
                if overshoot_median > max_overshoot:
                    logger.warning(f"【{self.pure_user_id}】⚠️ 学习到的超调比例中位数({overshoot_median:.2f})过高，"
                                   f"强制调整到{max_overshoot}")
                    overshoot_median = max_overshoot - 0.02
                elif overshoot_median < min_overshoot:
                    logger.warning(f"【{self.pure_user_id}】⚠️ 学习到的超调比例中位数({overshoot_median:.2f})过低，"
                                   f"强制调整到{min_overshoot}")
                    overshoot_median = min_overshoot + 0.02
                
                # 应用边界限制
                overshoot_min = max(min_overshoot, overshoot_median - max(overshoot_std * 0.3, 0.03))
                overshoot_max = min(max_overshoot, overshoot_median + max(overshoot_std * 0.3, 0.03))
                
                # 🔧 确保最小范围宽度（至少0.04的差距，即4%）
                if overshoot_max - overshoot_min < 0.04:
                    overshoot_min = max(min_overshoot, overshoot_median - 0.02)
                    overshoot_max = min(max_overshoot, overshoot_median + 0.02)
                
                learned_overshoot = (overshoot_min, overshoot_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优超调比例: {overshoot_min:.2f}-{overshoot_max:.2f}x "
                           f"(中位数:{overshoot_median:.2f}, 边界限制:{min_overshoot}-{max_overshoot})")
            else:
                learned_overshoot = (1.03, 1.08)  # 🔧 新默认值：3-8%超调
            
            # 学习基础延迟（影响速度感知）
            # 🔧 2025-12-25：改为毫秒级延迟（0.004-0.015秒）
            if base_delays:
                delay_median = safe_percentile(base_delays, 0.5)
                delay_std = safe_std(base_delays)
                delay_min = max(0.003, delay_median - delay_std * 0.4)
                delay_max = min(0.020, delay_median + delay_std * 0.4)
                
                # 🔧 确保最小范围宽度（至少3ms的差距）
                if delay_max - delay_min < 0.003:
                    delay_min = max(0.003, delay_median - 0.0015)
                    delay_max = min(0.020, delay_median + 0.0015)
                
                learned_delay = (delay_min, delay_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优延迟: {delay_min*1000:.1f}-{delay_max*1000:.1f}ms "
                           f"(中位数:{delay_median*1000:.1f}ms)")
            else:
                learned_delay = (0.006, 0.012)  # 🔧 新默认值：6-12ms
            
            # 学习加速曲线（影响轨迹形状）
            # 🔧 2025-12-25：适配贝塞尔曲线的ease-out指数
            if acceleration_curves:
                curve_median = safe_percentile(acceleration_curves, 0.5)
                curve_std = safe_std(acceleration_curves)
                curve_min = max(1.3, curve_median - curve_std * 0.3)
                curve_max = min(2.5, curve_median + curve_std * 0.3)
                
                # 🔧 确保最小范围宽度（至少0.2的差距）
                if curve_max - curve_min < 0.2:
                    curve_min = max(1.3, curve_median - 0.1)
                    curve_max = min(2.5, curve_median + 0.1)
                
                learned_curve = (curve_min, curve_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优加速曲线: ^{curve_min:.2f}-^{curve_max:.2f} "
                           f"(中位数:^{curve_median:.2f})")
            else:
                learned_curve = (1.6, 2.0)  # 🔧 新默认值
            
            # 学习Y轴抖动（影响真实感）
            if y_jitter_maxs:
                jitter_median = safe_percentile(y_jitter_maxs, 0.5)
                jitter_std = safe_std(y_jitter_maxs)
                
                # 🔧 关键修复：如果中位数超过边界，强制拉回
                if jitter_median > max_y_jitter:
                    logger.warning(f"【{self.pure_user_id}】⚠️ 学习到的Y抖动中位数({jitter_median:.1f})过高，"
                                   f"强制调整到{max_y_jitter}")
                    jitter_median = max_y_jitter - 0.3
                elif jitter_median < min_y_jitter:
                    jitter_median = min_y_jitter + 0.3
                
                # 应用边界限制
                jitter_min = max(min_y_jitter, jitter_median - max(jitter_std * 0.4, 0.4))
                jitter_max = min(max_y_jitter, jitter_median + max(jitter_std * 0.4, 0.4))
                
                # 🔧 确保最小范围宽度（至少0.6的差距）
                if jitter_max - jitter_min < 0.6:
                    jitter_min = max(min_y_jitter, jitter_median - 0.3)
                    jitter_max = min(max_y_jitter, jitter_median + 0.3)
                
                learned_jitter = (jitter_min, jitter_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优Y抖动: {jitter_min:.1f}-{jitter_max:.1f}px "
                           f"(中位数:{jitter_median:.1f}px, 边界限制:{min_y_jitter}-{max_y_jitter})")
            else:
                learned_jitter = (1.5, 2.2)  # 🔧 新默认值
            
            # 学习步数范围
            # 这里的步数会直接传递给新轨迹生成器，避免策略与执行脱节
            if total_steps_list:
                steps_median = int(safe_percentile(total_steps_list, 0.5))
                steps_std = safe_std(total_steps_list)
                steps_min = max(20, int(steps_median - steps_std * 0.5))
                steps_max = min(40, int(steps_median + steps_std * 0.5))
                
                # 🔧 确保最小范围宽度（至少5步的差距）
                if steps_max - steps_min < 5:
                    steps_min = max(20, steps_median - 2)
                    steps_max = min(40, steps_median + 3)

                # 防御性兜底：历史样本中位数可能超过上限，导致区间反转
                if steps_min > steps_max:
                    clamped_median = min(40, max(20, steps_median))
                    steps_min = max(20, clamped_median - 3)
                    steps_max = min(40, max(steps_min + 2, clamped_median))

                learned_steps = (steps_min, steps_max)
                logger.info(f"【{self.pure_user_id}】📚 学习到最优步数: {steps_min}-{steps_max}步 "
                           f"(中位数:{steps_median}步)")
            else:
                learned_steps = (22, 30)  # 🔧 新默认值
            
            # 🎯 新增：学习滑动行为参数（18种行为参数）
            logger.info(f"【{self.pure_user_id}】📚 开始学习滑动行为参数...")
            
            # 收集所有成功记录的滑动行为数据
            slide_behaviors = [record.get("slide_behavior", {}) for record in history if record.get("slide_behavior")]
            
            learned_behavior = {}
            
            if slide_behaviors:
                # 学习接近偏移
                approach_offset_x_list = [b.get("approach_offset_x", -20) for b in slide_behaviors if b.get("approach_offset_x")]
                if approach_offset_x_list:
                    median = safe_percentile(approach_offset_x_list, 0.5)
                    std = safe_std(approach_offset_x_list)
                    x_min = max(-45, median - std * 0.5)
                    x_max = min(-5, median + std * 0.5)
                    # 🔧 确保最小范围宽度（至少10px）
                    if x_max - x_min < 10:
                        x_min = max(-45, median - 5)
                        x_max = min(-5, median + 5)
                    learned_behavior["approach_offset_x"] = (x_min, x_max)
                
                approach_offset_y_list = [b.get("approach_offset_y", 0) for b in slide_behaviors if b.get("approach_offset_y")]
                if approach_offset_y_list:
                    median = safe_percentile(approach_offset_y_list, 0.5)
                    std = safe_std(approach_offset_y_list)
                    y_min = max(-25, median - std * 0.5)
                    y_max = min(25, median + std * 0.5)
                    # 🔧 确保最小范围宽度（至少10px）
                    if y_max - y_min < 10:
                        y_min = max(-25, median - 5)
                        y_max = min(25, median + 5)
                    learned_behavior["approach_offset_y"] = (y_min, y_max)
                
                # 学习接近步数
                approach_steps_list = [b.get("approach_steps", 7) for b in slide_behaviors if b.get("approach_steps")]
                if approach_steps_list:
                    median = int(safe_percentile(approach_steps_list, 0.5))
                    std = safe_std(approach_steps_list)
                    steps_min = max(3, int(median - std * 0.5))
                    steps_max = min(15, int(median + std * 0.5))
                    # 🔧 确保最小范围宽度（至少3步）
                    if steps_max - steps_min < 3:
                        steps_min = max(3, median - 2)
                        steps_max = min(15, median + 2)
                    learned_behavior["approach_steps"] = (steps_min, steps_max)
                
                # 学习停顿时间
                approach_pause_list = [b.get("approach_pause", 0.2) for b in slide_behaviors if b.get("approach_pause")]
                if approach_pause_list:
                    median = safe_percentile(approach_pause_list, 0.5)
                    std = safe_std(approach_pause_list)
                    pause_min = max(0.05, median - std * 0.4)
                    pause_max = min(0.5, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.1秒）
                    if pause_max - pause_min < 0.1:
                        pause_min = max(0.05, median - 0.05)
                        pause_max = min(0.5, median + 0.05)
                    learned_behavior["approach_pause"] = (pause_min, pause_max)
                
                precision_steps_list = [b.get("precision_steps", 5) for b in slide_behaviors if b.get("precision_steps")]
                if precision_steps_list:
                    median = int(safe_percentile(precision_steps_list, 0.5))
                    std = safe_std(precision_steps_list)
                    steps_min = max(2, int(median - std * 0.5))
                    steps_max = min(10, int(median + std * 0.5))
                    # 🔧 确保最小范围宽度（至少2步）
                    if steps_max - steps_min < 2:
                        steps_min = max(2, median - 1)
                        steps_max = min(10, median + 1)
                    learned_behavior["precision_steps"] = (steps_min, steps_max)
                
                precision_pause_list = [b.get("precision_pause", 0.15) for b in slide_behaviors if b.get("precision_pause")]
                if precision_pause_list:
                    median = safe_percentile(precision_pause_list, 0.5)
                    std = safe_std(precision_pause_list)
                    pause_min = max(0.03, median - std * 0.4)
                    pause_max = min(0.4, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.08秒）
                    if pause_max - pause_min < 0.08:
                        pause_min = max(0.03, median - 0.04)
                        pause_max = min(0.4, median + 0.04)
                    learned_behavior["precision_pause"] = (pause_min, pause_max)
                
                # 学习悬停概率
                skip_hover_list = [b.get("skip_hover", False) for b in slide_behaviors if "skip_hover" in b]
                if skip_hover_list:
                    skip_rate = sum(1 for x in skip_hover_list if x) / len(skip_hover_list)
                    learned_behavior["skip_hover_rate"] = skip_rate
                
                hover_pause_list = [b.get("hover_pause", 0.2) for b in slide_behaviors if b.get("hover_pause")]
                if hover_pause_list:
                    median = safe_percentile(hover_pause_list, 0.5)
                    std = safe_std(hover_pause_list)
                    pause_min = max(0.03, median - std * 0.4)
                    pause_max = min(0.5, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.1秒）
                    if pause_max - pause_min < 0.1:
                        pause_min = max(0.03, median - 0.05)
                        pause_max = min(0.5, median + 0.05)
                    learned_behavior["hover_pause"] = (pause_min, pause_max)
                
                # 学习按下停顿
                pre_down_list = [b.get("pre_down_pause", 0.1) for b in slide_behaviors if b.get("pre_down_pause")]
                if pre_down_list:
                    median = safe_percentile(pre_down_list, 0.5)
                    std = safe_std(pre_down_list)
                    pause_min = max(0.01, median - std * 0.4)
                    pause_max = min(0.25, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.05秒）
                    if pause_max - pause_min < 0.05:
                        pause_min = max(0.01, median - 0.025)
                        pause_max = min(0.25, median + 0.025)
                    learned_behavior["pre_down_pause"] = (pause_min, pause_max)
                
                post_down_list = [b.get("post_down_pause", 0.1) for b in slide_behaviors if b.get("post_down_pause")]
                if post_down_list:
                    median = safe_percentile(post_down_list, 0.5)
                    std = safe_std(post_down_list)
                    pause_min = max(0.01, median - std * 0.4)
                    pause_max = min(0.25, median + std * 0.4)
                    # 🔧 确保最小范围宽度（至少0.05秒）
                    if pause_max - pause_min < 0.05:
                        pause_min = max(0.01, median - 0.025)
                        pause_max = min(0.25, median + 0.025)
                    learned_behavior["post_down_pause"] = (pause_min, pause_max)

                server_wait_list = [b.get("server_judge_wait", 0) for b in slide_behaviors if b.get("server_judge_wait")]
                if server_wait_list:
                    median = safe_percentile(server_wait_list, 0.5)
                    std = safe_std(server_wait_list)
                    wait_min = max(0.8, median - max(std * 0.4, 0.3))
                    wait_max = min(15.0, median + max(std * 0.4, 0.3))
                    if wait_max - wait_min < 0.6:
                        wait_min = max(0.8, median - 0.3)
                        wait_max = min(15.0, median + 0.3)
                    learned_behavior["server_judge_wait"] = (wait_min, wait_max)

                logger.info(f"【{self.pure_user_id}】📚 成功学习{len(learned_behavior)}个滑动行为参数")
            
            # 基于完整轨迹数据的学习
            completion_usage_rate = 0
            avg_completion_steps = 0
            
            if len(history) > 0:
                # 计算补全使用率
                completion_used_count = sum(1 for record in history if record.get("completion_used", False))
                completion_usage_rate = completion_used_count / len(history)
                
                # 计算平均补全步数
                completion_steps_list = [record.get("completion_steps", 0) for record in history if record.get("completion_used", False)]
                if completion_steps_list:
                    avg_completion_steps = sum(completion_steps_list) / len(completion_steps_list)
            
            # 构建优化后的参数（新版结构）
            optimized_params = {
                # 新版参数（基于学习结果）
                "learned_overshoot_range": learned_overshoot,
                "learned_delay_range": learned_delay,
                "learned_curve_range": learned_curve,
                "learned_jitter_range": learned_jitter,
                "learned_steps_range": learned_steps,
                # 🎯 新增：学习到的滑动行为参数
                "learned_behavior": learned_behavior,
                # 旧版参数（保留兼容性）
                "total_steps_range": learned_steps,
                "base_delay_range": learned_delay,
                "jitter_x_range": [0, 1],
                "jitter_y_range": [0, 1],
                "slow_factor_range": [10, 15],
                "acceleration_phase": 1.0,
                "fast_phase": 1.0,
                "slow_start_ratio_base": learned_overshoot[0],
                # 学习统计
                "completion_usage_rate": completion_usage_rate,
                "avg_completion_steps": avg_completion_steps,
                "learning_enabled": True,
                "history_count": len(history),
                "learning_version": "2.0"  # 标记为新版学习算法
            }
            
            logger.info(f"【{self.pure_user_id}】基于{len(history)}条成功记录优化轨迹参数: 步数{optimized_params['total_steps_range']}, 延迟{optimized_params['base_delay_range']}")

            return optimized_params
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】优化轨迹参数失败: {e}")
            return self.trajectory_params
    
    def _get_cookies_after_success(self):
        """滑块验证成功后获取cookie"""
        try:
            logger.info(f"【{self.pure_user_id}】开始获取滑块验证成功后的页面cookie...")

            # 检查当前页面URL
            current_url = self.page.url
            logger.info(f"【{self.pure_user_id}】当前页面URL: {current_url}")

            # 检查页面标题
            page_title = self.page.title()
            logger.info(f"【{self.pure_user_id}】当前页面标题: {page_title}")

            # 滑块拦截页常在通过后把浏览器跳到 www.taobao.com，导致新的 _m_h5_tk 落在
            # .taobao.com 域；后续再去签 h5api.m.goofish.com 的接口就会被网关回 FAIL_SYS_ILLEGAL_ACCESS。
            # 主动回访一次 goofish 主域，让网关在 .goofish.com 域上重发 H5 token，再做快照。
            try:
                current_host = (urlparse(current_url).hostname or '').lower()
            except Exception:
                current_host = ''
            if 'goofish.com' not in current_host:
                try:
                    self.page.goto(
                        'https://www.goofish.com/',
                        wait_until='domcontentloaded',
                        timeout=8000,
                    )
                    time.sleep(1.5)
                    logger.info(
                        f"【{self.pure_user_id}】滑块通过后已回访 goofish 主域，"
                        f"等待 .goofish.com 域重新颁发 _m_h5_tk"
                    )
                except Exception as goto_e:
                    logger.warning(
                        f"【{self.pure_user_id}】回访 goofish 主域失败，仍按当前页 cookie 继续: {goto_e}"
                    )

            # 等待一下确保cookie完全更新
            time.sleep(1)

            new_cookies = self._snapshot_context_cookies(
                self.context,
                page=self.page,
                preferred_domain_suffixes=('goofish.com',),
            )

            if new_cookies:
                logger.info(f"【{self.pure_user_id}】滑块验证成功后已获取cookie，共{len(new_cookies)}个cookie")
                
                # 记录所有cookie的详细信息
                logger.info(f"【{self.pure_user_id}】获取到的所有cookie: {list(new_cookies.keys())}")
                
                # 单独记录x5相关cookie，便于排查风控链路
                x5_cookies = {}

                # 筛选出x5相关的cookies（包括x5sec, x5step等）
                for cookie_name, cookie_value in new_cookies.items():
                    cookie_name_lower = cookie_name.lower()
                    if cookie_name_lower.startswith('x5') or 'x5sec' in cookie_name_lower:
                        x5_cookies[cookie_name] = cookie_value
                        logger.info(f"【{self.pure_user_id}】x5相关cookie已获取: {cookie_name} = {cookie_value}")

                logger.info(f"【{self.pure_user_id}】找到{len(x5_cookies)}个x5相关cookies: {list(x5_cookies.keys())}")

                if x5_cookies:
                    logger.info(f"【{self.pure_user_id}】返回完整cookie集合，并保留x5相关cookie日志: {list(x5_cookies.keys())}")
                else:
                    logger.warning(f"【{self.pure_user_id}】未找到x5相关cookie")

                return new_cookies
            else:
                logger.warning(f"【{self.pure_user_id}】未获取到任何cookie")
                return None
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】获取滑块验证成功后的cookie失败: {str(e)}")
            return None
    
    def _save_cookies_to_file(self, cookies):
        """保存cookie到文件"""
        try:
            # 确保目录存在
            cookie_dir = f"slider_cookies/{self.user_id}"
            os.makedirs(cookie_dir, exist_ok=True)

            # 保存cookie到JSON文件
            cookie_file = f"{cookie_dir}/cookies_{int(time.time())}.json"
            with open(cookie_file, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)

            logger.info(f"【{self.pure_user_id}】Cookie已保存到文件: {cookie_file}")

        except Exception as e:
            logger.error(f"【{self.pure_user_id}】保存cookie到文件失败: {str(e)}")

    # 关键 Cookie 名称列表（用于判定"有意义的刷新"）
    _KEY_COOKIE_NAMES = {
        '_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 'unb', 'sgcookie',
        'uc1', 'uc3', 'uc4', 'csg', 'sn',
    }
    _PROTECTED_SESSION_COOKIE_FIELDS = (
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
    _REQUIRED_SESSION_COOKIE_FIELDS = (
        'unb',
        'sgcookie',
        'cookie2',
        '_m_h5_tk',
        '_m_h5_tk_enc',
        't',
    )
    _OBSERVED_SESSION_COOKIE_FIELDS = (
        'cna',
    )
    _IDENTITY_VERIFY_PENDING_COOKIE_FIELDS = (
        'ivActionType',
        'tmp0',
        'siv20',
        'last_u_xianyu_web',
    )
    _X5_COOKIE_PREFIX = 'x5'

    def _snapshot_context_cookies_via_cdp(self, context=None, page=None) -> Dict[str, str]:
        """通过 CDP 兜底抓取 Chromium 全量 Cookie，补齐 Playwright context.cookies() 可能遗漏的票据。"""
        current_context = context or self.context
        if not current_context:
            return {}

        probe_page = page
        if not probe_page:
            pages = self._get_context_pages(current_context)
            probe_page = pages[0] if pages else None
        if not probe_page:
            return {}

        session = None
        try:
            session = current_context.new_cdp_session(probe_page)
            try:
                session.send("Network.enable")
            except Exception:
                pass
            response = session.send("Network.getAllCookies") or {}
            raw_cookies = response.get("cookies") if isinstance(response, dict) else []
            merged = {}
            for item in raw_cookies or []:
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                merged[name] = str(item.get("value") or "")
            return merged
        except Exception as cdp_e:
            logger.debug(f"【{self.pure_user_id}】CDP Cookie 快照失败: {cdp_e}")
            return {}
        finally:
            if session:
                try:
                    session.detach()
                except Exception:
                    pass

    def _flatten_cookies_by_domain_preference(
        self,
        raw_cookies,
        preferred_domain_suffixes=None,
    ) -> Dict[str, str]:
        """将 [{name, value, domain, ...}] 压扁为 {name: value}。

        当 preferred_domain_suffixes 非空时，同名 Cookie 在多个域共存时优先取
        domain 命中后缀的版本；其它情况下保持原行为（列表顺序后者覆盖前者）。
        """
        if not raw_cookies:
            return {}
        if not preferred_domain_suffixes:
            return {c['name']: c['value'] for c in raw_cookies if c.get('name')}

        suffixes = tuple(s.lstrip('.').lower() for s in preferred_domain_suffixes if s)
        by_name: Dict[str, dict] = {}
        for c in raw_cookies:
            name = c.get('name')
            if not name:
                continue
            existing = by_name.get(name)
            if existing is None:
                by_name[name] = c
                continue
            existing_domain = (existing.get('domain') or '').lstrip('.').lower()
            new_domain = (c.get('domain') or '').lstrip('.').lower()
            existing_hit = any(existing_domain.endswith(s) for s in suffixes)
            new_hit = any(new_domain.endswith(s) for s in suffixes)
            if new_hit and not existing_hit:
                by_name[name] = c
            elif existing_hit and not new_hit:
                pass  # 保留首选域版本
            else:
                by_name[name] = c  # 都命中或都不命中：沿用原"后者覆盖"行为
        return {c['name']: c['value'] for c in by_name.values()}

    def _snapshot_context_cookies(
        self,
        context=None,
        page=None,
        preferred_domain_suffixes=None,
    ) -> Dict[str, str]:
        """快照浏览器上下文中的所有 Cookie，返回 {name: value} 字典。

        Args:
            preferred_domain_suffixes: 可选；同名 Cookie 跨域共存时优先选 domain
                命中这些后缀的版本（默认 None，行为不变）。仅 _get_cookies_after_success
                之类需要"按目标域取真值"的调用方使用，其它调用方保持默认。
        """
        try:
            current_context = context or self.context
            if not current_context:
                return {}

            playwright_cookies = {}
            try:
                raw = current_context.cookies()
                playwright_cookies = self._flatten_cookies_by_domain_preference(
                    raw, preferred_domain_suffixes
                )
            except Exception as playwright_e:
                logger.debug(f"【{self.pure_user_id}】Playwright Cookie 快照失败: {playwright_e}")

            cdp_cookies = self._snapshot_context_cookies_via_cdp(current_context, page=page)
            if not cdp_cookies:
                return playwright_cookies

            merged_cookies = dict(playwright_cookies)
            merged_cookies.update(cdp_cookies)

            extra_keys = sorted(set(cdp_cookies.keys()) - set(playwright_cookies.keys()))
            if extra_keys:
                protected_from_cdp = [
                    key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                    if key in extra_keys
                ]
                logger.info(
                    f"【{self.pure_user_id}】CDP Cookie 快照补充了 {len(extra_keys)} 个字段: "
                    f"{extra_keys[:12]}{' ...' if len(extra_keys) > 12 else ''}"
                )
                if protected_from_cdp:
                    logger.info(
                        f"【{self.pure_user_id}】CDP Cookie 快照补到了关键字段: {protected_from_cdp}"
                    )

            return merged_cookies
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】快照 Cookie 失败: {e}")
            return {}

    def _log_cookie_snapshot_integrity(self, cookies_dict: Dict[str, str], scene: str):
        """记录登录链路中的 Cookie 快照完整性，避免不完整快照静默通过。"""
        if not cookies_dict:
            logger.warning(f"【{self.pure_user_id}】{scene}Cookie快照为空")
            return

        missing_protected_fields = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        missing_required_fields = [
            key for key in self._REQUIRED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]

        if missing_protected_fields:
            logger.warning(
                f"【{self.pure_user_id}】{scene}Cookie快照完整性告警: "
                f"field_count={len(cookies_dict)}, "
                f"missing_protected_fields={missing_protected_fields}"
            )
        if missing_required_fields:
            logger.warning(
                f"【{self.pure_user_id}】{scene}Cookie快照核心字段不足: "
                f"field_count={len(cookies_dict)}, "
                f"missing_required_fields={missing_required_fields}"
            )

    def _detect_pending_identity_verification_cookie_state(self, cookies_dict: Dict[str, str]) -> List[str]:
        """识别“前端已登录但仍卡在二次身份校验态”的 Cookie 痕迹。"""
        if not cookies_dict:
            return []

        pending_markers = [
            key for key in self._IDENTITY_VERIFY_PENDING_COOKIE_FIELDS
            if cookies_dict.get(key)
        ]
        if not pending_markers:
            return []

        if cookies_dict.get('havana_lgc2_77'):
            return []

        missing_required_fields = [
            key for key in self._REQUIRED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if not missing_required_fields:
            return []

        return pending_markers

    def _resolve_pending_identity_verification_url(self, cookies_dict: Dict[str, str]) -> Optional[str]:
        """基于半登录态 Cookie 反查身份校验页面链接。"""
        if not cookies_dict:
            return None

        cookie_text = '; '.join(
            f"{key}={value}"
            for key, value in cookies_dict.items()
            if key and value is not None
        )
        if not cookie_text:
            return None

        try:
            verification_url = resolve_verification_url_from_cookie(
                cookie_text,
                proxy=self.proxy_config,
            )
            if verification_url:
                logger.info(
                    f"【{self.pure_user_id}】已根据半登录态Cookie反查到身份验证链接: {verification_url}"
                )
                return verification_url
        except Exception as resolve_err:
            logger.warning(
                f"【{self.pure_user_id}】根据半登录态Cookie反查身份验证链接失败: {resolve_err}"
            )

        return None

    def _is_password_login_scene(self) -> bool:
        return self.risk_trigger_scene in {'password_login', 'manual_password_refresh'}

    def _is_password_scene_success_sample(self, history_path: str, record: Optional[Dict[str, Any]] = None) -> bool:
        if not self._is_password_login_scene():
            return True

        parts = [os.path.basename(str(history_path or ""))]
        if isinstance(record, dict):
            parts.append(str(record.get("user_id") or ""))

        sample_text = " ".join(parts).lower()
        if not sample_text:
            return False

        if any(token in sample_text for token in ("cookie", "import_user_cookie", "ui_cookie")):
            return False

        return any(token in sample_text for token in ("password", "pwd"))

    def _handle_pending_identity_verification_state(
        self,
        context,
        fallback_page,
        cookies_dict: Dict[str, str],
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ):
        """处理“前端已登录但服务端仍要求二次身份校验”的半登录态。"""
        pending_identity_markers = self._detect_pending_identity_verification_cookie_state(cookies_dict)
        if not pending_identity_markers:
            return None

        logger.error(
            f"【{self.pure_user_id}】检测到前端已登录但仍处于二次身份校验态，"
            f"待确认Cookie标记: {pending_identity_markers}"
        )
        logger.error(
            f"【{self.pure_user_id}】该状态下通常不会下发完整业务会话Cookie，"
            f"例如 havana_lgc2_77 / x5secdata"
        )

        monitor_page = self._select_monitor_page(context, fallback_page) or fallback_page
        if monitor_page:
            try:
                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                if has_qr:
                    logger.warning(f"【{self.pure_user_id}】半登录态下检测到可见身份验证页，转入验证等待流程")
                    return self._process_verification_requirement(
                        context,
                        monitor_page,
                        qr_frame,
                        notification_callback,
                        notification_scene,
                    )
            except PasswordLoginVerificationError:
                raise
            except Exception as verify_probe_err:
                logger.warning(
                    f"【{self.pure_user_id}】半登录态复检身份验证页失败，准备尝试反查验证链接: {verify_probe_err}"
                )

        verification_url = self._resolve_pending_identity_verification_url(cookies_dict)
        if verification_url and context:
            verify_page = None
            try:
                verify_page = context.new_page()
                logger.info(f"【{self.pure_user_id}】打开反查到的身份验证链接...")
                verify_page.goto(verification_url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)

                recovered_slider_detected = self._page_has_slider(verify_page)
                recovered_purecaptcha_detected = any(
                    token in verification_url.lower()
                    for token in ('purecaptcha=', 'action=captcha', 'punish?', 'x5step=2')
                )
                if recovered_slider_detected or recovered_purecaptcha_detected:
                    logger.info(
                        f"【{self.pure_user_id}】半登录态恢复页命中"
                        f"{'滑块' if recovered_slider_detected else 'pureCaptcha'}特征，优先尝试自动续解"
                    )
                    solved = self._attempt_solve_slider_on_page(verify_page)
                    if solved:
                        login_success, active_page, _ = self._probe_context_login_success(context, verify_page)
                        if login_success:
                            logger.success(f"【{self.pure_user_id}】✅ 半登录态恢复页滑块续解后已确认登录成功")
                            return self._finalize_logged_in_cookies(
                                context,
                                active_page or verify_page,
                                scene="半登录态恢复页自动续解",
                                notification_callback=notification_callback,
                                notification_scene=notification_scene,
                            )
                        logger.warning(
                            f"【{self.pure_user_id}】半登录态恢复页滑块已处理，但暂未确认登录成功，继续走验证识别流程"
                        )
                    else:
                        logger.warning(
                            f"【{self.pure_user_id}】半登录态恢复页自动续解未成功，继续判断是否需要人工验证"
                        )

                verification_type = self._detect_verification_type(verify_page)
                if verification_type == 'unknown' and 'identity_verify' in verification_url.lower():
                    verification_type = 'face_verify'

                verification_screenshot = self._capture_verification_screenshot(verify_page)
                verification_wrapper = VerificationFrameWrapper(
                    verify_page,
                    verification_type=verification_type,
                    verify_url=verification_url,
                    screenshot_path=verification_screenshot,
                )
                logger.warning(f"【{self.pure_user_id}】已根据半登录态Cookie恢复身份验证页面，转入验证等待流程")
                return self._process_verification_requirement(
                    context,
                    verify_page,
                    verification_wrapper,
                    notification_callback,
                    notification_scene,
                )
            except Exception as open_verify_err:
                logger.warning(
                    f"【{self.pure_user_id}】打开反查到的身份验证链接失败: {open_verify_err}"
                )
                try:
                    if verify_page:
                        verify_page.close()
                except Exception:
                    pass

            self._notify_verification_required(
                'qr_verify',
                verification_url,
                None,
                notification_callback,
                notification_scene,
            )
            return self._fail_login(
                "检测到二次身份校验未完成，请按通知中的验证链接完成验证后重试"
            )

        missing_required_fields = [
            key for key in self._REQUIRED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if not missing_required_fields:
            fallback_cookies = dict(cookies_dict)
            cleared_pending_markers = [
                key for key in self._IDENTITY_VERIFY_PENDING_COOKIE_FIELDS
                if fallback_cookies.pop(key, None) is not None
            ]
            logger.warning(
                f"【{self.pure_user_id}】半登录态未恢复出新的验证页，但当前核心会话字段已齐全；"
                f"回退为受保护Cookie交接，待上层合并补齐缺失字段。"
                f"已清理待确认标记: {cleared_pending_markers}"
            )
            return fallback_cookies

        return self._fail_login(
            "检测到二次身份校验未完成，当前仅形成前端登录态，服务端会话未建立"
        )

    def _extract_set_cookie_updates_from_playwright_response(self, response) -> Dict[str, str]:
        """从 Playwright Response 中提取 Set-Cookie，避免关键票据已下发但未沉淀到 context.cookies。"""
        if not response:
            return {}

        set_cookie_values = []
        try:
            if hasattr(response, 'header_values'):
                set_cookie_values = response.header_values('set-cookie') or []
        except Exception:
            set_cookie_values = []

        if not set_cookie_values:
            try:
                if hasattr(response, 'header_value'):
                    raw_value = response.header_value('set-cookie')
                    if raw_value:
                        set_cookie_values = [item.strip() for item in str(raw_value).splitlines() if item.strip()]
            except Exception:
                set_cookie_values = []

        if not set_cookie_values:
            try:
                headers = response.headers() if callable(getattr(response, 'headers', None)) else (response.headers or {})
                raw_value = headers.get('set-cookie') or headers.get('Set-Cookie')
                if isinstance(raw_value, list):
                    set_cookie_values = [str(item).strip() for item in raw_value if str(item).strip()]
                elif raw_value:
                    set_cookie_values = [item.strip() for item in str(raw_value).splitlines() if item.strip()]
            except Exception:
                set_cookie_values = []

        updates = {}
        for cookie_line in set_cookie_values:
            first_part = str(cookie_line).split(';', 1)[0].strip()
            if not first_part or '=' not in first_part:
                continue
            name, value = first_part.split('=', 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            updates[name] = value
        return updates

    def _stabilize_logged_in_context_cookies(self, context, page=None, scene: str = "登录完成后") -> Dict[str, str]:
        """登录成功后补做一次轻量页面稳定化，尽量把延迟下发的会话 Cookie 补齐。"""
        best_cookies = self._snapshot_context_cookies(context, page=page)
        best_missing = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not best_cookies.get(key)
        ]
        self._log_cookie_snapshot_integrity(best_cookies, f"{scene}初始快照")
        if not best_missing:
            return best_cookies

        work_page = page
        if not work_page:
            pages = self._get_context_pages(context)
            work_page = pages[0] if pages else None
        if not work_page:
            return best_cookies

        actions = [
            ("reload_current", None),
            ("goto_home", "https://www.goofish.com/"),
            ("goto_im", "https://www.goofish.com/im"),
        ]

        logger.info(
            f"【{self.pure_user_id}】{scene}检测到关键Cookie缺失，开始轻量稳定化: "
            f"missing_protected_fields={best_missing}"
        )

        for action_name, target_url in actions:
            try:
                if target_url:
                    logger.info(f"【{self.pure_user_id}】{scene}稳定化动作: {action_name} -> {target_url}")
                    work_page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                else:
                    logger.info(f"【{self.pure_user_id}】{scene}稳定化动作: {action_name}")
                    work_page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception as nav_e:
                logger.warning(f"【{self.pure_user_id}】{scene}稳定化动作 {action_name} 失败: {nav_e}")
                continue

            time.sleep(1.0)
            try:
                work_page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            time.sleep(0.5)

            current_cookies = self._snapshot_context_cookies(context, page=work_page)
            current_missing = [
                key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                if not current_cookies.get(key)
            ]
            self._log_cookie_snapshot_integrity(current_cookies, f"{scene}稳定化[{action_name}]")

            if current_cookies and len(current_missing) < len(best_missing):
                best_cookies = current_cookies
                best_missing = current_missing
                logger.info(
                    f"【{self.pure_user_id}】{scene}稳定化后关键Cookie缺失减少到 {len(best_missing)} 个: {best_missing}"
                )

            if not best_missing:
                break

        if best_missing:
            warmed_cookies = self._perform_browser_cookie_warmup_probes(
                context,
                work_page,
                scene=scene,
                initial_cookies=best_cookies,
            )
            warmed_missing = [
                key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                if not warmed_cookies.get(key)
            ]
            if warmed_cookies and len(warmed_missing) < len(best_missing):
                best_cookies = warmed_cookies
                best_missing = warmed_missing

        return best_cookies

    def _build_browser_mtop_probe_requests(self, cookies_dict: Dict[str, str]) -> List[Dict[str, str]]:
        """构造登录成功后的浏览器侧业务预热探测请求。"""
        token = str((cookies_dict or {}).get('_m_h5_tk') or '').split('_')[0]
        user_id = str((cookies_dict or {}).get('unb') or '').strip()
        if not token or not user_id:
            return []

        common_params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }
        probes: List[Dict[str, str]] = []

        token_ts = str(int(time.time() * 1000))
        token_data = json.dumps(
            {
                "appKey": "444e9908a51d1cb236a27862abc769c9",
                "deviceId": generate_cookie_verification_device_id(user_id),
            },
            separators=(',', ':'),
            ensure_ascii=False,
        )
        token_params = dict(common_params)
        token_params.update({
            't': token_ts,
            'api': 'mtop.taobao.idlemessage.pc.login.token',
            'dangerouslySetWindvaneParams': '%5Bobject%20Object%5D',
            'smToken': 'token',
            'queryToken': 'sm',
            'sm': 'sm',
            'sign': build_cookie_verification_sign(token_ts, token, token_data),
        })
        probes.append({
            'name': 'login_token_fetch',
            'url': (
                "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/?"
                + urlencode(token_params)
            ),
            'body': f"data={quote_plus(token_data)}",
        })

        user_ts = str(int(time.time() * 1000))
        user_data = '{}'
        user_params = dict(common_params)
        user_params.update({
            't': user_ts,
            'api': 'mtop.taobao.idlemessage.pc.loginuser.get',
            'sign': build_cookie_verification_sign(user_ts, token, user_data),
        })
        probes.append({
            'name': 'login_user_fetch',
            'url': (
                "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.loginuser.get/1.0/?"
                + urlencode(user_params)
            ),
            'body': f"data={quote_plus(user_data)}",
        })

        return probes

    def _perform_browser_cookie_warmup_probes(
        self,
        context,
        page,
        scene: str,
        initial_cookies: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """在浏览器上下文中主动探测业务接口，尝试逼出延迟下发的关键 Cookie。"""
        if not context or not page:
            return initial_cookies or {}

        self.last_browser_cookie_warmup_verification_hint = None
        best_cookies = dict(initial_cookies or self._snapshot_context_cookies(context, page=page))
        best_missing = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not best_cookies.get(key)
        ]
        probe_requests = self._build_browser_mtop_probe_requests(best_cookies)
        if not probe_requests:
            logger.info(f"【{self.pure_user_id}】{scene}浏览器业务预热跳过：缺少 _m_h5_tk 或 unb")
            return best_cookies

        logger.info(
            f"【{self.pure_user_id}】{scene}标准稳定化后仍缺少关键Cookie，开始浏览器业务预热: "
            f"missing_protected_fields={best_missing}"
        )

        for probe in probe_requests:
            probe_name = probe.get('name') or 'unknown_probe'
            probe_result = {}
            try:
                logger.info(f"【{self.pure_user_id}】{scene}浏览器业务预热探测: {probe_name}")
                probe_result = self._execute_browser_cookie_warmup_probe(context, page, probe)
                if isinstance(probe_result, dict):
                    summary = str(
                        probe_result.get('error')
                        or probe_result.get('text')
                        or ''
                    ).replace('\n', ' ')[:220]
                    logger.info(
                        f"【{self.pure_user_id}】{scene}浏览器业务预热结果[{probe_name}]: "
                        f"status={probe_result.get('status')} ok={probe_result.get('ok')} summary={summary}"
                    )
                    response_cookie_updates = probe_result.get('set_cookie_updates') or {}
                    if response_cookie_updates:
                        logger.info(
                            f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]响应补充Cookie: "
                            f"{sorted(response_cookie_updates.keys())}"
                        )
                    if probe_result.get('timed_out'):
                        logger.warning(
                            f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]超时中止，"
                            f"timeout_ms={probe_result.get('timeout_ms')}"
                        )
                    if (
                        "FAIL_SYS_SESSION_EXPIRED" in summary or
                        "FAIL_SYS_USER_VALIDATE" in summary
                    ):
                        self.last_browser_cookie_warmup_session_unready = True
                        logger.warning(
                            f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]仍提示服务端Session未就绪"
                        )
                    verification_hint = self._extract_browser_cookie_warmup_verification_hint(
                        probe_name,
                        probe_result,
                    )
                    if verification_hint:
                        self.last_browser_cookie_warmup_verification_hint = verification_hint
                        logger.warning(
                            f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]返回了后续验证提示: "
                            f"{verification_hint.get('verification_url')}"
                        )
            except Exception as probe_e:
                logger.warning(f"【{self.pure_user_id}】{scene}浏览器业务预热[{probe_name}]失败: {probe_e}")
                continue

            time.sleep(1.0)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            time.sleep(0.5)

            current_cookies = self._snapshot_context_cookies(context, page=page)
            response_cookie_updates = probe_result.get('set_cookie_updates') or {}
            if response_cookie_updates:
                current_cookies = dict(current_cookies or {})
                for cookie_name, cookie_value in response_cookie_updates.items():
                    if cookie_name and cookie_value and not current_cookies.get(cookie_name):
                        current_cookies[cookie_name] = cookie_value
            current_missing = [
                key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                if not current_cookies.get(key)
            ]
            self._log_cookie_snapshot_integrity(current_cookies, f"{scene}业务预热[{probe_name}]")

            if current_cookies and len(current_missing) < len(best_missing):
                best_cookies = current_cookies
                best_missing = current_missing
                logger.info(
                    f"【{self.pure_user_id}】{scene}浏览器业务预热后关键Cookie缺失减少到 "
                    f"{len(best_missing)} 个: {best_missing}"
                )

            if not best_missing:
                break

        if best_cookies.get('havana_lgc2_77'):
            self.last_browser_cookie_warmup_verification_hint = None

        return best_cookies

    def _extract_browser_cookie_warmup_verification_hint(
        self,
        probe_name: str,
        probe_result: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(probe_result, dict):
            return None

        raw_text = str(probe_result.get('text') or '').strip()
        if not raw_text:
            return None

        try:
            payload = json.loads(raw_text)
        except Exception:
            return None

        ret_items = payload.get('ret')
        if isinstance(ret_items, list):
            ret_values = [str(item) for item in ret_items if item is not None]
        elif ret_items is None:
            ret_values = []
        else:
            ret_values = [str(ret_items)]
        ret_summary = " ".join(ret_values)

        data_payload = payload.get('data')
        if not isinstance(data_payload, dict):
            data_payload = {}

        verification_url = str(data_payload.get('url') or '').strip()
        verification_url_lower = verification_url.lower()
        ret_hit = (
            'FAIL_SYS_USER_VALIDATE' in ret_summary or
            'FAIL_SYS_SESSION_EXPIRED' in ret_summary
        )
        url_hit = any(
            token in verification_url_lower
            for token in (
                'punish',
                'x5step=2',
                'action=captcha',
                'purecaptcha',
                'identity_verify',
                '/iv/',
                'qrcode',
                'scan',
            )
        )
        if not verification_url or not (ret_hit or url_hit):
            return None

        verification_type = 'unknown'
        if 'identity_verify' in verification_url_lower or '/iv/' in verification_url_lower:
            verification_type = 'face_verify'
        elif 'qrcode' in verification_url_lower or 'scan' in verification_url_lower:
            verification_type = 'qr_verify'

        return {
            'source': 'browser_cookie_warmup',
            'probe_name': probe_name or 'unknown_probe',
            'verification_url': verification_url,
            'verification_type': verification_type,
            'ret': ret_values,
            'summary': ret_summary,
        }

    def _infer_browser_cookie_warmup_risk_trigger_scene(
        self,
        verification_hint: Optional[Dict[str, Any]],
        verification_url: str,
    ) -> Optional[str]:
        if not isinstance(verification_hint, dict):
            return None

        source = str(verification_hint.get('source') or '').strip().lower()
        if source != 'browser_cookie_warmup':
            return None

        probe_name = str(verification_hint.get('probe_name') or '').strip().lower()
        verification_url_lower = str(verification_url or '').strip().lower()
        if (
            probe_name == 'login_token_fetch' or
            'mtop.taobao.idlemessage.pc.login.token' in verification_url_lower
        ):
            return 'token_refresh'

        return None

    def _execute_browser_cookie_warmup_probe(
        self,
        context,
        page,
        probe: Dict[str, str],
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not probe:
            return {}

        effective_timeout_ms = timeout_ms
        if effective_timeout_ms is None:
            effective_timeout_ms = getattr(self, 'browser_cookie_warmup_probe_timeout_ms', 5000) or 5000
        try:
            effective_timeout_ms = max(1000, int(effective_timeout_ms))
        except Exception:
            effective_timeout_ms = 5000

        request_headers = {
            'accept': 'application/json, text/plain, */*',
            'content-type': 'application/x-www-form-urlencoded',
        }

        request_context = getattr(context, 'request', None) if context else None
        if request_context and hasattr(request_context, 'post'):
            try:
                response = request_context.post(
                    probe['url'],
                    data=probe.get('body') or '',
                    headers=request_headers,
                    timeout=effective_timeout_ms,
                )
                response_text = ''
                try:
                    response_text = str(response.text() or '')
                except Exception as body_err:
                    response_text = ''
                    logger.debug(
                        f"【{self.pure_user_id}】浏览器业务预热响应读取失败，继续使用已有Cookie快照: {body_err}"
                    )

                result = {
                    'ok': bool(getattr(response, 'ok', False)),
                    'status': int(getattr(response, 'status', 0) or 0),
                    'text': response_text[:600],
                    'timed_out': False,
                    'timeout_ms': effective_timeout_ms,
                }
                response_cookie_updates = self._extract_set_cookie_updates_from_playwright_response(response)
                if response_cookie_updates:
                    result['set_cookie_updates'] = response_cookie_updates
                return result
            except Exception as request_err:
                error_text = str(request_err)
                error_name = type(request_err).__name__
                timed_out = (
                    'Timeout' in error_name or
                    'timed out' in error_text.lower() or
                    'timeout' in error_text.lower()
                )
                if timed_out or not page:
                    return {
                        'ok': False,
                        'status': 0,
                        'error': error_text,
                        'timed_out': timed_out,
                        'timeout_ms': effective_timeout_ms,
                    }

                logger.debug(
                    f"【{self.pure_user_id}】浏览器业务预热 request.post 失败，回退到页面内 fetch: {request_err}"
                )

        if not page:
            return {}

        return page.evaluate(
            """
            async ({ url, body, timeoutMs }) => {
                let didTimeout = false;
                const controller = new AbortController();
                const timer = setTimeout(() => {
                    didTimeout = true;
                    controller.abort();
                }, timeoutMs);
                try {
                    const resp = await fetch(url, {
                        method: 'POST',
                        credentials: 'include',
                        cache: 'no-store',
                        headers: {
                            'accept': 'application/json, text/plain, */*',
                            'content-type': 'application/x-www-form-urlencoded',
                        },
                        body,
                        signal: controller.signal,
                    });
                    const text = await resp.text();
                    return {
                        ok: resp.ok,
                        status: resp.status,
                        text: text.slice(0, 600),
                        timed_out: false,
                        timeout_ms: timeoutMs,
                    };
                } catch (error) {
                    return {
                        ok: false,
                        status: 0,
                        error: String((error && error.message) || error || ''),
                        timed_out: didTimeout || String((error && error.name) || '') === 'AbortError',
                        timeout_ms: timeoutMs,
                    };
                } finally {
                    clearTimeout(timer);
                }
            }
            """,
            {
                "url": probe['url'],
                "body": probe['body'],
                "timeoutMs": effective_timeout_ms,
            },
        )

    def _consume_browser_cookie_warmup_verification_hint(
        self,
        context,
        fallback_page,
        cookies_dict: Dict[str, str],
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ):
        verification_hint = getattr(self, 'last_browser_cookie_warmup_verification_hint', None) or {}
        verification_url = str(verification_hint.get('verification_url') or '').strip()
        if not verification_url or not context:
            return None

        if cookies_dict.get('havana_lgc2_77'):
            return None

        logger.warning(
            f"【{self.pure_user_id}】检测到浏览器业务预热返回后续验证入口，"
            f"当前 havana_lgc2_77 仍缺失，转入验证接管: {verification_url}"
        )

        verify_page = None
        override_risk_trigger_scene = self._infer_browser_cookie_warmup_risk_trigger_scene(
            verification_hint,
            verification_url,
        )
        previous_risk_trigger_scene = getattr(self, 'risk_trigger_scene', None)
        try:
            if override_risk_trigger_scene:
                if override_risk_trigger_scene != previous_risk_trigger_scene:
                    logger.info(
                        f"【{self.pure_user_id}】浏览器业务预热验证页临时切换 risk_trigger_scene="
                        f"{override_risk_trigger_scene}（from {previous_risk_trigger_scene or 'unset'}）"
                    )
                self.risk_trigger_scene = override_risk_trigger_scene

            verify_page = context.new_page()
            verify_page.goto(verification_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            recovered_slider_detected = self._page_has_slider(verify_page)
            recovered_purecaptcha_detected = any(
                token in verification_url.lower()
                for token in ('purecaptcha=', 'action=captcha', 'punish?', 'x5step=2')
            )
            if recovered_slider_detected or recovered_purecaptcha_detected:
                logger.info(
                    f"【{self.pure_user_id}】浏览器业务预热返回的验证页命中"
                    f"{'滑块' if recovered_slider_detected else 'pureCaptcha'}特征，优先尝试自动续解"
                )
                solved = self._attempt_solve_slider_on_page(verify_page)
                if solved:
                    login_success, active_page, _ = self._probe_context_login_success(context, verify_page)
                    if login_success:
                        logger.success(f"【{self.pure_user_id}】✅ 浏览器业务预热验证页自动续解后已确认登录成功")
                        return self._finalize_logged_in_cookies(
                            context,
                            active_page or verify_page,
                            scene="浏览器业务预热验证页自动续解",
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                        )

            verification_type = str(verification_hint.get('verification_type') or '').strip() or self._detect_verification_type(verify_page)
            if verification_type == 'unknown' and 'identity_verify' in verification_url.lower():
                verification_type = 'face_verify'

            verification_screenshot = self._capture_verification_screenshot(verify_page)
            verification_wrapper = VerificationFrameWrapper(
                verify_page,
                verification_type=verification_type,
                verify_url=verification_url,
                screenshot_path=verification_screenshot,
            )
            return self._process_verification_requirement(
                context,
                verify_page,
                verification_wrapper,
                notification_callback,
                notification_scene,
            )
        except Exception as open_verify_err:
            logger.warning(
                f"【{self.pure_user_id}】打开浏览器业务预热返回的验证入口失败: {open_verify_err}"
            )
            try:
                if verify_page:
                    verify_page.close()
            except Exception:
                pass
        finally:
            if override_risk_trigger_scene:
                self.risk_trigger_scene = previous_risk_trigger_scene
        return None

    def _safe_page_url(self, page) -> str:
        try:
            return str(page.url or '')
        except Exception:
            return ''

    def _safe_page_title(self, page) -> str:
        try:
            return str(page.title() or '')
        except Exception:
            return ''

    def _get_context_pages(self, context=None, fallback_page=None) -> List[Any]:
        pages = []
        seen = set()
        candidates = []

        current_context = context or self.context
        if current_context:
            try:
                candidates.extend(list(current_context.pages))
            except Exception:
                pass

        if fallback_page:
            candidates.append(fallback_page)

        for candidate in candidates:
            if not candidate:
                continue
            candidate_id = id(candidate)
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            try:
                if candidate.is_closed():
                    continue
            except Exception:
                pass
            pages.append(candidate)

        return pages

    def _has_completed_login_cookies(self, cookie_dict: Dict[str, str]) -> bool:
        if not cookie_dict.get('unb'):
            return False

        companion_keys = (
            'cookie2', 'havana_lgc2_77', '_tb_token_', 'sgcookie',
            '_m_h5_tk', '_m_h5_tk_enc', 't'
        )
        return any(cookie_dict.get(key) for key in companion_keys)

    def _is_logged_in_url(self, url: str) -> bool:
        current_url = str(url or '')
        if not current_url:
            return False

        current_url_lower = current_url.lower()

        if self._looks_like_verification_url(current_url_lower):
            return False

        if 'www.goofish.com/im' in current_url_lower:
            return True

        return (
            'goofish.com' in current_url_lower and
            'passport.goofish.com' not in current_url_lower and
            'mini_login' not in current_url_lower and
            '/iv/' not in current_url_lower
        )

    def _looks_like_verification_url(self, url: str) -> bool:
        current_url = str(url or '').lower()
        if not current_url:
            return False

        verification_tokens = (
            'passport.goofish.com',
            'mini_login',
            'identity_verify',
            '/iv/',
            'qrcode',
            'scan',
            'verify',
            'punish',
            'x5step=2',
            'action=captcha',
            'purecaptcha',
        )
        return any(token in current_url for token in verification_tokens)

    def _page_has_keep_login_prompt(self, page) -> bool:
        try:
            prompt_selectors = [
                'text=保持登录',
                'text=不保持',
            ]
            for selector in prompt_selectors:
                try:
                    element = page.query_selector(selector)
                    if element and element.is_visible():
                        return True
                except Exception as selector_error:
                    _mark_detached_runtime(selector_error)
                    continue
        except Exception:
            pass
        return False

    def _get_password_login_selectors(self) -> Dict[str, List[str]]:
        return {
            'account': [
                '#fm-login-id',
                'input[name="fm-login-id"]',
                'input[placeholder*="手机号"]',
                'input[placeholder*="手机"]',
                'input[placeholder*="邮箱"]',
                'input[placeholder*="账号"]',
                '.fm-login-id',
                '#J_LoginForm input[type="text"]',
                '#TPL_username_1',
            ],
            'password': [
                '#fm-login-password',
                'input[name="fm-login-password"]',
                'input[type="password"]',
                'input[placeholder*="密码"]',
                '#TPL_password_1',
            ],
            'submit': [
                'button.password-login',
                '.fm-button.fm-submit.password-login',
                '.password-login',
                'button.fm-submit',
                'text=登录',
            ],
            'tab': [
                'a.password-login-tab-item',
                '.password-login-tab-item',
                'text=密码登录',
                'text=账号密码登录',
            ],
            'agreement': [
                '#fm-agreement-checkbox',
                'input[type="checkbox"]',
            ],
        }

    def _query_first_visible(self, frame, selectors: List[str]):
        if not frame:
            return None, None

        for selector in selectors:
            try:
                element = frame.query_selector(selector)
                if element and element.is_visible():
                    return element, selector
            except Exception:
                continue

        return None, None

    def _probe_login_form_state(self, frame) -> Dict[str, Any]:
        """探测当前 frame 是否具备真正可交互的账密登录表单。"""
        if not frame:
            return {
                'is_login_form': False,
                'probe_type': 'missing',
                'matched_selector': None,
                'matched_text': None,
            }

        selectors = self._get_password_login_selectors()
        account_input, account_selector = self._query_first_visible(frame, selectors['account'])
        if account_input:
            return {
                'is_login_form': True,
                'probe_type': 'account_input',
                'matched_selector': account_selector,
                'matched_text': None,
            }

        password_input, password_selector = self._query_first_visible(frame, selectors['password'])
        if password_input:
            return {
                'is_login_form': True,
                'probe_type': 'password_input',
                'matched_selector': password_selector,
                'matched_text': None,
            }

        password_tab, tab_selector = self._query_first_visible(frame, selectors['tab'])
        submit_button, submit_selector = self._query_first_visible(frame, selectors['submit'])

        submit_text = None
        if submit_button:
            try:
                submit_text = ' '.join((submit_button.inner_text() or '').split())
            except Exception:
                submit_text = None

        if password_tab and submit_button:
            return {
                'is_login_form': True,
                'probe_type': 'password_tab_plus_submit',
                'matched_selector': f"{tab_selector} + {submit_selector}",
                'matched_text': submit_text,
            }

        if submit_button:
            probe_type = 'submit_only'
            submit_text_value = submit_text or ''
            # “text=登录” 在主页面/弹窗遮罩里太宽泛，不能当成快速进入；
            # 只有按钮自身文案明确包含“快速进入/继续/去登录/去看看”等免密直达语义时才自动点击。
            if any(keyword in submit_text_value for keyword in ('快速进入', '进入', '继续', '去登录', '去看看')):
                probe_type = 'direct_enter_like'
            return {
                'is_login_form': False,
                'probe_type': probe_type,
                'matched_selector': submit_selector,
                'matched_text': submit_text,
            }

        if password_tab:
            return {
                'is_login_form': False,
                'probe_type': 'tab_only',
                'matched_selector': tab_selector,
                'matched_text': None,
            }

        return {
            'is_login_form': False,
            'probe_type': 'none',
            'matched_selector': None,
            'matched_text': None,
        }

    def _find_login_form_with_retry(self, page, timeout_seconds: float = 8.0,
                                    poll_interval: float = 1.0):
        if not page:
            return None, False, None

        deadline = time.time() + max(timeout_seconds, 0.0)
        attempt = 0
        last_non_form_probe = None

        while True:
            attempt += 1
            search_frames = [('主页面', page)]
            try:
                for idx, frame in enumerate(page.frames):
                    if frame == page.main_frame:
                        continue
                    search_frames.append((f'Frame {idx}', frame))
            except Exception:
                pass

            for frame_label, frame in search_frames:
                probe_info = self._probe_login_form_state(frame)
                if probe_info.get('is_login_form'):
                    matched_selector = probe_info.get('matched_selector')
                    probe_type = probe_info.get('probe_type')
                    probe_text = probe_info.get('matched_text')
                    probe_note = f" [{probe_text}]" if probe_text else ""
                    logger.info(
                        f"【{self.pure_user_id}】✓ 第{attempt}次探测在{frame_label}找到登录表单({probe_type}): "
                        f"{matched_selector}{probe_note}"
                    )
                    return frame, True, matched_selector

                if probe_info.get('probe_type') not in {'missing', 'none'}:
                    last_non_form_probe = {
                        'frame_label': frame_label,
                        'attempt': attempt,
                        **probe_info,
                    }

            if time.time() >= deadline:
                break

            time.sleep(max(poll_interval, 0.1))

        if last_non_form_probe:
            probe_text = last_non_form_probe.get('matched_text')
            probe_note = f" [{probe_text}]" if probe_text else ""
            logger.warning(
                f"【{self.pure_user_id}】登录表单探测超时，最近一次仅命中非表单态"
                f"({last_non_form_probe.get('probe_type')})，位置={last_non_form_probe.get('frame_label')}，"
                f"选择器={last_non_form_probe.get('matched_selector')}{probe_note}"
            )
        logger.warning(
            f"【{self.pure_user_id}】在 {timeout_seconds:.1f}s 内未探测到登录表单"
        )
        return None, False, None

    def _find_direct_enter_candidate(self, page):
        """查找普通扫码/免密页上的“快速进入/继续/去登录”等直接进入按钮。"""
        if not page:
            return None, None, None

        # 优先检查 iframe。普通登录页的“快速进入”通常在 alibaba-login-box/mini_login iframe 内；
        # 主页面上的 text=登录 很容易被外层弹窗遮罩拦截，不能优先点击。
        search_frames = []
        try:
            for idx, frame in enumerate(page.frames):
                if frame == page.main_frame:
                    continue
                search_frames.append((f'Frame {idx}', frame))
        except Exception:
            pass
        search_frames.append(('主页面', page))

        candidates = []
        for frame_label, frame in search_frames:
            probe_info = self._probe_login_form_state(frame)
            if probe_info.get('probe_type') != 'direct_enter_like':
                continue
            selector = probe_info.get('matched_selector')
            if not selector:
                continue
            element, matched_selector = self._query_first_visible(frame, [selector])
            if not element:
                continue
            matched_text = probe_info.get('matched_text') or ''
            score = 0
            if frame_label != '主页面':
                score += 10
            if '快速进入' in matched_text:
                score += 20
            elif any(keyword in matched_text for keyword in ('进入', '继续', '去登录', '去看看')):
                score += 8
            candidates.append((score, frame, element, {
                'frame_label': frame_label,
                'matched_selector': matched_selector,
                'matched_text': matched_text or None,
            }))

        if not candidates:
            return None, None, None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, frame, element, probe_info = candidates[0]
        return frame, element, probe_info

    def _click_direct_enter_if_present(self, page, context=None) -> Tuple[bool, Any]:
        """普通登录页命中“快速进入”时先自动点击，再探测是否已登录。"""
        frame, element, probe_info = self._find_direct_enter_candidate(page)
        if not element:
            return False, None

        probe_text = probe_info.get('matched_text') if probe_info else None
        probe_note = f" [{probe_text}]" if probe_text else ""
        logger.info(
            f"【{self.pure_user_id}】检测到普通登录页直接进入按钮，自动点击: "
            f"{probe_info.get('frame_label') if probe_info else '未知位置'} "
            f"{probe_info.get('matched_selector') if probe_info else ''}{probe_note}"
        )
        try:
            try:
                element.click(timeout=5000)
            except TypeError:
                element.click()
        except Exception as click_e:
            logger.warning(f"【{self.pure_user_id}】点击普通登录页直接进入按钮失败，尝试JS点击兜底: {click_e}")
            try:
                element.evaluate("el => el.click()")
            except Exception as js_click_e:
                logger.warning(f"【{self.pure_user_id}】JS点击普通登录页直接进入按钮也失败: {js_click_e}")
                return False, None
        time.sleep(3)

        login_success = False
        active_page = page
        try:
            if context:
                login_success, active_page, _ = self._probe_context_login_success(context, page)
            else:
                login_success = self._check_login_success_by_element(page)
        except Exception as probe_e:
            logger.debug(f"【{self.pure_user_id}】点击快速进入后探测登录态失败: {probe_e}")

        if login_success:
            logger.success(f"【{self.pure_user_id}】✅ 点击快速进入后登录态已确认")
        else:
            logger.warning(f"【{self.pure_user_id}】点击快速进入后仍未确认登录态，继续后续验证/扫码流程")
        return True, active_page or page

    def _clear_page_storage_state(self, context=None, fallback_page=None) -> int:
        cleared_pages = 0
        for candidate in self._get_context_pages(context, fallback_page):
            try:
                candidate.evaluate(
                    "() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }"
                )
                cleared_pages += 1
            except Exception:
                continue
        return cleared_pages

    def _prepare_login_page_after_cleanup(self, context, page, *, clear_storage: bool = False,
                                          reopen_fresh_page: bool = False,
                                          timeout_seconds: float = 8.0):
        if context:
            context.clear_cookies()

        if clear_storage:
            cleared_pages = self._clear_page_storage_state(context, page)
            logger.info(f"【{self.pure_user_id}】已清理 {cleared_pages} 个页面的本地存储")

        active_page = page
        active_page.goto("https://www.goofish.com/im", wait_until="domcontentloaded", timeout=30000)
        time.sleep(1)
        login_frame, found_login_form, matched_selector = self._find_login_form_with_retry(
            active_page,
            timeout_seconds=timeout_seconds,
            poll_interval=1.0,
        )
        if found_login_form:
            return active_page, login_frame, True, matched_selector, False

        if reopen_fresh_page and context:
            try:
                fresh_page = context.new_page()
                fresh_page.goto("https://www.goofish.com/im", wait_until="domcontentloaded", timeout=30000)
                time.sleep(1)
                login_frame, found_login_form, matched_selector = self._find_login_form_with_retry(
                    fresh_page,
                    timeout_seconds=timeout_seconds,
                    poll_interval=1.0,
                )
                if found_login_form:
                    logger.info(f"【{self.pure_user_id}】✓ 新建页面后找到登录表单")
                    return fresh_page, login_frame, True, matched_selector, True
                try:
                    fresh_page.close()
                except Exception:
                    pass
            except Exception as fresh_page_error:
                logger.warning(f"【{self.pure_user_id}】新建页面重新探测登录表单失败: {fresh_page_error}")

        return active_page, None, False, None, False

    def _page_has_login_form(self, page) -> bool:
        if not page:
            return False

        frames_to_check = [page]
        try:
            frames_to_check.extend(list(page.frames))
        except Exception:
            pass

        for frame in frames_to_check:
            try:
                if self._probe_login_form_state(frame).get('is_login_form'):
                    return True
            except Exception:
                continue

        return False

    def _read_frame_text_for_detection(self, frame) -> str:
        """优先读取可见文本，避免把 HTML/CSS/JS 误判成验证文案。"""
        if not frame:
            return ''

        try:
            visible_text = frame.inner_text('body', timeout=1500)
            if visible_text:
                return str(visible_text)[:20000]
        except Exception:
            pass

        try:
            content_text = frame.text_content('body', timeout=1500)
            if content_text:
                return str(content_text)[:20000]
        except Exception:
            pass

        return ''

    def _collect_page_text_for_detection(self, page) -> str:
        """读取页面主体文案，用于识别验证页是否已经超时或失效。"""
        if not page:
            return ''

        try:
            visible_text = page.inner_text('body', timeout=1500)
            if visible_text:
                return str(visible_text)[:20000]
        except Exception:
            pass

        try:
            content_text = page.text_content('body', timeout=1500)
            if content_text:
                return str(content_text)[:20000]
        except Exception:
            pass

        return ''

    def _is_timed_out_verification_text(self, text: str) -> bool:
        content = str(text or '').strip()
        if not content:
            return False

        timeout_markers = (
            '验证失败',
            '验证超时',
            '请在指定时间内完成验证',
            '请重新扫描二维码完成身份验证',
            '重新扫描二维码',
            '返回二维码',
            '返回扫码',
            '二维码已失效',
            '二维码过期',
        )
        return any(marker in content for marker in timeout_markers)

    def _collect_verification_target_text(self, target, fallback_page=None) -> str:
        if target:
            try:
                target_text = self._read_frame_text_for_detection(target)
                if target_text:
                    return target_text
            except Exception:
                pass

        if fallback_page and fallback_page is not target:
            try:
                page_text = self._collect_page_text_for_detection(fallback_page)
                if page_text:
                    return page_text
            except Exception:
                pass

        return ''

    def _verification_target_is_timed_out(self, target, fallback_page=None) -> bool:
        detection_text = self._collect_verification_target_text(target, fallback_page=fallback_page)
        return self._is_timed_out_verification_text(detection_text)

    def _recover_timed_out_verification_page(self, qr_frame, fallback_page=None):
        recovery_markers = ['返回二维码', '返回扫码', '重新扫描二维码', '重新扫码']
        base_target = getattr(qr_frame, '_original_frame', qr_frame)
        candidate_targets = []
        for candidate in (base_target, fallback_page):
            if candidate is None or candidate in candidate_targets:
                continue
            candidate_targets.append(candidate)

        clicked_marker = None
        clicked_target = None
        for candidate in candidate_targets:
            if not hasattr(candidate, 'evaluate'):
                continue
            try:
                clicked_marker = candidate.evaluate(
                    """
                    (markers) => {
                        const normalize = (text) => (text || '').replace(/\\s+/g, '');
                        const isVisible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            if (!style || style.display === 'none' || style.visibility === 'hidden') {
                                return false;
                            }
                            const rect = el.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0;
                        };

                        const elements = Array.from(
                            document.querySelectorAll('a,button,[role=\"button\"],span,div')
                        );
                        for (const marker of markers) {
                            const normalizedMarker = normalize(marker);
                            const matched = elements.find((el) => {
                                const text = normalize(el.innerText || el.textContent || '');
                                return text && text.includes(normalizedMarker) && isVisible(el);
                            });
                            if (matched) {
                                matched.click();
                                return marker;
                            }
                        }
                        return null;
                    }
                    """,
                    recovery_markers,
                )
                if clicked_marker:
                    clicked_target = candidate
                    logger.info(
                        f"【{self.pure_user_id}】检测到验证页已超时，已尝试点击恢复入口: {clicked_marker}"
                    )
                    break
            except Exception as click_err:
                logger.debug(f"【{self.pure_user_id}】点击超时验证页恢复入口失败: {click_err}")

        if not clicked_marker:
            logger.warning(f"【{self.pure_user_id}】当前超时验证页未找到可用的二维码恢复入口")
            return None

        time.sleep(1.5)
        monitor_page = fallback_page or clicked_target or base_target
        try:
            has_verification, recovered_frame = self._detect_qr_code_verification(monitor_page)
        except Exception as detect_err:
            logger.warning(f"【{self.pure_user_id}】点击恢复入口后重新检测验证页失败: {detect_err}")
            return None

        if not has_verification or not recovered_frame:
            logger.warning(f"【{self.pure_user_id}】点击恢复入口后未检测到新的验证页")
            return None

        if self._verification_target_is_timed_out(recovered_frame, fallback_page=monitor_page):
            logger.warning(f"【{self.pure_user_id}】点击恢复入口后仍然拿到超时/失效验证页")
            return None

        recovered_screenshot_path = getattr(recovered_frame, 'screenshot_path', None)
        if not recovered_screenshot_path:
            try:
                recovered_screenshot_path = self._capture_verification_screenshot(
                    monitor_page,
                    frame=(None if recovered_frame is monitor_page else recovered_frame),
                )
                if recovered_screenshot_path and hasattr(recovered_frame, 'screenshot_path'):
                    recovered_frame.screenshot_path = recovered_screenshot_path
            except Exception as screenshot_err:
                logger.debug(f"【{self.pure_user_id}】恢复后补抓验证截图失败: {screenshot_err}")

        logger.info(f"【{self.pure_user_id}】已从超时验证页恢复出新的可用验证入口")
        return recovered_frame

    def _build_timed_out_verification_message(self, verification_type: str) -> str:
        verification_type_names = {
            'face_verify': '人脸验证',
            'sms_verify': '短信验证',
            'qr_verify': '二维码验证',
            'unknown': '身份验证',
        }
        type_name = verification_type_names.get(verification_type or 'unknown', '身份验证')
        return f"当前{type_name}页面已超时/失效，请重新发起验证"

    def _page_looks_like_verification(self, page) -> bool:
        try:
            if self._page_has_login_form(page):
                return False

            page_url = self._safe_page_url(page)
            if self._looks_like_verification_url(page_url):
                return True

            try:
                iframe = page.query_selector('iframe#alibaba-login-box')
                if iframe:
                    return True
            except Exception:
                pass

            try:
                for frame in page.frames:
                    if self._looks_like_verification_url(getattr(frame, 'url', '')):
                        return True
            except Exception:
                pass
        except Exception:
            pass

        return False

    def _looks_like_verification_title(self, title: str) -> bool:
        current_title = str(title or '')
        current_title_lower = current_title.lower()
        title_tokens = (
            'captcha',
            'intercept',
            'punish',
            '验证',
            '拦截',
            '验证码',
        )
        return any(token in current_title_lower or token in current_title for token in title_tokens)

    def _select_monitor_page(self, context=None, fallback_page=None):
        pages = self._get_context_pages(context, fallback_page)
        if not pages:
            return fallback_page

        reversed_pages = list(reversed(pages))

        for candidate in reversed_pages:
            if self._page_looks_like_verification(candidate):
                return candidate

        for candidate in reversed_pages:
            if self._page_has_keep_login_prompt(candidate):
                return candidate

        for candidate in reversed_pages:
            page_url = self._safe_page_url(candidate)
            if page_url and page_url != 'about:blank':
                return candidate

        return reversed_pages[0]

    def _probe_context_login_success(self, context, fallback_page=None) -> Tuple[bool, Any, Dict[str, str]]:
        monitor_page = self._select_monitor_page(context, fallback_page)
        cookie_dict = self._snapshot_context_cookies(context, page=monitor_page)
        pending_identity_markers = self._detect_pending_identity_verification_cookie_state(cookie_dict)

        if monitor_page:
            try:
                current_url = self._safe_page_url(monitor_page)
                page_has_slider = self._page_has_slider(monitor_page)
                page_looks_verification = self._page_looks_like_verification(monitor_page)
                if (
                    self._check_login_success_by_element(monitor_page) and
                    self._has_completed_login_cookies(cookie_dict) and
                    self._is_logged_in_url(current_url) and
                    not page_has_slider and
                    not page_looks_verification and
                    not pending_identity_markers
                ):
                    logger.success(f"【{self.pure_user_id}】✅ 当前监控页面已确认登录成功")
                    return True, monitor_page, cookie_dict
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】检查监控页面登录状态失败: {e}")

        if not self._has_completed_login_cookies(cookie_dict):
            return False, monitor_page, cookie_dict

        pending_identity_markers = self._detect_pending_identity_verification_cookie_state(cookie_dict)
        if monitor_page:
            current_url = self._safe_page_url(monitor_page)
            page_has_slider = self._page_has_slider(monitor_page)
            page_looks_verification = self._page_looks_like_verification(monitor_page)
            if (
                self._is_logged_in_url(current_url) and
                not page_has_slider and
                not page_looks_verification and
                not pending_identity_markers
            ):
                logger.success(
                    f"【{self.pure_user_id}】✅ 检测到上下文已登录，当前URL: {current_url}"
                )
                return True, monitor_page, cookie_dict

        probe_page = None
        try:
            probe_page = context.new_page()
            probe_page.goto('https://www.goofish.com/im', wait_until='domcontentloaded', timeout=30000)
            time.sleep(1.5)

            probe_cookies = self._snapshot_context_cookies(context, page=probe_page)
            probe_url = self._safe_page_url(probe_page)
            probe_has_slider = self._page_has_slider(probe_page)
            probe_looks_verification = self._page_looks_like_verification(probe_page)
            probe_pending_identity_markers = self._detect_pending_identity_verification_cookie_state(probe_cookies)
            if (
                self._check_login_success_by_element(probe_page) and
                self._has_completed_login_cookies(probe_cookies) and
                self._is_logged_in_url(probe_url) and
                not probe_has_slider and
                not probe_looks_verification and
                not probe_pending_identity_markers
            ):
                logger.success(f"【{self.pure_user_id}】✅ 通过探测页面确认登录成功")
                return True, probe_page, probe_cookies

            probe_has_slider = self._page_has_slider(probe_page)
            probe_looks_verification = self._page_looks_like_verification(probe_page)
            probe_pending_identity_markers = self._detect_pending_identity_verification_cookie_state(probe_cookies)
            if (
                self._has_completed_login_cookies(probe_cookies) and
                self._is_logged_in_url(probe_url) and
                not probe_has_slider and
                not probe_looks_verification and
                not probe_pending_identity_markers
            ):
                logger.success(f"【{self.pure_user_id}】✅ 通过探测页面URL和Cookie确认登录成功")
                return True, probe_page, probe_cookies
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】探测上下文登录状态失败: {e}")
        finally:
            if probe_page:
                try:
                    probe_page.close()
                except Exception:
                    pass

        return False, monitor_page, cookie_dict

    def _recover_from_missing_login_inputs(
        self,
        context,
        page,
        *,
        missing_field: str,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ) -> Tuple[bool, Any]:
        logger.warning(
            f"【{self.pure_user_id}】未找到{missing_field}，复检当前页面是否处于已登录态或验证页..."
        )

        login_success, active_page, _ = self._probe_context_login_success(context, page)
        if login_success:
            cookies_result = self._finalize_logged_in_cookies(
                context,
                active_page or page,
                scene=f"{missing_field}复检已登录",
                notification_callback=notification_callback,
                notification_scene=notification_scene,
            )
            logger.success(f"【{self.pure_user_id}】✅ 页面实际已登录，停止继续账密输入")
            return True, cookies_result

        monitor_page = self._select_monitor_page(context, active_page or page) or active_page or page
        if monitor_page:
            has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
            if has_qr:
                logger.info(f"【{self.pure_user_id}】复检发现当前页面需要人工验证，转入验证流程")
                return True, self._process_verification_requirement(
                    context,
                    monitor_page,
                    qr_frame,
                    notification_callback,
                    notification_scene,
                )

        return False, None

    def _page_has_slider(self, page) -> bool:
        if not page:
            return False

        slider_selectors = [
            '#nc_1_n1z',
            '.nc-container',
            '.nc_scale',
            '.nc-wrapper',
            '#baxia-dialog-content',
            '.nc_wrapper',
            '#nocaptcha',
        ]

        frames_to_check = [page]
        try:
            frames_to_check.extend(list(page.frames))
        except Exception:
            pass

        for frame in frames_to_check:
            for selector in slider_selectors:
                try:
                    element = frame.query_selector(selector)
                    if element and element.is_visible():
                        logger.info(f"【{self.pure_user_id}】检测到滑块元素: {selector}")
                        return True
                except Exception:
                    continue

        return False

    def _attempt_solve_slider_on_page(self, page) -> bool:
        if not page or not self._page_has_slider(page):
            return False

        logger.info(f"【{self.pure_user_id}】在当前活动页面检测到滑块，尝试自动处理...")
        original_page = self.page
        try:
            self.page = page
            solved = self.solve_slider(max_retries=3, fast_mode=True)
            if solved:
                logger.success(f"【{self.pure_user_id}】✅ 当前活动页面滑块处理成功")
                time.sleep(2)
            else:
                logger.warning(f"【{self.pure_user_id}】⚠️ 当前活动页面滑块处理未成功")
            return solved
        finally:
            self.page = original_page

    def _cleanup_verification_screenshots(self):
        try:
            import glob

            screenshots_dir = 'static/uploads/images'
            all_screenshots = glob.glob(os.path.join(screenshots_dir, f'face_verify_{self.pure_user_id}_*.jpg'))
            all_screenshots += glob.glob(os.path.join(screenshots_dir, f'face_verify_{self.pure_user_id}_*.png'))
            for screenshot_file in all_screenshots:
                try:
                    if os.path.exists(screenshot_file):
                        os.remove(screenshot_file)
                        logger.info(f"【{self.pure_user_id}】✅ 已删除验证截图: {screenshot_file}")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】⚠️ 删除截图失败: {e}")
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】删除截图时出错: {e}")

    def _finalize_logged_in_cookies(
        self,
        context,
        page,
        *,
        scene: str,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
        extra_cookie_updates: Optional[Dict[str, str]] = None,
    ):
        """登录态已确认后，尽量获取完整 Cookie，并对半登录态做最后兜底。"""
        target_page = page
        try:
            if target_page and hasattr(target_page, 'is_closed') and target_page.is_closed():
                target_page = None
        except Exception:
            pass

        if not target_page:
            target_page = self._select_monitor_page(context, page)

        self.last_browser_cookie_warmup_session_unready = False

        # 账密登录成功后，浏览器可能停留在 login.taobao.com / www.taobao.com，新的 _m_h5_tk
        # 会落到 .taobao.com 域；后续 mtop.idlemessage.pc.login.token 接口被 h5api.m.goofish.com
        # 网关 H5 token 校验时拿不到对应域的 token，直接回 FAIL_SYS_ILLEGAL_ACCESS::非法请求。
        # 参考 1157ab3 在 _get_cookies_after_success 的做法，先回访 goofish 主域让 H5 token
        # 重发到 .goofish.com，再做 cookie 快照，并显式让同名 Cookie 取 goofish 域版本。
        if target_page:
            try:
                pre_snapshot_url = target_page.url or ''
                pre_snapshot_host = (urlparse(pre_snapshot_url).hostname or '').lower()
            except Exception:
                pre_snapshot_host = ''
            if 'goofish.com' not in pre_snapshot_host:
                try:
                    target_page.goto(
                        'https://www.goofish.com/',
                        wait_until='domcontentloaded',
                        timeout=8000,
                    )
                    time.sleep(1.5)
                    logger.info(
                        f"【{self.pure_user_id}】{scene}前已回访 goofish 主域，"
                        f"等待 .goofish.com 域重新颁发 _m_h5_tk"
                    )
                except Exception as goto_e:
                    logger.warning(
                        f"【{self.pure_user_id}】{scene}前回访 goofish 主域失败，仍按当前页 cookie 继续: {goto_e}"
                    )

        cookies_dict = self._snapshot_context_cookies(
            context,
            page=target_page,
            preferred_domain_suffixes=('goofish.com',),
        )
        if extra_cookie_updates:
            merged_from_network = dict(cookies_dict)
            merged_from_network.update(extra_cookie_updates)
            cookies_dict = merged_from_network
            observed_names = sorted(extra_cookie_updates.keys())
            observed_protected = [
                key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                if key in extra_cookie_updates
            ]
            logger.info(
                f"【{self.pure_user_id}】已合并登录响应中的 {len(extra_cookie_updates)} 个Set-Cookie到{scene}快照: "
                f"{observed_names[:16]}{' ...' if len(observed_names) > 16 else ''}"
            )
            if observed_protected:
                logger.info(
                    f"【{self.pure_user_id}】登录响应中包含关键会话Cookie: {observed_protected}"
                )
        logger.info(f"【{self.pure_user_id}】{scene}后获取到 {len(cookies_dict)} 个Cookie字段")

        if not cookies_dict:
            logger.error(f"【{self.pure_user_id}】❌ {scene}后未获取到Cookie")
            return self._fail_login(f"{scene}后未获取到Cookie")

        missing_protected_fields = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if missing_protected_fields:
            logger.warning(
                f"【{self.pure_user_id}】{scene}后Cookie仍缺少关键字段，先执行标准稳定化: "
                f"{missing_protected_fields}"
            )
            stabilized_cookies = self._stabilize_logged_in_context_cookies(
                context,
                target_page,
                scene=scene,
            )
            if stabilized_cookies:
                cookies_dict = stabilized_cookies

        missing_protected_fields = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if missing_protected_fields and target_page:
            logger.warning(
                f"【{self.pure_user_id}】{scene}标准稳定化后仍缺少关键字段，继续执行浏览器业务预热: "
                f"{missing_protected_fields}"
            )
            warmed_cookies = self._perform_browser_cookie_warmup_probes(
                context,
                target_page,
                scene=scene,
                initial_cookies=cookies_dict,
            )
            if warmed_cookies:
                cookies_dict = warmed_cookies

        warmup_hint_result = self._consume_browser_cookie_warmup_verification_hint(
            context,
            target_page,
            cookies_dict,
            notification_callback=notification_callback,
            notification_scene=notification_scene,
        )
        if warmup_hint_result is not None:
            return warmup_hint_result

        pending_identity_error_before = self.last_login_error
        pending_identity_result = self._handle_pending_identity_verification_state(
            context,
            target_page,
            cookies_dict,
            notification_callback=notification_callback,
            notification_scene=notification_scene,
        )
        if pending_identity_result is not None:
            return pending_identity_result
        if self.last_login_error and self.last_login_error != pending_identity_error_before:
            return None

        missing_protected_fields = [
            key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if missing_protected_fields and getattr(self, 'last_browser_cookie_warmup_session_unready', False):
            self._log_cookie_snapshot_integrity(cookies_dict, f"{scene}完成后")
            logger.error(
                f"【{self.pure_user_id}】❌ {scene}后关键Cookie仍未齐全，且浏览器业务预热仍提示服务端Session未就绪: "
                f"{missing_protected_fields}"
            )
            return self._fail_login(
                f"{scene}后关键Cookie仍未齐全，服务端Session仍未就绪: {', '.join(missing_protected_fields)}"
            )

        missing_required_fields = [
            key for key in self._REQUIRED_SESSION_COOKIE_FIELDS
            if not cookies_dict.get(key)
        ]
        if missing_required_fields:
            self._log_cookie_snapshot_integrity(cookies_dict, f"{scene}完成后")
            logger.error(
                f"【{self.pure_user_id}】❌ {scene}后Cookie仍缺失核心字段: "
                f"{missing_required_fields}"
            )
            return self._fail_login(
                f"{scene}后Cookie仍缺失核心字段: {', '.join(missing_required_fields)}"
            )

        self._log_cookie_snapshot_integrity(cookies_dict, f"{scene}完成后")
        logger.success(f"【{self.pure_user_id}】✅ {scene}后Cookie获取完成，字段数: {len(cookies_dict)}")
        cleared_pending_markers = []
        sanitized_cookies = dict(cookies_dict)
        for key in self._IDENTITY_VERIFY_PENDING_COOKIE_FIELDS:
            if sanitized_cookies.pop(key, None) is not None:
                cleared_pending_markers.append(key)
        if cleared_pending_markers:
            logger.info(
                f"[{self.pure_user_id}] {scene} cleared pending identity markers: "
                f"{cleared_pending_markers}"
            )
        return sanitized_cookies

    def _wait_for_context_login(
        self,
        context,
        fallback_page,
        max_wait_time: int = 450,
        check_interval: int = 10,
        verification_type: str = 'unknown',
        verification_url: Optional[str] = None,
        verification_screenshot_path: Optional[str] = None,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ) -> Tuple[bool, Any]:
        waited_time = 0
        monitor_page = fallback_page
        last_verification_type = verification_type or 'unknown'
        last_verification_url = verification_url or None
        last_verification_screenshot_path = verification_screenshot_path or None

        while waited_time < max_wait_time:
            monitor_page = self._select_monitor_page(context, monitor_page)
            self._attempt_solve_slider_on_page(monitor_page)
            has_verification, refreshed_frame = self._detect_qr_code_verification(monitor_page)

            login_success, success_page, success_cookies = self._probe_context_login_success(context, monitor_page)
            if login_success:
                pending_identity_markers = self._detect_pending_identity_verification_cookie_state(success_cookies or {})
                missing_protected_fields = [
                    key for key in self._PROTECTED_SESSION_COOKIE_FIELDS
                    if not (success_cookies or {}).get(key)
                ]
                if pending_identity_markers:
                    logger.warning(
                        f"【{self.pure_user_id}】验证等待期间虽然检测到页面已登录，"
                        f"但待确认Cookie标记仍存在，继续等待后续验证完成: {pending_identity_markers}"
                    )
                elif has_verification and missing_protected_fields:
                    logger.warning(
                        f"【{self.pure_user_id}】验证等待期间验证页仍存在，且关键Cookie仍未齐全，"
                        f"继续等待后续验证完成: {missing_protected_fields}"
                    )
                else:
                    return True, success_page or monitor_page

            if has_verification and refreshed_frame:
                refreshed_type = getattr(refreshed_frame, 'verification_type', None) or 'unknown'
                refreshed_url = getattr(refreshed_frame, 'verify_url', None)
                if not refreshed_url and hasattr(refreshed_frame, 'url'):
                    refreshed_url = refreshed_frame.url
                refreshed_screenshot_path = getattr(refreshed_frame, 'screenshot_path', None)

                if self._verification_target_is_timed_out(refreshed_frame, fallback_page=monitor_page):
                    recovered_frame = self._recover_timed_out_verification_page(
                        refreshed_frame,
                        fallback_page=monitor_page,
                    )
                    if recovered_frame:
                        refreshed_frame = recovered_frame
                        refreshed_type = getattr(refreshed_frame, 'verification_type', None) or refreshed_type
                        refreshed_url = getattr(refreshed_frame, 'verify_url', None)
                        if not refreshed_url and hasattr(refreshed_frame, 'url'):
                            refreshed_url = refreshed_frame.url
                        refreshed_screenshot_path = getattr(refreshed_frame, 'screenshot_path', None)
                        recovered_from_timeout = True
                    else:
                        timeout_message = self._build_timed_out_verification_message(refreshed_type)
                        self.last_login_error = timeout_message
                        logger.warning(f"【{self.pure_user_id}】{timeout_message}")
                        return False, monitor_page
                else:
                    recovered_from_timeout = False

                # 只按验证类型和 URL 判断是否变化；截图文件每轮都会生成新路径，
                # 如果把 screenshot_path 纳入变化判断，会导致同一个扫码页反复推送通知。
                verification_changed = (
                    recovered_from_timeout or
                    refreshed_type != last_verification_type or
                    (refreshed_url or None) != last_verification_url
                )
                if verification_changed:
                    logger.info(
                        f"【{self.pure_user_id}】验证等待期间检测到验证页变化: "
                        f"{last_verification_type}->{refreshed_type}, url={refreshed_url or 'N/A'}"
                    )
                    self._notify_verification_required(
                        refreshed_type,
                        refreshed_url,
                        refreshed_screenshot_path,
                        notification_callback,
                        notification_scene,
                    )
                    last_verification_type = refreshed_type
                    last_verification_url = refreshed_url or None
                    last_verification_screenshot_path = refreshed_screenshot_path or None

            time.sleep(check_interval)
            waited_time += check_interval
            logger.info(f"【{self.pure_user_id}】等待验证中... (已等待{waited_time}秒/{max_wait_time}秒)")

        return False, self._select_monitor_page(context, monitor_page)

    def _notify_verification_required(
        self,
        verification_type: str,
        frame_url: Optional[str],
        screenshot_path: Optional[str],
        notification_callback: Optional[Callable],
        notification_scene: str,
    ):
        if not notification_callback or not (screenshot_path or frame_url):
            if not notification_callback:
                logger.warning(f"【{self.pure_user_id}】⚠️ notification_callback 未提供，无法发送通知")
            else:
                logger.warning(f"【{self.pure_user_id}】无法获取验证信息，跳过通知发送")
            return

        dedup_key = (
            str(getattr(self, 'pure_user_id', self.user_id) or ''),
            str(verification_type or 'unknown'),
            str(frame_url or ''),
        )
        dedup_seconds = max(
            30,
            int(os.environ.get('XY_VERIFICATION_NOTIFY_DEDUP_SECONDS', self._verification_notification_dedup_seconds) or self._verification_notification_dedup_seconds),
        )
        now = time.time()
        with self._verification_notification_lock:
            # 顺手清理过期项，避免长期运行缓存增长。
            expired_keys = [
                key for key, sent_at in self._verification_notification_cache.items()
                if now - sent_at > dedup_seconds * 3
            ]
            for key in expired_keys:
                self._verification_notification_cache.pop(key, None)

            last_sent_at = self._verification_notification_cache.get(dedup_key)
            if last_sent_at and now - last_sent_at < dedup_seconds:
                logger.info(
                    f"【{self.pure_user_id}】同一验证入口通知在去重窗口内已发送，跳过重复通知: "
                    f"type={verification_type}, url={frame_url or 'N/A'}, remaining={dedup_seconds - int(now - last_sent_at)}s"
                )
                return
            self._verification_notification_cache[dedup_key] = now

        verification_type_titles = {
            'face_verify': f'⚠️ {notification_scene}需要人脸验证',
            'sms_verify': f'⚠️ {notification_scene}需要短信验证',
            'qr_verify': f'⚠️ {notification_scene}需要二维码验证',
            'login_page': f'⚠️ {notification_scene}需要扫码登录',
            'unknown': f'⚠️ {notification_scene}需要身份验证',
        }
        title = verification_type_titles.get(verification_type, f'⚠️ {notification_scene}需要身份验证')

        if screenshot_path:
            notification_msg = (
                f"{title}\n\n"
                f"账号: {self.pure_user_id}\n"
                f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"请登录自动化网站，访问账号管理模块，进行对应账号的验证。"
                f"在验证期间，自动回复功能暂时无法使用。"
            )
        else:
            notification_msg = (
                f"{title}\n\n"
                f"账号: {self.pure_user_id}\n"
                f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"请点击验证链接完成验证:\n{frame_url}\n\n"
                f"在验证期间，自动回复功能暂时无法使用。"
            )

        try:
            logger.info(f"【{self.pure_user_id}】准备发送验证通知，截图路径: {screenshot_path}, URL: {frame_url}")
            import inspect

            if inspect.iscoroutinefunction(notification_callback):
                def run_async_callback():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        try:
                            loop.run_until_complete(
                                notification_callback(
                                    notification_msg,
                                    screenshot_path,
                                    frame_url,
                                    verification_type=verification_type,
                                )
                            )
                        except TypeError:
                            loop.run_until_complete(notification_callback(notification_msg, screenshot_path, frame_url))
                        logger.info(f"【{self.pure_user_id}】✅ 异步通知回调已执行")
                    except Exception as async_err:
                        logger.error(f"【{self.pure_user_id}】异步通知回调执行失败: {async_err}")
                        import traceback
                        logger.error(traceback.format_exc())
                    finally:
                        loop.close()

                thread = threading.Thread(target=run_async_callback, daemon=True)
                thread.start()
                logger.info(f"【{self.pure_user_id}】异步通知线程已启动")
            else:
                try:
                    notification_callback(
                        notification_msg,
                        None,
                        frame_url,
                        screenshot_path,
                        verification_type=verification_type,
                    )
                except TypeError:
                    notification_callback(notification_msg, None, frame_url, screenshot_path)
                logger.info(f"【{self.pure_user_id}】✅ 同步通知回调已执行")
        except Exception as notify_err:
            logger.error(f"【{self.pure_user_id}】发送验证通知失败: {notify_err}")
            import traceback
            logger.error(traceback.format_exc())

    def _process_verification_requirement(
        self,
        context,
        fallback_page,
        qr_frame,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '账号密码登录',
    ):
        verification_type = 'unknown'
        if qr_frame and hasattr(qr_frame, 'verification_type'):
            verification_type = qr_frame.verification_type

        verification_type_names = {
            'face_verify': '人脸验证',
            'sms_verify': '短信验证',
            'qr_verify': '二维码验证',
            'login_page': '扫码登录',
            'unknown': '身份验证',
        }
        type_name = verification_type_names.get(verification_type, '身份验证')

        frame_url = None
        screenshot_path = None
        if qr_frame:
            try:
                if hasattr(qr_frame, 'verify_url') and qr_frame.verify_url:
                    frame_url = qr_frame.verify_url
                else:
                    frame_url = qr_frame.url if hasattr(qr_frame, 'url') else None

                if hasattr(qr_frame, 'screenshot_path') and qr_frame.screenshot_path:
                    screenshot_path = qr_frame.screenshot_path
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】获取验证信息失败: {e}")

        if self._verification_target_is_timed_out(qr_frame, fallback_page=fallback_page):
            recovered_frame = self._recover_timed_out_verification_page(
                qr_frame,
                fallback_page=fallback_page,
            )
            if recovered_frame:
                qr_frame = recovered_frame
                verification_type = getattr(qr_frame, 'verification_type', None) or verification_type
                type_name = verification_type_names.get(verification_type, '身份验证')
                frame_url = getattr(qr_frame, 'verify_url', None)
                if not frame_url and hasattr(qr_frame, 'url'):
                    frame_url = qr_frame.url
                screenshot_path = getattr(qr_frame, 'screenshot_path', None)
                logger.info(f"【{self.pure_user_id}】已将超时验证页恢复为新的{type_name}入口")
            else:
                timeout_message = self._build_timed_out_verification_message(verification_type)
                logger.warning(f"【{self.pure_user_id}】{timeout_message}")
                return self._fail_login(timeout_message)

        logger.warning(f"【{self.pure_user_id}】⚠️ 检测到{type_name}")
        logger.info(f"【{self.pure_user_id}】请在浏览器中完成{type_name}")

        if screenshot_path:
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
            logger.warning(f"【{self.pure_user_id}】二维码/人脸验证截图:")
            logger.warning(f"【{self.pure_user_id}】{screenshot_path}")
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
        elif frame_url:
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
            logger.warning(f"【{self.pure_user_id}】二维码/人脸验证链接:")
            logger.warning(f"【{self.pure_user_id}】{frame_url}")
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
        else:
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")
            logger.warning(f"【{self.pure_user_id}】二维码/人脸验证已检测到，但无法获取验证信息")
            logger.warning(f"【{self.pure_user_id}】请在浏览器中查看验证页面")
            logger.warning(f"【{self.pure_user_id}】{'=' * 60}")

        self._notify_verification_required(
            verification_type,
            frame_url,
            screenshot_path,
            notification_callback,
            notification_scene,
        )

        wait_timeout = max(5, int(getattr(self, 'verification_wait_timeout', 450) or 450))
        logger.info(f"【{self.pure_user_id}】等待二维码/人脸验证完成... (timeout={wait_timeout}s)")
        login_success = False
        success_page = fallback_page
        try:
            login_success, success_page = self._wait_for_context_login(
                context,
                fallback_page,
                max_wait_time=wait_timeout,
                check_interval=10,
                verification_type=verification_type,
                verification_url=frame_url,
                verification_screenshot_path=screenshot_path,
                notification_callback=notification_callback,
                notification_scene=notification_scene,
            )
        finally:
            if screenshot_path:
                logger.info(
                    f"【{self.pure_user_id}】验证流程结束后暂不自动删除验证截图，"
                    f"改由会话过期或手动清理: {screenshot_path}"
                )
            elif self.keep_verification_screenshots:
                logger.info(f"【{self.pure_user_id}】保留验证截图供后续调试")

        if not login_success:
            if self.last_login_error and '已超时/失效，请重新发起验证' in self.last_login_error:
                logger.error(f"【{self.pure_user_id}】❌ {self.last_login_error}")
                return None
            logger.error(f"【{self.pure_user_id}】❌ 等待验证超时（{wait_timeout}秒）")
            return self._fail_login(f"等待{type_name}超时（{wait_timeout}秒）")

        logger.success(f"【{self.pure_user_id}】✅ 验证成功，登录状态已确认！")
        return self._finalize_logged_in_cookies(
            context,
            success_page or fallback_page,
            scene=f"{type_name}验证完成",
            notification_callback=notification_callback,
            notification_scene=notification_scene,
        )

    def _has_meaningful_cookie_refresh(self, baseline: Dict[str, str], current: Dict[str, str]) -> bool:
        """判断关键 Cookie 是否发生了有意义的变化。

        判定逻辑（满足其一即可）：
        1. 任何 x5 系 Cookie 的值发生了变化或新增
        2. 关键会话 Cookie 的值发生了变化或新增
        """
        # 检查 x5 系 Cookie
        for name, value in current.items():
            if name.lower().startswith(self._X5_COOKIE_PREFIX):
                old_value = baseline.get(name)
                if old_value is None or old_value != value:
                    logger.info(f"【{self.pure_user_id}】Cookie 刷新检测: x5 系 Cookie '{name}' 已变化")
                    return True

        # 检查关键会话 Cookie
        for name in self._KEY_COOKIE_NAMES:
            new_val = current.get(name)
            if new_val is not None:
                old_val = baseline.get(name)
                if old_val is None or old_val != new_val:
                    logger.info(f"【{self.pure_user_id}】Cookie 刷新检测: 关键会话 Cookie '{name}' 已变化")
                    return True

        logger.warning(f"【{self.pure_user_id}】Cookie 刷新检测: 无有意义的 Cookie 变化")
        return False

    def _probe_context_login_during_slider(self, fallback_page=None) -> Tuple[bool, Dict[str, str]]:
        """刷新模式下，允许用 context 级登录态确认滑块已间接通过。"""
        if not getattr(self, '_slider_refresh_mode', False):
            return False, {}

        if not self.context:
            return False, {}

        try:
            login_success, _, cookies = self._probe_context_login_success(self.context, fallback_page or self.page)
            if login_success:
                logger.success(f"【{self.pure_user_id}】✅ 滑块阶段检测到上下文已登录，停止继续重试")
                self.last_verification_feedback = {
                    "status": "success",
                    "source": "context_login_confirmed",
                    "message": "上下文登录状态已确认"
                }
                return True, cookies or {}
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】滑块阶段探测上下文登录状态失败: {e}")

        return False, {}
    
    def _get_random_browser_features(self):
        """获取稳定浏览器特征。

        同一账号长期复用同一套桌面画像，避免后台无头链路在每次重启后漂移成
        不同设备，降低风控对“同账号多台机器来回切换”的判定概率。
        """
        runtime_is_windows = os.name == 'nt'

        browser_profiles = [
            # Windows Chrome 120 - 高配台式机
            {
                'profile_id': 'win_chrome_120_desktop',
                'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                'platform': 'Win32',
                'vendor': 'Google Inc.',
                'window_size': '1920,1080',
                'device_memory': 16,
                'hardware_concurrency': 8,
                'max_touch_points': 0,
                'device_scale_factor': 1.0,
                'color_depth': 24,
            },
            # Windows Chrome 120 - 中配笔记本
            {
                'profile_id': 'win_chrome_120_laptop',
                'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                'platform': 'Win32',
                'vendor': 'Google Inc.',
                'window_size': '1366,768',
                'device_memory': 8,
                'hardware_concurrency': 4,
                'max_touch_points': 0,
                'device_scale_factor': 1.25,
                'color_depth': 24,
            },
            # Windows Chrome 119 - 高配台式机
            {
                'profile_id': 'win_chrome_119_desktop',
                'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                'platform': 'Win32',
                'vendor': 'Google Inc.',
                'window_size': '1920,1200',
                'device_memory': 8,
                'hardware_concurrency': 6,
                'max_touch_points': 0,
                'device_scale_factor': 1.0,
                'color_depth': 24,
            },
            # Windows Chrome 118 - 标准台式机
            {
                'profile_id': 'win_chrome_118_standard',
                'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
                'platform': 'Win32',
                'vendor': 'Google Inc.',
                'window_size': '1600,900',
                'device_memory': 8,
                'hardware_concurrency': 4,
                'max_touch_points': 0,
                'device_scale_factor': 1.0,
                'color_depth': 24,
            },
            # Mac Chrome 120 - MacBook Pro
            {
                'profile_id': 'mac_chrome_120_pro',
                'user_agent': "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                'platform': 'MacIntel',
                'vendor': 'Google Inc.',
                'window_size': '2560,1440',
                'device_memory': 16,
                'hardware_concurrency': 10,
                'max_touch_points': 0,
                'device_scale_factor': 2.0,
                'color_depth': 30,
            },
            # Mac Chrome 119 - MacBook Air
            {
                'profile_id': 'mac_chrome_119_air',
                'user_agent': "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                'platform': 'MacIntel',
                'vendor': 'Google Inc.',
                'window_size': '1920,1080',
                'device_memory': 8,
                'hardware_concurrency': 8,
                'max_touch_points': 0,
                'device_scale_factor': 2.0,
                'color_depth': 30,
            },
        ]

        if runtime_is_windows:
            browser_profiles = [profile for profile in browser_profiles if str(profile.get('platform')) == 'Win32']

        languages = [
            ("zh-CN", "zh-CN,zh;q=0.9,en;q=0.8"),
            ("zh-CN", "zh-CN,zh;q=0.9"),
            ("zh-CN", "zh-CN,zh;q=0.8,en;q=0.6")
        ]

        identity = self._load_or_create_browser_identity(
            len(browser_profiles),
            len(languages),
            profile_version=3,
        )

        profile = browser_profiles[identity["profile_index"]]

        # 实测阿里 nocaptcha 在 Windows 无头下对 1366x768 / 高 DPI 组合更敏感，
        # 1600x900 + scale 1.0 的桌面画像通过率明显更稳，直接固定到这套。
        if runtime_is_windows and self.headless:
            preferred_profile = next(
                (item for item in browser_profiles if item.get('window_size') == '1600,900'),
                None,
            )
            if preferred_profile:
                profile = preferred_profile
        lang, accept_lang = languages[identity["language_index"]]

        # 解析窗口大小
        width, height = map(int, profile['window_size'].split(','))

        # 网络特征（桌面端只用 4g，rtt/downlink 在合理范围内随机）
        connection_rtt = random.randint(20, 80)
        connection_downlink = round(random.uniform(3, 10), 2)

        features = {
            'profile_id': profile['profile_id'],
            'window_size': profile['window_size'],
            'lang': lang,
            'accept_lang': accept_lang,
            'user_agent': profile['user_agent'],
            'locale': lang,
            'viewport_width': width,
            'viewport_height': height,
            'device_scale_factor': profile['device_scale_factor'],
            'is_mobile': False,
            'has_touch': False,
            'timezone_id': 'Asia/Shanghai',
            # 一致性指纹字段（与 UA 对应）
            'platform': profile['platform'],
            'vendor': profile['vendor'],
            'device_memory': profile['device_memory'],
            'hardware_concurrency': profile['hardware_concurrency'],
            'max_touch_points': profile['max_touch_points'],
            'color_depth': profile['color_depth'],
            'connection_type': '4g',
            'connection_rtt': connection_rtt,
            'connection_downlink': connection_downlink,
            'color_scheme': identity.get('color_scheme', 'light'),
            'plugin_count': identity.get('plugin_count', 5),
            'notification_permission': identity.get('notification_permission', 'default'),
            'do_not_track': identity.get('do_not_track', '0'),
            'battery_charging': identity.get('battery_charging', True),
            'battery_level': identity.get('battery_level', 0.76),
        }
        return self._apply_runtime_browser_profile(features)
    
    def _get_stealth_script(self, browser_features):
        """获取增强反检测脚本"""
        return f"""
            // 隐藏webdriver属性
            Object.defineProperty(navigator, 'webdriver', {{
                get: () => undefined,
            }});
            
            // 隐藏自动化相关属性
            delete navigator.__proto__.webdriver;
            delete window.navigator.webdriver;
            delete window.navigator.__proto__.webdriver;
            
            // 覆盖plugins - 随机化
            const pluginCount = {browser_features['plugin_count']};
            Object.defineProperty(navigator, 'plugins', {{
                get: () => Array.from({{length: pluginCount}}, (_, i) => ({{
                    name: 'Plugin' + i,
                    description: 'Plugin ' + i
                }})),
            }});
            
            // 覆盖languages
            Object.defineProperty(navigator, 'languages', {{
                get: () => ['{browser_features['locale']}', 'zh', 'en'],
            }});
            
            // 模拟真实的屏幕信息 - 使用 Profile 一致值
            Object.defineProperty(screen, 'availWidth', {{ get: () => {browser_features['viewport_width']} }});
            Object.defineProperty(screen, 'availHeight', {{ get: () => {browser_features['viewport_height'] - 40} }});
            Object.defineProperty(screen, 'width', {{ get: () => {browser_features['viewport_width']} }});
            Object.defineProperty(screen, 'height', {{ get: () => {browser_features['viewport_height']} }});
            Object.defineProperty(screen, 'colorDepth', {{ get: () => {browser_features['color_depth']} }});
            Object.defineProperty(screen, 'pixelDepth', {{ get: () => {browser_features['color_depth']} }});
            
            // 隐藏自动化检测 - 使用 Profile 一致的硬件信息
            Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {browser_features['hardware_concurrency']} }});
            Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {browser_features['device_memory']} }});
            
            // 模拟真实的时区
            Object.defineProperty(Intl.DateTimeFormat.prototype, 'resolvedOptions', {{
                value: function() {{
                    return {{ timeZone: '{browser_features['timezone_id']}' }};
                }}
            }});
            
            // 隐藏自动化痕迹
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
            
            // 模拟有头模式的特征 - 使用 Profile 一致值
            Object.defineProperty(navigator, 'maxTouchPoints', {{ get: () => {browser_features['max_touch_points']} }});
            Object.defineProperty(navigator, 'platform', {{ get: () => '{browser_features['platform']}' }});
            Object.defineProperty(navigator, 'vendor', {{ get: () => '{browser_features['vendor']}' }});
            Object.defineProperty(navigator, 'vendorSub', {{ get: () => '' }});
            Object.defineProperty(navigator, 'productSub', {{ get: () => '20030107' }});
            
            // 模拟真实的连接信息 - 使用 Profile 一致值
            Object.defineProperty(navigator, 'connection', {{
                get: () => ({{
                    effectiveType: "{browser_features['connection_type']}",
                    rtt: {browser_features['connection_rtt']},
                    downlink: {browser_features['connection_downlink']}
                }})
            }});
            
            // 隐藏无头模式特征
            Object.defineProperty(navigator, 'headless', {{ get: () => undefined }});
            Object.defineProperty(window, 'outerHeight', {{ get: () => {browser_features['viewport_height']} }});
            Object.defineProperty(window, 'outerWidth', {{ get: () => {browser_features['viewport_width']} }});
            
            // 模拟真实的媒体设备
            Object.defineProperty(navigator, 'mediaDevices', {{
                get: () => ({{
                    enumerateDevices: () => Promise.resolve([])
                }}),
            }});
            
            // 隐藏自动化检测特征
            Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
            Object.defineProperty(navigator, '__webdriver_script_fn', {{ get: () => undefined }});
            Object.defineProperty(navigator, '__webdriver_evaluate', {{ get: () => undefined }});
            Object.defineProperty(navigator, '__webdriver_unwrapped', {{ get: () => undefined }});
            Object.defineProperty(navigator, '__fxdriver_evaluate', {{ get: () => undefined }});
            Object.defineProperty(navigator, '__driver_evaluate', {{ get: () => undefined }});
            Object.defineProperty(navigator, '__webdriver_script_func', {{ get: () => undefined }});
            
            // 隐藏Playwright特定的对象
            delete window.playwright;
            delete window.__playwright;
            delete window.__pw_manual;
            delete window.__pw_original;
            
            // 模拟真实的用户代理
            Object.defineProperty(navigator, 'userAgent', {{
                get: () => '{browser_features['user_agent']}'
            }});
            
            // 隐藏自动化相关的全局变量
            delete window.webdriver;
            delete window.__webdriver_script_fn;
            delete window.__webdriver_evaluate;
            delete window.__webdriver_unwrapped;
            delete window.__fxdriver_evaluate;
            delete window.__driver_evaluate;
            delete window.__webdriver_script_func;
            delete window._selenium;
            delete window._phantom;
            delete window.callPhantom;
            delete window._phantom;
            delete window.phantom;
            delete window.Buffer;
            delete window.emit;
            delete window.spawn;
            
            // Canvas指纹随机化
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function() {{
                const context = this.getContext('2d');
                if (context) {{
                    const imageData = context.getImageData(0, 0, this.width, this.height);
                    const data = imageData.data;
                    for (let i = 0; i < data.length; i += 4) {{
                        if (Math.random() < 0.001) {{
                            data[i] = Math.floor(Math.random() * 256);
                        }}
                    }}
                    context.putImageData(imageData, 0, 0);
                }}
                return originalToDataURL.apply(this, arguments);
            }};
            
            // 音频指纹随机化
            const originalGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function(channel) {{
                const data = originalGetChannelData.call(this, channel);
                for (let i = 0; i < data.length; i += 1000) {{
                    if (Math.random() < 0.01) {{
                        data[i] += Math.random() * 0.0001;
                    }}
                }}
                return data;
            }};
            
            // WebGL指纹随机化
            const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) {{ // UNMASKED_VENDOR_WEBGL
                    return 'Intel Inc.';
                }}
                if (parameter === 37446) {{ // UNMASKED_RENDERER_WEBGL
                    return 'Intel Iris OpenGL Engine';
                }}
                return originalGetParameter.call(this, parameter);
            }};
            
            // 模拟真实的鼠标事件
            const originalAddEventListener = EventTarget.prototype.addEventListener;
            EventTarget.prototype.addEventListener = function(type, listener, options) {{
                if (type === 'mousedown' || type === 'mouseup' || type === 'mousemove') {{
                    const originalListener = listener;
                    listener = function(event) {{
                        setTimeout(() => originalListener.call(this, event), Math.random() * 10);
                    }};
                }}
                return originalAddEventListener.call(this, type, listener, options);
            }};
            
            // 随机化字体检测
            Object.defineProperty(document, 'fonts', {{
                get: () => ({{
                    ready: Promise.resolve(),
                    check: () => true,
                    load: () => Promise.resolve([])
                }})
            }});

            // 增强鼠标移动轨迹记录
            let mouseMovements = [];
            let lastMouseTime = Date.now();
            document.addEventListener('mousemove', function(e) {{
                const now = Date.now();
                const timeDiff = now - lastMouseTime;
                mouseMovements.push({{
                    x: e.clientX,
                    y: e.clientY,
                    time: now,
                    timeDiff: timeDiff
                }});
                lastMouseTime = now;
                // 保持最近100个移动记录
                if (mouseMovements.length > 100) {{
                    mouseMovements.shift();
                }}
            }}, true);

            // 模拟真实的电池API
            if (navigator.getBattery) {{
                const originalGetBattery = navigator.getBattery;
                navigator.getBattery = async function() {{
                    const battery = await originalGetBattery.call(navigator);
                        Object.defineProperty(battery, 'charging', {{ get: () => {str(browser_features['battery_charging']).lower()} }});
                        Object.defineProperty(battery, 'level', {{ get: () => {browser_features['battery_level']:.2f} }});
                    return battery;
                }};
            }}
            
            // 伪装鼠标移动加速度（反检测关键）
            let velocityProfile = [];
            window.addEventListener('mousemove', function(e) {{
                const now = performance.now();
                velocityProfile.push({{ x: e.clientX, y: e.clientY, t: now }});
                if (velocityProfile.length > 50) velocityProfile.shift();
            }}, true);
            
            // 伪装Permission API
            const originalQuery = Permissions.prototype.query;
            Permissions.prototype.query = function(parameters) {{
                if (parameters.name === 'notifications') {{
                    return Promise.resolve({{ state: '{browser_features['notification_permission']}' }});
                }}
                return originalQuery.apply(this, arguments);
            }};
            
            // 伪装Performance API
            const originalNow = Performance.prototype.now;
            Performance.prototype.now = function() {{
                return originalNow.call(this) + Math.random() * 0.1;
            }};
            
            // 伪装Date API（添加微小随机偏移）
            const OriginalDate = Date;
            Date = function(...args) {{
                if (args.length === 0) {{
                    const date = new OriginalDate();
                    const offset = Math.floor(Math.random() * 3) - 1; // -1到1毫秒
                    return new OriginalDate(date.getTime() + offset);
                }}
                return new OriginalDate(...args);
            }};
            Date.prototype = OriginalDate.prototype;
            Date.now = function() {{
                return OriginalDate.now() + Math.floor(Math.random() * 3) - 1;
            }};
            
            // 伪装RTCPeerConnection（WebRTC指纹）
            if (window.RTCPeerConnection) {{
                const originalRTC = window.RTCPeerConnection;
                window.RTCPeerConnection = function(...args) {{
                    const pc = new originalRTC(...args);
                    const originalCreateOffer = pc.createOffer;
                    pc.createOffer = function(...args) {{
                        return originalCreateOffer.apply(this, args).then(offer => {{
                            // 修改SDP指纹
                            offer.sdp = offer.sdp.replace(/a=fingerprint:.*\\r\\n/g, 
                                `a=fingerprint:sha-256 ${{Array.from({{length:64}}, ()=>Math.floor(Math.random()*16).toString(16)).join('')}}\\r\\n`);
                            return offer;
                        }});
                    }};
                    return pc;
                }};
            }}
            
            // 伪装 Notification 权限（防止被检测为自动化）
            Object.defineProperty(Notification, 'permission', {{
                get: function() {{
                    return '{browser_features['notification_permission']}';
                }}
            }});

            // 伪装 DoNotTrack
            Object.defineProperty(navigator, 'doNotTrack', {{
                get: function() {{
                    return '{browser_features['do_not_track']}';
                }}
            }});
            
            // 伪装 Geolocation（添加微小延迟和误差）
            if (navigator.geolocation) {{
                const originalGetCurrentPosition = navigator.geolocation.getCurrentPosition;
                navigator.geolocation.getCurrentPosition = function(success, error, options) {{
                    const wrappedSuccess = function(position) {{
                        // 添加微小的位置偏移（模拟真实GPS误差）
                        const offset = Math.random() * 0.001;
                        position.coords.latitude += offset;
                        position.coords.longitude += offset;
                        success(position);
                    }};
                    // 添加随机延迟
                    setTimeout(() => {{
                        originalGetCurrentPosition.call(this, wrappedSuccess, error, options);
                    }}, Math.random() * 100);
                }};
            }}
            
            // 伪装 Clipboard API（防止检测剪贴板访问模式）
            if (navigator.clipboard) {{
                const originalReadText = navigator.clipboard.readText;
                navigator.clipboard.readText = async function() {{
                    // 添加微小延迟
                    await new Promise(resolve => setTimeout(resolve, Math.random() * 50));
                    return originalReadText.call(this);
                }};
            }}

            // 🔑 伪装chrome对象（统一定义，防止检测headless）
            window.chrome = {{
                runtime: {{
                    id: undefined,
                    sendMessage: function() {{}},
                    connect: function() {{}}
                }},
                loadTimes: function() {{}},
                csi: function() {{}},
                app: {{}}
            }};

            // 🔑 覆盖Function.prototype.toString以隐藏代理
            const oldToString = Function.prototype.toString;
            Function.prototype.toString = function() {{
                if (this === navigator.permissions.query) {{
                    return 'function query() {{ [native code] }}';
                }}
                return oldToString.call(this);
            }};
        """
    
    def _bezier_curve(self, p0, p1, p2, p3, t):
        """三次贝塞尔曲线 - 生成更自然的轨迹"""
        return (1-t)**3 * p0 + 3*(1-t)**2*t * p1 + 3*(1-t)*t**2 * p2 + t**3 * p3
    
    def _easing_function(self, t, mode='easeOutQuad'):
        """缓动函数 - 模拟真实人类滑动的速度变化"""
        if mode == 'easeOutQuad':
            return t * (2 - t)
        elif mode == 'easeInOutCubic':
            return 4*t**3 if t < 0.5 else 1 - pow(-2*t + 2, 3) / 2
        elif mode == 'easeOutBack':
            c1 = 1.70158
            c3 = c1 + 1
            return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)
        else:
            return t
    
    def _build_client_hint_profile(self, browser_features: Dict[str, Any]) -> Dict[str, Any]:
        user_agent = str(browser_features.get("user_agent") or "")
        version_match = re.search(r"Chrome/(\d+(?:\.\d+){0,3})", user_agent)
        full_version = version_match.group(1) if version_match else "118.0.0.0"
        major_version = full_version.split(".", 1)[0]

        browser_family = self._get_browser_family()
        brands = [{"brand": "Not.A/Brand", "version": "8"}]
        full_version_list = [{"brand": "Not.A/Brand", "version": "8.0.0.0"}]
        sec_ch_ua_parts = ['"Not.A/Brand";v="8"']

        if browser_family == "edge":
            brands.extend([
                {"brand": "Chromium", "version": major_version},
                {"brand": "Microsoft Edge", "version": major_version},
            ])
            full_version_list.extend([
                {"brand": "Chromium", "version": full_version},
                {"brand": "Microsoft Edge", "version": full_version},
            ])
            sec_ch_ua_parts.extend([
                f'"Chromium";v="{major_version}"',
                f'"Microsoft Edge";v="{major_version}"',
            ])
        else:
            brands.extend([
                {"brand": "Chromium", "version": major_version},
                {"brand": "Google Chrome", "version": major_version},
            ])
            full_version_list.extend([
                {"brand": "Chromium", "version": full_version},
                {"brand": "Google Chrome", "version": full_version},
            ])
            sec_ch_ua_parts.extend([
                f'"Chromium";v="{major_version}"',
                f'"Google Chrome";v="{major_version}"',
            ])

        sec_ch_ua = ", ".join(sec_ch_ua_parts)

        return {
            "userAgent": user_agent,
            "fullVersion": full_version,
            "majorVersion": major_version,
            "brands": brands,
            "fullVersionList": full_version_list,
            "secChUa": sec_ch_ua,
            "secChUaMobile": "?1" if browser_features.get("is_mobile") else "?0",
            "secChUaPlatform": f'"{browser_features.get("platform") or "Windows"}"',
            "platform": browser_features.get("platform") or "Windows",
            "platformVersion": "10.0.0",
            "architecture": "x86",
            "bitness": "64",
            "mobile": bool(browser_features.get("is_mobile")),
            "model": "",
            "wow64": False,
        }

    def _build_headless_extra_headers(self, browser_features: Dict[str, Any]) -> Dict[str, str]:
        hints = self._build_client_hint_profile(browser_features)
        return {
            "sec-ch-ua": hints["secChUa"],
            "sec-ch-ua-mobile": hints["secChUaMobile"],
            "sec-ch-ua-platform": hints["secChUaPlatform"],
        }

    def _apply_headless_network_fingerprint(self, page, browser_features: Dict[str, Any]):
        if not self.headless or not self.context or not page:
            return

        try:
            hints = self._build_client_hint_profile(browser_features)
            session = self.context.new_cdp_session(page)
            session.send("Network.enable")
            session.send(
                "Network.setUserAgentOverride",
                {
                    "userAgent": hints["userAgent"],
                    "acceptLanguage": browser_features.get("accept_lang") or "zh-CN,zh;q=0.9",
                    "platform": hints["platform"],
                    "userAgentMetadata": {
                        "brands": hints["brands"],
                        "fullVersionList": hints["fullVersionList"],
                        "fullVersion": hints["fullVersion"],
                        "platform": hints["platform"],
                        "platformVersion": hints["platformVersion"],
                        "architecture": hints["architecture"],
                        "bitness": hints["bitness"],
                        "model": hints["model"],
                        "mobile": hints["mobile"],
                        "wow64": hints["wow64"],
                    },
                },
            )
            logger.info(f"【{self.pure_user_id}】已应用无头浏览器 UA/Client-Hints 网络层伪装")
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】应用无头网络层指纹伪装失败: {e}")

    def _get_stealth_script(self, browser_features):
        """获取更接近真实桌面 Chrome 的反检测脚本。"""
        client_hints = self._build_client_hint_profile(browser_features)
        brands_json = json.dumps(client_hints["brands"], ensure_ascii=False)
        full_version_list_json = json.dumps(client_hints["fullVersionList"], ensure_ascii=False)

        return f"""
            (() => {{
                const defineGetter = (target, key, getter) => {{
                    try {{
                        Object.defineProperty(target, key, {{
                            get: getter,
                            configurable: true
                        }});
                    }} catch (e) {{}}
                }};

                const locale = {json.dumps(browser_features['locale'], ensure_ascii=False)};
                const languages = [locale, 'zh', 'en'];
                const pluginNames = [
                    'PDF Viewer',
                    'Chrome PDF Viewer',
                    'Chromium PDF Viewer',
                    'WebKit built-in PDF'
                ].slice(0, Math.max(1, {int(browser_features['plugin_count'])}));
                const mimeTypes = [
                    {{
                        type: 'application/pdf',
                        suffixes: 'pdf',
                        description: 'Portable Document Format'
                    }},
                    {{
                        type: 'text/pdf',
                        suffixes: 'pdf',
                        description: 'Portable Document Format'
                    }}
                ];

                const makePluginArray = () => {{
                    const arr = pluginNames.map((name) => ({{
                        name,
                        filename: name.toLowerCase().replace(/\\s+/g, '-') + '.dll',
                        description: name,
                        length: 1,
                        0: mimeTypes[0]
                    }}));
                    arr.item = (i) => arr[i] || null;
                    arr.namedItem = (name) => arr.find(p => p.name === name) || null;
                    return arr;
                }};

                const makeMimeTypeArray = () => {{
                    const arr = mimeTypes.map((item) => Object.assign({{}}, item));
                    arr.item = (i) => arr[i] || null;
                    arr.namedItem = (name) => arr.find(p => p.type === name) || null;
                    return arr;
                }};

                const uaData = {{
                    brands: {brands_json},
                    mobile: {str(bool(browser_features['is_mobile'])).lower()},
                    platform: {json.dumps(client_hints['platform'], ensure_ascii=False)},
                    getHighEntropyValues: async (hints) => {{
                        const payload = {{
                            architecture: {json.dumps(client_hints['architecture'])},
                            bitness: {json.dumps(client_hints['bitness'])},
                            brands: {brands_json},
                            fullVersionList: {full_version_list_json},
                            mobile: {str(bool(client_hints['mobile'])).lower()},
                            model: {json.dumps(client_hints['model'])},
                            platform: {json.dumps(client_hints['platform'], ensure_ascii=False)},
                            platformVersion: {json.dumps(client_hints['platformVersion'])},
                            uaFullVersion: {json.dumps(client_hints['fullVersion'])},
                            wow64: {str(bool(client_hints['wow64'])).lower()}
                        }};
                        if (!Array.isArray(hints) || hints.length === 0) {{
                            return payload;
                        }}
                        const result = {{}};
                        for (const key of hints) {{
                            if (Object.prototype.hasOwnProperty.call(payload, key)) {{
                                result[key] = payload[key];
                            }}
                        }}
                        return result;
                    }},
                    toJSON() {{
                        return {{
                            brands: this.brands,
                            mobile: this.mobile,
                            platform: this.platform
                        }};
                    }}
                }};

                // real Chrome keeps navigator.webdriver as a present boolean-like property,
                // deleting it entirely is itself a detectable anomaly.
                defineGetter(Navigator.prototype, 'webdriver', () => false);
                defineGetter(Navigator.prototype, 'languages', () => languages);
                defineGetter(Navigator.prototype, 'plugins', () => makePluginArray());
                defineGetter(Navigator.prototype, 'mimeTypes', () => makeMimeTypeArray());
                defineGetter(Navigator.prototype, 'platform', () => {json.dumps(browser_features['platform'], ensure_ascii=False)});
                defineGetter(Navigator.prototype, 'vendor', () => {json.dumps(browser_features['vendor'], ensure_ascii=False)});
                defineGetter(Navigator.prototype, 'userAgent', () => {json.dumps(browser_features['user_agent'])});
                defineGetter(Navigator.prototype, 'hardwareConcurrency', () => {int(browser_features['hardware_concurrency'])});
                defineGetter(Navigator.prototype, 'deviceMemory', () => {int(browser_features['device_memory'])});
                defineGetter(Navigator.prototype, 'maxTouchPoints', () => {int(browser_features['max_touch_points'])});
                defineGetter(Navigator.prototype, 'userAgentData', () => uaData);
                defineGetter(Navigator.prototype, 'pdfViewerEnabled', () => true);
                defineGetter(Navigator.prototype, 'doNotTrack', () => {json.dumps(browser_features['do_not_track'])});
                defineGetter(window, 'outerWidth', () => {int(browser_features['viewport_width'])});
                defineGetter(window, 'outerHeight', () => {int(browser_features['viewport_height']) + 88});
                defineGetter(screen, 'width', () => {int(browser_features['viewport_width'])});
                defineGetter(screen, 'height', () => {int(browser_features['viewport_height'])});
                defineGetter(screen, 'availWidth', () => {int(browser_features['viewport_width'])});
                defineGetter(screen, 'availHeight', () => {int(browser_features['viewport_height']) - 40});
                defineGetter(screen, 'colorDepth', () => {int(browser_features['color_depth'])});
                defineGetter(screen, 'pixelDepth', () => {int(browser_features['color_depth'])});

                defineGetter(Navigator.prototype, 'connection', () => ({{
                    effectiveType: {json.dumps(browser_features['connection_type'])},
                    rtt: {int(browser_features['connection_rtt'])},
                    downlink: {float(browser_features['connection_downlink'])},
                    saveData: false
                }}));

                if (!window.chrome) {{
                    window.chrome = {{}};
                }}
                window.chrome.runtime = window.chrome.runtime || {{}};
                window.chrome.app = window.chrome.app || {{
                    InstallState: {{
                        DISABLED: 'disabled',
                        INSTALLED: 'installed',
                        NOT_INSTALLED: 'not_installed'
                    }},
                    RunningState: {{
                        CANNOT_RUN: 'cannot_run',
                        READY_TO_RUN: 'ready_to_run',
                        RUNNING: 'running'
                    }},
                    getDetails: () => null,
                    getIsInstalled: () => false,
                    runningState: () => 'cannot_run'
                }};
                window.chrome.csi = window.chrome.csi || (() => ({{}}));
                window.chrome.loadTimes = window.chrome.loadTimes || (() => ({{}}));

                const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
                if (originalQuery) {{
                    window.navigator.permissions.query = (parameters) => {{
                        const name = parameters && parameters.name;
                        if (name === 'notifications') {{
                            return Promise.resolve({{
                                state: {json.dumps(browser_features['notification_permission'])},
                                onchange: null
                            }});
                        }}
                        return originalQuery(parameters);
                    }};
                }}

                if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {{
                    navigator.mediaDevices.enumerateDevices = async () => ([
                        {{
                            deviceId: 'default',
                            kind: 'audioinput',
                            label: '',
                            groupId: 'default'
                        }},
                        {{
                            deviceId: 'default',
                            kind: 'audiooutput',
                            label: '',
                            groupId: 'default'
                        }}
                    ]);
                }}

                const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                    if (parameter === 37445) return 'Google Inc. (Intel)';
                    if (parameter === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)';
                    return originalGetParameter.call(this, parameter);
                }};

                const originalToString = Function.prototype.toString;
                Function.prototype.toString = function() {{
                    if (this === window.navigator.permissions.query) {{
                        return 'function query() {{ [native code] }}';
                    }}
                    return originalToString.call(this);
                }};

                delete window.playwright;
                delete window.__playwright;
                delete window.__pw_manual;
                delete window.__pw_original;
                delete window.webdriver;
                delete window.__webdriver_script_fn;
                delete window.__webdriver_evaluate;
                delete window.__webdriver_unwrapped;
                delete window.__fxdriver_evaluate;
                delete window.__driver_evaluate;
                delete window.__webdriver_script_func;
                delete window._selenium;
                delete window._phantom;
                delete window.callPhantom;
                delete window.phantom;
            }})();
        """

    def _generate_physics_trajectory(self, distance: float):
        """基于物理加速度模型生成轨迹 - 极速模式（增强随机性）
        
        优化策略：
        1. 极少轨迹点（5-8步）：快速完成
        2. 持续加速：一气呵成，不减速
        3. 确保超调50%以上：保证滑动到位
        4. 无回退：单向滑动
        5. 每次都有随机变化：步数、速度、曲线都随机
        
        注意：此方法已被参数化版本取代，保留用于兼容性
        """
        # 生成随机参数
        overshoot_ratio = random.uniform(2.0, 2.2)
        steps = random.randint(5, 8)
        base_delay = random.uniform(0.0002, 0.0006)
        acceleration_curve = random.uniform(1.3, 1.8)
        y_jitter_max = random.uniform(1, 3)
        
        # 调用参数化版本
        return self._generate_physics_trajectory_with_params(
            distance, overshoot_ratio, steps, base_delay,
            acceleration_curve, y_jitter_max
        )

    def _get_effective_learning_ranges(self, optimized_params: Dict[str, Any]) -> Dict[str, Tuple[float, float]]:
        """统一整理学习参数边界，确保不同重试分支使用一致口径"""
        bounds = ML_STRATEGY_CONFIG.get("learning_bounds", {})

        learned_overshoot = optimized_params.get("learned_overshoot_range", (1.03, 1.08))
        learned_overshoot = (
            max(bounds.get("min_overshoot_ratio", 1.01), learned_overshoot[0]),
            min(bounds.get("max_overshoot_ratio", 1.15), learned_overshoot[1])
        )

        learned_delay = optimized_params.get("learned_delay_range", (0.006, 0.012))
        learned_curve = optimized_params.get("learned_curve_range", (1.6, 2.0))

        learned_jitter = optimized_params.get("learned_jitter_range", (1.5, 2.2))
        learned_jitter = (
            max(bounds.get("min_y_jitter", 1.0), learned_jitter[0]),
            min(bounds.get("max_y_jitter", 3.0), learned_jitter[1])
        )

        learned_steps = optimized_params.get("learned_steps_range", (22, 30))
        learned_steps = (
            max(20, min(40, int(learned_steps[0]))),
            max(20, min(40, int(learned_steps[1]))),
        )
        if learned_steps[0] > learned_steps[1]:
            learned_steps = (learned_steps[1], learned_steps[0])
        if learned_steps[1] - learned_steps[0] < 2:
            learned_steps = (learned_steps[0], min(40, learned_steps[0] + 2))

        return {
            "overshoot": learned_overshoot,
            "delay": learned_delay,
            "curve": learned_curve,
            "jitter": learned_jitter,
            "steps": learned_steps,
            "bounds": bounds,
        }

    def _generate_upstream_three_phase_trajectory(self, distance: float):
        """Generate the upstream-style no-overshoot three-phase trajectory."""
        trajectory = []
        total_steps = random.randint(5, 6)
        total_duration = random.uniform(0.010, 0.020)
        avg_delay = total_duration / total_steps

        accel_steps = max(2, int(round(total_steps * (0.35 + random.uniform(-0.05, 0.05)))))
        decel_steps = max(2, int(round(total_steps * (0.30 + random.uniform(-0.05, 0.05)))))
        const_steps = max(2, total_steps - accel_steps - decel_steps)
        total_steps = accel_steps + const_steps + decel_steps

        accel_dist = distance * (0.30 + random.uniform(-0.05, 0.05))
        const_dist = distance * (0.55 + random.uniform(-0.05, 0.05))
        decel_dist = distance - accel_dist - const_dist

        for i in range(1, accel_steps + 1):
            t = i / accel_steps
            trajectory.append((
                accel_dist * (t * t),
                random.uniform(-1.0, 1.0),
                avg_delay * random.uniform(1.0, 1.3),
            ))

        for i in range(1, const_steps + 1):
            t = i / const_steps
            delay = avg_delay * random.uniform(0.85, 1.15)
            if random.random() < 0.03:
                delay *= random.uniform(1.1, 1.3)
            trajectory.append((
                accel_dist + const_dist * t,
                random.uniform(-1.0, 1.0) * 0.6,
                delay,
            ))

        decel_base_x = accel_dist + const_dist
        for i in range(1, decel_steps + 1):
            t = i / decel_steps
            trajectory.append((
                decel_base_x + decel_dist * (1 - (1 - t) ** 2),
                random.uniform(-1.0, 1.0) * 0.4,
                avg_delay * random.uniform(1.1, 1.5),
            ))

        if trajectory:
            _, last_y, last_delay = trajectory[-1]
            trajectory[-1] = (distance, last_y, last_delay)

        logger.info(
            f"【{self.pure_user_id}】🧍 原仓库风格三阶段轨迹: {total_steps}步 "
            f"(加速{accel_steps}/匀速{const_steps}/减速{decel_steps})、"
            f"总时长≈{total_duration * 1000:.0f}ms、距离{distance:.1f}px（无超调）"
        )
        return trajectory
    
    def generate_human_trajectory(self, distance: float, attempt: int = 1):
        """生成人类化滑动轨迹 - 只使用极速物理模型（带智能学习+失败后增加扰动）
        
        Args:
            distance: 滑动距离
            attempt: 当前尝试次数（从1开始），用于在失败后增加随机扰动
            
        🔧 优化说明（基于成功案例分析 + 机器学习策略）：
        - 成功超调比例: 1.79-2.05 (中位数1.97)
        - 成功步数: 6-8步
        - 成功延迟: 0.0003-0.0006秒
        - 成功加速曲线: 1.35-1.7 (中位数1.52)
        - 成功Y抖动: 1.3-2.55像素
        - 成功总耗时: 0.9-1.55秒
        
        🎰 当前重试策略：
        - 第1次优先利用历史成功参数
        - 第2次继续利用，但主动放慢节奏
        - 第3次切换到更果断的高收益分支，不再使用 slow_fallback
        """
        try:
            # 记录轨迹生成前的随机种子状态（用于分析）
            random_state_snapshot = random.getstate()[1][:5]  # 记录前5个随机状态
            
            # 🧠 尝试从历史成功数据中学习最优参数
            optimized_params = self._optimize_trajectory_params(reference_distance=distance)
            force_explore_threshold = ML_STRATEGY_CONFIG.get("force_explore_after_failures", 2)
            slow_fallback_threshold = max(3, force_explore_threshold + 1)
            has_learning = optimized_params.get("learning_enabled") and optimized_params.get("history_count", 0) >= 3
            failure_pressure = self._has_recent_failure_pressure(reference_distance=distance)
            if failure_pressure:
                has_learning = False
                optimized_params = dict(self.trajectory_params)
            effective_ranges = self._get_effective_learning_ranges(optimized_params)
            bounds = effective_ranges["bounds"]

            use_exploration = False
            selected_strategy = None
            profile_name = "primary"

            if attempt >= slow_fallback_threshold:
                # 第 3 次及以后：优先使用 learned 变体（加大抖动），无学习数据时才轮换
                if has_learning:
                    if self._is_password_login_scene() and attempt >= max(4, self.slider_max_retries):
                        template_ranges = self._get_password_scene_final_retry_template(effective_ranges, bounds)
                        selected_strategy = "learned_password_template"
                        profile_name = "password_scene_final_template"
                        overshoot_ratio = random.uniform(template_ranges["overshoot"][0], template_ranges["overshoot"][1])
                        steps = random.randint(template_ranges["steps"][0], template_ranges["steps"][1])
                        base_delay = random.uniform(template_ranges["delay"][0], template_ranges["delay"][1])
                        acceleration_curve = random.uniform(template_ranges["curve"][0], template_ranges["curve"][1])
                        y_jitter_max = random.uniform(template_ranges["jitter"][0], template_ranges["jitter"][1])

                        logger.info(
                            f"【{self.pure_user_id}】🧷 第{attempt}次尝试，账密无头最终模板回放: "
                            f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                            f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                        )
                    else:
                        # 🔧 优化：第3次仍然使用学习参数，但加大抖动幅度以增加多样性
                        selected_strategy = "learned_with_jitter"
                        profile_name = "retry_learned_aggressive_jitter"

                        jitter_config = ML_STRATEGY_CONFIG.get("param_jitter", {})
                        # 第3次使用更大的抖动幅度（原来的2倍）
                        overshoot_jitter = jitter_config.get("overshoot_ratio_jitter", 0.05) * 2.0

                        overshoot_ratio = random.uniform(effective_ranges["overshoot"][0], effective_ranges["overshoot"][1])
                        overshoot_ratio *= random.uniform(1 - overshoot_jitter, 1 + overshoot_jitter)
                        overshoot_ratio = max(1.01, min(bounds.get("max_overshoot_ratio", 1.18), overshoot_ratio))

                        # 步数和延迟也加大变化范围
                        steps_min = max(18, effective_ranges["steps"][0] - 3)
                        steps_max = min(42, effective_ranges["steps"][1] + 5)
                        steps = random.randint(steps_min, steps_max)

                        delay_min = max(0.004, effective_ranges["delay"][0] * 0.85)
                        delay_max = min(0.022, effective_ranges["delay"][1] * 1.5)
                        base_delay = random.uniform(delay_min, delay_max)

                        curve_min = max(1.2, effective_ranges["curve"][0] - 0.2)
                        curve_max = min(2.6, effective_ranges["curve"][1] + 0.2)
                        acceleration_curve = random.uniform(curve_min, curve_max)

                        jitter_min = max(0.8, effective_ranges["jitter"][0] - 0.3)
                        jitter_max = min(3.5, effective_ranges["jitter"][1] + 0.5)
                        y_jitter_max = random.uniform(jitter_min, jitter_max)

                        logger.info(
                            f"【{self.pure_user_id}】🛟 第{attempt}次尝试，使用学习参数(大抖动): "
                            f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                            f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                        )
                else:
                    if self._should_prefer_docker_conservative_profile(has_learning):
                        rotation_strategies = ["conservative", "standard"]
                    else:
                        rotation_strategies = ["aggressive", "standard"]
                    rotation_idx = (attempt - slow_fallback_threshold) % len(rotation_strategies)
                    selected_strategy = rotation_strategies[rotation_idx]
                    profile_name = f"retry_rotation_{selected_strategy}"

                    strategy_config = ML_STRATEGY_CONFIG["strategies"][selected_strategy]
                    overshoot_ratio = random.uniform(*strategy_config["overshoot_ratio"])
                    steps = random.randint(*strategy_config["steps"])
                    base_delay = random.uniform(*strategy_config["base_delay"])
                    acceleration_curve = random.uniform(*strategy_config["acceleration_curve"])
                    y_jitter_max = random.uniform(*strategy_config["y_jitter_max"])

                    logger.info(
                        f"【{self.pure_user_id}】🛟 第{attempt}次尝试，轮换策略[{selected_strategy}]: "
                        f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                        f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                    )
            elif attempt == 2 and self._should_prefer_docker_conservative_profile(has_learning):
                selected_strategy = "conservative"
                profile_name = "docker_retry_conservative"

                overshoot_ratio = random.uniform(1.015, 1.045)
                steps = random.randint(32, 42)
                base_delay = random.uniform(0.011, 0.019)
                acceleration_curve = random.uniform(1.95, 2.30)
                y_jitter_max = random.uniform(0.9, 1.8)

                logger.info(
                    f"【{self.pure_user_id}】🧱 Docker第2次继续保守策略: "
                    f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                    f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                )
            elif attempt == 2 and has_learning:
                selected_strategy = "learned_with_jitter"
                profile_name = "retry_stabilized"

                jitter_config = ML_STRATEGY_CONFIG.get("param_jitter", {})
                overshoot_jitter = jitter_config.get("overshoot_ratio_jitter", 0.05)

                overshoot_ratio = random.uniform(effective_ranges["overshoot"][0], effective_ranges["overshoot"][1])
                overshoot_ratio *= random.uniform(1 - overshoot_jitter, 1 + overshoot_jitter)
                overshoot_ratio = max(1.01, min(bounds.get("max_overshoot_ratio", 1.18), overshoot_ratio))

                steps_min = max(24, effective_ranges["steps"][0])
                steps_max = min(40, max(steps_min + 2, effective_ranges["steps"][1] + 5))
                if steps_max < steps_min:
                    steps_max = min(40, steps_min)
                steps = random.randint(steps_min, steps_max)

                delay_min = max(0.007, effective_ranges["delay"][0] * 1.10)
                delay_max = min(0.020, max(delay_min + 0.002, effective_ranges["delay"][1] * 1.35))
                base_delay = random.uniform(delay_min, delay_max)

                curve_min = max(1.45, effective_ranges["curve"][0] - 0.10)
                curve_max = min(2.40, max(curve_min + 0.15, effective_ranges["curve"][1] + 0.10))
                acceleration_curve = random.uniform(curve_min, curve_max)

                jitter_min = max(1.2, effective_ranges["jitter"][0])
                jitter_max = min(bounds.get("max_y_jitter", 3.5), max(jitter_min + 0.3, effective_ranges["jitter"][1] + 0.3))
                y_jitter_max = random.uniform(jitter_min, jitter_max)

                logger.info(
                    f"【{self.pure_user_id}】🧩 第2次尝试继续利用学习参数并放慢节奏: "
                    f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                    f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                )
            else:
                exploration_rate = ML_STRATEGY_CONFIG.get("exploration_rate", 0.35)
                if failure_pressure:
                    overshoot_ratio = 1.0
                    steps = random.randint(5, 6)
                    base_delay = random.uniform(0.010, 0.020)
                    acceleration_curve = 0.0
                    y_jitter_max = 1.0
                    selected_strategy = "upstream_three_phase"
                    profile_name = "failure_pressure_upstream_three_phase"
                    logger.info(
                        f"【{self.pure_user_id}】🧱 近期失败压力过高，改用原仓库风格无超调三阶段策略: "
                        f"步数{steps}, 延迟{base_delay*1000:.1f}ms"
                    )
                elif self._should_force_docker_cold_start_conservative(attempt, has_learning):
                    conservative = ML_STRATEGY_CONFIG["strategies"]["conservative"]
                    overshoot_ratio = random.uniform(*conservative["overshoot_ratio"])
                    steps = random.randint(*conservative["steps"])
                    base_delay = random.uniform(*conservative["base_delay"])
                    acceleration_curve = random.uniform(*conservative["acceleration_curve"])
                    y_jitter_max = random.uniform(*conservative["y_jitter_max"])
                    selected_strategy = "conservative"
                    profile_name = "docker_cold_start_conservative"
                    logger.info(
                        f"【{self.pure_user_id}】🧱 Docker冷启动优先保守策略: "
                        f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                        f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                    )
                elif not has_learning and random.random() < exploration_rate:
                    use_exploration = True
                    overshoot_ratio, steps, base_delay, acceleration_curve, y_jitter_max, selected_strategy = \
                        self._select_exploration_strategy(attempt)
                    profile_name = "cold_start_exploration"
                    logger.info(
                        f"【{self.pure_user_id}】🎯 冷启动探索策略[{selected_strategy}]: "
                        f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                        f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                    )
                elif has_learning:
                    logger.info(f"【{self.pure_user_id}】📐 利用模式：使用学习参数 "
                               f"(基于{optimized_params['history_count']}条记录)")

                    # 添加参数抖动（防止模式被识别）
                    jitter_config = ML_STRATEGY_CONFIG.get("param_jitter", {})
                    overshoot_jitter = jitter_config.get("overshoot_ratio_jitter", 0.03)
                    
                    overshoot_ratio = random.uniform(effective_ranges["overshoot"][0], effective_ranges["overshoot"][1])
                    overshoot_ratio *= random.uniform(1 - overshoot_jitter, 1 + overshoot_jitter)
                    overshoot_ratio = max(1.01, min(bounds.get("max_overshoot_ratio", 1.18), overshoot_ratio))
                    
                    steps = random.randint(effective_ranges["steps"][0], effective_ranges["steps"][1])
                    base_delay = random.uniform(effective_ranges["delay"][0], effective_ranges["delay"][1])
                    acceleration_curve = random.uniform(effective_ranges["curve"][0], effective_ranges["curve"][1])
                    y_jitter_max = random.uniform(effective_ranges["jitter"][0], effective_ranges["jitter"][1])
                    
                    selected_strategy = "learned_with_jitter"
                    profile_name = "primary"
                    logger.info(f"【{self.pure_user_id}】🎯 应用学习参数(带抖动): 超调{(overshoot_ratio-1)*100:.1f}%, "
                               f"步数{steps}, 延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}")
                elif attempt == 1 and self._use_headless_stable_profile():
                    overshoot_ratio = random.uniform(1.03, 1.08)
                    steps = random.randint(23, 34)
                    base_delay = random.uniform(0.008, 0.0135)
                    acceleration_curve = random.uniform(1.68, 2.00)
                    y_jitter_max = random.uniform(1.35, 2.40)

                    selected_strategy = "headless_stable"
                    profile_name = "cold_start_headless_stable"
                    logger.info(
                        f"【{self.pure_user_id}】🎯 使用无头稳定画像策略: "
                        f"超调{(overshoot_ratio-1)*100:.1f}%, 步数{steps}, "
                        f"延迟{base_delay*1000:.1f}ms, 曲线^{acceleration_curve:.2f}"
                    )
                else:
                    # 使用标准策略
                    standard = ML_STRATEGY_CONFIG["strategies"]["standard"]
                    overshoot_ratio = random.uniform(standard["overshoot_ratio"][0], standard["overshoot_ratio"][1])
                    steps = random.randint(standard["steps"][0], standard["steps"][1])
                    base_delay = random.uniform(standard["base_delay"][0], standard["base_delay"][1])
                    acceleration_curve = random.uniform(standard["acceleration_curve"][0], standard["acceleration_curve"][1])
                    y_jitter_max = random.uniform(standard["y_jitter_max"][0], standard["y_jitter_max"][1])
                    selected_strategy = "standard"
                    profile_name = "cold_start_standard"
                    logger.info(f"【{self.pure_user_id}】📐 使用标准策略: 超调{(overshoot_ratio-1)*100:.1f}%, "
                               f"步数{steps}, 延迟{base_delay*1000:.1f}ms")
            
            # 生成轨迹（使用上面预生成的参数）
            if selected_strategy == "upstream_three_phase":
                trajectory = self._generate_upstream_three_phase_trajectory(distance)
            else:
                trajectory = self._generate_physics_trajectory_with_params(
                    distance, overshoot_ratio, steps, base_delay,
                    acceleration_curve, y_jitter_max
                )
            
            logger.debug(f"【{self.pure_user_id}】轨迹模式: 贝塞尔超调后回退，执行配置={selected_strategy}/{profile_name}")
            
            # 保存轨迹数据（包含所有随机参数）
            self.current_trajectory_data = {
                "distance": distance,
                "model": "physics_fast_learned" if optimized_params.get("learning_enabled") else "physics_fast",
                "browser_profile_id": self.profile_id,
                "headless": self.headless,
                "total_steps": len(trajectory),
                "trajectory_points": trajectory.copy(),
                "final_left_px": 0,
                "completion_used": False,
                "completion_steps": 0,
                # 新增：记录所有随机参数
                "random_params": {
                    "overshoot_ratio": overshoot_ratio,
                    "steps": steps,
                    "base_delay": base_delay,
                    "acceleration_curve": acceleration_curve,
                    "y_jitter_max": y_jitter_max,
                    "random_state_snapshot": list(random_state_snapshot),
                    "is_learned": optimized_params.get("learning_enabled", False),
                    # 🎰 新增：记录使用的策略名称
                    "strategy": selected_strategy if selected_strategy else "unknown",
                    "profile": profile_name,
                    "use_exploration": use_exploration,
                }
            }
            
            return trajectory
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】生成轨迹时出错: {str(e)}")
            return []
    
    def _select_exploration_strategy(self, attempt: int):
        """🎰 探索策略选择（机器学习多臂老虎机思想 + 自适应权重）
        
        根据尝试次数和动态权重选择不同的策略
        
        Returns:
            tuple: (overshoot_ratio, steps, base_delay, acceleration_curve, y_jitter_max, strategy_name)
        """
        strategies = ML_STRATEGY_CONFIG.get("strategies", {})
        
        # 🤖 使用自适应策略管理器获取动态权重
        try:
            weights = adaptive_strategy_manager.get_dynamic_weights(attempt)
            logger.debug(f"【{self.pure_user_id}】🤖 使用自适应权重: "
                        f"保守={weights.get('conservative', 0)*100:.1f}%, "
                        f"标准={weights.get('standard', 0)*100:.1f}%, "
                        f"激进={weights.get('aggressive', 0)*100:.1f}%")
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】获取动态权重失败: {e}，使用默认权重")
            # 回退到静态权重
            if attempt <= 2:
                weights = {"conservative": 0.18, "standard": 0.52, "aggressive": 0.30}
            elif attempt == 3:
                weights = {"conservative": 0.12, "standard": 0.38, "aggressive": 0.50}
            else:
                weights = {"conservative": 0.10, "standard": 0.30, "aggressive": 0.60}
        
        # 按权重随机选择策略
        rand_val = random.random()
        cumulative = 0
        selected_name = "standard"
        
        for name, weight in weights.items():
            cumulative += weight
            if rand_val <= cumulative:
                selected_name = name
                break
        
        strategy = strategies.get(selected_name, strategies["standard"])
        
        # 从选中的策略中随机生成参数
        overshoot_ratio = random.uniform(strategy["overshoot_ratio"][0], strategy["overshoot_ratio"][1])
        steps = random.randint(strategy["steps"][0], strategy["steps"][1])
        base_delay = random.uniform(strategy["base_delay"][0], strategy["base_delay"][1])
        acceleration_curve = random.uniform(strategy["acceleration_curve"][0], strategy["acceleration_curve"][1])
        y_jitter_max = random.uniform(strategy["y_jitter_max"][0], strategy["y_jitter_max"][1])
        
        # 添加额外的随机扰动（防止模式识别）
        jitter_config = ML_STRATEGY_CONFIG.get("param_jitter", {})
        
        # 对超调比例添加随机扰动
        overshoot_jitter = jitter_config.get("overshoot_ratio_jitter", 0.08)
        overshoot_ratio *= random.uniform(1 - overshoot_jitter/2, 1 + overshoot_jitter/2)
        
        # 对延迟添加随机扰动
        delay_jitter = jitter_config.get("delay_jitter", 0.12)
        base_delay *= random.uniform(1 - delay_jitter/2, 1 + delay_jitter/2)
        
        # 对加速曲线添加随机扰动
        curve_jitter = jitter_config.get("curve_jitter", 0.08)
        acceleration_curve *= random.uniform(1 - curve_jitter/2, 1 + curve_jitter/2)
        
        # 🔧 2025-12-25：确保参数在新的合理范围内
        bounds = ML_STRATEGY_CONFIG.get("learning_bounds", {})
        overshoot_ratio = max(bounds.get("min_overshoot_ratio", 1.01), 
                              min(bounds.get("max_overshoot_ratio", 1.15), overshoot_ratio))
        y_jitter_max = max(bounds.get("min_y_jitter", 1.0), 
                           min(bounds.get("max_y_jitter", 3.0), y_jitter_max))
        base_delay = max(0.003, min(0.020, base_delay))  # 3-20ms
        acceleration_curve = max(1.3, min(2.5, acceleration_curve))
        
        return overshoot_ratio, steps, base_delay, acceleration_curve, y_jitter_max, selected_name
    
    def _generate_physics_trajectory_with_params(self, distance: float, 
                                                  overshoot_ratio: float,
                                                  steps: int,
                                                  base_delay: float,
                                                  acceleration_curve: float,
                                                  y_jitter_max: float):
        """使用指定参数生成物理轨迹（用于参数记录和复现）
        
        🔧 2025-12-25 重构：使用贝塞尔曲线+真实超调回退+连续Y轴抖动
        """
        trajectory = []
        
        # 尊重上层策略传入的步数，避免“选中的策略”和“实际执行轨迹”脱节
        # Fitts 定律动态步数：距离越长步数越多，距离越短步数越少
        # 基于策略传入的步数，再根据距离做 ±30% 的缩放
        fitts_factor = math.log2(max(1, distance / 50 + 1)) / math.log2(7)  # 归一化到 ~0.5-1.3
        fitts_steps = int(round(steps * max(0.7, min(1.3, fitts_factor))))
        actual_steps = max(18, min(45, fitts_steps))
        
        # 超调目标位置（先滑过，再回退）
        overshoot_target = distance * overshoot_ratio
        
        # === 阶段1：主滑动阶段（使用贝塞尔曲线） ===
        # 控制点设计：模拟人类手部加速-匀速-减速
        main_steps = int(actual_steps * 0.75)  # 75%用于主滑动
        
        # 贝塞尔控制点（三次贝塞尔）
        p0 = 0  # 起点
        p1 = overshoot_target * random.uniform(0.2, 0.35)  # 控制点1（早期加速）
        p2 = overshoot_target * random.uniform(0.7, 0.85)  # 控制点2（后期减速）
        p3 = overshoot_target  # 终点（超调位置）
        
        # Y轴使用 Perlin 噪声（非周期性连续平滑，比 sin 叠加更难被模式识别）
        y_seed1 = random.uniform(0, 1000)  # 低频噪声种子
        y_seed2 = random.uniform(0, 1000)  # 高频噪声种子
        y_freq1 = random.uniform(2.0, 4.0)  # 低频采样频率（手臂移动）
        y_freq2 = random.uniform(6.0, 10.0)  # 高频采样频率（手指颤抖）
        # 延迟也使用 Perlin 生成连续变化（同一次滑动中各点延迟相关联）
        delay_seed = random.uniform(0, 1000)
        
        prev_x = 0
        prev_y = 0
        
        for i in range(main_steps):
            # 进度 0->1，使用非线性进度模拟加速减速
            t = (i + 1) / main_steps
            
            # 使用ease-out曲线（开始快，结束慢）
            eased_t = 1 - (1 - t) ** acceleration_curve
            
            # 三次贝塞尔曲线计算X位置
            x = (1-eased_t)**3 * p0 + \
                3*(1-eased_t)**2 * eased_t * p1 + \
                3*(1-eased_t) * eased_t**2 * p2 + \
                eased_t**3 * p3
            
            # Perlin 噪声 Y 轴波动（叠加低频+高频，非周期性）
            y_low = perlin_octaves_1d(t * y_freq1, octaves=2, seed_offset=y_seed1) * y_jitter_max * 0.65
            y_high = perlin_noise_1d(t * y_freq2, seed_offset=y_seed2) * y_jitter_max * 0.35
            y = y_low + y_high + random.uniform(-0.2, 0.2)  # 微小随机噪声

            # Perlin 连续延迟：开始和结束慢，中间快，且相邻点延迟相关联
            speed_factor = math.sin(t * 3.14159)  # 基础速度包络仍用 sin（0->1->0）
            if speed_factor < 0.1:
                speed_factor = 0.1
            
            # 基础延迟 + 速度调整 + Perlin 连续抖动（相邻点的延迟有平滑关联）
            delay_jitter = 1.0 + perlin_noise_1d(t * 5.0, seed_offset=delay_seed) * 0.15  # ±15% 连续波动
            delay = base_delay / speed_factor * delay_jitter
            
            # 中间可能有微小停顿（8%概率，模拟人类犹豫/调整）
            if 0.2 < t < 0.8 and random.random() < 0.08:
                delay += random.uniform(0.01, 0.03)
            
            # 添加微小位移抖动（生理性颤抖，±0.5px）
            x += random.uniform(-0.5, 0.5)
            
            trajectory.append((x, y, delay))
            prev_x, prev_y = x, y
        
        # === 阶段2：回退阶段（从超调位置回退到目标） ===
        # 5-10%的回退距离
        retreat_steps = int(actual_steps * 0.25)
        retreat_distance = overshoot_target - distance  # 需要回退的距离
        
        if retreat_steps > 0 and retreat_distance > 0:
            for i in range(retreat_steps):
                t = (i + 1) / retreat_steps
                
                # 回退使用ease-in-out（开始慢，中间快，结束慢）
                eased_t = t * t * (3 - 2 * t)  # smoothstep
                
                # 从超调位置回退到目标
                x = overshoot_target - retreat_distance * eased_t
                
                # Y轴继续波动
                y = prev_y * (1 - t) + random.uniform(-y_jitter_max * 0.3, y_jitter_max * 0.3)
                
                # 回退时速度更慢（人类精确调整时更谨慎）
                delay = base_delay * random.uniform(1.2, 1.8)
                
                # 微小位移抖动
                x += random.uniform(-0.3, 0.3)
                
                trajectory.append((x, y, delay))
                prev_x, prev_y = x, y
        
        # === 阶段3：最终微调（模拟人类精确对齐） ===
        # 随机添加1-3个微调点
        fine_tune_count = random.randint(1, 3)
        for _ in range(fine_tune_count):
            # 在目标位置附近做微小调整
            x = distance + random.uniform(-1.5, 1.5)
            y = random.uniform(-y_jitter_max * 0.2, y_jitter_max * 0.2)
            delay = base_delay * random.uniform(0.8, 1.5)
            trajectory.append((x, y, delay))
        
        # 确保最后一个点非常接近目标
        final_x = distance + random.uniform(-0.5, 0.5)
        final_y = random.uniform(-0.2, 0.2)
        trajectory.append((final_x, final_y, base_delay * random.uniform(0.5, 1.0)))
        
        logger.info(f"【{self.pure_user_id}】🎯 贝塞尔轨迹：{len(trajectory)}步，"
                   f"超调{(overshoot_ratio-1)*100:.0f}%→回退到目标，"
                   f"加速曲线^{acceleration_curve:.2f}")
        return trajectory
    
    def simulate_slide(self, slider_button: ElementHandle, trajectory):
        """模拟滑动 - 优化版本（增强随机性+智能学习）"""
        try:
            # 🧠 获取学习到的行为参数
            reference_distance = ((getattr(self, 'current_trajectory_data', {}) or {}).get("distance"))
            optimized_params = self._optimize_trajectory_params(reference_distance=reference_distance)
            learned_behavior = optimized_params.get("learned_behavior", {})
            is_learned = optimized_params.get("learning_enabled", False) and len(learned_behavior) > 0

            if is_learned:
                logger.info(f"【{self.pure_user_id}】🧠 应用学习到的滑动行为参数（{len(learned_behavior)}个）")
            else:
                logger.info(f"【{self.pure_user_id}】开始优化滑动模拟...")

            current_profile = str(
                ((getattr(self, "current_trajectory_data", {}) or {}).get("random_params", {}) or {}).get("profile", "")
            )
            stable_headless_profile = current_profile == "cold_start_headless_stable"

            # 🎭 用户速度人格因子：模拟同一个人各阶段行为的一致性
            # 快用户 (0.75~0.95) 各阶段等待都偏短，慢用户 (1.05~1.25) 各阶段等待都偏长
            # 使用 Perlin 噪声使各阶段因子有连续相关性，而非完全相同
            _tempo_seed = random.uniform(0, 1000)
            _tempo_base = random.uniform(0.92, 1.10) if stable_headless_profile else random.uniform(0.80, 1.20)
            def _tempo(phase_idx):
                """为第 phase_idx 个阶段生成连续相关的速度因子"""
                noise_val = perlin_noise_1d(phase_idx * 0.8, seed_offset=_tempo_seed)
                return max(0.65, min(1.40, _tempo_base + noise_val * 0.15))
            logger.debug(f"【{self.pure_user_id}】用户速度人格: base={_tempo_base:.2f}")

            # 🎲 随机1：页面稳定等待时间随机化
            # 🔧 优化：根据成功案例，总耗时约0.9-1.55秒，页面等待不宜过长
            page_wait_range = (0.12, 0.24) if stable_headless_profile else (0.08, 0.25)
            page_wait = random.uniform(*page_wait_range) * _tempo(0)
            time.sleep(page_wait)
            
            # 获取滑块按钮中心位置
            button_box = slider_button.bounding_box()
            if not button_box:
                logger.error(f"【{self.pure_user_id}】无法获取滑块按钮位置")
                return False
            
            start_x = button_box["x"] + button_box["width"] / 2
            start_y = button_box["y"] + button_box["height"] / 2
            logger.debug(f"【{self.pure_user_id}】滑块位置: ({start_x}, {start_y})")
            
            # 记录滑动行为参数（用于学习）
            slide_behavior = {}
            
            # 第一阶段：移动到滑块附近（模拟人类寻找滑块）
            # 🔧 优化说明：根据成功案例，接近偏移集中在 X:-9到-22, Y:-2到-18
            try:
                # 🎲 随机2：偏移量随机化（应用学习结果）
                if "approach_offset_x" in learned_behavior:
                    x_range = learned_behavior["approach_offset_x"]
                    offset_x = random.uniform(x_range[0], x_range[1])
                    logger.debug(f"【{self.pure_user_id}】🧠 使用学习的X偏移: {x_range[0]:.1f}~{x_range[1]:.1f}")
                else:
                    # 🔧 修复：成功记录显示X偏移约-23到-24
                    offset_x = random.uniform(-25, -20)
                
                if "approach_offset_y" in learned_behavior:
                    y_range = learned_behavior["approach_offset_y"]
                    offset_y = random.uniform(y_range[0], y_range[1])
                else:
                    # 🔧 修复：成功记录显示Y偏移应为正值（+12到+18）
                    offset_y = random.uniform(12, 18)
                
                slide_behavior['approach_offset_x'] = offset_x
                slide_behavior['approach_offset_y'] = offset_y
                
                # 🎲 随机3：接近步数随机化（应用学习结果）
                # 🔧 优化：成功案例的接近步数集中在 3-12步，但以3-6步居多
                if "approach_steps" in learned_behavior:
                    steps_range = learned_behavior["approach_steps"]
                    approach_steps = random.randint(steps_range[0], steps_range[1])
                    logger.debug(f"【{self.pure_user_id}】🧠 使用学习的接近步数: {steps_range[0]}~{steps_range[1]}")
                else:
                    # 🔧 修复：成功记录显示接近步数约8-9步
                    approach_steps = random.randint(8, 10)
                
                slide_behavior['approach_steps'] = approach_steps
                
                self.page.mouse.move(
                    start_x + offset_x,
                    start_y + offset_y,
                    steps=approach_steps
                )
                
                # 🎲 随机4：接近后停顿随机化（应用学习结果）
                # 🔧 优化：成功案例的接近停顿集中在 0.17-0.36秒
                if "approach_pause" in learned_behavior:
                    pause_range = learned_behavior["approach_pause"]
                    approach_pause = random.uniform(pause_range[0], pause_range[1])
                else:
                    # 🔧 修复：成功记录显示接近停顿约0.05-0.12秒（更短）
                    approach_pause = random.uniform(0.05, 0.15)
                
                slide_behavior['approach_pause'] = approach_pause
                time.sleep(approach_pause * _tempo(1))
                
                # 🎲 随机5：精确定位步数随机化（应用学习结果）
                # 🔧 优化：成功案例的精确定位步数集中在 3-8步
                if "precision_steps" in learned_behavior:
                    steps_range = learned_behavior["precision_steps"]
                    precision_steps = random.randint(steps_range[0], steps_range[1])
                else:
                    # 🔧 修复：成功记录显示精确定位步数约9-10步
                    precision_steps = random.randint(8, 10)
                
                slide_behavior['precision_steps'] = precision_steps
                
                self.page.mouse.move(
                    start_x,
                    start_y,
                    steps=precision_steps
                )
                
                # 🎲 随机6：定位后停顿随机化（应用学习结果）
                # 🔧 优化：成功案例的定位停顿集中在 0.19-0.28秒
                if "precision_pause" in learned_behavior:
                    pause_range = learned_behavior["precision_pause"]
                    precision_pause = random.uniform(pause_range[0], pause_range[1])
                else:
                    # 🔧 修复：成功记录显示精确定位停顿约0.07-0.09秒（更短）
                    precision_pause = random.uniform(0.07, 0.12)
                
                slide_behavior['precision_pause'] = precision_pause
                time.sleep(precision_pause * _tempo(2))
                
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】移动到滑块失败: {e}，继续尝试")
            
            # 第二阶段：悬停在滑块上
            # 🎲 随机7：跳过悬停概率（应用学习结果）
            # 🔧 优化：成功案例中大多数跳过了悬停（skip_hover=true居多）
            if "skip_hover_rate" in learned_behavior:
                skip_hover = random.random() < learned_behavior["skip_hover_rate"]
                logger.debug(f"【{self.pure_user_id}】🧠 使用学习的跳过悬停概率: {learned_behavior['skip_hover_rate']*100:.1f}%")
            else:
                # 🔧 修复：成功记录显示skip_hover=false，降低跳过率到15%
                skip_hover = False if stable_headless_profile else (random.random() < 0.15)
            
            slide_behavior['skip_hover'] = skip_hover
            
            if not skip_hover:
                try:
                    slider_button.hover(timeout=2000)
                    # 🎲 随机8：悬停时间随机化（应用学习结果）
                    if "hover_pause" in learned_behavior:
                        pause_range = learned_behavior["hover_pause"]
                        hover_pause = random.uniform(pause_range[0], pause_range[1])
                    else:
                        hover_pause = random.uniform(0.08, 0.33) if stable_headless_profile else random.uniform(0.05, 0.4)
                    
                    slide_behavior['hover_pause'] = hover_pause
                    time.sleep(hover_pause * _tempo(3))
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】悬停滑块失败: {e}")
            else:
                logger.debug(f"【{self.pure_user_id}】跳过悬停（随机行为）")
            
            # 第三阶段：按下鼠标
            try:
                self.page.mouse.move(start_x, start_y)
                
                # 🎲 随机9：按下前停顿随机化（应用学习结果）
                # 🔧 优化：成功案例的按下前停顿集中在 0.08-0.17秒
                if "pre_down_pause" in learned_behavior:
                    pause_range = learned_behavior["pre_down_pause"]
                    pre_down_pause = random.uniform(pause_range[0], pause_range[1])
                else:
                    # 🔧 修复：成功记录显示按下前停顿约0.12-0.14秒
                    pre_down_pause = random.uniform(0.10, 0.15)
                
                slide_behavior['pre_down_pause'] = pre_down_pause
                time.sleep(pre_down_pause * _tempo(4))
                
                self.page.mouse.down()
                
                # 🎲 随机10：按下后停顿随机化（应用学习结果）
                # 🔧 优化：成功案例的按下后停顿集中在 0.04-0.09秒
                if "post_down_pause" in learned_behavior:
                    pause_range = learned_behavior["post_down_pause"]
                    post_down_pause = random.uniform(pause_range[0], pause_range[1])
                else:
                    # 🔧 修复：成功记录显示按下后停顿约0.12-0.14秒
                    post_down_pause = random.uniform(0.10, 0.15)
                
                slide_behavior['post_down_pause'] = post_down_pause
                time.sleep(post_down_pause * _tempo(5))
                
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】按下鼠标失败: {e}")
                return False
            
            # 第四阶段：执行滑动轨迹
            try:
                start_time = time.time()
                current_x = start_x
                current_y = start_y
                
                # 🔧 2025-12-25 重构：不使用 Playwright 的 steps 参数
                # steps 会生成均匀插值点，这不是人类行为
                # 直接移动到每个轨迹点，轨迹本身已经包含足够的采样点
                
                # 🎲 延迟波动范围随机化
                delay_variation_min = random.uniform(0.85, 0.95)
                delay_variation_max = random.uniform(1.05, 1.15)
                slide_behavior['delay_variation'] = (delay_variation_min, delay_variation_max)
                
                # 记录上一个位置，用于检测大跳跃
                last_x, last_y = 0, 0
                
                # 执行拖动轨迹 - 直接移动到每个点
                trajectory_profile = str(
                    ((getattr(self, "current_trajectory_data", {}) or {}).get("random_params", {}) or {}).get("profile", "")
                )
                use_upstream_steps = trajectory_profile == "failure_pressure_upstream_three_phase"
                for i, (x, y, delay) in enumerate(trajectory):
                    # 更新当前位置
                    current_x = start_x + x
                    current_y = start_y + y
                    
                    if use_upstream_steps:
                        self.page.mouse.move(
                            current_x,
                            current_y,
                            steps=random.randint(1, 3)
                        )
                    else:
                        # 🔧 关键改进：直接移动到目标点，不使用 steps 插值
                        # 如果位移过大（>30px），分多次小步移动以更自然
                        dx = x - last_x
                        dy = y - last_y
                        move_distance = math.sqrt(dx*dx + dy*dy)

                        if move_distance > 30:
                            # 大位移时，分成多个小步
                            sub_steps = max(2, int(move_distance / 15))
                            for j in range(sub_steps):
                                progress = (j + 1) / sub_steps
                                sub_x = start_x + last_x + dx * progress
                                sub_y = start_y + last_y + dy * progress
                                self.page.mouse.move(sub_x, sub_y)
                                # 小步之间只有极短延迟
                                time.sleep(random.uniform(0.001, 0.003))
                        else:
                            # 小位移直接移动
                            self.page.mouse.move(current_x, current_y)
                    
                    last_x, last_y = x, y
                    
                    # 🎲 延迟使用自定义波动范围
                    actual_delay = delay * random.uniform(delay_variation_min, delay_variation_max)
                    
                    # 🎲 随机：8%概率在非首尾点增加额外停顿（模拟人类调整）
                    if 0.15 < (i / len(trajectory)) < 0.85 and random.random() < 0.08:
                        hesitation = random.uniform(0.01, 0.04)
                        actual_delay += hesitation
                        slide_behavior[f'hesitation_at_{i}'] = hesitation
                    
                    time.sleep(actual_delay)
                    
                    # 记录最终位置
                    if i == len(trajectory) - 1:
                        try:
                            current_style = slider_button.get_attribute("style")
                            if current_style and "left:" in current_style:
                                import re
                                left_match = re.search(r'left:\s*([^;]+)', current_style)
                                if left_match:
                                    left_value = left_match.group(1).strip()
                                    left_px = float(left_value.replace('px', ''))
                                    if hasattr(self, 'current_trajectory_data'):
                                        self.current_trajectory_data["final_left_px"] = left_px
                                    logger.info(f"【{self.pure_user_id}】滑动完成: {len(trajectory)}步 - 最终位置: {left_value}")
                        except:
                            pass
                
                # 🎨 刮刮乐特殊处理：在目标位置停顿观察
                is_scratch = self.is_scratch_captcha()
                if is_scratch:
                    # 🎲 随机16：刮刮乐停顿时间随机化（0.2-0.6秒）
                    pause_duration = random.uniform(0.2, 0.6)
                    slide_behavior['scratch_pause'] = pause_duration
                    logger.warning(f"【{self.pure_user_id}】🎨 刮刮乐模式：在目标位置停顿{pause_duration:.2f}秒观察...")
                    time.sleep(pause_duration)
                
                # 🎲 随机17：释放前停顿随机化
                # 🔧 优化：成功案例的释放前停顿集中在 0.01-0.07秒
                pre_up_pause = random.uniform(0.01, 0.07)  # 优化：原0.01-0.08
                slide_behavior['pre_up_pause'] = pre_up_pause
                time.sleep(pre_up_pause * _tempo(6))
                
                # 释放鼠标
                self.page.mouse.up()

                # 释放后短暂停顿（模拟手指离开）
                post_up_pause = random.uniform(0.02, 0.06)
                slide_behavior['post_up_pause'] = post_up_pause
                time.sleep(post_up_pause * _tempo(7))

                try:
                    slider_button.evaluate(
                        """(slider, point) => {
                            const event = new MouseEvent('click', {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                                clientX: point.x,
                                clientY: point.y,
                                button: 0
                            });
                            slider.dispatchEvent(event);
                        }""",
                        {"x": current_x, "y": current_y},
                    )
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】释放后补发click事件失败（可忽略）: {e}")

                # 等待服务端验证判定（关键：阿里滑块验证是异步的，需要给服务端足够时间返回结果）
                if "server_judge_wait" in learned_behavior:
                    wait_range = learned_behavior["server_judge_wait"]
                    server_wait_range = (
                        max(0.8, float(wait_range[0])),
                        max(float(wait_range[0]) + 0.1, float(wait_range[1])),
                    )
                    server_wait_tempo = max(1.0, min(1.2, _tempo(8)))
                elif getattr(self, "risk_trigger_scene", None) == "token_refresh":
                    server_wait_range = (2.2, 4.2) if stable_headless_profile else (2.0, 3.6)
                    server_wait_tempo = max(1.0, min(1.2, _tempo(8)))
                else:
                    server_wait_range = (1.25, 2.10) if stable_headless_profile else (1.0, 2.0)
                    server_wait_tempo = _tempo(8)
                server_judge_wait = random.uniform(*server_wait_range) * server_wait_tempo
                slide_behavior['server_judge_wait'] = server_judge_wait
                logger.debug(f"【{self.pure_user_id}】等待服务端判定: {server_judge_wait:.2f}秒")
                time.sleep(server_judge_wait)

                elapsed_time = time.time() - start_time
                slide_behavior['total_elapsed_time'] = elapsed_time
                slide_behavior['used_learned_params'] = is_learned  # 标记是否使用了学习参数
                
                # 💾 保存滑动行为参数到轨迹数据（用于成功后学习）
                if hasattr(self, 'current_trajectory_data'):
                    self.current_trajectory_data['slide_behavior'] = slide_behavior
                    logger.debug(f"【{self.pure_user_id}】已记录{len(slide_behavior)}个滑动行为参数")
                
                learn_status = "🧠智能学习模式" if is_learned else "🎲随机模式"
                logger.info(f"【{self.pure_user_id}】滑动完成 [{learn_status}]: "
                           f"耗时={elapsed_time:.2f}秒, "
                           f"最终位置=({current_x:.1f}, {current_y:.1f}), "
                           f"行为参数={len(slide_behavior)}个")
                
                return True
                
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】执行滑动轨迹失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                # 确保释放鼠标
                try:
                    self.page.mouse.up()
                except:
                    pass
                return False
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】滑动模拟异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _simulate_human_page_behavior(self):
        """在验证码页先停留一会儿，再做轻微交互，别一上来就莽。"""
        if not self.page:
            return

        try:
            entry_ts = getattr(self, "_captcha_page_entry_ts", None)
            target_dwell = random.uniform(2.8, 4.2)
            if entry_ts:
                elapsed = time.time() - entry_ts
                if elapsed < target_dwell:
                    wait_time = target_dwell - elapsed
                    logger.info(f"【{self.pure_user_id}】验证码页预停留 {wait_time:.2f} 秒，等页面和风控脚本稳定")
                    time.sleep(wait_time)

            width = int(self.browser_features.get("viewport_width") or 1600)
            height = int(self.browser_features.get("viewport_height") or 900)
            move_count = random.randint(2, 4)
            for _ in range(move_count):
                target_x = random.randint(max(140, width // 5), max(260, width - width // 5))
                target_y = random.randint(max(180, height // 4), max(260, height - height // 4))
                self.page.mouse.move(target_x, target_y, steps=random.randint(10, 24))
                time.sleep(random.uniform(0.08, 0.22))

            if random.random() < 0.35:
                self.page.mouse.wheel(0, random.randint(50, 160))
                time.sleep(random.uniform(0.05, 0.15))
                if random.random() < 0.5:
                    self.page.mouse.wheel(0, -random.randint(30, 90))
                    time.sleep(random.uniform(0.05, 0.12))

            settle_time = random.uniform(0.35, 0.8)
            logger.info(f"【{self.pure_user_id}】验证码页行为预热完成，额外静置 {settle_time:.2f} 秒")
            time.sleep(settle_time)
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】验证码页行为预热失败，继续尝试滑块: {e}")

    def _is_hard_block_page(self, page=None) -> bool:
        target_page = page or self.page
        if not target_page:
            return False

        try:
            special_block = self._detect_special_captcha_block(target_page)
            special_block = self._wait_for_punish_slider_dom_ready_if_needed(
                target_page,
                special_block,
                "初始页面拦截判定",
            )
            special_block = self._recover_punish_slider_shell_if_possible(
                target_page,
                special_block,
                "初始页面拦截判定",
            )
            if special_block:
                return True

            page_text = ""
            try:
                page_text = target_page.inner_text('body', timeout=1500) or ""
            except Exception:
                page_text = target_page.content() or ""

            hard_block_keywords = [
                "抱歉，页面访问出现了问题",
                "页面访问出现了问题",
                "点我反馈",
            ]
            keyword_hit = any(keyword in page_text for keyword in hard_block_keywords)

            has_qrcode = False
            has_feedback_link = False
            for selector in (
                ".bx-pu-qrcode-wrap",
                ".captcha-qrcode",
                "#bx-feedback-btn",
                "a[href*='page/feedback']",
            ):
                try:
                    element = target_page.query_selector(selector)
                    if element:
                        if selector in (".bx-pu-qrcode-wrap", ".captcha-qrcode"):
                            has_qrcode = True
                        else:
                            has_feedback_link = True
                except Exception:
                    continue

            has_slider_button = False
            for selector in ("#nc_1_n1z", ".btn_slide", ".sm-btn", ".sm-btn-wrapper", ".nc_scale"):
                try:
                    element = target_page.query_selector(selector)
                    if element:
                        has_slider_button = True
                        break
                except Exception:
                    continue

            if keyword_hit and (has_qrcode or has_feedback_link) and not has_slider_button:
                return True
        except Exception:
            pass

        return False

    def _detect_special_captcha_block(self, target=None) -> Optional[Dict[str, Any]]:
        """检测验证码处罚页/反馈拦截页，避免把不可解风控页继续当普通滑块拖。"""
        target_page = target or self.page
        if not target_page:
            return None

        try:
            detached_runtime = False

            def _mark_detached_runtime(error: Exception) -> bool:
                nonlocal detached_runtime
                error_text = str(error).lower()
                if 'detached' in error_text or 'disconnected' in error_text:
                    detached_runtime = True
                    return True
                return False

            try:
                current_url = str(getattr(target_page, 'url', '') or '')
            except Exception:
                current_url = ''
            current_url_lower = current_url.lower()

            current_title = ''
            try:
                raw_title = target_page.title() if callable(getattr(target_page, 'title', None)) else getattr(target_page, 'title', '')
                current_title = str(raw_title or '')
            except Exception as title_error:
                _mark_detached_runtime(title_error)
                current_title = ''
            current_title_lower = current_title.lower()

            page_text = ''
            try:
                page_text = target_page.inner_text('body', timeout=1500) or ''
            except Exception as text_error:
                _mark_detached_runtime(text_error)
                try:
                    page_text = target_page.content() or ''
                except Exception as content_error:
                    _mark_detached_runtime(content_error)
                    page_text = ''
            page_text_lower = str(page_text or '').lower()

            has_slider_button = False
            for selector in ("#nc_1_n1z", ".btn_slide", ".sm-btn", ".nc_scale"):
                try:
                    element = target_page.query_selector(selector)
                    if element and element.is_visible():
                        has_slider_button = True
                        break
                except Exception:
                    continue

            # `#nocaptcha` / `.sm-btn-wrapper` 常只是处罚页的外壳容器；
            # 只有真正的轨道/按钮出现时，才算“仍可操作的滑块”。
            has_slider_track = False
            for selector in ("#nc_1_n1t", ".nc_scale"):
                try:
                    element = target_page.query_selector(selector)
                    if element and element.is_visible():
                        has_slider_track = True
                        break
                except Exception:
                    continue

            has_operable_slider = has_slider_button or has_slider_track
            if detached_runtime and not page_text and not has_operable_slider:
                logger.debug(
                    f"【{self.pure_user_id}】检测验证码处罚页时目标已分离，忽略旧 frame 残留状态: "
                    f"{current_url or 'unknown'}"
                )
                return None

            punish_tokens = (
                'punish?x5secdata',
                'action=captcha',
                'purecaptcha=true',
                'x5step=2',
            )
            punish_hit_count = sum(1 for token in punish_tokens if token in current_url_lower)
            punish_title_hit = ('验证码拦截' in current_title) or ('captcha intercept' in current_title_lower)
            punish_text_hit = ('验证码拦截' in page_text) or ('验证失败，点击框体重试' in page_text)
            if punish_hit_count >= 2 or punish_title_hit or punish_text_hit:
                if has_operable_slider:
                    logger.debug(
                        f"【{self.pure_user_id}】当前命中 pureCaptcha/处罚页特征，但页面仍存在可操作滑块，继续按普通滑块处理"
                    )
                else:
                    return {
                        'kind': 'punish_captcha',
                        'url': current_url,
                        'title': current_title,
                        'message': '当前命中阿里验证码拦截处罚页（pureCaptcha），且页面不存在可操作滑块',
                    }

            hard_block_keywords = [
                "抱歉，页面访问出现了问题",
                "页面访问出现了问题",
                "点我反馈",
            ]
            keyword_hit = any(keyword in page_text for keyword in hard_block_keywords)
            has_qrcode = False
            has_feedback_link = False
            for selector in (
                ".bx-pu-qrcode-wrap",
                ".captcha-qrcode",
                "#bx-feedback-btn",
                "a[href*='page/feedback']",
            ):
                try:
                    element = target_page.query_selector(selector)
                    if element:
                        if selector in (".bx-pu-qrcode-wrap", ".captcha-qrcode"):
                            has_qrcode = True
                        else:
                            has_feedback_link = True
                except Exception:
                    continue

            if keyword_hit and (has_qrcode or has_feedback_link) and not has_operable_slider:
                return {
                    'kind': 'feedback_block',
                    'url': current_url,
                    'title': current_title,
                    'message': '当前命中反馈二维码/处罚页，不存在可操作滑块',
                }
        except Exception:
            return None

        return None

    def _has_recoverable_punish_slider_shell(self, target) -> bool:
        """识别 pureCaptcha 壳页里仍可被点活的滑块容器。"""
        if not target:
            return False

        shell_selectors = (
            ".errloading",
            "[data-nc-status='error']",
            "#nocaptcha",
            ".nc-container",
            ".nc_wrapper",
            ".nc_scale",
            ".sm-btn-wrapper",
            "#baxia-dialog-content",
        )
        for selector in shell_selectors:
            try:
                element = target.query_selector(selector)
                if not element:
                    continue
                try:
                    if element.is_visible():
                        return True
                except Exception:
                    return True
            except Exception:
                continue
        return False

    def _has_ready_punish_slider_dom(self, target) -> bool:
        """处罚页经常先出壳子、后出真滑块，这里只看关键DOM是否已出现。"""
        if not target:
            return False

        button_selectors = (
            "#nc_1_n1z",
            ".btn_slide",
            ".sm-btn",
        )
        track_selectors = (
            "#nc_1_n1t",
            ".nc_scale",
        )
        text_selectors = (
            "#nc_1__scale_text",
            ".captcha-tips",
        )

        has_button = False
        has_track = False
        has_text = False

        for selector in button_selectors:
            try:
                if target.query_selector(selector):
                    has_button = True
                    break
            except Exception:
                continue

        for selector in track_selectors:
            try:
                if target.query_selector(selector):
                    has_track = True
                    break
            except Exception:
                continue

        for selector in text_selectors:
            try:
                element = target.query_selector(selector)
                if element and str(element.text_content() or "").strip():
                    has_text = True
                    break
            except Exception:
                continue

        return has_button and (has_track or has_text)

    def _wait_for_punish_slider_dom_ready_if_needed(
        self,
        target,
        current_block: Optional[Dict[str, Any]],
        context_label: str,
        max_wait_seconds: float = 1.2,
        poll_interval: float = 0.25,
    ) -> Optional[Dict[str, Any]]:
        """pureCaptcha 页面真实滑块会晚一点挂出来，先给一小段收敛窗口，别太早判死刑。"""
        if not current_block or current_block.get("kind") != "punish_captcha":
            return current_block

        if self._has_ready_punish_slider_dom(target):
            logger.info(f"【{self.pure_user_id}】{context_label} 检测到处罚页真实滑块DOM已就绪，继续按正常滑块处理")
            return None

        deadline = time.time() + max(0.0, max_wait_seconds)
        refreshed_block = current_block
        while time.time() < deadline:
            time.sleep(max(0.05, poll_interval))
            if self._has_ready_punish_slider_dom(target):
                logger.info(f"【{self.pure_user_id}】{context_label} 处罚页真实滑块DOM已延迟出现，继续按正常滑块处理")
                return None
            refreshed_block = self._detect_special_captcha_block(target)
            if not refreshed_block:
                logger.info(f"【{self.pure_user_id}】{context_label} 处罚页状态已恢复，继续按正常滑块处理")
                return None

        return refreshed_block

    def _click_first_activation_target(self, target, selectors: List[Tuple[str, str]], context_label: str) -> bool:
        """在指定 page/frame 中点击第一个可用激活区域。"""
        if not target:
            return False

        for selector, desc in selectors:
            try:
                element = target.query_selector(selector)
                if not element:
                    continue
                try:
                    if not element.is_visible():
                        continue
                except Exception:
                    pass

                try:
                    box = element.bounding_box()
                except Exception:
                    box = None

                try:
                    if box and getattr(self, "page", None):
                        click_x = box["x"] + box["width"] / 2
                        click_y = box["y"] + box["height"] / 2
                        self.page.mouse.click(click_x, click_y)
                    else:
                        element.click(timeout=1000)
                    logger.info(f"【{self.pure_user_id}】已点击{context_label}激活区域[{desc}]: {selector}")
                    return True
                except Exception as click_err:
                    logger.debug(f"【{self.pure_user_id}】点击{context_label}激活区域[{desc}]失败: {click_err}")
                    continue
            except Exception as find_err:
                logger.debug(f"【{self.pure_user_id}】查找{context_label}激活区域[{desc}]失败: {find_err}")
                continue
        return False

    def _recover_punish_slider_shell_if_possible(
        self,
        target,
        current_block: Optional[Dict[str, Any]],
        context_label: str,
    ) -> Optional[Dict[str, Any]]:
        """对可恢复的 pureCaptcha 壳页先尝试点活，再重新探测。"""
        if not current_block or current_block.get("kind") != "punish_captcha":
            return current_block
        if not self._has_recoverable_punish_slider_shell(target):
            return current_block

        activation_selectors = [
            (".errloading", "错误提示区"),
            ("[data-nc-status='error']", "NC错误状态"),
            (".nc-container", "滑块容器"),
            ("#nocaptcha", "NoCaptcha容器"),
            (".nc_wrapper", "滑块包装器"),
            (".nc_scale", "滑块轨道"),
            (".sm-btn-wrapper", "滑块按钮包装器"),
            ("#baxia-dialog-content", "验证码对话框"),
        ]
        if not self._click_first_activation_target(target, activation_selectors, context_label):
            return current_block

        time.sleep(0.8)
        refreshed_block = self._detect_special_captcha_block(target)
        if refreshed_block:
            logger.info(
                f"【{self.pure_user_id}】{context_label} pureCaptcha 壳页点活后仍是硬拦截[{refreshed_block.get('kind')}]"
            )
        else:
            logger.info(f"【{self.pure_user_id}】{context_label} pureCaptcha 壳页已点活，继续按正常滑块处理")
        return refreshed_block

    def find_slider_elements(self, fast_mode=False):
        """查找滑块元素（支持在主页面和所有frame中查找）
        
        Args:
            fast_mode: 快速模式，不使用wait_for_selector，减少等待时间（当已确认滑块存在时使用）
        """
        try:
            # 快速等待页面稳定（快速模式下跳过）
            if not fast_mode:
                time.sleep(0.1)

            current_block = self._detect_special_captcha_block(self.page)
            current_block = self._wait_for_punish_slider_dom_ready_if_needed(
                self.page,
                current_block,
                "主页面滑块探测",
            )
            current_block = self._recover_punish_slider_shell_if_possible(
                self.page,
                current_block,
                "主页面滑块探测",
            )
            if current_block:
                logger.error(
                    f"【{self.pure_user_id}】当前页面命中高风险验证码页[{current_block['kind']}]: "
                    f"{current_block['message']}"
                )
                self.last_verification_feedback = {
                    "status": "hard_block",
                    "source": current_block["kind"],
                    "message": current_block["message"],
                    "url": current_block.get("url") or "",
                    "title": current_block.get("title") or "",
                }
                self._save_debug_snapshot("hard_block_page", self.page)
                return None, None, None
            
            # ===== 【优化】优先在 frames 中快速查找最常见的滑块组合 =====
            # 根据实际日志，滑块按钮和轨道通常在同一个 frame 中
            # 按钮: #nc_1_n1z, 轨道: #nc_1_n1t
            logger.debug(f"【{self.pure_user_id}】优先在frames中快速查找常见滑块组合...")
            try:
                frames = self.page.frames
                for idx, frame in enumerate(frames):
                    try:
                        frame_block = self._detect_special_captcha_block(frame)
                        frame_block = self._wait_for_punish_slider_dom_ready_if_needed(
                            frame,
                            frame_block,
                            f"Frame {idx} 滑块探测",
                        )
                        frame_block = self._recover_punish_slider_shell_if_possible(
                            frame,
                            frame_block,
                            f"Frame {idx} 滑块探测",
                        )
                        if frame_block:
                            logger.error(
                                f"【{self.pure_user_id}】Frame {idx} 命中高风险验证码页[{frame_block['kind']}]: "
                                f"{frame_block['message']}"
                            )
                            self._detected_slider_frame = frame
                            self.last_verification_feedback = {
                                "status": "hard_block",
                                "source": frame_block["kind"],
                                "message": frame_block["message"],
                                "url": frame_block.get("url") or "",
                                "title": frame_block.get("title") or "",
                                "frame_index": idx,
                            }
                            self._save_debug_snapshot("hard_block_page", frame)
                            return None, None, None

                        # 优先查找最常见的按钮选择器
                        button_element = frame.query_selector("#nc_1_n1z")
                        if button_element and button_element.is_visible():
                            # 在同一个 frame 中查找轨道
                            track_element = frame.query_selector("#nc_1_n1t")
                            if track_element and track_element.is_visible():
                                # 找到容器（可以用按钮或其他选择器）
                                container_element = frame.query_selector("#baxia-dialog-content")
                                if not container_element:
                                    container_element = frame.query_selector(".nc-container")
                                if not container_element:
                                    # 如果找不到容器，用按钮作为容器标识
                                    container_element = button_element
                                
                                logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 快速找到完整滑块组合！")
                                logger.info(f"【{self.pure_user_id}】  - 按钮: #nc_1_n1z")
                                logger.info(f"【{self.pure_user_id}】  - 轨道: #nc_1_n1t")
                                
                                # 保存frame引用
                                self._detected_slider_frame = frame
                                return container_element, button_element, track_element
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】Frame {idx} 快速查找失败: {e}")
                        continue
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】frames 快速查找出错: {e}")
            
            # ===== 如果快速查找失败，使用原来的完整查找逻辑 =====
            logger.debug(f"【{self.pure_user_id}】快速查找未成功，使用完整查找逻辑...")
            
            # 定义滑块容器选择器（支持多种类型）
            container_selectors = [
                "#nc_1_n1z",  # 滑块按钮也可以作为容器标识
                "#baxia-dialog-content",
                ".nc-container",
                ".nc_wrapper",
                ".nc_scale",
                "[class*='nc-container']",
                # 刮刮乐类型滑块
                "#nocaptcha",
                ".nc_1_nocaptcha",
                ".sm-pop-inner.nc-container",
                ".sm-btn-wrapper",
                ".scratch-captcha-container",
                ".scratch-captcha-question-bg",
                # 通用选择器
                "[class*='slider']",
                "[class*='btn_slide']"
            ]
            
            # 查找滑块容器
            slider_container = None
            found_frame = None
            
            # 🔑 优化：如果是重试且之前在"已知位置"查找失败，跳过已知位置，直接全局搜索
            skip_known_location = False
            if hasattr(self, '_slider_search_failed_in_known_location') and self._slider_search_failed_in_known_location:
                logger.warning(f"【{self.pure_user_id}】上次在已知位置查找失败，本次跳过已知位置，直接全局搜索")
                skip_known_location = True
                # 清除标记，避免影响下次验证
                self._slider_search_failed_in_known_location = False
            
            # 如果检测时已经知道滑块在哪个frame中，直接在该frame中查找
            if not skip_known_location and hasattr(self, '_detected_slider_frame'):
                if self._detected_slider_frame is not None:
                    # 在已知的frame中查找
                    logger.info(f"【{self.pure_user_id}】已知滑块在frame中，直接在frame中查找...")
                    target_frame = self._detected_slider_frame
                    for selector in container_selectors:
                        try:
                            element = target_frame.query_selector(selector)
                            if element:
                                try:
                                    if element.is_visible():
                                        logger.info(f"【{self.pure_user_id}】在已知Frame中找到滑块容器: {selector}")
                                        slider_container = element
                                        found_frame = target_frame
                                        break
                                except:
                                    # 如果无法检查可见性，也尝试使用
                                    logger.info(f"【{self.pure_user_id}】在已知Frame中找到滑块容器（无法检查可见性）: {selector}")
                                    slider_container = element
                                    found_frame = target_frame
                                    break
                        except Exception as e:
                            logger.debug(f"【{self.pure_user_id}】已知Frame选择器 {selector} 未找到: {e}")
                            continue
                else:
                    # _detected_slider_frame 是 None，表示在主页面
                    logger.info(f"【{self.pure_user_id}】已知滑块在主页面，直接在主页面查找...")
                    for selector in container_selectors:
                        try:
                            element = self.page.wait_for_selector(selector, timeout=2000)  # 增加超时时间
                            if element:
                                logger.info(f"【{self.pure_user_id}】在已知主页面找到滑块容器: {selector}")
                                slider_container = element
                                found_frame = self.page
                                break
                        except Exception as e:
                            logger.debug(f"【{self.pure_user_id}】主页面选择器 {selector} 未找到: {e}")
                            continue
            
            # 如果已知位置中没找到，或者没有已知位置，先尝试在主页面查找
            if not slider_container:
                for selector in container_selectors:
                    try:
                        element = self.page.wait_for_selector(selector, timeout=1000)  # 减少超时时间，快速跳过
                        if element:
                            logger.info(f"【{self.pure_user_id}】在主页面找到滑块容器: {selector}")
                            slider_container = element
                            found_frame = self.page
                            break
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】主页面选择器 {selector} 未找到: {e}")
                        continue
            
            # 如果主页面没找到，在所有frame中查找
            if not slider_container and self.page:
                try:
                    frames = self.page.frames
                    logger.info(f"【{self.pure_user_id}】主页面未找到滑块，开始在所有frame中查找（共{len(frames)}个frame）...")
                    for idx, frame in enumerate(frames):
                        try:
                            for selector in container_selectors:
                                try:
                                    # 在frame中使用query_selector，因为frame可能不支持wait_for_selector
                                    element = frame.query_selector(selector)
                                    if element:
                                        # 检查元素是否可见
                                        try:
                                            if element.is_visible():
                                                logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块容器: {selector}")
                                                slider_container = element
                                                found_frame = frame
                                                break
                                        except:
                                            # 如果无法检查可见性，也尝试使用
                                            logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块容器（无法检查可见性）: {selector}")
                                            slider_container = element
                                            found_frame = frame
                                            break
                                except Exception as e:
                                    logger.debug(f"【{self.pure_user_id}】Frame {idx} 选择器 {selector} 未找到: {e}")
                                    continue
                            if slider_container:
                                break
                        except Exception as e:
                            logger.debug(f"【{self.pure_user_id}】检查Frame {idx} 时出错: {e}")
                            continue
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】获取frame列表时出错: {e}")
            
            if not slider_container:
                logger.error(f"【{self.pure_user_id}】未找到任何滑块容器（主页面和所有frame都已检查）")
                return None, None, None
            
            # 定义滑块按钮选择器（支持多种类型）
            button_selectors = [
                # nc 系列滑块
                "#nc_1_n1z",
                ".nc_iconfont",
                ".btn_slide",
                # 刮刮乐类型滑块
                "#scratch-captcha-btn",
                ".scratch-captcha-slider .button",
                # 通用选择器
                "[class*='slider']",
                "[class*='btn']",
                "[role='button']"
            ]
            
            # 查找滑块按钮（在找到容器的同一个frame中查找）
            slider_button = None
            search_frame = found_frame if found_frame and found_frame != self.page else self.page
            
            # 如果容器是在主页面找到的，按钮也应该在主页面查找
            # 如果容器是在frame中找到的，按钮也应该在同一个frame中查找
            for selector in button_selectors:
                try:
                    element = None
                    if fast_mode:
                        # 快速模式：直接使用 query_selector，不等待
                        element = search_frame.query_selector(selector)
                    else:
                        # 正常模式：使用 wait_for_selector
                        if search_frame == self.page:
                            element = self.page.wait_for_selector(selector, timeout=3000)
                        else:
                            # 在frame中先尝试wait_for_selector（如果支持）
                            try:
                                # 尝试使用wait_for_selector（Playwright的frame支持）
                                element = search_frame.wait_for_selector(selector, timeout=3000)
                            except:
                                # 如果不支持wait_for_selector，使用query_selector并等待
                                time.sleep(0.5)  # 等待元素加载
                                element = search_frame.query_selector(selector)
                    
                    if element:
                        # 检查元素是否可见，但不要因为不可见就放弃
                        try:
                            is_visible = element.is_visible()
                            if not is_visible:
                                logger.debug(f"【{self.pure_user_id}】找到元素但不可见: {selector}，继续尝试其他选择器")
                                element = None
                        except Exception as vis_e:
                            # 如果无法检查可见性，仍然使用该元素
                            logger.debug(f"【{self.pure_user_id}】无法检查元素可见性: {vis_e}，继续使用该元素")
                            pass
                    
                    if element:
                        frame_info = "主页面" if search_frame == self.page else f"Frame"
                        logger.info(f"【{self.pure_user_id}】在{frame_info}找到滑块按钮: {selector}")
                        slider_button = element
                        break
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】选择器 {selector} 未找到: {e}")
                    continue

            if not slider_button and slider_container:
                if self._try_reset_slider_error_state(search_frame, slider_container):
                    logger.info(f"【{self.pure_user_id}】滑块错误态已重置，重新在当前上下文查找滑块按钮...")
                    for selector in button_selectors:
                        try:
                            element = None
                            if search_frame == self.page:
                                element = self.page.wait_for_selector(selector, timeout=1500)
                            else:
                                try:
                                    element = search_frame.wait_for_selector(selector, timeout=1500)
                                except Exception:
                                    element = search_frame.query_selector(selector)
                            if element:
                                try:
                                    if not element.is_visible():
                                        element = None
                                except Exception:
                                    pass
                            if element:
                                logger.info(f"【{self.pure_user_id}】重置错误态后找到滑块按钮: {selector}")
                                slider_button = element
                                break
                        except Exception:
                            continue
            
            # 如果在找到容器的frame中没找到按钮，尝试在所有frame中查找
            # 无论容器是在主页面还是frame中找到的，如果按钮找不到，都应该在所有frame中查找
            if not slider_button:
                logger.warning(f"【{self.pure_user_id}】在找到容器的位置未找到按钮，尝试在所有frame中查找...")
                try:
                    frames = self.page.frames
                    for idx, frame in enumerate(frames):
                        # 如果容器是在frame中找到的，跳过已经检查过的frame
                        if found_frame and found_frame != self.page and frame == found_frame:
                            continue
                        # 如果容器是在主页面找到的，跳过主页面（因为已经检查过了）
                        if found_frame == self.page and frame == self.page:
                            continue
                            
                        for selector in button_selectors:
                            try:
                                element = None
                                if fast_mode:
                                    # 快速模式：直接使用 query_selector
                                    element = frame.query_selector(selector)
                                else:
                                    # 正常模式：先尝试wait_for_selector
                                    try:
                                        element = frame.wait_for_selector(selector, timeout=2000)
                                    except:
                                        time.sleep(0.3)  # 等待元素加载
                                        element = frame.query_selector(selector)
                                
                                if element:
                                    try:
                                        is_visible = element.is_visible()
                                        if is_visible:
                                            logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块按钮: {selector}")
                                            slider_button = element
                                            found_frame = frame  # 更新found_frame
                                            break
                                        else:
                                            logger.debug(f"【{self.pure_user_id}】在Frame {idx} 找到元素但不可见: {selector}")
                                    except:
                                        # 如果无法检查可见性，仍然使用该元素
                                        logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块按钮（无法检查可见性）: {selector}")
                                        slider_button = element
                                        found_frame = frame  # 更新found_frame
                                        break
                            except Exception as e:
                                logger.debug(f"【{self.pure_user_id}】Frame {idx} 选择器 {selector} 查找失败: {e}")
                                continue
                        if slider_button:
                            break
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】在所有frame中查找按钮时出错: {e}")
            
            # 如果还是没找到，尝试在主页面查找（如果之前没在主页面查找过）
            if not slider_button and found_frame != self.page:
                logger.warning(f"【{self.pure_user_id}】在所有frame中未找到按钮，尝试在主页面查找...")
                for selector in button_selectors:
                    try:
                        element = None
                        if fast_mode:
                            # 快速模式：直接使用 query_selector
                            element = self.page.query_selector(selector)
                        else:
                            # 正常模式：使用 wait_for_selector
                            element = self.page.wait_for_selector(selector, timeout=2000)
                        
                        if element:
                            try:
                                if element.is_visible():
                                    logger.info(f"【{self.pure_user_id}】在主页面找到滑块按钮: {selector}")
                                    slider_button = element
                                    found_frame = self.page  # 更新found_frame
                                    break
                                else:
                                    logger.debug(f"【{self.pure_user_id}】在主页面找到元素但不可见: {selector}")
                            except:
                                # 如果无法检查可见性，仍然使用该元素
                                logger.info(f"【{self.pure_user_id}】在主页面找到滑块按钮（无法检查可见性）: {selector}")
                                slider_button = element
                                found_frame = self.page  # 更新found_frame
                                break
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】主页面选择器 {selector} 查找失败: {e}")
                        continue
            
            # 如果还是没找到，尝试使用更宽松的查找方式（不检查可见性）
            if not slider_button:
                logger.warning(f"【{self.pure_user_id}】使用宽松模式查找滑块按钮（不检查可见性）...")
                # 先在所有frame中查找
                try:
                    frames = self.page.frames
                    for idx, frame in enumerate(frames):
                        for selector in button_selectors[:3]:  # 只使用前3个最常用的选择器
                            try:
                                element = frame.query_selector(selector)
                                if element:
                                    logger.info(f"【{self.pure_user_id}】在Frame {idx} 找到滑块按钮（宽松模式）: {selector}")
                                    slider_button = element
                                    found_frame = frame
                                    break
                            except:
                                continue
                        if slider_button:
                            break
                except:
                    pass
                
                # 如果还是没找到，在主页面查找
                if not slider_button:
                    for selector in button_selectors[:3]:
                        try:
                            element = self.page.query_selector(selector)
                            if element:
                                logger.info(f"【{self.pure_user_id}】在主页面找到滑块按钮（宽松模式）: {selector}")
                                slider_button = element
                                found_frame = self.page
                                break
                        except:
                            continue
            
            if not slider_button:
                logger.error(f"【{self.pure_user_id}】未找到任何滑块按钮（主页面和所有frame都已检查，包括宽松模式）")
                return slider_container, None, None
            
            # 定义滑块轨道选择器
            track_selectors = [
                "#nc_1_n1t",
                ".nc_scale",
                ".nc_1_n1t",
                "[class*='track']",
                "[class*='scale']"
            ]
            
            # 查找滑块轨道（在找到按钮的同一个frame中查找，因为按钮和轨道应该在同一个位置）
            slider_track = None
            # 使用找到按钮的frame来查找轨道
            track_search_frame = found_frame if found_frame and found_frame != self.page else self.page
            
            for selector in track_selectors:
                try:
                    element = None
                    if fast_mode:
                        # 快速模式：直接使用 query_selector
                        element = track_search_frame.query_selector(selector)
                    else:
                        # 正常模式：使用 wait_for_selector
                        if track_search_frame == self.page:
                            element = self.page.wait_for_selector(selector, timeout=3000)
                        else:
                            # 在frame中使用query_selector
                            element = track_search_frame.query_selector(selector)
                    
                    if element:
                        try:
                            if not element.is_visible():
                                element = None
                        except:
                            pass
                    
                    if element:
                        frame_info = "主页面" if track_search_frame == self.page else f"Frame"
                        logger.info(f"【{self.pure_user_id}】在{frame_info}找到滑块轨道: {selector}")
                        slider_track = element
                        break
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】选择器 {selector} 未找到: {e}")
                    continue
            
            # 🔑 关键修复：如果在找到按钮的位置没找到轨道，尝试其他位置
            # 不再限制只在frame中才尝试其他搜索策略，主页面找不到也要尝试frame
            if not slider_track and track_search_frame:
                # 如果按钮在frame中，先点击激活
                if track_search_frame != self.page:
                    logger.warning(f"【{self.pure_user_id}】在已知Frame中未找到轨道，尝试点击frame激活后再查找...")
                    try:
                        # 点击frame以激活它，让轨道出现
                        # 尝试点击frame中的容器或按钮来激活
                        clicked_element = False
                        if slider_container:
                            try:
                                slider_container.click(timeout=1000)
                                logger.info(f"【{self.pure_user_id}】已点击滑块容器以激活frame")
                                clicked_element = True
                                time.sleep(0.3)  # 等待轨道出现
                            except:
                                pass
                        elif slider_button:
                            try:
                                slider_button.click(timeout=1000)
                                logger.info(f"【{self.pure_user_id}】已点击滑块按钮以激活frame")
                                clicked_element = True
                                time.sleep(0.3)  # 等待轨道出现
                            except:
                                pass
                        
                        # 🔑 关键修复：点击后重新查找滑块按钮，因为DOM可能已更新
                        if clicked_element:
                            logger.info(f"【{self.pure_user_id}】点击激活frame后，重新查找滑块按钮以更新元素引用...")
                            old_button = slider_button
                            for selector in button_selectors:
                                try:
                                    element = track_search_frame.query_selector(selector)
                                    if element:
                                        try:
                                            if element.is_visible():
                                                logger.info(f"【{self.pure_user_id}】重新找到滑块按钮: {selector}")
                                                slider_button = element
                                                break
                                        except:
                                            # 如果无法检查可见性，也尝试使用
                                            logger.info(f"【{self.pure_user_id}】重新找到滑块按钮（无法检查可见性）: {selector}")
                                            slider_button = element
                                            break
                                except:
                                    continue
                            
                            if slider_button != old_button:
                                logger.info(f"【{self.pure_user_id}】✅ 滑块按钮元素引用已更新")
                            else:
                                logger.warning(f"【{self.pure_user_id}】⚠️ 未能更新滑块按钮元素引用，可能导致后续操作失败")
                        
                        # 再次在同一个frame中查找轨道
                        for selector in track_selectors:
                            try:
                                element = track_search_frame.query_selector(selector)
                                if element:
                                    try:
                                        if element.is_visible():
                                            logger.info(f"【{self.pure_user_id}】点击frame后在Frame中找到滑块轨道: {selector}")
                                            slider_track = element
                                            break
                                    except:
                                        # 如果无法检查可见性，也尝试使用
                                        logger.info(f"【{self.pure_user_id}】点击frame后在Frame中找到滑块轨道（无法检查可见性）: {selector}")
                                        slider_track = element
                                        break
                            except:
                                continue
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】点击frame后查找轨道时出错: {e}")
                
                # 🔑 关键修复：无论按钮在哪里，都要在所有frame中查找轨道
                if not slider_track:
                    location_desc = "点击frame后仍" if track_search_frame != self.page else "在已知位置"
                    logger.warning(f"【{self.pure_user_id}】{location_desc}未找到轨道，尝试在所有frame中查找...")
                    try:
                        frames = self.page.frames
                        logger.info(f"【{self.pure_user_id}】开始遍历{len(frames)}个frame查找轨道...")
                        for idx, frame in enumerate(frames):
                            if frame == track_search_frame:
                                logger.debug(f"【{self.pure_user_id}】跳过Frame {idx}（已检查过）")
                                continue  # 跳过已经检查过的frame
                            logger.debug(f"【{self.pure_user_id}】检查Frame {idx}...")
                            for selector in track_selectors:
                                try:
                                    element = frame.query_selector(selector)
                                    if element:
                                        # 🔑 降低可见性要求：找到就使用，不强制检查可见性
                                        logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 找到滑块轨道: {selector}")
                                        slider_track = element
                                        # 更新found_frame为找到轨道的frame
                                        found_frame = frame
                                        break
                                except Exception as e:
                                    logger.debug(f"【{self.pure_user_id}】Frame {idx} 选择器 {selector} 出错: {e}")
                                    continue
                            if slider_track:
                                break
                        if not slider_track:
                            logger.warning(f"【{self.pure_user_id}】遍历完{len(frames)}个frame，未找到轨道")
                    except Exception as e:
                        logger.error(f"【{self.pure_user_id}】在所有frame中查找轨道时出错: {e}")
            
            # 如果还是没找到，尝试在主页面查找
            if not slider_track:
                logger.warning(f"【{self.pure_user_id}】在所有frame中未找到轨道，尝试在主页面查找...")
                for selector in track_selectors:
                    try:
                        element = self.page.wait_for_selector(selector, timeout=1000)
                        if element:
                            logger.info(f"【{self.pure_user_id}】在主页面找到滑块轨道: {selector}")
                            slider_track = element
                            break
                    except:
                        continue
            
            if not slider_track:
                logger.error(f"【{self.pure_user_id}】未找到任何滑块轨道（主页面和所有frame都已检查）")
                return slider_container, slider_button, None
            
            # 保存找到滑块的frame引用，供后续验证使用
            if found_frame and found_frame != self.page:
                self._detected_slider_frame = found_frame
                logger.info(f"【{self.pure_user_id}】保存滑块frame引用，供后续验证使用")
            elif found_frame == self.page:
                # 如果是在主页面找到的，设置为None
                self._detected_slider_frame = None
            
            return slider_container, slider_button, slider_track
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】查找滑块元素时出错: {str(e)}")
            return None, None, None
    
    def is_scratch_captcha(self):
        """检测是否为刮刮乐类型验证码"""
        try:
            page_content = self.page.content()
            # 检测刮刮乐特征（更精确的判断）
            # 必须包含明确的刮刮乐特征词
            scratch_required = ['scratch-captcha', 'scratch-captcha-btn', 'scratch-captcha-slider']
            has_scratch_feature = any(keyword in page_content for keyword in scratch_required)
            
            # 或者包含刮刮乐的指令文字
            scratch_instructions = ['Release the slider', 'pillows', 'fully appears', 'after', 'appears']
            has_scratch_instruction = sum(1 for keyword in scratch_instructions if keyword in page_content) >= 2
            
            is_scratch = has_scratch_feature or has_scratch_instruction
            
            if is_scratch:
                logger.info(f"【{self.pure_user_id}】🎨 检测到刮刮乐类型验证码")
            
            return is_scratch
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】检测刮刮乐类型时出错: {e}")
            return False
    
    def calculate_slide_distance(self, slider_button: ElementHandle, slider_track: ElementHandle):
        """计算滑动距离 - 增强精度，支持刮刮乐"""
        try:
            # 🔑 增强错误处理：检查元素是否仍然有效
            button_box = None
            track_box = None
            
            # 尝试获取滑块按钮位置和大小（增加重试机制）
            for retry in range(2):
                try:
                    button_box = slider_button.bounding_box()
                    if button_box:
                        break
                    if retry == 0:
                        logger.warning(f"【{self.pure_user_id}】第{retry+1}次获取滑块按钮位置失败，等待后重试...")
                        time.sleep(0.1)
                except Exception as e:
                    if retry == 0:
                        logger.warning(f"【{self.pure_user_id}】获取滑块按钮位置异常: {e}，等待后重试...")
                        time.sleep(0.1)
                    else:
                        logger.error(f"【{self.pure_user_id}】多次尝试后仍无法获取滑块按钮位置: {e}")
            
            if not button_box:
                logger.error(f"【{self.pure_user_id}】无法获取滑块按钮位置（元素可能已失效，建议重新查找元素）")
                return 0
            
            # 获取滑块轨道位置和大小
            track_box = slider_track.bounding_box()
            if not track_box:
                logger.error(f"【{self.pure_user_id}】无法获取滑块轨道位置")
                return 0
            
            # 🎨 检测是否为刮刮乐类型
            is_scratch = self.is_scratch_captcha()
            
            # 🔑 关键优化1：使用JavaScript获取更精确的尺寸（避免DPI缩放影响）
            try:
                precise_distance = self.page.evaluate("""
                    () => {
                        const button = document.querySelector('#nc_1_n1z') || document.querySelector('.nc_iconfont');
                        const track = document.querySelector('#nc_1_n1t') || document.querySelector('.nc_scale');
                        if (button && track) {
                            const buttonRect = button.getBoundingClientRect();
                            const trackRect = track.getBoundingClientRect();
                            // 计算实际可滑动距离（考虑padding和边距）
                            return trackRect.width - buttonRect.width;
                        }
                        return null;
                    }
                """)
                
                if precise_distance and precise_distance > 0:
                    logger.info(f"【{self.pure_user_id}】使用JavaScript精确计算滑动距离: {precise_distance:.2f}px")
                    
                    # 🎨 刮刮乐特殊处理：只滑动75-85%的距离
                    if is_scratch:
                        scratch_ratio = random.uniform(0.25, 0.35)
                        final_distance = precise_distance * scratch_ratio
                        logger.warning(f"【{self.pure_user_id}】🎨 刮刮乐模式：滑动{scratch_ratio*100:.1f}%距离 ({final_distance:.2f}px)")
                        return final_distance
                    
                    # 🔑 关键优化2：添加微小随机偏移（防止每次都完全相同）
                    # 真人操作时，滑动距离会有微小偏差
                    random_offset = random.uniform(-0.5, 0.5)
                    return precise_distance + random_offset
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】JavaScript精确计算失败，使用后备方案: {e}")
            
            # 后备方案：使用bounding_box计算
            slide_distance = track_box["width"] - button_box["width"]
            
            # 🎨 刮刮乐特殊处理：只滑动75-85%的距离
            if is_scratch:
                scratch_ratio = random.uniform(0.25, 0.35)
                slide_distance = slide_distance * scratch_ratio
                logger.warning(f"【{self.pure_user_id}】🎨 刮刮乐模式：滑动{scratch_ratio*100:.1f}%距离 ({slide_distance:.2f}px)")
            else:
                # 添加微小随机偏移
                random_offset = random.uniform(-0.5, 0.5)
                slide_distance += random_offset
            
            logger.info(f"【{self.pure_user_id}】计算滑动距离: {slide_distance:.2f}px (轨道宽度: {track_box['width']}px, 滑块宽度: {button_box['width']}px)")
            
            return slide_distance
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】计算滑动距离时出错: {str(e)}")
            return 0
    
    def check_verification_success_fast(self, slider_button: ElementHandle):
        """检查验证结果 - 极速模式"""
        try:
            logger.info(f"【{self.pure_user_id}】检查验证结果（极速模式）...")
            self.last_verification_feedback = {}
            
            # 确定滑块所在的frame（如果已知）
            target_frame = None
            if hasattr(self, '_detected_slider_frame') and self._detected_slider_frame is not None:
                target_frame = self._detected_slider_frame
                logger.info(f"【{self.pure_user_id}】在已知Frame中检查验证结果")
                # 先检查frame是否还存在（未被分离）
                try:
                    # 尝试访问frame的属性来检查是否被分离
                    _ = target_frame.url if hasattr(target_frame, 'url') else None
                except Exception as frame_check_error:
                    error_msg = str(frame_check_error).lower()
                    # 如果frame被分离（detached），说明验证成功，容器已消失
                    if 'detached' in error_msg or 'disconnected' in error_msg:
                        current_block = self._detect_post_slider_blocking_state(self.page)
                        if current_block:
                            logger.warning(
                                f"【{self.pure_user_id}】Frame已分离，但当前命中[{current_block['kind']}]，按验证失败处理"
                            )
                            return False
                        logger.info(f"【{self.pure_user_id}】✓ Frame已被分离，验证成功")
                        self.last_verification_feedback = {"status": "success", "source": "frame_detached", "message": "Frame已被分离"}
                        return True
            else:
                target_frame = self.page
                logger.info(f"【{self.pure_user_id}】在主页面检查验证结果")
            
            # 等待一小段时间让验证结果出现
            time.sleep(0.3)
            
            # 核心逻辑：首先检查frame容器状态
            # 如果容器消失，直接返回成功；如果容器还在，检查失败提示
            def check_container_status():
                """检查容器状态，返回(存在, 可见)"""
                try:
                    if target_frame == self.page:
                        container = self.page.query_selector(".nc-container")
                    else:
                        # 检查frame是否还存在（未被分离）
                        try:
                            # 再次检查frame是否被分离
                            _ = target_frame.url if hasattr(target_frame, 'url') else None
                            container = target_frame.query_selector(".nc-container")
                        except Exception as frame_error:
                            error_msg = str(frame_error).lower()
                            # 如果frame被分离（detached），说明容器已经不存在
                            if 'detached' in error_msg or 'disconnected' in error_msg:
                                logger.info(f"【{self.pure_user_id}】Frame已被分离，容器不存在")
                                return (False, False)
                            # 其他错误，继续尝试
                            raise frame_error
                    
                    if container is None:
                        return (False, False)  # 容器不存在
                    
                    try:
                        is_visible = container.is_visible()
                        return (True, is_visible)
                    except Exception as vis_error:
                        vis_error_msg = str(vis_error).lower()
                        # 如果元素被分离，说明容器不存在
                        if 'detached' in vis_error_msg or 'disconnected' in vis_error_msg:
                            logger.info(f"【{self.pure_user_id}】容器元素已被分离，容器不存在")
                            return (False, False)
                        # 无法检查可见性，假设存在且可见
                        return (True, True)
                except Exception as e:
                    error_msg = str(e).lower()
                    # 如果frame或元素被分离，说明容器不存在
                    if 'detached' in error_msg or 'disconnected' in error_msg:
                        logger.info(f"【{self.pure_user_id}】Frame或容器已被分离，容器不存在")
                        return (False, False)
                    # 其他错误，保守处理，假设存在
                    logger.warning(f"【{self.pure_user_id}】检查容器状态时出错: {e}")
                    return (True, True)
            
            # 第一次检查容器状态
            container_exists, container_visible = check_container_status()
            
            # 如果容器不存在或不可见，直接返回成功
            if not container_exists or not container_visible:
                current_block = self._detect_post_slider_blocking_state(target_frame)
                if current_block:
                    logger.warning(
                        f"【{self.pure_user_id}】滑块容器已消失，但当前命中[{current_block['kind']}]，按验证失败处理"
                    )
                    return False
                logger.info(f"【{self.pure_user_id}】✓ 滑块容器已消失（不存在或不可见），验证成功")
                self.last_verification_feedback = {"status": "success", "source": "container_missing", "message": "滑块容器已消失"}
                return True
            
            # 容器还在，需要等待更长时间并检查失败提示
            logger.info(f"【{self.pure_user_id}】滑块容器仍存在且可见，等待验证结果...")
            time.sleep(1.2)  # 等待验证结果
            
            # 再次检查容器状态
            container_exists, container_visible = check_container_status()
            
            # 如果容器消失了，返回成功
            if not container_exists or not container_visible:
                current_block = self._detect_post_slider_blocking_state(target_frame)
                if current_block:
                    logger.warning(
                        f"【{self.pure_user_id}】滑块容器二次检查已消失，但当前命中[{current_block['kind']}]，按验证失败处理"
                    )
                    return False
                logger.info(f"【{self.pure_user_id}】✓ 滑块容器已消失，验证成功")
                self.last_verification_feedback = {"status": "success", "source": "container_missing", "message": "滑块容器已消失"}
                return True
            
            # 容器还在，检查是否有验证失败提示
            logger.info(f"【{self.pure_user_id}】滑块容器仍存在，检查验证失败提示...")
            if self.check_verification_failure():
                logger.warning(f"【{self.pure_user_id}】检测到验证失败提示，验证失败")
                return False
            
            # 容器还在，但没有失败提示，可能还在验证中或验证失败
            # 再等待一小段时间后再次检查
            time.sleep(0.5)
            container_exists, container_visible = check_container_status()
            
            if not container_exists or not container_visible:
                current_block = self._detect_post_slider_blocking_state(target_frame)
                if current_block:
                    logger.warning(
                        f"【{self.pure_user_id}】滑块容器末次检查已消失，但当前命中[{current_block['kind']}]，按验证失败处理"
                    )
                    return False
                logger.info(f"【{self.pure_user_id}】✓ 滑块容器已消失，验证成功")
                self.last_verification_feedback = {"status": "success", "source": "container_missing", "message": "滑块容器已消失"}
                return True
            
            if self.check_page_changed():
                logger.info(f"【{self.pure_user_id}】✓ 页面状态已变化，按验证成功处理")
                self.last_verification_feedback = {"status": "success", "source": "page_changed", "message": "页面状态已变化"}
                return True

            if self._check_login_success_by_element(self.page):
                logger.info(f"【{self.pure_user_id}】✓ 已检测到登录成功元素，按验证成功处理")
                self.last_verification_feedback = {"status": "success", "source": "login_element_detected", "message": "已检测到登录成功元素"}
                return True

            context_login_success, _ = self._probe_context_login_during_slider(self.page)
            if context_login_success:
                logger.info(f"【{self.pure_user_id}】✓ 上下文登录状态已确认，按验证成功处理")
                self.last_verification_feedback = {
                    "status": "success",
                    "source": "context_login_confirmed",
                    "message": "上下文登录状态已确认"
                }
                return True

            # 容器仍然存在，且没有失败提示，可能是验证失败但没有显示失败提示
            # 或者验证还在进行中，但为了不无限等待，返回失败
            logger.warning(f"【{self.pure_user_id}】滑块容器仍存在且可见，且未检测到失败提示，但验证可能失败")
            self.last_verification_feedback = {
                "status": "failure",
                "source": "container_still_visible",
                "message": "滑块容器仍存在且可见，未检测到明确失败提示"
            }
            self._merge_runtime_feedback(target_frame)
            return False
            
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】检查验证结果时出错: {str(e)}")
            self.last_verification_feedback = {"status": "error", "source": "exception", "message": str(e)}
            self._merge_runtime_feedback(target_frame if 'target_frame' in locals() else None)
            return False

    def _detect_post_slider_blocking_state(self, primary_target=None):
        """滑块动作后兜底探测处罚页/硬拒绝，避免把容器切换误判成成功。"""
        targets = []
        for candidate in (
            primary_target,
            getattr(self, '_detected_slider_frame', None),
            self.page,
        ):
            if candidate is None:
                continue
            if any(candidate is existing for existing in targets):
                continue
            targets.append(candidate)

        for target in targets:
            try:
                current_block = self._detect_special_captcha_block(target)
            except Exception:
                current_block = None
            if not current_block:
                continue

            self.last_verification_feedback = {
                "status": "hard_block",
                "source": current_block["kind"],
                "message": current_block["message"],
                "url": current_block.get("url") or "",
                "title": current_block.get("title") or "",
            }
            try:
                self._merge_runtime_feedback(target)
            except Exception:
                pass
            return current_block

        return None
    
    def check_page_changed(self):
        """检查页面是否改变"""
        try:
            # 检查页面标题是否改变
            current_title = self.page.title()
            logger.info(f"【{self.pure_user_id}】当前页面标题: {current_title}")

            if self._looks_like_verification_title(current_title):
                logger.info(f"【{self.pure_user_id}】页面标题仍像验证页，暂不判定成功")
                return False

            # 检查URL是否改变
            current_url = self.page.url
            logger.info(f"【{self.pure_user_id}】当前页面URL: {current_url}")

            if self._looks_like_verification_url(current_url):
                logger.info(f"【{self.pure_user_id}】页面URL仍处于验证链路，暂不判定成功")
                return False

            logger.info(f"【{self.pure_user_id}】页面已脱离验证链路，判定验证成功")
            return True
            
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】检查页面改变时出错: {e}")
            return False
    
    def check_verification_failure(self):
        """检查验证失败提示"""
        try:
            logger.info(f"【{self.pure_user_id}】检查验证失败提示...")
            
            # 等待一下让失败提示出现（由于调用前已经等待了，这里等待时间缩短）
            time.sleep(1.5)

            failure_keywords = [
                "框体错误",
                "验证失败，点击框体重试",
                "点击框体重试",
                "请重试",
                "验证码错误",
                "滑动验证失败"
            ]

            search_targets = []
            if hasattr(self, '_detected_slider_frame') and self._detected_slider_frame is not None:
                search_targets.append((self._detected_slider_frame, "已知Frame"))
            search_targets.append((self.page, "主页面"))
            
            # 检查各种可能的验证失败提示元素
            failure_selectors = [
                "text=验证失败，点击框体重试",
                "text=框体错误",
                "text=点击框体重试",
                ".errloading",
                ".sm-btn-fail",
                ".wrong-cross",
                "[class*='retry']",
                "[class*='fail']",
                "[class*='error']",
                ".captcha-tips"
            ]
            
            seen_targets = set()
            for search_target, target_name in search_targets:
                if search_target is None:
                    continue

                target_key = id(search_target)
                if target_key in seen_targets:
                    continue
                seen_targets.add(target_key)

                try:
                    target_content = search_target.content()
                except Exception as content_err:
                    logger.debug(f"【{self.pure_user_id}】读取{target_name}内容失败: {content_err}")
                    target_content = ""

                for keyword in failure_keywords:
                    if keyword and keyword in target_content:
                        logger.info(f"【{self.pure_user_id}】{target_name}内容包含失败关键词: {keyword}")
                        self.last_verification_feedback = {
                            "status": "failure",
                            "source": "keyword",
                            "message": keyword,
                            "context": target_name
                        }
                        self._merge_runtime_feedback(search_target)
                        self._save_debug_snapshot(f"failure__{target_name}_keyword", search_target)
                        logger.info(f"【{self.pure_user_id}】检测到验证失败关键词，验证失败")
                        return True

                for selector in failure_selectors:
                    try:
                        element = search_target.query_selector(selector)
                        if element and element.is_visible():
                            element_text = ""
                            try:
                                element_text = element.text_content()
                            except Exception:
                                pass
                            
                            logger.info(f"【{self.pure_user_id}】在{target_name}找到验证失败提示: {selector}, 文本: {element_text}")
                            self.last_verification_feedback = {
                                "status": "failure",
                                "source": "selector",
                                "message": element_text or selector,
                                "selector": selector,
                                "context": target_name
                            }
                            self._merge_runtime_feedback(search_target)
                            self._save_debug_snapshot(f"failure__{target_name}_selector", search_target)
                            logger.info(f"【{self.pure_user_id}】检测到验证失败提示元素，验证失败")
                            return True
                    except Exception:
                        continue

            logger.info(f"【{self.pure_user_id}】未找到验证失败提示，可能验证成功了")
            return False
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】检查验证失败时出错: {e}")
            return False
    
    def _analyze_failure(self, attempt: int, slide_distance: float, trajectory_data: dict):
        """分析失败原因并记录"""
        try:
            failure_reason = {
                "attempt": attempt,
                "slide_distance": slide_distance,
                "total_steps": trajectory_data.get("total_steps", 0),
                "base_delay": trajectory_data.get("base_delay", 0),
                "final_left_px": trajectory_data.get("final_left_px", 0),
                "completion_used": trajectory_data.get("completion_used", False),
                "verification_feedback": self.last_verification_feedback.copy(),
                "timestamp": datetime.now().isoformat()
            }
            
            # 记录失败信息
            logger.warning(f"【{self.pure_user_id}】第{attempt}次尝试失败 - 距离:{slide_distance}px, "
                         f"步数:{failure_reason['total_steps']}, "
                         f"最终位置:{failure_reason['final_left_px']}px")
            
            return failure_reason
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】分析失败原因时出错: {e}")
            return {}
    
    def click_to_reset_slider(self):
        """点击失败提示区域以重置滑块"""
        try:
            logger.info(f"【{self.pure_user_id}】尝试点击失败提示区域以重置滑块...")

            # 构建搜索 frame 列表：优先已知 frame，回退到所有 frame
            search_frames = []
            if hasattr(self, '_detected_slider_frame') and self._detected_slider_frame is not None:
                try:
                    _ = self._detected_slider_frame.url if hasattr(self._detected_slider_frame, 'url') else None
                    search_frames.append(self._detected_slider_frame)
                    logger.info(f"【{self.pure_user_id}】将在已知Frame中查找并点击")
                except Exception:
                    logger.warning(f"【{self.pure_user_id}】已知Frame已失效，回退到全局搜索")

            if not search_frames:
                search_frames.append(self.page)
                try:
                    for frame in self.page.frames:
                        if frame != self.page.main_frame:
                            search_frames.append(frame)
                except Exception:
                    pass
                logger.info(f"【{self.pure_user_id}】将在主页面和所有iframe中查找（共{len(search_frames)}个frame）")

            # 按优先级尝试点击不同的区域
            # 优先点击错误状态元素（"点击框体重试"），再尝试容器/包装器
            click_selectors = [
                (".errloading", "错误提示区域"),
                (".nc-lang-cnt .errloading", "NC错误提示"),
                ("[data-nc-status='error']", "NC错误状态元素"),
                (".nc-container", "滑块容器"),
                (".nc_wrapper", "滑块包装器"),
                (".nc_scale", "滑块轨道区域"),
                ("#baxia-dialog-content", "对话框内容"),
                ("#nc_1__bg", "背景区域"),
                ("div[class*='nc']", "NC相关元素"),
            ]

            clicked = False
            for target_frame in search_frames:
                if clicked:
                    break
                for selector, desc in click_selectors:
                    try:
                        element = target_frame.query_selector(selector)
                        if element:
                            try:
                                box = element.bounding_box()
                                if box:
                                    click_x = box['x'] + box['width'] / 2
                                    click_y = box['y'] + box['height'] / 2
                                    self.page.mouse.click(click_x, click_y)
                                    logger.info(f"【{self.pure_user_id}】✅ 已点击{desc}: {selector} (位置: {click_x:.1f}, {click_y:.1f})")
                                    clicked = True
                                    time.sleep(0.5)
                                    break
                                else:
                                    element.click(timeout=1000)
                                    logger.info(f"【{self.pure_user_id}】✅ 已点击{desc}: {selector}")
                                    clicked = True
                                    time.sleep(0.5)
                                    break
                            except Exception as click_e:
                                logger.debug(f"【{self.pure_user_id}】点击{desc} {selector} 失败: {click_e}")
                                continue
                    except Exception as find_e:
                        logger.debug(f"【{self.pure_user_id}】查找{desc} {selector} 失败: {find_e}")
                        continue
            
            if clicked:
                logger.info(f"【{self.pure_user_id}】成功点击失败提示区域，等待滑块重新加载...")
                time.sleep(0.8)  # 等待滑块重新加载（增加等待时间）
                return True
            else:
                logger.warning(f"【{self.pure_user_id}】未找到可点击的失败提示区域，滑块可能已存在")
                return False
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】点击失败提示区域时出错: {e}")
            return False
    
    def solve_slider(self, max_retries: int = 3, fast_mode: bool = False):
        """处理滑块验证（极速模式 + 自适应策略）

        Args:
            max_retries: 最大重试次数（默认3；手动调试链路允许放宽到4次兜底）
            fast_mode: 快速查找模式（当已确认滑块存在时使用，减少等待时间）

        🔧 2026-01-28 优化说明：
        - 默认减少最大重试次数（5→3），避免后台链路无效重试
        - 手动调试链路保留最多第4次兜底，用于真实浏览器单次验证
        - 增加重试间隔冷却时间，避免触发反爬机制
        - 第1次失败后等待2-3秒，第2次失败后等待3-5秒
        """
        original_max_retries = max_retries
        max_retries = max(1, min(int(max_retries or 3), 4))
        if original_max_retries != max_retries:
            logger.info(f"【{self.pure_user_id}】重试次数已收敛到 {max_retries} 次（原请求: {original_max_retries}）")

        failure_records = []
        current_strategy = 'ultra_fast_optimized'  # 优化后的极速策略
        last_attempt = 0

        def finalize_slider_success(
            attempt_no: int,
            success_note: Optional[str] = None,
            cookie_refresh_confirmed: Optional[bool] = None,
            soft_success: bool = False,
        ) -> bool:
            if success_note:
                logger.success(f"【{self.pure_user_id}】✅ {success_note}")

            logger.info(f"【{self.pure_user_id}】✅ 滑块验证成功! (第{attempt_no}次尝试)")

            strategy_stats.record_attempt(attempt_no, current_strategy, success=True)
            logger.info(f"【{self.pure_user_id}】📊 记录策略: 第{attempt_no}次-{current_strategy}策略-成功")

            if hasattr(self, 'current_trajectory_data'):
                used_strategy = self.current_trajectory_data.get("random_params", {}).get("strategy", "unknown")
                adaptive_strategy_manager.record_result(used_strategy, success=True)
                self._update_current_result_meta(
                    "success",
                    attempt=attempt_no,
                    cookie_refresh_confirmed=cookie_refresh_confirmed,
                    soft_success=soft_success,
                    note=success_note,
                )

            if self.enable_learning and hasattr(self, 'current_trajectory_data'):
                self._save_success_record(self.current_trajectory_data)
                logger.info(f"【{self.pure_user_id}】已保存成功记录用于参数优化")

            if attempt_no > 1:
                logger.info(f"【{self.pure_user_id}】经过{attempt_no}次尝试后验证成功")

            strategy_stats.log_summary()
            logger.info(adaptive_strategy_manager.get_stats_summary())
            return True

        # 快照当前 Cookie 基线（用于验证成功后判定"有意义的刷新"）
        cookie_baseline = self._snapshot_context_cookies()
        if cookie_baseline:
            x5_count = sum(1 for k in cookie_baseline if k.lower().startswith('x5'))
            key_count = sum(1 for k in self._KEY_COOKIE_NAMES if k in cookie_baseline)
            logger.info(f"【{self.pure_user_id}】Cookie 基线已快照: 共{len(cookie_baseline)}个, x5系{x5_count}个, 关键会话{key_count}个")
        else:
            logger.warning(f"【{self.pure_user_id}】Cookie 基线为空，将跳过 Cookie 刷新校验")

        for attempt in range(1, max_retries + 1):
            try:
                last_attempt = attempt
                logger.info(f"【{self.pure_user_id}】开始处理滑块验证... (第{attempt}/{max_retries}次尝试)")

                current_block = self._detect_special_captcha_block(self.page)
                current_block = self._wait_for_punish_slider_dom_ready_if_needed(
                    self.page,
                    current_block,
                    f"滑块第{attempt}次尝试起始页",
                )
                current_block = self._recover_punish_slider_shell_if_possible(
                    self.page,
                    current_block,
                    f"滑块第{attempt}次尝试起始页",
                )
                if current_block:
                    logger.error(
                        f"【{self.pure_user_id}】当前页面命中高风险验证码页[{current_block['kind']}]: "
                        f"{current_block['message']}，停止继续滑块重试"
                    )
                    self.last_verification_feedback = {
                        "status": "hard_block",
                        "source": current_block["kind"],
                        "message": current_block["message"],
                        "url": current_block.get("url") or "",
                        "title": current_block.get("title") or "",
                        "attempt": attempt,
                    }
                    self._save_debug_snapshot("hard_block_page", self.page)
                    break

                # 检测账号受限状态（如果受限则立即停止，不浪费重试机会）
                try:
                    page_text = self.page.inner_text('body', timeout=2000) if self.page else ''
                    restricted_keywords = ['账号已被限制', '限制访问', '账号异常', '账号被冻结', '暂时无法使用',
                                          '您的账号', '安全验证未通过', '账户被限制']
                    for kw in restricted_keywords:
                        if kw in page_text:
                            logger.error(f"【{self.pure_user_id}】检测到账号受限状态: '{kw}'，停止滑块处理")
                            return False
                except Exception:
                    pass

                # 如果不是第一次尝试，使用渐进式等待策略
                if attempt > 1:
                    # 🔧 优化：增加重试间隔，降低反爬触发风险
                    # 第2次等待4-6秒，第3次等待6-8秒
                    base_delay = 4.0 + (attempt - 1) * 2.0  # 基础4秒，每次增加2秒
                    retry_delay = random.uniform(base_delay, base_delay + 2.0)
                    logger.info(f"【{self.pure_user_id}】⏳ 等待{retry_delay:.1f}秒后重试...")
                    time.sleep(retry_delay)

                    # 优先点击重置滑块（不刷新页面，避免丢失已输入的表单数据）
                    logger.info(f"【{self.pure_user_id}】🔄 尝试点击重置滑块...")
                    reset_success = self.click_to_reset_slider()
                    if reset_success:
                        logger.info(f"【{self.pure_user_id}】✅ 滑块已重置，准备重新检测")
                        time.sleep(1.0)
                    else:
                        # 点击重置失败时才回退到刷新页面
                        logger.warning(f"【{self.pure_user_id}】⚠️ 点击重置失败，回退到刷新页面...")
                        try:
                            self.page.reload(wait_until='networkidle', timeout=15000)
                            time.sleep(1.0)
                            logger.info(f"【{self.pure_user_id}】✅ 页面刷新完成，准备重新检测滑块")
                        except Exception as refresh_error:
                            logger.warning(f"【{self.pure_user_id}】⚠️ 页面刷新也失败: {refresh_error}")

                    # 清除缓存的frame引用，强制重新检测滑块位置
                    if hasattr(self, '_detected_slider_frame'):
                        delattr(self, '_detected_slider_frame')
                        logger.info(f"【{self.pure_user_id}】已清除frame缓存，将重新全局搜索滑块")
                
                # 1. 查找滑块元素（使用快速模式）
                slider_container, slider_button, slider_track = self.find_slider_elements(fast_mode=fast_mode)
                if not all([slider_container, slider_button, slider_track]):
                    logger.error(f"【{self.pure_user_id}】滑块元素查找失败")
                    if (self.last_verification_feedback or {}).get("status") == "hard_block":
                        logger.error(f"【{self.pure_user_id}】当前页面已识别为高风险验证码页，停止当前滑块流程")
                        break
                    self.last_verification_feedback = {
                        "status": "page_state_changed",
                        "source": "slider_missing",
                        "message": "当前页面未找到滑块容器",
                        "attempt": attempt
                    }
                    # 🔑 关键修复：清除缓存的frame位置，下次重试时重新全局搜索
                    if hasattr(self, '_detected_slider_frame'):
                        logger.warning(f"【{self.pure_user_id}】清除缓存的滑块位置信息，下次重试将重新全局搜索")
                        delattr(self, '_detected_slider_frame')

                    context_login_success, _ = self._probe_context_login_during_slider(self.page)
                    if context_login_success:
                        return finalize_slider_success(
                            attempt,
                            "当前页面已无滑块，但上下文已确认登录",
                            cookie_refresh_confirmed=None,
                            soft_success=False,
                        )

                    logger.warning(f"【{self.pure_user_id}】当前页面已无滑块，不再继续同轮滑块重试")
                    break

                slider_search_target = getattr(self, "_detected_slider_frame", None)
                self._harden_password_slider_runtime(slider_search_target)
                
                # 2. 计算滑动距离
                slide_distance = self.calculate_slide_distance(slider_button, slider_track)
                if slide_distance <= 0:
                    logger.error(f"【{self.pure_user_id}】滑动距离计算失败")
                    continue
                
                # 3. 生成人类化轨迹（传递尝试次数以增加随机扰动）
                trajectory = self.generate_human_trajectory(slide_distance, attempt=attempt)
                if not trajectory:
                    logger.error(f"【{self.pure_user_id}】轨迹生成失败")
                    continue
                
                # 4. 模拟滑动
                if not self.simulate_slide(slider_button, trajectory):
                    logger.error(f"【{self.pure_user_id}】滑动模拟失败")
                    continue
                
                # 5. 检查验证结果（极速模式）
                verification_success = self.check_verification_success_fast(slider_button)
                if not verification_success:
                    context_login_success, _ = self._probe_context_login_during_slider(self.page)
                    if context_login_success:
                        verification_success = True
                        logger.success(f"【{self.pure_user_id}】✅ 滑块结果未明确成功，但上下文已确认登录，按成功收口")

                if verification_success:
                    # 🔑 Cookie 双重校验：页面状态通过后，轮询检查关键 Cookie 是否真正刷新
                    cookie_refresh_confirmed: Optional[bool] = None
                    soft_success = False
                    if cookie_baseline:
                        # 先等待稳定窗口（1.2 秒），给页面回写票据留时间
                        time.sleep(1.2)
                        cookie_refreshed = False
                        current_cookies = dict(cookie_baseline)
                        # 以 500ms 间隔轮询 x5/关键 Cookie 变化，最长等 10 秒
                        poll_interval = 0.5
                        max_poll_time = 10.0
                        poll_start = time.time()
                        while time.time() - poll_start < max_poll_time:
                            current_cookies = self._snapshot_context_cookies()
                            if self._has_meaningful_cookie_refresh(cookie_baseline, current_cookies):
                                cookie_refreshed = True
                                break
                            time.sleep(poll_interval)

                        if not cookie_refreshed:
                            context_login_success, confirmed_cookies = self._probe_context_login_during_slider(self.page)
                            if context_login_success:
                                logger.success(
                                    f"【{self.pure_user_id}】✅ 页面显示验证通过且上下文已确认登录，放宽 Cookie 变化校验"
                                )
                                cookie_refreshed = True
                                if confirmed_cookies:
                                    current_cookies = confirmed_cookies
                            else:
                                soft_success_allowed, soft_success_reason = self._should_accept_soft_success_without_cookie_refresh(
                                    current_cookies,
                                    self.page,
                                )
                                if soft_success_allowed:
                                    logger.success(
                                        f"【{self.pure_user_id}】✅ 页面已脱离验证态，接受软成功: {soft_success_reason}"
                                    )
                                    cookie_refresh_confirmed = False
                                    soft_success = True
                                    cookie_refreshed = True
                                    self.last_verification_feedback = {
                                        "status": "success",
                                        "source": "soft_success_cookie_pending",
                                        "message": soft_success_reason,
                                    }
                                else:
                                    logger.warning(f"【{self.pure_user_id}】⚠️ 页面显示验证通过，但等待{max_poll_time}秒后关键 Cookie 仍无变化，判定为假通过")
                                    if hasattr(self, 'current_trajectory_data'):
                                        self._update_current_result_meta(
                                            "failure",
                                            attempt=attempt,
                                            cookie_refresh_confirmed=False,
                                            soft_success=False,
                                            note="cookie_not_refreshed_after_page_success",
                                        )
                                        used_strategy = self.current_trajectory_data.get("random_params", {}).get("strategy", "unknown")
                                        adaptive_strategy_manager.record_result(used_strategy, success=False)
                                    strategy_stats.record_attempt(attempt, current_strategy, success=False)
                                    if attempt < max_retries:
                                        continue
                                    else:
                                        break

                        # Cookie 校验通过，更新基线
                        cookie_baseline = current_cookies
                        if cookie_refresh_confirmed is None:
                            cookie_refresh_confirmed = not soft_success

                    return finalize_slider_success(
                        attempt,
                        cookie_refresh_confirmed=cookie_refresh_confirmed,
                        soft_success=soft_success,
                    )
                else:
                    logger.warning(f"【{self.pure_user_id}】❌ 第{attempt}次验证失败")
                    
                    # 📊 记录策略失败
                    strategy_stats.record_attempt(attempt, current_strategy, success=False)
                    logger.info(f"【{self.pure_user_id}】📊 记录策略: 第{attempt}次-{current_strategy}策略-失败")
                    
                    # 🤖 记录到自适应策略管理器
                    if hasattr(self, 'current_trajectory_data'):
                        used_strategy = self.current_trajectory_data.get("random_params", {}).get("strategy", "unknown")
                        adaptive_strategy_manager.record_result(used_strategy, success=False)
                    
                    # 分析失败原因
                    if hasattr(self, 'current_trajectory_data'):
                        self._update_current_result_meta(
                            "failure",
                            attempt=attempt,
                            cookie_refresh_confirmed=False,
                            soft_success=False,
                            note="verification_failed",
                        )
                        failure_info = self._analyze_failure(attempt, slide_distance, self.current_trajectory_data)
                        failure_records.append(failure_info)
                        self._save_failure_record(self.current_trajectory_data, failure_info)

                    abort_retry, abort_reason = self._should_abort_slider_retry_after_failure()
                    if abort_retry:
                        logger.warning(f"【{self.pure_user_id}】{abort_reason}")
                        if hasattr(self, 'current_trajectory_data'):
                            self._update_current_result_meta(
                                "failure",
                                attempt=attempt,
                                cookie_refresh_confirmed=False,
                                soft_success=False,
                                note="token_refresh_hard_reject_abort_retry",
                            )
                        break
                    
                    # 如果不是最后一次尝试，继续
                    if attempt < max_retries:
                        continue
                
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】第{attempt}次处理滑块验证时出错: {str(e)}")
                if attempt < max_retries:
                    continue
        
        # 所有尝试都失败了
        attempts_used = max(last_attempt, len(failure_records))
        logger.error(f"【{self.pure_user_id}】滑块验证失败，已尝试{attempts_used}次")
        
        # 输出失败分析摘要
        if failure_records:
            logger.info(f"【{self.pure_user_id}】失败分析摘要:")
            for record in failure_records:
                logger.info(f"  - 第{record['attempt']}次: 距离{record['slide_distance']}px, "
                          f"步数{record['total_steps']}, 最终位置{record['final_left_px']}px")
        
        # 输出当前统计摘要
        strategy_stats.log_summary()

        self._save_debug_snapshot("solve_slider_failed", getattr(self, "_detected_slider_frame", None))
        
        return False
    
    def _release_concurrency_slot(self, reason: str = "") -> bool:
        """幂等释放并发槽位，避免清理过程卡死导致后续账号永远排队。"""
        if not getattr(self, '_concurrency_slot_registered', False):
            return False
        try:
            concurrency_manager.unregister_instance(self.user_id, self)
            self._concurrency_slot_registered = False
            stats = concurrency_manager.get_stats()
            reason_suffix = f"（{reason}）" if reason else ""
            logger.info(
                f"【{self.pure_user_id}】已释放并发槽位{reason_suffix}，当前并发: "
                f"{stats['active_count']}/{stats['max_concurrent']}，等待队列: {stats['queue_length']}"
            )
            return True
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】释放并发槽位时出错: {e}")
            return False

    def _stop_playwright_with_timeout(self, timeout_seconds: float = 5.0) -> bool:
        """best-effort 停止 Playwright，遇到跨线程 greenlet 错误时降级为引用置空。

        历史实现把 stop() 放进新 daemon 线程做超时保护——但 Playwright sync 实例
        必须在 start() 时所在的同一线程销毁，跨线程 stop() 必抛
        `Cannot switch to a different thread`，等于零保护还污染日志。
        现在改为：
        - 同线程：直接 stop()，让 Playwright 自己回收
        - 跨线程：跳过 stop()，仅返回 False，由 close_browser 负责把 self.playwright = None
        """
        if not getattr(self, 'playwright', None):
            return True

        creating_tid = getattr(self, '_playwright_thread_id', None)
        current_tid = threading.get_ident()
        if creating_tid is not None and current_tid != creating_tid:
            logger.warning(
                f"【{self.pure_user_id}】跨线程销毁 Playwright "
                f"(创建 tid={creating_tid}, 当前 tid={current_tid})，"
                f"跳过 sync stop() 以避免 greenlet 错误"
            )
            return False

        try:
            self.playwright.stop()
            return True
        except Exception as exc:
            msg = str(exc)
            if 'Cannot switch to a different thread' in msg or 'greenlet' in msg.lower():
                logger.warning(
                    f"【{self.pure_user_id}】Playwright.stop() 命中 greenlet 错误，已忽略: {msg}"
                )
                return False
            raise

    def _safe_pw_dispose(self, obj_name: str, obj, action: str = 'close') -> None:
        """统一封装 Playwright 同步资源关闭：跨线程 greenlet 错误降级为日志，不抛。"""
        if not obj:
            return
        creating_tid = getattr(self, '_playwright_thread_id', None)
        current_tid = threading.get_ident()
        if creating_tid is not None and current_tid != creating_tid:
            logger.warning(
                f"【{self.pure_user_id}】跨线程销毁 {obj_name} "
                f"(创建 tid={creating_tid}, 当前 tid={current_tid})，"
                f"跳过 sync {action}() 以避免 greenlet 错误"
            )
            return
        try:
            getattr(obj, action)()
            logger.debug(f"【{self.pure_user_id}】{obj_name} 已 {action}")
        except Exception as e:
            msg = str(e)
            if 'Cannot switch to a different thread' in msg or 'greenlet' in msg.lower():
                logger.warning(
                    f"【{self.pure_user_id}】销毁 {obj_name} 命中 greenlet 错误，已忽略: {msg}"
                )
            else:
                logger.warning(f"【{self.pure_user_id}】{action} {obj_name} 时出错: {e}")

    def _extract_browser_pid(self, runtime_obj) -> Optional[int]:
        """尽量从 Playwright runtime 对象上提取浏览器进程 PID。"""
        try:
            process = getattr(runtime_obj, "process", None)
            pid = getattr(process, "pid", None)
            if pid:
                return int(pid)
        except Exception:
            pass
        try:
            browser = getattr(runtime_obj, "browser", None)
            process = getattr(browser, "process", None) if browser else None
            pid = getattr(process, "pid", None)
            if pid:
                return int(pid)
        except Exception:
            pass
        return None

    def _collect_process_tree(self, root_pid: int) -> List[int]:
        """收集给定 PID 的全部子孙进程，避免残留 Chromium 进程树。"""
        try:
            output = subprocess.check_output(
                ["ps", "-eo", "pid=,ppid="],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return [root_pid]

        parent_map: Dict[int, List[int]] = {}
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except Exception:
                continue
            parent_map.setdefault(ppid, []).append(pid)

        to_visit = [root_pid]
        seen = set()
        ordered: List[int] = []
        while to_visit:
            pid = to_visit.pop()
            if pid in seen:
                continue
            seen.add(pid)
            ordered.append(pid)
            to_visit.extend(parent_map.get(pid, []))
        return ordered

    def _force_kill_browser_process_tree(self, reason: str = "") -> bool:
        """兜底终止浏览器进程树，用于 close 失败或页面崩溃后的残留清理。"""
        pid = self._browser_pid
        if not pid:
            return False

        reason_suffix = f"（{reason}）" if reason else ""
        try:
            process_tree = self._collect_process_tree(int(pid))
            if not process_tree:
                return False

            logger.warning(
                f"【{self.pure_user_id}】开始兜底清理浏览器进程树{reason_suffix}: {process_tree}"
            )

            for signal_name, sig in (("TERM", signal.SIGTERM), ("KILL", signal.SIGKILL)):
                for proc_pid in process_tree:
                    try:
                        os.kill(proc_pid, sig)
                    except ProcessLookupError:
                        continue
                    except PermissionError as e:
                        logger.warning(f"【{self.pure_user_id}】终止进程 {proc_pid} 失败: {e}")
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】清理进程 {proc_pid} 时出错: {e}")

                time.sleep(0.4 if signal_name == "TERM" else 0.2)

            self._browser_pid = None
            return True
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】兜底清理浏览器进程树失败: {e}")
            return False

    def close_browser(self):
        """安全关闭浏览器并清理资源"""
        logger.info(f"【{self.pure_user_id}】开始清理资源...")

        # 先释放槽位，避免后续任一清理步骤卡死把同账号任务永久堵住。
        self._release_concurrency_slot("close_browser开始")

        # 清理页面 / 上下文 / 浏览器：跨线程 greenlet 错误由 _safe_pw_dispose 统一吸收
        self._safe_pw_dispose('页面', getattr(self, 'page', None), action='close')
        self.page = None

        self._safe_pw_dispose('上下文', getattr(self, 'context', None), action='close')
        self.context = None

        self._safe_pw_dispose('浏览器', getattr(self, 'browser', None), action='close')
        self.browser = None

        # 停止 Playwright（_stop_playwright_with_timeout 内部已做跨线程保护）
        try:
            if hasattr(self, 'playwright') and self.playwright:
                stopped = self._stop_playwright_with_timeout()
                if stopped:
                    logger.info(f"【{self.pure_user_id}】Playwright已停止")
                else:
                    logger.warning(f"【{self.pure_user_id}】Playwright未能在当前线程停止，已放弃 stop() 仅置空引用")
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】停止Playwright时出错: {e}")
        finally:
            # 不论 stop 成功与否，都把引用置空，避免下一次 close_browser 又对死引用操作
            self.playwright = None
            self._playwright_thread_id = None

        # 再补一层浏览器子进程兜底清理，防止 browser.close()/playwright.stop() 没有真正回收干净
        self._force_kill_browser_process_tree("close_browser")

        # 清理临时目录
        try:
            if hasattr(self, 'temp_dir') and self.temp_dir:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                logger.debug(f"【{self.pure_user_id}】临时目录已清理: {self.temp_dir}")
                self.temp_dir = None  # 设置为None，防止重复清理
        except Exception as e:
            logger.warning(f"【{self.pure_user_id}】清理临时目录时出错: {e}")

        # 再兜底释放一次，兼容前面提前释放失败的极端情况。
        self._release_concurrency_slot("close_browser收尾")

        logger.info(f"【{self.pure_user_id}】资源清理完成")
    
    def __del__(self):
        """析构函数，确保资源释放（保险机制）"""
        try:
            # 检查是否有未关闭的浏览器
            if hasattr(self, 'browser') and self.browser:
                logger.warning(f"【{self.pure_user_id}】析构函数检测到未关闭的浏览器，执行清理")
                self.close_browser()
        except Exception as e:
            # 析构函数中不要抛出异常
            logger.debug(f"【{self.pure_user_id}】析构函数清理时出错: {e}")
    
    # ==================== Playwright 登录辅助方法 ====================
    
    def _check_login_success_by_element(self, page) -> bool:
        """通过页面元素检测登录是否成功
        
        Args:
            page: Page对象
        
        Returns:
            bool: 登录成功返回True，否则返回False
        """
        try:
            # 检查目标元素
            selector = '.rc-virtual-list-holder-inner'
            logger.info(f"【{self.pure_user_id}】========== 检查登录状态（通过页面元素） ==========")
            logger.info(f"【{self.pure_user_id}】检查选择器: {selector}")
            
            # 查找元素
            element = page.query_selector(selector)
            
            if element:
                # 获取元素的子元素数量
                child_count = element.evaluate('el => el.children.length')
                inner_html = element.inner_html()
                inner_text = element.inner_text() if element.is_visible() else ""
                
                logger.info(f"【{self.pure_user_id}】找到目标元素:")
                logger.info(f"【{self.pure_user_id}】  - 子元素数量: {child_count}")
                logger.info(f"【{self.pure_user_id}】  - 是否可见: {element.is_visible()}")
                logger.info(f"【{self.pure_user_id}】  - innerText长度: {len(inner_text)}")
                logger.info(f"【{self.pure_user_id}】  - innerHTML长度: {len(inner_html)}")
                
                # 判断是否有数据：子元素数量大于0
                if child_count > 0:
                    logger.success(f"【{self.pure_user_id}】✅ 登录成功！检测到列表有 {child_count} 个子元素")
                    logger.info(f"【{self.pure_user_id}】================================================")
                    return True
                else:
                    logger.debug(f"【{self.pure_user_id}】列表为空，登录未完成")
                    logger.info(f"【{self.pure_user_id}】================================================")
                    return False
            else:
                logger.debug(f"【{self.pure_user_id}】未找到目标元素: {selector}")
                logger.info(f"【{self.pure_user_id}】================================================")
                return False
                
        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】检查登录状态时出错: {e}")
            import traceback
            logger.debug(f"【{self.pure_user_id}】错误堆栈: {traceback.format_exc()}")
            return False
    
    def _check_login_error(self, page) -> tuple:
        """检测登录是否出现错误（如账密错误）
        
        Args:
            page: Page对象
        
        Returns:
            tuple: (has_error, error_message) - 是否有错误，错误消息
        """
        try:
            logger.debug(f"【{self.pure_user_id}】检查登录错误...")
            
            # 检测账密错误
            error_selectors = [
                '.login-error-msg',  # 主要的错误消息类
                '[class*="error-msg"]',  # 包含error-msg的类
                'div:has-text("账密错误")',  # 包含"账密错误"文本的div
                'text=账密错误',  # 直接文本匹配
            ]
            
            # 在主页面和所有frame中查找
            frames_to_check = [page] + page.frames
            
            for frame in frames_to_check:
                try:
                    for selector in error_selectors:
                        try:
                            element = frame.query_selector(selector)
                            if element and element.is_visible():
                                error_text = element.inner_text()
                                logger.error(f"【{self.pure_user_id}】❌ 检测到登录错误: {error_text}")
                                return True, error_text
                        except:
                            continue
                            
                    # 也检查页面HTML中是否包含错误文本
                    try:
                        detection_text = self._read_frame_text_for_detection(frame)
                        if '账密错误' in detection_text or '账号密码错误' in detection_text or '用户名或密码错误' in detection_text:
                            logger.error(f"【{self.pure_user_id}】❌ 页面内容中检测到账密错误")
                            return True, "账密错误"
                    except PasswordLoginVerificationError:
                        raise
                    except Exception:
                        pass
                        
                except:
                    continue
            
            return False, None

        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】检查登录错误时出错: {e}")
            return False, None

    def _detect_verification_type(self, frame) -> str:
        """检测 iframe 内的具体验证类型

        Args:
            frame: iframe 的 content_frame

        Returns:
            str: 验证类型 - 'password_error' / 'face_verify' / 'sms_verify' / 'qr_verify'
                 / 'login_page' / 'unknown'
                 'login_page' 用于命中阿里普通登录页（如 mini_login.htm 左侧"快速进入"扫码登录），
                 这种情况只是登录态丢失而非身份校验，调用方应直接走登录补救而不要标为风控暂停。
        """
        try:
            # ── 0. 先看 frame URL，把"普通登录页"从 keyword 判定中独立出来 ──
            # 历史上 keyword 'qr_verify'(扫码/二维码/扫一扫/手机淘宝) 会把
            # passport.goofish.com/mini_login.htm 误判为身份验证页（因为该页本身就是
            # "扫码登录 + 账密登录" 的组合，文案天然包含"扫码"等关键词），导致
            # _request_stop_after_account_pause 误暂停账号。
            try:
                frame_url_raw = frame.url if hasattr(frame, 'url') else ""
            except Exception:
                frame_url_raw = ""
            frame_url_lower = (frame_url_raw or "").lower()
            risk_url_markers = (
                '/punish', 'captcha', 'verify_account', 'identity_verify',
                'face_verify', 'faceverify', 'liveness', 'risk_control',
                'sec_verify', 'security_verify', 'risk-control',
            )
            login_page_url_markers = (
                'passport.goofish.com/mini_login',
                'passport.goofish.com/newlogin',
                'passport.taobao.com/mini_login',
                'login.taobao.com/member/login',
            )
            if frame_url_lower and not any(m in frame_url_lower for m in risk_url_markers):
                if any(m in frame_url_lower for m in login_page_url_markers):
                    logger.info(
                        f"【{self.pure_user_id}】frame URL 命中普通登录页({frame_url_raw})，"
                        f"不进入身份验证 keyword 判定"
                    )
                    return 'login_page'

            detection_text = self._read_frame_text_for_detection(frame)
            detection_text_lower = detection_text.lower()

            # 1. 检查是否是账密错误
            # 这里不要用过宽的“登录失败”做账密错误判定，mini_login 风控页也会包含该文案。
            password_error_keywords = ['账密错误', '账号密码错误', '用户名或密码错误', '密码错误', '账号或密码错误']
            for keyword in password_error_keywords:
                if keyword in detection_text:
                    logger.info(f"【{self.pure_user_id}】检测到验证类型: 账密错误 (关键词: {keyword})")
                    return 'password_error'

            # 2. 检查是否是短信验证
            sms_keywords = ['短信验证', '验证码', '手机号', '发送验证码', '获取验证码']
            sms_count = sum(1 for keyword in sms_keywords if keyword in detection_text)
            if sms_count >= 2:  # 至少匹配2个关键词
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 短信验证")
                return 'sms_verify'

            # 3. 已超时/失效的人脸页通常需要回到二维码重新开始，不应继续误标为 face_verify
            if self._is_timed_out_verification_text(detection_text):
                logger.info(f"【{self.pure_user_id}】检测到验证页已超时/失效，按二维码恢复页处理")
                return 'qr_verify'

            # 4. 检查是否是人脸验证
            face_keywords = ['人脸', '刷脸', '面部', '拍摄脸部', '刷脸验证', '人脸验证']
            for keyword in face_keywords:
                if keyword in detection_text_lower:
                    logger.info(f"【{self.pure_user_id}】检测到验证类型: 人脸验证 (关键词: {keyword})")
                    return 'face_verify'

            # 5. 检查是否是二维码验证
            qr_keywords = ['扫码', '二维码', '扫一扫', '手机淘宝', '手机扫码']
            for keyword in qr_keywords:
                if keyword in detection_text:
                    logger.info(f"【{self.pure_user_id}】检测到验证类型: 二维码验证 (关键词: {keyword})")
                    return 'qr_verify'

            # 6. 检查 URL 特征
            frame_url = ""
            try:
                frame_url = frame.url if hasattr(frame, 'url') else ""
            except:
                pass

            if 'sms' in frame_url.lower() or 'phone' in frame_url.lower():
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 短信验证 (URL特征)")
                return 'sms_verify'

            if any(token in frame_url.lower() for token in ('face_verify', 'faceverify', 'liveness')):
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 人脸验证 (URL特征)")
                return 'face_verify'

            if 'identity_verify' in frame_url.lower():
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 人脸验证 (identity_verify URL特征)")
                return 'face_verify'

            if 'qrcode' in frame_url.lower() or 'scan' in frame_url.lower():
                logger.info(f"【{self.pure_user_id}】检测到验证类型: 二维码验证 (URL特征)")
                return 'qr_verify'

            # 顶层业务页经常既不是登录页也不是验证页，这里只是未命中验证特征，降低为 debug 避免误导。
            logger.debug(f"【{self.pure_user_id}】当前页面未命中具体验证类型，暂标记为 unknown")
            return 'unknown'

        except Exception as e:
            logger.debug(f"【{self.pure_user_id}】检测验证类型时出错: {e}")
            return 'unknown'

    def _detect_qr_code_verification(self, page) -> tuple:
        """检测是否存在二维码/人脸验证（排除滑块验证）
        
        Args:
            page: Page对象
        
        Returns:
            tuple: (has_qr, qr_frame) - 是否有二维码/人脸验证，验证frame
                   (False, None) - 如果检测到滑块验证，会先处理滑块，然后返回
        """
        try:
            logger.info(f"【{self.pure_user_id}】检测二维码/人脸验证...")
            
            # 先检查是否是滑块验证，如果是滑块验证，立即处理并返回
            slider_selectors = [
                '#nc_1_n1z',
                '.nc-container',
                '.nc_scale',
                '.nc-wrapper',
                '.nc_iconfont',
                '[class*="nc_"]'
            ]
            
            # 在主页面和所有frame中检查滑块
            frames_to_check = [page] + list(page.frames)
            for frame in frames_to_check:
                try:
                    for selector in slider_selectors:
                        try:
                            element = frame.query_selector(selector)
                            if element and element.is_visible():
                                logger.info(f"【{self.pure_user_id}】检测到滑块验证元素，立即处理滑块: {selector}")
                                # 检测到滑块验证，记录是在哪个frame中找到的
                                frame_info = "主页面" if frame == page else f"Frame: {frame.url if hasattr(frame, 'url') else '未知'}"
                                logger.info(f"【{self.pure_user_id}】滑块元素位置: {frame_info}")
                                
                                # 保存找到滑块的frame，供find_slider_elements使用
                                # 如果是在frame中找到的，保存frame引用；如果在主页面找到，保存None
                                if frame == page:
                                    self._detected_slider_frame = None  # 主页面
                                else:
                                    self._detected_slider_frame = frame  # 保存frame引用
                                
                                # 检测到滑块验证，立即处理
                                logger.warning(f"【{self.pure_user_id}】检测到滑块验证，开始自动处理...")
                                slider_risk_log = self._start_password_login_slider_risk_log(
                                    verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                    detection_phase='verification_probe',
                                )
                                slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                                if slider_success:
                                    logger.success(f"【{self.pure_user_id}】✅ 滑块验证成功！")
                                    self._finish_password_login_slider_risk_log(
                                        slider_risk_log,
                                        success=True,
                                        verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                        processing_result='密码登录流程中的滑块验证自动处理成功',
                                        extra_meta={'detection_source': '_detect_qr_code_verification'},
                                    )
                                    time.sleep(3)  # 等待滑块验证后的状态更新
                                    # 内层自救成功 → 立刻把 cookies 抓出来交给 run() 主流程，
                                    # 否则 run() 主体的 success 仍然是 False，会误存 run_failed 快照并触发退避
                                    try:
                                        recovered = self._get_cookies_after_success()
                                        if recovered:
                                            self._post_recovery_success = True
                                            self._post_recovery_cookies = recovered
                                            logger.info(
                                                f"【{self.pure_user_id}】内层滑块自救成功并已捕获 "
                                                f"cookies(条数 {len(recovered) if hasattr(recovered, '__len__') else 'unknown'})，"
                                                f"将上抛给 run() 主流程"
                                            )
                                    except Exception as recover_e:
                                        logger.warning(
                                            f"【{self.pure_user_id}】内层滑块自救成功但获取 cookie 失败: {recover_e}"
                                        )
                                else:
                                    # 常规重试仍失败后，刷新页面再补一次机会。
                                    logger.warning(
                                        f"【{self.pure_user_id}】⚠️ 滑块处理{self.slider_max_retries}次仍失败，刷新页面后重试..."
                                    )
                                    try:
                                        self.page.reload(wait_until="domcontentloaded", timeout=30000)
                                        logger.info(f"【{self.pure_user_id}】✅ 页面刷新完成")
                                        time.sleep(2)
                                        slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                                        if not slider_success:
                                            logger.error(f"【{self.pure_user_id}】❌ 刷新后滑块验证仍然失败")
                                            self._finish_password_login_slider_risk_log(
                                                slider_risk_log,
                                                success=False,
                                                verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                                error_message=self._get_slider_failure_message('滑块验证失败，请稍后重试'),
                                                extra_meta={'detection_source': '_detect_qr_code_verification'},
                                            )
                                        else:
                                            logger.success(f"【{self.pure_user_id}】✅ 刷新后滑块验证成功！")
                                            self._finish_password_login_slider_risk_log(
                                                slider_risk_log,
                                                success=True,
                                                verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                                processing_result='密码登录流程中的滑块验证自动处理成功（刷新后）',
                                                extra_meta={'detection_source': '_detect_qr_code_verification'},
                                            )
                                            time.sleep(3)
                                            # 同上：内层自救成功 → 抓 cookies 交给 run() 主流程
                                            try:
                                                recovered = self._get_cookies_after_success()
                                                if recovered:
                                                    self._post_recovery_success = True
                                                    self._post_recovery_cookies = recovered
                                                    logger.info(
                                                        f"【{self.pure_user_id}】刷新后内层滑块自救成功并已捕获 "
                                                        f"cookies(条数 {len(recovered) if hasattr(recovered, '__len__') else 'unknown'})，"
                                                        f"将上抛给 run() 主流程"
                                                    )
                                            except Exception as recover_e:
                                                logger.warning(
                                                    f"【{self.pure_user_id}】刷新后内层滑块自救成功但获取 cookie 失败: {recover_e}"
                                                )
                                    except Exception as e:
                                        logger.error(f"【{self.pure_user_id}】❌ 页面刷新失败: {e}")
                                        self._finish_password_login_slider_risk_log(
                                            slider_risk_log,
                                            success=False,
                                            verification_url=frame.url if hasattr(frame, 'url') else getattr(page, 'url', None),
                                            error_message=f'页面刷新失败: {str(e)}',
                                            extra_meta={'detection_source': '_detect_qr_code_verification'},
                                        )
                                
                                # 清理临时变量
                                if hasattr(self, '_detected_slider_frame'):
                                    delattr(self, '_detected_slider_frame')
                                
                                # 返回 False, None 表示不是二维码/人脸验证（已处理滑块）
                                return False, None
                        except:
                            continue
                except:
                    continue

            # 检测所有frames中的二维码/人脸验证
            page_url = self._safe_page_url(page)
            page_verification_type = self._detect_verification_type(page)
            page_has_login_form = self._page_has_login_form(page)
            if self._looks_like_verification_url(page_url) or (
                page_verification_type in {'face_verify', 'sms_verify', 'qr_verify'} and not page_has_login_form
            ):
                if page_verification_type == 'password_error':
                    logger.error(f"【{self.pure_user_id}】❌ 顶层页面判定为账号密码错误")
                    raise PasswordLoginVerificationError("账号密码错误，请检查账号密码是否正确")

                logger.info(f"【{self.pure_user_id}】✅ 顶层页面命中验证特征，URL: {page_url}")
                verification_screenshot = self._capture_verification_screenshot(page)
                return True, VerificationFrameWrapper(
                    page,
                    verification_type=page_verification_type,
                    verify_url=page_url or None,
                    screenshot_path=verification_screenshot
                )

            # 首先检查是否有 alibaba-login-box iframe（人脸验证或短信验证）
            try:
                iframes = page.query_selector_all('iframe')
                for iframe in iframes:
                    try:
                        iframe_id = iframe.get_attribute('id')
                        if iframe_id == 'alibaba-login-box':
                            logger.info(f"【{self.pure_user_id}】✅ 检测到 alibaba-login-box iframe")
                            frame = iframe.content_frame()
                            if frame:
                                frame_url = frame.url if hasattr(frame, 'url') else '未知'
                                logger.info(f"【{self.pure_user_id}】验证Frame URL: {frame_url}")

                                # 先检测具体的验证类型
                                verification_type = self._detect_verification_type(frame)
                                logger.info(f"【{self.pure_user_id}】检测到验证类型: {verification_type}")

                                # 命中"普通登录页"（mini_login.htm 等）→ 不是风控验证，
                                # 但仍是"账号需要重新登录"——交给 _process_verification_requirement
                                # 走「等待用户操作」路径并通知用户扫码；普通扫码登录不应作为暂停账号的诱因。
                                if verification_type == 'login_page':
                                    logger.info(
                                        f"【{self.pure_user_id}】alibaba-login-box 是普通登录页，"
                                        f"作为「待扫码登录」上抛给 _process_verification_requirement"
                                    )
                                    verification_screenshot = self._capture_verification_screenshot(
                                        page,
                                        frame=frame,
                                        iframe_selector='iframe#alibaba-login-box'
                                    )
                                    return True, VerificationFrameWrapper(
                                        frame,
                                        verification_type='login_page',
                                        verify_url=(frame.url if hasattr(frame, 'url') else None),
                                        screenshot_path=verification_screenshot,
                                    )

                                # 记录风控日志
                                try:
                                    from db_manager import db_manager
                                    event_type_map = {
                                        'password_error': 'password_error',
                                        'sms_verify': 'sms_verify',
                                        'qr_verify': 'qr_verify',
                                        'face_verify': 'face_verify',
                                        'unknown': 'unknown'
                                    }
                                    event_type_names = {
                                        'password_error': '账号密码错误',
                                        'sms_verify': '短信验证',
                                        'qr_verify': '二维码验证',
                                        'face_verify': '人脸验证',
                                        'unknown': '身份验证'
                                    }
                                    db_event_type = event_type_map.get(verification_type, 'unknown')
                                    event_name = event_type_names.get(verification_type, '身份验证')
                                    db_manager.add_risk_control_log(
                                        cookie_id=self.pure_user_id,
                                        event_type=db_event_type,
                                        session_id=getattr(self, 'risk_session_id', None),
                                        trigger_scene=getattr(self, 'risk_trigger_scene', None) or 'password_login',
                                        result_code=f"{verification_type}_detected",
                                        event_description=f"检测到{event_name}",
                                        event_meta=self._build_risk_event_meta(
                                            verification_url=frame_url,
                                            extra={
                                                'verification_type': verification_type,
                                                'account_id': self.pure_user_id,
                                            }
                                        ),
                                        processing_status='processing' if verification_type != 'password_error' else 'failed',
                                        error_message='检测到需要人工完成的身份验证' if verification_type != 'password_error' else '账号密码错误'
                                    )
                                    logger.info(f"【{self.pure_user_id}】已记录风控日志: {db_event_type}")
                                except Exception as log_err:
                                    logger.warning(f"【{self.pure_user_id}】记录风控日志失败: {log_err}")

                                # 如果是账密错误，抛出异常让调用者处理
                                if verification_type == 'password_error':
                                    logger.error(f"【{self.pure_user_id}】❌ 检测到账号密码错误")
                                    raise PasswordLoginVerificationError("账号密码错误，请检查账号密码是否正确")

                                verification_screenshot = self._capture_verification_screenshot(
                                    page,
                                    frame=frame,
                                    iframe_selector='iframe#alibaba-login-box'
                                )

                                # 如果是短信验证
                                if verification_type == 'sms_verify':
                                    logger.warning(f"【{self.pure_user_id}】⚠️ 需要短信验证，暂不支持自动处理")
                                    return True, VerificationFrameWrapper(
                                        frame,
                                        verification_type='sms_verify',
                                        screenshot_path=verification_screenshot
                                    )

                                # 如果是二维码验证
                                if verification_type == 'qr_verify':
                                    logger.warning(f"【{self.pure_user_id}】⚠️ 需要二维码验证")
                                    return True, VerificationFrameWrapper(
                                        frame,
                                        verification_type='qr_verify',
                                        screenshot_path=verification_screenshot
                                    )

                                verify_url = None
                                if verification_type == 'face_verify':
                                    verify_url = self._get_face_verification_url(frame)
                                    if verify_url:
                                        logger.info(f"【{self.pure_user_id}】✅ 获取到人脸验证链接: {verify_url}")
                                elif verification_type == 'unknown':
                                    logger.warning(
                                        f"【{self.pure_user_id}】验证类型仍不明确，保留为unknown，不默认按人脸验证处理"
                                    )

                                return True, VerificationFrameWrapper(
                                    frame,
                                    verification_type=verification_type if verification_type in {'face_verify', 'unknown'} else 'unknown',
                                    verify_url=verify_url,
                                    screenshot_path=verification_screenshot
                                )
                    except PasswordLoginVerificationError:
                        raise
                    except Exception as e:
                        logger.debug(f"【{self.pure_user_id}】检查iframe时出错: {e}")
                        continue
            except PasswordLoginVerificationError:
                raise
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】检查alibaba-login-box iframe时出错: {e}")
            
            for idx, frame in enumerate(page.frames):
                try:
                    frame_url = frame.url
                    logger.debug(f"【{self.pure_user_id}】检查Frame {idx} 是否有二维码: {frame_url}")
                    
                    # 检查frame URL是否包含 mini_login（人脸验证或短信验证页面）
                    if 'mini_login' in frame_url:
                        # 进一步确认不是滑块验证
                        is_slider = False
                        for selector in slider_selectors:
                            try:
                                element = frame.query_selector(selector)
                                if element and element.is_visible():
                                    is_slider = True
                                    break
                            except:
                                continue
                        
                        if not is_slider:
                            verification_type = self._detect_verification_type(frame)
                            if verification_type == 'login_page':
                                logger.info(
                                    f"【{self.pure_user_id}】Frame {idx} mini_login 判定为普通登录页，"
                                    f"作为「待扫码登录」上抛"
                                )
                                verification_screenshot = self._capture_verification_screenshot(page, frame=frame)
                                return True, VerificationFrameWrapper(
                                    frame,
                                    verification_type='login_page',
                                    verify_url=frame_url,
                                    screenshot_path=verification_screenshot,
                                )
                            if verification_type == 'password_error':
                                logger.error(f"【{self.pure_user_id}】❌ mini_login 页面判定为账号密码错误")
                                raise PasswordLoginVerificationError("账号密码错误，请检查账号密码是否正确")

                            verification_screenshot = self._capture_verification_screenshot(page, frame=frame)
                            verify_url = frame_url
                            if verification_type == 'face_verify':
                                verify_url = self._get_face_verification_url(frame) or frame_url

                            logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到 mini_login 页面（人脸验证/短信验证）")
                            logger.info(f"【{self.pure_user_id}】人脸验证/短信验证Frame URL: {frame_url}")
                            return True, VerificationFrameWrapper(
                                frame,
                                verification_type=verification_type,
                                verify_url=verify_url,
                                screenshot_path=verification_screenshot
                            )
                    
                    # 检查frame的父iframe是否是alibaba-login-box
                    try:
                        # 尝试通过frame的父元素查找
                        frame_element = frame.frame_element()
                        if frame_element:
                            parent_iframe_id = frame_element.get_attribute('id')
                            if parent_iframe_id == 'alibaba-login-box':
                                logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到 alibaba-login-box（人脸验证/短信验证）")
                                logger.info(f"【{self.pure_user_id}】人脸验证/短信验证Frame URL: {frame_url}")
                                verification_type = self._detect_verification_type(frame)
                                if verification_type == 'login_page':
                                    logger.info(
                                        f"【{self.pure_user_id}】Frame {idx} alibaba-login-box 是普通登录页，"
                                        f"作为「待扫码登录」上抛"
                                    )
                                    verification_screenshot = self._capture_verification_screenshot(page, frame=frame)
                                    return True, VerificationFrameWrapper(
                                        frame,
                                        verification_type='login_page',
                                        verify_url=frame_url,
                                        screenshot_path=verification_screenshot,
                                    )
                                if verification_type == 'password_error':
                                    logger.error(f"【{self.pure_user_id}】❌ alibaba-login-box 页面判定为账号密码错误")
                                    raise PasswordLoginVerificationError("账号密码错误，请检查账号密码是否正确")

                                verification_screenshot = self._capture_verification_screenshot(page, frame=frame)
                                verify_url = frame_url
                                if verification_type == 'face_verify':
                                    verify_url = self._get_face_verification_url(frame) or frame_url

                                return True, VerificationFrameWrapper(
                                    frame,
                                    verification_type=verification_type,
                                    verify_url=verify_url,
                                    screenshot_path=verification_screenshot
                                )
                    except PasswordLoginVerificationError:
                        raise
                    except Exception:
                        pass
                    
                    # 先检查这个frame是否是滑块验证
                    is_slider_frame = False
                    for selector in slider_selectors:
                        try:
                            element = frame.query_selector(selector)
                            if element and element.is_visible():
                                logger.debug(f"【{self.pure_user_id}】Frame {idx} 包含滑块验证元素，跳过")
                                is_slider_frame = True
                                break
                        except:
                            continue
                    
                    if is_slider_frame:
                        continue  # 跳过滑块验证的frame
                    
                    # 二维码验证的选择器（更精确，避免误判滑块验证）
                    qr_selectors = [
                        'img[alt*="二维码"]',
                        'img[alt*="扫码"]',
                        'img[src*="qrcode"]',
                        'canvas[class*="qrcode"]',
                        '.qr-code',
                        '#qr-code',
                        '[class*="qr-code"]',
                        '[id*="qr-code"]'
                    ]
                    
                    # 检查是否有真正的二维码图片（不是滑块验证中的qrcode类）
                    for selector in qr_selectors:
                        try:
                            element = frame.query_selector(selector)
                            if element and element.is_visible():
                                # 进一步验证：检查是否包含滑块元素，如果包含则跳过
                                has_slider_in_frame = False
                                for slider_sel in slider_selectors:
                                    try:
                                        slider_elem = frame.query_selector(slider_sel)
                                        if slider_elem and slider_elem.is_visible():
                                            has_slider_in_frame = True
                                            break
                                    except:
                                        continue
                                
                                if not has_slider_in_frame:
                                    logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到二维码验证: {selector}")
                                    logger.info(f"【{self.pure_user_id}】二维码Frame URL: {frame_url}")
                                    return True, frame
                        except:
                            continue
                    
                    # 人脸验证的关键词（更精确）
                    face_keywords = ['拍摄脸部', '人脸验证', '人脸识别', '面部验证', '请进行人脸验证', '请完成人脸识别']
                    try:
                        frame_text = self._read_frame_text_for_detection(frame)
                        # 检查是否包含人脸验证关键词，但不包含滑块相关关键词
                        has_face_keyword = False
                        for keyword in face_keywords:
                            if keyword in frame_text:
                                has_face_keyword = True
                                break
                        
                        # 如果包含人脸验证关键词，且不包含滑块关键词，则认为是人脸验证
                        if has_face_keyword:
                            slider_keywords = ['滑块', '拖动', 'nc_', 'nc-container']
                            has_slider_keyword = any(keyword in frame_text for keyword in slider_keywords)
                            
                            if not has_slider_keyword:
                                logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到人脸验证")
                                logger.info(f"【{self.pure_user_id}】人脸验证Frame URL: {frame_url}")
                                return True, frame
                    except:
                        pass
                        
                except PasswordLoginVerificationError:
                    raise
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】检查Frame {idx} 失败: {e}")
                    continue
            
            logger.info(f"【{self.pure_user_id}】未检测到二维码/人脸验证")
            return False, None
            
        except PasswordLoginVerificationError:
            raise
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】检测二维码/人脸验证时出错: {e}")
            return False, None
    
    def _get_face_verification_url(self, frame) -> str:
        """在alibaba-login-box frame中，点击'其他验证方式'，然后找到'通过拍摄脸部'的验证按钮，获取链接"""
        try:
            logger.info(f"【{self.pure_user_id}】开始查找人脸验证链接...")
            
            # 等待frame加载完成
            time.sleep(2)
            
            # 查找"其他验证方式"链接并点击
            other_verify_clicked = False
            try:
                # 尝试通过文本内容查找所有链接
                all_links = frame.query_selector_all('a')
                for link in all_links:
                    try:
                        text = link.inner_text()
                        if '其他验证方式' in text or ('其他' in text and '验证' in text):
                            logger.info(f"【{self.pure_user_id}】找到'其他验证方式'链接，点击中...")
                            link.click()
                            time.sleep(2)  # 等待页面切换
                            other_verify_clicked = True
                            break
                    except:
                        continue
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】查找'其他验证方式'链接时出错: {e}")
            
            if not other_verify_clicked:
                logger.warning(f"【{self.pure_user_id}】未找到'其他验证方式'链接，可能已经在验证方式选择页面")
            
            # 等待页面加载
            time.sleep(2)
            
            # 查找"通过拍摄脸部"相关的验证按钮，获取href并点击按钮
            face_verify_url = None
            
            # 方法1: 使用JavaScript精确查找，获取href并点击按钮（根据HTML结构：li > div.desc包含"通过 拍摄脸部" + a.ui-button包含"立即验证"）
            try:
                href = frame.evaluate("""
                    () => {
                        // 查找所有li元素
                        const listItems = document.querySelectorAll('li');
                        for (let li of listItems) {
                            // 查找包含"通过 拍摄脸部"或"通过拍摄脸部"的desc div，但不能包含"手机"
                            const descDiv = li.querySelector('div.desc');
                            if (descDiv && !descDiv.innerText.includes('手机') && (descDiv.innerText.includes('通过 拍摄脸部') || descDiv.innerText.includes('通过拍摄脸部') || descDiv.innerText.includes('拍摄脸部'))) {
                                // 在同一li中查找"立即验证"按钮
                                const verifyButton = li.querySelector('a.ui-button, a.ui-button-small, button');
                                if (verifyButton && verifyButton.innerText && verifyButton.innerText.includes('立即验证')) {
                                    // 获取按钮的href属性
                                    const href = verifyButton.href || verifyButton.getAttribute('href') || null;
                                    // 点击按钮
                                    verifyButton.click();
                                    // 返回href
                                    return href;
                                }
                            }
                        }
                        return null;
                    }
                """)
                if href:
                    face_verify_url = href
                    logger.info(f"【{self.pure_user_id}】通过JavaScript找到'通过拍摄脸部'验证按钮的href并已点击: {face_verify_url}")
            except Exception as e:
                logger.debug(f"【{self.pure_user_id}】方法1（JavaScript）查找失败: {e}")
            
            # 方法2: 如果方法1失败，使用Playwright API查找并点击
            if not face_verify_url:
                try:
                    # 查找所有li元素
                    list_items = frame.query_selector_all('li')
                    for li in list_items:
                        try:
                            # 查找desc div
                            desc_div = li.query_selector('div.desc')
                            if desc_div:
                                desc_text = desc_div.inner_text()
                                if '手机' not in desc_text and ('通过 拍摄脸部' in desc_text or '通过拍摄脸部' in desc_text or '拍摄脸部' in desc_text):
                                    logger.info(f"【{self.pure_user_id}】找到'通过拍摄脸部'选项（方法2）")
                                    # 在同一li中查找验证按钮
                                    verify_button = li.query_selector('a.ui-button, a.ui-button-small, button')
                                    if verify_button:
                                        button_text = verify_button.inner_text()
                                        if '立即验证' in button_text:
                                            # 获取按钮的href属性
                                            href = verify_button.get_attribute('href')
                                            if href:
                                                face_verify_url = href
                                                logger.info(f"【{self.pure_user_id}】找到'通过拍摄脸部'验证按钮的href: {face_verify_url}")
                                                # 点击按钮
                                                logger.info(f"【{self.pure_user_id}】点击'立即验证'按钮...")
                                                verify_button.click()
                                                logger.info(f"【{self.pure_user_id}】已点击'立即验证'按钮")
                                                break
                        except:
                            continue
                except Exception as e:
                    logger.debug(f"【{self.pure_user_id}】方法2查找失败: {e}")
            
            if face_verify_url:
                # 如果是相对路径，转换为绝对路径
                if not face_verify_url.startswith('http'):
                    base_url = frame.url.split('/iv/')[0] if '/iv/' in frame.url else 'https://passport.goofish.com'
                    if face_verify_url.startswith('/'):
                        face_verify_url = base_url + face_verify_url
                    else:
                        face_verify_url = base_url + '/' + face_verify_url
                
                return face_verify_url
            else:
                logger.warning(f"【{self.pure_user_id}】未找到人脸验证链接，返回原始frame URL")
                return frame.url if hasattr(frame, 'url') else None
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】获取人脸验证链接时出错: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def _is_profile_in_use_launch_error(self, error: Exception) -> bool:
        error_text = str(error or "").lower()
        lock_markers = (
            "profile appears to be in use",
            "process_singleton",
            "chromium has locked the profile",
            "user data directory is already in use",
        )
        return any(marker in error_text for marker in lock_markers)

    def _get_current_hostname(self) -> str:
        try:
            return str(socket.gethostname() or "").strip()
        except Exception:
            return ""

    def _looks_like_docker_container_hostname(self, hostname: str) -> bool:
        normalized = str(hostname or "").strip().lower()
        return bool(re.fullmatch(r"[0-9a-f]{12}", normalized))

    def _is_process_alive(self, pid: int) -> bool:
        try:
            normalized_pid = int(pid)
        except (TypeError, ValueError):
            return False
        if normalized_pid <= 0:
            return False
        try:
            os.kill(normalized_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _parse_chromium_singleton_lock(self, profile_dir: str) -> Optional[Dict[str, Any]]:
        lock_path = os.path.join(profile_dir, "SingletonLock")
        if not os.path.islink(lock_path):
            return None
        try:
            target = os.readlink(lock_path)
        except OSError as read_error:
            logger.warning(f"【{self.pure_user_id}】读取 Chromium SingletonLock 失败: {read_error}")
            return None

        target_name = os.path.basename(str(target or "").rstrip("/\\"))
        if "-" not in target_name:
            return {
                "lock_path": lock_path,
                "target": target,
                "host": None,
                "pid": None,
            }

        lock_host, pid_text = target_name.rsplit("-", 1)
        if not lock_host or not pid_text.isdigit():
            return {
                "lock_path": lock_path,
                "target": target,
                "host": None,
                "pid": None,
            }

        return {
            "lock_path": lock_path,
            "target": target,
            "host": lock_host,
            "pid": int(pid_text),
        }

    def _try_cleanup_stale_chromium_singleton_lock(self, profile_dir: str) -> bool:
        lock_info = self._parse_chromium_singleton_lock(profile_dir)
        if not lock_info:
            logger.info(f"【{self.pure_user_id}】未发现可判定的 Chromium SingletonLock，跳过自动清理")
            return False

        current_host = self._get_current_hostname()
        lock_host = str(lock_info.get("host") or "").strip()
        lock_pid = lock_info.get("pid")
        if not current_host or not lock_host or lock_pid is None:
            logger.warning(
                f"【{self.pure_user_id}】SingletonLock 信息不足，无法证明是 stale 锁，保持原有 fallback: "
                f"host={lock_host or 'unknown'}, pid={lock_pid}"
            )
            return False

        same_host = lock_host == current_host
        same_docker_host_rollover = (
            not same_host and
            self._looks_like_docker_container_hostname(lock_host) and
            self._looks_like_docker_container_hostname(current_host)
        )
        if not same_host and not same_docker_host_rollover:
            logger.warning(
                f"【{self.pure_user_id}】SingletonLock 指向其他宿主机，拒绝自动清理: "
                f"lock_host={lock_host}, current_host={current_host}, pid={lock_pid}"
            )
            return False
        if same_docker_host_rollover:
            logger.warning(
                f"【{self.pure_user_id}】检测到 Docker 容器 hostname 漂移导致的 stale SingletonLock，"
                f"允许按失效锁清理: lock_host={lock_host}, current_host={current_host}, pid={lock_pid}"
            )

        if self._is_process_alive(lock_pid):
            logger.info(f"【{self.pure_user_id}】SingletonLock 对应进程仍存活(pid={lock_pid})，跳过自动清理")
            return False

        removed_any = False
        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock_path = os.path.join(profile_dir, lock_name)
            try:
                if os.path.lexists(lock_path):
                    os.unlink(lock_path)
                    removed_any = True
                    logger.warning(f"【{self.pure_user_id}】已清理 stale Chromium 锁文件: {lock_path}")
            except OSError as cleanup_error:
                logger.warning(f"【{self.pure_user_id}】清理 stale Chromium 锁文件失败({lock_path}): {cleanup_error}")

        return removed_any

    def _launch_clean_cookie_seeded_context(
        self,
        playwright,
        launch_options: Dict[str, Any],
        browser_features: Dict[str, Any],
    ) -> Tuple[Any, Any]:
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context(
            viewport={'width': browser_features['viewport_width'], 'height': browser_features['viewport_height']},
            user_agent=browser_features['user_agent'],
            locale=browser_features['locale'],
            accept_downloads=True,
            ignore_https_errors=True,
            extra_http_headers={
                'Accept-Language': browser_features['accept_lang']
            }
        )
        try:
            cookies_to_inject = self._build_initial_cookie_payload()
            cookie_str = ''
            if not cookies_to_inject:
                from db_manager import db_manager as _db
                cookie_info = _db.get_cookie_details(self.pure_user_id)
                if cookie_info and cookie_info.get('value'):
                    cookie_str = cookie_info['value']
            if cookie_str:
                cookies_to_inject = []
                for pair in cookie_str.split(';'):
                    pair = pair.strip()
                    if '=' in pair:
                        name, value = pair.split('=', 1)
                        name = name.strip()
                        value = value.strip()
                        if name:
                            cookies_to_inject.append({
                                'name': name,
                                'value': value,
                                'domain': '.goofish.com',
                                'path': '/',
                            })
                            if name in ('_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 'sgcookie', 'unb', 't', 'cna'):
                                cookies_to_inject.append({
                                    'name': name,
                                    'value': value,
                                    'domain': '.taobao.com',
                                    'path': '/',
                                })
            if cookies_to_inject:
                context.add_cookies(cookies_to_inject)
                logger.info(f"【{self.pure_user_id}】已注入 {len(cookies_to_inject)} 个历史 Cookie 到干净上下文")
            else:
                logger.info(f"【{self.pure_user_id}】未找到可注入的历史 Cookie，继续使用全新上下文")
        except Exception as inject_e:
            logger.warning(f"【{self.pure_user_id}】注入历史 Cookie 失败（不影响继续登录）: {inject_e}")
        return browser, context
    
    def login_with_password_playwright(self, account: str, password: str, show_browser: bool = False,
                                      notification_callback: Optional[Callable] = None,
                                      force_clean_context: bool = False) -> dict:
        """使用Playwright进行密码登录（新方法，替代DrissionPage）
        
        Args:
            account: 登录账号（必填）
            password: 登录密码（必填）
            show_browser: 是否显示浏览器窗口（默认False为无头模式）
            notification_callback: 可选的通知回调函数，用于发送二维码/人脸验证通知（接受错误消息字符串作为参数）
            force_clean_context: 是否强制使用干净的临时浏览器上下文
        
        Returns:
            dict: Cookie字典，失败返回None
        """
        try:
            self.last_login_error = ""
            previous_slider_refresh_mode = getattr(self, '_slider_refresh_mode', False)
            self._slider_refresh_mode = force_clean_context
            previous_risk_trigger_scene = getattr(self, 'risk_trigger_scene', None)
            inferred_risk_trigger_scene = 'manual_password_refresh' if force_clean_context else 'password_login'
            if not previous_risk_trigger_scene:
                self.risk_trigger_scene = inferred_risk_trigger_scene
                logger.info(f"【{self.pure_user_id}】密码登录流程自动补齐 risk_trigger_scene={self.risk_trigger_scene}")
            else:
                logger.info(f"【{self.pure_user_id}】密码登录流程沿用 risk_trigger_scene={previous_risk_trigger_scene}")
            self._password_slider_runtime_hardened = False

            # 检查日期有效性
            if not self._check_date_validity():
                logger.error(f"【{self.pure_user_id}】日期验证失败，无法执行登录")
                return self._fail_login("日期验证失败，无法执行登录")

            if not self.browser_channel and not self.executable_path:
                self._ensure_project_playwright_browser()
            
            # 验证必需参数
            if not account or not password:
                logger.error(f"【{self.pure_user_id}】账号或密码不能为空")
                return self._fail_login("账号或密码不能为空")
            
            browser_mode = "有头" if show_browser else "无头"
            notification_scene = "手动刷新Cookie" if force_clean_context else "账号密码登录"
            logger.info(f"【{self.pure_user_id}】开始{browser_mode}模式密码登录流程（使用Playwright）...")
            logger.info(f"【{self.pure_user_id}】账号: {account}")
            logger.info("=" * 60)
            
            import os
            if force_clean_context:
                logger.warning(f"【{self.pure_user_id}】刷新模式启用干净上下文，不复用历史浏览器会话")
            else:
                user_data_dir = os.path.join(os.getcwd(), 'browser_data', f'user_{self.pure_user_id}')
                os.makedirs(user_data_dir, exist_ok=True)
                logger.info(f"【{self.pure_user_id}】使用用户数据目录: {user_data_dir}")
            
            # 在启动Playwright之前，重新检查和设置浏览器路径
            # 确保使用正确的浏览器版本（避免版本不匹配问题）
            import sys
            from pathlib import Path
            if getattr(sys, 'frozen', False):
                # 如果是打包后的exe，检查exe同目录下的浏览器
                exe_dir = Path(sys.executable).parent
                playwright_dir = exe_dir / 'playwright'

                if playwright_dir.exists():
                    chromium_dirs = list(playwright_dir.glob('chromium-*'))
                    # 找到第一个完整的浏览器目录
                    for chromium_dir in chromium_dirs:
                        chrome_exe = chromium_dir / 'chrome-win' / 'chrome.exe'
                        if chrome_exe.exists() and chrome_exe.stat().st_size > 0:
                            # 清除旧的环境变量，使用实际存在的浏览器
                            if 'PLAYWRIGHT_BROWSERS_PATH' in os.environ:
                                old_path = os.environ['PLAYWRIGHT_BROWSERS_PATH']
                                if old_path != str(playwright_dir):
                                    logger.info(f"【{self.pure_user_id}】清除旧的环境变量: {old_path}")
                                    del os.environ['PLAYWRIGHT_BROWSERS_PATH']
                            # 设置正确的环境变量
                            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(playwright_dir)
                            logger.info(f"【{self.pure_user_id}】已设置PLAYWRIGHT_BROWSERS_PATH: {playwright_dir}")
                            logger.info(f"【{self.pure_user_id}】使用浏览器版本: {chromium_dir.name}")
                            break

            # 🔧 关键修复：复用完整浏览器画像，与 captcha 验证流程保持一致
            browser_features = self._get_random_browser_features()
            self.browser_features = browser_features
            self.profile_id = browser_features.get("profile_id", "unknown")
            logger.info(f"【{self.pure_user_id}】密码登录使用浏览器画像: {self.profile_id}, "
                       f"viewport: {browser_features['viewport_width']}x{browser_features['viewport_height']}, "
                       f"scale: {browser_features['device_scale_factor']}")

            # 设置浏览器启动参数（保持原始参数，之前有头模式正常工作）
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
                '--lang=zh-CN',
                '--disable-infobars',
                '--disable-extensions',
                '--disable-popup-blocking',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
            ]

            # 启动浏览器
            if not self.browser_channel and not self.executable_path:
                self._ensure_project_playwright_browser()

            playwright_factory = self._get_sync_playwright_factory()
            playwright = playwright_factory().start()
            self._playwright_thread_id = threading.get_ident()
            browser = None
            used_profile_lock_fallback = False
            launch_options: Dict[str, Any] = {
                'headless': not show_browser,
                'ignore_default_args': ['--enable-automation'],
                'args': browser_args,
            }
            proxy_settings = self._build_playwright_proxy_settings()
            if proxy_settings:
                launch_options['proxy'] = proxy_settings
                logger.info(f"【{self.pure_user_id}】密码登录浏览器启用代理: {proxy_settings['server']}")
            if self.browser_channel:
                launch_options['channel'] = self.browser_channel
            if self.executable_path:
                launch_options['executable_path'] = self.executable_path
            if force_clean_context:
                browser, context = self._launch_clean_cookie_seeded_context(
                    playwright,
                    launch_options,
                    browser_features,
                )
            else:
                try:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir,
                        **launch_options,
                        viewport={'width': browser_features['viewport_width'], 'height': browser_features['viewport_height']},
                        user_agent=browser_features['user_agent'],
                        locale=browser_features['locale'],
                        accept_downloads=True,
                        ignore_https_errors=True,
                        extra_http_headers={
                            'Accept-Language': browser_features['accept_lang']
                        }
                    )
                except Exception as persistent_launch_error:
                    if not self._is_profile_in_use_launch_error(persistent_launch_error):
                        raise
                    used_profile_lock_fallback = True
                    logger.warning(
                        f"【{self.pure_user_id}】持久化浏览器目录被其他 Chromium 进程占用，"
                        f"自动切换到干净上下文兜底登录: {persistent_launch_error}"
                    )
                    browser, context = self._launch_clean_cookie_seeded_context(
                        playwright,
                        launch_options,
                        browser_features,
                    )
            effective_clean_context = force_clean_context or used_profile_lock_fallback
            logger.info(f"【{self.pure_user_id}】已设置浏览器语言为中文（zh-CN）")

            if not browser:
                browser = context.browser
            self._browser_pid = self._extract_browser_pid(browser or context)
            page = context.new_page()
            self._apply_headless_network_fingerprint(page, browser_features)
            observed_set_cookie_updates: Dict[str, str] = {}

            def _capture_response_set_cookie(response):
                try:
                    updates = self._extract_set_cookie_updates_from_playwright_response(response)
                    if not updates:
                        return
                    interesting_keys = [
                        key for key in ('havana_lgc2_77', 'x5secdata', 'x5sec', '_m_h5_tk', '_m_h5_tk_enc', 'sgcookie')
                        if key in updates
                    ]
                    for key, value in updates.items():
                        observed_set_cookie_updates[key] = value
                    if interesting_keys:
                        summary = ', '.join(
                            f"{key}(长度:{len(str(observed_set_cookie_updates.get(key) or ''))})"
                            for key in interesting_keys
                        )
                        logger.info(
                            f"【{self.pure_user_id}】登录网络响应捕获到Set-Cookie: {summary} | URL: {getattr(response, 'url', '')}"
                        )
                except Exception as capture_e:
                    logger.debug(f"【{self.pure_user_id}】捕获登录响应Set-Cookie失败: {capture_e}")

            try:
                context.on("response", _capture_response_set_cookie)
            except Exception as listener_e:
                logger.warning(f"【{self.pure_user_id}】注册登录响应监听失败（不影响主流程）: {listener_e}")

            # 有头模式使用轻量反检测脚本（完整脚本会覆盖 document.fonts / EventTarget /
            # Performance.now / Date 等浏览器核心 API，导致页面白屏无法渲染）；
            # 无头模式使用完整脚本以通过自动化检测。
            if show_browser:
                stealth_js = """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
                window.chrome = { runtime: {} };
                """
                page.add_init_script(stealth_js)
            else:
                password_login_stealth_mode = None
                if not show_browser and not self.stealth_mode_override:
                    password_login_stealth_mode = "lite"
                    logger.info(f"【{self.pure_user_id}】密码登录链路默认使用 lite 反检测脚本，避免无头登录页白屏")
                self._install_stealth_init_script(page, browser_features, mode_override=password_login_stealth_mode)

            logger.info(f"【{self.pure_user_id}】浏览器已成功启动（{browser_mode}模式，画像: {self.profile_id}）")

            try:
                # 预访问：先访问闲鱼首页建立正常浏览历史（降低空白浏览器的风控风险）
                try:
                    logger.info(f"【{self.pure_user_id}】预访问闲鱼首页，建立浏览历史...")
                    page.goto("https://www.goofish.com", wait_until='domcontentloaded', timeout=15000)
                    time.sleep(random.uniform(1.0, 2.0))
                    logger.info(f"【{self.pure_user_id}】预访问完成，当前URL: {page.url}")
                except Exception as warmup_e:
                    logger.warning(f"【{self.pure_user_id}】预访问失败（不影响登录）: {warmup_e}")

                # 访问登录页面（带重试逻辑）
                login_url = "https://www.goofish.com/im"
                logger.info(f"【{self.pure_user_id}】访问登录页面: {login_url}")

                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        page.goto(login_url, wait_until='networkidle', timeout=60000)
                        break
                    except Exception as e:
                        error_msg = str(e)
                        if any(err in error_msg for err in ['ERR_CONNECTION_CLOSED', 'ERR_CONNECTION_RESET', 'ERR_CONNECTION_REFUSED']):
                            if attempt < max_retries - 1:
                                wait_time = 2 * (attempt + 1)
                                logger.warning(f"【{self.pure_user_id}】连接被关闭，{wait_time}秒后第{attempt+2}次重试...")
                                time.sleep(wait_time)
                                continue
                        raise
                
                # 等待页面加载
                wait_time = 2 if not show_browser else 2
                logger.info(f"【{self.pure_user_id}】等待页面加载（{wait_time}秒）...")
                time.sleep(wait_time)
                
                # 页面诊断信息
                logger.info(f"【{self.pure_user_id}】========== 页面诊断信息 ==========")
                logger.info(f"【{self.pure_user_id}】当前URL: {page.url}")
                logger.info(f"【{self.pure_user_id}】页面标题: {page.title()}")
                logger.info(f"【{self.pure_user_id}】=====================================")
                
                # 【步骤1】查找登录frame（闲鱼登录通常在iframe中）
                logger.info(f"【{self.pure_user_id}】查找登录frame...")
                login_selectors = self._get_password_login_selectors()
                
                # 等待页面和iframe加载完成
                logger.info(f"【{self.pure_user_id}】等待页面和iframe加载...")
                time.sleep(1)
                login_frame, found_login_form, matched_selector = self._find_login_form_with_retry(
                    page,
                    timeout_seconds=8.0,
                    poll_interval=1.0,
                )
                iframes = page.query_selector_all('iframe')
                logger.info(f"【{self.pure_user_id}】当前检测到 {len(iframes)} 个 iframe")
                
                # 【情况1】找到frame且找到登录表单 → 正常登录流程
                if found_login_form:
                    logger.info(f"【{self.pure_user_id}】找到登录表单（{matched_selector}），开始正常登录流程...")
                
                # 【情况2】找到frame但未找到登录表单 → 可能已登录，直接检测滑块
                elif len(iframes) > 0:
                    logger.warning(f"【{self.pure_user_id}】找到iframe但未找到登录表单，可能已登录，检测滑块...")
                    
                    # 先将page和context保存到实例变量（供solve_slider使用）
                    original_page = self.page
                    original_context = self.context
                    original_browser = self.browser
                    original_playwright = self.playwright
                    
                    self.page = page
                    self.context = context
                    self.browser = browser
                    self.playwright = playwright
                    
                    try:
                        monitor_page = self._select_monitor_page(context, page)

                        has_error, error_message = self._check_login_error(monitor_page)
                        if has_error:
                            logger.error(f"【{self.pure_user_id}】❌ 登录失败：{error_message}")
                            raise Exception(error_message if error_message else "登录失败，请检查账号密码是否正确")

                        clicked_direct_enter, direct_enter_page = self._click_direct_enter_if_present(monitor_page, context)
                        if clicked_direct_enter:
                            login_success, active_page, _ = self._probe_context_login_success(context, direct_enter_page or monitor_page)
                            if login_success:
                                logger.success(f"【{self.pure_user_id}】✅ 普通登录页快速进入后登录成功")
                                return self._finalize_logged_in_cookies(
                                    context,
                                    active_page or direct_enter_page or monitor_page,
                                    scene="普通登录页快速进入后登录成功",
                                    notification_callback=notification_callback,
                                    notification_scene=notification_scene,
                                )
                            monitor_page = self._select_monitor_page(context, direct_enter_page or monitor_page)

                        has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                        if has_qr:
                            logger.warning(f"【{self.pure_user_id}】检测到前置身份验证，直接进入验证等待流程")
                            return self._process_verification_requirement(
                                context,
                                monitor_page,
                                qr_frame,
                                notification_callback,
                                notification_scene,
                            )

                        # 检测滑块元素（在主页面和所有frame中查找）
                        slider_selectors = [
                            '#nc_1_n1z',
                            '.nc-container',
                            '.nc_scale',
                            '.nc-wrapper'
                        ]
                        
                        has_slider = False
                        detected_slider_frame = None
                        
                        # 先在主页面查找
                        for selector in slider_selectors:
                            try:
                                element = page.query_selector(selector)
                                if element and element.is_visible():
                                    logger.info(f"【{self.pure_user_id}】✅ 在主页面检测到滑块验证元素: {selector}")
                                    has_slider = True
                                    detected_slider_frame = None  # None表示主页面
                                    break
                            except:
                                continue
                        
                        # 如果主页面没找到，在所有frame中查找
                        if not has_slider:
                            for idx, iframe in enumerate(iframes):
                                try:
                                    frame = iframe.content_frame()
                                    if frame:
                                        # 等待frame内容加载
                                        try:
                                            frame.wait_for_load_state('domcontentloaded', timeout=2000)
                                        except:
                                            pass
                                        
                                        for selector in slider_selectors:
                                            try:
                                                element = frame.query_selector(selector)
                                                if element and element.is_visible():
                                                    logger.info(f"【{self.pure_user_id}】✅ 在Frame {idx} 检测到滑块验证元素: {selector}")
                                                    has_slider = True
                                                    detected_slider_frame = frame
                                                    break
                                            except:
                                                continue
                                        
                                        if has_slider:
                                            break
                                except Exception as e:
                                    logger.debug(f"【{self.pure_user_id}】检查Frame {idx}时出错: {e}")
                                    continue
                        
                        if has_slider:
                            # 设置检测到的frame，供solve_slider使用
                            self._detected_slider_frame = detected_slider_frame
                            if effective_clean_context:
                                logger.info(f"【{self.pure_user_id}】干净上下文检测到前置风控滑块，尝试自动处理...")

                            logger.warning(f"【{self.pure_user_id}】检测到滑块验证，开始处理...")
                            slider_risk_log = self._start_password_login_slider_risk_log(
                                verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                detection_phase='pre_login_monitor',
                            )
                            time.sleep(3)
                            slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                            
                            if not slider_success:
                                feedback = self.last_verification_feedback or {}
                                if feedback.get("source") == "slider_missing":
                                    logger.error(f"【{self.pure_user_id}】❌ 滑块流程结束后页面已不再包含滑块，停止额外刷新重试")
                                    self._finish_password_login_slider_risk_log(
                                        slider_risk_log,
                                        success=False,
                                        verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                        error_message=self._get_slider_failure_message("页面状态已变化，未找到滑块容器，请重新尝试刷新Cookie"),
                                        extra_meta={'detection_source': 'login_with_password_playwright_pre_login'},
                                    )
                                    return self._fail_login(self._get_slider_failure_message("页面状态已变化，未找到滑块容器，请重新尝试刷新Cookie"))

                                # 常规重试仍失败后，刷新页面再补一次机会。
                                logger.warning(
                                    f"【{self.pure_user_id}】⚠️ 滑块处理{self.slider_max_retries}次仍失败，刷新页面后重试..."
                                )
                                try:
                                    page.reload(wait_until="domcontentloaded", timeout=30000)
                                    logger.info(f"【{self.pure_user_id}】✅ 页面刷新完成")
                                    time.sleep(2)
                                    slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                                    if not slider_success:
                                        feedback = self.last_verification_feedback or {}
                                        if feedback.get("source") == "slider_missing":
                                            logger.error(f"【{self.pure_user_id}】❌ 刷新后页面未出现滑块，停止重复尝试")
                                        logger.error(f"【{self.pure_user_id}】❌ 刷新后滑块验证仍然失败")
                                        self._finish_password_login_slider_risk_log(
                                            slider_risk_log,
                                            success=False,
                                            verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                            error_message=self._get_slider_failure_message("滑块验证失败，请稍后重试"),
                                            extra_meta={'detection_source': 'login_with_password_playwright_pre_login'},
                                        )
                                        return self._fail_login(self._get_slider_failure_message("滑块验证失败，请稍后重试"))
                                    else:
                                        logger.success(f"【{self.pure_user_id}】✅ 刷新后滑块验证成功！")
                                except Exception as e:
                                    logger.error(f"【{self.pure_user_id}】❌ 页面刷新失败: {e}")
                                    self._finish_password_login_slider_risk_log(
                                        slider_risk_log,
                                        success=False,
                                        verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                        error_message=f"页面会话已失效: {str(e)}",
                                        extra_meta={'detection_source': 'login_with_password_playwright_pre_login'},
                                    )
                                    return self._fail_login("页面会话已失效，请重新尝试刷新Cookie")
                            else:
                                logger.success(f"【{self.pure_user_id}】✅ 滑块验证成功！")
                            self._finish_password_login_slider_risk_log(
                                slider_risk_log,
                                success=True,
                                verification_url=(detected_slider_frame.url if detected_slider_frame and hasattr(detected_slider_frame, 'url') else getattr(page, 'url', None)),
                                processing_result='密码登录流程中的滑块验证自动处理成功',
                                extra_meta={'detection_source': 'login_with_password_playwright_pre_login'},
                            )
                            
                            # 等待页面加载和状态更新（第一次等待3秒）
                            logger.info(f"【{self.pure_user_id}】等待3秒，让页面加载完成...")
                            time.sleep(3)
                            
                            # 第一次检查登录状态
                            login_success, active_page, _ = self._probe_context_login_success(context, page)
                            
                            # 如果第一次没检测到，再等待5秒后重试
                            if not login_success:
                                logger.info(f"【{self.pure_user_id}】第一次检测未发现登录状态，等待5秒后重试...")
                                time.sleep(5)
                                login_success, active_page, _ = self._probe_context_login_success(context, active_page or page)
                            
                            if login_success:
                                logger.success(f"【{self.pure_user_id}】✅ 滑块验证后登录成功")
                                return self._finalize_logged_in_cookies(
                                    context,
                                    active_page or page,
                                    scene="滑块验证后登录成功",
                                    notification_callback=notification_callback,
                                    notification_scene=notification_scene,
                                )
                            else:
                                # 滑块验证后登录状态不明确，检测是否需要人脸/短信/二维码验证
                                logger.warning(f"【{self.pure_user_id}】⚠️ 滑块验证后登录状态不明确，检测是否需要身份验证...")
                                time.sleep(1)
                                monitor_page = self._select_monitor_page(context, page)
                                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)

                                if has_qr:
                                    return self._process_verification_requirement(
                                        context,
                                        monitor_page,
                                        qr_frame,
                                        notification_callback,
                                        notification_scene,
                                    )
                                else:
                                    logger.warning(f"【{self.pure_user_id}】⚠️ 未检测到身份验证，登录状态不明确")
                                    return self._fail_login("滑块验证后登录状态未确认，请稍后重试")
                        else:
                            logger.info(f"【{self.pure_user_id}】未检测到滑块验证")

                            # 未检测到滑块时，检查是否已登录
                            login_success, active_page, _ = self._probe_context_login_success(context, page)
                            if login_success:
                                logger.success(f"【{self.pure_user_id}】✅ 检测到已登录状态")
                                return self._finalize_logged_in_cookies(
                                    context,
                                    active_page or page,
                                    scene="无滑块已登录场景",
                                    notification_callback=notification_callback,
                                    notification_scene=notification_scene,
                                )
                            else:
                                monitor_page = self._select_monitor_page(context, active_page or page)
                                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                                if has_qr:
                                    return self._process_verification_requirement(
                                        context,
                                        monitor_page,
                                        qr_frame,
                                        notification_callback,
                                        notification_scene,
                                    )
                                logger.warning(f"【{self.pure_user_id}】⚠️ 未检测到滑块且未登录，不获取Cookie")
                                return self._fail_login("未检测到登录表单或有效登录态")
                    
                    finally:
                        # 恢复原始值
                        self.page = original_page
                        self.context = original_context
                        self.browser = original_browser
                        self.playwright = original_playwright
                
                # 【情况3】未找到frame → 检查是否已登录
                else:
                    logger.warning(f"【{self.pure_user_id}】未找到任何iframe，检查是否已登录...")
                    
                    # 等待一下让页面完全加载
                    time.sleep(2)
                    
                    # 检查是否已登录（只有过了滑块才会有这个元素）
                    login_success, active_page, _ = self._probe_context_login_success(context, page)
                    if login_success:
                        logger.success(f"【{self.pure_user_id}】✅ 检测到已登录状态")

                        # 🔧 刷新模式下验证 session 是否真的有效
                        # 注入旧 Cookie 可能让前端显示"已登录"，但服务端 session 已过期
                        if effective_clean_context:
                            logger.info(f"【{self.pure_user_id}】刷新模式：验证服务端Session是否有效...")
                            try:
                                verify_page = context.new_page()
                                verify_resp = verify_page.goto(
                                    "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/?jsv=2.7.2&appKey=34839810&type=originaljson&dataType=json&v=1.0&api=mtop.taobao.idlemessage.pc.login.token&sessionOption=AutoLoginOnly",
                                    wait_until="domcontentloaded",
                                    timeout=10000
                                )
                                verify_text = verify_page.content()
                                verify_page.close()

                                if "FAIL_SYS_SESSION_EXPIRED" in verify_text or "FAIL_SYS_USER_VALIDATE" in verify_text:
                                    logger.warning(
                                        f"【{self.pure_user_id}】服务端Session已过期，"
                                        f"前端登录状态为假象，需要重新账密登录"
                                    )
                                    page, login_frame, found_login_form, matched_selector, reopened_fresh_page = (
                                        self._prepare_login_page_after_cleanup(
                                            context,
                                            page,
                                            clear_storage=True,
                                            reopen_fresh_page=True,
                                            timeout_seconds=8.0,
                                        )
                                    )
                                    if not found_login_form:
                                        logger.error(f"【{self.pure_user_id}】清理会话状态后仍未找到登录表单")
                                        return self._fail_login("Session过期且清理会话状态后未找到登录表单")
                                    if reopened_fresh_page:
                                        logger.info(f"【{self.pure_user_id}】已切换到新页面继续账密登录")
                                    # 跳出当前分支，继续走下面的账密输入流程
                                else:
                                    logger.info(f"【{self.pure_user_id}】✅ 服务端Session验证通过，Cookie有效")
                                    return self._finalize_logged_in_cookies(
                                        context,
                                        active_page or page,
                                        scene="无 iframe 已登录场景(Session已验证)",
                                        notification_callback=notification_callback,
                                        notification_scene=notification_scene,
                                    )
                            except Exception as verify_e:
                                logger.warning(f"【{self.pure_user_id}】Session验证异常: {verify_e}，按Session过期处理")
                                page, login_frame, found_login_form, matched_selector, reopened_fresh_page = (
                                    self._prepare_login_page_after_cleanup(
                                        context,
                                        page,
                                        clear_storage=True,
                                        reopen_fresh_page=True,
                                        timeout_seconds=8.0,
                                    )
                                )
                                if not found_login_form:
                                    return self._fail_login("Session验证异常且清理会话状态后未找到登录表单")
                                if reopened_fresh_page:
                                    logger.info(f"【{self.pure_user_id}】Session异常后已切换到新页面继续账密登录")
                        else:
                            # 非刷新模式，直接返回Cookie
                            return self._finalize_logged_in_cookies(
                                context,
                                active_page or page,
                                scene="无 iframe 已登录场景",
                                notification_callback=notification_callback,
                                notification_scene=notification_scene,
                            )
                    else:
                        # 持久化上下文可能因浏览器缓存导致页面处于"半登录"状态
                        # 既没有登录 iframe，也没有已登录元素
                        if not effective_clean_context:
                            logger.warning(
                                f"【{self.pure_user_id}】持久化上下文页面状态异常（无iframe、无已登录态），"
                                f"清除Cookie和缓存后重新加载..."
                            )
                            page, login_frame, found_login_form, matched_selector, _ = (
                                self._prepare_login_page_after_cleanup(
                                    context,
                                    page,
                                    clear_storage=True,
                                    reopen_fresh_page=False,
                                    timeout_seconds=8.0,
                                )
                            )

                            if not found_login_form:
                                logger.error(f"【{self.pure_user_id}】❌ 清除缓存后仍未找到登录表单")
                                return self._fail_login("持久化上下文清除缓存后仍未找到登录表单")
                            logger.info(f"【{self.pure_user_id}】✓ 清除缓存后找到登录表单: {matched_selector}")
                            # found_login_form=True → 继续走下面的账密输入流程
                        else:
                            logger.error(f"【{self.pure_user_id}】❌ 未找到登录表单且未检测到已登录")
                            return self._fail_login("未找到登录表单且未检测到已登录状态")
                
                # 点击密码登录标签
                logger.info(f"【{self.pure_user_id}】查找密码登录标签...")
                try:
                    password_tab, password_tab_selector = self._query_first_visible(
                        login_frame,
                        login_selectors['tab'],
                    )
                    if password_tab:
                        logger.info(f"【{self.pure_user_id}】✓ 找到密码登录标签，点击中: {password_tab_selector}")
                        password_tab.click()
                        time.sleep(1.5)
                    else:
                        logger.info(f"【{self.pure_user_id}】未找到密码登录标签，可能默认已处于密码登录模式")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】查找密码登录标签失败: {e}")
                
                # 输入账号
                logger.info(f"【{self.pure_user_id}】输入账号: {account}")
                time.sleep(1)
                
                account_input, account_selector = self._query_first_visible(
                    login_frame,
                    login_selectors['account'],
                )
                if account_input:
                    logger.info(f"【{self.pure_user_id}】✓ 找到账号输入框: {account_selector}")
                    account_input.fill(account)
                    logger.info(f"【{self.pure_user_id}】✓ 账号已输入")
                    time.sleep(random.uniform(0.5, 1.0))
                else:
                    handled, recovery_result = self._recover_from_missing_login_inputs(
                        context,
                        page,
                        missing_field='账号输入框',
                        notification_callback=notification_callback,
                        notification_scene=notification_scene,
                    )
                    if handled:
                        return recovery_result
                    logger.error(f"【{self.pure_user_id}】✗ 未找到账号输入框")
                    return self._fail_login("未找到账号输入框")
                
                # 输入密码
                logger.info(f"【{self.pure_user_id}】输入密码...")
                password_input, password_selector = self._query_first_visible(
                    login_frame,
                    login_selectors['password'],
                )
                if password_input:
                    logger.info(f"【{self.pure_user_id}】✓ 找到密码输入框: {password_selector}")
                    password_input.fill(password)
                    logger.info(f"【{self.pure_user_id}】✓ 密码已输入")
                    time.sleep(random.uniform(0.5, 1.0))
                else:
                    handled, recovery_result = self._recover_from_missing_login_inputs(
                        context,
                        page,
                        missing_field='密码输入框',
                        notification_callback=notification_callback,
                        notification_scene=notification_scene,
                    )
                    if handled:
                        return recovery_result
                    logger.error(f"【{self.pure_user_id}】✗ 未找到密码输入框")
                    return self._fail_login("未找到密码输入框")
                
                # 勾选用户协议
                logger.info(f"【{self.pure_user_id}】查找并勾选用户协议...")
                try:
                    agreement_checkbox, agreement_selector = self._query_first_visible(
                        login_frame,
                        login_selectors['agreement'],
                    )
                    if agreement_checkbox:
                        is_checked = agreement_checkbox.evaluate('el => el.checked')
                        if not is_checked:
                            agreement_checkbox.click()
                            time.sleep(0.3)
                            logger.info(f"【{self.pure_user_id}】✓ 用户协议已勾选: {agreement_selector}")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】勾选用户协议失败: {e}")
                
                # 点击登录按钮
                logger.info(f"【{self.pure_user_id}】点击登录按钮...")
                time.sleep(1)
                
                login_button, login_button_selector = self._query_first_visible(
                    login_frame,
                    login_selectors['submit'],
                )
                if login_button:
                    logger.info(f"【{self.pure_user_id}】✓ 找到登录按钮: {login_button_selector}")
                    login_button.click()
                    logger.info(f"【{self.pure_user_id}】✓ 登录按钮已点击")
                else:
                    logger.warning(f"【{self.pure_user_id}】未找到登录按钮，尝试回车提交")
                    try:
                        password_input.press('Enter')
                        logger.info(f"【{self.pure_user_id}】✓ 已通过回车提交登录")
                    except Exception:
                        logger.error(f"【{self.pure_user_id}】✗ 未找到登录按钮且回车提交失败")
                        return self._fail_login("未找到登录按钮")
                
                # 【关键】点击登录后，等待一下再检测滑块
                logger.info(f"【{self.pure_user_id}】========== 登录后监控 ==========")
                logger.info(f"【{self.pure_user_id}】等待页面响应...")
                time.sleep(3)
                
                # 【核心】检测是否有滑块验证 → 如果有，调用 solve_slider() 处理
                logger.info(f"【{self.pure_user_id}】检测是否有滑块验证...")
                
                # 先将page和context保存到实例变量（供solve_slider使用）
                original_page = self.page
                original_context = self.context
                original_browser = self.browser
                original_playwright = self.playwright
                
                self.page = page
                self.context = context
                self.browser = browser
                self.playwright = playwright
                
                try:
                    # 检查页面内容是否包含滑块相关元素
                    page_content = page.content()
                    has_slider = False

                    # 检测滑块元素
                    slider_selectors = [
                        '#nc_1_n1z',
                        '.nc-container',
                        '.nc_scale',
                        '.nc-wrapper'
                    ]

                    # 在主页面和所有 iframe 中查找滑块（阿里系滑块常嵌在 iframe 中）
                    search_frames = [page]
                    try:
                        for frame in page.frames:
                            if frame != page.main_frame:
                                search_frames.append(frame)
                    except Exception:
                        pass

                    for search_frame in search_frames:
                        if has_slider:
                            break
                        for selector in slider_selectors:
                            try:
                                element = search_frame.query_selector(selector)
                                if element and element.is_visible():
                                    logger.info(f"【{self.pure_user_id}】✅ 检测到滑块验证元素: {selector} (frame: {getattr(search_frame, 'url', 'main')[:80]})")
                                    has_slider = True
                                    break
                            except:
                                continue
                    
                    if has_slider:
                        logger.warning(f"【{self.pure_user_id}】检测到滑块验证，开始处理...")
                        slider_risk_log = self._start_password_login_slider_risk_log(
                            verification_url=(getattr(search_frame, 'url', None) if 'search_frame' in locals() else getattr(page, 'url', None)),
                            detection_phase='post_login_monitor',
                        )

                        # 【复用】直接调用 solve_slider() 方法处理滑块
                        slider_success = self.solve_slider(max_retries=self.slider_max_retries)

                        if slider_success:
                            logger.success(f"【{self.pure_user_id}】✅ 滑块验证成功！")
                            self._finish_password_login_slider_risk_log(
                                slider_risk_log,
                                success=True,
                                verification_url=(getattr(search_frame, 'url', None) if 'search_frame' in locals() else getattr(page, 'url', None)),
                                processing_result='密码登录流程中的滑块验证自动处理成功',
                                extra_meta={'detection_source': 'login_with_password_playwright_post_login'},
                            )
                        else:
                            logger.error(f"【{self.pure_user_id}】❌ 滑块验证{self.slider_max_retries}次均失败")
                            self._finish_password_login_slider_risk_log(
                                slider_risk_log,
                                success=False,
                                verification_url=(getattr(search_frame, 'url', None) if 'search_frame' in locals() else getattr(page, 'url', None)),
                                error_message=self._get_slider_failure_message("滑块验证失败，请稍后重试"),
                                extra_meta={'detection_source': 'login_with_password_playwright_post_login'},
                            )
                            fallback_page = locals().get('active_page') or page
                            monitor_page = self._select_monitor_page(context, fallback_page) or fallback_page
                            qr_handoff_frame = None
                            qr_markers = ('扫码', '扫一扫', '安全登录', '二维码')
                            qr_selectors = (
                                'img[src*=\"qrcode\"]',
                                'canvas[class*=\"qrcode\"]',
                                '.qr-code',
                                '#qr-code',
                                '[class*=\"qr-code\"]',
                                '[id*=\"qr-code\"]',
                            )
                            for qr_candidate in [monitor_page] + list(monitor_page.frames):
                                try:
                                    frame_text = qr_candidate.text_content('body') or ''
                                except Exception:
                                    frame_text = ''
                                marker_hit = any(marker in frame_text for marker in qr_markers)
                                selector_hit = False
                                for qr_selector in qr_selectors:
                                    try:
                                        qr_element = qr_candidate.query_selector(qr_selector)
                                        if qr_element and qr_element.is_visible():
                                            selector_hit = True
                                            break
                                    except Exception:
                                        continue
                                if marker_hit or selector_hit:
                                    qr_handoff_frame = qr_candidate
                                    break
                            if qr_handoff_frame is not None:
                                screenshot_path = self._capture_verification_screenshot(
                                    monitor_page,
                                    frame=(None if qr_handoff_frame == monitor_page else qr_handoff_frame),
                                )
                                qr_frame = VerificationFrameWrapper(
                                    qr_handoff_frame,
                                    verification_type='qr_verify',
                                    verify_url=(
                                        qr_handoff_frame.url
                                        if hasattr(qr_handoff_frame, 'url')
                                        else getattr(monitor_page, 'url', None)
                                    ),
                                    screenshot_path=screenshot_path,
                                )
                                logger.warning(
                                    f"【{self.pure_user_id}】滑块多次失败后检测到可扫码验证，转为二维码验证接管"
                                )
                                self._finish_password_login_slider_risk_log(
                                    slider_risk_log,
                                    success=False,
                                    verification_url=(getattr(search_frame, 'url', None) if 'search_frame' in locals() else getattr(page, 'url', None)),
                                    processing_result='滑块失败后检测到可扫码验证，已转交二维码验证流程',
                                    extra_meta={'detection_source': 'login_with_password_playwright_post_login_qr_handoff'},
                                )
                                return self._process_verification_requirement(
                                    context,
                                    monitor_page,
                                    qr_frame,
                                    notification_callback,
                                    '账号密码登录',
                                )
                            return self._fail_login(self._get_slider_failure_message("滑块验证失败，请稍后重试"))
                    else:
                        logger.info(f"【{self.pure_user_id}】未检测到滑块验证")
                    
                    # 等待登录完成
                    logger.info(f"【{self.pure_user_id}】等待登录完成...")
                    time.sleep(5)
                    
                    # 再次检查是否有滑块验证（可能在等待过程中出现）
                    logger.info(f"【{self.pure_user_id}】等待1秒后检查是否有滑块验证...")
                    time.sleep(1)
                    has_slider_after_wait = False
                    for search_frame in search_frames:
                        if has_slider_after_wait:
                            break
                        for selector in slider_selectors:
                            try:
                                element = search_frame.query_selector(selector)
                                if element and element.is_visible():
                                    logger.info(f"【{self.pure_user_id}】✅ 等待后检测到滑块验证元素: {selector}")
                                    has_slider_after_wait = True
                                    break
                            except:
                                continue

                    active_page = locals().get('active_page') or page
                    if has_slider_after_wait:
                        logger.warning(f"【{self.pure_user_id}】检测到滑块验证，开始处理...")
                        wait_slider_risk_log = self._start_password_login_slider_risk_log(
                            verification_url=getattr(active_page or page, 'url', None),
                            detection_phase='post_wait_monitor',
                        )
                        slider_success = self.solve_slider(max_retries=self.slider_max_retries)
                        if slider_success:
                            logger.success(f"【{self.pure_user_id}】✅ 滑块验证成功！")
                            self._finish_password_login_slider_risk_log(
                                wait_slider_risk_log,
                                success=True,
                                verification_url=getattr(active_page or page, 'url', None),
                                processing_result='密码登录流程中的滑块验证自动处理成功（等待后）',
                                extra_meta={'detection_source': 'login_with_password_playwright_post_wait'},
                            )
                            time.sleep(3)  # 等待滑块验证后的状态更新
                        else:
                            logger.error(f"【{self.pure_user_id}】❌ 滑块验证3次均失败")
                            self._finish_password_login_slider_risk_log(
                                wait_slider_risk_log,
                                success=False,
                                verification_url=getattr(active_page or page, 'url', None),
                                error_message=self._get_slider_failure_message("滑块验证失败，请稍后重试"),
                                extra_meta={'detection_source': 'login_with_password_playwright_post_wait'},
                            )
                            return self._fail_login(self._get_slider_failure_message("滑块验证失败，请稍后重试"))
                    
                    # 检查登录状态
                    logger.info(f"【{self.pure_user_id}】等待1秒后检查登录状态...")
                    time.sleep(1)
                    login_success, active_page, _ = self._probe_context_login_success(context, page)
                    
                    if login_success:
                        monitor_page = self._select_monitor_page(context, active_page or page)
                        has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                        if has_qr:
                            logger.warning(f"【{self.pure_user_id}】虽然页面元素判定已登录，但当前仍存在身份验证页，转入验证等待流程")
                            return self._process_verification_requirement(
                                context,
                                monitor_page,
                                qr_frame,
                                notification_callback,
                                notification_scene,
                            )
                        logger.success(f"【{self.pure_user_id}】✅ 登录验证成功！")
                    else:
                        # 检查是否有账密错误
                        logger.info(f"【{self.pure_user_id}】等待1秒后检查是否有账密错误...")
                        time.sleep(1)
                        monitor_page = self._select_monitor_page(context, active_page or page)
                        has_error, error_message = self._check_login_error(monitor_page)
                        if has_error:
                            logger.error(f"【{self.pure_user_id}】❌ 登录失败：{error_message}")
                            # 抛出异常，包含错误消息，让调用者能够获取
                            raise Exception(error_message if error_message else "登录失败，请检查账号密码是否正确")
                        
                        # 【重要】检测是否需要二维码/人脸验证（排除滑块验证）
                        # 注意：_detect_qr_code_verification 如果检测到滑块，会立即处理滑块
                        logger.info(f"【{self.pure_user_id}】等待1秒后检测是否需要二维码/人脸验证...")
                        time.sleep(1)
                        logger.info(f"【{self.pure_user_id}】检测是否需要二维码/人脸验证...")
                        monitor_page = self._select_monitor_page(context, active_page or page)
                        has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                        
                        # 如果检测到滑块并已处理，再次检查登录状态
                        if not has_qr:
                            # 滑块可能已被处理，再次检查登录状态
                            logger.info(f"【{self.pure_user_id}】等待1秒后再次检查登录状态...")
                            time.sleep(1)
                            login_success_after_slider, active_page, _ = self._probe_context_login_success(context, monitor_page)
                            if login_success_after_slider:
                                logger.success(f"【{self.pure_user_id}】✅ 滑块验证后，登录验证成功！")
                                login_success = True
                            else:
                                # 滑块验证后仍未登录成功，继续检测二维码/人脸验证（此时应该不会再检测到滑块）
                                logger.info(f"【{self.pure_user_id}】等待1秒后继续检测是否需要二维码/人脸验证...")
                                time.sleep(1)
                                logger.info(f"【{self.pure_user_id}】滑块验证后，继续检测是否需要二维码/人脸验证...")
                                monitor_page = self._select_monitor_page(context, active_page or monitor_page)
                                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                        
                        if has_qr:
                            return self._process_verification_requirement(
                                context,
                                monitor_page,
                                qr_frame,
                                notification_callback,
                                notification_scene,
                            )
                        else:
                            logger.info(f"【{self.pure_user_id}】未检测到二维码/人脸验证")
                            # 再次检查登录状态，确保登录成功
                            logger.info(f"【{self.pure_user_id}】等待1秒后再次检查登录状态...")
                            time.sleep(1)
                            login_success, active_page, _ = self._probe_context_login_success(context, active_page or page)
                            if not login_success:
                                logger.error(f"【{self.pure_user_id}】❌ 登录状态未确认，无法获取Cookie")
                                return self._fail_login("登录状态未确认，无法获取Cookie")
                            else:
                                logger.success(f"【{self.pure_user_id}】✅ 登录状态已确认")
                    
                    # 【重要】只有在 login_success = True 的情况下，才获取Cookie
                    if not login_success:
                        logger.error(f"【{self.pure_user_id}】❌ 登录未成功，无法获取Cookie")
                        return self._fail_login("登录未成功，无法获取Cookie")
                    
                    # 获取Cookie
                    logger.info(f"【{self.pure_user_id}】等待1秒后获取Cookie...")
                    time.sleep(1)
                    try:
                        cookies_result = self._finalize_logged_in_cookies(
                            context,
                            active_page or page,
                            scene="密码登录完成后",
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                            extra_cookie_updates=observed_set_cookie_updates or None,
                        )
                        if cookies_result:
                            logger.success("✅ 登录成功！Cookie有效")
                        return cookies_result
                    except Exception as e:
                        logger.error(f"【{self.pure_user_id}】获取Cookie失败: {e}")
                        return self._fail_login("获取Cookie失败")
                
                finally:
                    # 恢复原始值
                    self.page = original_page
                    self.context = original_context
                    self.browser = original_browser
                    self.playwright = original_playwright
            
            finally:
                # 关闭浏览器。这里不能无限阻塞，否则上层会话会一直卡在 processing。
                try:
                    close_errors = []

                    def _close_runtime_resources():
                        try:
                            if context:
                                context.close()
                        except Exception as close_context_err:
                            close_errors.append(f"context.close: {close_context_err}")

                        if effective_clean_context and browser:
                            try:
                                browser.close()
                            except Exception as close_browser_err:
                                close_errors.append(f"browser.close: {close_browser_err}")

                        try:
                            if playwright:
                                playwright.stop()
                        except Exception as stop_playwright_err:
                            close_errors.append(f"playwright.stop: {stop_playwright_err}")

                    close_thread = threading.Thread(
                        target=_close_runtime_resources,
                        name=f"pwd-login-close-{self.pure_user_id}",
                        daemon=True,
                    )
                    close_thread.start()
                    close_thread.join(timeout=8)

                    if close_thread.is_alive():
                        logger.warning(f"【{self.pure_user_id}】关闭浏览器超时，改为后台继续清理，避免阻塞密码登录会话收尾")
                        self._force_kill_browser_process_tree("password_login_close_timeout")
                    elif close_errors:
                        logger.warning(f"【{self.pure_user_id}】关闭浏览器时出现异常: {close_errors}")
                        self._force_kill_browser_process_tree("password_login_close_error")
                    elif effective_clean_context:
                        logger.info(f"【{self.pure_user_id}】浏览器已关闭，干净上下文已销毁")
                    else:
                        logger.info(f"【{self.pure_user_id}】浏览器已关闭，缓存已保存")
                        self._browser_pid = None
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】关闭浏览器时出错: {e}")

                # 释放并发槽位（防止槽位泄漏导致后续任务永远等待）
                try:
                    self._release_concurrency_slot("密码登录结束")
                except Exception as e:
                    logger.warning(f"【{self.pure_user_id}】释放并发槽位时出错: {e}")
        
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】密码登录流程异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            error_message = str(e)
            if self._is_profile_in_use_launch_error(e):
                return self._fail_login("浏览器用户目录正被其他登录流程占用，请稍后重试")
            if "Target page, context or browser has been closed" in error_message:
                return self._fail_login("页面会话已失效，请重新尝试刷新Cookie")
            return self._fail_login(error_message if error_message else "密码登录流程异常")
        finally:
            self._slider_refresh_mode = previous_slider_refresh_mode
            self._password_slider_runtime_hardened = False
            self.risk_trigger_scene = previous_risk_trigger_scene
            # 最外层 finally：确保任何退出路径都释放并发槽位
            try:
                self._release_concurrency_slot("密码登录finally兜底")
            except Exception:
                pass
    
    def login_with_password_headful(self, account: str = None, password: str = None, show_browser: bool = False):
        """通过浏览器进行密码登录并获取Cookie (使用DrissionPage)
        
        Args:
            account: 登录账号（必填）
            password: 登录密码（必填）
            show_browser: 是否显示浏览器窗口（默认False为无头模式）
                         True: 有头模式，登录后等待5分钟（可手动处理验证码）
                         False: 无头模式，登录后等待10秒
            
        Returns:
            dict: 获取到的cookie字典，失败返回None
        """
        page = None
        try:
            # 检查日期有效性
            if not self._check_date_validity():
                logger.error(f"【{self.pure_user_id}】日期验证失败，无法执行登录")
                return None
            
            # 验证必需参数
            if not account or not password:
                logger.error(f"【{self.pure_user_id}】账号或密码不能为空")
                return None
            
            browser_mode = "有头" if show_browser else "无头"
            logger.info(f"【{self.pure_user_id}】开始{browser_mode}模式密码登录流程（使用DrissionPage）...")
            
            # 导入 DrissionPage
            try:
                from DrissionPage import ChromiumPage, ChromiumOptions
                logger.info(f"【{self.pure_user_id}】DrissionPage导入成功")
            except ImportError:
                logger.error(f"【{self.pure_user_id}】DrissionPage未安装，请执行: pip install DrissionPage")
                return None
            
            # 配置浏览器选项
            logger.info(f"【{self.pure_user_id}】配置浏览器选项（{browser_mode}模式）...")
            co = ChromiumOptions()
            
            # 根据 show_browser 参数决定是否启用无头模式
            if not show_browser:
                co.headless()
                logger.info(f"【{self.pure_user_id}】已启用无头模式")
            else:
                logger.info(f"【{self.pure_user_id}】已启用有头模式（浏览器可见）")
            
            # 设置浏览器参数（反检测）
            co.set_argument('--no-sandbox')
            co.set_argument('--disable-setuid-sandbox')
            co.set_argument('--disable-dev-shm-usage')
            co.set_argument('--disable-blink-features=AutomationControlled')
            co.set_argument('--disable-infobars')
            co.set_argument('--disable-extensions')
            co.set_argument('--disable-popup-blocking')
            co.set_argument('--disable-notifications')
            
            # 无头模式需要的额外参数
            if not show_browser:
                co.set_argument('--disable-gpu')
                co.set_argument('--disable-software-rasterizer')
            else:
                # 有头模式窗口最大化
                co.set_argument('--start-maximized')
            
            # 设置用户代理
            browser_features = self._get_random_browser_features()
            co.set_user_agent(browser_features['user_agent'])
            
            # 设置中文语言
            co.set_argument('--lang=zh-CN')
            logger.info(f"【{self.pure_user_id}】已设置浏览器语言为中文（zh-CN）")
            
            # 禁用自动化特征检测
            co.set_pref('excludeSwitches', ['enable-automation'])
            co.set_pref('useAutomationExtension', False)
            
            # 创建浏览器页面，添加重试机制
            logger.info(f"【{self.pure_user_id}】启动DrissionPage浏览器（{browser_mode}模式）...")
            max_retries = 3
            retry_count = 0
            page = None
            
            while retry_count < max_retries and page is None:
                try:
                    if retry_count > 0:
                        logger.info(f"【{self.pure_user_id}】第 {retry_count + 1} 次尝试启动浏览器...")
                        time.sleep(2)  # 等待2秒后重试
                    
                    page = ChromiumPage(addr_or_opts=co)
                    logger.info(f"【{self.pure_user_id}】浏览器已成功启动（{browser_mode}模式）")
                    break
                    
                except Exception as browser_error:
                    retry_count += 1
                    logger.warning(f"【{self.pure_user_id}】浏览器启动失败 (尝试 {retry_count}/{max_retries}): {str(browser_error)}")
                    
                    if retry_count >= max_retries:
                        logger.error(f"【{self.pure_user_id}】浏览器启动失败，已达到最大重试次数")
                        logger.error(f"【{self.pure_user_id}】可能的原因：")
                        logger.error(f"【{self.pure_user_id}】1. Chrome/Chromium 浏览器未正确安装或路径不正确")
                        logger.error(f"【{self.pure_user_id}】2. 远程调试端口被占用，请关闭其他Chrome实例")
                        logger.error(f"【{self.pure_user_id}】3. 系统资源不足")
                        logger.error(f"【{self.pure_user_id}】建议：")
                        logger.error(f"【{self.pure_user_id}】- 检查Chrome浏览器是否已安装")
                        logger.error(f"【{self.pure_user_id}】- 关闭所有Chrome浏览器窗口后重试")
                        logger.error(f"【{self.pure_user_id}】- 检查任务管理器中是否有残留的chrome.exe进程")
                        raise
                    
                    # 尝试清理可能残留的Chrome进程
                    try:
                        import subprocess
                        import platform
                        if platform.system() == 'Windows':
                            subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'], 
                                         capture_output=True, timeout=5)
                            logger.info(f"【{self.pure_user_id}】已尝试清理残留Chrome进程")
                    except Exception as cleanup_error:
                        logger.debug(f"【{self.pure_user_id}】清理进程时出错: {cleanup_error}")
            
            if page is None:
                logger.error(f"【{self.pure_user_id}】无法启动浏览器")
                return None
            
            # 访问登录页面
            target_url = "https://www.goofish.com/im"
            logger.info(f"【{self.pure_user_id}】访问登录页面: {target_url}")
            page.get(target_url)
            
            # 等待页面加载
            logger.info(f"【{self.pure_user_id}】等待页面加载...")
            time.sleep(5)
            
            # 检查页面状态
            logger.info(f"【{self.pure_user_id}】========== 页面诊断信息 ==========")
            current_url = page.url
            logger.info(f"【{self.pure_user_id}】当前URL: {current_url}")
            page_title = page.title
            logger.info(f"【{self.pure_user_id}】页面标题: {page_title}")
            
            
            logger.info(f"【{self.pure_user_id}】====================================")
            
            # 查找并点击密码登录标签
            logger.info(f"【{self.pure_user_id}】查找密码登录标签...")
            password_tab_selectors = [
                '.password-login-tab-item',
                'text:密码登录',
                'text:账号密码登录',
            ]
            
            password_tab_found = False
            for selector in password_tab_selectors:
                try:
                    tab = page.ele(selector, timeout=3)
                    if tab:
                        logger.info(f"【{self.pure_user_id}】找到密码登录标签: {selector}")
                        tab.click()
                        logger.info(f"【{self.pure_user_id}】密码登录标签已点击")
                        time.sleep(2)
                        password_tab_found = True
                        break
                except:
                    continue
            
            if not password_tab_found:
                logger.warning(f"【{self.pure_user_id}】未找到密码登录标签，可能页面默认就是密码登录模式")
            
            # 查找登录表单
            logger.info(f"【{self.pure_user_id}】开始检测登录表单...")
            username_selectors = [
                '#fm-login-id',
                'input:name=fm-login-id',
                'input:placeholder^=手机',
                'input:placeholder^=账号',
                'input:type=text',
                '#TPL_username_1',
            ]
            
            login_input = None
            for selector in username_selectors:
                try:
                    login_input = page.ele(selector, timeout=2)
                    if login_input:
                        logger.info(f"【{self.pure_user_id}】找到登录表单: {selector}")
                        break
                except:
                    continue
            
            if not login_input:
                logger.error(f"【{self.pure_user_id}】未找到登录表单")
                return None
            
            # 输入账号
            logger.info(f"【{self.pure_user_id}】输入账号: {account}")
            try:
                login_input.click()
                time.sleep(0.5)
                login_input.input(account)
                logger.info(f"【{self.pure_user_id}】账号已输入")
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】输入账号失败: {str(e)}")
                return None
            
            # 输入密码
            logger.info(f"【{self.pure_user_id}】输入密码...")
            password_selectors = [
                '#fm-login-password',
                'input:name=fm-login-password',
                'input:type=password',
                'input:placeholder^=密码',
                '#TPL_password_1',
            ]
            
            password_input = None
            for selector in password_selectors:
                try:
                    password_input = page.ele(selector, timeout=2)
                    if password_input:
                        logger.info(f"【{self.pure_user_id}】找到密码输入框: {selector}")
                        break
                except:
                    continue
            
            if not password_input:
                logger.error(f"【{self.pure_user_id}】未找到密码输入框")
                return None
            
            try:
                password_input.click()
                time.sleep(0.5)
                password_input.input(password)
                logger.info(f"【{self.pure_user_id}】密码已输入")
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"【{self.pure_user_id}】输入密码失败: {str(e)}")
                return None
            
            # 勾选协议（可选）
            logger.info(f"【{self.pure_user_id}】查找并勾选用户协议...")
            agreement_selectors = [
                '#fm-agreement-checkbox',
                'input:type=checkbox',
            ]
            
            for selector in agreement_selectors:
                try:
                    checkbox = page.ele(selector, timeout=1)
                    if checkbox and not checkbox.states.is_checked:
                        checkbox.click()
                        logger.info(f"【{self.pure_user_id}】用户协议已勾选")
                        time.sleep(0.5)
                        break
                except:
                    continue
            
            # 点击登录按钮
            logger.info(f"【{self.pure_user_id}】点击登录按钮...")
            login_button_selectors = [
                '@class=fm-button fm-submit password-login ',
                '.fm-button.fm-submit.password-login',
                'button.password-login',
                '.password-login',
                'button.fm-submit',
                'text:登录',
            ]
            
            login_button_found = False
            for selector in login_button_selectors:
                try:
                    button = page.ele(selector, timeout=2)
                    if button:
                        logger.info(f"【{self.pure_user_id}】找到登录按钮: {selector}")
                        button.click()
                        logger.info(f"【{self.pure_user_id}】登录按钮已点击")
                        login_button_found = True
                        break
                except:
                    continue
            
            if not login_button_found:
                logger.warning(f"【{self.pure_user_id}】未找到登录按钮，尝试按Enter键...")
                try:
                    password_input.input('\n')  # 模拟按Enter
                    logger.info(f"【{self.pure_user_id}】已按Enter键")
                except Exception as e:
                    logger.error(f"【{self.pure_user_id}】按Enter键失败: {str(e)}")
            
            # 等待登录完成
            logger.info(f"【{self.pure_user_id}】等待登录完成...")
            time.sleep(5)
            
            # 检查当前URL和标题
            current_url = page.url
            logger.info(f"【{self.pure_user_id}】登录后URL: {current_url}")
            page_title = page.title
            logger.info(f"【{self.pure_user_id}】登录后页面标题: {page_title}")
            
            # 根据浏览器模式决定等待时间
            # 有头模式：等待5分钟（用户可能需要手动处理验证码等）
            # 无头模式：等待10秒
            if show_browser:
                wait_seconds = 300  # 5分钟
                logger.info(f"【{self.pure_user_id}】有头模式：等待5分钟让Cookie完全生成（期间可手动处理验证码等）...")
            else:
                wait_seconds = 10
                logger.info(f"【{self.pure_user_id}】无头模式：等待10秒让Cookie完全生成...")
            
            time.sleep(wait_seconds)
            logger.info(f"【{self.pure_user_id}】等待完成，准备获取Cookie")
            
            # 获取Cookie
            logger.info(f"【{self.pure_user_id}】开始获取Cookie...")
            cookies_raw = page.cookies()
            
            # 将cookies转换为字典格式
            cookies = {}
            if isinstance(cookies_raw, list):
                # 如果返回的是列表格式，转换为字典
                for cookie in cookies_raw:
                    if isinstance(cookie, dict) and 'name' in cookie and 'value' in cookie:
                        cookies[cookie['name']] = cookie['value']
                    elif isinstance(cookie, tuple) and len(cookie) >= 2:
                        cookies[cookie[0]] = cookie[1]
            elif isinstance(cookies_raw, dict):
                # 如果已经是字典格式，直接使用
                cookies = cookies_raw
            
            if cookies:
                logger.info(f"【{self.pure_user_id}】成功获取 {len(cookies)} 个Cookie")
                logger.info(f"【{self.pure_user_id}】Cookie名称列表: {list(cookies.keys())}")
                
                # 打印完整的Cookie
                logger.info(f"【{self.pure_user_id}】完整Cookie内容:")
                for name, value in cookies.items():
                    # 对长cookie值进行截断显示
                    if len(value) > 50:
                        display_value = f"{value[:25]}...{value[-25:]}"
                    else:
                        display_value = value
                    logger.info(f"【{self.pure_user_id}】  {name} = {display_value}")
                
                # 将cookie转换为字符串格式
                cookie_str = '; '.join([f"{k}={v}" for k, v in cookies.items()])
                logger.info(f"【{self.pure_user_id}】Cookie字符串格式: {cookie_str[:200]}..." if len(cookie_str) > 200 else f"【{self.pure_user_id}】Cookie字符串格式: {cookie_str}")
                
                logger.info(f"【{self.pure_user_id}】登录成功，准备关闭浏览器")
                
                return cookies
            else:
                logger.error(f"【{self.pure_user_id}】未获取到任何Cookie")
                return None
                
        except Exception as e:
            logger.error(f"【{self.pure_user_id}】密码登录流程出错: {str(e)}")
            import traceback
            logger.error(f"【{self.pure_user_id}】详细错误信息: {traceback.format_exc()}")
            return None
        finally:
            # 关闭浏览器
            logger.info(f"【{self.pure_user_id}】关闭浏览器...")
            try:
                if page:
                    page.quit()
                    logger.info(f"【{self.pure_user_id}】DrissionPage浏览器已关闭")
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】关闭浏览器时出错: {e}")
    
    def run(
        self,
        url: str,
        notification_callback: Optional[Callable] = None,
        notification_scene: str = '手动导入 Cookie',
    ):
        """运行主流程，返回(成功状态, cookie数据)"""
        cookies = None
        # 每次 run() 进入都先清空内层自救兜底标记，避免上次状态残留
        self._post_recovery_success = False
        self._post_recovery_cookies = None
        try:
            # 检查日期有效性
            if not self._check_date_validity():
                logger.error(f"【{self.pure_user_id}】日期验证失败，无法执行")
                return False, None
            
            # 初始化浏览器
            self.init_browser()

            # 无头模式默认跳过额外预热，避免先访问其它页面把风控状态搞得更脏；
            # 如需回滚，可设置 XY_SLIDER_HEADLESS_WARMUP=1。
            if not (self.headless and self.disable_headless_warmup):
                self._warmup_slider_context(url)
            
            # 导航到目标URL，快速加载
            logger.info(f"【{self.pure_user_id}】导航到URL: {url}")
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning(f"【{self.pure_user_id}】页面加载异常，尝试继续: {str(e)}")
                # 如果页面加载失败，尝试等待一下
                time.sleep(2)

            self._captcha_page_entry_ts = time.time()
            
            # 短暂延迟，快速处理
            delay = random.uniform(0.3, 0.8)
            logger.info(f"【{self.pure_user_id}】等待页面加载: {delay:.2f}秒")
            time.sleep(delay)
            
            # 初始轻微鼠标移动，避免一打开就是静止死板页
            self.page.mouse.move(
                random.randint(520, 760),
                random.randint(280, 420),
                steps=random.randint(6, 16),
            )
            time.sleep(random.uniform(0.05, 0.12))
            
            # 检查页面标题
            page_title = self.page.title()
            logger.info(f"【{self.pure_user_id}】页面标题: {page_title}")
            
            # 检查页面内容
            page_content = self.page.content()
            if any(keyword in page_content for keyword in ["验证码", "captcha", "滑块", "slider"]):
                logger.info(f"【{self.pure_user_id}】页面内容包含验证码相关关键词")

                if self._is_hard_block_page(self.page):
                    self.last_verification_feedback = {
                        "status": "hard_block",
                        "source": "deny_page",
                        "message": "当前页面是阿里处罚页/反馈二维码页，不是真正可拖动的滑块",
                    }
                    logger.error(
                        f"【{self.pure_user_id}】当前命中的是处罚页/反馈二维码页，"
                        f"{'无头' if self.headless else '有头'}环境指纹已被风控拦截，当前页面不存在可操作滑块"
                    )
                    self._save_debug_snapshot("hard_block_page", self.page)
                    monitor_page = self._select_monitor_page(self.context, self.page) or self.page
                    has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                    if has_qr:
                        verification_result = self._process_verification_requirement(
                            self.context,
                            monitor_page,
                            qr_frame,
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                        )
                        if verification_result:
                            return True, verification_result
                    return False, None

                self._simulate_human_page_behavior()

                # 处理滑块验证
                success = self.solve_slider(max_retries=self.slider_max_retries)
                
                if success:
                    logger.info(f"【{self.pure_user_id}】滑块验证成功")

                    # 等待页面完全加载和跳转，让新的cookie生效（快速模式）
                    try:
                        logger.info(f"【{self.pure_user_id}】等待页面加载...")
                        time.sleep(1)  # 快速等待，从3秒减少到1秒

                        # 等待页面跳转或刷新
                        self.page.wait_for_load_state("networkidle", timeout=10000)
                        time.sleep(0.5)  # 快速确认，从2秒减少到0.5秒

                        logger.info(f"【{self.pure_user_id}】页面加载完成，开始获取cookie")
                    except Exception as e:
                        error_text = str(e)
                        logger.warning(f"【{self.pure_user_id}】等待页面加载时出错: {error_text}")
                        lowered_error = error_text.lower()
                        if "page crashed" in lowered_error or "target crashed" in lowered_error:
                            self.last_login_error = f"浏览器页面崩溃: {error_text}"
                            self.last_verification_feedback = {
                                "status": "error",
                                "source": "page_crashed",
                                "message": error_text,
                            }
                            self._save_debug_snapshot("page_crashed", getattr(self, "_detected_slider_frame", None))
                            return False, None

                    monitor_page = self._select_monitor_page(self.context, self.page) or self.page
                    has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                    if has_qr:
                        logger.warning(f"【{self.pure_user_id}】滑块通过后检测到身份验证页，转入验证等待流程")
                        verification_result = self._process_verification_requirement(
                            self.context,
                            monitor_page,
                            qr_frame,
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                        )
                        if verification_result:
                            return True, verification_result
                        return False, None

                    # 在关闭浏览器前获取cookie
                    try:
                        cookies = self._get_cookies_after_success()
                    except Exception as e:
                        logger.warning(f"【{self.pure_user_id}】获取cookie时出错: {str(e)}")
                else:
                    logger.warning(f"【{self.pure_user_id}】滑块验证失败")
                    monitor_page = self._select_monitor_page(self.context, self.page) or self.page
                    has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                    if has_qr:
                        logger.warning(f"【{self.pure_user_id}】滑块流程结束后检测到身份验证页，转入验证等待流程")
                        verification_result = self._process_verification_requirement(
                            self.context,
                            monitor_page,
                            qr_frame,
                            notification_callback=notification_callback,
                            notification_scene=notification_scene,
                        )
                        if verification_result:
                            return True, verification_result
                    # 兜底回流：_detect_qr_code_verification 内部 reload+solve_slider 自救成功时，
                    # 会把 cookies 写入 self._post_recovery_cookies 并标记 _post_recovery_success。
                    # 这里识别该信号并把 run() 主流程翻成成功，避免外层误以为失败而触发 600s 退避。
                    if self._post_recovery_success and self._post_recovery_cookies:
                        logger.success(
                            f"【{self.pure_user_id}】✅ 外层滑块判失败，但内层 _detect_qr_code_verification 自救成功，"
                            f"按 run() 成功收口"
                        )
                        return True, self._post_recovery_cookies
                    self._save_debug_snapshot("run_failed", getattr(self, "_detected_slider_frame", None))

                return success, cookies
            else:
                logger.info(f"【{self.pure_user_id}】页面内容不包含验证码相关关键词，可能不需要验证")
                monitor_page = self._select_monitor_page(self.context, self.page) or self.page
                has_qr, qr_frame = self._detect_qr_code_verification(monitor_page)
                if has_qr:
                    logger.warning(f"【{self.pure_user_id}】页面无滑块但存在身份验证页，转入验证等待流程")
                    verification_result = self._process_verification_requirement(
                        self.context,
                        monitor_page,
                        qr_frame,
                        notification_callback=notification_callback,
                        notification_scene=notification_scene,
                    )
                    if verification_result:
                        return True, verification_result
                    return False, None
                return True, None
                
        except Exception as e:
            error_text = str(e)
            logger.error(f"【{self.pure_user_id}】执行过程中出错: {error_text}")
            self.last_login_error = error_text
            self.last_verification_feedback = {
                "status": "error",
                "source": "exception",
                "message": error_text,
            }
            return False, None
        finally:
            # 关闭浏览器
            self.close_browser()

    async def async_run(self, url: str):
        """异步运行主流程，返回(成功状态, cookie数据)

        在独立线程中运行同步的 Playwright，避免事件循环冲突
        """
        import asyncio

        def _run_in_thread():
            """在独立线程中运行同步代码"""
            import asyncio
            # 确保线程中没有运行的事件循环
            try:
                loop = asyncio.get_running_loop()
                # 如果有运行中的循环，创建新循环
                asyncio.set_event_loop(asyncio.new_event_loop())
            except RuntimeError:
                # 没有运行中的循环，正常
                pass

            # 调用同步的 run 方法
            return self.run(url)

        # 使用 asyncio.to_thread 在独立线程中运行
        return await self._run_sync_method_on_fresh_thread(self.run, url)

    async def _run_sync_method_on_fresh_thread(self, func, *args, **kwargs):
        import asyncio
        import threading

        loop = asyncio.get_running_loop()
        result_future = loop.create_future()

        def _complete_result(value):
            if not result_future.done():
                result_future.set_result(value)

        def _complete_exception(exc: BaseException):
            if not result_future.done():
                result_future.set_exception(exc)

        def _worker():
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass

            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                loop.call_soon_threadsafe(_complete_exception, exc)
                return

            loop.call_soon_threadsafe(_complete_result, result)

        worker = threading.Thread(
            target=_worker,
            name=f"xianyu-slider-{self.pure_user_id}",
            daemon=True,
        )
        worker.start()
        return await result_future

    async def _async_close_browser(self):
        """异步版本的清理方法（兼容性保留，实际清理由同步 run 方法完成）"""
        # 由于 async_run 现在调用同步的 run 方法，清理工作已经在 run 的 finally 中完成
        pass

def get_slider_stats():
    """获取滑块验证并发统计信息"""
    return concurrency_manager.get_stats()

if __name__ == "__main__":
    # 简单的命令行示例
    import sys
    if len(sys.argv) < 2:
        print("用法: python xianyu_slider_stealth.py <URL>")
        sys.exit(1)
    
    url = sys.argv[1]
    # 第三个参数可以指定 headless 模式，默认为 True（无头）
    headless = sys.argv[2].lower() == 'true' if len(sys.argv) > 2 else True
    slider = XianyuSliderStealth("test_user", enable_learning=True, headless=headless)
    try:
        success, cookies = slider.run(url)
        print(f"验证结果: {'成功' if success else '失败'}")
        if cookies:
            print(f"获取到 {len(cookies)} 个cookies")
    except Exception as e:
        print(f"验证异常: {e}")
