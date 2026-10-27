import logging

from derivatives_bt_engine.logging_config import setup_ib_logger, setup_logger


def test_package_handlers_collect_child_logs_to_file_and_console(tmp_path, capsys):
    """Package descendants write debug logs to file and INFO logs to console."""
    package_logger = logging.getLogger('derivatives_bt_engine')
    original_handlers = package_logger.handlers[:]
    original_level = package_logger.level
    original_propagate = package_logger.propagate
    log_path = tmp_path / 'shared.log'

    try:
        for handler in original_handlers:
            package_logger.removeHandler(handler)

        configured = setup_logger(str(log_path))
        logging.getLogger('derivatives_bt_engine.live.tsmom_rebalance').debug('live decision')
        logging.getLogger('derivatives_bt_engine.strats.naked_futures').info('futures decision')
        for handler in configured.handlers:
            handler.flush()

        assert {handler.get_name() for handler in configured.handlers} == {
            'derivatives_bt_engine_file',
            'derivatives_bt_engine_console',
        }
        contents = log_path.read_text()
        assert 'live decision' in contents
        assert 'futures decision' in contents
        console = capsys.readouterr().err
        assert 'futures decision' in console
        assert 'live decision' not in console
    finally:
        for handler in package_logger.handlers[:]:
            package_logger.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            package_logger.addHandler(handler)
        package_logger.setLevel(original_level)
        package_logger.propagate = original_propagate


def test_ib_diagnostics_use_a_dedicated_non_propagating_file(tmp_path):
    """IB wrapper errors must remain auditable without entering the main log."""
    ib_logger = logging.getLogger('ib_insync')
    original_handlers = ib_logger.handlers[:]
    original_level = ib_logger.level
    original_propagate = ib_logger.propagate
    log_path = tmp_path / 'ib.log'

    try:
        for handler in original_handlers:
            ib_logger.removeHandler(handler)

        configured = setup_ib_logger(log_path)
        logging.getLogger('ib_insync.wrapper').error(
            'Error 460, reqId 7: No trading permissions.'
        )
        for handler in configured.handlers:
            handler.flush()

        assert not configured.propagate
        contents = log_path.read_text()
        assert 'Error 460, reqId 7: No trading permissions.' in contents
    finally:
        for handler in ib_logger.handlers[:]:
            ib_logger.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            ib_logger.addHandler(handler)
        ib_logger.setLevel(original_level)
        ib_logger.propagate = original_propagate
