"""
Supermaven – Sublime Text plugin entry point.

This file is loaded first by Sublime Text.  It owns the singleton
BinaryHandler instance and exposes get_handler() for the rest of the
package.  The other plugin-class files (listener.py, commands.py) are
loaded automatically by Sublime because they live in the same package
directory.
"""

# from typing import Union

import sublime

from .binary_handler import BinaryHandler

_handler = None  # type: Union[BinaryHandler, None]


def plugin_loaded():
    # type: () -> None
    global _handler
    _handler = BinaryHandler()
    # Start in a background thread so the binary download never blocks the UI
    sublime.set_timeout_async(_handler.start, 0)


def plugin_unloaded():
    # type: () -> None
    global _handler
    if _handler is not None:
        _handler.stop()
        _handler = None


def get_handler():
    # type: () -> Union[BinaryHandler, None]
    """Return the active BinaryHandler, or None if the plugin is not loaded."""
    return _handler
