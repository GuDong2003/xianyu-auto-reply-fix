import json
import time
from typing import Any, Dict, List, Optional

import aiohttp
from loguru import logger

from utils.order_detail_fetcher import OrderDetailFetcher
from utils.time_utils import parse_db_timestamp, parse_local_datetime_text_to_db_utc
from utils.xianyu_utils import generate_sign, trans_cookies


ORDER_LIST_API_URL = 'https://h5api.m.goofish.com/h5/mtop.taobao.idle.trade.merchant.sold.get/1.0/'
ORDER_LIST_API_NAME = 'mtop.taobao.idle.trade.merchant.sold.get'
ORDER_LIST_REFERER = 'https://seller.goofish.com/?site=COMMONPRO#/seller-trade/order-manage'
ORDER_LIST_QUERY_CODE = 'ALL'
DEFAULT_PAGE_SIZE = 20
ORDER_HISTORY_ANCHOR_FIELDS = (
    'platform_paid_at',
    'platform_created_at',
    'platform_completed_at',
)

ORDER_STATUS_ALIASES = {
    'processing': 'processing',
    'pending_payment': 'pending_payment',
    'pending_ship': 'pending_ship',
    'partial_success': 'partial_success',
    'partial_pending_finalize': 'partial_pending_finalize',
    'shipped': 'shipped',
    'completed': 'completed',
    'refunding': 'refunding',
    'refund_cancelled': 'refund_cancelled',
    'cancelled': 'cancelled',
    'unknown': 'unknown',
    '处理中': 'processing',
    '待付款': 'pending_payment',
    '待发货': 'pending_ship',
    '部分发货': 'partial_success',
    '部分待收尾': 'partial_pending_finalize',
    '已发货': 'shipped',
    '交易成功': 'completed',
    '已完成': 'completed',
    '退款中': 'refunding',
    '退款撤销': 'refund_cancelled',
    '交易关闭': 'cancelled',
    '已关闭': 'cancelled',
}


class OrderHistorySyncError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = 'api_error',
        ret_value: Any = None,
        guidance: str = '',
    ):
        super().__init__(message)
        self.kind = kind
        self.ret_value = ret_value
        self.guidance = guidance


def _ret_value_to_text(ret_value: Any) -> str:
    if isinstance(ret_value, str):
        return ret_value
    if isinstance(ret_value, (list, tuple)):
        return ' | '.join(str(item) for item in ret_value)
    if isinstance(ret_value, dict):
        try:
            return json.dumps(ret_value, ensure_ascii=False)
        except Exception:
            return str(ret_value)
    return str(ret_value or '')


def classify_order_history_api_error(ret_value: Any, cookie_id: str = 'unknown') -> OrderHistorySyncError:
    ret_text = _ret_value_to_text(ret_value)
    ret_text_lower = ret_text.lower()
    account_label = cookie_id or 'unknown'

    session_keywords = (
        '令牌过期',
        'session过期',
        'session expired',
        'FAIL_SYS_USER_VALIDATE',
        'FAIL_SYS_TOKEN_EXPIRED',
        'FAIL_SYS_TOKEN_EXOIRED',
        'FAIL_SYS_SESSION_EXPIRED',
        'passport.goofish.com',
        'mini_login',
        'login',
    )
    permission_keywords = (
        'PERMISSION_EXCEPTION',
        '无权限访问',
        'permission denied',
        'permission',
    )

    if any(keyword.lower() in ret_text_lower for keyword in session_keywords):
        guidance = '请先对该账号执行手动刷新 Cookie 或重新扫码登录；若仍失败，导入浏览器中人工登录后的完整 Cookie 后再同步。'
        return OrderHistorySyncError(
            f'【{account_label}】历史订单列表 Cookie/Session 已过期，无法同步历史订单。平台返回: {ret_text}',
            kind='session_expired',
            ret_value=ret_value,
            guidance=guidance,
        )

    if any(keyword.lower() in ret_text_lower for keyword in permission_keywords):
        guidance = '请确认该账号可以在闲鱼/鱼店长卖家中心打开订单管理页；如果网页端也无权限，该接口无法由程序侧修复。'
        return OrderHistorySyncError(
            f'【{account_label}】账号暂无订单列表访问权限，无法同步历史订单。平台返回: {ret_text}',
            kind='permission_denied',
            ret_value=ret_value,
            guidance=guidance,
        )

    return OrderHistorySyncError(
        f'【{account_label}】历史订单列表 API 调用失败: {ret_text}',
        kind='api_error',
        ret_value=ret_value,
        guidance='请稍后重试；如果持续失败，请查看日志中的平台返回值和账号 Cookie 状态。',
    )


