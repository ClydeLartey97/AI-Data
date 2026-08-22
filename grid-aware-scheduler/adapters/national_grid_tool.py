"""Deferred access to the National Grid Tool's GB data clients.

GB is the only market here whose price and carbon arrive through a sibling
checkout rather than a direct HTTP call, because that project already had
tested Elexon and Carbon Intensity clients and reimplementing them would have
been duplication for its own sake. CAISO, NYISO and MISO each talk to their
operator directly and need nothing from it.

The import happens on the first GB fetch rather than at module import, so a
machine that only ever plans against a US market runs without that folder
present. Before this, importing ``adapters.gb`` failed outright and took the
whole server down with it — including for an operator who had no interest in
GB at all.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "National-Grid-Tool"


def project_path() -> Path:
    """Where the National Grid Tool is expected to be checked out."""
    return Path(os.environ.get("NATIONAL_GRID_TOOL_PATH", _DEFAULT_PATH))


def available() -> bool:
    """Whether GB market data can be fetched on this machine."""
    return (project_path() / "sources").is_dir()


def load(module: str, *names: str) -> tuple:
    """Import ``names`` from a National Grid Tool module, on first use."""
    root = project_path()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        loaded = importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"GB market data needs the National Grid Tool, which was not found "
            f"at {root}. Set NATIONAL_GRID_TOOL_PATH to point at that project, "
            f"or use a market that does not depend on it: CAISO, NYISO or MISO."
        ) from exc
    return tuple(getattr(loaded, name) for name in names)
