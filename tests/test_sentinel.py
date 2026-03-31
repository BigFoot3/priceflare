"""
PriceFlare — test suite.
"""

import json
import time
import threading
import pytest
from unittest.mock import MagicMock, patch
from priceflare import Sentinel, parsers
from priceflare.exceptions import ParserError, SentinelError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sentinel(**kwargs) -> Sentinel:
    defaults = dict(
        ws_url           = "wss://fake.example.com/ws",
        price_parser     = parsers.binance,
        crash_threshold  = 3.0,
        pump_threshold   = 3.0,
        window_seconds   = 300,
        cooldown_seconds = 600,
    )
    defaults.update(kwargs)
    return Sentinel(**defaults)


def _feed_prices(sentinel: Sentinel, prices: list[float],
                 base_ts: float | None = None) -> list[dict | None]:
    """Feed a list of prices into sentinel._check_movement with controlled timestamps."""
    results = []
    now = base_ts or time.time()
    for i, price in enumerate(prices):
        with patch("time.time", return_value=now + i):
            results.append(sentinel._check_movement(price))
    return results


# ---------------------------------------------------------------------------
# Sentinel.__init__ — validation
# ---------------------------------------------------------------------------

class TestSentinelInit:
    def test_valid_init(self):
        s = make_sentinel()
        assert s is not None

    def test_empty_ws_url_does_not_raise_on_init(self):
        """ws_url="" is valid for offline/backtesting use via feed()."""
        s = make_sentinel(ws_url="")
        assert s is not None

    def test_empty_ws_url_raises_on_start(self):
        """start() must raise SentinelError when ws_url is empty."""
        s = make_sentinel(ws_url="")
        with pytest.raises(SentinelError, match="ws_url"):
            s.start()

    def test_non_callable_parser_raises(self):
        with pytest.raises(SentinelError, match="price_parser"):
            make_sentinel(price_parser="not_a_function")

    def test_zero_crash_threshold_raises(self):
        with pytest.raises(SentinelError, match="crash_threshold"):
            make_sentinel(crash_threshold=0)

    def test_negative_pump_threshold_raises(self):
        with pytest.raises(SentinelError, match="pump_threshold"):
            make_sentinel(pump_threshold=-1.0)

    def test_zero_window_raises(self):
        with pytest.raises(SentinelError, match="window_seconds"):
            make_sentinel(window_seconds=0)

    def test_negative_cooldown_raises(self):
        with pytest.raises(SentinelError, match="cooldown_seconds"):
            make_sentinel(cooldown_seconds=-1)

    def test_zero_max_reconnects_raises(self):
        with pytest.raises(SentinelError, match="max_reconnects"):
            make_sentinel(max_reconnects=0)

    def test_negative_max_reconnects_raises(self):
        with pytest.raises(SentinelError, match="max_reconnects"):
            make_sentinel(max_reconnects=-5)

    def test_custom_parser_accepted(self):
        s = make_sentinel(price_parser=lambda raw: 1.0)
        assert s is not None


# ---------------------------------------------------------------------------
# Movement detection — _check_movement
# ---------------------------------------------------------------------------

class TestCheckMovementNoop:
    def test_fewer_than_10_prices_returns_none(self):
        s = make_sentinel()
        for i in range(9):
            result = s._check_movement(1000.0 + i)
        assert result is None

    def test_stable_prices_return_none(self):
        s = make_sentinel()
        results = _feed_prices(s, [1000.0] * 20)
        assert all(r is None for r in results)

    def test_small_move_below_threshold_returns_none(self):
        s = make_sentinel(crash_threshold=3.0)
        prices = [1000.0] * 10 + [985.0]  # −1.5% — below threshold
        results = _feed_prices(s, prices)
        assert all(r is None for r in results)


