import json
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.xianyu_slider_stealth import XianyuSliderStealth  # noqa: E402


class _FakeElement:
    def __init__(self, *, visible=True, text="", on_click=None, box=None):
        self._visible = visible
        self._text = text
        self._on_click = on_click
        self._box = box or {"x": 120, "y": 240, "width": 60, "height": 32}

    def is_visible(self):
        return self._visible

    def text_content(self):
        return self._text

    def click(self, timeout=None):
        if self._on_click:
            self._on_click()

    def bounding_box(self):
        return dict(self._box)


class _FakeMouse:
    def __init__(self, on_click=None):
        self._on_click = on_click

    def click(self, *_args, **_kwargs):
        if self._on_click:
            self._on_click()


class _FakePage:
    def __init__(self, *, title="", url="", selectors=None):
        self._title = title
        self.url = url
        self.frames = []
        self._selectors = selectors or {}
        self.mouse = _FakeMouse()

    def title(self):
        return self._title

    def query_selector(self, selector):
        return self._selectors.get(selector)

    def wait_for_selector(self, selector, timeout=None):
        return self.query_selector(selector)

    def inner_text(self, selector, timeout=None):
        if selector != "body":
            raise AssertionError(f"unexpected selector: {selector}")
        return ""

    def content(self):
        return ""


class _RecoverablePunishPage(_FakePage):
    def __init__(self):
        super().__init__(
            title="验证码拦截",
            url="https://h5api.m.goofish.com/mtop.taobao.idlemessage.pc.login.token/punish?action=captcha&pureCaptcha=",
        )
        self.activated = False
        self.mouse = _FakeMouse(on_click=self.activate)

    def activate(self):
        self.activated = True

    def inner_text(self, selector, timeout=None):
        if selector != "body":
            raise AssertionError(f"unexpected selector: {selector}")
        if self.activated:
            return "亲，请按住滑块，拖动到最右边"
        return "亲，请拖动下方滑块完成验证\n验证失败，点击框体重试(error:unit1)"

    def content(self):
        return self.inner_text("body")

    def query_selector(self, selector):
        if selector in {".nc-container", "#nocaptcha"}:
            return _FakeElement(on_click=self.activate)
        if self.activated and selector in {"#nc_1_n1z", "#nc_1_n1t"}:
            return _FakeElement()
        return None


class _DelayedPunishSliderPage(_FakePage):
    def __init__(self):
        super().__init__(
            title="楠岃瘉鐮佹嫤鎴?",
            url="https://h5api.m.goofish.com/mtop.taobao.idlemessage.pc.login.token/punish?x5secdata=abc123&x5step=2&action=captcha&pureCaptcha=",
        )
        self.phase = 0
        self.ready_phase = 3

    def advance(self):
        self.phase += 1

    def inner_text(self, selector, timeout=None):
        if selector != "body":
            raise AssertionError(f"unexpected selector: {selector}")
        if self.phase >= self.ready_phase:
            return "浜诧紝璇锋寜浣忔粦鍧楋紝鎷栧姩鍒版渶鍙宠竟"
        return "浜诧紝璇锋嫋鍔ㄤ笅鏂规粦鍧楀畬鎴愰獙璇?"

    def content(self):
        return self.inner_text("body")

    def query_selector(self, selector):
        if selector in {"#nocaptcha", ".nc-container"}:
            return _FakeElement()
        if self.phase >= self.ready_phase and selector in {"#nc_1_n1z", "#nc_1_n1t", ".nc_scale"}:
            return _FakeElement()
        return None


class _FakeVerificationFrame:
    def __init__(self, *, verification_type="qr_verify", verify_url="", screenshot_path=None):
        self.verification_type = verification_type
        self.verify_url = verify_url
        self.screenshot_path = screenshot_path
        self.url = verify_url


class _DetachedPunishFrame:
    def __init__(self):
        self.url = (
            "https://passport.goofish.com/newlogin/login.do/_____tmd_____/punish?"
            "x5step=2&action=captcha&pureCaptcha=true&x5secdata=unit_detached"
        )

    def title(self):
        raise Exception("Frame was detached")

    def inner_text(self, *_args, **_kwargs):
        raise Exception("Frame was detached")

    def content(self):
        raise Exception("Frame was detached")

    def query_selector(self, *_args, **_kwargs):
        raise Exception("Frame was detached")


