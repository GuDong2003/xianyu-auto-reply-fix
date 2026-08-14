from typing import Any


DEFAULT_ITEM_LIST_PAGE_SIZE = 20
MAX_ITEM_LIST_PAGE_SIZE = 20


def normalize_item_list_page_size(value: Any) -> int:
    """将商品列表分页大小限制在闲鱼网页端接口允许的范围内。"""
    try:
        page_size = int(value)
    except (TypeError, ValueError):
        page_size = DEFAULT_ITEM_LIST_PAGE_SIZE

    return min(max(page_size, 1), MAX_ITEM_LIST_PAGE_SIZE)
