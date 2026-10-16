"""Compatibility imports for the multi-speed EWMAC strategy research facade."""

from derivatives_bt_engine.strats import multi_speed_ewmac as _implementation

globals().update({
    name: value
    for name, value in vars(_implementation).items()
    if not name.startswith("__")
})
