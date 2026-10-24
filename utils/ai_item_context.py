"""构建可读商品上下文，仅提取已知描述和规格字段。"""

import json
from decimal import Decimal, InvalidOperation


def _mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            pass
    return {}


def readable_description(value, fallback=''):
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()[:2000]
    data = _mapping(value)
    for key in ('description', 'desc', 'item_description', 'itemDesc', 'content'):
        text = data.get(key)
        if isinstance(text, str) and text.strip() and not text.lstrip().startswith(('{', '[')):
            return text.strip()[:2000]
    if isinstance(value, str) and value.strip() and not value.lstrip().startswith(('{', '[')):
        return value.strip()[:2000]
    return '暂无商品描述'


def _price(value, cents=False):
    try:
        if value is None or isinstance(value, bool):
            return None
        number = Decimal(str(value)) / (100 if cents else 1)
        if not number.is_finite() or number < 0:
            return None
        return format(number, '.2f')
    except (InvalidOperation, ValueError):
        return None


def _quantity(value):
    try:
        number = int(value)
        return number if not isinstance(value, bool) and str(number) == str(value) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def extract_sku_rows(value):
    data = _mapping(value)
    config = _mapping(data.get('sku_config') or data.get('skuConfig'))
    rows = []
    if config and config.get('enabled') is not False:
        properties = config.get('properties')
        items = config.get('items')
        if isinstance(properties, list) and isinstance(items, list):
            names = [str(p.get('name') or '').strip() if isinstance(p, dict) else '' for p in properties]
            for item in items[:50]:
                if not isinstance(item, dict):
                    continue
                values = item.get('values')
                if not names or not all(names) or not isinstance(values, list) or len(values) != len(names):
                    continue
                if any(not isinstance(v, str) or not v.strip() for v in values):
                    continue
                rows.append({
                    'spec': ' / '.join(f'{n}: {v.strip()}' for n, v in zip(names, values))[:300],
                    'price': _price(item.get('price')), 'quantity': _quantity(item.get('quantity')),
                })
    platform_items = data.get('itemSkuList')
    if not rows and isinstance(platform_items, list):
        for item in platform_items[:50]:
            if not isinstance(item, dict) or not isinstance(item.get('propertyList'), list):
                continue
            pairs = item['propertyList']
            if not pairs or any(not isinstance(p, dict) or not p.get('propertyText') or not p.get('valueText') for p in pairs):
                continue
            rows.append({
                'spec': ' / '.join(f"{p['propertyText']}: {p['valueText']}" for p in pairs)[:300],
                'price': _price(item.get('priceInCent'), cents=True),
                'quantity': _quantity(item.get('quantity')),
            })
    return rows


def build_ai_item_info(raw):
    raw = raw or {}
    detail = raw.get('item_detail')
    return {
        'title': str(raw.get('item_title') or '未知商品')[:200],
        'price': raw.get('item_price') if raw.get('item_price') not in (None, '') else '未知',
        'desc': readable_description(detail, raw.get('item_description')),
        'ai_prompt': str(raw.get('ai_prompt') or '')[:4000],
        'sku_rows': extract_sku_rows(detail),
        'is_multi_spec': bool(raw.get('is_multi_spec')),
    }


def format_ai_item_context(info):
    lines = [
        f"商品标题: {str(info.get('title') or '未知')[:200]}",
        f"商品参考价格: {info.get('price', '未知')}",
        f"商品描述: {readable_description(info.get('desc'))}",
    ]
    if info.get('ai_prompt'):
        lines.append(f"商品专属说明: {str(info['ai_prompt'])[:4000]}")
    rows = info.get('sku_rows') or []
    if rows:
        lines.append('已知规格资料（最近一次记录，不代表实时库存）：')
        for row in rows[:50]:
            price = f"{row['price']}元" if row.get('price') is not None else '价格未知'
            quantity = f"库存{row['quantity']}" if row.get('quantity') is not None else '库存未知'
            lines.append(f"- {row['spec']}：{price}，{quantity}")
    if rows or info.get('is_multi_spec'):
        lines.append('规格报价规则：买家未明确规格时先询问；只使用对应规格的已知价格，不能把参考价用于所有规格。')
        lines.append('缺少规格价格或库存时说明尚需确认，不编造信息；库存为0的规格不要推荐下单。')
    return '\n'.join(lines)
