import itertools
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_SKU_PROPERTY_NAMES: Tuple[str, ...] = (
    "颜色",
    "尺码",
    "容量",
    "份数",
    "大小",
    "高度",
    "总量",
)

MAX_SKU_PROPERTIES = 2
MAX_SKU_COMBINATIONS = 1500
MAX_SKU_PRICE = Decimal("99999999.99")
MAX_SKU_QUANTITY = 9999

_EMOJI_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "\u200D"
    "\uFE0F"
    "]"
)


class ProductSkuValidationError(ValueError):
    """商品发布规格配置校验失败。"""


def _contains_emoji(value: str) -> bool:
    return bool(_EMOJI_RE.search(value or ""))


def _parse_json_object(value: Any) -> Optional[Dict[str, Any]]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProductSkuValidationError("多规格配置不是有效的 JSON") from exc
    if not isinstance(value, dict):
        raise ProductSkuValidationError("多规格配置必须是对象")
    return value


def _normalize_image(image: Any, property_name: str, property_value: str) -> Optional[Dict[str, Any]]:
    if image in (None, ""):
        return None
    if not isinstance(image, dict):
        raise ProductSkuValidationError(f"规格 {property_name}:{property_value} 的图片格式无效")
    if not any(image.get(key) for key in ("url", "image_url", "src", "content", "data", "base64")):
        raise ProductSkuValidationError(f"规格 {property_name}:{property_value} 的图片缺少 URL 或图片内容")
    return dict(image)


def _parse_price(value: Any, combination_label: str) -> float:
    if value in (None, ""):
        raise ProductSkuValidationError(f"规格组合 {combination_label} 必须填写价格")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProductSkuValidationError(f"规格组合 {combination_label} 的价格格式无效") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > MAX_SKU_PRICE:
        raise ProductSkuValidationError(
            f"规格组合 {combination_label} 的价格必须在 0 到 {MAX_SKU_PRICE} 元之间"
        )
    if parsed.as_tuple().exponent < -2:
        raise ProductSkuValidationError(f"规格组合 {combination_label} 的价格最多保留 2 位小数")
    return float(parsed.quantize(Decimal("0.01")))


def _parse_quantity(value: Any, combination_label: str) -> int:
    if value in (None, "") or isinstance(value, bool):
        raise ProductSkuValidationError(f"规格组合 {combination_label} 必须填写库存")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProductSkuValidationError(f"规格组合 {combination_label} 的库存格式无效") from exc
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise ProductSkuValidationError(f"规格组合 {combination_label} 的库存必须是整数")
    quantity = int(parsed)
    if quantity < 0 or quantity > MAX_SKU_QUANTITY:
        raise ProductSkuValidationError(
            f"规格组合 {combination_label} 的库存必须在 0 到 {MAX_SKU_QUANTITY} 之间"
        )
    return quantity


