"""
闲鱼订单详情获取工具
基于Playwright实现订单详情页面访问和数据提取
"""

import asyncio
import time
import sys
import os
from typing import Optional, Dict, Any, Tuple, List
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from loguru import logger
import re
import json
from threading import Lock
from collections import defaultdict

# 修复Docker环境中的asyncio事件循环策略问题
if sys.platform.startswith('linux') or os.getenv('DOCKER_ENV'):
    try:
        # 在Linux/Docker环境中设置事件循环策略
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    except Exception as e:
        logger.warning(f"设置事件循环策略失败: {e}")

# 确保在Docker环境中使用正确的事件循环
if os.getenv('DOCKER_ENV'):
    try:
        # 强制使用SelectorEventLoop（在Docker中更稳定）
        if hasattr(asyncio, 'SelectorEventLoop'):
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)
    except Exception as e:
        logger.warning(f"设置SelectorEventLoop失败: {e}")


def _normalize_cached_amount(amount: Any) -> Optional[float]:
    if amount in (None, ''):
        return None

    amount_clean = str(amount).replace('¥', '').replace('￥', '').replace('$', '').strip()
    try:
        return float(amount_clean)
    except (ValueError, TypeError):
        return None


def _is_coin_deduction_item_config(item_config: Dict[str, Any]) -> bool:
    if not item_config:
        return False

    detail_text = str(item_config.get('item_detail') or '').strip()
    return '闲鱼币抵扣' in detail_text


def _should_use_cached_order(existing_order: Dict[str, Any], item_config: Dict[str, Any] = None) -> bool:
    if not existing_order:
        return False

    amount_value = _normalize_cached_amount(existing_order.get('amount'))
    amount_valid = amount_value is not None and amount_value > 0
    has_valid_spec = bool((existing_order.get('spec_name') or '').strip() and (existing_order.get('spec_value') or '').strip())
    status_value = str(existing_order.get('order_status') or '').strip().lower()
    status_valid = bool(status_value and status_value not in ('unknown', 'processing'))

    if _is_coin_deduction_item_config(item_config):
        configured_amount = _normalize_cached_amount(item_config.get('item_price'))
        if configured_amount is not None and amount_value is not None and abs(amount_value - configured_amount) <= 0.0009:
            return False

    if item_config and item_config.get('is_multi_spec'):
        return amount_valid and status_valid and has_valid_spec

    return amount_valid and (status_valid or has_valid_spec)