def normalize_order_history_status(value: Any) -> Optional[str]:
    text = str(value or '').strip()
    if not text:
        return None

    normalized = ORDER_STATUS_ALIASES.get(text)
    if normalized:
        return normalized

    return ORDER_STATUS_ALIASES.get(text.lower())


class OrderHistorySyncError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = 'api_error',
        ret_value: Any = None,
        guidance: str = '',
    ):
        super().__init__(message)
        self.kind = kind
        self.ret_value = ret_value
        self.guidance = guidance


def _ret_value_to_text(ret_value: Any) -> str:
    if isinstance(ret_value, str):
        return ret_value
    if isinstance(ret_value, (list, tuple)):
        return ' | '.join(str(item) for item in ret_value)
    if isinstance(ret_value, dict):
        try:
            return json.dumps(ret_value, ensure_ascii=False)
        except Exception:
            return str(ret_value)
    return str(ret_value or '')


def classify_order_history_api_error(ret_value: Any, cookie_id: str = 'unknown') -> OrderHistorySyncError:
    ret_text = _ret_value_to_text(ret_value)
    ret_text_lower = ret_text.lower()
    account_label = cookie_id or 'unknown'

    session_keywords = (
        '令牌过期',
        'session过期',
        'session expired',
        'FAIL_SYS_USER_VALIDATE',
        'FAIL_SYS_TOKEN_EXPIRED',
        'FAIL_SYS_TOKEN_EXOIRED',
        'FAIL_SYS_SESSION_EXPIRED',
        'passport.goofish.com',
        'mini_login',
        'login',
    )
    permission_keywords = (
        'PERMISSION_EXCEPTION',
        '无权限访问',
        'permission denied',
        'permission',
    )

    if any(keyword.lower() in ret_text_lower for keyword in session_keywords):
        guidance = '请先对该账号执行手动刷新 Cookie 或重新扫码登录；若仍失败，导入浏览器中人工登录后的完整 Cookie 后再同步。'
        return OrderHistorySyncError(
            f'【{account_label}】历史订单列表 Cookie/Session 已过期，无法同步历史订单。平台返回: {ret_text}',
            kind='session_expired',
            ret_value=ret_value,
            guidance=guidance,
        )

    if any(keyword.lower() in ret_text_lower for keyword in permission_keywords):
        guidance = '请确认该账号可以在闲鱼/鱼店长卖家中心打开订单管理页；如果网页端也无权限，该接口无法由程序侧修复。'
        return OrderHistorySyncError(
            f'【{account_label}】账号暂无订单列表访问权限，无法同步历史订单。平台返回: {ret_text}',
            kind='permission_denied',
            ret_value=ret_value,
            guidance=guidance,
        )

    return OrderHistorySyncError(
        f'【{account_label}】历史订单列表 API 调用失败: {ret_text}',
        kind='api_error',
        ret_value=ret_value,
        guidance='请稍后重试；如果持续失败，请查看日志中的平台返回值和账号 Cookie 状态。',
    )


def normalize_history_amount(value: Any) -> Optional[str]:
    text = str(value or '').strip()
    if not text:
        return None

    cleaned = text.replace('¥', '').replace('￥', '').replace(',', '').strip()
    try:
        return f"{float(cleaned):.2f}"
    except (TypeError, ValueError):
        return None


