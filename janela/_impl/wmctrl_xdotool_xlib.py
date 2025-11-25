"""Linux implementation using wmctrl/xdotool/Xlib."""
# pylint: disable=import-error,logging-fstring-interpolation,line-too-long

from typing import Dict, List, Optional, Tuple

from Xlib import display, X

from janela.interfaces.interface import Janela
from janela.interfaces.models import Monitor, Window
from janela.logger import logger
from janela.util.cmd import run_command
from janela.util.restore import fallback_restore_bounds


class LinuxImpl(Janela):  # pylint: disable=too-many-public-methods
    """Linux-specific window management implementation."""
    def __init__(self, xdotool_path: str, wmctrl_path: str):
        """
        Initialize the WindowManager implementation.

        :param xdotool_path: Path to the xdotool executable.
        :param wmctrl_path: Path to the wmctrl executable.
        """
        self.xdotool_path = xdotool_path
        self.wmctrl_path = wmctrl_path
        self._normal_bounds: Dict[str, Tuple[int, int, int, int]] = {}
        self.display = display.Display()

    def get_monitors(self) -> List[Monitor]:
        output = run_command(["xrandr", "--current"])
        monitors = []
        monitor_id = 0
        for line in output.splitlines():
            if " connected" in line:
                parts = line.split()
                name = parts[0]
                # Extract geometry information
                geometry_part = next((p for p in parts if "+" in p and "x" in p), None)
                if geometry_part:
                    width_height, x_pos, y_pos = geometry_part.split("+")
                    width, height = map(int, width_height.split("x"))
                    x, y = int(x_pos), int(y_pos)
                    monitors.append(
                        Monitor(monitor_id, name, x, y, width, height, self)
                    )
                    monitor_id += 1
                else:
                    logger.warning(f"Could not parse geometry for monitor {name}")
        return monitors

    def get_active_window_id(self) -> str:
        decimal_id = run_command([self.xdotool_path, "getactivewindow"]).strip()
        if not decimal_id.isdigit():
            logger.error(f"Invalid window ID returned by xdotool: {decimal_id}")
            return ""
        return f"0x{int(decimal_id):x}"

    def list_windows(self) -> List[Window]:  # pylint: disable=too-many-locals
        active_window_id = self.get_active_window_id()
        output = run_command([self.wmctrl_path, "-lG"])
        windows = []
        for line in output.splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8:
                logger.warning(f"Unexpected wmctrl output: {line}")
                continue
            window_id, _desktop_id, x, y, width, height, _host, name = parts
            # Skip unwanted windows
            if name.startswith("Desktop — Plasma") or name.startswith("Plasma"):
                continue

            # Standardize the window_id format
            try:
                window_id_hex = f"0x{int(window_id, 16):x}"
                x, y, width, height = map(int, [x, y, width, height])
                is_active = window_id_hex.lower() == active_window_id.lower()
                window_obj = Window(window_id_hex, name, x, y, width, height, is_active, self)
                windows.append(window_obj)
                self._record_normal_bounds(window_obj)
            except ValueError as e:
                logger.error(f"Error parsing window data: {e}")
                continue
        return windows

    def get_monitor_for_window(self, window: Window) -> Optional[Monitor]:
        monitors = self.get_monitors()
        for monitor in monitors:
            if monitor.contains(window.x, window.y):
                return monitor
        return None

    def move_window_to_position(self, window: Window, x: int, y: int):
        result = run_command(
            [self.wmctrl_path, "-ir", window.id, "-e", f"0,{x},{y},-1,-1"]
        )
        if result is not None:
            window.x, window.y = x, y
            self._record_normal_bounds(window)
        else:
            logger.error(f"Failed to move window {window.name} to position ({x}, {y})")

    def resize_window(self, window: Window, width: int, height: int):
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
            self._record_normal_bounds(window)
        else:
            logger.error(
                f"Failed to resize window {window.name} to ({width}, {height})"
            )

    def minimize_window(self, window: Window):
        # Capture last known usable bounds before minimizing.
        self._record_normal_bounds(window)
        result = run_command([self.xdotool_path, "windowminimize", window.id])
        if result is None:
            logger.error(f"Failed to minimize window {window.name}")

    def maximize_window(self, window: Window):
        self._record_normal_bounds(window)
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
        updated_window = self.get_window_by_id(window.id)

        if updated_window is None:
            logger.warning(f"Window {window.name} not found after move attempt")
            return False

        # Check if the window is on the correct monitor
        updated_monitor = self.get_monitor_for_window(updated_window)
        if updated_monitor != target_monitor:
            logger.warning(
                "Window %s is on monitor %s, expected %s",
                window.name,
                updated_monitor.id if updated_monitor else "Unknown",
                target_monitor.id,
            )
            return False

        # Check if the window position is close to the expected position
        tolerance = 10  # Reduced tolerance for better accuracy
        if (
            abs(updated_window.x - expected_x) > tolerance
            or abs(updated_window.y - expected_y) > tolerance
        ):
            logger.warning(
                "Window %s position (%s, %s) is not close to expected (%s, %s)",
                window.name,
                updated_window.x,
                updated_window.y,
                expected_x,
                expected_y,
            )
            return False

        # Update the original window object with the new position
        window.x, window.y = updated_window.x, updated_window.y
        return True

    def get_window_by_id(self, window_id: str) -> Optional[Window]:
        windows = self.list_windows()
        for window in windows:
            if window.id.lower() == window_id.lower():
                return window
        return None

    def focus_window(self, window: Window):
        result = run_command([self.wmctrl_path, "-ia", window.id])
        if result is not None:
            window.is_active = True
        else:
            logger.error(f"Failed to focus window {window.name}")

    def close_window(self, window: Window):
        result = run_command([self.wmctrl_path, "-ic", window.id])
        if result is None:
            logger.error(f"Failed to close window {window.name}")

    def list_monitors(self) -> List[Monitor]:
        return sorted(self.get_monitors(), key=lambda x: x.name)

    def get_active_window(self) -> Optional[Window]:
        active_id = self.get_active_window_id()
        return self.get_window_by_id(active_id)

    def verify_window_positions(self):
        logger.debug("Verifying window positions:")
        for monitor in self.list_monitors():
            logger.debug(f"Monitor {monitor.id} ({monitor.name}):")
            windows = [
                window
                for window in self.list_windows()
                if self.get_monitor_for_window(window) == monitor
            ]
            for window in windows:
                logger.debug(f"  - {window.name} at ({window.x}, {window.y})")

    def get_window_by_name(self, name: str) -> Optional[Window]:
        for window in self.list_windows():
            if name.lower() in window.name.lower():
                return window
        return None

    def get_monitor_by_id(self, monitor_id: int) -> Optional[Monitor]:
        for monitor in self.get_monitors():
            if monitor.id == monitor_id:
                return monitor
        return None

    def _get_wm_state_atoms(self, window: Window) -> list:
        try:
            win = self.display.create_resource_object("window", int(window.id, 16))
            wm_state = win.get_full_property(
                self.display.intern_atom("_NET_WM_STATE"), X.AnyPropertyType
            )
            return list(wm_state.value) if wm_state else []
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Error fetching _NET_WM_STATE for %s: %s", window.name, e)
        return []

    def is_window_maximized(self, window: Window) -> bool:
        atoms = self._get_wm_state_atoms(window)
        if not atoms:
            return False
        maximized_vert = self.display.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT")
        maximized_horz = self.display.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ")
        return maximized_vert in atoms and maximized_horz in atoms

    def is_window_minimized(self, window: Window) -> bool:
        atoms = self._get_wm_state_atoms(window)
        if not atoms:
            return False
        minimized_atom = self.display.intern_atom("_NET_WM_STATE_HIDDEN")
        return minimized_atom in atoms

    def unminimize_window(self, window: Window) -> None:
        result = run_command([self.xdotool_path, "windowmap", window.id])
        if result is None:
            logger.error(f"Failed to unminimize window {window.name}")
            return
        restore = self._normal_bounds.get(window.id)
        if restore:
            x, y, width, height = restore
            self._apply_geometry(window, (x, y, width, height))
        else:
            self._apply_default_restore(window)
        self._record_normal_bounds(window)

    def unmaximize_window(self, window: Window):
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
            return

        restore = self._normal_bounds.get(window.id)
        if restore:
            x, y, width, height = restore
            self._apply_geometry(window, (x, y, width, height))
        else:
            self._apply_default_restore(window)

    def _record_normal_bounds(self, window: Window) -> None:
        """Track last known non-minimized/non-maximized bounds for restoration."""
        try:
            if self.is_window_minimized(window):
                return
            if self.is_window_maximized(window):
                return
            self._normal_bounds[window.id] = (window.x, window.y, window.width, window.height)
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("Unable to record bounds for window %s: %s", window.id, exc)

    def _apply_geometry(self, window: Window, geometry: Tuple[int, int, int, int]) -> None:
        x, y, width, height = geometry
        result = run_command([self.wmctrl_path, "-ir", window.id, "-e", f"0,{x},{y},{width},{height}"])
        if result is None:
            logger.error("Failed to apply geometry to window %s", window.name)
            return
        window.x, window.y, window.width, window.height = x, y, width, height

    def _apply_default_restore(self, window: Window) -> None:
        monitor = self.get_monitor_for_window(window)
        monitors = self.get_monitors()
        x, y, width, height = fallback_restore_bounds(window, monitor, monitors)
        self._apply_geometry(window, (x, y, width, height))
