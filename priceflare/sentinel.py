"""
PriceFlare — Sentinel.

Exchange-agnostic real-time price movement detector over WebSocket.

Usage:
    from priceflare import Sentinel
    from priceflare import parsers

    def on_alert(alert):
        print(f"{alert['type']} {alert['change_pct']:+.2f}%")

    sentinel = Sentinel(
        ws_url           = "wss://stream.binance.com:9443/ws/btcusdt@trade",
        price_parser     = parsers.binance,
        crash_threshold  = 3.0,
        pump_threshold   = 3.0,
        window_seconds   = 300,
        cooldown_seconds = 600,
        on_alert         = on_alert,
        on_crash         = lambda price: print(f"stop-loss check at {price}"),
        on_pump          = lambda price: print(f"pump detected at {price}"),
    )

    thread = sentinel.start()
    status = sentinel.status()
    sentinel.stop()

Offline / backtesting usage (no WebSocket):
    sentinel = Sentinel(
        ws_url       = "",            # unused — feed() only
        price_parser = parsers.binance,
        crash_threshold  = 3.0,
        cooldown_seconds = 0,
        on_alert     = on_alert,
    )
    for price in historical_prices:
        alert = sentinel.feed(price)
"""

import threading
import time
from datetime import datetime, timezone
from typing import Callable

import websocket

from .exceptions import SentinelError, ParserError


