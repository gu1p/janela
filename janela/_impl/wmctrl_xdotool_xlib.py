from typing import List, Optional
import platform

if platform.platform().lower().startswith('linux'):
    from Xlib import display, X

from janela.interfaces.interface import WindowManager
from janela.interfaces.models import Monitor, Window
from janela.logger import logger
from janela.util.cmd import run_command


class WindowManagerImpl(WindowManager):
    def __init__(self, xdotool_path: str, wmctrl_path: str):
        """
        Initialize the WindowManager implementation.

        :param xdotool_path: Path to the xdotool executable.
        :param wmctrl_path: Path to the wmctrl executable.
        """
        self.xdotool_path = xdotool_path
        self.wmctrl_path = wmctrl_path
        self.display = display.Display()

    def get_monitors(self) -> List[Monitor]:
        """
        Get a list of monitors available on the system.

        :return: A list of Monitor objects.
        """
        output = run_command(["xrandr", "--current"])
        monitors = []
        monitor_id = 0
        for line in output.splitlines():
            if " connected" in line:
                parts = line.split()
                name = parts[0]
                # Extract geometry information
                geometry_part = next((p for p in parts if '+' in p and 'x' in p), None)
                if geometry_part:
                    width_height, x_pos, y_pos = geometry_part.split('+')
                    width, height = map(int, width_height.split('x'))
                    x, y = int(x_pos), int(y_pos)
                    monitors.append(Monitor(monitor_id, name, x, y, width, height, self))
                    monitor_id += 1
                else:
                    logger.warning(f"Could not parse geometry for monitor {name}")
        return monitors

    def get_active_window_id(self) -> str:
        """
        Get the ID of the currently active window.

        :return: The window ID as a hexadecimal string.
        """
        decimal_id = run_command([self.xdotool_path, "getactivewindow"]).strip()
        if not decimal_id.isdigit():
            logger.error(f"Invalid window ID returned by xdotool: {decimal_id}")
            return ""
        return f"0x{int(decimal_id):x}"

    def list_windows(self) -> List[Window]:
        """
        List all windows currently open on the system.

        :return: A list of Window objects.
        """
        active_window_id = self.get_active_window_id()
        output = run_command([self.wmctrl_path, "-lG"])
        windows = []
        for line in output.splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8:
                logger.warning(f"Unexpected wmctrl output: {line}")
                continue
            window_id, desktop_id, x, y, width, height, host, name = parts
            # Skip unwanted windows
            if name.startswith("Desktop — Plasma") or name.startswith("Plasma"):
                continue

            # Standardize the window_id format
            try:
                window_id_hex = f"0x{int(window_id, 16):x}"
                x, y, width, height = map(int, [x, y, width, height])
                is_active = window_id_hex.lower() == active_window_id.lower()
                windows.append(Window(window_id_hex, name, x, y, width, height, is_active, self))
            except ValueError as e:
                logger.error(f"Error parsing window data: {e}")
                continue
        return windows

    def get_monitor_for_window(self, window: Window) -> Optional[Monitor]:
        """
        Get the monitor that the given window is on.

        :param window: The window to check.
        :return: The Monitor object or None if not found.
        """
        monitors = self.get_monitors()
        for monitor in monitors:
            if monitor.contains(window.x, window.y):
                return monitor
        return None

    def move_window_to_position(self, window: Window, x: int, y: int):
        """
        Move the window to the specified position.

        :param window: The window to move.
        :param x: The x-coordinate.
        :param y: The y-coordinate.
        """
        result = run_command([self.wmctrl_path, "-ir", window.id, "-e", f"0,{x},{y},-1,-1"])
        if result is not None:
            window.x, window.y = x, y
        else:
            logger.error(f"Failed to move window {window.name} to position ({x}, {y})")

    def resize_window(self, window: Window, width: int, height: int):
        """
        Resize the window to the specified dimensions.

        :param window: The window to resize.
        :param width: The new width.
        :param height: The new height.
        """
        # Unmaximize the window first
        self.unmaximize_window(window)

        # Use wmctrl to resize the window
        result = run_command(
            [
                self.wmctrl_path,
                "-ir",
                window.id,
                "-e",
                f"0,{window.x},{window.y},{width},{height}",
            ]
        )
        if result is not None:
            window.width, window.height = width, height
        else:
            logger.error(f"Failed to resize window {window.name} to ({width}, {height})")

    def minimize_window(self, window: Window):
        """
        Minimize the window.

        :param window: The window to minimize.
        """
        result = run_command([self.xdotool_path, "windowminimize", window.id])
        if result is None:
            logger.error(f"Failed to minimize window {window.name}")

    def maximize_window(self, window: Window):
        """
        Maximize the window.

        :param window: The window to maximize.
        """
        result = run_command(
            [
                self.wmctrl_path,
                "-ir",
                window.id,
                "-b",
                "add,maximized_vert,maximized_horz",
            ]
        )
        if result is not None:
            monitor = self.get_monitor_for_window(window)
            if monitor:
                window.x, window.y = monitor.x, monitor.y
                window.width, window.height = monitor.width, monitor.height
        else:
            logger.error(f"Failed to maximize window {window.name}")

    def move_to_monitor(self, window: Window, monitor: Monitor):
        """
        Move the window to the specified monitor.

        :param window: The window to move.
        :param monitor: The target monitor.
        """
        logger.debug(f"Attempting to move window {window.name} to monitor {monitor.id}")

        # Check if the window is maximized
        was_maximized = self.is_window_maximized(window)
        if was_maximized:
            logger.debug(f"Unmaximizing window {window.name} before moving")
            self.unmaximize_window(window)

        # Calculate the center position on the target monitor
        center_x = monitor.x + (monitor.width - window.width) // 2
        center_y = monitor.y + (monitor.height - window.height) // 2

        # Try moving the window using wmctrl
        result = run_command(
            [
                self.wmctrl_path,
                "-ir",
                window.id,
                "-e",
                f"0,{center_x},{center_y},{window.width},{window.height}",
            ]
        )
        if result is not None:
            # Verify the move
            if self.verify_window_move(window, monitor, center_x, center_y):
                logger.debug(
                    f"Successfully moved window {window.name} to ({center_x}, {center_y})"
                )
                if was_maximized:
                    logger.debug(f"Restoring maximized state for window {window.name}")
                    self.maximize_window(window)
            else:
                logger.warning(
                    f"Failed to move window {window.name} to the correct position"
                )
        else:
            logger.error(f"Error moving window {window.name}")

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
        updated_window = self.get_window_by_id(window.id)

        if updated_window is None:
            logger.warning(f"Window {window.name} not found after move attempt")
            return False

        # Check if the window is on the correct monitor
        updated_monitor = self.get_monitor_for_window(updated_window)
        if updated_monitor != target_monitor:
            logger.warning(
                f"Window {window.name} is on monitor {updated_monitor.id if updated_monitor else 'Unknown'}, expected {target_monitor.id}"
            )
            return False

        # Check if the window position is close to the expected position
        tolerance = 10  # Reduced tolerance for better accuracy
        if (
            abs(updated_window.x - expected_x) > tolerance
            or abs(updated_window.y - expected_y) > tolerance
        ):
            logger.warning(
                f"Window {window.name} position ({updated_window.x}, {updated_window.y}) is not close to expected ({expected_x}, {expected_y})"
            )
            return False

        # Update the original window object with the new position
        window.x, window.y = updated_window.x, updated_window.y
        return True

    def get_window_by_id(self, window_id: str) -> Optional[Window]:
        """
        Get a window by its ID.

        :param window_id: The window ID.
        :return: The Window object or None if not found.
        """
        windows = self.list_windows()
        for window in windows:
            if window.id.lower() == window_id.lower():
                return window
        return None

    def focus_window(self, window: Window):
        """
        Focus on the specified window.

        :param window: The window to focus.
        """
        result = run_command([self.wmctrl_path, "-ia", window.id])
        if result is not None:
            window.is_active = True
        else:
            logger.error(f"Failed to focus window {window.name}")

    def close_window(self, window: Window):
        """
        Close the specified window.

        :param window: The window to close.
        """
        result = run_command([self.wmctrl_path, "-ic", window.id])
        if result is None:
            logger.error(f"Failed to close window {window.name}")

    def list_monitors(self) -> List[Monitor]:
        """
        List all monitors sorted by name.

        :return: A sorted list of Monitor objects.
        """
        return sorted(self.get_monitors(), key=lambda x: x.name)

    def get_active_window(self) -> Optional[Window]:
        """
        Get the currently active window.

        :return: The active Window object or None if not found.
        """
        active_id = self.get_active_window_id()
        return self.get_window_by_id(active_id)

    def verify_window_positions(self):
        """
        Verify the positions of all windows on all monitors.
        """
        logger.debug("Verifying window positions:")
        for monitor in self.list_monitors():
            logger.debug(f"Monitor {monitor.id} ({monitor.name}):")
            windows = [window for window in self.list_windows() if self.get_monitor_for_window(window) == monitor]
            for window in windows:
                logger.debug(f"  - {window.name} at ({window.x}, {window.y})")

    def get_window_by_name(self, name: str) -> Optional[Window]:
        """
        Get a window by its name.

        :param name: The name to search for.
        :return: The Window object or None if not found.
        """
        for window in self.list_windows():
            if name.lower() in window.name.lower():
                return window
        return None

    def get_monitor_by_id(self, monitor_id: int) -> Optional[Monitor]:
        """
        Get a monitor by its ID.

        :param monitor_id: The monitor ID.
        :return: The Monitor object or None if not found.
        """
        for monitor in self.get_monitors():
            if monitor.id == monitor_id:
                return monitor
        return None

    def is_window_maximized(self, window: Window) -> bool:
        """
        Check if the window is maximized.

        :param window: The window to check.
        :return: True if maximized, False otherwise.
        """
        try:
            win = self.display.create_resource_object("window", int(window.id, 16))
            wm_state = win.get_full_property(
                self.display.intern_atom("_NET_WM_STATE"), X.AnyPropertyType
            )
            if wm_state:
                atoms = wm_state.value
                maximized_vert = self.display.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT")
                maximized_horz = self.display.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ")
                return maximized_vert in atoms and maximized_horz in atoms
        except Exception as e:
            logger.error(f"Error checking if window {window.name} is maximized: {e}")
        return False

    def unmaximize_window(self, window: Window):
        """
        Unmaximize the window.

        :param window: The window to unmaximize.
        """
        result = run_command(
            [
                self.wmctrl_path,
                "-ir",
                window.id,
                "-b",
                "remove,maximized_vert,maximized_horz",
            ]
        )
        if result is None:
            logger.error(f"Failed to unmaximize window {window.name}")
