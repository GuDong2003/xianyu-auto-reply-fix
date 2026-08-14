import unittest

from utils.item_pagination import (
    DEFAULT_ITEM_LIST_PAGE_SIZE,
    MAX_ITEM_LIST_PAGE_SIZE,
    normalize_item_list_page_size,
)


class ItemPaginationTests(unittest.TestCase):
    def test_keeps_supported_page_size(self):
        self.assertEqual(normalize_item_list_page_size(10), 10)
        self.assertEqual(normalize_item_list_page_size("20"), 20)

    def test_caps_page_size_at_goofish_limit(self):
        self.assertEqual(MAX_ITEM_LIST_PAGE_SIZE, 20)
        self.assertEqual(normalize_item_list_page_size(100), 20)

    def test_normalizes_invalid_page_size(self):
        self.assertEqual(normalize_item_list_page_size(0), 1)
        self.assertEqual(normalize_item_list_page_size("invalid"), DEFAULT_ITEM_LIST_PAGE_SIZE)


if __name__ == "__main__":
    unittest.main()
