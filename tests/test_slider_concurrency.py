import time
import unittest

from utils.xianyu_slider_stealth import SliderConcurrencyManager, XianyuSliderStealth


class SliderConcurrencyManagerTests(unittest.TestCase):
    def test_stale_same_account_instance_does_not_block_slot(self):
        manager = SliderConcurrencyManager()
        manager.active_instances = {
            "account_1": {
                "instance": object(),
                "start_time": time.time() - 3600,
            }
        }
        manager.waiting_queue = []
        manager.max_concurrent = 1
        manager.wait_timeout = 1

        self.assertTrue(manager.wait_for_slot("account_1_retry", timeout=0.01))
        self.assertEqual(manager.active_instances, {})

    def test_slider_instance_creation_does_not_take_slot_until_solving(self):
        manager = SliderConcurrencyManager()
        manager.active_instances = {}
        manager.waiting_queue = []

        slider = XianyuSliderStealth("account_2", enable_learning=False, headless=True)
        try:
            self.assertEqual(manager.active_instances, {})
            self.assertFalse(slider._slider_slot_acquired)
        finally:
            slider.close_browser()

    def test_solve_slider_releases_slot_when_solver_raises(self):
        manager = SliderConcurrencyManager()
        manager.active_instances = {}
        manager.waiting_queue = []
        manager.max_concurrent = 1

        slider = XianyuSliderStealth("account_3", enable_learning=False, headless=True)

        def fail_solver(*args, **kwargs):
            raise RuntimeError("boom")

        slider._solve_slider_locked = fail_solver
        try:
            with self.assertRaises(RuntimeError):
                slider.solve_slider()
            self.assertEqual(manager.active_instances, {})
            self.assertFalse(slider._slider_slot_acquired)
        finally:
            slider.close_browser()


if __name__ == "__main__":
    unittest.main()