class SliderVerificationGuardsTest(unittest.TestCase):
    def _make_slider(self, page):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "unit_test_account"
        slider.page = page
        slider.last_verification_feedback = {}
        slider._merge_runtime_feedback = lambda *_args, **_kwargs: None
        slider._save_debug_snapshot = lambda *_args, **_kwargs: None
        slider._check_login_success_by_element = lambda _page: False
        slider._probe_context_login_during_slider = lambda _page=None: (False, {})
        return slider

    def _make_success_record(self, user_id, *, distance=258.0, server_judge_wait=2.0):
        return {
            "success": True,
            "user_id": user_id,
            "distance": distance,
            "profile_id": "win_chrome_147_1600x900",
            "headless": True,
            "overshoot_ratio": 1.04,
            "base_delay": 0.0075,
            "acceleration_curve": 1.74,
            "y_jitter_max": 1.48,
            "total_steps": 34,
            "slide_behavior": {
                "approach_offset_x": -24.9,
                "approach_offset_y": 12.6,
                "approach_steps": 10,
                "approach_pause": 0.11,
                "precision_steps": 9,
                "precision_pause": 0.10,
                "skip_hover": False,
                "hover_pause": 0.24,
                "pre_down_pause": 0.12,
                "post_down_pause": 0.14,
                "pre_up_pause": 0.06,
                "post_up_pause": 0.03,
                "delay_variation": [0.91, 1.06],
                "server_judge_wait": server_judge_wait,
                "total_elapsed_time": 4.2,
            },
            "verification_result": {
                "status": "success",
                "profile_id": "win_chrome_147_1600x900",
                "headless": True,
            },
        }

    def test_check_page_changed_does_not_treat_punish_url_as_success(self):
        page = _FakePage(
            title="闲鱼",
            url="https://h5api.m.goofish.com/mtop.taobao.idlemessage.pc.login.token/punish?x5secdata=abc123",
        )
        slider = self._make_slider(page)

        self.assertFalse(slider.check_page_changed())

    def test_check_page_changed_accepts_logged_in_page(self):
        page = _FakePage(
            title="闲鱼消息",
            url="https://www.goofish.com/im",
        )
        slider = self._make_slider(page)

        self.assertTrue(slider.check_page_changed())

    @mock.patch("utils.xianyu_slider_stealth.time.sleep", return_value=None)
    def test_check_verification_success_fast_rejects_punish_after_container_missing(self, _mock_sleep):
        page = _FakePage(
            title="闲鱼",
            url="https://h5api.m.goofish.com/mtop.taobao.idlemessage.pc.login.token/punish?x5secdata=abc123",
            selectors={
                ".nc-container": None,
            },
        )
        slider = self._make_slider(page)
        slider._detect_special_captcha_block = lambda _page=None: {
            "kind": "punish_captcha",
            "message": "当前命中阿里验证码拦截处罚页（pureCaptcha），且页面不存在可操作滑块",
            "url": page.url,
            "title": page.title(),
        }
        slider.check_verification_failure = lambda: False
        slider.check_page_changed = lambda: False

        result = slider.check_verification_success_fast(_FakeElement())

        self.assertFalse(result)
        self.assertEqual(slider.last_verification_feedback.get("status"), "hard_block")
        self.assertEqual(slider.last_verification_feedback.get("source"), "punish_captcha")

    @mock.patch("utils.xianyu_slider_stealth.time.sleep", return_value=None)
    def test_wait_for_context_login_does_not_finish_while_verification_page_still_visible_and_cookies_incomplete(self, _mock_sleep):
        page = _FakePage(title="扫码验证", url="https://www.taobao.com/")
        verify_frame = _FakeVerificationFrame(
            verification_type="qr_verify",
            verify_url="https://www.taobao.com/",
        )
        success_cookies = {
            "unb": "u",
            "sgcookie": "s",
            "cookie2": "c2",
            "_m_h5_tk": "tk",
            "_m_h5_tk_enc": "tk_enc",
            "t": "t_cookie",
        }
        slider = self._make_slider(page)
        slider._select_monitor_page = lambda _context, fallback_page=None: fallback_page or page
        slider._attempt_solve_slider_on_page = lambda _page: False
        slider._probe_context_login_success = lambda _context, _page: (True, page, dict(success_cookies))
        slider._detect_pending_identity_verification_cookie_state = lambda _cookies: []
        slider._detect_qr_code_verification = lambda _page: (True, verify_frame)
        slider._verification_target_is_timed_out = lambda _frame, fallback_page=None: False
        slider._notify_verification_required = lambda *_args, **_kwargs: None

        login_success, success_page = slider._wait_for_context_login(
            context=object(),
            fallback_page=page,
            max_wait_time=1,
            check_interval=1,
            verification_type="qr_verify",
            verification_url=verify_frame.verify_url,
        )

        self.assertFalse(login_success)
        self.assertIs(success_page, page)

    def test_finalize_logged_in_cookies_fails_when_session_still_unready(self):
        page = _FakePage(title="闲鱼消息", url="https://www.goofish.com/im")
        cookies_missing_cna = {
            "unb": "u",
            "sgcookie": "s",
            "cookie2": "c2",
            "_m_h5_tk": "tk",
            "_m_h5_tk_enc": "tk_enc",
            "t": "t_cookie",
        }
        slider = self._make_slider(page)
        slider.last_login_error = ""
        slider._snapshot_context_cookies = lambda _context, page=None, **_kwargs: dict(cookies_missing_cna)
        slider._stabilize_logged_in_context_cookies = lambda _context, _page, scene=None: dict(cookies_missing_cna)
        def _warmup(_context, _page, scene=None, initial_cookies=None):
            slider.last_browser_cookie_warmup_session_unready = True
            return dict(cookies_missing_cna)
        slider._perform_browser_cookie_warmup_probes = _warmup
        slider._consume_browser_cookie_warmup_verification_hint = lambda *_args, **_kwargs: None
        slider._handle_pending_identity_verification_state = lambda *_args, **_kwargs: None
        slider._log_cookie_snapshot_integrity = lambda *_args, **_kwargs: None

        def _fail_login(message):
            slider.last_login_error = message
            return None

        slider._fail_login = _fail_login

        result = slider._finalize_logged_in_cookies(
            context=object(),
            page=page,
            scene="单测收口",
        )

        self.assertIsNone(result)
        self.assertIn("服务端Session仍未就绪", slider.last_login_error)

    @mock.patch("utils.xianyu_slider_stealth.time.sleep", return_value=None)
    def test_find_slider_elements_reactivates_recoverable_punish_shell(self, _mock_sleep):
        page = _RecoverablePunishPage()
        slider = self._make_slider(page)

        slider_container, slider_button, slider_track = slider.find_slider_elements()

        self.assertTrue(page.activated)
        self.assertIsNotNone(slider_container)
        self.assertIsNotNone(slider_button)
        self.assertIsNotNone(slider_track)
        self.assertNotEqual(slider.last_verification_feedback.get("status"), "hard_block")

    @mock.patch("utils.xianyu_slider_stealth.time.sleep", return_value=None)
    def test_solve_slider_recovers_recoverable_punish_shell_before_hard_block(self, _mock_sleep):
        page = _RecoverablePunishPage()
        slider = self._make_slider(page)
        slider.enable_learning = False
        slider.headless = True
        slider.profile_id = "win_chrome_147_1600x900"
        slider.risk_trigger_scene = "token_refresh"
        slider._KEY_COOKIE_NAMES = set()
        slider.current_trajectory_data = {
            "random_params": {"strategy": "unit_test"},
            "trajectory_points": [],
            "total_steps": 1,
            "distance": 258,
            "final_left_px": 258,
        }
        slider._snapshot_context_cookies = lambda: {}
        slider._harden_password_slider_runtime = lambda *_args, **_kwargs: None
        slider.calculate_slide_distance = lambda *_args, **_kwargs: 258
        slider.generate_human_trajectory = lambda distance, attempt=1: [(distance, 0, 0)]
        slider.simulate_slide = lambda *_args, **_kwargs: True
        slider._probe_context_login_during_slider = lambda *_args, **_kwargs: (False, None)
        slider._save_failure_record = lambda *_args, **_kwargs: None
        slider._save_debug_snapshot = lambda *_args, **_kwargs: None
        slider._analyze_failure = lambda attempt, slide_distance, data: {
            "attempt": attempt,
            "slide_distance": slide_distance,
            "total_steps": data.get("total_steps", 0),
            "final_left_px": data.get("final_left_px", 0),
            "verification_feedback": dict(slider.last_verification_feedback),
        }

        def _fake_check_verification_success(_slider_button):
            slider.last_verification_feedback = {
                "status": "failure",
                "source": "keyword",
                "message": "验证失败，点击框体重试",
                "fail_code": "unit1",
            }
            return False

        slider.check_verification_success_fast = _fake_check_verification_success

        result = slider.solve_slider(max_retries=1)

        self.assertFalse(result)
        self.assertTrue(page.activated)
        self.assertEqual(slider.last_verification_feedback.get("source"), "keyword")

    @mock.patch("utils.xianyu_slider_stealth.time.sleep")
    def test_find_slider_elements_waits_for_punish_slider_dom_before_hard_block(self, mock_sleep):
        page = _DelayedPunishSliderPage()
        mock_sleep.side_effect = lambda *_args, **_kwargs: page.advance()
        slider = self._make_slider(page)

        slider_container, slider_button, slider_track = slider.find_slider_elements()

        self.assertIsNotNone(slider_container)
        self.assertIsNotNone(slider_button)
        self.assertIsNotNone(slider_track)
        self.assertNotEqual(slider.last_verification_feedback.get("status"), "hard_block")

    @mock.patch("utils.xianyu_slider_stealth.time.sleep")
    def test_solve_slider_waits_for_punish_slider_dom_before_hard_block(self, mock_sleep):
        page = _DelayedPunishSliderPage()
        mock_sleep.side_effect = lambda *_args, **_kwargs: page.advance()
        slider = self._make_slider(page)
        slider.enable_learning = False
        slider.headless = True
        slider.profile_id = "win_chrome_147_1600x900"
        slider.risk_trigger_scene = "token_refresh"
        slider._KEY_COOKIE_NAMES = set()
        slider.current_trajectory_data = {
            "random_params": {"strategy": "unit_test"},
            "trajectory_points": [],
            "total_steps": 1,
            "distance": 258,
            "final_left_px": 258,
        }
        slider._snapshot_context_cookies = lambda: {}
        slider._harden_password_slider_runtime = lambda *_args, **_kwargs: None
        slider.calculate_slide_distance = lambda *_args, **_kwargs: 258
        slider.generate_human_trajectory = lambda distance, attempt=1: [(distance, 0, 0)]
        slider.simulate_slide = lambda *_args, **_kwargs: True
        slider._probe_context_login_during_slider = lambda *_args, **_kwargs: (False, None)
        slider._save_failure_record = lambda *_args, **_kwargs: None
        slider._save_debug_snapshot = lambda *_args, **_kwargs: None
        slider._analyze_failure = lambda attempt, slide_distance, data: {
            "attempt": attempt,
            "slide_distance": slide_distance,
            "total_steps": data.get("total_steps", 0),
            "final_left_px": data.get("final_left_px", 0),
            "verification_feedback": dict(slider.last_verification_feedback),
        }

        def _fake_check_verification_success(_slider_button):
            slider.last_verification_feedback = {
                "status": "failure",
                "source": "keyword",
                "message": "楠岃瘉澶辫触",
                "fail_code": "unit_delayed",
            }
            return False

        slider.check_verification_success_fast = _fake_check_verification_success

        result = slider.solve_slider(max_retries=1)

        self.assertFalse(result)
        self.assertEqual(slider.last_verification_feedback.get("source"), "keyword")

    @mock.patch("utils.xianyu_slider_stealth.time.sleep", return_value=None)
    def test_is_hard_block_page_allows_recoverable_punish_shell(self, _mock_sleep):
        page = _RecoverablePunishPage()
        slider = self._make_slider(page)

        result = slider._is_hard_block_page(page)

        self.assertFalse(result)
        self.assertTrue(page.activated)

    @mock.patch("utils.xianyu_slider_stealth.time.sleep")
    def test_is_hard_block_page_waits_for_delayed_punish_slider_dom(self, mock_sleep):
        page = _DelayedPunishSliderPage()
        mock_sleep.side_effect = lambda *_args, **_kwargs: page.advance()
        slider = self._make_slider(page)

        result = slider._is_hard_block_page(page)

        self.assertFalse(result)

    def test_get_learning_history_with_fallback_filters_token_refresh_samples_only(self):
        slider = self._make_slider(_FakePage())
        slider.headless = True
        slider.profile_id = "win_chrome_147_1600x900"
        slider.risk_trigger_scene = "token_refresh"

        with tempfile.TemporaryDirectory() as tmpdir:
            current_history_path = os.path.join(tmpdir, "probe_scene_success.json")
            password_history_path = os.path.join(tmpdir, "global_password_success.json")
            cookie_history_path = os.path.join(tmpdir, "global_cookie_import_success.json")
            keepalive_history_path = os.path.join(tmpdir, "global_token_refresh_keepalive_success.json")

            with open(current_history_path, "w", encoding="utf-8") as handle:
                json.dump([self._make_success_record("current_token_refresh_sample")], handle)
            with open(password_history_path, "w", encoding="utf-8") as handle:
                json.dump([self._make_success_record("password_sample")], handle)
            with open(cookie_history_path, "w", encoding="utf-8") as handle:
                json.dump([self._make_success_record("cookie_sample")], handle)
            with open(keepalive_history_path, "w", encoding="utf-8") as handle:
                json.dump([self._make_success_record("keepalive_sample")], handle)

            slider.success_history_file = current_history_path

            history = slider._get_learning_history_with_fallback(reference_distance=258.0)

        user_ids = {record.get("user_id") for record in history}
        self.assertEqual(user_ids, {"current_token_refresh_sample", "keepalive_sample"})

    def test_save_success_record_persists_trigger_scene_and_server_wait(self):
        slider = self._make_slider(_FakePage())
        slider.profile_id = "win_chrome_147_1600x900"
        slider.headless = True
        slider.risk_trigger_scene = "token_refresh"

        with tempfile.TemporaryDirectory() as tmpdir:
            slider.success_history_file = os.path.join(tmpdir, "token_refresh_success.json")
            slider._save_success_record(
                {
                    "distance": 258.0,
                    "total_steps": 34,
                    "model": "physics_fast_learned",
                    "random_params": {
                        "overshoot_ratio": 1.04,
                        "base_delay": 0.0075,
                        "acceleration_curve": 1.74,
                        "y_jitter_max": 1.48,
                        "random_state_snapshot": [1, 2, 3],
                    },
                    "slide_behavior": {
                        "approach_offset_x": -24.9,
                        "approach_offset_y": 12.6,
                        "approach_steps": 10,
                        "approach_pause": 0.11,
                        "precision_steps": 9,
                        "precision_pause": 0.10,
                        "skip_hover": False,
                        "hover_pause": 0.24,
                        "pre_down_pause": 0.12,
                        "post_down_pause": 0.14,
                        "pre_up_pause": 0.06,
                        "post_up_pause": 0.03,
                        "delay_variation": [0.91, 1.06],
                        "server_judge_wait": 9.25,
                        "total_elapsed_time": 4.2,
                    },
                    "trajectory_points": [],
                    "final_left_px": 258,
                    "verification_result": {
                        "status": "success",
                        "profile_id": "win_chrome_147_1600x900",
                        "headless": True,
                    },
                }
            )

            with open(slider.success_history_file, encoding="utf-8") as handle:
                saved = json.load(handle)

        self.assertEqual(saved[0]["trigger_scene"], "token_refresh")
        self.assertEqual(saved[0]["slide_behavior"]["server_judge_wait"], 9.25)

    def test_optimize_trajectory_params_learns_server_judge_wait(self):
        slider = self._make_slider(_FakePage())
        slider.enable_learning = True
        slider.headless = True
        slider.profile_id = "win_chrome_147_1600x900"
        slider.risk_trigger_scene = "token_refresh"
        slider.trajectory_params = {"fallback": True}
        slider._get_learning_history_with_fallback = lambda reference_distance=None: [
            self._make_success_record("sample_a", server_judge_wait=8.8),
            self._make_success_record("sample_b", server_judge_wait=9.2),
            self._make_success_record("sample_c", server_judge_wait=9.6),
        ]

        optimized = slider._optimize_trajectory_params(reference_distance=258.0)

        self.assertIn("server_judge_wait", optimized["learned_behavior"])
        wait_range = optimized["learned_behavior"]["server_judge_wait"]
        self.assertLess(wait_range[0], wait_range[1])
        self.assertGreaterEqual(wait_range[0], 8.0)
        self.assertLessEqual(wait_range[1], 10.5)

    def test_generate_human_trajectory_attempt_two_handles_high_learned_step_floor(self):
        slider = self._make_slider(_FakePage())
        slider.enable_learning = True
        slider.headless = True
        slider.slider_max_retries = 3
        slider.profile_id = "win_chrome_147_1600x900"
        slider.risk_trigger_scene = "token_refresh"
        slider._should_prefer_docker_conservative_profile = lambda has_learning: False
        slider._use_headless_stable_profile = lambda: False
        slider._generate_physics_trajectory_with_params = (
            lambda distance, overshoot_ratio, steps, base_delay, acceleration_curve, y_jitter_max: [
                (distance, 0, 0)
            ] * steps
        )
        slider._optimize_trajectory_params = lambda reference_distance=None: {
            "learning_enabled": True,
            "history_count": 3,
            "learned_overshoot_range": (1.03, 1.09),
            "learned_delay_range": (0.0098, 0.0128),
            "learned_curve_range": (1.66, 1.86),
            "learned_jitter_range": (1.7, 2.5),
            "learned_steps_range": (39, 40),
        }

        trajectory = slider.generate_human_trajectory(258.0, attempt=2)

        self.assertTrue(trajectory)
        self.assertGreaterEqual(slider.current_trajectory_data["random_params"]["steps"], 39)

    def test_init_browser_uses_account_persistent_profile_when_enabled(self):
        class _FakeBrowser:
            def is_connected(self):
                return True

            def close(self):
                return None

        class _FakePageForInit:
            def close(self):
                return None

        class _FakePersistentContext:
            def __init__(self):
                self.browser = _FakeBrowser()
                self.page = _FakePageForInit()
                self.added_cookies = []

            def add_cookies(self, payload):
                self.added_cookies.extend(payload)

            def new_page(self):
                return self.page

            def close(self):
                return None

        class _FakeChromium:
            def __init__(self, context):
                self.context = context
                self.launch_called = False
                self.persistent_calls = []

            def launch(self, **_kwargs):
                self.launch_called = True
                raise AssertionError("should prefer launch_persistent_context")

            def launch_persistent_context(self, user_data_dir, **kwargs):
                self.persistent_calls.append(
                    {
                        "user_data_dir": user_data_dir,
                        "kwargs": kwargs,
                    }
                )
                return self.context

        class _FakePlaywright:
            def __init__(self, chromium):
                self.chromium = chromium

            def start(self):
                return self

            def stop(self):
                return None

        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "persistent_profile_unit_test"
        slider.headless = True
        slider.browser_channel = None
        slider.executable_path = None
        slider.page = None
        slider.browser = None
        slider.context = None
        slider.playwright = None
        slider.automation_backend = "playwright"
        slider.profile_id = "unassigned"
        slider.proxy_config = {}
        slider.use_account_persistent_profile = True
        slider.account_persistent_profile_dir = None
        slider._cleanup_on_init_failure = lambda: None
        slider._should_prefer_project_browser_for_playwright = lambda: False
        slider._ensure_project_playwright_browser = lambda: None
        slider._build_playwright_proxy_settings = lambda: None
        slider._build_initial_cookie_payload = lambda: []
        slider._install_stealth_init_script = lambda *_args, **_kwargs: None
        slider._get_random_browser_features = lambda: {
            "profile_id": "win_chrome_147_1600x900",
            "user_agent": "unit-test-agent",
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "color_scheme": "light",
            "accept_lang": "zh-CN,zh;q=0.9",
            "lang": "zh-CN",
            "window_size": "1600,900",
            "viewport_width": 1600,
            "viewport_height": 900,
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
        }

        fake_context = _FakePersistentContext()
        fake_chromium = _FakeChromium(fake_context)
        fake_playwright = _FakePlaywright(fake_chromium)
        slider._get_sync_playwright_factory = lambda: (lambda: fake_playwright)

        slider.init_browser()

        self.assertFalse(fake_chromium.launch_called)
        self.assertEqual(len(fake_chromium.persistent_calls), 1)
        self.assertEqual(
            fake_chromium.persistent_calls[0]["user_data_dir"],
            os.path.join(os.getcwd(), "browser_data", f"user_{slider.pure_user_id}"),
        )
        self.assertIs(slider.context, fake_context)
        self.assertIs(slider.page, fake_context.page)


    def test_init_browser_retries_persistent_profile_after_stale_singleton_cleanup(self):
        class _FakeBrowser:
            def is_connected(self):
                return True

        class _FakePageForInit:
            def close(self):
                return None

        class _FakePersistentContext:
            def __init__(self):
                self.browser = _FakeBrowser()
                self.page = _FakePageForInit()
                self.added_cookies = []

            def add_cookies(self, payload):
                self.added_cookies.extend(payload)

            def new_page(self):
                return self.page

            def close(self):
                return None

        class _FakeChromium:
            def __init__(self, context):
                self.context = context
                self.launch_called = False
                self.persistent_calls = []

            def launch(self, **_kwargs):
                self.launch_called = True
                raise AssertionError("should retry launch_persistent_context before fallback launch")

            def launch_persistent_context(self, user_data_dir, **kwargs):
                self.persistent_calls.append(
                    {
                        "user_data_dir": user_data_dir,
                        "kwargs": kwargs,
                    }
                )
                if len(self.persistent_calls) == 1:
                    raise RuntimeError(
                        "BrowserType.launch_persistent_context: "
                        "The profile appears to be in use by another Chromium process"
                    )
                return self.context

        class _FakePlaywright:
            def __init__(self, chromium):
                self.chromium = chromium

            def start(self):
                return self

        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "persistent_profile_retry_unit_test"
        slider.headless = True
        slider.browser_channel = None
        slider.executable_path = None
        slider.page = None
        slider.browser = None
        slider.context = None
        slider.playwright = None
        slider.automation_backend = "playwright"
        slider.profile_id = "unassigned"
        slider.proxy_config = {}
        slider.use_account_persistent_profile = True
        slider.account_persistent_profile_dir = None
        slider._cleanup_on_init_failure = lambda: None
        slider._should_prefer_project_browser_for_playwright = lambda: False
        slider._ensure_project_playwright_browser = lambda: None
        slider._build_playwright_proxy_settings = lambda: None
        slider._build_initial_cookie_payload = lambda: []
        slider._install_stealth_init_script = lambda *_args, **_kwargs: None
        slider._try_cleanup_stale_chromium_singleton_lock = lambda _profile_dir: True
        slider._get_random_browser_features = lambda: {
            "profile_id": "win_chrome_147_1600x900",
            "user_agent": "unit-test-agent",
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "color_scheme": "light",
            "accept_lang": "zh-CN,zh;q=0.9",
            "lang": "zh-CN",
            "window_size": "1600,900",
            "viewport_width": 1600,
            "viewport_height": 900,
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
        }

        fake_context = _FakePersistentContext()
        fake_chromium = _FakeChromium(fake_context)
        fake_playwright = _FakePlaywright(fake_chromium)
        slider._get_sync_playwright_factory = lambda: (lambda: fake_playwright)

        page = slider.init_browser()

        self.assertEqual(len(fake_chromium.persistent_calls), 2)
        self.assertFalse(fake_chromium.launch_called)
        self.assertIs(slider.context, fake_context)
        self.assertIs(slider.page, fake_context.page)
        self.assertIs(page, fake_context.page)
        self.assertIsNone(slider.browser)

    def test_init_browser_falls_back_when_stale_singleton_cleanup_not_allowed(self):
        class _FakePageForInit:
            pass

        class _FakeContext:
            def __init__(self):
                self.page = _FakePageForInit()
                self.added_cookies = []

            def add_cookies(self, payload):
                self.added_cookies.extend(payload)

            def new_page(self):
                return self.page

            def close(self):
                return None

        class _FakeBrowser:
            def __init__(self, context):
                self.context = context

            def is_connected(self):
                return True

            def new_context(self, **_kwargs):
                return self.context

            def close(self):
                return None

        class _FakeChromium:
            def __init__(self, context):
                self.context = context
                self.launch_called = False
                self.persistent_calls = []

            def launch(self, **_kwargs):
                self.launch_called = True
                return _FakeBrowser(self.context)

            def launch_persistent_context(self, user_data_dir, **kwargs):
                self.persistent_calls.append(
                    {
                        "user_data_dir": user_data_dir,
                        "kwargs": kwargs,
                    }
                )
                raise RuntimeError(
                    "BrowserType.launch_persistent_context: "
                    "The profile appears to be in use by another Chromium process"
                )

        class _FakePlaywright:
            def __init__(self, chromium):
                self.chromium = chromium

            def start(self):
                return self

            def stop(self):
                return None

        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "persistent_profile_fallback_unit_test"
        slider.headless = True
        slider.browser_channel = None
        slider.executable_path = None
        slider.page = None
        slider.browser = None
        slider.context = None
        slider.playwright = None
        slider.automation_backend = "playwright"
        slider.profile_id = "unassigned"
        slider.proxy_config = {}
        slider.use_account_persistent_profile = True
        slider.account_persistent_profile_dir = None
        slider._cleanup_on_init_failure = lambda: None
        slider._should_prefer_project_browser_for_playwright = lambda: False
        slider._ensure_project_playwright_browser = lambda: None
        slider._build_playwright_proxy_settings = lambda: None
        slider._build_initial_cookie_payload = lambda: []
        slider._install_stealth_init_script = lambda *_args, **_kwargs: None
        slider._try_cleanup_stale_chromium_singleton_lock = lambda _profile_dir: False
        slider._get_random_browser_features = lambda: {
            "profile_id": "win_chrome_147_1600x900",
            "user_agent": "unit-test-agent",
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "color_scheme": "light",
            "accept_lang": "zh-CN,zh;q=0.9",
            "lang": "zh-CN",
            "window_size": "1600,900",
            "viewport_width": 1600,
            "viewport_height": 900,
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
        }

        fake_context = _FakeContext()
        fake_chromium = _FakeChromium(fake_context)
        fake_playwright = _FakePlaywright(fake_chromium)
        slider._get_sync_playwright_factory = lambda: (lambda: fake_playwright)

        page = slider.init_browser()

        self.assertEqual(len(fake_chromium.persistent_calls), 1)
        self.assertTrue(fake_chromium.launch_called)
        self.assertIs(slider.context, fake_context)
        self.assertIs(slider.page, fake_context.page)
        self.assertIs(page, fake_context.page)

    def test_try_cleanup_stale_chromium_singleton_lock_removes_only_local_dead_lock(self):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "singleton_cleanup_unit_test"
        slider._get_current_hostname = lambda: "local-host"
        slider._is_process_alive = lambda pid: False
        removed_paths = []
        profile_dir = os.path.join(os.getcwd(), "browser_data", "user_local_dead_lock")

        with mock.patch("utils.xianyu_slider_stealth.os.path.islink", return_value=True), \
             mock.patch("utils.xianyu_slider_stealth.os.readlink", return_value="local-host-4321"), \
             mock.patch("utils.xianyu_slider_stealth.os.path.lexists", return_value=True), \
             mock.patch("utils.xianyu_slider_stealth.os.unlink", side_effect=lambda path: removed_paths.append(path)):
            cleaned = slider._try_cleanup_stale_chromium_singleton_lock(profile_dir)

        self.assertTrue(cleaned)
        self.assertEqual(
            removed_paths,
            [
                os.path.join(profile_dir, "SingletonLock"),
                os.path.join(profile_dir, "SingletonCookie"),
                os.path.join(profile_dir, "SingletonSocket"),
            ],
        )

    def test_try_cleanup_stale_chromium_singleton_lock_allows_dead_docker_container_rollover_lock(self):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "singleton_cleanup_container_rollover_test"
        slider._get_current_hostname = lambda: "a94804069a5e"
        slider._is_process_alive = lambda pid: False
        removed_paths = []
        profile_dir = os.path.join(os.getcwd(), "browser_data", "user_container_rollover_lock")

        with mock.patch("utils.xianyu_slider_stealth.os.path.islink", return_value=True), \
             mock.patch("utils.xianyu_slider_stealth.os.readlink", return_value="2d33e833c324-911"), \
             mock.patch("utils.xianyu_slider_stealth.os.path.lexists", return_value=True), \
             mock.patch("utils.xianyu_slider_stealth.os.unlink", side_effect=lambda path: removed_paths.append(path)):
            cleaned = slider._try_cleanup_stale_chromium_singleton_lock(profile_dir)

        self.assertTrue(cleaned)
        self.assertEqual(
            removed_paths,
            [
                os.path.join(profile_dir, "SingletonLock"),
                os.path.join(profile_dir, "SingletonCookie"),
                os.path.join(profile_dir, "SingletonSocket"),
            ],
        )

    def test_try_cleanup_stale_chromium_singleton_lock_skips_foreign_host_lock(self):
        slider = XianyuSliderStealth.__new__(XianyuSliderStealth)
        slider.pure_user_id = "singleton_cleanup_foreign_host_test"
        slider._get_current_hostname = lambda: "local-host"
        slider._is_process_alive = lambda pid: False
        removed_paths = []
        profile_dir = os.path.join(os.getcwd(), "browser_data", "user_foreign_host_lock")

        with mock.patch("utils.xianyu_slider_stealth.os.path.islink", return_value=True), \
             mock.patch("utils.xianyu_slider_stealth.os.readlink", return_value="remote-host-4321"), \
             mock.patch("utils.xianyu_slider_stealth.os.path.lexists", return_value=True), \
             mock.patch("utils.xianyu_slider_stealth.os.unlink", side_effect=lambda path: removed_paths.append(path)):
            cleaned = slider._try_cleanup_stale_chromium_singleton_lock(profile_dir)

        self.assertFalse(cleaned)
        self.assertEqual(removed_paths, [])

    def test_detect_post_slider_blocking_state_ignores_detached_punish_frame(self):
        page = _FakePage(
            title="闂查奔娑堟伅",
            url="https://www.goofish.com/im",
        )
        detached_frame = _DetachedPunishFrame()
        slider = self._make_slider(page)
        slider._detected_slider_frame = detached_frame

        result = slider._detect_post_slider_blocking_state(detached_frame)

        self.assertIsNone(result)
        self.assertEqual(slider.last_verification_feedback, {})

if __name__ == "__main__":
    unittest.main()
