"""
PriceFlare — built-in price parsers.

A parser is any callable with the signature:
    parser(raw: str) -> float | None

Return None to silently skip the message (e.g. non-trade events).
Raise ParserError only on clearly malformed messages.

Built-in parsers
----------------
binance   — Binance Spot trade stream  (wss://stream.binance.com:9443/ws/<symbol>@trade)
kraken    — Kraken v2 ticker stream    (wss://ws.kraken.com/v2)
coinbase  — Coinbase Advanced Trade    (wss://advanced-trade-ws.coinbase.com)

Custom parser example
---------------------
    def my_parser(raw: str) -> float | None:
        data = json.loads(raw)
        if data.get("type") != "trade":
            return None
        return float(data["price"])

    sentinel = Sentinel(ws_url="wss://...", price_parser=my_parser, ...)
"""

import json
from .exceptions import ParserError


def binance(raw: str) -> float | None:
    """
    Binance Spot/Futures trade stream.

    WebSocket URL: wss://stream.binance.com:9443/ws/<symbol>@trade
    Example:       wss://stream.binance.com:9443/ws/btcusdt@trade

    Message format:
        {"e": "trade", "p": "67000.50", "q": "0.001", ...}
    """
    try:
        data = json.loads(raw)
        if "p" not in data:
            return None
        return float(data["p"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise ParserError(f"binance parser failed: {e}") from e


def kraken(raw: str) -> float | None:
    """
    Kraken v2 ticker stream.

    WebSocket URL: wss://ws.kraken.com/v2
    Subscribe msg: {"method": "subscribe", "params": {"channel": "ticker", "symbol": ["BTC/USD"]}}

    Message format:
        {"channel": "ticker", "data": [{"last": 67000.50, ...}]}
    """
    try:
        data = json.loads(raw)
        if data.get("channel") != "ticker":
            return None
        entries = data.get("data", [])
        if not entries:
            return None
        return float(entries[0]["last"])
    except (KeyError, ValueError, IndexError, json.JSONDecodeError) as e:
        raise ParserError(f"kraken parser failed: {e}") from e


def coinbase(raw: str) -> float | None:
    """
    Coinbase Advanced Trade WebSocket.

    WebSocket URL: wss://advanced-trade-ws.coinbase.com
    Subscribe msg: {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "ticker"}

    Message format:
        {"channel": "ticker", "events": [{"tickers": [{"price": "67000.50", ...}]}]}
    """
    try:
        data = json.loads(raw)
        if data.get("channel") != "ticker":
            return None
        events = data.get("events", [])
        if not events:
            return None
        tickers = events[0].get("tickers", [])
        if not tickers:
            return None
        return float(tickers[0]["price"])
    except (KeyError, ValueError, IndexError, json.JSONDecodeError) as e:
        raise ParserError(f"coinbase parser failed: {e}") from e
