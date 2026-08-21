import unittest
from unittest.mock import MagicMock, PropertyMock, patch

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
            preferred_domain_suffixes=('goofish.com',),
        )

    @patch("utils.xianyu_slider_stealth.time.sleep", return_value=None)
    def test_cookie_reader_uses_normal_goofish_collection_before_manual_snapshot(self, _sleep):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "account-1"
        slider.context = object()
        slider.page = MagicMock()
        type(slider.page).url = PropertyMock(return_value="https://www.taobao.com/")
        slider.page.title.return_value = "verified"
        slider._manual_success_cookies = {
            "_m_h5_tk": "stale-taobao-token",
        }
        normalized = {"unb": "account-1", "_m_h5_tk": "goofish-token"}
        slider._snapshot_context_cookies = MagicMock(return_value=normalized)

        self.assertEqual(slider._get_cookies_after_success(), normalized)
        slider.page.goto.assert_called_once()
        slider._snapshot_context_cookies.assert_called_once_with(
            slider.context,
            page=slider.page,
            preferred_domain_suffixes=('goofish.com',),
        )

    def test_cookie_reader_uses_manual_snapshot_only_when_page_is_unavailable(self):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "account-1"
        slider.page = MagicMock()
        type(slider.page).url = PropertyMock(side_effect=RuntimeError("page closed"))
        slider._manual_success_cookies = {"unb": "account-1"}

        self.assertEqual(slider._get_cookies_after_success(), {"unb": "account-1"})
        self.assertIsNone(slider._manual_success_cookies)

    def test_failed_manual_retry_does_not_reuse_previous_cookie_snapshot(self):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.auto_slider_enabled = False
        slider.manual_slider_timeout = 30
        slider.pure_user_id = "account-1"
        slider.page = MagicMock()
        slider.page.is_closed.return_value = True
        slider.find_slider_elements = MagicMock(return_value=(None, "slider-button", None))
        slider._manual_success_cookies = {"unb": "previous-account"}

        self.assertFalse(slider.solve_slider())
        self.assertIsNone(slider._manual_success_cookies)

    @patch("utils.xianyu_slider_stealth.time.sleep", return_value=None)
    def test_manual_run_without_slider_keeps_qr_detection_and_returns_immediately(self, _sleep):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "account-1"
        slider.headless = False
        slider.auto_slider_enabled = False
        slider.risk_trigger_scene = "token_refresh"
        slider.disable_headless_warmup = True
        slider.context = object()
        slider.page = MagicMock()
        slider.page.title.return_value = "verification"
        slider.page.content.return_value = "captcha verification"
        slider._check_date_validity = MagicMock(return_value=True)
        slider.init_browser = MagicMock()
        slider._warmup_slider_context = MagicMock()
        slider._is_hard_block_page = MagicMock(return_value=False)
        slider._simulate_human_page_behavior = MagicMock()
        slider.find_slider_elements = MagicMock(return_value=(None, None, None))
        slider._select_monitor_page = MagicMock(return_value=slider.page)
        slider._detect_qr_code_verification = MagicMock(return_value=(False, None))
        slider._save_debug_snapshot = MagicMock()
        slider.close_browser = MagicMock()

        self.assertEqual(slider.run("https://example.com/captcha"), (False, None))
        slider._detect_qr_code_verification.assert_called_once_with(slider.page)


if __name__ == "__main__":
    unittest.main()
