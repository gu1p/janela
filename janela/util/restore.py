"""Helpers for restoring windows to reasonable fallback bounds."""

from typing import Optional, Sequence, Tuple

from janela.interfaces.models import Monitor, Window

_MIN_WIDTH = 800
_MIN_HEIGHT = 600


def fallback_restore_bounds(
    window: Window, monitor: Optional[Monitor], monitors: Sequence[Monitor]
) -> Tuple[int, int, int, int]:
    """
    Compute a conservative restore rectangle when no cached bounds exist.

    Prefers the provided monitor if available; otherwise falls back to the first
    monitor in the sequence. Returns (x, y, width, height).
    """
    chosen = monitor or (monitors[0] if monitors else None)
    if chosen:
        width = max(_MIN_WIDTH, min(window.width or chosen.width // 2, chosen.width))
        height = max(_MIN_HEIGHT, min(window.height or chosen.height // 2, chosen.height))
        x = chosen.x + (chosen.width - width) // 2
        y = chosen.y + (chosen.height - height) // 2
        return x, y, width, height
    return 0, 0, 1024, 768
