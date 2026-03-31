# PriceFlare

**Exchange-agnostic real-time price crash and pump detector over WebSocket.**

Any trading bot needs to react immediately to sudden price movements. PriceFlare handles the detection — you supply the WebSocket URL, a price parser, and your callbacks.

```
WebSocket stream → price parser → rolling window → CRASH / PUMP → your callback
```

---

## Install

```bash
pip install priceflare
```

---

## Quickstart

```python
from priceflare import Sentinel
from priceflare import parsers

def on_alert(alert):
    print(f"[{alert['type']}] {alert['change_pct']:+.2f}% in {alert['window_sec']//60}min")
    print(f"  {alert['ref_price']} → {alert['cur_price']}")

def on_crash(price):
    print(f"Crash at {price} — checking stop-loss...")

sentinel = Sentinel(
    ws_url           = "wss://stream.binance.com:9443/ws/btcusdt@trade",
    price_parser     = parsers.binance,
    crash_threshold  = 3.0,   # % drop to trigger CRASH
    pump_threshold   = 3.0,   # % rise to trigger PUMP
    window_seconds   = 300,   # rolling window (5 min)
    cooldown_seconds = 600,   # min delay between alerts (10 min)
    on_alert         = on_alert,
    on_crash         = on_crash,   # optional — CRASH only
)

thread = sentinel.start()
```

---

## Built-in parsers

| Parser | Exchange | WebSocket URL |
|--------|----------|---------------|
| `parsers.binance` | Binance Spot/Futures | `wss://stream.binance.com:9443/ws/<symbol>@trade` |
| `parsers.kraken` | Kraken | `wss://ws.kraken.com/v2` |
| `parsers.coinbase` | Coinbase Advanced Trade | `wss://advanced-trade-ws.coinbase.com` |

### Custom parser

A parser is any callable `(raw: str) -> float | None`.
Return `None` to silently skip non-price messages.

```python
import json

def my_parser(raw: str) -> float | None:
    data = json.loads(raw)
    if data.get("type") != "trade":
        return None
    return float(data["price"])

sentinel = Sentinel(
    ws_url       = "wss://my-exchange.com/ws",
    price_parser = my_parser,
    on_alert     = on_alert,
)
```

---

## Alert format

```python
{
    "type":       "CRASH",        # or "PUMP"
    "change_pct": -4.21,          # signed percentage
    "ref_price":  67000.0,        # price at start of window
    "cur_price":  64178.0,        # current price
    "window_sec": 300,
    "timestamp":  "2026-03-31T08:00:00+00:00",
}
```

---

## Backtesting / offline use

Feed prices directly without a WebSocket connection using `feed()`:

```python
sentinel = Sentinel(
    ws_url       = "",            # unused in offline mode
    price_parser = parsers.binance,
    crash_threshold = 3.0,
    cooldown_seconds = 0,
    on_alert     = on_alert,
)

for price in historical_prices:
    alert = sentinel.feed(price)
    if alert:
        print(alert)
```

---

## API reference

### `Sentinel(ws_url, price_parser, **kwargs)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ws_url` | str | required | WebSocket URL |
| `price_parser` | callable | required | `(str) -> float \| None` |
| `crash_threshold` | float | `3.0` | % drop to trigger CRASH |
| `pump_threshold` | float | `3.0` | % rise to trigger PUMP |
| `window_seconds` | int | `300` | Rolling window in seconds |
| `cooldown_seconds` | int | `600` | Min seconds between alerts |
| `max_reconnects` | int | `50` | Max reconnection attempts |
| `on_alert` | callable | `None` | Called on CRASH or PUMP |
| `on_crash` | callable | `None` | Called on CRASH only |

### Methods

| Method | Description |
|--------|-------------|
| `start()` | Start in a background daemon thread. Returns the thread. |
| `stop()` | Signal the sentinel to stop. |
| `feed(price)` | Inject a price directly. Returns alert dict or None. |
| `status()` | Returns current state dict. |

---

## Tests

```bash
pip install -e ".[dev]"
pytest                   # 50 tests
pytest --cov=priceflare  # with coverage
```

---

## License

MIT — free to use, modify, and redistribute.
