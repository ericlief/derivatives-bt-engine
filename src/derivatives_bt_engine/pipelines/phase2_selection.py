"""Compatibility imports for the former phase-named selection module."""

from derivatives_bt_engine.pipelines import portfolio_selection as _implementation

globals().update({
    name: value
    for name, value in vars(_implementation).items()
    if not name.startswith("__")
})
