from abc import ABC, abstractmethod
from typing import List, Optional

from janela.interfaces.models import Monitor, Window

__all__ = ["WindowManager"]

class WindowManager(ABC):
    @abstractmethod
    def get_monitors(self) -> List[Monitor]: pass

    @abstractmethod
    def get_active_window_id(self) -> str: pass

    @abstractmethod
    def list_windows(self) -> List[Window]: pass

    @abstractmethod
    def get_monitor_for_window(self, window: Window) -> Optional[Monitor]: pass

    @abstractmethod
    def move_window_to_position(self, window: Window, x: int, y: int): pass

    @abstractmethod
    def resize_window(self, window: Window, width: int, height: int): pass

    @abstractmethod
    def minimize_window(self, window: Window): pass

    @abstractmethod
    def maximize_window(self, window: Window): pass

    @abstractmethod
    def move_to_monitor(self, window: Window, monitor: Monitor): pass

    @abstractmethod
    def verify_window_move(
        self, window: Window, target_monitor: Monitor, expected_x: int, expected_y: int
    ) -> bool:  pass

    @abstractmethod
    def get_window_by_id(self, window_id: str) -> Optional[Window]: pass

    @abstractmethod
    def focus_window(self, window: Window): pass

    @abstractmethod
    def close_window(self, window: Window): pass

    @abstractmethod
    def list_monitors(self) -> List[Monitor]:   pass

    @abstractmethod
    def get_active_window(self) -> Optional[Window]:    pass

    @abstractmethod
    def verify_window_positions(self) -> bool:  pass

    @abstractmethod
    def get_window_by_name(self, name: str) -> Optional[Window]:    pass

    @abstractmethod
    def get_monitor_by_id(self, monitor_id: int) -> Optional[Monitor]:  pass

    @abstractmethod
    def is_window_maximized(self, window: Window) -> bool:  pass

    @abstractmethod
    def unmaximize_window(self, window: Window) -> None:    pass
