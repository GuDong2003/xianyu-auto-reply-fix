"""商品分页同步：完整快照才允许对账，同账号同步操作互斥。"""

import asyncio
from contextlib import contextmanager
import threading
import time

from loguru import logger

from utils.item_pagination import normalize_item_list_page_size


_sync_lock = threading.Lock()
_sync_accounts = set()


class ItemSyncInProgress(RuntimeError):
    pass


@contextmanager
def item_sync_slot(cookie_id):
    with _sync_lock:
        if cookie_id in _sync_accounts:
            raise ItemSyncInProgress('该账号正在同步商品，请等待本次同步结束')
        _sync_accounts.add(cookie_id)
    try:
        yield
    finally:
        with _sync_lock:
            _sync_accounts.discard(cookie_id)


async def sync_all_item_pages(
    runtime, database, page_size=20, max_pages=None, sync_item_details=False,
    *, reconcile_listing=True,
):
    """补拉缺页，并在分页冲突时最多重取三轮；只对完整的一轮结果对账。"""
    page_size = normalize_item_list_page_size(page_size)
    page_sizes = (page_size, page_size, max(1, page_size // 2))
    items = []
    seen_ids = set()
    pages = 0
    saved = 0
    attempts = 0

    def result(success, error=None, unlisted_count=0):
        return {
            'success': success, 'complete': success, 'partial': bool(items) and not success,
            'total_pages': pages, 'total_count': len(items), 'total_saved': saved,
            'items': items, 'unlisted_count': unlisted_count, 'attempts': attempts,
            **({'error': error} if error else {}),
        }

    try:
        with item_sync_slot(runtime.cookie_id):
            for attempts, current_page_size in enumerate(page_sizes, 1):
                # 商品顺序可能在两次请求间变化，不合并不同轮次的快照。
                items, seen_ids, pages, saved = [], set(), 0, 0
                # 记录本轮每页实际条数，用于区分「漏页」和「平台总数口径不同」：
                # [20, 20, 3] 且已结束 = 末页不满，属正常收尾，对不上多半是口径问题；
                # [20, 3, 20] = 中间页不满却不是最后一页，那是真漏页。
                page_sizes_seen = []
                expected_total = None
                started_ns = time.time_ns()
                while True:
                    if max_pages is not None and pages >= max_pages:
                        return result(False, '达到同步页数上限，商品列表尚未完整，不更新下架状态')
                    response = await runtime.get_item_list_info(
                        pages + 1, current_page_size, sync_item_details=sync_item_details,
                    )
                    pages += 1
                    if not isinstance(response, dict) or response.get('success') is not True:
                        error = response.get('error') if isinstance(response, dict) else None
                        return result(False, error or f'第 {pages} 页商品同步失败')
                    current = response.get('items')
                    if not isinstance(current, list):
                        return result(False, f'第 {pages} 页商品列表格式无效')
                    current_ids = [str(row.get('id') or '').strip() if isinstance(row, dict) else '' for row in current]
                    if any(not item_id or item_id.startswith('auto_') for item_id in current_ids):
                        return result(False, f'第 {pages} 页包含无效商品ID，不更新下架状态')
                    if len(set(current_ids)) != len(current_ids) or seen_ids.intersection(current_ids):
                        retry_reason = '商品分页存在重复记录，无法确认完整列表'
                        break
                    if response.get('saved_count', 0) < len(current):
                        return result(False, f'第 {pages} 页商品未完整保存，不更新下架状态')
                    items.extend(current)
                    seen_ids.update(current_ids)
                    saved += response.get('saved_count', 0)
                    page_sizes_seen.append(len(current))

                    page_total = response.get('total_count')
                    if page_total is not None:
                        if type(page_total) is not int or page_total < 0:
                            return result(False, '商品总数无效，无法确认完整列表')
                        if expected_total is not None and page_total != expected_total:
                            retry_reason = '获取过程中平台商品总数发生变化'
                            break
                        expected_total = page_total

                    has_next = response.get('has_next')
                    if has_next is not None and not isinstance(has_next, bool):
                        return result(False, '商品分页结束标记无效')
                    if not current and has_next:
                        retry_reason = '商品分页为空但仍提示下一页，无法确认完整列表'
                        break
                    if expected_total is not None and len(items) > expected_total:
                        retry_reason = (
                            f'商品数量与平台返回总数不一致（已抓取 {len(items)} 条，'
                            f'平台返回 {expected_total} 条，共 {pages} 页）'
                        )
                        break

                    complete = has_next is False or (has_next is None and len(current) < current_page_size)
                    if complete:
                        if expected_total is not None and len(items) != expected_total:
                            # 短页或结束标记可能提前出现；有进展时继续探测下一页。
                            if not current:
                                retry_reason = (
                                    f'商品数量与平台返回总数不一致（已抓取 {len(items)} 条，'
                                    f'平台返回 {expected_total} 条，共 {pages} 页，'
                                    f'各页条数 {page_sizes_seen}）'
                                )
                                break
                        else:
                            unlisted = (
                                database.reconcile_item_listing(runtime.cookie_id, seen_ids, started_ns)
                                if reconcile_listing else 0
                            )
                            return result(True, unlisted_count=unlisted)
                    await asyncio.sleep(1)

                if attempts == len(page_sizes):
                    return result(False, f'商品列表自动获取 {attempts} 轮后仍未通过完整性校验：{retry_reason}，未更新下架状态')
                logger.warning(
                    f'商品列表第 {attempts} 轮校验失败：{retry_reason}；'
                    f'本轮各页条数 {page_sizes_seen}，稍后从第一页重新获取'
                )
                await asyncio.sleep(attempts)
    except ItemSyncInProgress as exc:
        return result(False, str(exc))
    except Exception:
        return result(False, '商品同步未完成，请稍后重试；未更新下架状态')
