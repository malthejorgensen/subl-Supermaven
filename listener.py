"""
Sublime Text event listener for Supermaven.

Hooks into view modifications and cursor movements to submit queries to the
sm-agent binary and poll for completions, then shows/hides ghost text.
"""

import time

# from typing import Any
import sublime
import sublime_plugin

from . import completion_manager
from . import plugin as _plugin  # for get_handler()

POLL_INTERVAL_MS = 25
POLL_TIMEOUT_MS = 5000


class SupermavenViewEventListener(sublime_plugin.ViewEventListener):
    """Attached to every view; drives completion requests and display."""

    def __init__(self, view):
        # type: (sublime.View) -> None
        super().__init__(view)
        self._modified = False  # type: bool
        self._poll_deadline = 0.0  # type: float
        self._polling = False  # type: bool

    # ------------------------------------------------------------------
    # ViewEventListener overrides
    # ------------------------------------------------------------------

    @classmethod
    def is_applicable(cls, settings):
        # type: (sublime.Settings) -> bool
        return True

    @classmethod
    def applies_to_primary_view_only(cls):
        # type: () -> bool
        return False

    def on_modified_async(self):
        # type: () -> None
        self._modified = True
        handler = _plugin.get_handler()
        if handler and handler.is_running():
            self._restart_polling()

    def on_selection_modified_async(self):
        # type: () -> None
        if not self._modified:
            # Pure cursor move with no text change — discard current ghost text
            sublime.set_timeout(
                lambda: completion_manager.hide_completion(self.view), 0
            )
        self._modified = False

    def on_deactivated_async(self):
        # type: () -> None
        sublime.set_timeout(lambda: completion_manager.hide_completion(self.view), 0)

    def on_close(self):
        # type: () -> None
        completion_manager.close_view(self.view)

    def on_query_context(self, key, operator, operand, match_all):
        # type: (str, int, Any, bool) -> bool | None
        if key == "supermaven.has_completion":
            value = completion_manager.has_completion(self.view)
            if operator == sublime.OP_EQUAL:
                return value == operand
            if operator == sublime.OP_NOT_EQUAL:
                return value != operand
        return None

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _restart_polling(self):
        # type: () -> None
        self._poll_deadline = time.monotonic() + POLL_TIMEOUT_MS / 1000
        if not self._polling:
            self._polling = True
            sublime.set_timeout_async(self._poll, POLL_INTERVAL_MS)

    def _poll(self):
        # type: () -> None
        """Background-thread poll: submit query and check for a completion."""
        handler = _plugin.get_handler()
        if not handler or not handler.is_running():
            self._polling = False
            return

        if time.monotonic() > self._poll_deadline:
            self._polling = False
            return

        sels = self.view.sel()
        if not sels:
            self._polling = False
            return

        cursor_pos = sels[0].begin()  # type: int
        content = self.view.substr(sublime.Region(0, self.view.size()))  # type: str
        file_path = self.view.file_name() or "<untitled:%s>" % (self.view.id(),)  # type: str
        prefix = content[:cursor_pos]  # type: str

        handler.submit_query(file_path, content, cursor_pos)
        text, prior_delete = handler.get_completion(prefix)

        if text:
            self._polling = False
            # Validate state is still current before showing
            sublime.set_timeout(
                lambda: self._show_if_current(text, prior_delete, cursor_pos, prefix),
                0,
            )
        else:
            sublime.set_timeout_async(self._poll, POLL_INTERVAL_MS)

    def _show_if_current(self, text, prior_delete, cursor_pos, prefix):
        # type: (str, int, int, str) -> None
        """Called on the main thread; only shows the completion if view state matches."""
        sels = self.view.sel()
        if not sels:
            return
        if sels[0].begin() != cursor_pos:
            return
        current_content = self.view.substr(sublime.Region(0, cursor_pos))
        if current_content != prefix:
            return
        completion_manager.show_completion(self.view, text, prior_delete, cursor_pos)
