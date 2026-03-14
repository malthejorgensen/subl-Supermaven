"""
Sublime Text commands for Supermaven.

  supermaven_accept_completion   – insert the full ghost-text completion
  supermaven_accept_word         – insert only the first word of the completion
  supermaven_dismiss_completion  – hide ghost text without inserting
  supermaven_use_free_version    – tell the binary to use the free tier
  supermaven_logout              – log out from Supermaven
"""

import sublime
import sublime_plugin

from . import completion_manager
from . import plugin as _plugin


def _next_word_end(text):
    # type: (str) -> int
    """Return the index just past the first 'word' in *text*.

    A word ends at the first whitespace character that comes after at least
    one non-whitespace character, or at the end of the string.
    """
    seen_non_ws = False
    for i, ch in enumerate(text):
        if ch in (" ", "\t", "\n", "\r"):
            if seen_non_ws:
                return i + 1  # include the trailing space so cursor lands nicely
        else:
            seen_non_ws = True
    return len(text)


class SupermavenAcceptCompletionCommand(sublime_plugin.TextCommand):
    """Insert the entire current ghost-text completion."""

    def run(self, edit):
        # type: (sublime.Edit) -> None
        text, prior_delete, _cursor_pos = completion_manager.get_completion(self.view)
        if not text:
            return

        completion_manager.hide_completion(self.view)

        sels = self.view.sel()
        if not sels:
            return
        cursor = sels[0].begin()

        # Delete the dedented prefix (if any) that the binary wants replaced
        if prior_delete > 0:
            line_start = self.view.line(cursor).begin()
            delete_from = max(line_start, cursor - prior_delete)
            self.view.erase(edit, sublime.Region(delete_from, cursor))
            sels = self.view.sel()
            cursor = sels[0].begin()

        self.view.insert(edit, cursor, text)

    def is_enabled(self):
        # type: () -> bool
        return completion_manager.has_completion(self.view)


class SupermavenAcceptWordCommand(sublime_plugin.TextCommand):
    """Insert only the first word of the current ghost-text completion."""

    def run(self, edit):
        # type: (sublime.Edit) -> None
        text, prior_delete, _cursor_pos = completion_manager.get_completion(self.view)
        if not text:
            return

        word = text[: _next_word_end(text)]
        completion_manager.hide_completion(self.view)

        sels = self.view.sel()
        if not sels:
            return
        cursor = sels[0].begin()

        if prior_delete > 0:
            line_start = self.view.line(cursor).begin()
            delete_from = max(line_start, cursor - prior_delete)
            self.view.erase(edit, sublime.Region(delete_from, cursor))
            sels = self.view.sel()
            cursor = sels[0].begin()

        self.view.insert(edit, cursor, word)

    def is_enabled(self):
        # type: () -> bool
        return completion_manager.has_completion(self.view)


class SupermavenDismissCompletionCommand(sublime_plugin.TextCommand):
    """Hide the current ghost-text completion without inserting anything."""

    def run(self, edit):
        # type: (sublime.Edit) -> None
        completion_manager.hide_completion(self.view)

    def is_enabled(self):
        # type: () -> bool
        return completion_manager.has_completion(self.view)


class SupermavenUseFreeVersionCommand(sublime_plugin.WindowCommand):
    """Switch the running binary to the free tier."""

    def run(self):
        # type: () -> None
        handler = _plugin.get_handler()
        if handler:
            handler.use_free_version()
            sublime.status_message("Supermaven: Using free version.")
        else:
            sublime.status_message("Supermaven: Not running.")


class SupermavenLogoutCommand(sublime_plugin.WindowCommand):
    """Log out from Supermaven."""

    def run(self):
        # type: () -> None
        handler = _plugin.get_handler()
        if handler:
            handler.logout()
            sublime.status_message("Supermaven: Logged out.")
        else:
            sublime.status_message("Supermaven: Not running.")


class SupermavenRestartCommand(sublime_plugin.WindowCommand):
    """Stop and restart the sm-agent binary."""

    def run(self):
        # type: () -> None
        handler = _plugin.get_handler()
        if handler:
            handler.stop()
            sublime.set_timeout_async(handler.start, 100)
            sublime.status_message("Supermaven: Restarting…")
        else:
            sublime.status_message("Supermaven: Not running.")
