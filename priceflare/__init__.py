"""
PriceFlare — exchange-agnostic real-time price movement detector.

Detects CRASH and PUMP events over any WebSocket price stream.

Usage:
    from priceflare import Sentinel
    from priceflare import parsers

    sentinel = Sentinel(
        ws_url       = "wss://stream.binance.com:9443/ws/btcusdt@trade",
        price_parser = parsers.binance,
        on_alert     = lambda alert: print(alert),
    )
    sentinel.start()
"""

from .sentinel   import Sentinel
from .exceptions import PriceFlareError, ParserError, SentinelError
from . import parsers

__version__ = "0.1.0"
__all__ = [
    "Sentinel",
    "PriceFlareError",
    "ParserError",
    "SentinelError",
    "parsers",
]
