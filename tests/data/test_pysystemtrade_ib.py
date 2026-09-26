import pytest

from derivatives_bt_engine.data.pysystemtrade_ib import load_pysystemtrade_ib_mapping


def test_bundled_carver_ib_mapping_has_expected_coverage_and_point_values():
    mapping = load_pysystemtrade_ib_mapping()

    assert mapping.height == 584
    assert mapping["instrument_code"].n_unique() == 584
    corn = mapping.filter(mapping["instrument_code"] == "CORN").row(0, named=True)
    assert corn["ib_symbol"] == "ZC"
    assert corn["ib_exchange"] == "CBOT"
    assert corn["ib_multiplier"] == pytest.approx(5000)
    assert corn["price_magnifier"] == pytest.approx(100)
    assert corn["ib_effective_point_value"] == pytest.approx(50)


def test_na_ib_currency_becomes_unspecified_not_literal_na():
    mapping = load_pysystemtrade_ib_mapping()
    corn = mapping.filter(mapping["instrument_code"] == "CORN").row(0, named=True)
    assert corn["ib_currency"] == ""
