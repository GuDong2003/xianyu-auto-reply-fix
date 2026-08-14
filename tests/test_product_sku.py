import json
import unittest

from utils.product_sku import ProductSkuValidationError, normalize_sku_config


def build_single_property_config():
    return {
        "enabled": True,
        "properties": [
            {
                "name": "颜色",
                "type": "default",
                "support_image": True,
                "values": [
                    {"value": "红色", "image": {"url": "https://img.example/red.jpg"}},
                    {"value": "蓝色", "image": None},
                ],
            }
        ],
        "items": [
            {"values": ["红色"], "price": "12.30", "quantity": "3"},
            {"values": ["蓝色"], "price": 15, "quantity": 0},
        ],
    }


class ProductSkuNormalizationTests(unittest.TestCase):
    def test_disabled_config_keeps_legacy_publish_path(self):
        self.assertIsNone(normalize_sku_config(None))
        self.assertIsNone(normalize_sku_config({"enabled": False}))

    def test_normalizes_default_property_and_json_input(self):
        result = normalize_sku_config(json.dumps(build_single_property_config(), ensure_ascii=False))

        self.assertTrue(result["enabled"])
        self.assertEqual(result["properties"][0]["type"], "default")
        self.assertEqual(result["items"][0]["price"], 12.3)
        self.assertEqual(result["items"][0]["quantity"], 3)

    def test_accepts_two_properties_with_custom_type(self):
        config = build_single_property_config()
        config["properties"].append(
            {
                "name": "套餐",
                "type": "custom",
                "support_image": False,
                "values": [{"value": "标准"}, {"value": "豪华"}],
            }
        )
        config["items"] = [
            {"values": [color, package], "price": 20 + index, "quantity": 1}
            for index, (color, package) in enumerate(
                [("红色", "标准"), ("红色", "豪华"), ("蓝色", "标准"), ("蓝色", "豪华")]
            )
        ]

        result = normalize_sku_config(config)

        self.assertEqual([item["values"] for item in result["items"]], [
            ["红色", "标准"],
            ["红色", "豪华"],
            ["蓝色", "标准"],
            ["蓝色", "豪华"],
        ])

    def test_rejects_default_name_marked_as_custom(self):
        config = build_single_property_config()
        config["properties"][0]["type"] = "custom"

        with self.assertRaisesRegex(ProductSkuValidationError, "属于默认规格类型"):
            normalize_sku_config(config)

    def test_rejects_missing_combination(self):
        config = build_single_property_config()
        config["items"].pop()

        with self.assertRaisesRegex(ProductSkuValidationError, "缺少价格或库存"):
            normalize_sku_config(config)

    def test_rejects_duplicate_value(self):
        config = build_single_property_config()
        config["properties"][0]["values"][1]["value"] = "红色"

        with self.assertRaisesRegex(ProductSkuValidationError, "重复"):
            normalize_sku_config(config)

    def test_requires_available_stock(self):
        config = build_single_property_config()
        for item in config["items"]:
            item["quantity"] = 0

        with self.assertRaisesRegex(ProductSkuValidationError, "库存大于 0"):
            normalize_sku_config(config)

    def test_rejects_price_with_more_than_two_decimals(self):
        config = build_single_property_config()
        config["items"][0]["price"] = "12.345"

        with self.assertRaisesRegex(ProductSkuValidationError, "最多保留 2 位小数"):
            normalize_sku_config(config)


if __name__ == "__main__":
    unittest.main()
