import sys
import types

import polars as pl

from derivatives_bt_engine.utils.tsmom_sheets import upload_tsmom_frames


def test_upload_tsmom_frames_names_each_non_empty_artifact(monkeypatch):
    uploads = []
    fake_gspread = types.ModuleType('derivatives_bt_engine.utils.gspread_log_util')
    fake_gspread.upload_df_to_google_sheets = lambda frame, strategy_name, spreadsheet_name: uploads.append(
        (frame, strategy_name, spreadsheet_name)
    )
    monkeypatch.setitem(sys.modules, 'derivatives_bt_engine.utils.gspread_log_util', fake_gspread)

    upload_tsmom_frames(
        spreadsheet_name='futures_bt', run_label='tsmom_backtest_MES',
        frames={
            'signals': pl.DataFrame({'symbol': ['MES']}),
            'empty': pl.DataFrame(schema={'symbol': pl.String}),
        },
    )

    assert len(uploads) == 1
    frame, tab_prefix, spreadsheet = uploads[0]
    assert frame.to_dicts() == [{'symbol': 'MES'}]
    assert tab_prefix == 'tsmom_backtest_MES_signals'
    assert spreadsheet == 'futures_bt'
