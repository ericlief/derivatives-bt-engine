"""One shared file logger for all derivatives-bt-engine modules.

Every package module may call :func:`setup_logger` during import.
The returned package logger owns the sole handler, while child loggers such as
``derivatives_bt_engine.live.tsmom_rebalance`` propagate to it. A live run and
a backtest therefore write one coherent, run-scoped log instead of each module
creating or bypassing an unrelated handler.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


_PACKAGE_LOGGER_NAME = 'derivatives_bt_engine'
_FILE_HANDLER_NAME = 'derivatives_bt_engine_file'
_IB_LOGGER_NAME = 'ib_insync'
_IB_FILE_HANDLER_NAME = 'derivatives_bt_engine_ib_file'


def _default_log_path() -> Path:
    """Return a project-root log path, independent of the caller's CWD."""
    # This module lives directly under ``src/derivatives_bt_engine``; two
    # parent hops reach the repository root regardless of the caller's CWD.
    project_root = Path(__file__).resolve().parents[2]
    logs_dir = project_root / 'logs'
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return logs_dir / f'derivatives_bt_engine_{timestamp}.log'


def setup_logger(log_file: Optional[str] = None) -> logging.Logger:
    """Configure and return the shared package logger.

    The file handler records DEBUG and above; there is deliberately no console
    handler. ``derivatives_bt_engine.*`` child loggers inherit this handler,
    so options backtests, futures backtests, and the live rebalance all write
    to the same run file. Repeated import-time calls are idempotent.

    ``log_file`` remains available for callers that need a specific path. It
    affects the first configuration in a process, preserving the former
    function's practical first-call behavior without creating duplicate files.
    """
    logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    # This project installs no console handler. Keep propagation enabled so
    # embedding applications and pytest's caplog handler can observe records;
    # in normal CLI use the sole project-installed sink is the shared file.
    logger.propagate = True

    if any(handler.get_name() == _FILE_HANDLER_NAME for handler in logger.handlers):
        return logger

    path = Path(log_file) if log_file is not None else _default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(path)
    file_handler.set_name(_FILE_HANDLER_NAME)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(name)s [%(levelname)s] %(message)s'
    ))
    logger.addHandler(file_handler)
    return logger


def setup_ib_logger(log_file: str | Path) -> logging.Logger:
    """Route IB API warnings and errors to one dedicated file.

    Parameters
    ----------
    log_file
        Destination for ``ib_insync`` diagnostics. Parent directories are
        created when needed.

    Returns
    -------
    logging.Logger
        The configured ``ib_insync`` namespace logger.

    Notes
    -----
    IB's wrapper emits request errors through ``ib_insync.wrapper``. Without
    an installed handler those records reach Python's last-resort stderr
    handler, which leaves the terminal output unavailable after a long market
    snapshot. Owning the parent namespace captures the same records in a
    durable audit file and prevents them from leaking into the application
    log or terminal.
    """
    logger = logging.getLogger(_IB_LOGGER_NAME)
    logger.setLevel(logging.WARNING)
    logger.propagate = False

    path = Path(log_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    # A notebook or test process can run more than one snapshot. Replace our
    # prior sink when the requested run path changes instead of duplicating
    # every IB record across stale files.
    for handler in logger.handlers[:]:
        if handler.get_name() != _IB_FILE_HANDLER_NAME:
            continue
        if Path(handler.baseFilename) == path.resolve():
            return logger
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(path)
    file_handler.set_name(_IB_FILE_HANDLER_NAME)
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(name)s [%(levelname)s] %(message)s'
    ))
    logger.addHandler(file_handler)
    return logger
