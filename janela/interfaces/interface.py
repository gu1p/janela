"""Interfaces for Janela implementations."""

from abc import ABC, abstractmethod
from typing import List, Optional

from janela.interfaces.models import Monitor, Window

__all__ = ["Janela"]


class Janela(ABC):  # pylint: disable=too-many-public-methods
    """Abstract base class for platform-specific window managers."""
    @abstractmethod
    def get_monitors(self) -> List[Monitor]:
        """
        Get a list of monitors available on the system.

        :return: A list of Monitor objects.
        """
        raise NotImplementedError

    @abstractmethod
    def get_active_window_id(self) -> str:
        """
        Get the ID of the currently active window.

        :return: The window ID as a hexadecimal string.
        """
        raise NotImplementedError

    @abstractmethod
    def list_windows(self) -> List[Window]:
        """
        List all windows currently open on the system.

        :return: A list of Window objects.
        """
        raise NotImplementedError

    @abstractmethod
    def get_monitor_for_window(self, window: Window) -> Optional[Monitor]:
        """
        Get the monitor that the given window is on.

          :param window: The window to check.
          :return: The Monitor object or None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def move_window_to_position(self, window: Window, x: int, y: int):
        """
        Move the window to the specified position.

        :param window: The window to move.
        :param x: The x-coordinate.
        :param y: The y-coordinate.
        """
        raise NotImplementedError

    @abstractmethod
    def resize_window(self, window: Window, width: int, height: int):
        """
        Resize the window to the specified dimensions.

        :param window: The window to resize.
        :param width: The new width.
        :param height: The new height.
        """
        raise NotImplementedError

    @abstractmethod
    def minimize_window(self, window: Window):
        """
        Minimize the window.

        :param window: The window to minimize.
        """
        raise NotImplementedError

    @abstractmethod
    def maximize_window(self, window: Window):
        """
        Maximize the window.

        :param window: The window to maximize.
        """
        raise NotImplementedError

    @abstractmethod
    def move_to_monitor(self, window: Window, monitor: Monitor):
        """
        Move the window to the specified monitor.

        :param window: The window to move.
        :param monitor: The target monitor.
        """
        raise NotImplementedError

    @abstractmethod
    def verify_window_move(
        self, window: Window, target_monitor: Monitor, expected_x: int, expected_y: int
    ) -> bool:
        """
        Verify that the window has moved to the expected position.

        :param window: The window to check.
        :param target_monitor: The target monitor.
        :param expected_x: The expected x-coordinate.
        :param expected_y: The expected y-coordinate.
        :return: True if the window is correctly positioned, False otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def get_window_by_id(self, window_id: str) -> Optional[Window]:
        """
        Get a window by its ID.

        :param window_id: The window ID.
        :return: The Window object or None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def focus_window(self, window: Window):
        """
        Focus on the specified window.

        :param window: The window to focus.
        """
        raise NotImplementedError

    @abstractmethod
    def close_window(self, window: Window):
        """
        Close the specified window.

        :param window: The window to close.
        """
        raise NotImplementedError

    @abstractmethod
    def list_monitors(self) -> List[Monitor]:
        """
        List all monitors sorted by name.

        :return: A sorted list of Monitor objects.
        """
        raise NotImplementedError

    @abstractmethod
    def get_active_window(self) -> Optional[Window]:
        """
        Get the currently active window.

        :return: The active Window object or None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def verify_window_positions(self) -> bool:
        """
        Verify the positions of all windows on all monitors.
        """
        raise NotImplementedError

    @abstractmethod
    def get_window_by_name(self, name: str) -> Optional[Window]:
        """
        Get a window by its name.

        :param name: The name to search for.
        :return: The Window object or None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def get_monitor_by_id(self, monitor_id: int) -> Optional[Monitor]:
        """
        Get a monitor by its ID.

        :param monitor_id: The monitor ID.
        :return: The Monitor object or None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def is_window_maximized(self, window: Window) -> bool:
        """
        Check if the window is maximized.

        :param window: The window to check.
        :return: True if maximized, False otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def unmaximize_window(self, window: Window) -> None:
        """
        Unmaximize the window.

        :param window: The window to unmaximize.
        """
        raise NotImplementedError

    def can_control_window(self, window: Window) -> bool:
        """
        Check if this backend can control the given window (move/resize).

        By default returns True; platform-specific implementations may override.
        """
        del window
        return True
