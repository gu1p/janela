from dataclasses import field, dataclass
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid circular import
    from interface import Janela


__all__ = ["Monitor", "Window"]


@dataclass
class Monitor:
    id: int
    name: str
    x: int
    y: int
    width: int
    height: int
    wm: "Janela" = field(repr=False)

    def list_windows(self) -> List["Window"]:
        return [w for w in self.wm.list_windows() if self.contains(w.x, w.y)]

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def is_vertical(self) -> bool:
        return self.height > self.width

    def is_horizontal(self) -> bool:
        return self.width > self.height

    def aspect_ratio(self) -> float:
        return self.width / self.height


@dataclass
class Window:
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
        return self.wm.get_monitor_for_window(self)

    def set_position(self, x: int, y: int):
        self.wm.move_window_to_position(self, x, y)

    def move(self, x: int, y: int):
        self.wm.move_window_to_position(self, self.x + x, self.y + y)

    def resize(self, width: int, height: int):
        self.wm.resize_window(self, width, height)

    def minimize(self):
        self.wm.minimize_window(self)

    def maximize(self):
        self.wm.maximize_window(self)

    def move_to_monitor(self, monitor: Monitor):
        self.wm.move_to_monitor(self, monitor)

    def is_maximized(self):
        return self.wm.is_window_maximized(self)

    def unmaximize(self):
        self.wm.unmaximize_window(self)

    def focus(self):
        self.wm.focus_window(self)

    def close(self):
        self.wm.close_window(self)
