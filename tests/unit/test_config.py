# tests/unit/test_config.py
from portfolio_intel.config import Settings


def test_settings_load_defaults():
    s = Settings()
    assert s.base_currency == "USD"
    assert s.risk_window_days > 0