def resolve_order_history_anchor_time(candidate: Dict[str, Any]) -> Optional[str]:
    if not isinstance(candidate, dict):
        return None

    for field_name in ORDER_HISTORY_ANCHOR_FIELDS:
        value = str(candidate.get(field_name) or '').strip()
        if value:
            return value
    return None


def classify_order_history_range(
    anchor_time: Optional[str],
    utc_start: Optional[str] = None,
    utc_end_exclusive: Optional[str] = None,
) -> str:
    if not utc_start or not utc_end_exclusive:
        return 'in_range'

    anchor_dt = parse_db_timestamp(anchor_time) if anchor_time else None
    start_dt = parse_db_timestamp(utc_start)
    end_dt = parse_db_timestamp(utc_end_exclusive)
    if not anchor_dt or not start_dt or not end_dt:
        return 'unknown'
    if anchor_dt < start_dt:
        return 'before'
    if anchor_dt >= end_dt:
        return 'after'
    return 'in_range'


def _cookie_dict_to_string(cookies_dict: Dict[str, str]) -> str:
    return '; '.join(
        f'{name}={value}'
        for name, value in cookies_dict.items()
        if str(name).strip() and value is not None
    )


def _extract_set_cookie_updates(response_headers) -> Dict[str, str]:
    try:
        set_cookie_values = response_headers.getall('Set-Cookie', [])
    except Exception:
        raw_value = response_headers.get('Set-Cookie')
        if isinstance(raw_value, list):
            set_cookie_values = raw_value
        elif raw_value:
            set_cookie_values = [raw_value]
        else:
            set_cookie_values = []

    updates: Dict[str, str] = {}
    for cookie in set_cookie_values:
        if '=' not in cookie:
            continue
        try:
            name, value = cookie.split(';', 1)[0].split('=', 1)
        except ValueError:
            continue
        updates[name.strip()] = value.strip()
    return updates


