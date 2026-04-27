"""Shared pytest fixtures."""
from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: requires real LLM/API calls")
    config.addinivalue_line("markers", "regression: end-to-end agent regression cases")
    config.addinivalue_line("markers", "live: requires real PPE infrastructure access")
