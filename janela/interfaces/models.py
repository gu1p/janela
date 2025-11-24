"""Data models for monitors and windows."""

from dataclasses import field, dataclass
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid circular import
    from interface import Janela


__all__ = ["Monitor", "Window"]


@dataclass
class Monitor:
    """Monitor representation."""

    id: int
    name: str
    x: int
    y: int
    width: int
    height: int
    wm: "Janela" = field(repr=False)

    def list_windows(self) -> List["Window"]:
        """Return windows that belong to this monitor."""
        return [w for w in self.wm.list_windows() if self.contains(w.x, w.y)]

    def contains(self, x: int, y: int) -> bool:
        """Whether a point lies within the monitor bounds."""
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def is_vertical(self) -> bool:
        """True if height exceeds width."""
        return self.height > self.width

    def is_horizontal(self) -> bool:
        """True if width exceeds height."""
        return self.width > self.height

    def aspect_ratio(self) -> float:
        """Aspect ratio as width / height."""
        return self.width / self.height


@dataclass
class Window:  # pylint: disable=too-many-instance-attributes
    """Window representation."""

    id: str
    name: str
    x: int
    y: int
    width: int
    height: int
    is_active: bool
    wm: "Janela" = field(repr=False)
    pid: Optional[int] = field(default=None)

    @property
    def monitor(self) -> Optional[Monitor]:
        """Monitor the window resides on."""
        return self.wm.get_monitor_for_window(self)

    def set_position(self, x: int, y: int):
        """Set absolute position."""
        self.wm.move_window_to_position(self, x, y)

    def move(self, x: int, y: int):
        """Move relative to current position."""
        self.wm.move_window_to_position(self, self.x + x, self.y + y)

    def resize(self, width: int, height: int):
        """Resize window."""
        self.wm.resize_window(self, width, height)

    def minimize(self):
        """Minimize window."""
        self.wm.minimize_window(self)

    def maximize(self):
        """Maximize window."""
        self.wm.maximize_window(self)

    def move_to_monitor(self, monitor: Monitor):
        """Move to target monitor."""
        self.wm.move_to_monitor(self, monitor)

    def is_maximized(self):
        """Check maximized state."""
        return self.wm.is_window_maximized(self)

    def unmaximize(self):
        """Restore from maximized state."""
        self.wm.unmaximize_window(self)

    def focus(self):
        """Focus window."""
        self.wm.focus_window(self)

    def close(self):
        """Close window."""
        self.wm.close_window(self)
