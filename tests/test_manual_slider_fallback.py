import unittest
from unittest.mock import MagicMock

from utils.xianyu_slider_stealth import XianyuSliderStealth


class ManualSliderFallbackTest(unittest.TestCase):
    def test_disabled_auto_slider_waits_for_manual_success_and_snapshots_cookies(self):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.auto_slider_enabled = False
        slider.manual_slider_timeout = 30
        slider.pure_user_id = "account-1"
        slider.page = MagicMock()
        slider.page.is_closed.return_value = False
        slider.context = object()
        slider.find_slider_elements = MagicMock(return_value=(None, "slider-button", None))
        slider.check_verification_success_fast = MagicMock(return_value=True)
        slider._snapshot_context_cookies = MagicMock(return_value={"unb": "account-1"})

        self.assertTrue(slider.solve_slider())
        self.assertEqual(slider._manual_success_cookies, {"unb": "account-1"})
        slider._snapshot_context_cookies.assert_called_once_with(
            slider.context,
            page=slider.page,
        )

    def test_cookie_reader_prefers_snapshot_taken_at_manual_success(self):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "account-1"
        slider._manual_success_cookies = {
            "unb": "account-1",
            "_m_h5_tk": "token_value",
        }

        self.assertEqual(
            slider._get_cookies_after_success(),
            slider._manual_success_cookies,
        )


if __name__ == "__main__":
    unittest.main()