class TestCrashDetection:
    def test_crash_detected(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0)
        prices = [1000.0] * 10 + [960.0]  # −4%
        results = _feed_prices(s, prices)
        alert = next(r for r in results if r is not None)
        assert alert["type"] == "CRASH"

    def test_crash_change_pct_is_negative(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0)
        prices = [1000.0] * 10 + [940.0]  # −6%
        results = _feed_prices(s, prices)
        alert = next(r for r in results if r is not None)
        assert alert["change_pct"] < 0

    def test_crash_contains_expected_keys(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0)
        prices = [1000.0] * 10 + [960.0]
        results = _feed_prices(s, prices)
        alert = next(r for r in results if r is not None)
        for key in ("type", "change_pct", "ref_price", "cur_price", "window_sec", "timestamp"):
            assert key in alert

    def test_crash_ref_and_cur_price(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0)
        prices = [1000.0] * 10 + [960.0]
        results = _feed_prices(s, prices)
        alert = next(r for r in results if r is not None)
        assert alert["ref_price"] == 1000.0
        assert alert["cur_price"] == 960.0

    def test_crash_at_exact_threshold(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0)
        prices = [1000.0] * 10 + [970.0]  # exactly −3%
        results = _feed_prices(s, prices)
        alert = next((r for r in results if r is not None), None)
        assert alert is not None
        assert alert["type"] == "CRASH"


class TestPumpDetection:
    def test_pump_detected(self):
        s = make_sentinel(pump_threshold=3.0, cooldown_seconds=0)
        prices = [1000.0] * 10 + [1040.0]  # +4%
        results = _feed_prices(s, prices)
        alert = next(r for r in results if r is not None)
        assert alert["type"] == "PUMP"

    def test_pump_change_pct_is_positive(self):
        s = make_sentinel(pump_threshold=3.0, cooldown_seconds=0)
        prices = [1000.0] * 10 + [1060.0]
        results = _feed_prices(s, prices)
        alert = next(r for r in results if r is not None)
        assert alert["change_pct"] > 0

    def test_pump_at_exact_threshold(self):
        s = make_sentinel(pump_threshold=3.0, cooldown_seconds=0)
        prices = [1000.0] * 10 + [1030.0]  # exactly +3%
        results = _feed_prices(s, prices)
        alert = next((r for r in results if r is not None), None)
        assert alert is not None
        assert alert["type"] == "PUMP"


class TestCooldown:
    def test_cooldown_prevents_second_alert(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=600)
        now = time.time()

        # First crash
        with patch("time.time", return_value=now):
            prices = [1000.0] * 10 + [960.0]
            for p in prices:
                s._check_movement(p)

        # Second crash — within cooldown
        with patch("time.time", return_value=now + 60):
            result = s._check_movement(900.0)
        assert result is None

    def test_alert_fires_after_cooldown_expires(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=600)
        now = time.time()

        # First crash
        with patch("time.time", return_value=now):
            for p in [1000.0] * 10 + [960.0]:
                s._check_movement(p)

        # After cooldown — prices need to be within new window
        after = now + 700
        with patch("time.time", return_value=after):
            for p in [1000.0] * 10:
                s._check_movement(p)
            result = s._check_movement(960.0)

        assert result is not None
        assert result["type"] == "CRASH"

    def test_zero_cooldown_allows_immediate_second_alert(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0)
        now = time.time()
        alerts = []
        with patch("time.time", return_value=now):
            for p in [1000.0] * 10 + [960.0]:
                r = s._check_movement(p)
                if r:
                    alerts.append(r)
        with patch("time.time", return_value=now + 1):
            r = s._check_movement(920.0)
            if r:
                alerts.append(r)
        assert len(alerts) >= 1


# ---------------------------------------------------------------------------
# Window eviction
# ---------------------------------------------------------------------------

class TestWindowEviction:
    def test_old_prices_evicted(self):
        s = make_sentinel(window_seconds=300, cooldown_seconds=0)
        now = time.time()

        # Feed prices at t=0
        with patch("time.time", return_value=now):
            for p in [1000.0] * 10:
                s._check_movement(p)

        # Feed at t=400 (outside 300s window)
        with patch("time.time", return_value=now + 400):
            result = s._check_movement(500.0)  # would be −50% if ref was 1000

        # Old prices evicted → fewer than 10 prices in window → no alert
        assert result is None

    def test_prices_within_window_kept(self):
        s = make_sentinel(window_seconds=300, cooldown_seconds=0)
        now = time.time()
        with patch("time.time", return_value=now):
            for p in [1000.0] * 10:
                s._check_movement(p)
        with patch("time.time", return_value=now + 299):
            result = s._check_movement(960.0)  # −4%, within window
        assert result is not None


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