class OrderHistoryPageFetcher:
    def __init__(self, cookie_string: str, cookie_id_for_log: str = 'unknown', headless: bool = True):
        self.cookie_id_for_log = cookie_id_for_log or 'unknown'
        self.headless = headless
        self.cookie_string = str(cookie_string or '').strip()
        self.cookies: Dict[str, str] = trans_cookies(self.cookie_string) if self.cookie_string else {}
        self.fetcher = OrderDetailFetcher(self.cookie_string, headless=headless, cookie_id_for_log=self.cookie_id_for_log)
        self.session: Optional[aiohttp.ClientSession] = None

    def _is_auth_failure_ret(self, ret_value: Any) -> bool:
        return classify_order_history_api_error(ret_value, self.cookie_id_for_log).kind == 'session_expired'

    def _set_runtime_cookie_state(self, cookies_dict: Dict[str, str]) -> bool:
        normalized = {str(name): str(value) for name, value in cookies_dict.items() if str(name).strip()}
        new_cookie_string = _cookie_dict_to_string(normalized)
        if new_cookie_string == self.cookie_string:
            return False

        self.cookies = normalized
        self.cookie_string = new_cookie_string
        self.fetcher.cookie = new_cookie_string
        return True

    async def _persist_cookie_update(self) -> None:
        if not self.cookie_string or self.cookie_id_for_log == 'unknown':
            return

        try:
            from db_manager import db_manager

            db_manager.update_cookie_account_info(self.cookie_id_for_log, cookie_value=self.cookie_string)
        except Exception as exc:
            logger.warning(f"【{self.cookie_id_for_log}】保存刷新后的 Cookie 失败: {exc}")

    async def _apply_response_cookie_updates(self, response_headers) -> bool:
        updates = _extract_set_cookie_updates(response_headers)
        if not updates:
            return False

        merged_cookies = dict(self.cookies)
        merged_cookies.update(updates)
        changed = self._set_runtime_cookie_state(merged_cookies)
        if changed:
            await self._persist_cookie_update()
        return changed

    async def _ensure_session(self) -> None:
        if self.session and not self.session.closed:
            return

        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(timeout=timeout)

    def _build_request_headers(self) -> Dict[str, str]:
        return {
            'accept': 'application/json',
            'content-type': 'application/x-www-form-urlencoded',
            'cookie': self.cookie_string,
            'idle_site_biz_code': 'COMMONPRO',
            'idle_user_group_member_id': '',
            'origin': 'https://seller.goofish.com',
            'referer': ORDER_LIST_REFERER,
            'user-agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/138.0.0.0 Safari/537.36'
            ),
        }

    def _build_request_params(self, data_val: str) -> Dict[str, str]:
        token = self.cookies.get('_m_h5_tk', '').split('_')[0]
        if not token:
            raise ValueError(f'【{self.cookie_id_for_log}】Cookie 缺少 _m_h5_tk，无法请求历史订单列表')

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time() * 1000)),
            'sign': '',
            'v': '1.0',
            'type': 'json',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': ORDER_LIST_API_NAME,
            'valueType': 'string',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21107h.42831410.0.0',
        }
        params['sign'] = generate_sign(params['t'], token, data_val)
        return params

    async def _request_order_page(self, page_number: int, allow_retry: bool = True) -> Dict[str, Any]:
        await self._ensure_session()
        assert self.session is not None

        payload = {
            'pageNumber': page_number,
            'rowsPerPage': DEFAULT_PAGE_SIZE,
            'orderIds': '',
            'queryCode': ORDER_LIST_QUERY_CODE,
            'orderSearchParam': '{}',
        }
        data_val = json.dumps(payload, separators=(',', ':'))

        try:
            params = self._build_request_params(data_val)
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

        async with self.session.post(
            ORDER_LIST_API_URL,
            params=params,
            data={'data': data_val},
            headers=self._build_request_headers(),
        ) as response:
            try:
                res_json = await response.json(content_type=None)
            except Exception as exc:
                response_text = await response.text()
                raise RuntimeError(
                    f'【{self.cookie_id_for_log}】历史订单列表返回非 JSON: status={response.status}, body={response_text[:300]}'
                ) from exc

            cookies_updated = await self._apply_response_cookie_updates(response.headers)

        ret_value = res_json.get('ret', [])
        if any('SUCCESS::调用成功' in str(ret) for ret in ret_value):
            return res_json

        if allow_retry and cookies_updated and self._is_auth_failure_ret(ret_value):
            logger.warning(f"【{self.cookie_id_for_log}】历史订单列表鉴权失败，Cookie 更新后重试第 {page_number} 页")
            return await self._request_order_page(page_number, allow_retry=False)

        raise classify_order_history_api_error(ret_value or res_json, self.cookie_id_for_log)

    def _normalize_order_candidate(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None

        common_data = raw.get('commonData') if isinstance(raw.get('commonData'), dict) else {}
        buyer_info = raw.get('buyerInfoVO') if isinstance(raw.get('buyerInfoVO'), dict) else {}
        price_info = raw.get('priceVO') if isinstance(raw.get('priceVO'), dict) else {}

        order_id = str(common_data.get('orderId') or '').strip()
        if not order_id:
            return None

        return {
            'order_id': order_id,
            'item_id': str(common_data.get('itemId') or '').strip() or None,
            'sid': None,
            'buyer_id': str(buyer_info.get('buyerId') or '').strip() or None,
            'buyer_nick': str(buyer_info.get('userNick') or '').strip() or None,
            'order_status': normalize_order_history_status(common_data.get('orderStatus')) or str(common_data.get('orderStatus') or '').strip() or None,
            'amount': (
                normalize_history_amount(price_info.get('totalPrice')) or
                normalize_history_amount(price_info.get('confirmFee')) or
                normalize_history_amount(price_info.get('auctionPrice'))
            ),
            'platform_created_at': parse_local_datetime_text_to_db_utc(common_data.get('createTime')),
            'platform_paid_at': parse_local_datetime_text_to_db_utc(common_data.get('paySuccessTime')),
            'platform_completed_at': parse_local_datetime_text_to_db_utc(common_data.get('finishTime')),
            'raw_source': raw,
        }

    async def open(self) -> bool:
        return True

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        await self.fetcher.close()

    async def fetch_recent_orders(
        self,
        max_orders: int = 100,
        max_scroll_rounds: int = 12,
        utc_start: Optional[str] = None,
        utc_end_exclusive: Optional[str] = None,
    ) -> Dict[str, Any]:
        del max_scroll_rounds

        if max_orders <= 0:
            return {
                'orders': [],
                'scanned_count': 0,
                'matched_count': 0,
                'out_of_range_count': 0,
                'pages_scanned': 0,
                'stopped_by_range': False,
            }

        await self.open()

        captured_orders: List[Dict[str, Any]] = []
        seen_order_ids = set()
        page_number = 1
        scanned_count = 0
        out_of_range_count = 0
        pages_scanned = 0
        stopped_by_range = False

        while len(captured_orders) < max_orders:
            response_json = await self._request_order_page(page_number)
            pages_scanned += 1
            module = ((response_json.get('data') or {}).get('module') or {})
            items = module.get('items') or []
            next_page = str(module.get('nextPage') or '').lower() == 'true'
            total_count = str(module.get('totalCount') or '').strip() or 'unknown'

            if not isinstance(items, list):
                items = []

            page_scanned_count = 0
            page_in_range_count = 0
            page_before_count = 0
            page_after_count = 0
            page_unknown_count = 0

            for raw_item in items:
                candidate = self._normalize_order_candidate(raw_item)
                if not candidate:
                    continue

                order_id = candidate.get('order_id')
                if not order_id or order_id in seen_order_ids:
                    continue

                seen_order_ids.add(order_id)
                scanned_count += 1
                page_scanned_count += 1

                anchor_time = resolve_order_history_anchor_time(candidate)
                range_status = classify_order_history_range(anchor_time, utc_start=utc_start, utc_end_exclusive=utc_end_exclusive)
                if range_status == 'in_range':
                    captured_orders.append(candidate)
                    page_in_range_count += 1
                else:
                    out_of_range_count += 1
                    if range_status == 'before':
                        page_before_count += 1
                    elif range_status == 'after':
                        page_after_count += 1
                    else:
                        page_unknown_count += 1

                if len(captured_orders) >= max_orders:
                    break

            logger.info(
                f"【{self.cookie_id_for_log}】历史订单列表第 {page_number} 页抓取完成: "
                f"page_items={len(items)}, scanned={page_scanned_count}, in_range={page_in_range_count}, "
                f"before_range={page_before_count}, after_range={page_after_count}, unknown_anchor={page_unknown_count}, "
                f"captured={len(captured_orders)}, totalCount={total_count}, nextPage={next_page}"
            )

            if len(captured_orders) >= max_orders or not next_page or not items:
                break

            if (
                utc_start and utc_end_exclusive
                and page_scanned_count > 0
                and page_in_range_count == 0
                and page_unknown_count == 0
                and page_before_count == page_scanned_count
            ):
                stopped_by_range = True
                logger.info(
                    f"【{self.cookie_id_for_log}】历史订单列表在第 {page_number} 页已全部早于开始时间，停止继续翻页"
                )
                break

            page_number += 1

        matched_orders = captured_orders[:max_orders]
        return {
            'orders': matched_orders,
            'scanned_count': scanned_count,
            'matched_count': len(matched_orders),
            'out_of_range_count': out_of_range_count,
            'pages_scanned': pages_scanned,
            'stopped_by_range': stopped_by_range,
        }

    async def fetch_order_detail(self, order_id: str, force_refresh: bool = True) -> Optional[Dict[str, Any]]:
        return await self.fetcher.fetch_order_detail(order_id, force_refresh=force_refresh)
