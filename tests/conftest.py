"""Shared fixtures for the HAEO Klima Forecast test suite."""
from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/haeo_klima_forecast loadable as a real integration in tests."""
    yield