class TestCallbacks:
    def test_on_alert_called_on_crash(self):
        cb = MagicMock()
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0, on_alert=cb)
        for p in [1000.0] * 10 + [960.0]:
            s.feed(p)
        cb.assert_called_once()
        assert cb.call_args[0][0]["type"] == "CRASH"

    def test_on_alert_called_on_pump(self):
        cb = MagicMock()
        s = make_sentinel(pump_threshold=3.0, cooldown_seconds=0, on_alert=cb)
        for p in [1000.0] * 10 + [1040.0]:
            s.feed(p)
        cb.assert_called_once()
        assert cb.call_args[0][0]["type"] == "PUMP"

    def test_on_crash_called_with_correct_price(self):
        crash_cb = MagicMock()
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0, on_crash=crash_cb)
        for p in [1000.0] * 10:
            s.feed(p)
        s.feed(960.0)
        crash_cb.assert_called_once_with(960.0)

    def test_on_crash_not_called_on_pump(self):
        crash_cb = MagicMock()
        s = make_sentinel(pump_threshold=3.0, cooldown_seconds=0, on_crash=crash_cb)
        _feed_prices(s, [1000.0] * 10 + [1040.0])
        crash_cb.assert_not_called()

    def test_on_pump_called_with_correct_price(self):
        pump_cb = MagicMock()
        s = make_sentinel(pump_threshold=3.0, cooldown_seconds=0, on_pump=pump_cb)
        for p in [1000.0] * 10:
            s.feed(p)
        s.feed(1040.0)
        pump_cb.assert_called_once_with(1040.0)

    def test_on_pump_not_called_on_crash(self):
        pump_cb = MagicMock()
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0, on_pump=pump_cb)
        _feed_prices(s, [1000.0] * 10 + [960.0])
        pump_cb.assert_not_called()

    def test_on_alert_not_called_without_alert(self):
        cb = MagicMock()
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0, on_alert=cb)
        _feed_prices(s, [1000.0] * 20)
        cb.assert_not_called()

    def test_callback_exception_does_not_propagate(self):
        def bad_cb(alert):
            raise RuntimeError("boom")

        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0, on_alert=bad_cb)
        # Should not raise
        s._on_message(None, json.dumps({"p": "1000"}))
        for _ in range(9):
            s._on_message(None, json.dumps({"p": "1000"}))
        s._on_message(None, json.dumps({"p": "960"}))  # triggers callback

    def test_on_crash_callback_exception_does_not_propagate(self):
        def bad_crash(price):
            raise RuntimeError("crash boom")

        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0, on_crash=bad_crash)
        for _ in range(10):
            s.feed(1000.0)
        s.feed(960.0)  # should not raise

    def test_on_pump_callback_exception_does_not_propagate(self):
        def bad_pump(price):
            raise RuntimeError("pump boom")

        s = make_sentinel(pump_threshold=3.0, cooldown_seconds=0, on_pump=bad_pump)
        for _ in range(10):
            s.feed(1000.0)
        s.feed(1040.0)  # should not raise


# ---------------------------------------------------------------------------
# feed() — public injection method
# ---------------------------------------------------------------------------

class TestFeed:
    def test_feed_returns_none_below_threshold(self):
        s = make_sentinel()
        for _ in range(10):
            s.feed(1000.0)
        assert s.feed(990.0) is None  # only −1%

    def test_feed_returns_alert_on_crash(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0)
        for _ in range(10):
            s.feed(1000.0)
        result = s.feed(960.0)
        assert result is not None
        assert result["type"] == "CRASH"

    def test_feed_works_with_empty_ws_url(self):
        """Offline/backtesting mode: feed() must work even without a ws_url."""
        s = make_sentinel(ws_url="", cooldown_seconds=0)
        for _ in range(10):
            s.feed(1000.0)
        result = s.feed(960.0)
        assert result is not None
        assert result["type"] == "CRASH"


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

