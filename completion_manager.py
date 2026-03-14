"""
Per-view completion state and phantom rendering.

Each Sublime view gets a CompletionState that tracks whether a completion is
currently visible and holds the text to be inserted.  Phantoms are used for
the ghost-text display:

  • First line  → LAYOUT_INLINE phantom placed one character past the cursor
  • Extra lines → LAYOUT_BLOCK phantom placed at the cursor

This mirrors the approach used by LSP-copilot's _PhantomCompletion class.
"""

import html
import threading

import sublime

# Key used for both the per-view setting flag and the PhantomSet
_PHANTOM_KEY = "supermaven_completion"

_PHANTOM_TEMPLATE = """
<body id="supermaven-completion">
    <style>
        body {{
            margin: 0;
            padding: 0;
            color: color(var(--foreground) alpha(0.45));
            font-style: italic;
        }}
        .line {{
            line-height: 0;
            margin-top: {lpt}px;
            margin-bottom: {lpb}px;
        }}
        .line.first {{
            margin-top: 0;
        }}
    </style>
    {body}
</body>
"""

_LINE_TEMPLATE = '<div class="line {cls}">{content}</div>'


# ---------------------------------------------------------------------------
# Module-level state (per view)
# ---------------------------------------------------------------------------


class _CompletionState:
    __slots__ = ("text", "prior_delete", "cursor_pos", "is_visible", "phantom_set")

    def __init__(self):
        # type: () -> None
        self.text = ""  # type: str
        self.prior_delete = 0  # type: int
        self.cursor_pos = -1  # type: int
        self.is_visible = False  # type: bool
        self.phantom_set = None  # type: sublime.PhantomSet | None


_states = {}  # type: dict[int, _CompletionState]
_lock = threading.Lock()


def _get_state(view):
    # type: (sublime.View) -> _CompletionState
    vid = view.id()
    with _lock:
        if vid not in _states:
            _states[vid] = _CompletionState()
        return _states[vid]


def _remove_state(view):
    # type: (sublime.View) -> None
    with _lock:
        _states.pop(view.id(), None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(line, tab_size=4):
    # type: (str, int) -> str
    """Escape HTML and replace whitespace for phantom rendering."""
    return html.escape(line).replace(" ", "&nbsp;").replace("\t", "&nbsp;" * tab_size)


def _build_body(lines, tab_size):
    # type: (list[str], int) -> str
    if len(lines) == 1:
        return _normalize(lines[0], tab_size)
    return "".join(
        _LINE_TEMPLATE.format(
            cls="first" if i == 0 else "rest",
            content=_normalize(line, tab_size),
        )
        for i, line in enumerate(lines)
    )


def _render_html(lines, view):
    # type: (list[str], sublime.View) -> str
    lpt = int(view.settings().get("line_padding_top", 0)) * 2
    lpb = int(view.settings().get("line_padding_bottom", 0)) * 2
    tab_size = int(view.settings().get("tab_size", 4))
    body = _build_body(lines, tab_size)
    return _PHANTOM_TEMPLATE.format(body=body, lpt=lpt, lpb=lpb)


# ---------------------------------------------------------------------------
# Public API  (must be called from the main thread)
# ---------------------------------------------------------------------------


def show_completion(view, text, prior_delete, cursor_pos):
    # type: (sublime.View, str, int, int) -> None
    """Render *text* as ghost text at *cursor_pos* in *view*."""
    state = _get_state(view)
    state.text = text
    state.prior_delete = prior_delete
    state.cursor_pos = cursor_pos
    state.is_visible = True

    view.settings().set("supermaven.has_completion", True)

    if state.phantom_set is None:
        state.phantom_set = sublime.PhantomSet(view, _PHANTOM_KEY)

    lines = text.splitlines() or [""]
    first_line_html = _render_html([lines[0]], view)
    rest_lines = lines[1:]

    phantoms = []  # type: list[sublime.Phantom]

    # Inline phantom for the first line — anchored at the cursor so the
    # preview matches the eventual insertion point.
    inline_pos = cursor_pos
    phantoms.append(
        sublime.Phantom(
            sublime.Region(inline_pos, inline_pos),
            first_line_html,
            sublime.LAYOUT_INLINE,
        )
    )

    # Block phantom for remaining lines (below the current line)
    if rest_lines:
        rest_html = _render_html(rest_lines, view)
        phantoms.append(
            sublime.Phantom(
                sublime.Region(cursor_pos, cursor_pos),
                rest_html,
                sublime.LAYOUT_BLOCK,
            )
        )

    state.phantom_set.update(phantoms)


def hide_completion(view):
    # type: (sublime.View) -> None
    """Remove ghost text from *view*."""
    state = _get_state(view)
    if not state.is_visible:
        return
    state.is_visible = False
    state.text = ""
    state.prior_delete = 0
    view.settings().set("supermaven.has_completion", False)
    if state.phantom_set is not None:
        state.phantom_set.update([])


def has_completion(view):
    # type: (sublime.View) -> bool
    state = _get_state(view)
    return state.is_visible and bool(state.text)


def get_completion(view):
    # type: (sublime.View) -> tuple[str, int, int]
    """Return (text, prior_delete, cursor_pos) for the current completion."""
    state = _get_state(view)
    return state.text, state.prior_delete, state.cursor_pos


def close_view(view):
    # type: (sublime.View) -> None
    """Called when a view is closed; cleans up state."""
    hide_completion(view)
    _remove_state(view)
