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

    def overlap_area(self, window: "Window") -> int:
        """Area of overlap between this monitor and a window."""
        wx1, wy1 = window.x, window.y
        wx2 = wx1 + max(window.width, 1)
        wy2 = wy1 + max(window.height, 1)
        mx1, my1 = self.x, self.y
        mx2, my2 = self.x + self.width, self.y + self.height
        inter_w = max(0, min(wx2, mx2) - max(wx1, mx1))
        inter_h = max(0, min(wy2, my2) - max(wy1, my1))
        return inter_w * inter_h

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

    def resize_left(self, delta: int):
        """Increase width toward the left, staying within the current monitor."""
        if delta <= 0:
            return
        monitor = self.monitor
        allowed = delta if monitor is None else min(delta, max(0, self.x - monitor.x))
        if allowed <= 0:
            return
        new_x = self.x - allowed
        new_width = self.width + allowed
        self.set_position(new_x, self.y)
        self.resize(new_width, self.height)

    def resize_right(self, delta: int):
        """Increase width toward the right, staying within the current monitor."""
        if delta <= 0:
            return
        monitor = self.monitor
        if monitor is None:
            allowed = delta
        else:
            right_edge = self.x + self.width
            available = monitor.x + monitor.width - right_edge
            allowed = min(delta, max(0, available))
        if allowed <= 0:
            return
        self.resize(self.width + allowed, self.height)

    def resize_top(self, delta: int):
        """Increase height upward, staying within the current monitor."""
        if delta <= 0:
            return
        monitor = self.monitor
        allowed = delta if monitor is None else min(delta, max(0, self.y - monitor.y))
        if allowed <= 0:
            return
        new_y = self.y - allowed
        new_height = self.height + allowed
        self.set_position(self.x, new_y)
        self.resize(self.width, new_height)

    def resize_down(self, delta: int):
        """Increase height downward, staying within the current monitor."""
        if delta <= 0:
            return
        monitor = self.monitor
        if monitor is None:
            allowed = delta
        else:
            bottom_edge = self.y + self.height
            available = monitor.y + monitor.height - bottom_edge
            allowed = min(delta, max(0, available))
        if allowed <= 0:
            return
        self.resize(self.width, self.height + allowed)

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

    def is_minimized(self) -> bool:
        """Check minimized state."""
        return self.wm.is_window_minimized(self)

    def unminimize(self) -> None:
        """Restore a minimized window."""
        self.wm.unminimize_window(self)

    def focus(self):
        """Focus window."""
        self.wm.focus_window(self)

    def close(self):
        """Close window."""
        self.wm.close_window(self)
