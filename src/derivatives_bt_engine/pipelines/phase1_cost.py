"""Compatibility imports for the former phase-named market-screen module."""

from derivatives_bt_engine.pipelines import market_screen as _implementation

globals().update({
    name: value
    for name, value in vars(_implementation).items()
    if not name.startswith("__")
})
