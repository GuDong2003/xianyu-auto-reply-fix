import json
import unittest

from utils.ai_item_context import build_ai_item_info, extract_sku_rows, format_ai_item_context, readable_description


class AIItemContextTests(unittest.TestCase):
    def test_plain_and_structured_descriptions(self):
        for value in ('Readable description', json.dumps({'content': 'Readable description'}), {'description': 'Readable description'}):
            self.assertEqual(readable_description(value), 'Readable description')
        self.assertEqual(readable_description({'title': 'Title', 'internal': 'synthetic-private-marker'}), '暂无商品描述')
        self.assertEqual(readable_description('{malformed JSON'), '暂无商品描述')

    def test_explicit_description_preferred_and_bounded(self):
        self.assertEqual(readable_description({'content': 'Old description'}, 'Edited description'), 'Edited description')
        self.assertEqual(len(readable_description('a' * 5000)), 2000)

    def test_sku_config_keeps_each_price_and_zero_inventory(self):
        config = {'sku_config': {'enabled': True, 'properties': [{'name': '版本'}], 'items': [
            {'values': ['标准'], 'price': '12.30', 'quantity': 0},
            {'values': ['专业'], 'price': '45.60', 'quantity': 2},
        ]}}
        rows = extract_sku_rows(config)
        self.assertEqual(rows[0], {'spec': '版本: 标准', 'price': '12.30', 'quantity': 0})
        self.assertEqual(rows[1]['price'], '45.60')
        info = build_ai_item_info({'item_detail': json.dumps(config), 'item_price': '12.30-45.60'})
        prompt = format_ai_item_context(info)
        self.assertIn('12.30-45.60', prompt)
        self.assertIn('版本: 专业：45.60元', prompt)
        self.assertIn('库存0', prompt)
        self.assertIn('未明确规格时先询问', prompt)

    def test_platform_sku_price_is_in_cents(self):
        rows = extract_sku_rows({'itemSkuList': [{
            'propertyList': [{'propertyText': '颜色', 'valueText': '蓝色'}],
            'priceInCent': '1234', 'quantity': '3',
        }]})
        self.assertEqual(rows, [{'spec': '颜色: 蓝色', 'price': '12.34', 'quantity': 3}])

    def test_invalid_prices_and_unknown_stock_never_fall_back_to_zero(self):
        for price in (None, 'NaN', 'Infinity', '-1', True):
            rows = extract_sku_rows({'sku_config': {'properties': [{'name': '版本'}], 'items': [
                {'values': ['专业'], 'price': price, 'quantity': None},
            ]}})
            self.assertIsNone(rows[0]['price'])
            self.assertIsNone(rows[0]['quantity'])

    def test_malformed_spec_combinations_not_forwarded(self):
        value = {'sku_config': {'properties': [{'name': '颜色'}, {'name': '套餐'}], 'items': [
            {'values': ['蓝色'], 'price': 1},
            {'values': ['', '专业'], 'price': 2},
        ]}}
        self.assertEqual(extract_sku_rows(value), [])

    def test_item_specific_prompt_and_unresolved_multi_sku_rules(self):
        info = build_ai_item_info({
            'item_title': 'Synthetic item', 'item_detail': 'Description',
            'ai_prompt': '标准版12元，专业版需确认价格', 'is_multi_spec': True,
        })
        prompt = format_ai_item_context(info)
        self.assertIn('商品专属说明: 标准版12元', prompt)
        self.assertIn('价格或库存时说明尚需确认', prompt)
        self.assertEqual(info['price'], '未知')

    def test_zero_reference_price_is_preserved(self):
        self.assertEqual(build_ai_item_info({'item_price': 0})['price'], 0)


if __name__ == '__main__':
    unittest.main()
