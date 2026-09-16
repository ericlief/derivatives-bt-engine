"""Opt-in Google Sheets uploads for the compact TSMOM reporting contract."""
from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()


def upload_tsmom_frames(*, spreadsheet_name: str, run_label: str,
                        frames: Mapping[str, pl.DataFrame]) -> None:
    """Upload non-empty TSMOM report frames to timestamped tabs.

    Import the Google client only when explicitly requested. This keeps all
    normal CLI runs independent of service-account configuration and avoids
    making merely importing a strategy authenticate with Google.
    """
    from derivatives_bt_engine.utils.gspread_log_util import upload_df_to_google_sheets

    for artifact, frame in frames.items():
        if frame.is_empty():
            logger.info('Skipping empty TSMOM Sheets artifact: %s', artifact)
            continue
        strategy_name = f'{run_label}_{artifact}'
        logger.info('Uploading TSMOM %s (%d rows) to spreadsheet %r',
                    artifact, frame.height, spreadsheet_name)
        upload_df_to_google_sheets(
            frame, strategy_name=strategy_name, spreadsheet_name=spreadsheet_name,
        )
