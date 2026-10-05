import polars as pl

from derivatives_bt_engine.data.futures_selection_phase2 import run


def test_run_loads_explicit_phase1_csv_and_saves_selected_rows(tmp_path):
    source = tmp_path / "pysystemtrade_cost_phase1_ib_20261005_120000.csv"
    output = tmp_path / "phase2_step1.csv"
    pl.DataFrame({
        "symbol": ["MZC", "VIX"],
        "asset_cls": ["Ags", "Vol"],
        "ann_dvol": [600.0, 6_200.0],
        "avg_daily_volume": [2_000.0, None],
        "pct_mkt_volume": [0.2, None],
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
    assert saved.columns == selected.columns
