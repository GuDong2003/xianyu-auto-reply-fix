import unittest

from utils.product_sku import build_sku_payload_fields, normalize_sku_config


class ItemPublisherSkuPayloadTests(unittest.TestCase):
    def test_builds_official_multi_sku_fields(self):
        config = normalize_sku_config({
            "enabled": True,
            "properties": [{
                "name": "颜色",
                "type": "default",
                "support_image": True,
                "values": [
                    {"value": "红色", "image": {"url": "https://img.example/red.jpg"}},
                    {"value": "蓝色", "image": None},
                ],
            }],
            "items": [
                {"values": ["红色"], "price": 12.34, "quantity": 8},
                {"values": ["蓝色"], "price": 20, "quantity": 1},
            ],
        })
        config["properties"][0]["values"][0]["image"].update({"width": 640, "height": 480})

        payload = build_sku_payload_fields(config)

        self.assertEqual(payload["itemSkuList"][0], {
            "priceInCent": "1234",
            "quantity": 8,
            "propertyList": [{"propertyText": "颜色", "valueText": "红色"}],
        })
        self.assertEqual(payload["propertyImageList"], [{
            "property": {"propertyText": "颜色", "valueText": "红色"},
            "url": "https://img.example/red.jpg",
        }])
        self.assertTrue(payload["itemProperties"][0]["supportImage"])
        self.assertEqual(payload["itemProperties"][0]["propertyValues"][0]["propertyValueImg"], {
            "url": "https://img.example/red.jpg",
            "widthSize": 640,
            "heightSize": 480,
            "status": "done",
        })

    def test_omits_property_image_list_when_no_spec_images(self):
        config = normalize_sku_config({
            "enabled": True,
            "properties": [{
                "name": "尺码",
                "type": "default",
                "support_image": False,
                "values": [{"value": "S"}, {"value": "M"}],
            }],
            "items": [
                {"values": ["S"], "price": 10, "quantity": 1},
                {"values": ["M"], "price": 11, "quantity": 2},
            ],
        })

        payload = build_sku_payload_fields(config)

        self.assertNotIn("propertyImageList", payload)

    def test_builds_property_list_for_two_dimensions(self):
        config = normalize_sku_config({
            "enabled": True,
            "properties": [
                {
                    "name": "颜色",
                    "type": "default",
                    "support_image": False,
                    "values": [{"value": "红色"}, {"value": "蓝色"}],
                },
                {
                    "name": "套餐",
                    "type": "custom",
                    "support_image": False,
                    "values": [{"value": "标准"}, {"value": "豪华"}],
                },
            ],
            "items": [
                {"values": [color, package], "price": 10 + index, "quantity": index + 1}
                for index, (color, package) in enumerate([
                    ("红色", "标准"),
                    ("红色", "豪华"),
                    ("蓝色", "标准"),
                    ("蓝色", "豪华"),
                ])
            ],
        })

        payload = build_sku_payload_fields(config)

        self.assertEqual(payload["itemSkuList"][3]["propertyList"], [
            {"propertyText": "颜色", "valueText": "蓝色"},
            {"propertyText": "套餐", "valueText": "豪华"},
        ])


if __name__ == "__main__":
    unittest.main()
