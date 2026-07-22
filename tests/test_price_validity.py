"""Tests for A4 price-validity dimensions and the immutable price record."""

import pytest

from src.costs import (
    ModelPrice,
    StalePriceError,
    UnverifiedPriceError,
    assert_price_fresh,
    price_record,
)


def _price(**overrides):
    base = dict(
        input_per_mtok=1.0,
        output_per_mtok=2.0,
        status="verified",
        last_verified="2026-07-08",
    )
    base.update(overrides)
    return ModelPrice(**base)


def test_fresh_price_within_window_passes():
    p = _price(valid_from="2026-07-01", valid_until="2026-12-31")
    assert_price_fresh(p, "2026-07-21") is None


def test_expired_price_is_refused():
    p = _price(valid_until="2026-07-20")
    with pytest.raises(StalePriceError, match="expired"):
        assert_price_fresh(p, "2026-07-21")


def test_price_before_valid_from_is_refused():
    p = _price(valid_from="2026-08-01")
    with pytest.raises(StalePriceError, match="not valid until"):
        assert_price_fresh(p, "2026-07-21")


def test_effective_valid_from_falls_back_to_last_verified():
    p = _price(last_verified="2026-07-08")  # no explicit valid_from
    assert p.effective_valid_from == "2026-07-08"
    with pytest.raises(StalePriceError):
        assert_price_fresh(p, "2026-07-01")  # before verification date


def test_require_bounded_rejects_open_ended_price():
    p = _price(valid_until=None)  # no expiry recorded
    # Lenient (estimates) accepts it...
    assert_price_fresh(p, "2026-07-21")
    # ...canonical runs must pin an expiry.
    with pytest.raises(StalePriceError, match="bounded"):
        assert_price_fresh(p, "2026-07-21", require_bounded=True)


def test_lane_mismatch_refused():
    p = _price(lane="sync")
    with pytest.raises(StalePriceError, match="lane"):
        assert_price_fresh(p, "2026-07-21", lane="batch")


def test_price_record_is_immutable_snapshot():
    rec = price_record("deepseek-v4-flash")
    assert rec["model"] == "deepseek-v4-flash"
    assert rec["currency"] == "USD"
    assert rec["input_per_mtok"] == 0.14
    assert rec["lane"] == "sync"
    assert rec["valid_from"] == "2026-07-08"  # falls back to last_verified
    assert rec["source"]


def test_price_record_unknown_model_raises():
    with pytest.raises(UnverifiedPriceError):
        price_record("no-such-model")
