"""Guard the concrete runtime-package layout against architectural drift."""

import ast
from pathlib import Path


PACKAGE_ROOT = (
    Path(__file__).resolve().parents[1] / "src" / "derivatives_bt_engine"
)


def _imported_modules(path: Path) -> list[str]:
    """Return absolute module names imported by one Python source file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_generic_domain_package_does_not_return():
    """Code must live in a package named for its concrete responsibility."""
    assert not (PACKAGE_ROOT / "domain").exists()
    for path in PACKAGE_ROOT.rglob("*.py"):
        assert all(
            not module.startswith("derivatives_bt_engine.domain")
            for module in _imported_modules(path)
        ), path


def test_generic_utils_and_enum_buckets_do_not_return():
    """Infrastructure and vocabulary must live with concrete owners."""
    assert not (PACKAGE_ROOT / "utils").exists()
    assert not (PACKAGE_ROOT / "calculations" / "enums.py").exists()
    assert not (PACKAGE_ROOT / "backtest" / "types.py").exists()
    forbidden = (
        "derivatives_bt_engine.utils",
        "derivatives_bt_engine.calculations.enums",
        "derivatives_bt_engine.backtest.types",
    )
    for path in PACKAGE_ROOT.rglob("*.py"):
        assert all(
            not module.startswith(forbidden)
            for module in _imported_modules(path)
        ), path


def test_calculations_do_not_depend_on_io_or_orchestration_packages():
    """Pure calculations may not reach upward into runtime workflow layers."""
    forbidden = (
        "derivatives_bt_engine.data",
        "derivatives_bt_engine.backtest",
        "derivatives_bt_engine.pipelines",
        "derivatives_bt_engine.live",
        "derivatives_bt_engine.reports",
        "derivatives_bt_engine.strats",
    )
    for path in (PACKAGE_ROOT / "calculations").glob("*.py"):
        imports = _imported_modules(path)
        assert not [
            module for module in imports if module.startswith(forbidden)
        ], path


def test_data_and_backtest_packages_do_not_import_workflow_layers():
    """Lower layers must remain reusable outside research and live runners."""
    forbidden_by_package = {
        "data": (
            "derivatives_bt_engine.backtest",
            "derivatives_bt_engine.pipelines",
            "derivatives_bt_engine.live",
            "derivatives_bt_engine.reports",
            "derivatives_bt_engine.strats",
        ),
        "backtest": (
            "derivatives_bt_engine.pipelines",
            "derivatives_bt_engine.live",
            "derivatives_bt_engine.reports",
            "derivatives_bt_engine.strats",
        ),
    }
    for package, forbidden in forbidden_by_package.items():
        for path in (PACKAGE_ROOT / package).glob("*.py"):
            imports = _imported_modules(path)
            assert not [
                module for module in imports if module.startswith(forbidden)
            ], path