class OrderDetailFetcher:
    """闲鱼订单详情获取器"""

    # 类级别的锁字典，为每个order_id维护一个锁
    _order_locks = defaultdict(lambda: asyncio.Lock())

    def __init__(self, cookie_string: str = None, headless: bool = True, cookie_id_for_log: str = "unknown"):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.headless = headless  # 保存headless设置
        self.cookie_id_for_log = cookie_id_for_log or "unknown"
        self._last_order_status_source = 'unknown'
        self._active_order_id = ''
        self._captured_amount_candidates: List[Dict[str, Any]] = []
        self._captured_sku_candidates: List[Dict[str, Any]] = []
        self._pending_response_tasks = set()
        self._response_handler = None

        # 请求头配置
        self.headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en,zh-CN;q=0.9,zh;q=0.8,ru;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "sec-ch-ua": "\"Not)A;Brand\";v=\"8\", \"Chromium\";v=\"138\", \"Google Chrome\";v=\"138\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1"
        }

        # Cookie配置 - 支持动态传入
        self.cookie = cookie_string

    async def init_browser(self, headless: bool = None):
        """初始化浏览器"""
        try:
            # 如果没有传入headless参数，使用实例的设置
            if headless is None:
                headless = self.headless

            logger.info(f"开始初始化浏览器，headless模式: {headless}")

            playwright = await async_playwright().start()

            # 启动浏览器（Docker环境优化）
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

            # 移除--single-process参数，使用多进程模式提高稳定性
            # if os.getenv('DOCKER_ENV'):
            #     browser_args.append('--single-process')  # 注释掉，避免崩溃

            # 在Docker环境中添加额外参数
            if os.getenv('DOCKER_ENV'):
                browser_args.extend([
                    '--disable-background-networking',
                    '--disable-background-timer-throttling',
                    '--disable-client-side-phishing-detection',
                    '--disable-default-apps',
                    '--disable-hang-monitor',
                    '--disable-popup-blocking',
                    '--disable-prompt-on-repost',
                    '--disable-sync',
                    '--disable-web-resources',
                    '--metrics-recording-only',
                    '--no-first-run',
                    '--safebrowsing-disable-auto-update',
                    '--enable-automation',
                    '--password-store=basic',
                    '--use-mock-keychain',
                    # 添加内存优化和稳定性参数
                    '--memory-pressure-off',
                    '--max_old_space_size=512',
                    '--disable-ipc-flooding-protection',
                    '--disable-component-extensions-with-background-pages',
                    '--disable-features=TranslateUI,BlinkGenPropertyTrees',
                    '--disable-logging',
                    '--disable-permissions-api',
                    '--disable-notifications',
                    '--no-pings',
                    '--no-zygote'
                ])

            logger.info(f"启动浏览器，参数: {browser_args}")
            self.browser = await playwright.chromium.launch(
                headless=headless,
                args=browser_args
            )

            logger.info("浏览器启动成功，创建上下文...")

            # 创建浏览器上下文
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            )

            logger.info("浏览器上下文创建成功，设置HTTP头...")

            # 设置额外的HTTP头
            await self.context.set_extra_http_headers(self.headers)

            logger.info("创建页面...")

            # 创建页面
            self.page = await self.context.new_page()

            logger.info("页面创建成功，设置Cookie...")

            # 设置Cookie
            await self._set_cookies()

            # 等待一段时间确保浏览器完全初始化
            await asyncio.sleep(1)

            logger.info("浏览器初始化成功")
            return True
            
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            return False

    async def _set_cookies(self):
        """设置Cookie"""
        try:
            # 解析Cookie字符串
            cookies = []
            for cookie_pair in self.cookie.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })
            
            # 添加Cookie到上下文
            await self.context.add_cookies(cookies)
            logger.info(f"已设置 {len(cookies)} 个Cookie")
            
        except Exception as e:
            logger.error(f"设置Cookie失败: {e}")

    async def fetch_order_detail(self, order_id: str, timeout: int = 30, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        获取订单详情（带锁机制和数据库缓存）

        Args:
            order_id: 订单ID
            timeout: 超时时间（秒）
            force_refresh: 是否强制刷新（跳过缓存直接从闲鱼获取）

        Returns:
            包含订单详情的字典，失败时返回None
        """
        # 获取该订单ID的锁
        order_lock = self._order_locks[order_id]

        async with order_lock:
            logger.info(f"🔒 获取订单 {order_id} 的锁，开始处理...")

            try:
                # 如果不是强制刷新，先查询数据库缓存
                if not force_refresh:
                    from db_manager import db_manager
                    existing_order = db_manager.get_order_by_id(order_id)

                    if existing_order:
                        amount = existing_order.get('amount', '')
                        item_config = None
                        if existing_order.get('item_id') and existing_order.get('cookie_id'):
                            item_config = db_manager.get_item_info(existing_order.get('cookie_id'), existing_order.get('item_id'))

                        if _should_use_cached_order(existing_order, item_config=item_config):
                            logger.info(f"📋 订单 {order_id} 已存在于数据库中且金额有效({amount})，直接返回缓存数据")
                            print(f"✅ 订单 {order_id} 使用缓存数据，跳过浏览器获取")

                            # 构建返回格式，与浏览器获取的格式保持一致
                            result = {
                                'order_id': existing_order['order_id'],
                                'url': f"https://www.goofish.com/order-detail?orderId={order_id}&role=seller",
                                'title': f"订单详情 - {order_id}",
                                'sku_info': {
                                    'spec_name': existing_order.get('spec_name', ''),
                                    'spec_value': existing_order.get('spec_value', ''),
                                    'spec_name_2': existing_order.get('spec_name_2', ''),
                                    'spec_value_2': existing_order.get('spec_value_2', ''),
                                    'quantity': existing_order.get('quantity', ''),
                                    'amount': existing_order.get('amount', ''),
                                    'amount_source': 'cache',
                                },
                                'spec_name': existing_order.get('spec_name', ''),
                                'spec_value': existing_order.get('spec_value', ''),
                                'spec_name_2': existing_order.get('spec_name_2', ''),
                                'spec_value_2': existing_order.get('spec_value_2', ''),
                                'quantity': existing_order.get('quantity', ''),
                                'amount': existing_order.get('amount', ''),
                                'amount_source': 'cache',
                                'timestamp': time.time(),
                                'from_cache': True  # 标记数据来源
                            }
                            return result
                        else:
                            logger.info(f"📋 订单 {order_id} 缓存字段不完整或状态无效，重新获取详情: amount={amount}, status={existing_order.get('order_status')}")
                            print(f"⚠️ 订单 {order_id} 缓存不满足复用条件，重新获取详情...")
                else:
                    logger.info(f"🔄 订单 {order_id} 强制刷新模式，跳过缓存检查")

                # 只有在数据库中没有有效数据时才初始化浏览器
                logger.info(f"🌐 订单 {order_id} 需要浏览器获取，开始初始化浏览器...")
                print(f"🔍 订单 {order_id} 开始浏览器获取详情...")

                # 确保浏览器准备就绪
                if not await self._ensure_browser_ready():
                    logger.error("浏览器初始化失败，无法获取订单详情")
                    return None

                self._register_response_capture_handler(order_id)
                try:
                    # 构建订单详情URL
                    url = f"https://www.goofish.com/order-detail?orderId={order_id}&role=seller"
                    logger.info(f"开始访问订单详情页面: {url}")

                    # 访问页面（带重试机制）
                    max_retries = 2
                    response = None

                    for retry in range(max_retries + 1):
                        try:
                            response = await self.page.goto(url, wait_until='networkidle', timeout=timeout * 1000)

                            if response and response.status == 200:
                                break
                            else:
                                logger.warning(f"页面访问失败，状态码: {response.status if response else 'None'}，重试 {retry + 1}/{max_retries + 1}")

                        except Exception as e:
                            logger.warning(f"页面访问异常: {e}，重试 {retry + 1}/{max_retries + 1}")

                            # 如果是浏览器连接问题，尝试重新初始化
                            if "Target page, context or browser has been closed" in str(e):
                                logger.info("检测到浏览器连接断开，尝试重新初始化...")
                                if await self._ensure_browser_ready():
                                    logger.info("浏览器重新初始化成功，继续重试...")
                                    self._register_response_capture_handler(order_id)
                                    continue
                                else:
                                    logger.error("浏览器重新初始化失败")
                                    return None

                            if retry == max_retries:
                                logger.error(f"页面访问最终失败: {e}")
                                return None

                            await asyncio.sleep(1)  # 重试前等待1秒

                    if not response or response.status != 200:
                        logger.error(f"页面访问最终失败，状态码: {response.status if response else 'None'}")
                        return None

                    logger.info("页面加载成功，等待内容渲染...")

                    # 等待页面完全加载
                    try:
                        await self.page.wait_for_load_state('networkidle')
                    except Exception as e:
                        logger.warning(f"等待页面加载状态失败: {e}")
                        # 继续执行，不中断流程

                    # 额外等待确保动态内容加载完成
                    await asyncio.sleep(3)

                    # 获取并解析SKU信息
                    sku_info = await self._get_sku_content()

                    # 获取订单状态
                    order_status = await self._get_order_status()
                    logger.info(f"订单 {order_id} 状态: {order_status}")

                    # 解析失败时，刷新页面后重试一次，降低偶发结构变化/异步渲染导致的漏解析概率
                    if not self._is_order_detail_parse_success(sku_info, order_status):
                        self._log_order_detail_parse_event(
                            event_name="ORDER_DETAIL_PARSE_ALERT",
                            order_id=order_id,
                            url=url,
                            attempt="first",
                            sku_info=sku_info,
                            order_status=order_status,
                            level="warning"
                        )
                        logger.warning(
                            f"订单 {order_id} 首次解析结果不完整，准备刷新页面重试: "
                            f"sku_info={sku_info}, order_status={order_status}"
                        )
                        try:
                            await self.page.reload(wait_until='networkidle', timeout=timeout * 1000)
                            await asyncio.sleep(2)
                            retry_sku_info = await self._get_sku_content()
                            retry_order_status = await self._get_order_status()
                            logger.info(
                                f"订单 {order_id} 重试解析结果: sku_info={retry_sku_info}, "
                                f"order_status={retry_order_status}"
                            )

                            if self._is_order_detail_parse_success(retry_sku_info, retry_order_status):
                                sku_info = retry_sku_info
                                order_status = retry_order_status
                                logger.info(f"订单 {order_id} 刷新重试后解析成功")
                                self._log_order_detail_parse_event(
                                    event_name="ORDER_DETAIL_PARSE_RECOVERED",
                                    order_id=order_id,
                                    url=url,
                                    attempt="retry",
                                    sku_info=sku_info,
                                    order_status=order_status,
                                    level="info"
                                )
                            else:
                                logger.warning(f"订单 {order_id} 刷新重试后仍未解析到完整详情")
                                self._log_order_detail_parse_event(
                                    event_name="ORDER_DETAIL_PARSE_ALERT",
                                    order_id=order_id,
                                    url=url,
                                    attempt="retry_final",
                                    sku_info=retry_sku_info,
                                    order_status=retry_order_status,
                                    level="warning"
                                )
                        except Exception as retry_e:
                            logger.warning(f"订单 {order_id} 刷新重试解析异常: {retry_e}")
                            self._log_order_detail_parse_event(
                                event_name="ORDER_DETAIL_PARSE_ALERT",
                                order_id=order_id,
                                url=url,
                                attempt="retry_exception",
                                sku_info=sku_info,
                                order_status=order_status,
                                level="warning",
                                error=str(retry_e)
                            )

                    # 获取页面标题
                    try:
                        title = await self.page.title()
                    except Exception as e:
                        logger.warning(f"获取页面标题失败: {e}")
                        title = f"订单详情 - {order_id}"

                    result = {
                        'order_id': order_id,
                        'url': url,
                        'title': title,
                        'sku_info': sku_info,  # 包含解析后的规格信息
                        'spec_name': sku_info.get('spec_name', '') if sku_info else '',
                        'spec_value': sku_info.get('spec_value', '') if sku_info else '',
                        'spec_name_2': sku_info.get('spec_name_2', '') if sku_info else '',  # 规格2名称
                        'spec_value_2': sku_info.get('spec_value_2', '') if sku_info else '',  # 规格2值
                        'quantity': sku_info.get('quantity', '') if sku_info else '',  # 数量
                        'amount': sku_info.get('amount', '') if sku_info else '',      # 金额
                        'amount_source': sku_info.get('amount_source', '') if sku_info else '',
                        'spec_parse_mode': self._classify_spec_parse_mode(sku_info),
                        'order_status': order_status,  # 订单状态
                        'order_status_source': self._last_order_status_source,
                        'timestamp': time.time(),
                        'from_cache': False  # 标记数据来源
                    }

                    logger.info(f"订单详情获取成功: {order_id}")
                    if sku_info:
                        logger.info(f"规格信息 - 名称: {result['spec_name']}, 值: {result['spec_value']}")
                        logger.info(f"数量: {result['quantity']}, 金额: {result['amount']}")
                    return result
                finally:
                    await self._wait_for_response_capture_tasks(timeout=0.5)
                    self._clear_response_capture_handler()

            except Exception as e:
                logger.error(f"获取订单详情失败: {e}")
                return None

    def _parse_sku_content(self, sku_content: str) -> Dict[str, str]:
        """
        解析SKU内容，根据冒号分割规格名称和规格值
        支持双规格格式：例如 "版本选择:mac 版 - 单文件;远程:自行安装"

        Args:
            sku_content: 原始SKU内容字符串

        Returns:
            包含规格名称和规格值的字典，如果解析失败则返回空字典
            对于双规格，会额外包含 spec_name_2 和 spec_value_2
        """
        try:
            if not sku_content or ':' not in sku_content:
                logger.warning(f"SKU内容格式无效或不包含冒号: {sku_content}")
                return {}

            # 检查是否包含双规格（通过分号分隔，且分号后有冒号）
            # 格式如：版本选择:mac 版 - 单文件;远程:自行安装
            if ';' in sku_content:
                # 查找分号位置，检查分号后面是否有冒号（表示有第二个规格）
                semicolon_idx = sku_content.find(';')
                second_part = sku_content[semicolon_idx + 1:].strip()

                if ':' in second_part:
                    # 这是双规格格式
                    first_part = sku_content[:semicolon_idx].strip()

                    # 解析第一个规格
                    first_spec_parts = first_part.split(':', 1)
                    if len(first_spec_parts) == 2:
                        spec_name = first_spec_parts[0].strip()
                        spec_value = first_spec_parts[1].strip()
                    else:
                        logger.warning(f"第一个规格解析失败: {first_part}")
                        spec_name = ''
                        spec_value = first_part

                    # 解析第二个规格
                    second_spec_parts = second_part.split(':', 1)
                    spec_name_2 = second_spec_parts[0].strip()
                    spec_value_2 = second_spec_parts[1].strip() if len(second_spec_parts) > 1 else ''

                    result = {
                        'spec_name': spec_name,
                        'spec_value': spec_value
                    }

                    if spec_name_2 and spec_value_2:
                        result['spec_name_2'] = spec_name_2
                        result['spec_value_2'] = spec_value_2
                        logger.info(f"双规格解析成功 - 规格1: {spec_name}:{spec_value}, 规格2: {spec_name_2}:{spec_value_2}")
                    else:
                        logger.info(f"SKU解析成功（单规格）- 规格名称: {spec_name}, 规格值: {spec_value}")

                    return result

            # 单规格处理（原有逻辑）
            parts = sku_content.split(':', 1)  # 只分割第一个冒号

            if len(parts) == 2:
                spec_name = parts[0].strip()
                spec_value = parts[1].strip()

                if spec_name and spec_value:
                    result = {
                        'spec_name': spec_name,
                        'spec_value': spec_value
                    }
                    logger.info(f"SKU解析成功 - 规格名称: {spec_name}, 规格值: {spec_value}")
                    return result
                else:
                    logger.warning(f"SKU解析失败，规格名称或值为空: 名称='{spec_name}', 值='{spec_value}'")
                    return {}
            else:
                logger.warning(f"SKU内容分割失败: {sku_content}")
                return {}

        except Exception as e:
            logger.error(f"解析SKU内容异常: {e}")
            return {}

    def _normalize_amount_text(self, amount_text: str) -> Optional[str]:
        """标准化金额文本，返回纯数字字符串（如 29.90）"""
        try:
            if amount_text is None:
                return None
            text = str(amount_text).strip()
            if not text:
                return None

            # 优先提取货币格式
            money_match = re.search(r'[¥￥$]\s*([0-9]+(?:\.[0-9]{1,2})?)', text)
            if money_match:
                return money_match.group(1)

            # 兜底提取纯数字
            number_match = re.search(r'([0-9]+(?:\.[0-9]{1,2})?)', text)
            if number_match:
                return number_match.group(1)

            return None
        except Exception:
            return None

    def _has_valid_amount(self, amount_text: Any) -> bool:
        """判断金额是否可解析为数字（0 也视为有效）"""
        normalized = self._normalize_amount_text(str(amount_text) if amount_text is not None else '')
        if normalized is None:
            return False
        try:
            float(normalized)
            return True
        except (ValueError, TypeError):
            return False

    def _parse_amount_value(self, amount_text: Any) -> Optional[float]:
        normalized = self._normalize_amount_text(str(amount_text) if amount_text is not None else '')
        if normalized is None:
            return None
        try:
            return float(normalized)
        except (ValueError, TypeError):
            return None

    def _reset_amount_capture(self, order_id: str) -> None:
        self._active_order_id = str(order_id or '').strip()
        self._captured_amount_candidates = []
        self._captured_sku_candidates = []
        self._pending_response_tasks = set()

    def _clear_response_capture_handler(self) -> None:
        if not self._response_handler:
            return

        try:
            if self.page and hasattr(self.page, 'remove_listener'):
                self.page.remove_listener('response', self._response_handler)
            elif self.page and hasattr(self.page, 'off'):
                self.page.off('response', self._response_handler)
        except Exception as e:
            logger.debug(f"移除订单详情响应监听失败: {e}")
        finally:
            self._response_handler = None

    def _register_response_capture_handler(self, order_id: str) -> None:
        self._clear_response_capture_handler()
        self._reset_amount_capture(order_id)

        if not self.page:
            return

        current_order_id = self._active_order_id

        def _on_task_done(task: asyncio.Task) -> None:
            self._pending_response_tasks.discard(task)
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as task_error:
                logger.debug(f"订单详情响应解析任务异常: {task_error}")

        def _response_handler(response) -> None:
            try:
                task = asyncio.create_task(self._process_order_detail_response(response, current_order_id))
            except Exception as e:
                logger.debug(f"创建订单详情响应解析任务失败: {e}")
                return

            self._pending_response_tasks.add(task)
            task.add_done_callback(_on_task_done)

        self._response_handler = _response_handler
        self.page.on('response', _response_handler)

    async def _wait_for_response_capture_tasks(self, timeout: float = 1.5) -> None:
        if not self._pending_response_tasks:
            return

        try:
            await asyncio.wait(list(self._pending_response_tasks), timeout=timeout)
        except Exception as e:
            logger.debug(f"等待订单详情响应解析任务失败: {e}")

    def _try_parse_json_text(self, text: str) -> Optional[Any]:
        if not text:
            return None

        stripped = str(text).strip()
        if not stripped or stripped[0] not in '{[':
            return None

        try:
            return json.loads(stripped)
        except Exception:
            return None

    def _is_trusted_order_detail_response_url(self, url: str) -> bool:
        lowered_url = str(url or '').lower()
        trusted_tokens = (
            'mtop.idle.web.trade.order.detail',
            'trade.order.detail',
        )
        return any(token in lowered_url for token in trusted_tokens)

    def _normalize_minor_amount_value(self, amount_value: Any) -> Any:
        text = str(amount_value).strip() if amount_value is not None else ''
        if not re.fullmatch(r'\d+', text):
            return amount_value

        try:
            minor_value = int(text)
        except (TypeError, ValueError):
            return amount_value

        if minor_value <= 0:
            return amount_value

        return f"{minor_value / 100:.2f}"

    def _payload_references_order(self, payload: Any, order_id: str, url: str = '') -> bool:
        order_id_text = str(order_id or '').strip()
        url_text = str(url or '')
        lowered_url = url_text.lower()

        if order_id_text and order_id_text in url_text:
            return True

        try:
            payload_text = json.dumps(payload, ensure_ascii=False)
        except Exception:
            payload_text = str(payload)

        if order_id_text and order_id_text in payload_text:
            return True

        return self._is_trusted_order_detail_response_url(lowered_url)

    def _normalize_quantity_text(self, quantity_value: Any) -> Optional[str]:
        text = str(quantity_value or '').strip()
        if not text:
            return None

        match = re.search(r'(\d+)', text)
        if not match:
            return None

        try:
            normalized = str(int(match.group(1)))
        except (TypeError, ValueError):
            return None

        if normalized == '0':
            return None
        return normalized

    def _normalize_sku_candidate_text(self, sku_text: Any) -> str:
        if sku_text is None:
            return ''
        return re.sub(r'\s+', ' ', str(sku_text).replace('：', ':')).strip()

    def _is_numeric_index_spec_name_like(self, spec_name: str, spec_value: str) -> bool:
        normalized_name = re.sub(r'\s+', '', (spec_name or '').strip())
        normalized_value = re.sub(r'\s+', ' ', (spec_value or '').strip())
        if not normalized_name or not normalized_value:
            return False

        if not re.fullmatch(r'(?:第)?\d{1,2}(?:项|号|档)?', normalized_name):
            return False

        if len(normalized_value) < 2 or len(normalized_value) > 40:
            return False

        if self._is_datetime_like(normalized_value):
            return False

        if re.fullmatch(r'[¥￥]?\d+(?:\.\d{1,2})?', normalized_value):
            return False

        if normalized_value.lower().startswith(('http://', 'https://', 'fleamarket://')):
            return False

        if not re.search(r'[\u4e00-\u9fffA-Za-z]', normalized_value):
            return False

        return True

    def _score_sku_text_candidate(
        self,
        normalized_key: str,
        *,
        path: str = '',
        context: str = '',
        sku_text: str = '',
        from_pair: bool = False
    ) -> int:
        key = str(normalized_key or '').lower()
        path_lower = str(path or '').lower()
        normalized_context = re.sub(r'\s+', ' ', str(context or '')).strip()
        normalized_sku_text = self._normalize_sku_candidate_text(sku_text)

        if not normalized_sku_text or len(normalized_sku_text) > 120 or ':' not in normalized_sku_text:
            return 0

        score = 0
        strong_keys = {
            'skuinfo', 'sku_info', 'skutext', 'sku_text', 'skudesc', 'sku_desc',
            'skucontent', 'sku_content', 'specinfo', 'spec_info', 'spectext',
            'spec_text', 'specdesc', 'spec_desc', 'itemsku', 'item_sku',
            'itemspec', 'item_spec'
        }
        medium_key_tokens = ('sku', 'spec', 'attr', 'property', 'option', 'variant', 'model')

        if key in strong_keys:
            score = 220
        elif any(token in key for token in medium_key_tokens):
            score = 170
        elif from_pair:
            score = 135
        elif any(token in path_lower for token in ('.sku', '.spec', '.attr', '.property', '.option', '.variant', '.model')):
            score = 120
        else:
            return 0

        if '.iteminfo.' in path_lower:
            score += 70
        elif '.components[' in path_lower:
            score += 20

        if any(token in normalized_context for token in ('规格', '型号', '版本', '选项', '属性', '套餐')):
            score += 35

        if ';' in normalized_sku_text:
            score += 10

        return score

    def _append_sku_candidate(
        self,
        candidates: List[Dict[str, Any]],
        sku_text: Any,
        *,
        quantity: Optional[str] = None,
        path: str = '',
        score: int = 0
    ) -> None:
        normalized_sku_text = self._normalize_sku_candidate_text(sku_text)
        if score <= 0 or not normalized_sku_text or len(normalized_sku_text) > 120 or ':' not in normalized_sku_text:
            return

        candidates.append({
            'sku_text': normalized_sku_text,
            'quantity': quantity,
            'path': path,
            'score': score,
        })

    def _extract_sku_candidates_from_payload(self, payload: Any, path: str = 'root', depth: int = 0) -> List[Dict[str, Any]]:
        if payload is None or depth > 8:
            return []

        candidates: List[Dict[str, Any]] = []

        if isinstance(payload, dict):
            quantity_context = None
            for quantity_key in ('buyAmount', 'buy_amount', 'quantity', 'itemCount', 'count', 'num'):
                if quantity_key in payload:
                    quantity_context = self._normalize_quantity_text(payload.get(quantity_key))
                    if quantity_context:
                        break

            context_fields = []
            for context_key in ('title', 'label', 'name', 'preText', 'subTitle', 'displayText', 'content', 'desc', 'text'):
                context_value = payload.get(context_key)
                if isinstance(context_value, (str, int, float)):
                    normalized_context_value = self._normalize_sku_candidate_text(context_value)
                    if normalized_context_value:
                        context_fields.append(normalized_context_value)
            dict_context = ' | '.join(context_fields)[:240]

            title_text = ''
            title_key = ''
            for candidate_key in ('title', 'label', 'name', 'preText', 'subTitle', 'displayText', 'key', 'attrName', 'specName', 'skuName'):
                candidate_value = payload.get(candidate_key)
                if isinstance(candidate_value, (str, int, float)):
                    normalized_title = self._normalize_sku_candidate_text(candidate_value)
                    if normalized_title:
                        title_text = normalized_title
                        title_key = candidate_key
                        break

            value_text = ''
            value_key = ''
            for candidate_key in ('value', 'text', 'content', 'displayText', 'attrValue', 'specValue', 'skuValue'):
                candidate_value = payload.get(candidate_key)
                if isinstance(candidate_value, (str, int, float)):
                    normalized_value = self._normalize_sku_candidate_text(candidate_value)
                    if normalized_value:
                        value_text = normalized_value
                        value_key = candidate_key
                        break

            if not quantity_context and title_text and value_text and any(token in title_text for token in ('数量', '购买数量', '件数')):
                quantity_context = self._normalize_quantity_text(value_text)

            if (
                title_text and value_text and
                ':' not in title_text and ':' not in value_text and
                (
                    self._is_text_fallback_spec_name_like(title_text) or
                    self._is_numeric_index_spec_name_like(title_text, value_text)
                )
            ):
                pair_path = f"{path}.{title_key}+{value_key}" if title_key and value_key else path
                pair_sku_text = f"{title_text}:{value_text}"
                pair_score = self._score_sku_text_candidate(
                    f"{title_key}_{value_key}",
                    path=pair_path,
                    context=dict_context,
                    sku_text=pair_sku_text,
                    from_pair=True
                )
                self._append_sku_candidate(
                    candidates,
                    pair_sku_text,
                    quantity=quantity_context,
                    path=pair_path,
                    score=pair_score
                )

            for key, value in payload.items():
                key_text = str(key)
                normalized_key = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]', '', key_text).lower()
                key_path = f"{path}.{key_text}"

                if isinstance(value, str):
                    nested_payload = self._try_parse_json_text(value)
                    if nested_payload is not None:
                        candidates.extend(
                            self._extract_sku_candidates_from_payload(
                                nested_payload,
                                path=f"{key_path}.json",
                                depth=depth + 1
                            )
                        )

                    score = self._score_sku_text_candidate(
                        normalized_key,
                        path=key_path,
                        context=dict_context,
                        sku_text=value
                    )
                    self._append_sku_candidate(
                        candidates,
                        value,
                        quantity=quantity_context,
                        path=key_path,
                        score=score
                    )

                candidates.extend(self._extract_sku_candidates_from_payload(value, path=key_path, depth=depth + 1))

        elif isinstance(payload, list):
            for index, item in enumerate(payload[:50]):
                candidates.extend(self._extract_sku_candidates_from_payload(item, path=f"{path}[{index}]", depth=depth + 1))

        return candidates

    def _score_amount_key_candidate(self, normalized_key: str, *, context: str = '', path: str = '') -> int:
        key = str(normalized_key or '').lower()
        if not key:
            return 0

        ignored_key_tokens = [
            'coupon', 'discount', 'freight', 'postage', 'shipping', 'delivery',
            'deduction', 'coin', 'hongbao', 'voucher', 'reduce', 'cut',
            'original', 'origin', 'raw', 'list', 'market', 'crossed', 'strike',
            'buyamount'
        ]
        if any(token in key for token in ignored_key_tokens):
            return 0

        strong_key_tokens = [
            'actualpay', 'payamount', 'realpay', 'orderamount', 'paymentamount',
            'paidamount', 'finalamount', 'tradeamount', 'dealprice', 'buyerpayamount',
            'buyeractualpay', 'sellerrealamount', 'selleractualamount'
        ]
        medium_key_tokens = [
            'currentprice', 'realamount', 'finalprice', 'settleamount', 'settleprice',
            'payprice', 'buyerpay', 'orderprice'
        ]

        matched_strong_key = any(token in key for token in strong_key_tokens)
        matched_medium_key = any(token in key for token in medium_key_tokens)

        score = 0
        if matched_strong_key:
            score = 220
        elif matched_medium_key:
            score = 170
        elif key in {'price', 'amount', 'money'} or key.endswith('price') or key.endswith('amount'):
            score = 80
        else:
            return 0

        normalized_context = re.sub(r'\s+', ' ', str(context or '')).strip()
        path_lower = str(path or '').lower()
        high_context_tokens = ['实付款', '订单金额', '应付金额', '应付', '实收金额', '实收', '付款金额', '支付金额', '实付']
        medium_context_tokens = ['改价后', '优惠后', '成交价', '支付价', '最终价', '待发货', '去发货', '小刀']
        low_context_tokens = ['合计', '总价', '商品总价']
        negative_context_tokens = ['闲鱼币抵扣', '优惠', '立减', '折扣', '运费', '邮费', '红包', '券']

        if key == 'price' and any(token in path_lower for token in ('.iteminfo.price', '.priceinfo.price', '.paymentinfo.price')):
            score = max(score, 210)

        if any(token in normalized_context for token in high_context_tokens):
            score += 180
        elif any(token in normalized_context for token in medium_context_tokens):
            score += 120
        elif any(token in normalized_context for token in low_context_tokens):
            score += 70

        if 'priceinfo' in path_lower:
            score += 20

        if any(token in normalized_context for token in negative_context_tokens) and not any(
            token in normalized_context for token in high_context_tokens + medium_context_tokens
        ):
            score -= 110

        trusted_price_path = any(token in path_lower for token in ('.iteminfo.price', '.priceinfo.price', '.paymentinfo.price'))

        if (
            not matched_strong_key and
            not matched_medium_key and
            not trusted_price_path and
            (key in {'price', 'amount', 'money'} or key.endswith('price') or key.endswith('amount'))
        ) and not any(token in normalized_context for token in high_context_tokens + medium_context_tokens + low_context_tokens):
            return 0

        if score < 100 and not normalized_context:
            return 0

        return max(score, 0)

    def _append_amount_candidate(
        self,
        candidates: List[Dict[str, Any]],
        amount_value: Any,
        source: str,
        score: int,
        *,
        path: str = '',
        context: str = ''
    ) -> None:
        if score <= 0:
            return

        normalized_amount = self._normalize_amount_text(str(amount_value) if amount_value is not None else '')
        parsed_amount = self._parse_amount_value(normalized_amount)
        if normalized_amount is None or parsed_amount is None or parsed_amount <= 0 or parsed_amount > 100000:
            return

        candidates.append({
            'amount': normalized_amount,
            'source': source,
            'score': score,
            'path': path,
            'context': re.sub(r'\s+', ' ', str(context or '')).strip()[:240],
        })

    def _score_amount_title_candidate(self, title_text: str) -> int:
        normalized_title = re.sub(r'\s+', ' ', str(title_text or '')).strip()
        if not normalized_title:
            return 0

        ignored_title_tokens = ['闲鱼币抵扣', '智能抵扣', '待收闲鱼币', '优惠', '立减', '折扣', '运费', '邮费', '红包', '券']
        if any(token in normalized_title for token in ignored_title_tokens):
            return 0

        high_title_tokens = ['实付款', '订单金额', '应付金额', '应付', '实收金额', '实收', '付款金额', '支付金额', '实付', '成交价', '支付价', '最终价']
        medium_title_tokens = ['改价后', '优惠后', '合计', '总价', '商品总价']

        if any(token in normalized_title for token in high_title_tokens):
            return 280
        if any(token in normalized_title for token in medium_title_tokens):
            return 170
        return 0

    def _extract_amount_candidates_from_payload(
        self,
        payload: Any,
        *,
        path: str = 'payload',
        depth: int = 0
    ) -> List[Dict[str, Any]]:
        if payload is None or depth > 6:
            return []

        candidates: List[Dict[str, Any]] = []

        if isinstance(payload, dict):
            context_fields = []
            for context_key in ('title', 'desc', 'text', 'label', 'name', 'preText', 'subTitle', 'displayText', 'content'):
                context_value = payload.get(context_key)
                if isinstance(context_value, (str, int, float)):
                    normalized_context_value = re.sub(r'\s+', ' ', str(context_value)).strip()
                    if normalized_context_value:
                        context_fields.append(normalized_context_value)
            dict_context = ' | '.join(context_fields)[:240]

            title_candidate = None
            for title_key in ('title', 'label', 'name', 'preText', 'subTitle', 'displayText'):
                title_value = payload.get(title_key)
                if isinstance(title_value, (str, int, float)):
                    normalized_title_value = re.sub(r'\s+', ' ', str(title_value)).strip()
                    if normalized_title_value:
                        title_candidate = normalized_title_value
                        break

            raw_value_candidate = payload.get('value')
            title_score = self._score_amount_title_candidate(title_candidate)
            if title_score > 0 and isinstance(raw_value_candidate, (str, int, float)):
                self._append_amount_candidate(
                    candidates,
                    raw_value_candidate,
                    'payload_title_value',
                    title_score,
                    path=f'{path}.value',
                    context=title_candidate,
                )

            for key, value in payload.items():
                key_text = str(key)
                key_path = f"{path}.{key_text}"
                normalized_key = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]', '', key_text).lower()

                if isinstance(value, (dict, list)):
                    candidates.extend(self._extract_amount_candidates_from_payload(value, path=key_path, depth=depth + 1))
                    continue

                if isinstance(value, str):
                    nested_payload = self._try_parse_json_text(value)
                    if nested_payload is not None:
                        candidates.extend(
                            self._extract_amount_candidates_from_payload(
                                nested_payload,
                                path=f"{key_path}.json",
                                depth=depth + 1
                            )
                        )

                    semantic_amount, semantic_source = self._extract_preferred_amount_from_text(value)
                    if semantic_amount:
                        semantic_score = 0
                        if semantic_source == 'keyword_high':
                            semantic_score = 260
                        elif semantic_source == 'keyword_low':
                            semantic_score = 180
                        elif semantic_source == 'currency' and any(token in normalized_key for token in ('price', 'amount', 'money', 'pay', 'text', 'desc', 'label')):
                            semantic_score = 120

                        self._append_amount_candidate(
                            candidates,
                            semantic_amount,
                            f'payload_text_{semantic_source}',
                            semantic_score,
                            path=key_path,
                            context=value
                        )

                if isinstance(value, (str, int, float)):
                    key_score = self._score_amount_key_candidate(normalized_key, context=dict_context, path=key_path)
                    self._append_amount_candidate(
                        candidates,
                        value,
                        f'payload_key_{normalized_key or "unknown"}',
                        key_score,
                        path=key_path,
                        context=dict_context
                    )

            return candidates

        if isinstance(payload, list):
            for index, item in enumerate(payload[:50]):
                candidates.extend(self._extract_amount_candidates_from_payload(item, path=f"{path}[{index}]", depth=depth + 1))

        return candidates

    async def _process_order_detail_response(self, response, order_id: str) -> None:
        try:
            if not response or response.status != 200:
                return

            url = str(response.url or '')
            lowered_url = url.lower()
            if not any(domain in lowered_url for domain in ('goofish.com', 'idlefish.com', 'taobao.com', 'mtop')):
                return

            if not self._is_trusted_order_detail_response_url(lowered_url):
                return

            headers = response.headers or {}
            content_type = (headers.get('content-type') or headers.get('Content-Type') or '').lower()
            resource_type = getattr(getattr(response, 'request', None), 'resource_type', '')
            if resource_type not in ('fetch', 'xhr', 'document') and 'json' not in content_type and 'mtop' not in lowered_url:
                return

            payload = None
            try:
                payload = await response.json()
            except Exception:
                try:
                    response_text = await response.text()
                except Exception:
                    response_text = ''
                payload = self._try_parse_json_text(response_text)

            if payload is None or not self._payload_references_order(payload, order_id, url):
                return

            response_candidates = self._extract_amount_candidates_from_payload(payload, path=f"response[{url.split('?')[0]}]")
            for candidate in response_candidates:
                candidate_copy = dict(candidate)
                candidate_copy['source'] = f"structured_response::{candidate['source']}"
                candidate_copy['response_url'] = url
                self._captured_amount_candidates.append(candidate_copy)

            if response_candidates:
                best_candidate = max(response_candidates, key=lambda item: item.get('score', 0))
                logger.info(
                    f"捕获订单金额候选: order_id={order_id}, amount={best_candidate.get('amount')}, "
                    f"score={best_candidate.get('score')}, source={best_candidate.get('source')}, url={url}"
                )

            sku_candidates = self._extract_sku_candidates_from_payload(payload, path=f"response[{url.split('?')[0]}]")
            self._captured_sku_candidates.extend(sku_candidates)
            if sku_candidates:
                best_sku_candidate = max(sku_candidates, key=lambda item: item.get('score', 0))
                logger.info(
                    f"捕获订单规格候选: order_id={order_id}, sku={best_sku_candidate.get('sku_text')}, "
                    f"quantity={best_sku_candidate.get('quantity') or ''}, path={best_sku_candidate.get('path')}"
                )
        except Exception as e:
            logger.debug(f"解析订单详情响应失败: {e}")

    def _get_best_captured_amount_candidate(self) -> Optional[Dict[str, Any]]:
        if not self._captured_amount_candidates:
            return None

        deduped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for candidate in self._captured_amount_candidates:
            dedupe_key = (
                str(candidate.get('amount', '')),
                str(candidate.get('source', '')),
                str(candidate.get('path', '')),
            )
            existing = deduped.get(dedupe_key)
            if existing is None or candidate.get('score', 0) > existing.get('score', 0):
                deduped[dedupe_key] = candidate

        ranked_candidates = sorted(
            deduped.values(),
            key=lambda item: (item.get('score', 0), item.get('amount', '')),
            reverse=True
        )
        return ranked_candidates[0] if ranked_candidates else None

    def _get_best_captured_sku_candidate(self) -> Optional[Dict[str, Any]]:
        if not self._captured_sku_candidates:
            return None

        deduped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for candidate in self._captured_sku_candidates:
            dedupe_key = (
                str(candidate.get('sku_text', '')),
                str(candidate.get('quantity', '')),
                str(candidate.get('path', '')),
            )
            existing = deduped.get(dedupe_key)
            if existing is None or candidate.get('score', 0) > existing.get('score', 0):
                deduped[dedupe_key] = candidate

        ranked_candidates = sorted(
            deduped.values(),
            key=lambda item: (item.get('score', 0), len(str(item.get('sku_text', '')))),
            reverse=True,
        )
        return ranked_candidates[0] if ranked_candidates else None

    def _get_ranked_captured_sku_candidates(self) -> List[Dict[str, Any]]:
        if not self._captured_sku_candidates:
            return []

        deduped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for candidate in self._captured_sku_candidates:
            dedupe_key = (
                str(candidate.get('sku_text', '')),
                str(candidate.get('quantity', '')),
                str(candidate.get('path', '')),
            )
            existing = deduped.get(dedupe_key)
            if existing is None or candidate.get('score', 0) > existing.get('score', 0):
                deduped[dedupe_key] = candidate

        return sorted(
            deduped.values(),
            key=lambda item: (item.get('score', 0), len(str(item.get('sku_text', '')))),
            reverse=True,
        )

    async def _extract_amount_from_structured_content(self) -> Tuple[Optional[str], str]:
        await self._wait_for_response_capture_tasks(timeout=1.5)

        best_candidate = self._get_best_captured_amount_candidate()
        if best_candidate:
            logger.info(
                f"采用结构化响应金额候选: amount={best_candidate.get('amount')}, "
                f"score={best_candidate.get('score')}, source={best_candidate.get('source')}, "
                f"path={best_candidate.get('path')}"
            )
            return best_candidate.get('amount'), best_candidate.get('source', 'unknown')

        try:
            html_content = await self.page.content()
        except Exception as e:
            logger.debug(f"获取页面HTML失败，无法解析结构化金额: {e}")
            return None, 'unknown'

        if not html_content:
            return None, 'unknown'

        pattern_specs = [
            (
                'structured_html_priceinfo',
                re.compile(r'"preText"\s*:\s*"[^"]*(实付款|订单金额|应付金额|改价后|优惠后|成交价|支付金额|支付价)[^"]*".{0,240}?"price"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)"', re.IGNORECASE | re.DOTALL),
                2,
            ),
            (
                'structured_html_priceinfo',
                re.compile(r'"price"\s*:\s*"([0-9]+(?:\.[0-9]{1,2})?)".{0,240}?"preText"\s*:\s*"[^"]*(实付款|订单金额|应付金额|改价后|优惠后|成交价|支付金额|支付价)[^"]*"', re.IGNORECASE | re.DOTALL),
                1,
            ),
            (
                'structured_html_key',
                re.compile(r'"(?:actualPay|payAmount|realPay|orderAmount|paymentAmount|finalAmount|buyerPayAmount|dealPrice|paidAmount|tradeAmount)"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)"?', re.IGNORECASE),
                1,
            ),
            (
                'structured_html_text',
                re.compile(r'(?:实付款|订单金额|应付金额|改价后|优惠后|成交价|支付金额|支付价)[^0-9¥￥$]{0,20}[¥￥$]?\s*([0-9]+(?:\.[0-9]{1,2})?)', re.IGNORECASE),
                1,
            ),
        ]

        for source, pattern, group_index in pattern_specs:
            match = pattern.search(html_content)
            if not match:
                continue

            normalized_amount = self._normalize_amount_text(match.group(group_index))
            if normalized_amount is None:
                continue

            logger.info(f"通过页面结构化内容找到金额: {normalized_amount} (source={source})")
            return normalized_amount, source

        return None, 'unknown'

    async def _extract_sku_from_structured_content(self) -> Dict[str, str]:
        await self._wait_for_response_capture_tasks(timeout=1.5)

        for candidate in self._get_ranked_captured_sku_candidates():
            sku_text = str(candidate.get('sku_text') or '').strip()
            if not sku_text:
                continue

            parsed = self._parse_sku_content(sku_text)
            if not parsed:
                continue

            sanitized = self._sanitize_sku_result(parsed, source='structured_response_candidate')
            if not (sanitized.get('spec_name') and sanitized.get('spec_value')):
                continue

            quantity = self._normalize_quantity_text(candidate.get('quantity'))
            if quantity:
                sanitized['quantity'] = quantity

            logger.info(
                f"采用结构化响应规格候选: sku={sku_text}, quantity={quantity or ''}, "
                f"path={candidate.get('path')}"
            )
            return sanitized

        return {}

    async def _extract_amount_from_semantic_blocks(self) -> Tuple[Optional[str], str]:
        semantic_keywords = [
            '实付款', '订单金额', '应付金额', '应付', '实收', '付款金额', '支付金额', '实付',
            '改价后', '优惠后', '成交价', '支付价', '最终价', '闲鱼币抵扣'
        ]

        try:
            text_blocks = await self.page.evaluate(
                """(keywords) => {
                    const nodes = Array.from(document.querySelectorAll('div, span, p, section, article, li'));
                    const results = [];
                    const seen = new Set();
                    for (const node of nodes) {
                        const text = String(node.innerText || node.textContent || '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        if (!text || text.length < 4 || text.length > 180) {
                            continue;
                        }
                        if (!keywords.some(keyword => text.includes(keyword))) {
                            continue;
                        }
                        if (!/\\d/.test(text)) {
                            continue;
                        }
                        if (seen.has(text)) {
                            continue;
                        }
                        seen.add(text);
                        results.push(text);
                        if (results.length >= 24) {
                            break;
                        }
                    }
                    return results;
                }""",
                semantic_keywords,
            )
        except Exception as e:
            logger.debug(f"提取语义金额块失败: {e}")
            return None, 'unknown'

        high_signal_tokens = {'实付款', '订单金额', '应付金额', '应付', '实收', '付款金额', '支付金额', '实付', '改价后', '优惠后', '成交价', '支付价', '最终价'}

        for block in text_blocks or []:
            amount, source = self._extract_preferred_amount_from_text(block)
            if amount is None or source == 'unknown':
                continue

            if source == 'currency' and not any(token in block for token in high_signal_tokens):
                continue

            semantic_source = f'semantic_{source}'
            logger.info(f"通过语义金额块找到金额: {amount} (source={semantic_source}, block={block[:80]})")
            return amount, semantic_source

        return None, 'unknown'

    def _extract_preferred_amount_from_text(self, text: str) -> Tuple[Optional[str], str]:
        """从文本中提取更可信的金额，优先识别实付款等语义化字段。"""
        if not text:
            return None, 'unknown'

        normalized_text = re.sub(r'\s+', ' ', str(text)).strip()
        if not normalized_text:
            return None, 'unknown'

        keyword_groups = [
            ('keyword_high', ['实付款', '订单金额', '应付金额', '应付', '实收金额', '实收', '付款金额', '支付金额', '实付']),
            ('keyword_low', ['改价后', '优惠后', '成交价', '支付价', '最终价', '合计', '总价', '商品总价']),
        ]

        for source, keywords in keyword_groups:
            for keyword in keywords:
                escaped_keyword = re.escape(keyword)
                patterns = [
                    rf'{escaped_keyword}\s*[:：]?\s*[¥￥$]?\s*([0-9]+(?:\.[0-9]{{1,2}})?)',
                    rf'([0-9]+(?:\.[0-9]{{1,2}})?)\s*(?:元|块)?\s*{escaped_keyword}',
                    rf'[¥￥$]\s*([0-9]+(?:\.[0-9]{{1,2}})?)\s*{escaped_keyword}',
                ]
                for pattern in patterns:
                    matches = re.findall(pattern, normalized_text)
                    if matches:
                        normalized_amount = self._normalize_amount_text(matches[-1])
                        if normalized_amount is not None:
                            return normalized_amount, source

        currency_matches = re.findall(r'[¥￥$]\s*([0-9]+(?:\.[0-9]{1,2})?)', normalized_text)
        if len(currency_matches) == 1:
            normalized_amount = self._normalize_amount_text(currency_matches[0])
            if normalized_amount is not None:
                return normalized_amount, 'currency'

        return None, 'unknown'

    def _extract_coin_deduction_value_from_text(self, text: str) -> Optional[str]:
        if not text:
            return None

        normalized_text = re.sub(r'\s+', ' ', str(text)).strip()
        if not normalized_text or '闲鱼币抵扣' not in normalized_text:
            return None

        patterns = [
            r'闲鱼币抵扣[^0-9¥￥$]{0,20}[¥￥$]?\s*([0-9]+(?:\.[0-9]{1,2})?)',
            r'([0-9]+(?:\.[0-9]{1,2})?)\s*(?:元|块)?\s*闲鱼币抵扣',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, normalized_text)
            if matches:
                normalized_amount = self._normalize_amount_text(matches[-1])
                if normalized_amount is not None:
                    return normalized_amount

        return None

    def _resolve_coin_deduction_amount(
        self,
        primary_amount: Optional[str],
        primary_source: str,
        fallback_result: Dict[str, str],
        page_text: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        if not primary_amount or not page_text or '闲鱼币抵扣' not in page_text:
            return None, None

        primary_amount_value = self._parse_amount_value(primary_amount)
        if primary_amount_value is None or primary_amount_value <= 0:
            return None, None

        deduction_amount = self._extract_coin_deduction_value_from_text(page_text)
        deduction_amount_value = self._parse_amount_value(deduction_amount)
        if deduction_amount_value is not None and 0 < deduction_amount_value < primary_amount_value:
            adjusted_amount = self._normalize_amount_text(f"{primary_amount_value - deduction_amount_value:.2f}")
            adjusted_amount_value = self._parse_amount_value(adjusted_amount)
            if adjusted_amount and adjusted_amount_value is not None and 0 < adjusted_amount_value < primary_amount_value:
                logger.info(
                    f"检测到闲鱼币抵扣，使用实付金额覆盖原价: primary={primary_amount}, deduction={deduction_amount}, "
                    f"adjusted={adjusted_amount}, source={primary_source}"
                )
                return adjusted_amount, 'coin_deduction_adjusted'

        fallback_amount = fallback_result.get('amount')
        fallback_source = fallback_result.get('amount_source') or ''
        fallback_amount_value = self._parse_amount_value(fallback_amount)
        trusted_fallback_sources = {
            'text_keyword_high',
            'text_keyword_low',
            'semantic_keyword_high',
            'semantic_keyword_low',
        }

        if (
            fallback_amount_value is not None and
            0 < fallback_amount_value < primary_amount_value and
            fallback_source in trusted_fallback_sources
        ):
            logger.info(
                f"检测到闲鱼币抵扣，使用文本实付金额覆盖原价: primary={primary_amount}, "
                f"fallback={fallback_amount}, fallback_source={fallback_source}, source={primary_source}"
            )
            return fallback_amount, f'coin_deduction_{fallback_source}'

        return None, None

    async def _get_element_amount_context(self, element) -> str:
        """获取金额元素的局部上下文，用于判断当前数字是否真的是订单金额。"""
        try:
            return await element.evaluate(
                """(el) => {
                    const texts = [];
                    let current = el;
                    for (let i = 0; current && i < 4; i += 1, current = current.parentElement) {
                        const text = String(current.innerText || current.textContent || '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        if (!text) {
                            continue;
                        }
                        texts.push(text);
                        if (text.length >= 24) {
                            break;
                        }
                    }
                    return texts.join(' | ').slice(0, 240);
                }"""
            )
        except Exception as e:
            logger.debug(f"获取金额元素上下文失败: {e}")
            return ''

    async def _extract_amount_from_selectors(self) -> Tuple[Optional[str], str]:
        amount_selectors = [
            '.boldNum--JgEOXfA3',
            '[class*="boldNum"]',
            '[class*="pay"] [class*="num"]',
            '[class*="amount"] [class*="num"]',
            '[class*="price"] [class*="num"]',
        ]

        for amount_selector in amount_selectors:
            try:
                amount_elements = await self.page.query_selector_all(amount_selector)
            except Exception as selector_e:
                logger.debug(f"金额选择器 {amount_selector} 解析失败: {selector_e}")
                continue

            for amount_element in amount_elements:
                try:
                    amount_text = await amount_element.text_content()
                except Exception as text_error:
                    logger.debug(f"读取金额元素文本失败 {amount_selector}: {text_error}")
                    continue

                normalized_amount = self._normalize_amount_text(amount_text or '')
                if normalized_amount is None:
                    continue

                context_text = await self._get_element_amount_context(amount_element)
                context_amount, context_source = self._extract_preferred_amount_from_text(context_text)
                selector_lower = amount_selector.lower()
                is_generic_selector = (
                    'price' in selector_lower and
                    'pay' not in selector_lower and
                    'amount' not in selector_lower and
                    'boldnum' not in selector_lower
                )

                if context_amount and context_amount != normalized_amount:
                    logger.info(
                        f"金额候选与上下文主金额不一致，跳过: selector={amount_selector}, "
                        f"element={normalized_amount}, context={context_amount}, context_source={context_source}"
                    )
                    continue

                if is_generic_selector and not context_amount:
                    logger.info(
                        f"通用价格选择器缺少可信上下文，跳过金额候选: "
                        f"selector={amount_selector}, element={normalized_amount}"
                    )
                    continue

                if context_amount:
                    amount_source = f'selector_{context_source}'
                else:
                    amount_source = 'selector_direct'

                logger.info(f"通过选择器 {amount_selector} 找到金额: {normalized_amount} (source={amount_source})")
                return normalized_amount, amount_source

        return None, 'unknown'

    def _is_datetime_like(self, text: str) -> bool:
        """判断文本是否明显像时间/日期，而非规格。"""
        if not text:
            return False
        normalized = str(text).strip()
        if not normalized:
            return False

        datetime_patterns = [
            r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}$',
            r'^\d{1,2}:\d{2}(:\d{2})?$',
            r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(:\d{2})?$',
            r'^\d{10,13}$',
        ]
        return any(re.match(pattern, normalized) for pattern in datetime_patterns)

    def _is_text_fallback_spec_name_like(self, spec_name: str) -> bool:
        """校验纯文本兜底中的规格名称是否像真实SKU字段。"""
        normalized = re.sub(r'\s+', '', (spec_name or '').strip())
        if not normalized:
            return False

        strict_patterns = [
            r'^(?:商品)?类型\d*$',
            r'^(?:商品)?规格\d*$',
            r'^版本(?:选择)?\d*$',
            r'^(?:商品)?分类$',
            r'^选区$',
            r'^区服$',
            r'^服区$',
            r'^分区$',
            r'^平台$',
            r'^系统$',
            r'^颜色$',
            r'^尺码$',
            r'^尺寸$',
            r'^套餐(?:类型)?$',
            r'^型号(?:选择)?$',
            r'^配置$',
            r'^容量$',
            r'^时长$',
            r'^面额$',
            r'^账号(?:类型)?$',
            r'^远程$',
            r'^语言$',
            r'^发货方式$',
            r'^安装方式$',
            r'^接口$',
            r'^地区$',
            r'^区域$',
            r'^省份$',
            r'^城市$',
            r'^选项\d*$',
            r'^属性\d*$',
            r'^服务器$',
            r'^角色$',
            r'^职业$',
            r'^档位$',
        ]
        return any(re.match(pattern, normalized, re.IGNORECASE) for pattern in strict_patterns)

    def _is_valid_spec_candidate(self, spec_name: str, spec_value: str, *, strict: bool = False) -> bool:
        """校验规格候选是否可信，过滤备案信息/时间等误命中。"""
        name = (spec_name or '').strip()
        value = (spec_value or '').strip()

        if not name or not value:
            return False

        # 键名过长通常是正文信息，不是规格名称
        if len(name) > 20:
            return False

        # 时间戳/日期误识别
        if self._is_datetime_like(name) or self._is_datetime_like(value):
            return False

        # URL/协议字段不是规格
        invalid_protocol_tokens = ['http://', 'https://', 'fleamarket://']
        if any(token in name.lower() for token in invalid_protocol_tokens):
            return False
        if any(token in value.lower() for token in invalid_protocol_tokens):
            return False

        # 过滤常见平台资质、订单流程字段
        invalid_tokens = [
            '统一社会信用代码', '许可证', '备案', '经营', '广播电视节目',
            '营业性演出', '集邮市场', '增值电信', 'app备案号',
            '订单号', '付款', '交易', '退款', '发货', '收货',
            '买家', '卖家', '地址', '电话', '手机号', '快递', '物流',
            '创建时间', '付款时间', '成交时间', '下单时间'
        ]
        lower_name = name.lower()
        lower_value = value.lower()
        if any(token in lower_name for token in invalid_tokens):
            return False
        if any(token in lower_value for token in invalid_tokens):
            return False

        if strict and not (
            self._is_text_fallback_spec_name_like(name) or
            self._is_numeric_index_spec_name_like(name, value)
        ):
            return False

        return True

    def _sanitize_sku_result(self, sku_info: Dict[str, str], source: str = "unknown") -> Dict[str, str]:
        """清洗SKU结果中的可疑规格字段，避免误发。"""
        if not sku_info:
            return sku_info

        result = dict(sku_info)

        spec_name = (result.get('spec_name') or '').strip()
        spec_value = (result.get('spec_value') or '').strip()
        spec_name_2 = (result.get('spec_name_2') or '').strip()
        spec_value_2 = (result.get('spec_value_2') or '').strip()

        strict_validation = source.startswith('text_fallback')
        primary_valid = self._is_valid_spec_candidate(spec_name, spec_value, strict=strict_validation)
        secondary_valid = self._is_valid_spec_candidate(spec_name_2, spec_value_2, strict=strict_validation) if (spec_name_2 or spec_value_2) else False

        if not primary_valid and (spec_name or spec_value):
            logger.warning(
                f"过滤疑似误识别规格(primary, source={source}): {spec_name}:{spec_value}"
            )
            result.pop('spec_name', None)
            result.pop('spec_value', None)

        if not secondary_valid and (spec_name_2 or spec_value_2):
            logger.warning(
                f"过滤疑似误识别规格(secondary, source={source}): {spec_name_2}:{spec_value_2}"
            )
            result.pop('spec_name_2', None)
            result.pop('spec_value_2', None)

        # 如果主规格被清掉而次规格有效，则提升次规格为主规格
        if ('spec_name' not in result or not result.get('spec_name')) and result.get('spec_name_2') and result.get('spec_value_2'):
            result['spec_name'] = result.pop('spec_name_2')
            result['spec_value'] = result.pop('spec_value_2')
            logger.info(f"规格清洗后提升次规格为主规格(source={source})")

        return result

    def _get_status_priority(self, status: str) -> int:
        priority_map = {
            'unknown': 0,
            'pending_payment': 10,
            'pending_ship': 20,
            'shipped': 30,
            'completed': 40,
            'refunding': 50,
            'cancelled': 60,
        }
        return priority_map.get(status or 'unknown', 0)

    def _extract_status_matches_from_text(self, text: str, *, source: str = 'generic') -> Dict[str, list]:
        """从文本中提取状态命中详情，便于按来源做更保守的判定。"""
        if not text:
            return {}

        normalized_text = re.sub(r'\s+', ' ', str(text)).strip()
        if not normalized_text:
            return {}

        status_patterns = [
            ('cancelled', ['交易关闭', '已关闭', '钱款已原路退返', '订单关闭']),
            ('refunding', ['退款中', '退货退款', '退款关闭']),
            ('completed', ['买家确认收货', '已确认收货，交易成功', '交易成功', '已完成']),
            ('shipped', ['等待买家收货', '待收货', '已发货', '查看物流', '确认收货']),
            ('pending_ship', ['待发货', '等待你发货', '等待卖家发货', '去发货', '付款完成待发货', '记得及时发货']),
            ('pending_payment', ['待付款', '等待买家付款']),
        ]

        if source == 'button':
            status_patterns = [
                ('cancelled', ['关闭订单', '订单关闭']),
                ('refunding', ['退款中', '退款详情']),
                ('completed', ['交易成功', '已完成']),
                ('shipped', ['提醒收货', '延长收货', '查看物流', '已发货', '确认收货']),
                ('pending_ship', ['去发货', '立即发货', '待发货']),
                ('pending_payment', ['修改价格', '等待付款']),
            ]

        if source == 'body':
            status_patterns = [
                ('cancelled', ['交易关闭', '已关闭', '钱款已原路退返', '订单关闭']),
                ('refunding', ['退款中', '退货退款', '退款关闭']),
                ('completed', ['买家已确认收货', '买家确认收货，交易成功', '已确认收货，交易成功']),
                ('shipped', ['等待买家收货', '提醒收货', '延长收货']),
                ('pending_ship', ['待发货', '等待你发货', '等待卖家发货', '去发货', '付款完成待发货', '记得及时发货']),
                ('pending_payment', ['待付款', '等待买家付款']),
            ]

        if source == 'button_group':
            status_patterns = [
                ('cancelled', ['关闭订单', '订单关闭']),
                ('refunding', ['退款中', '退款详情']),
                ('completed', ['交易成功', '已完成']),
                ('shipped', ['提醒收货', '延长收货', '查看物流', '已发货', '确认收货']),
                ('pending_ship', ['去发货', '立即发货', '待发货']),
                ('pending_payment', ['修改价格', '等待付款']),
            ]

        matched_statuses: Dict[str, list] = {}
        for status, patterns in status_patterns:
            matched_patterns = [pattern for pattern in patterns if pattern in normalized_text]
            if matched_patterns:
                matched_statuses[status] = matched_patterns

        if source == 'button_group':
            completed_signals = []
            if '去评价' in normalized_text:
                completed_signals.append('去评价')
            if '查看钱款' in normalized_text:
                completed_signals.append('查看钱款')
            if '删除订单' in normalized_text:
                completed_signals.append('删除订单')

            if {'去评价', '查看钱款'}.issubset(set(completed_signals)):
                matched_statuses['completed'] = completed_signals

        if source == 'body':
            completed_signals = []
            if '快给ta一个评价吧~' in normalized_text or '快给ta一个评价吧～' in normalized_text:
                completed_signals.append('快给ta一个评价吧')
            if '查看钱款' in normalized_text:
                completed_signals.append('查看钱款')
            if '去评价' in normalized_text:
                completed_signals.append('去评价')

            if '快给ta一个评价吧' in ''.join(completed_signals) and ('查看钱款' in completed_signals or '去评价' in completed_signals):
                matched_statuses['completed'] = completed_signals

        return matched_statuses

    def _extract_status_from_text(self, text: str, *, source: str = 'generic') -> str:
        """从任意文本中提取订单状态，优先返回更可靠/更后置的状态。"""
        matched_status_map = self._extract_status_matches_from_text(text, source=source)
        if not matched_status_map:
            return 'unknown'

        if source == 'body':
            if 'completed' in matched_status_map and 'shipped' in matched_status_map:
                logger.warning(
                    f"订单状态全文兜底同时命中已发货/已完成信号，优先采用shipped: "
                    f"completed={matched_status_map.get('completed')}, "
                    f"shipped={matched_status_map.get('shipped')}"
                )
                return 'shipped'

            if 'pending_ship' in matched_status_map and 'shipped' in matched_status_map:
                logger.warning(
                    f"订单状态全文兜底出现冲突信号，保守返回unknown: "
                    f"pending_ship={matched_status_map.get('pending_ship')}, "
                    f"shipped={matched_status_map.get('shipped')}"
                )
                return 'unknown'

            if 'pending_ship' in matched_status_map and 'pending_payment' in matched_status_map:
                logger.info(
                    f"订单状态全文兜底检测到待付款/待发货混合信号，优先采用pending_ship: "
                    f"pending_ship={matched_status_map.get('pending_ship')}, "
                    f"pending_payment={matched_status_map.get('pending_payment')}"
                )
                return 'pending_ship'

        matched_statuses = list(matched_status_map.keys())

        matched_statuses.sort(key=self._get_status_priority, reverse=True)
        return matched_statuses[0]

    async def _collect_texts_by_selectors(self, selectors, *, max_length: int = 40, max_items: int = 12) -> list:
        """按选择器批量采集文本，自动去重。"""
        collected = []
        seen = set()

        for selector in selectors:
            try:
                elements = await self.page.query_selector_all(selector)
            except Exception as e:
                logger.debug(f"批量采集选择器失败 {selector}: {e}")
                continue

            for element in elements:
                try:
                    text = await element.text_content()
                except Exception as text_error:
                    logger.debug(f"读取元素文本失败 {selector}: {text_error}")
                    continue

                normalized_text = re.sub(r'\s+', ' ', str(text or '')).strip()
                if not normalized_text:
                    continue
                if max_length and len(normalized_text) > max_length:
                    continue
                if normalized_text in seen:
                    continue

                seen.add(normalized_text)
                collected.append(normalized_text)
                if len(collected) >= max_items:
                    return collected

        return collected

    async def _get_page_text(self) -> str:
        """获取页面可读文本，失败时返回空字符串"""
        try:
            return (await self.page.inner_text('body')).strip()
        except Exception:
            try:
                html_content = await self.page.content()
                return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html_content)).strip()
            except Exception:
                return ''

    def _build_spec_candidate_identity(self, candidate: Dict[str, str]) -> Tuple[str, str, str, str]:
        """构建规格候选去重键，避免同一候选重复进入兜底流程。"""
        return (
            (candidate.get('spec_name') or '').strip(),
            (candidate.get('spec_value') or '').strip(),
            (candidate.get('spec_name_2') or '').strip(),
            (candidate.get('spec_value_2') or '').strip(),
        )

    def _classify_spec_parse_mode(self, sku_info: Optional[Dict[str, str]]) -> str:
        """根据当前SKU结果判断规格解析模式。"""
        info = sku_info or {}
        has_primary = bool((info.get('spec_name') or '').strip() and (info.get('spec_value') or '').strip())
        has_secondary = bool((info.get('spec_name_2') or '').strip() and (info.get('spec_value_2') or '').strip())

        if has_primary and has_secondary:
            return 'two_spec'
        if has_primary:
            return 'one_spec'
        return 'no_spec'

    def _extract_sku_from_text(self, text: str) -> Dict[str, str]:
        """从页面纯文本中兜底提取金额/规格/数量"""
        result: Dict[str, str] = {}
        if not text:
            return result

        lines = [line.strip() for line in text.splitlines() if line and line.strip()]

        # 优先从金额关键词行提取金额
        amount_keywords = ['实付款', '订单金额', '实收', '合计', '总价', '应付', '支付金额', '实付']
        for line in lines:
            if any(keyword in line for keyword in amount_keywords):
                normalized_amount, amount_source = self._extract_preferred_amount_from_text(line)
                if normalized_amount:
                    result['amount'] = normalized_amount
                    result['amount_source'] = f'text_{amount_source}'
                    break

        # 兜底：从全文提取货币数字
        if 'amount' not in result:
            normalized_amount, amount_source = self._extract_preferred_amount_from_text(text)
            if normalized_amount:
                result['amount'] = normalized_amount
                result['amount_source'] = f'text_{amount_source}'

        # 数量提取
        quantity_patterns = [
            r'数量\s*[:：]?\s*x?\s*(\d+)',
            r'\bx\s*(\d{1,3})\b',
        ]
        for pattern in quantity_patterns:
            quantity_match = re.search(pattern, text, re.IGNORECASE)
            if quantity_match:
                result['quantity'] = quantity_match.group(1)
                break

        # 规格提取：过滤明显非规格行
        spec_candidates = []
        spec_candidate_keys = set()
        ignore_tokens = [
            'http://', 'https://', 'fleamarket://', '订单', '买家', '卖家', '地址',
            '手机', '电话', '时间', '发货', '付款', '交易', '退款', '去发货', '修改价格',
            '等待你发货', '等待买家', '已发货', '待收货', '待发货',
            '统一社会信用代码', '许可证', '备案', '经营', '广播电视节目',
            '营业性演出', '集邮市场', '增值电信', 'app备案号'
        ]

        for line in lines:
            normalized_line = line.replace('：', ':')
            if ':' not in normalized_line:
                continue
            if any(token in normalized_line for token in ignore_tokens):
                continue

            left, right = normalized_line.split(':', 1)
            left = left.strip()
            right = right.strip()
            if not left or not right:
                continue
            if len(left) > 16:
                continue

            parsed = self._parse_sku_content(f"{left}:{right}")
            if parsed:
                sanitized_candidate = self._sanitize_sku_result(parsed, source="text_fallback_candidate")
                if sanitized_candidate.get('spec_name') and sanitized_candidate.get('spec_value'):
                    candidate_key = self._build_spec_candidate_identity(sanitized_candidate)
                    if candidate_key not in spec_candidate_keys:
                        spec_candidate_keys.add(candidate_key)
                        spec_candidates.append(sanitized_candidate)

        if spec_candidates:
            explicit_multi_spec_candidates = [
                candidate for candidate in spec_candidates
                if candidate.get('spec_name_2') and candidate.get('spec_value_2')
            ]

            selected_candidate = None
            if len(explicit_multi_spec_candidates) == 1:
                selected_candidate = explicit_multi_spec_candidates[0]
            elif len(spec_candidates) == 1:
                selected_candidate = spec_candidates[0]
            else:
                logger.warning(
                    "SKU文本兜底检测到多个规格候选，判定为歧义并跳过规格字段: "
                    f"{[self._build_spec_candidate_identity(candidate) for candidate in spec_candidates]}"
                )

            if selected_candidate:
                if selected_candidate.get('spec_name') and selected_candidate.get('spec_value'):
                    result['spec_name'] = selected_candidate['spec_name']
                    result['spec_value'] = selected_candidate['spec_value']
                if selected_candidate.get('spec_name_2') and selected_candidate.get('spec_value_2'):
                    result['spec_name_2'] = selected_candidate['spec_name_2']
                    result['spec_value_2'] = selected_candidate['spec_value_2']

        return self._sanitize_sku_result(result, source="text_fallback_result")

    def _is_order_detail_parse_success(self, sku_info: Optional[Dict[str, str]], order_status: str) -> bool:
        """判定订单详情解析是否成功（金额/规格/状态任一有效即可）"""
        info = sku_info or {}
        has_valid_amount = self._has_valid_amount(info.get('amount'))
        has_valid_spec = bool(info.get('spec_name') and info.get('spec_value'))
        has_valid_status = bool(order_status and order_status != 'unknown')
        return has_valid_amount or has_valid_spec or has_valid_status

    def _build_parse_field_flags(self, sku_info: Optional[Dict[str, str]], order_status: str) -> Dict[str, Any]:
        """构建解析字段完整性标记，便于统一告警日志检索。"""
        info = sku_info or {}
        return {
            'has_amount': self._has_valid_amount(info.get('amount')),
            'has_spec': bool(info.get('spec_name') and info.get('spec_value')),
            'has_status': bool(order_status and order_status != 'unknown'),
            'amount': info.get('amount', ''),
            'spec_name': info.get('spec_name', ''),
            'spec_value': info.get('spec_value', ''),
            'quantity': info.get('quantity', ''),
            'order_status': order_status or ''
        }

    def _log_order_detail_parse_event(
        self,
        event_name: str,
        order_id: str,
        url: str,
        attempt: str,
        sku_info: Optional[Dict[str, str]],
        order_status: str,
        level: str = "warning",
        error: str = None
    ) -> None:
        """输出结构化的订单详情解析告警/恢复日志。"""
        try:
            field_flags = self._build_parse_field_flags(sku_info, order_status)
            payload = {
                'event': event_name,
                'cookie_id': self.cookie_id_for_log,
                'order_id': order_id,
                'attempt': attempt,
                'url': url,
                'field_flags': field_flags
            }
            if error:
                payload['error'] = error

            log_msg = f"{event_name} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
            if level == "info":
                logger.info(log_msg)
            else:
                logger.warning(log_msg)
        except Exception as log_error:
            logger.warning(f"订单解析事件日志输出失败: {log_error}")

    async def _get_order_status(self) -> str:
        """
        从订单详情页面获取订单状态

        Returns:
            订单状态字符串，可能的值:
            - 'pending_payment': 待付款
            - 'pending_ship': 待发货
            - 'shipped': 已发货/待收货
            - 'completed': 交易成功
            - 'refunding': 退款中
            - 'cancelled': 交易关闭
            - 'unknown': 未知状态
        """
        try:
            self._last_order_status_source = 'unknown'
            if not await self._check_browser_status():
                logger.error("浏览器状态异常，无法获取订单状态")
                return 'unknown'

            # 尝试多种选择器获取订单状态
            status_selectors = [
                '.orderStatusText--F6eoVcHD',  # 常见的订单状态选择器
                '.order-status',
                '.status-text',
                '[class*="orderStatus"]',
                '[class*="StatusText"]',
                '[class*="status"]',
            ]

            status_text = ''
            for selector in status_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        text = await element.text_content()
                        if text:
                            status_text = text.strip()
                            logger.info(f"通过选择器 {selector} 获取到订单状态: {status_text}")
                            break
                except Exception as e:
                    logger.debug(f"选择器 {selector} 获取失败: {e}")
                    continue

            button_selectors = [
                'button',
                '[role="button"]',
                '[class*="button"]',
                '[class*="Button"]',
                '[class*="btn"]',
            ]

            parsed_from_selector = 'unknown'
            button_texts = await self._collect_texts_by_selectors(button_selectors, max_length=24, max_items=16)
            button_status = 'unknown'
            for button_text in button_texts:
                candidate_status = self._extract_status_from_text(button_text, source='button')
                if self._get_status_priority(candidate_status) > self._get_status_priority(button_status):
                    button_status = candidate_status

            button_group_status = 'unknown'
            if button_texts:
                button_group_status = self._extract_status_from_text(' | '.join(button_texts), source='button_group')
                if self._get_status_priority(button_group_status) > self._get_status_priority(button_status):
                    button_status = button_group_status

            # 先解析选择器结果
            if status_text:
                parsed_from_selector = self._extract_status_from_text(status_text, source='selector')
                if parsed_from_selector == 'unknown':
                    logger.warning(f"未知的订单状态文本: {status_text}")

            preferred_status = parsed_from_selector
            preferred_source = 'selector' if parsed_from_selector != 'unknown' else 'unknown'
            if self._get_status_priority(button_status) > self._get_status_priority(preferred_status):
                preferred_status = button_status
                preferred_source = 'button'

            logger.info(
                f"订单状态解析候选: selector={parsed_from_selector} ({status_text or 'empty'}), "
                f"button={button_status} ({button_texts or []}), button_group={button_group_status}"
            )

            if preferred_status != 'unknown':
                self._last_order_status_source = preferred_source
                logger.info(f"订单状态解析最终采用结构化结果: {preferred_status} (source={preferred_source})")
                return preferred_status

            # 如果选择器/按钮都没有有效结果，尝试从页面文本中提取
            body_text = await self._get_page_text()
            body_status = self._extract_status_from_text(body_text, source='body')
            logger.info(f"订单状态解析候选: body={body_status}")
            if body_status != 'unknown':
                self._last_order_status_source = 'body'
                logger.info(f"从页面文本中检测到订单状态 -> {body_status}")
                return body_status

            logger.warning("无法获取订单状态")
            return 'unknown'

        except Exception as e:
            logger.error(f"获取订单状态异常: {e}")
            return 'unknown'

    async def _get_sku_content(self) -> Optional[Dict[str, str]]:
        """获取并解析SKU内容，包括规格、数量和金额，支持双规格"""
        try:
            # 检查浏览器状态
            if not await self._check_browser_status():
                logger.error("浏览器状态异常，无法获取SKU内容")
                return {}

            result: Dict[str, str] = {}
            page_text = await self._get_page_text()
            fallback_result = self._extract_sku_from_text(page_text) if page_text else {}

            # 获取规格元素（主通道）
            sku_selector = '.sku--u_ddZval'
            sku_elements = await self.page.query_selector_all(sku_selector)
            logger.info(f"找到 {len(sku_elements)} 个 sku--u_ddZval 元素")

            # 获取金额：优先结构化响应/结构化页面内容，再尝试语义块，最后才走选择器兜底
            amount, amount_source = await self._extract_amount_from_structured_content()
            if amount is None:
                amount, amount_source = await self._extract_amount_from_semantic_blocks()
            if amount is None:
                amount, amount_source = await self._extract_amount_from_selectors()
            if amount is not None:
                result['amount'] = amount
                result['amount_source'] = amount_source

            adjusted_coin_amount, adjusted_coin_source = self._resolve_coin_deduction_amount(
                result.get('amount'),
                result.get('amount_source', ''),
                fallback_result,
                page_text,
            )
            if adjusted_coin_amount is not None:
                result['amount'] = adjusted_coin_amount
                result['amount_source'] = adjusted_coin_source

            structured_sku_result = await self._extract_sku_from_structured_content()
            if structured_sku_result:
                for key in ['spec_name', 'spec_value', 'spec_name_2', 'spec_value_2', 'quantity']:
                    if structured_sku_result.get(key):
                        result[key] = structured_sku_result[key]

            # 收集所有元素的内容
            all_contents = []
            for i, element in enumerate(sku_elements):
                content = await element.text_content()
                if content:
                    content = content.strip()
                    all_contents.append(content)
                    logger.info(f"元素 {i+1} 原始内容: {content}")

            # 分类：规格 vs 数量
            specs = []
            quantity_content = None

            for content in all_contents:
                if '数量' in content:
                    # 这是数量
                    quantity_content = content
                elif ':' in content:
                    # 这是规格（包含冒号的）
                    specs.append(content)
                else:
                    # 没有冒号也没有"数量"，可能是纯数字（如 x1）
                    if content.startswith('x') or content.isdigit():
                        quantity_content = content
                    else:
                        # 其他情况当作规格处理
                        specs.append(content)

            # 解析规格1（主通道）
            if len(specs) >= 1:
                parsed_spec = self._parse_sku_content(specs[0])
                if parsed_spec:
                    result['spec_name'] = parsed_spec['spec_name']
                    result['spec_value'] = parsed_spec['spec_value']

                    # 检查第一个规格是否已包含双规格（分号分隔的情况）
                    if 'spec_name_2' in parsed_spec and 'spec_value_2' in parsed_spec:
                        result['spec_name_2'] = parsed_spec['spec_name_2']
                        result['spec_value_2'] = parsed_spec['spec_value_2']

            # 解析规格2（如果存在且尚未从分号分隔中获取）
            if len(specs) >= 2 and 'spec_name_2' not in result:
                parsed_spec2 = self._parse_sku_content(specs[1])
                if parsed_spec2:
                    result['spec_name_2'] = parsed_spec2['spec_name']
                    result['spec_value_2'] = parsed_spec2['spec_value']

            # 如果有更多规格，记录日志（目前只支持双规格）
            if len(specs) > 2:
                logger.warning(f"检测到 {len(specs)} 个规格，目前只支持双规格，多余的规格将被忽略")

            # 解析数量
            if quantity_content:
                logger.info(f"数量原始内容: {quantity_content}")

                if ':' in quantity_content:
                    quantity_value = quantity_content.split(':', 1)[1].strip()
                else:
                    quantity_value = quantity_content

                # 去掉数量值前面的 'x' 符号（如 "x2" -> "2"）
                if quantity_value.startswith('x'):
                    quantity_value = quantity_value[1:]

                result['quantity'] = quantity_value
                logger.info(f"提取到数量: {quantity_value}")

            # 如果核心字段缺失，使用页面文本兜底；规格字段仅在主通道缺失主规格时才整体补齐
            fallback_used = False
            if 'amount' not in result and fallback_result.get('amount'):
                result['amount'] = fallback_result['amount']
                fallback_used = True
            if 'amount_source' not in result and fallback_result.get('amount_source'):
                result['amount_source'] = fallback_result['amount_source']
                fallback_used = True

            has_primary_spec = bool(result.get('spec_name') and result.get('spec_value'))
            if not has_primary_spec and fallback_result.get('spec_name') and fallback_result.get('spec_value'):
                result['spec_name'] = fallback_result['spec_name']
                result['spec_value'] = fallback_result['spec_value']
                fallback_used = True

                if fallback_result.get('spec_name_2') and fallback_result.get('spec_value_2'):
                    result['spec_name_2'] = fallback_result['spec_name_2']
                    result['spec_value_2'] = fallback_result['spec_value_2']
            elif has_primary_spec and fallback_result.get('spec_name_2') and fallback_result.get('spec_value_2'):
                same_primary_spec = (
                    (result.get('spec_name') or '').strip() == (fallback_result.get('spec_name') or '').strip()
                    and (result.get('spec_value') or '').strip() == (fallback_result.get('spec_value') or '').strip()
                )

                if same_primary_spec:
                    result['spec_name_2'] = fallback_result['spec_name_2']
                    result['spec_value_2'] = fallback_result['spec_value_2']
                    fallback_used = True
                    logger.info(
                        "主通道与文本兜底主规格一致，补齐第二规格: "
                        f"{fallback_result.get('spec_name_2')}:{fallback_result.get('spec_value_2')}"
                    )
                else:
                    logger.warning(
                        "主通道已获取主规格，忽略文本兜底补入的不一致第二规格，避免单规格订单被误判为双规格: "
                        f"primary={result.get('spec_name')}:{result.get('spec_value')}, "
                        f"fallback={fallback_result.get('spec_name')}:{fallback_result.get('spec_value')}, "
                        f"secondary={fallback_result.get('spec_name_2')}:{fallback_result.get('spec_value_2')}"
                    )

            if 'quantity' not in result and fallback_result.get('quantity'):
                result['quantity'] = fallback_result['quantity']
                fallback_used = True

            if fallback_result and fallback_used:
                logger.info(f"SKU文本兜底解析结果: {fallback_result}")

            # 确保数量字段存在，如果不存在则设置为1
            if 'quantity' not in result:
                result['quantity'] = '1'
                logger.info("未获取到数量信息，默认设置为1")

            # 对最终规格做二次清洗，防止主通道/兜底误识别正文字段
            cleaned_result = self._sanitize_sku_result(result, source="sku_final")
            if cleaned_result != result:
                logger.warning(f"SKU结果已清洗: before={result}, after={cleaned_result}")
            result = cleaned_result

            # 打印最终结果
            if result:
                logger.info(f"最终解析结果: {result}")
                return result
            else:
                logger.warning("未能解析到任何有效信息")
                # 即使没有其他信息，也要返回默认数量
                return {'quantity': '0'}

        except Exception as e:
            logger.error(f"获取SKU内容失败: {e}")
            return {}

    async def _check_browser_status(self) -> bool:
        """检查浏览器状态是否正常"""
        try:
            if not self.browser or not self.context or not self.page:
                logger.warning("浏览器组件不完整")
                return False

            # 检查浏览器是否已连接
            if self.browser.is_connected():
                # 尝试获取页面标题来验证页面是否可用
                await self.page.title()
                return True
            else:
                logger.warning("浏览器连接已断开")
                return False
        except Exception as e:
            logger.warning(f"浏览器状态检查失败: {e}")
            return False

    async def _ensure_browser_ready(self) -> bool:
        """确保浏览器准备就绪，如果不可用则重新初始化"""
        try:
            if await self._check_browser_status():
                return True

            logger.info("浏览器状态异常，尝试重新初始化...")

            # 先尝试关闭现有的浏览器实例
            await self._force_close_browser()

            # 重新初始化浏览器
            await self.init_browser()

            # 等待更长时间确保浏览器完全就绪
            await asyncio.sleep(2)

            # 再次检查状态
            if await self._check_browser_status():
                logger.info("浏览器重新初始化成功")
                return True
            else:
                logger.error("浏览器重新初始化失败")
                return False

        except Exception as e:
            logger.error(f"确保浏览器就绪失败: {e}")
            return False

    async def _force_close_browser(self):
        """强制关闭浏览器，忽略所有错误"""
        try:
            self._clear_response_capture_handler()
            if self.page:
                try:
                    await self.page.close()
                except:
                    pass
                self.page = None

            if self.context:
                try:
                    await self.context.close()
                except:
                    pass
                self.context = None

            if self.browser:
                try:
                    await self.browser.close()
                except:
                    pass
                self.browser = None

            self._active_order_id = ''

        except Exception as e:
            logger.debug(f"强制关闭浏览器过程中的异常（可忽略）: {e}")

    async def close(self):
        """关闭浏览器"""
        try:
            await self._wait_for_response_capture_tasks(timeout=0.2)
            self._clear_response_capture_handler()
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            self._active_order_id = ''
            logger.info("浏览器已关闭")
        except Exception as e:
            logger.error(f"关闭浏览器失败: {e}")
            # 如果正常关闭失败，尝试强制关闭
            await self._force_close_browser()

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.init_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()


# 便捷函数
async def fetch_order_detail_simple(
    order_id: str,
    cookie_string: str = None,
    headless: bool = True,
    force_refresh: bool = False,
    cookie_id_for_log: str = "unknown"
) -> Optional[Dict[str, Any]]:
    """
    简单的订单详情获取函数（优化版：先检查数据库，再初始化浏览器）

    Args:
        order_id: 订单ID
        cookie_string: Cookie字符串，如果不提供则使用默认值
        headless: 是否无头模式
        force_refresh: 是否强制刷新（跳过缓存直接从闲鱼获取）
        cookie_id_for_log: 日志上下文中的账号ID，用于定位异常账号

    Returns:
        订单详情字典，包含以下字段：
        - order_id: 订单ID
        - url: 订单详情页面URL
        - title: 页面标题
        - sku_info: 完整的SKU信息字典
        - spec_name: 规格名称
        - spec_value: 规格值
        - quantity: 数量
        - amount: 金额
        - order_status: 订单状态
        - timestamp: 获取时间戳
        失败时返回None
    """
    # 如果不是强制刷新，先检查数据库中是否有有效数据
    if not force_refresh:
        try:
            from db_manager import db_manager
            existing_order = db_manager.get_order_by_id(order_id)

            if existing_order:
                amount = existing_order.get('amount', '')
                item_config = None
                if existing_order.get('item_id') and existing_order.get('cookie_id'):
                    item_config = db_manager.get_item_info(existing_order.get('cookie_id'), existing_order.get('item_id'))

                if _should_use_cached_order(existing_order, item_config=item_config):
                    logger.info(f"📋 订单 {order_id} 已存在于数据库中且金额有效({amount})，直接返回缓存数据")
                    print(f"✅ 订单 {order_id} 使用缓存数据，跳过浏览器获取")

                    # 构建返回格式
                    result = {
                        'order_id': existing_order['order_id'],
                        'url': f"https://www.goofish.com/order-detail?orderId={order_id}&role=seller",
                        'title': f"订单详情 - {order_id}",
                        'sku_info': {
                            'spec_name': existing_order.get('spec_name', ''),
                            'spec_value': existing_order.get('spec_value', ''),
                            'spec_name_2': existing_order.get('spec_name_2', ''),
                        'spec_value_2': existing_order.get('spec_value_2', ''),
                        'quantity': existing_order.get('quantity', ''),
                        'amount': existing_order.get('amount', ''),
                        'amount_source': 'cache'
                    },
                    'spec_name': existing_order.get('spec_name', ''),
                    'spec_value': existing_order.get('spec_value', ''),
                    'spec_name_2': existing_order.get('spec_name_2', ''),
                    'spec_value_2': existing_order.get('spec_value_2', ''),
                    'quantity': existing_order.get('quantity', ''),
                    'amount': existing_order.get('amount', ''),
                    'amount_source': 'cache',
                    'order_status': existing_order.get('order_status', 'unknown'),  # 添加订单状态
                    'order_status_source': 'cache',
                    'timestamp': time.time(),
                    'from_cache': True
                    }
                    return result
                else:
                    logger.info(f"📋 订单 {order_id} 缓存字段不完整或状态无效，重新获取详情: amount={amount}, status={existing_order.get('order_status')}")
                    print(f"⚠️ 订单 {order_id} 缓存不满足复用条件，重新获取详情...")
        except Exception as e:
            logger.warning(f"检查数据库缓存失败: {e}")
    else:
        logger.info(f"🔄 订单 {order_id} 强制刷新，跳过缓存检查")
        print(f"🔄 订单 {order_id} 强制刷新模式...")

    # 数据库中没有有效数据，使用浏览器获取
    logger.info(f"🌐 订单 {order_id} 需要浏览器获取，开始初始化浏览器...")
    print(f"🔍 订单 {order_id} 开始浏览器获取详情...")

    fetcher = OrderDetailFetcher(cookie_string, headless, cookie_id_for_log=cookie_id_for_log)
    try:
        if await fetcher.init_browser(headless=headless):
            return await fetcher.fetch_order_detail(order_id, force_refresh=force_refresh)
    finally:
        await fetcher.close()
    return None


# 测试代码
if __name__ == "__main__":
    async def test():
        # 测试订单ID
        test_order_id = "2856024697612814489"
        
        print(f"🔍 开始获取订单详情: {test_order_id}")
        
        result = await fetch_order_detail_simple(test_order_id, headless=False)
        
        if result:
            print("✅ 订单详情获取成功:")
            print(f"📋 订单ID: {result['order_id']}")
            print(f"🌐 URL: {result['url']}")
            print(f"📄 页面标题: {result['title']}")
            print(f"🛍️ 规格名称: {result.get('spec_name', '未获取到')}")
            print(f"📝 规格值: {result.get('spec_value', '未获取到')}")
            print(f"🔢 数量: {result.get('quantity', '未获取到')}")
            print(f"💰 金额: {result.get('amount', '未获取到')}")
        else:
            print("❌ 订单详情获取失败")
    
    # 运行测试
    asyncio.run(test())
