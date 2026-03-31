# Contributing to PriceFlare

Thank you for your interest in contributing to PriceFlare! This document provides guidelines and instructions for contributing to this open-source exchange-agnostic crash and pump detector over WebSocket.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Architecture](#project-architecture)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [How to Contribute](#how-to-contribute)
- [Release Process](#release-process)

## Code of Conduct

This project is committed to providing a welcoming and inclusive experience for everyone. We expect all contributors to:

- Be respectful and constructive in all interactions
- Focus on what is best for the community and the project
- Show empathy towards others
- Accept constructive criticism gracefully

## Getting Started

### Prerequisites

- Python 3.11 or higher
- pip for package management
- Git

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:

```bash
git clone https://github.com/YOUR_USERNAME/priceflare.git
cd priceflare
```

3. Add the upstream remote:

```bash
git remote add upstream https://github.com/BigFoot3/priceflare.git
```

## Development Setup

### 1. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -e ".[dev]"
```

This installs the package in editable mode with all development dependencies (`pytest`, `pytest-cov`).

### 3. Verify Setup

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=priceflare --cov-report=term-missing
```

You should see `57 passed`.

> **Note:** PriceFlare has no environment variables or configuration files — no `.env` setup required.

## Project Architecture

### Module Overview

```
priceflare/
├── __init__.py      # Public API exports (Sentinel, parsers, exceptions)
├── sentinel.py      # Sentinel — core detector class
├── parsers.py       # Built-in price parsers (Binance, Kraken, Coinbase)
└── exceptions.py    # PriceFlareError, ParserError, SentinelError
```

### Core Flow

```
WebSocket stream
      ↓
  _on_message()
      ↓
  price_parser(raw)  →  float | None
      ↓
  _check_movement(price)
  ├── append to rolling window
  ├── evict expired prices
  ├── compare current vs ref_price
  └── if threshold crossed + cooldown elapsed:
          build alert dict
              ↓
          _dispatch(alert)
          ├── print to stdout
          ├── on_alert(alert)
          ├── on_crash(price)  [CRASH only]
          └── on_pump(price)   [PUMP only]
```

### Key Design Principles

1. **Exchange-agnostic**: The `Sentinel` knows nothing about exchanges. All exchange-specific logic lives in `parsers.py` (or in your own callable).
2. **Thread-safe**: All mutable state is protected by a single `threading.Lock`. Callbacks are always fired outside the lock.
3. **Offline-friendly**: `feed()` allows injecting prices directly without a WebSocket — useful for backtesting and unit testing.
4. **Minimal dependencies**: Only `websocket-client` is required at runtime.
5. **Fail-safe callbacks**: Exceptions in user callbacks are caught and logged — they never crash the sentinel loop.

## Coding Standards

### Python Style

- Follow PEP 8
- Use type hints for all function signatures
- Maximum line length: 100 characters
- Use docstrings for all public classes and methods

### Example

```python
def binance(raw: str) -> float | None:
    """
    Binance Spot/Futures trade stream parser.

    WebSocket URL: wss://stream.binance.com:9443/ws/<symbol>@trade

    Args:
        raw: Raw WebSocket message string.

    Returns:
        Trade price as a float, or None if the message is not a trade event.

    Raises:
        ParserError: If the message cannot be parsed.
    """
    try:
        data = json.loads(raw)
        if "p" not in data:
            return None
        return float(data["p"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise ParserError(f"binance parser failed: {e}") from e
```

### Naming Conventions

- Classes: `PascalCase` (e.g., `Sentinel`, `PriceFlareError`)
- Functions/Variables: `snake_case` (e.g., `check_movement`, `ref_price`)
- Constants: `UPPER_SNAKE_CASE`
- Private methods: `_leading_underscore` (e.g., `_check_movement`, `_dispatch`)

### Import Order

1. Standard library
2. Third-party
3. Local modules

```python
import json
import threading
from datetime import datetime, timezone

import websocket

from .exceptions import ParserError, SentinelError
```

### Adding a New Parser

A parser is any callable with the signature `(raw: str) -> float | None`.

1. Add the function to `parsers.py`
2. Document the WebSocket URL and subscription message (if required)
3. Include the expected message format in the docstring
4. Add tests in `tests/test_sentinel.py` under `TestParsers`

```python
def myexchange(raw: str) -> float | None:
    """
    MyExchange trade stream.

    WebSocket URL: wss://ws.myexchange.com/trades
    Subscribe msg: {"action": "subscribe", "symbol": "BTCUSD"}

    Message format:
        {"type": "trade", "price": "67000.50", ...}
    """
    try:
        data = json.loads(raw)
        if data.get("type") != "trade":
            return None
        return float(data["price"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise ParserError(f"myexchange parser failed: {e}") from e
```

## Testing Guidelines

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=priceflare --cov-report=term-missing

# Run specific test class
pytest tests/test_sentinel.py::TestCrashDetection -v

# Run tests matching a keyword
pytest -k "subscribe or pump" -v
```

### Test Coverage

- Aim for 80%+ code coverage
- All new features must include tests
- WebSocket internals (`_run`, `_on_close`, `_on_error`) are excluded from the coverage target — they require a live connection and are tested manually

### Writing Tests

Use the `make_sentinel()` helper and `_feed_prices()` for controlled timestamps:

```python
def test_my_parser_returns_none_for_unknown_event():
    raw = json.dumps({"type": "heartbeat"})
    assert parsers.myexchange(raw) is None


def test_crash_triggers_on_pump_not_called():
    pump_cb = MagicMock()
    s = make_sentinel(crash_threshold=3.0, cooldown_seconds=0, on_pump=pump_cb)
    _feed_prices(s, [1000.0] * 10 + [960.0])  # CRASH, not PUMP
    pump_cb.assert_not_called()
```

Use `patch("time.time", return_value=...)` to control the rolling window in time-sensitive tests.

### Test Categories

1. **Init validation** (`TestSentinelInit`): Constructor guards — invalid parameters raise `SentinelError`
2. **Movement detection** (`TestCrashDetection`, `TestPumpDetection`): Threshold logic, direction, keys
3. **Cooldown** (`TestCooldown`): Alert rate limiting
4. **Window eviction** (`TestWindowEviction`): Stale price removal
5. **Callbacks** (`TestCallbacks`): All three callbacks (`on_alert`, `on_crash`, `on_pump`) — including isolation and exception handling
6. **feed()** (`TestFeed`): Offline/backtesting mode
7. **status()** (`TestStatus`): Internal state reporting
8. **subscribe_message** (`TestSubscribeMessage`): Exchange subscription on connect
9. **Parsers** (`TestParsers`): Per-exchange parsing, None returns, error cases
10. **_on_message** (`TestOnMessage`): Full pipeline from raw string

## How to Contribute

### Reporting Bugs

1. Check if the bug is already reported in [Issues](https://github.com/BigFoot3/priceflare/issues)
2. Create a new issue with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs actual behavior
   - Python version and environment details
   - Error messages and stack traces

### Suggesting Features

1. Open an issue with the `enhancement` label
2. Describe the use case and proposed solution
3. Discuss with maintainers before implementing

### Adding Exchange Support

Exchanges are the most common contribution. Before submitting a new parser:

1. Verify the WebSocket URL and message format against the official exchange documentation
2. Test manually with a live connection
3. Document the `subscribe_message` if required (Kraken-style exchanges)
4. Add at minimum: a happy-path test, a None-return test, and an error-case test

### Pull Request Process

1. **Create a Branch**

   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```

2. **Make Changes**

   - Write clean, documented code
   - Add tests for new functionality
   - Update `README.md` if the public API changes

3. **Run Quality Checks**

   ```bash
   # Run tests
   pytest

   # Run with coverage
   pytest --cov=priceflare --cov-report=term-missing

   # Optional — type checking
   mypy priceflare/
   ```

4. **Commit Changes**

   Use clear, descriptive commit messages following [Conventional Commits](https://www.conventionalcommits.org/):

   ```
   feat: add OKX parser

   - Implement parsers.okx() for OKX spot ticker stream
   - Add subscribe_message example in docstring
   - Add 3 tests (parse, none return, error case)

   Fixes #42
   ```

5. **Push and Create PR**

   ```bash
   git push origin feature/your-feature-name
   ```

   Then open a Pull Request on GitHub with:

   - Clear title and description
   - Link to related issues
   - Manual test evidence if the change touches WebSocket behavior

### Commit Message Format

- `feat:` — New feature (new parser, new parameter)
- `fix:` — Bug fix
- `docs:` — Documentation only
- `test:` — Adding or updating tests
- `refactor:` — Code refactoring without behavior change
- `perf:` — Performance improvement
- `chore:` — Maintenance (deps, CI, packaging)

## Release Process

1. Update version in `pyproject.toml`
2. Update `__version__` in `priceflare/__init__.py`
3. Update `README.md` test count if it changed
4. Create a git tag:

   ```bash
   git tag v0.x.x
   git push origin v0.x.x
   ```

5. Build and publish:

   ```bash
   python -m build
   twine upload dist/*
   ```

## Questions?

Open an issue for questions or discussions.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to PriceFlare! Together we're making trading bots more reactive. 📡📉🚀
