"""
Binary handler for the Supermaven sm-agent process.

Manages the subprocess lifecycle, JSON-over-stdio protocol, and the
state_map that caches completions keyed by an incrementing state ID.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
import urllib.request
from typing import Any

import sublime

HARD_SIZE_LIMIT = 10_000_000
MAX_STATE_ID_RETENTION = 50


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _platform() -> str:
    s = platform.system()
    if s == "Darwin":
        return "macosx"
    if s == "Linux":
        return "linux"
    if s == "Windows":
        return "windows"
    return ""


def _arch() -> str:
    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "aarch64"
    if m in ("x86_64", "amd64"):
        return "x86_64"
    return ""


def _binary_dir() -> str:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = xdg if xdg else os.path.expanduser("~/.supermaven")
    return os.path.join(base, "binary", "v20", f"{_platform()}-{_arch()}")


def _binary_path() -> str:
    d = _binary_dir()
    name = "sm-agent.exe" if _platform() == "windows" else "sm-agent"
    return os.path.join(d, name)


def _fetch_binary() -> str | None:
    path = _binary_path()
    if os.path.isfile(path):
        return path

    os.makedirs(_binary_dir(), exist_ok=True)

    plat, arch = _platform(), _arch()
    if not plat or not arch:
        sublime.error_message("Supermaven: Unsupported platform/architecture.")
        return None

    discovery_url = (
        f"https://supermaven.com/api/download-path-v2"
        f"?platform={plat}&arch={arch}&editor=sublime"
    )
    try:
        with urllib.request.urlopen(discovery_url, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        download_url = data.get("downloadUrl")
        if not download_url:
            sublime.error_message("Supermaven: Could not find download URL.")
            return None

        sublime.status_message("Supermaven: Downloading binary…")
        with urllib.request.urlopen(download_url, timeout=120) as resp:
            content = resp.read()

        with open(path, "wb") as f:
            f.write(content)

        if _platform() != "windows":
            os.chmod(path, 0o755)

        sublime.status_message("Supermaven: Binary ready.")
        return path
    except Exception as exc:
        sublime.error_message(f"Supermaven: Failed to download binary: {exc}")
        return None


# ---------------------------------------------------------------------------
# Completion item processing
# ---------------------------------------------------------------------------


def _shares_common_prefix(s1: str, s2: str) -> bool:
    n = min(len(s1), len(s2))
    return s1[:n] == s2[:n]


def _strip_prefix(
    completion: list[dict[str, Any]], user_input: str
) -> list[dict[str, Any]] | None:
    """
    Strip the characters the user has already typed from the completion
    items.  Returns None if the completion no longer matches.
    """
    remaining: list[dict[str, Any]] = []
    prefix = user_input

    for item in completion:
        kind = item.get("kind")
        if kind == "text":
            text: str = item["text"]
            if not _shares_common_prefix(text, prefix):
                return None
            trim = min(len(text), len(prefix))
            text = text[trim:]
            prefix = prefix[trim:]
            if text:
                remaining.append({"kind": "text", "text": text})
        elif kind == "delete":
            remaining.append(item)
        elif kind == "dedent":
            if prefix:
                return None
            remaining.append(item)
        else:
            if not prefix:
                remaining.append(item)

    return remaining


def _derive_completion_text(
    completion: list[dict[str, Any]],
    dust_strings: list[str],
) -> tuple[str | None, int]:
    """
    Walk the completion item list and produce a plain-text completion
    string plus the number of chars to delete before the cursor (dedent).

    Returns (text, prior_delete) or (None, 0).
    """
    output = ""
    dedent = ""

    for item in completion:
        kind = item.get("kind")

        if kind == "text":
            output += item["text"]

        elif kind in ("barrier", "finish_edit"):
            if output.strip():
                return output.rstrip(), len(dedent)
            break

        elif kind == "end":
            if "\n" in output:
                return output.rstrip(), len(dedent)
            return None, 0

        elif kind == "dedent":
            dedent += item.get("text", "")

        elif kind in ("jump", "delete", "skip"):
            # Complex edit operations — not handled inline
            if output.strip():
                return output.rstrip(), len(dedent)
            return None, 0

    output = output.rstrip()
    if not output:
        return None, 0

    return output, len(dedent)


# ---------------------------------------------------------------------------
# BinaryHandler
# ---------------------------------------------------------------------------


class BinaryHandler:
    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # state_id -> {"prefix": str, "completion": list}
        self.state_map: dict[int, dict[str, Any]] = {}
        self.current_state_id: int = 0
        self.dust_strings: list[str] = []
        self.activate_url: str | None = None

        # Last submitted state — used to skip duplicate sends
        self._last_state: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Download the binary if needed, then spawn the process."""
        binary_path = _fetch_binary()
        if not binary_path:
            return False

        try:
            self._process = subprocess.Popen(
                [binary_path, "stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as exc:
            sublime.error_message(f"Supermaven: Failed to start binary: {exc}")
            return False

        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self._send_greeting()
        return True

    def stop(self) -> None:
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                pass
            self._process = None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------

    def _send_json(self, obj: dict[str, Any]) -> None:
        if not self.is_running():
            return
        try:
            msg = (json.dumps(obj) + "\n").encode("utf-8")
            assert self._process and self._process.stdin
            self._process.stdin.write(msg)
            self._process.stdin.flush()
        except Exception:
            pass

    def _read_loop(self) -> None:
        buf = b""
        assert self._process and self._process.stdout
        while self._process and self._process.poll() is None:
            try:
                chunk = self._process.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._process_line(line.decode("utf-8", errors="replace"))
            except Exception:
                break

    def _process_line(self, line: str) -> None:
        prefix = "SM-MESSAGE "
        if line.startswith(prefix):
            try:
                msg = json.loads(line[len(prefix) :])
                self._process_message(msg)
            except json.JSONDecodeError:
                pass

    def _process_message(self, msg: dict[str, Any]) -> None:
        kind = msg.get("kind")

        if kind == "response":
            state_id = int(msg.get("stateId", 0))
            with self._lock:
                state = self.state_map.get(state_id)
                if state is not None:
                    state["completion"].extend(msg.get("items", []))

        elif kind == "metadata":
            dust = msg.get("dustStrings")
            if dust is not None:
                self.dust_strings = dust

        elif kind == "activation_request":
            self.activate_url = msg.get("activateUrl")
            sublime.set_timeout(self._show_activation_dialog, 0)

        elif kind == "activation_success":
            self.activate_url = None

        elif kind == "service_tier":
            display = msg.get("display", "")
            if display:
                sublime.set_timeout(
                    lambda: sublime.status_message(f"Supermaven {display} is running."),
                    0,
                )

        elif kind == "passthrough":
            inner = msg.get("passthrough")
            if isinstance(inner, dict):
                self._process_message(inner)

    def _show_activation_dialog(self) -> None:
        if self.activate_url:
            sublime.message_dialog(
                "Supermaven: Please visit the following URL to activate:\n\n"
                f"{self.activate_url}\n\n"
                "Or run 'Supermaven: Use Free Version' from the Command Palette."
            )

    # ------------------------------------------------------------------
    # Protocol helpers
    # ------------------------------------------------------------------

    def _send_greeting(self) -> None:
        self._send_json({"kind": "greeting", "allowGitignore": False})

    def use_free_version(self) -> None:
        self._send_json({"kind": "use_free_version"})

    def logout(self) -> None:
        self._send_json({"kind": "logout"})

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _purge_old_states(self) -> None:
        cutoff = self.current_state_id - MAX_STATE_ID_RETENTION
        old = [k for k in self.state_map if k < cutoff]
        for k in old:
            del self.state_map[k]

    def submit_query(
        self, file_path: str, content: str, cursor_offset: int
    ) -> int | None:
        """
        Send a state_update to the binary for the given document/cursor state.
        Returns the state_id that will carry the completions, or None on error.

        Skips sending if the state hasn't changed since the last call.
        """
        if len(content) > HARD_SIZE_LIMIT:
            return None

        prefix = content[:cursor_offset]

        # Skip if nothing changed
        ls = self._last_state
        if ls is not None:
            if (
                ls["file_path"] == file_path
                and ls["cursor_offset"] == cursor_offset
                and ls["content"] == content
            ):
                return self.current_state_id

        with self._lock:
            self._purge_old_states()
            self.current_state_id += 1
            state_id = self.current_state_id

            self._send_json({"kind": "inform_file_changed", "path": file_path})
            self._send_json(
                {
                    "kind": "state_update",
                    "newId": str(state_id),
                    "updates": [
                        {"kind": "file_update", "path": file_path, "content": content},
                        {
                            "kind": "cursor_update",
                            "path": file_path,
                            "offset": cursor_offset,
                        },
                    ],
                }
            )

            self.state_map[state_id] = {"prefix": prefix, "completion": []}

        self._last_state = {
            "file_path": file_path,
            "cursor_offset": cursor_offset,
            "content": content,
        }
        return state_id

    def get_completion(self, prefix: str) -> tuple[str | None, int]:
        """
        Search the state_map for the best completion that matches *prefix*.
        Returns (completion_text, prior_delete) or (None, 0).
        """
        best_text: str | None = None
        best_prior_delete: int = 0
        best_length: int = 0
        best_state_id: int = -1

        with self._lock:
            for state_id, state in self.state_map.items():
                state_prefix: str = state.get("prefix", "")
                if len(prefix) < len(state_prefix):
                    continue
                if not prefix.startswith(state_prefix):
                    continue

                user_input = prefix[len(state_prefix) :]
                remaining = _strip_prefix(state["completion"], user_input)
                if remaining is None:
                    continue

                text_len = sum(
                    len(item["text"])
                    for item in remaining
                    if item.get("kind") == "text"
                )
                if text_len > best_length or (
                    text_len == best_length and state_id > best_state_id
                ):
                    candidate, prior_delete = _derive_completion_text(
                        remaining, self.dust_strings
                    )
                    if candidate:
                        best_text = candidate
                        best_prior_delete = prior_delete
                        best_length = text_len
                        best_state_id = state_id

        return best_text, best_prior_delete