def normalize_sku_config(value: Any) -> Optional[Dict[str, Any]]:
    """校验并标准化网页端商品多规格配置。

    未开启多规格时返回 ``None``，从而让旧素材和旧调用继续走原有单商品逻辑。
    """

    config = _parse_json_object(value)
    if not config:
        return None

    enabled = bool(config.get("enabled"))
    if not enabled:
        return None

    raw_properties = config.get("properties")
    if not isinstance(raw_properties, list) or not raw_properties:
        raise ProductSkuValidationError("请至少设置 1 组商品规格")
    if len(raw_properties) > MAX_SKU_PROPERTIES:
        raise ProductSkuValidationError(f"最多支持 {MAX_SKU_PROPERTIES} 组商品规格")

    properties: List[Dict[str, Any]] = []
    property_names = set()
    image_property_count = 0

    for property_index, raw_property in enumerate(raw_properties, start=1):
        if not isinstance(raw_property, dict):
            raise ProductSkuValidationError(f"第 {property_index} 组规格格式无效")

        name = str(raw_property.get("name") or "").strip()
        property_type = str(raw_property.get("type") or "").strip().lower()
        if not property_type:
            property_type = "default" if name in DEFAULT_SKU_PROPERTY_NAMES else "custom"
        if property_type not in {"default", "custom"}:
            raise ProductSkuValidationError(f"第 {property_index} 组规格类型无效")

        if property_type == "default":
            if name not in DEFAULT_SKU_PROPERTY_NAMES:
                raise ProductSkuValidationError(f"默认规格类型 {name or '未填写'} 不受支持")
        else:
            if not name:
                raise ProductSkuValidationError(f"第 {property_index} 组规格名称不能为空")
            if name in DEFAULT_SKU_PROPERTY_NAMES:
                raise ProductSkuValidationError(f"{name} 属于默认规格类型，请从默认列表选择")
            if len(name) < 2:
                raise ProductSkuValidationError("自定义规格名称至少为两个字")
            if len(name) > 4:
                raise ProductSkuValidationError("自定义规格名称至多为四个字")
            if _contains_emoji(name):
                raise ProductSkuValidationError("规格名称不能包含 Emoji")

        if name in property_names:
            raise ProductSkuValidationError(f"规格类型 {name} 已存在")
        property_names.add(name)

        support_image = bool(raw_property.get("support_image"))
        if support_image:
            image_property_count += 1
            if image_property_count > 1:
                raise ProductSkuValidationError("仅支持 1 组规格添加图片")

        raw_values = raw_property.get("values")
        if not isinstance(raw_values, list):
            raise ProductSkuValidationError(f"规格 {name} 的规格值格式无效")

        values: List[Dict[str, Any]] = []
        value_names = set()
        for raw_value in raw_values:
            if isinstance(raw_value, dict):
                value_text = str(raw_value.get("value") or "").strip()
                raw_image = raw_value.get("image")
            else:
                value_text = str(raw_value or "").strip()
                raw_image = None
            if not value_text:
                continue
            if len(value_text) > 12:
                raise ProductSkuValidationError(f"规格 {name} 的规格值最大长度为 12 个字")
            if _contains_emoji(value_text):
                raise ProductSkuValidationError(f"规格 {name} 的规格值不能包含 Emoji")
            if value_text in value_names:
                raise ProductSkuValidationError(f"规格 {name} 的规格值 {value_text} 重复")
            value_names.add(value_text)

            image = _normalize_image(raw_image, name, value_text)
            if image and not support_image:
                raise ProductSkuValidationError(f"规格 {name} 未开启规格图片")
            values.append({"value": value_text, "image": image})

        if len(values) < 2:
            raise ProductSkuValidationError(f"规格 {name} 最少需要 2 个规格值")

        properties.append(
            {
                "name": name,
                "type": property_type,
                "support_image": support_image,
                "values": values,
            }
        )

    expected_combinations = list(
        itertools.product(*[[value["value"] for value in prop["values"]] for prop in properties])
    )
    if len(expected_combinations) > MAX_SKU_COMBINATIONS:
        raise ProductSkuValidationError(
            f"规格组合数量超过 {MAX_SKU_COMBINATIONS} 个，请精简规格值"
        )

    raw_items = config.get("items")
    if not isinstance(raw_items, list):
        raise ProductSkuValidationError("规格组合价格与库存格式无效")

    expected_set = set(expected_combinations)
    normalized_item_map: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ProductSkuValidationError("规格组合格式无效")
        raw_item_values = raw_item.get("values")
        if not isinstance(raw_item_values, list):
            raise ProductSkuValidationError("规格组合缺少规格值")
        item_values = tuple(str(item or "").strip() for item in raw_item_values)
        combination_label = " / ".join(item_values) or "未填写"
        if len(item_values) != len(properties) or item_values not in expected_set:
            raise ProductSkuValidationError(f"规格组合 {combination_label} 与规格值不匹配")
        if item_values in normalized_item_map:
            raise ProductSkuValidationError(f"规格组合 {combination_label} 重复")
        normalized_item_map[item_values] = {
            "values": list(item_values),
            "price": _parse_price(raw_item.get("price"), combination_label),
            "quantity": _parse_quantity(raw_item.get("quantity"), combination_label),
        }

    missing_combinations = [combination for combination in expected_combinations if combination not in normalized_item_map]
    if missing_combinations:
        missing_label = " / ".join(missing_combinations[0])
        raise ProductSkuValidationError(f"规格组合 {missing_label} 缺少价格或库存")
    if len(normalized_item_map) != len(expected_combinations):
        raise ProductSkuValidationError("规格组合数量与规格值不匹配")

    items = [normalized_item_map[combination] for combination in expected_combinations]
    if not any(item["quantity"] > 0 for item in items):
        raise ProductSkuValidationError("至少需有一个商品库存大于 0")

    return {
        "enabled": True,
        "properties": properties,
        "items": items,
    }


def build_sku_payload_fields(sku_config: Dict[str, Any]) -> Dict[str, Any]:
    """把已上传规格图片的标准配置转换为闲鱼网页端发布字段。"""

    properties = sku_config.get("properties") or []
    items = sku_config.get("items") or []

    item_properties = []
    property_image_list = []
    for sku_property in properties:
        property_name = str(sku_property.get("name") or "").strip()
        property_values = []
        for sku_value in sku_property.get("values") or []:
            value_text = str(sku_value.get("value") or "").strip()
            image = sku_value.get("image") if isinstance(sku_value.get("image"), dict) else None
            property_value_image = None
            if image and image.get("url"):
                property_value_image = {
                    "url": image["url"],
                    "widthSize": image.get("width", 0),
                    "heightSize": image.get("height", 0),
                    "status": "done",
                }
                property_image_list.append(
                    {
                        "property": {
                            "propertyText": property_name,
                            "valueText": value_text,
                        },
                        "url": image["url"],
                    }
                )
            property_values.append(
                {
                    "propertyValue": value_text,
                    "propertyValueImg": property_value_image,
                }
            )
        item_properties.append(
            {
                "propertyName": property_name,
                "supportImage": bool(sku_property.get("support_image")),
                "propertyValues": property_values,
            }
        )

    item_sku_list = []
    for item in items:
        item_values = item.get("values") or []
        property_list = [
            {
                "propertyText": str(properties[index].get("name") or "").strip(),
                "valueText": str(value or "").strip(),
            }
            for index, value in enumerate(item_values)
        ]
        price_in_cent = int(Decimal(str(item.get("price"))) * 100)
        item_sku_list.append(
            {
                "priceInCent": str(price_in_cent),
                "quantity": int(item.get("quantity", 0)),
                "propertyList": property_list,
            }
        )

    result = {
        "itemProperties": item_properties,
        "itemSkuList": item_sku_list,
    }
    if property_image_list:
        result["propertyImageList"] = property_image_list
    return result
