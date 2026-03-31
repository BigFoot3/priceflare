# Changelog

All notable changes to PriceFlare are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [0.2.0] — 2026-03-31

### Added
- `feed(price, timestamp=None)` — optional `timestamp` parameter for backtesting
  on historical data. Passing a custom Unix timestamp makes the rolling window
  behave correctly relative to the data rather than the wall clock.
- `subscribe_message` constructor parameter — JSON string sent to the server
  after WebSocket connection. Enables Kraken and Coinbase out of the box.
- `on_pump` callback — symmetric counterpart to `on_crash`, called with the
  current price on every PUMP alert.
- `max_reconnects` validation — raises `SentinelError` when `<= 0`.
- CI pipeline (GitHub Actions) — tests run on Python 3.11 and 3.12 on every
  push and pull request, with a hard coverage floor of 80%.
- `CONTRIBUTING.md` — contribution guidelines, architecture overview, parser
  authoring guide, release process.
- `CHANGELOG.md` — this file.

### Fixed
- `ws_url` empty-string validation moved from `__init__()` to `start()`.
  `feed()` now works with `ws_url=""` for offline/backtesting use.
- `except (ParserError, Exception)` simplified to `except Exception` in
  `_on_message` (redundant clause).
- Callback exceptions (`on_alert`, `on_crash`, `on_pump`) are now logged to
  stdout instead of being silently swallowed.

### Changed
- `_feed_prices()` test helper uses the new `ts` parameter instead of
  `unittest.mock.patch("time.time", ...)`.
- `patch` import removed from test file (no longer needed).

### Tests
- 57 → 61 tests.
- `TestFeedWithTimestamp` — 4 new tests covering historical timestamps,
  window eviction, cooldown, and real-time fallback.
- `TestSentinelInit` — 2 new tests for `max_reconnects` validation.
- `TestCallbacks` — 3 new tests for `on_pump` and callback exception handling.
- `TestSubscribeMessage` — 2 new tests.

---

## [0.1.0] — 2026-03-28

### Added
- Initial release.
- `Sentinel` class — exchange-agnostic crash/pump detector over WebSocket.
- Built-in parsers: `parsers.binance`, `parsers.kraken`, `parsers.coinbase`.
- `PriceFlareError`, `ParserError`, `SentinelError` exception hierarchy.
- `feed()` method for offline price injection.
- `status()` method for runtime state inspection.
- Exponential backoff reconnection with `_stop_event.wait()` (interruptible).
- Thread-safe rolling window with `threading.Lock`.
- 47 tests.
