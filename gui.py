"""Desktop entry point for 12306FairTicket.

The command-line application deliberately remains independent from Qt.  This
small launcher keeps that property and provides a useful error when the GUI
extra has not been installed yet.
"""

from __future__ import annotations

import os
import sys
from importlib.util import find_spec
from pathlib import Path


def _use_bundled_qt_plugins() -> None:
    # A conda-installed Qt drops bin/qt6.conf next to python, which redirects PySide6
    # to that (ABI-incompatible) Qt's plugin dir and breaks platform plugin loading.
    spec = find_spec("PySide6")
    if spec is None or not spec.origin or "QT_PLUGIN_PATH" in os.environ:
        return
    plugins = Path(spec.origin).parent / "Qt" / "plugins"
    if plugins.is_dir():
        os.environ["QT_PLUGIN_PATH"] = str(plugins)


def main() -> int:
    _use_bundled_qt_plugins()
    try:
        from ticket_app.gui.app import run_gui
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            print(
                "图形界面需要 PySide6。请先执行: pip install PySide6",
                file=sys.stderr,
            )
            return 2
        raise
    return run_gui(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
