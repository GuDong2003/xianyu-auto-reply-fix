"""类目识别：老字段缺失时回退到 kgraph 推荐的属性卡片。

平台现在把类目放在 `data.cardList[].cardData`（propertyName 含「类目/分类/品类」）
的 `valuesList` 里，`categoryPredictResult` 只剩 `{"sugShow": "1"}`。只读老字段会
拿到全空结果并报「自动识别类目不完整」，看起来像识别失败，其实是取值位置变了。
"""

import unittest

from utils.item_publisher import ItemPublisher


def card_list(values, property_name='分类'):
    return [{'cardData': {'propertyName': property_name, 'valuesList': values}}]


class CategoryFromCardListTests(unittest.TestCase):
    def test_reads_category_from_attribute_card(self):
        channel_res = {
            'data': {
                'categoryPredictResult': {'sugShow': '1'},
                'cardList': card_list([
                    {'catId': '50023914', 'channelCatId': '202036301', 'catName': '电子资料'},
                ]),
            }
        }

        result = ItemPublisher._validate_category_recommendation(channel_res)

        self.assertEqual(result['catId'], '50023914')
        self.assertEqual(result['channelCatId'], '202036301')
        self.assertEqual(result['catName'], '电子资料')

    def test_prefers_clicked_candidate(self):
        channel_res = {
            'data': {
                'cardList': card_list([
                    {'catId': '50023914', 'channelCatId': '1', 'catName': '服务', 'score': 0.9},
                    {'catId': '50023914', 'channelCatId': '202036301', 'catName': '电子资料', 'isClicked': '1'},
                ]),
            }
        }

        result = ItemPublisher._validate_category_recommendation(channel_res)

        self.assertEqual(result['channelCatId'], '202036301')

    def test_falls_back_to_highest_score(self):
        channel_res = {
            'data': {
                'cardList': card_list([
                    {'catId': '50023914', 'channelCatId': '1', 'catName': '服务', 'score': 0.2},
                    {'catId': '50023914', 'channelCatId': '202036301', 'catName': '电子资料', 'score': 0.8},
                ]),
            }
        }

        result = ItemPublisher._validate_category_recommendation(channel_res)

        self.assertEqual(result['channelCatId'], '202036301')

    def test_backfills_missing_parent_cat_id_from_siblings(self):
        # 平台有时只给 channelCatId，不给 catId；父级 catId 是同批候选共用的。
        channel_res = {
            'data': {
                'cardList': card_list([
                    {'channelCatId': '1', 'catName': '服务', 'catId': '50023914'},
                    {'channelCatId': '202036301', 'catName': '电子资料', 'isClicked': '1'},
                ]),
            }
        }

        result = ItemPublisher._validate_category_recommendation(channel_res)

        self.assertEqual(result['catId'], '50023914')
        self.assertEqual(result['channelCatId'], '202036301')

    def test_does_not_guess_when_parent_cat_ids_conflict(self):
        # 候选分属不同父级时不能猜：猜错会变成平台的类目路径查询失败，更难查。
        channel_res = {
            'data': {
                'cardList': card_list([
                    {'channelCatId': '1', 'catName': '服务', 'catId': '50023914'},
                    {'channelCatId': '2', 'catName': '技能培训', 'catId': '50088888'},
                    {'channelCatId': '202036301', 'catName': '电子资料', 'isClicked': '1'},
                ]),
            }
        }

        with self.assertRaisesRegex(ValueError, '自动识别类目不完整'):
            ItemPublisher._validate_category_recommendation(channel_res)

    def test_ignores_cards_without_category_keyword(self):
        channel_res = {
            'data': {
                'cardList': card_list(
                    [{'catId': '1', 'channelCatId': '2', 'catName': '不相关'}],
                    property_name='品牌',
                ),
            }
        }

        with self.assertRaisesRegex(ValueError, '自动识别类目不完整'):
            ItemPublisher._validate_category_recommendation(channel_res)

    def test_missing_tb_cat_id_is_not_blocking(self):
        channel_res = {
            'data': {
                'categoryPredictResult': {'catId': '50023914', 'catName': '电子资料', 'channelCatId': '202036301'},
            }
        }

        result = ItemPublisher._validate_category_recommendation(channel_res)

        self.assertEqual(result['tbCatId'], '')
        self.assertEqual(result['channelCatId'], '202036301')

    def test_legacy_payload_still_wins(self):
        # 老字段齐全时不应被卡片内容覆盖。
        channel_res = {
            'data': {
                'categoryPredictResult': {
                    'catId': '111', 'catName': '老类目', 'channelCatId': '222', 'tbCatId': '333',
                },
                'cardList': card_list([
                    {'catId': '50023914', 'channelCatId': '202036301', 'catName': '电子资料', 'isClicked': '1'},
                ]),
            }
        }

        result = ItemPublisher._validate_category_recommendation(channel_res)

        self.assertEqual(result['catId'], '111')
        self.assertEqual(result['channelCatId'], '222')

    def test_partial_legacy_does_not_mix_with_card_category(self):
        channel_res = {'data': {
            'categoryPredictResult': {'catId': '111', 'catName': '老类目', 'tbCatId': '333'},
            'cardList': card_list([
                {'catId': '50023914', 'channelCatId': '202036301', 'catName': '电子资料'},
            ]),
        }}
        self.assertEqual(ItemPublisher._validate_category_recommendation(channel_res), {
            'catId': '50023914', 'channelCatId': '202036301', 'catName': '电子资料', 'tbCatId': '',
        })

    def test_partial_legacy_cannot_hide_ambiguous_card_parent(self):
        channel_res = {'data': {
            'categoryPredictResult': {'catId': '111', 'catName': '老类目'},
            'cardList': card_list([
                {'catId': '50023914', 'channelCatId': '1', 'catName': '服务'},
                {'catId': '50088888', 'channelCatId': '2', 'catName': '技能培训'},
                {'channelCatId': '202036301', 'catName': '电子资料', 'isClicked': '1'},
            ]),
        }}
        with self.assertRaisesRegex(ValueError, '自动识别类目不完整'):
            ItemPublisher._validate_category_recommendation(channel_res)

    def test_raises_when_nothing_usable(self):
        with self.assertRaisesRegex(ValueError, '自动识别类目不完整'):
            ItemPublisher._validate_category_recommendation({'data': {}})


if __name__ == '__main__':
    unittest.main()