class Sentinel:
    """
    Real-time price movement detector over WebSocket.

    Detects CRASH and PUMP events by comparing the current price to the
    oldest price within a rolling time window. Thread-safe.

    Args:
        ws_url:            WebSocket URL to connect to.
                           May be empty when using feed() for offline/backtesting.
        price_parser:      Callable(raw: str) -> float | None.
                           Parses raw WebSocket messages into a price.
                           Return None to skip non-price messages silently.
        crash_threshold:   % drop to trigger a CRASH alert (default: 3.0).
        pump_threshold:    % rise to trigger a PUMP alert (default: 3.0).
        window_seconds:    Rolling window for price comparison (default: 300).
        cooldown_seconds:  Minimum delay between two alerts (default: 600).
        max_reconnects:    Max WebSocket reconnection attempts (default: 50).
        subscribe_message: JSON string sent to the server after connection.
                           Required for exchanges like Kraken or Coinbase that
                           need an explicit subscription message.
        on_alert:          Callback(alert: dict) called on CRASH or PUMP.
        on_crash:          Callback(price: float) called only on CRASH.
                           Intended for immediate stop-loss checks.
        on_pump:           Callback(price: float) called only on PUMP.
                           Intended for immediate opportunity checks.

    Note on reconnect_count:
        The counter resets to 0 on every successful connection (_on_open).
        This means a connection that repeatedly opens and immediately closes
        will never reach max_reconnects. This is intentional — max_reconnects
        guards against infrastructure failures, not flappy connections.

    Alert dict:
        {
            "type":       "CRASH" | "PUMP",
            "change_pct": float,   # signed percentage
            "ref_price":  float,   # price at start of window
            "cur_price":  float,   # current price
            "window_sec": int,
            "timestamp":  str,     # ISO 8601 UTC
        }
    """

    def __init__(
        self,
        ws_url: str,
        price_parser: Callable[[str], float | None],
        crash_threshold: float = 3.0,
        pump_threshold: float = 3.0,
        window_seconds: int = 300,
        cooldown_seconds: int = 600,
        max_reconnects: int = 50,
        subscribe_message: str | None = None,
        on_alert: Callable[[dict], None] | None = None,
        on_crash: Callable[[float], None] | None = None,
        on_pump: Callable[[float], None] | None = None,
    ) -> None:
        if not callable(price_parser):
            raise SentinelError("price_parser must be callable")
        if crash_threshold <= 0 or pump_threshold <= 0:
            raise SentinelError("crash_threshold and pump_threshold must be > 0")
        if window_seconds <= 0:
            raise SentinelError("window_seconds must be > 0")
        if cooldown_seconds < 0:
            raise SentinelError("cooldown_seconds must be >= 0")
        if max_reconnects <= 0:
            raise SentinelError("max_reconnects must be > 0")

        self._ws_url            = ws_url
        self._parser            = price_parser
        self._crash_threshold   = crash_threshold
        self._pump_threshold    = pump_threshold
        self._window_sec        = window_seconds
        self._cooldown_sec      = cooldown_seconds
        self._max_reconnects    = max_reconnects
        self._subscribe_message = subscribe_message
        self._on_alert          = on_alert
        self._on_crash          = on_crash
        self._on_pump           = on_pump

        # Internal state — protected by _lock
        self._lock            = threading.Lock()
        self._prices: list[tuple[float, float]] = []  # [(timestamp, price)]
        self._last_alert_ts   = 0.0
        self._last_price      = 0.0
        self._running         = False
        self._reconnect_count = 0
        self._stop_event      = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> threading.Thread:
        """
        Start the sentinel in a background daemon thread.

        Raises:
            SentinelError: if ws_url is empty (use feed() for offline mode).

        Returns:
            The running thread (for optional monitoring).
        """
        if not self._ws_url:
            raise SentinelError(
                "ws_url cannot be empty — use feed() for offline/backtesting"
            )
        self._stop_event.clear()
        t = threading.Thread(
            target=self._run,
            daemon=True,
            name="priceflare-sentinel",
        )
        t.start()
        return t

    def stop(self) -> None:
        """Signal the sentinel to stop after the current WebSocket cycle."""
        self._stop_event.set()

    def status(self) -> dict:
        """
        Return the current internal state of the sentinel.

        Returns:
            dict with keys: running, last_price, prices_in_window,
            reconnect_count, last_alert_at.
        """
        with self._lock:
            last_alert_at = (
                datetime.fromtimestamp(self._last_alert_ts, tz=timezone.utc).isoformat()
                if self._last_alert_ts > 0
                else None
            )
            return {
                "running":          self._running,
                "last_price":       self._last_price,
                "prices_in_window": len(self._prices),
                "reconnect_count":  self._reconnect_count,
                "last_alert_at":    last_alert_at,
            }

    # ------------------------------------------------------------------
    # Movement detection
    # ------------------------------------------------------------------

    def feed(self, price: float) -> dict | None:
        """
        Feed a price directly into the detector.

        Useful for testing or feeding prices from sources other than
        a WebSocket (e.g. REST polling, backtesting).
        Triggers on_alert / on_crash / on_pump callbacks if an alert fires.

        Returns:
            Alert dict if a movement is detected, None otherwise.
        """
        alert = self._check_movement(price)
        if alert:
            self._dispatch(alert)
        return alert

    def _check_movement(self, current_price: float) -> dict | None:
        now = time.time()

        with self._lock:
            self._prices.append((now, current_price))
            self._last_price = current_price

            # Evict prices outside the window
            self._prices = [
                (ts, p) for ts, p in self._prices
                if now - ts <= self._window_sec
            ]

            if len(self._prices) < 10:
                return None

            if now - self._last_alert_ts < self._cooldown_sec:
                return None

            ref_price  = self._prices[0][1]
            change_pct = round((current_price - ref_price) / ref_price * 100, 2)

            alert = None

            if change_pct <= -self._crash_threshold:
                alert = self._build_alert("CRASH", change_pct, ref_price, current_price)
            elif change_pct >= self._pump_threshold:
                alert = self._build_alert("PUMP",  change_pct, ref_price, current_price)

            if alert:
                self._last_alert_ts = now

        return alert

    def _build_alert(
        self,
        kind: str,
        change_pct: float,
        ref_price: float,
        cur_price: float,
    ) -> dict:
        return {
            "type":       kind,
            "change_pct": change_pct,
            "ref_price":  ref_price,
            "cur_price":  cur_price,
            "window_sec": self._window_sec,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # WebSocket internals
    # ------------------------------------------------------------------

    def _dispatch(self, alert: dict) -> None:
        """Fire callbacks and log for a confirmed alert."""
        emoji = "📉" if alert["type"] == "CRASH" else "🚀"
        ts    = alert["timestamp"][:19]
        print(
            f"[{ts}] {emoji} PriceFlare — {alert['type']} "
            f"{alert['change_pct']:+.2f}% in {alert['window_sec'] // 60}min "
            f"({alert['ref_price']} → {alert['cur_price']})",
            flush=True,
        )
        if self._on_alert:
            try:
                self._on_alert(alert)
            except Exception as e:
                print(f"⚠️  PriceFlare on_alert callback error: {e}", flush=True)

        if alert["type"] == "CRASH" and self._on_crash:
            try:
                self._on_crash(alert["cur_price"])
            except Exception as e:
                print(f"⚠️  PriceFlare on_crash callback error: {e}", flush=True)

        if alert["type"] == "PUMP" and self._on_pump:
            try:
                self._on_pump(alert["cur_price"])
            except Exception as e:
                print(f"⚠️  PriceFlare on_pump callback error: {e}", flush=True)

    def _on_message(self, ws, raw: str) -> None:
        try:
            price = self._parser(raw)
        except Exception:
            return
        if price is None:
            return
        alert = self._check_movement(price)
        if alert:
            self._dispatch(alert)

    def _on_open(self, ws) -> None:
        with self._lock:
            self._running         = True
            self._reconnect_count = 0
        print("✅ PriceFlare connected", flush=True)
        if self._subscribe_message:
            ws.send(self._subscribe_message)

    def _on_close(self, ws, code, msg) -> None:
        with self._lock:
            self._running = False
        print(f"⚠️  PriceFlare disconnected (code: {code})", flush=True)

    def _on_error(self, ws, error) -> None:
        print(f"⚠️  PriceFlare error: {error}", flush=True)

    def _run(self) -> None:
        """WebSocket loop with exponential backoff reconnection."""
        base_delay = 5

        while not self._stop_event.is_set():
            with self._lock:
                attempts = self._reconnect_count

            if attempts >= self._max_reconnects:
                print("❌ PriceFlare — too many reconnects, giving up.", flush=True)
                break

            try:
                ws = websocket.WebSocketApp(
                    self._ws_url,
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                ws.run_forever(ping_interval=30, ping_timeout=10)

            except Exception as e:
                print(f"❌ PriceFlare exception: {e}", flush=True)

            if self._stop_event.is_set():
                break

            with self._lock:
                self._reconnect_count += 1
                count = self._reconnect_count

            delay = min(base_delay * (2 ** min(count - 1, 4)), 60)
            print(
                f"🔄 PriceFlare reconnecting in {delay}s "
                f"({count}/{self._max_reconnects})...",
                flush=True,
            )
            self._stop_event.wait(timeout=delay)
