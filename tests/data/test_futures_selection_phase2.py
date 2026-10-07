import polars as pl
import pytest

from derivatives_bt_engine.data.futures_selection_phase2 import (
    run,
    slow_tilt_ewmac_weights,
)


def test_slow_tilt_ewmac_weights_use_sixty_forty_full_template():
    weights = slow_tilt_ewmac_weights(
        "4/16,8/32,16/64,32/128,64/256"
    )

    assert weights == pytest.approx({
        "4/16": 0.05,
        "8/32": 0.15,
        "16/64": 0.20,
        "32/128": 0.30,
        "64/256": 0.30,
    })
    assert weights["32/128"] + weights["64/256"] == pytest.approx(0.60)


def test_slow_tilt_ewmac_weights_renormalise_cost_eligible_subset():
    weights = slow_tilt_ewmac_weights(["64/256", "16/64", "32/128"])

    assert weights == pytest.approx({
        "16/64": 0.25,
        "32/128": 0.375,
        "64/256": 0.375,
    })
    assert sum(weights.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "rules, message",
    [
        ("", "at least one"),
        ("4/16,4/16", "duplicates"),
        ("2/8,4/16", "unsupported"),
    ],
)
def test_slow_tilt_ewmac_weights_reject_invalid_rule_sets(rules, message):
    with pytest.raises(ValueError, match=message):
        slow_tilt_ewmac_weights(rules)


def test_run_loads_explicit_phase1_csv_and_saves_selected_rows(tmp_path):
    source = tmp_path / "pysystemtrade_cost_phase1_ib_20261005_120000.csv"
    output = tmp_path / "phase2_step1.csv"
    pl.DataFrame({
        "symbol": ["MZC", "VIX"],
        "asset_cls": ["Ags", "Vol"],
        "ann_dvol": [600.126, 6_200.0],
        "avg_daily_volume": [2_000.0, None],
        "pct_mkt_volume": [0.2, None],
        "init_cap_usd": [100_000.0, 100_000.0],
        "target_vol": [0.2, 0.2],
        "cost_lim_sr": [0.01, 0.01],
        "rule_cost_lim_sr": [0.15, 0.15],
        "liq_ann_trades": [25.0, 25.0],
        "liq_days": [250, 250],
        "min_daily_volume": [100.0, 100.0],
        "max_pct_mkt_volume": [1.0, 1.0],
        "cost_elig": [True, True],
        "size_elig": [True, True],
        "liq_elig": [True, False],
        "data_elig": [True, False],
        "instr_has_elig_ewmac_rule": [True, True],
        "exec_elig": [True, True],
        "phase2_elig": [True, False],
        "ib_avail": ["contract_qualified", "unavailable_or_unverified"],
    }).write_csv(source)

    saved_path, selected = run([
        "--input",
        str(source),
        "--output",
        str(output),
    ])

    assert saved_path == output.resolve()
    assert selected.get_column("symbol").to_list() == ["MZC"]
    saved = pl.read_csv(output)
    assert saved.get_column("symbol").to_list() == ["MZC"]
    assert saved.get_column("ann_dvol").to_list() == [600.13]
    assert selected.get_column("ann_dvol").to_list() == [600.13]
    assert saved.get_column("init_cap_usd").to_list() == [100_000.0]
    assert saved.get_column("target_vol").to_list() == [0.2]
    assert saved.columns == selected.columns
