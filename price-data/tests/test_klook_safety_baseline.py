import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from klook_network_guard import (  # noqa: E402
    KlookNetworkGuardError,
    _extract_public_ip,
    _resolve_probe_consensus,
    validate_proxy_server,
)
from config_reader import get_browser_config, get_page_timing_config, load_config  # noqa: E402
from spider_core import (  # noqa: E402
    _detect_risk_page,
    _get_browser_executable_path,
    _normalize_browser_config,
    _normalize_page_timing_config,
)
from spiderv3 import QUERY_INTERVAL_SECONDS, _get_recovery_limits  # noqa: E402


class KlookSafetyBaselineTest(unittest.TestCase):
    def test_browser_reuses_existing_profile_context_and_page(self):
        config = _normalize_browser_config({})
        self.assertTrue(config["reuse_page_across_dates"])
        self.assertFalse(config["rebuild_runtime_on_crash"])
        self.assertEqual(0, config["profile_rebuild_after_context_failures"])
        self.assertEqual(0, config["max_date_pairs_per_context"])
        self.assertEqual(0, config["max_minutes_per_context"])

    def test_runtime_config_cannot_restore_unsafe_browser_rotation(self):
        config_path = os.path.join(os.path.dirname(__file__), "..", "src", "config.json")
        loaded = load_config(config_path)
        browser = _normalize_browser_config(get_browser_config(loaded))
        timing = _normalize_page_timing_config(get_page_timing_config(loaded))
        self.assertTrue(browser["reuse_page_across_dates"])
        self.assertEqual(0, browser["max_date_pairs_per_context"])
        self.assertEqual(0, timing["empty_response_reload_limit"])
        with self.assertRaises(ValueError):
            _normalize_browser_config({"reuse_page_across_dates": False})
        with self.assertRaises(ValueError):
            _normalize_page_timing_config({"empty_response_reload_limit": 1})

    def test_no_response_does_not_reload_by_default(self):
        timing = _normalize_page_timing_config({})
        self.assertEqual(0, timing["empty_response_reload_limit"])

    def test_query_interval_remains_two_to_five_seconds(self):
        self.assertEqual((2, 5), QUERY_INTERVAL_SECONDS)

    def test_zero_profile_rebuild_limit_is_not_replaced_with_default_retry(self):
        self.assertEqual((1, 0), _get_recovery_limits({
            "context_relaunch_after_reload_failures": 0,
            "profile_rebuild_after_context_failures": 0,
        }))
        self.assertEqual((1, 0), _get_recovery_limits({
            "context_relaunch_after_reload_failures": None,
            "profile_rebuild_after_context_failures": None,
        }))
        self.assertEqual((2, 3), _get_recovery_limits({
            "context_relaunch_after_reload_failures": 2,
            "profile_rebuild_after_context_failures": 3,
        }))

    def test_mac_uses_installed_google_chrome(self):
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(chrome_path):
            self.assertEqual(chrome_path, _get_browser_executable_path())

    def test_proxy_configuration_and_public_ip_parser(self):
        self.assertEqual(
            "http://127.0.0.1:7897",
            validate_proxy_server("http://127.0.0.1:7897"),
        )
        self.assertEqual("203.0.113.10", _extract_public_ip('{"ip":"203.0.113.10"}'))
        with self.assertRaises(KlookNetworkGuardError):
            validate_proxy_server("")

    def test_probe_consensus_tolerates_one_failure_but_rejects_weak_or_split_results(self):
        self.assertEqual(
            "203.0.113.10",
            _resolve_probe_consensus(["203.0.113.10", "203.0.113.10"], 3),
        )
        with self.assertRaises(KlookNetworkGuardError):
            _resolve_probe_consensus(["203.0.113.10"], 3)
        with patch("klook_network_guard.open_circuit") as open_guard:
            with self.assertRaises(KlookNetworkGuardError):
                _resolve_probe_consensus(["203.0.113.10", "203.0.113.11"], 3)
            open_guard.assert_called_once()


class _FakeFrame:
    def __init__(self, url, body="", title=""):
        self.url = url
        self._body = body
        self._title = title

    async def title(self):
        return self._title

    async def evaluate(self, _script):
        return self._body


class _FakePage:
    def __init__(self, frames):
        self.frames = frames
        self.url = frames[0].url if frames else ""


class KlookPageStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_auto_interstitial_is_distinct_from_hard_block(self):
        page = _FakePage(
            [
                _FakeFrame("https://www.klook.com/cars/"),
                _FakeFrame("https://captcha-delivery.com/interstitial/"),
            ]
        )
        state = await _detect_risk_page(page, {})
        self.assertEqual("AUTO_INTERSTITIAL", state["session_state"])
        self.assertFalse(state["non_retryable"])

    async def test_rate_limit_text_stops_without_becoming_l2(self):
        page = _FakePage(
            [_FakeFrame("https://www.klook.com/cars/", "Too Many Requests")]
        )
        state = await _detect_risk_page(page, {})
        self.assertEqual("RATE_LIMITED", state["session_state"])
        self.assertTrue(state["non_retryable"])

    async def test_bare_risk_id_is_not_a_hard_block(self):
        page = _FakePage(
            [_FakeFrame("https://www.klook.com/cars/", "Risk ID: abcdef123")]
        )
        state = await _detect_risk_page(page, {})
        self.assertEqual("NORMAL", state["session_state"])


if __name__ == "__main__":
    unittest.main()
