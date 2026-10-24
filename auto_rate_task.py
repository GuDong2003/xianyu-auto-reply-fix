"""自动补评价任务。

轻量本地调度版本：不引入上游独立 scheduler 服务，直接复用当前 SQLite、Cookie 和好评模板。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Dict, Optional

from loguru import logger

from db_manager import db_manager
from utils.rate_service import RateService
from utils.rate_followup import send_rate_thanks


DEFAULT_INTERVAL_SECONDS = int(os.getenv("AUTO_RATE_TASK_INTERVAL_SECONDS", "300") or 300)
DEFAULT_BATCH_LIMIT = int(os.getenv("AUTO_RATE_TASK_BATCH_LIMIT", "5") or 5)
DEFAULT_LOOKBACK_DAYS = int(os.getenv("AUTO_RATE_TASK_LOOKBACK_DAYS", "10") or 10)
DEFAULT_COOLDOWN_MINUTES = int(os.getenv("AUTO_RATE_TASK_COOLDOWN_MINUTES", "30") or 30)


def _status_from_result(result: Dict[str, Any]) -> str:
    if result.get("success"):
        return "success"
    if result.get("session_expired"):
        return "cookie_expired"
    return "failed"


async def rate_order_once(
    cookie_id: str,
    order_id: str,
    comment: Optional[str] = None,
    *,
    batch_id: Optional[str] = None,
    source: str = "manual",
) -> Dict[str, Any]:
    """对单个订单执行一次评价，并写入日志。"""
    cookie_id = str(cookie_id or "").strip()
    order_id = str(order_id or "").strip()
    batch_id = batch_id or f"{source}_{uuid.uuid4()}"

    order_info = db_manager.get_order_by_id(order_id)
    if not order_info:
        message = "订单不存在"
        db_manager.add_scheduled_rate_log(batch_id, cookie_id, order_id=order_id, status="skipped", message=message)
        return {"success": False, "message": message, "status": "skipped"}

    order_cookie_id = str(order_info.get("cookie_id") or "").strip()
    if order_cookie_id and order_cookie_id != cookie_id:
        message = "订单不属于当前账号"
        db_manager.add_scheduled_rate_log(
            batch_id, cookie_id, order_id=order_id, item_id=order_info.get("item_id"),
            buyer_id=order_info.get("buyer_id"), buyer_nick=order_info.get("buyer_nick"),
            status="skipped", message=message,
        )
        return {"success": False, "message": message, "status": "skipped"}

    if order_info.get("is_rated"):
        await send_rate_thanks(cookie_id, order_id)
        message = "订单已标记为已评价"
        db_manager.add_scheduled_rate_log(
            batch_id, cookie_id, order_id=order_id, item_id=order_info.get("item_id"),
            buyer_id=order_info.get("buyer_id"), buyer_nick=order_info.get("buyer_nick"),
            status="already_rated", message=message,
        )
        return {"success": True, "message": message, "status": "already_rated", "already_rated": True}

    if not comment:
        template = db_manager.get_active_comment_template(cookie_id)
        if not template or not str(template.get("content") or "").strip():
            message = "未设置激活的好评模板"
            db_manager.add_scheduled_rate_log(
                batch_id, cookie_id, order_id=order_id, item_id=order_info.get("item_id"),
                buyer_id=order_info.get("buyer_id"), buyer_nick=order_info.get("buyer_nick"),
                status="missing_template", message=message,
            )
            return {"success": False, "message": message, "status": "missing_template"}
        comment = str(template.get("content") or "").strip()

    cookie_string = db_manager.get_cookie(cookie_id)
    if not cookie_string:
        message = "账号 Cookie 为空或不存在"
        db_manager.add_scheduled_rate_log(
            batch_id, cookie_id, order_id=order_id, item_id=order_info.get("item_id"),
            buyer_id=order_info.get("buyer_id"), buyer_nick=order_info.get("buyer_nick"),
            comment=comment, status="cookie_expired", message=message,
        )
        return {"success": False, "message": message, "status": "cookie_expired"}

    rate_service = RateService(cookie_string, account_id=cookie_id)
    result = await rate_service.rate_buyer(order_id, comment)
    status = _status_from_result(result)
    message = str(result.get("message") or "")

    db_manager.add_scheduled_rate_log(
        batch_id=batch_id,
        cookie_id=cookie_id,
        order_id=order_id,
        item_id=order_info.get("item_id"),
        buyer_id=order_info.get("buyer_id"),
        buyer_nick=order_info.get("buyer_nick"),
        comment=comment,
        status=status,
        message=message,
        raw_response=result.get("raw") or result,
    )

    if result.get("success"):
        db_manager.mark_order_rated(order_id, True)
        await send_rate_thanks(cookie_id, order_id)
    else:
        db_manager.mark_order_rated(order_id, False, message)

    return {
        "success": bool(result.get("success")),
        "message": message,
        "status": status,
        "order_id": order_id,
        "cookie_id": cookie_id,
        "already_rated": bool(result.get("already_rated")),
    }


async def run_auto_rate_batch(
    *,
    batch_limit: int = DEFAULT_BATCH_LIMIT,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
) -> Dict[str, Any]:
    """执行一轮自动补评价。"""
    batch_id = str(uuid.uuid4())
    started_at = time.time()
    stats = {
        "batch_id": batch_id,
        "accounts": 0,
        "orders": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
    }

    all_cookies = db_manager.get_all_cookies()
    for cookie_id in list(all_cookies.keys()):
        try:
            if not db_manager.get_auto_comment(cookie_id):
                continue
            stats["accounts"] += 1
            template = db_manager.get_active_comment_template(cookie_id)
            if not template or not str(template.get("content") or "").strip():
                logger.info(f"【{cookie_id}】未设置激活好评模板，跳过自动补评价")
                continue

            orders = db_manager.get_pending_auto_comment_orders(
                cookie_id,
                limit=batch_limit,
                days=lookback_days,
                cooldown_minutes=cooldown_minutes,
            )
            if not orders:
                continue

            logger.info(f"【{cookie_id}】自动补评价找到 {len(orders)} 个待处理订单")
            for order in orders:
                stats["orders"] += 1
                result = await rate_order_once(
                    cookie_id,
                    order.get("order_id"),
                    str(template.get("content") or "").strip(),
                    batch_id=batch_id,
                    source="scheduled_rate",
                )
                if result.get("success"):
                    stats["success"] += 1
                elif result.get("status") in {"skipped", "missing_template", "already_rated"}:
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1
                await asyncio.sleep(1)
        except Exception as exc:
            stats["failed"] += 1
            logger.error(f"【{cookie_id}】自动补评价账号处理异常: {exc}")

    stats["duration_seconds"] = round(time.time() - started_at, 2)
    if stats["orders"]:
        logger.info(f"自动补评价批次完成: {stats}")
    return stats


async def auto_rate_task_loop(interval_seconds: int = DEFAULT_INTERVAL_SECONDS):
    """后台自动补评价循环。"""
    interval_seconds = max(60, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
    logger.info(f"自动补评价任务已启动，检查间隔 {interval_seconds} 秒")
    while True:
        try:
            await run_auto_rate_batch()
        except asyncio.CancelledError:
            logger.info("自动补评价任务已取消")
            raise
        except Exception as exc:
            logger.error(f"自动补评价任务异常: {exc}")
        await asyncio.sleep(interval_seconds)