class TestStatus:
    def test_initial_status(self):
        s = make_sentinel()
        st = s.status()
        assert st["running"] is False
        assert st["last_price"] == 0.0
        assert st["prices_in_window"] == 0
        assert st["reconnect_count"] == 0
        assert st["last_alert_at"] is None

    def test_status_updates_after_feed(self):
        s = make_sentinel()
        s.feed(1000.0)
        st = s.status()
        assert st["last_price"] == 1000.0
        assert st["prices_in_window"] == 1

    def test_last_alert_at_set_after_alert(self):
        s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0)
        for _ in range(10):
            s.feed(1000.0)
        s.feed(960.0)
        st = s.status()
        assert st["last_alert_at"] is not None


# ---------------------------------------------------------------------------
# subscribe_message
# ---------------------------------------------------------------------------

class TestSubscribeMessage:
    def test_subscribe_message_sent_on_open(self):
        msg = '{"method": "subscribe", "params": {"channel": "ticker"}}'
        s = make_sentinel(subscribe_message=msg)
        mock_ws = MagicMock()
        s._on_open(mock_ws)
        mock_ws.send.assert_called_once_with(msg)

    def test_no_subscribe_message_no_send(self):
        s = make_sentinel()
        mock_ws = MagicMock()
        s._on_open(mock_ws)
        mock_ws.send.assert_not_called()


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

class TestParsers:
    def test_binance_parses_price(self):
        raw = json.dumps({"e": "trade", "p": "67000.5", "q": "0.001"})
        assert parsers.binance(raw) == 67000.5

    def test_binance_returns_none_if_no_p_key(self):
        raw = json.dumps({"e": "ping"})
        assert parsers.binance(raw) is None

    def test_binance_raises_on_malformed_json(self):
        with pytest.raises(ParserError):
            parsers.binance("not json{{{")

    def test_kraken_parses_price(self):
        raw = json.dumps({"channel": "ticker", "data": [{"last": 67000.5}]})
        assert parsers.kraken(raw) == 67000.5

    def test_kraken_returns_none_for_non_ticker(self):
        raw = json.dumps({"channel": "heartbeat"})
        assert parsers.kraken(raw) is None

    def test_kraken_returns_none_for_empty_data(self):
        raw = json.dumps({"channel": "ticker", "data": []})
        assert parsers.kraken(raw) is None

    def test_coinbase_parses_price(self):
        raw = json.dumps({
            "channel": "ticker",
            "events": [{"tickers": [{"price": "67000.5"}]}]
        })
        assert parsers.coinbase(raw) == 67000.5

    def test_coinbase_returns_none_for_non_ticker(self):
        raw = json.dumps({"channel": "subscriptions", "events": []})
        assert parsers.coinbase(raw) is None

    def test_coinbase_returns_none_for_empty_events(self):
        raw = json.dumps({"channel": "ticker", "events": []})
        assert parsers.coinbase(raw) is None


# ---------------------------------------------------------------------------
# _on_message integration
# ---------------------------------------------------------------------------

class TestOnMessage:
    def test_on_message_ignores_non_price_message(self):
        cb = MagicMock()
        s = make_sentinel(on_alert=cb)
        s._on_message(None, json.dumps({"e": "ping"}))
        cb.assert_not_called()

    def test_on_message_ignores_malformed_json(self):
        cb = MagicMock()
        s = make_sentinel(on_alert=cb)
        s._on_message(None, "not json at all")
        cb.assert_not_called()

    def test_on_message_accumulates_prices(self):
        s = make_sentinel()
        for _ in range(5):
            s._on_message(None, json.dumps({"p": "1000.0"}))
        assert s.status()["prices_in_window"] == 5
