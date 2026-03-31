"""
PriceFlare — exceptions.
"""


class PriceFlareError(Exception):
    """Base exception for all PriceFlare errors."""


class ParserError(PriceFlareError):
    """Raised when a price parser fails to extract a valid price."""


class SentinelError(PriceFlareError):
    """Raised when the Sentinel is misconfigured or fails to start."""
