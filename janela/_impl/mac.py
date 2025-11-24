import os
import subprocess
from typing import Dict, List, Optional, Tuple

from AppKit import (
    NSScreen,
    NSWorkspace,
    NSRunningApplication,
    NSApplicationActivateIgnoringOtherApps,
)
import ApplicationServices as AS
from Quartz import CoreGraphics as CG

from janela.interfaces import Janela
from janela.interfaces.models import Monitor, Window
from janela.logger import logger


# Map Quartz symbols through submodules explicitly; fall back to string names if missing.
AXIsProcessTrustedWithOptions = AS.AXIsProcessTrustedWithOptions
kAXTrustedCheckOptionPrompt = getattr(AS, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt")
AXUIElementCopyAttributeValue = AS.AXUIElementCopyAttributeValue
AXUIElementCreateApplication = AS.AXUIElementCreateApplication
AXUIElementPerformAction = AS.AXUIElementPerformAction
AXUIElementSetAttributeValue = AS.AXUIElementSetAttributeValue
AXValueCreate = AS.AXValueCreate
kAXErrorSuccess = getattr(AS, "kAXErrorSuccess", 0)
kAXMinimizedAttribute = getattr(AS, "kAXMinimizedAttribute", "AXMinimized")
kAXPositionAttribute = getattr(AS, "kAXPositionAttribute", "AXPosition")
kAXSizeAttribute = getattr(AS, "kAXSizeAttribute", "AXSize")
kAXTitleAttribute = getattr(AS, "kAXTitleAttribute", "AXTitle")
kAXValueCGPointType = getattr(AS, "kAXValueCGPointType", None)
kAXValueCGSizeType = getattr(AS, "kAXValueCGSizeType", None)
kAXWindowsAttribute = getattr(AS, "kAXWindowsAttribute", "AXWindows")
CGWindowListCopyWindowInfo = CG.CGWindowListCopyWindowInfo
kCGNullWindowID = CG.kCGNullWindowID
kCGWindowListOptionAll = CG.kCGWindowListOptionAll
kCGWindowListOptionOnScreenOnly = CG.kCGWindowListOptionOnScreenOnly


class MacOSImpl(Janela):
    def __init__(self) -> None:
        self._ensure_accessibility_permissions()
        self._screen_recording_checked = False
        self._ensure_screen_recording_permissions()
        main_screen = NSScreen.mainScreen()
        self._main_screen_height = (
            int(main_screen.frame().size.height) if main_screen else 0
        )
        self._restore_bounds: Dict[str, Tuple[int, int, int, int]] = {}
        self._warned_screen_recording = False
        self._ax_missing_window_ids: set[str] = set()

    def _ensure_accessibility_permissions(self) -> None:
        # Prompt the user automatically if access has not been granted.
        options = {kAXTrustedCheckOptionPrompt: True} if kAXTrustedCheckOptionPrompt else None
        trusted = AXIsProcessTrustedWithOptions(options) if options is not None else getattr(AS, "AXIsProcessTrusted", lambda: False)()
        if not trusted:
            raise PermissionError(
                "Janela requires Accessibility access. Grant permission in "
                "System Settings → Privacy & Security → Accessibility and re-run."
            )

    def _ensure_screen_recording_permissions(self) -> None:
        if self._screen_recording_checked:
            return
        self._screen_recording_checked = True

        preflight = getattr(CG, "CGPreflightScreenCaptureAccess", None)
        request = getattr(CG, "CGRequestScreenCaptureAccess", None)
        try:
            if preflight and preflight():
                return
        except Exception:
            pass

        if request:
            try:
                granted = request()
                if granted:
                    return
            except Exception:
                pass

        # Fallback: open the Screen Recording preference pane for the user.
        self._open_screen_recording_settings()

    def _open_screen_recording_settings(self) -> None:
        try:
            subprocess.run(
                [
                    "open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenRecording",
                ],
                check=False,
            )
        except Exception:
            logger.warning(
                "Please enable Screen Recording for your terminal/Python in "
                "System Settings → Privacy & Security → Screen Recording."
            )

    def _to_cg_y(self, screen_y: int, screen_height: int) -> int:
        # Convert NSScreen (origin bottom-left) to CG window coordinates (origin top-left of main display)
        return self._main_screen_height - (screen_y + screen_height)

    def get_monitors(self) -> List[Monitor]:
        monitors: List[Monitor] = []
        for screen in NSScreen.screens():
            frame = screen.frame()
            monitor = Monitor(
                wm=self,
                id=screen.deviceDescription()["NSScreenNumber"],
                name=screen.localizedName(),
                width=int(frame.size.width),
                height=int(frame.size.height),
                x=int(frame.origin.x),
                y=self._to_cg_y(int(frame.origin.y), int(frame.size.height)),
            )
            monitors.append(monitor)
        return monitors

    def get_active_window_id(self) -> str:
        window_info = NSWorkspace.sharedWorkspace().frontmostApplication()
        pid = window_info.processIdentifier()
        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        )
        for window in window_list:
            if window.get("kCGWindowOwnerPID") == pid:
                return str(window["kCGWindowNumber"])
        return ""

    def _copy_attribute(self, element, attribute):
        try:
            result = AXUIElementCopyAttributeValue(element, attribute, None)
            if isinstance(result, tuple):
                return result
            # Some PyObjC versions return value directly
            return kAXErrorSuccess, result
        except TypeError:
            # Older signature without third argument
            result = AXUIElementCopyAttributeValue(element, attribute)
            if isinstance(result, tuple):
                return result
            return kAXErrorSuccess, result

    def _find_ax_window(self, window: Window, log_missing: bool = True):
        if window.pid is None:
            return None

        app_ref = AXUIElementCreateApplication(window.pid)
        err, ax_windows = self._copy_attribute(app_ref, kAXWindowsAttribute)
        if err != kAXErrorSuccess or not ax_windows:
            if log_missing and window.id not in self._ax_missing_window_ids:
                logger.warning(f"Unable to fetch AX windows for pid {window.pid}")
                self._ax_missing_window_ids.add(window.id)
            return None

        target_id = int(window.id)
        for ax_win in ax_windows:
            err, win_id = self._copy_attribute(ax_win, "AXWindowNumber")
            if err == kAXErrorSuccess:
                try:
                    if int(win_id) == target_id:
                        return ax_win
                except Exception:
                    pass

        # Fallback by title match if window number lookup fails
        for ax_win in ax_windows:
            err, title = self._copy_attribute(ax_win, kAXTitleAttribute)
            if err == kAXErrorSuccess and isinstance(title, str):
                title_lower = title.lower()
                target_lower = (window.name or "").lower()
                if (
                    title_lower == target_lower
                    or target_lower in title_lower
                    or title_lower in target_lower
                ):
                    return ax_win
        # Fallback: use the first AX window if nothing matched.
        if ax_windows:
            return ax_windows[0]

        return None

    def _set_window_position(self, ax_window, x: int, y: int) -> bool:
        if not kAXValueCGPointType:
            logger.error("Accessibility CGPoint type missing; cannot move window")
            return False
        pos_value = AXValueCreate(kAXValueCGPointType, (x, y))
        err = AXUIElementSetAttributeValue(ax_window, kAXPositionAttribute, pos_value)
        return err == kAXErrorSuccess

    def _set_window_size(self, ax_window, width: int, height: int) -> bool:
        if not kAXValueCGSizeType:
            logger.error("Accessibility CGSize type missing; cannot resize window")
            return False
        size_value = AXValueCreate(kAXValueCGSizeType, (width, height))
        err = AXUIElementSetAttributeValue(ax_window, kAXSizeAttribute, size_value)
        return err == kAXErrorSuccess

    def list_windows(self) -> List[Window]:
        windows: List[Window] = []
        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionAll, kCGNullWindowID
        )
        if not window_list and not self._warned_screen_recording:
            logger.warning(
                "No windows found. macOS may require Screen Recording permission "
                "for this terminal/app to enumerate other apps' windows. "
                "Enable it in System Settings → Privacy & Security → Screen Recording. Opening settings…"
            )
            self._open_screen_recording_settings()
            self._warned_screen_recording = True

        active_id = self.get_active_window_id()
        for win in window_list:
            if win.get("kCGWindowLayer", 0) != 0:
                continue

            owner_name = win.get("kCGWindowOwnerName", "")
            window_name = win.get("kCGWindowName", "")
            if not owner_name or not window_name:
                continue

            bounds = win.get("kCGWindowBounds", {})
            window_id = str(win["kCGWindowNumber"])
            windows.append(
                Window(
                    wm=self,
                    id=window_id,
                    name=window_name or owner_name,
                    x=int(bounds.get("X", 0)),
                    y=int(bounds.get("Y", 0)),
                    width=int(bounds.get("Width", 0)),
                    height=int(bounds.get("Height", 0)),
                    is_active=window_id == active_id,
                    pid=win.get("kCGWindowOwnerPID"),
                )
            )

        if windows and all(w.pid == os.getpid() for w in windows) and not self._warned_screen_recording:
            logger.warning(
                "Only this Python process is visible. macOS often restricts window "
                "enumeration without Screen Recording permission. "
                "Enable it in System Settings → Privacy & Security → Screen Recording. Opening settings…"
            )
            self._open_screen_recording_settings()
            self._warned_screen_recording = True
        return windows

    def get_monitor_for_window(self, window: Window) -> Optional[Monitor]:
        monitors = self.get_monitors()
        for monitor in monitors:
            if monitor.x <= window.x < monitor.x + monitor.width and monitor.y <= window.y < monitor.y + monitor.height:
                return monitor
        return None

    def move_window_to_position(self, window: Window, x: int, y: int):
        ax_window = self._find_ax_window(window)
        if not ax_window:
            logger.error(f"Could not find AX window for '{window.name}'")
            return
        if self._set_window_position(ax_window, x, y):
            window.x, window.y = x, y
        else:
            logger.error(f"Failed to move window '{window.name}' to ({x}, {y})")

    def resize_window(self, window: Window, width: int, height: int):
        ax_window = self._find_ax_window(window)
        if not ax_window:
            logger.error(f"Could not find AX window for '{window.name}'")
            return
        if self._set_window_size(ax_window, width, height):
            window.width, window.height = width, height
        else:
            logger.error(f"Failed to resize window '{window.name}' to ({width}, {height})")

    def minimize_window(self, window: Window):
        ax_window = self._find_ax_window(window)
        if not ax_window:
            logger.error(f"Could not find AX window for '{window.name}'")
            return
        err = AXUIElementSetAttributeValue(ax_window, kAXMinimizedAttribute, True)
        if err != kAXErrorSuccess:
            logger.error(f"Failed to minimize window '{window.name}'")

    def maximize_window(self, window: Window):
        monitor = self.get_monitor_for_window(window)
        if monitor:
            # Track prior bounds so we can restore later.
            self._restore_bounds[window.id] = (
                window.x,
                window.y,
                window.width,
                window.height,
            )
            self.move_window_to_position(window, monitor.x, monitor.y)
            self.resize_window(window, monitor.width, monitor.height)

    def move_to_monitor(self, window: Window, monitor: Monitor):
        target_x = monitor.x + (monitor.width - window.width) // 2
        target_y = monitor.y + (monitor.height - window.height) // 2
        self.move_window_to_position(window, target_x, target_y)

    def verify_window_move(
        self, window: Window, target_monitor: Monitor, expected_x: int, expected_y: int
    ) -> bool:
        updated_window = self.get_window_by_id(window.id)
        if updated_window is None:
            return False
        return (
            updated_window.x == expected_x
            and updated_window.y == expected_y
            and self.get_monitor_for_window(updated_window) == target_monitor
        )

    def get_window_by_id(self, window_id: str) -> Optional[Window]:
        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionAll, kCGNullWindowID
        )
        active_id = self.get_active_window_id()
        for win in window_list:
            if str(win.get("kCGWindowNumber")) != window_id:
                continue
            owner_name = win.get("kCGWindowOwnerName", "")
            window_name = win.get("kCGWindowName", "")
            bounds = win.get("kCGWindowBounds", {})
            return Window(
                id=str(win["kCGWindowNumber"]),
                name=window_name or owner_name,
                x=int(bounds.get("X", 0)),
                y=int(bounds.get("Y", 0)),
                width=int(bounds.get("Width", 0)),
                height=int(bounds.get("Height", 0)),
                wm=self,
                is_active=window_id == active_id,
                pid=win.get("kCGWindowOwnerPID"),
            )
        return None

    def focus_window(self, window: Window):
        if window.pid is None:
            logger.error(f"Cannot focus window '{window.name}' without PID")
            return
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(window.pid)
        if app:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            window.is_active = True
        else:
            logger.error(f"Failed to focus window '{window.name}' (pid {window.pid})")

    def close_window(self, window: Window):
        ax_window = self._find_ax_window(window)
        if not ax_window:
            logger.error(f"Could not find AX window for '{window.name}'")
            return
        err, close_button = self._copy_attribute(ax_window, "AXCloseButton")
        if err == kAXErrorSuccess and close_button:
            press_err = AXUIElementPerformAction(close_button, "AXPress")
            if press_err != kAXErrorSuccess:
                logger.error(f"Failed to close window '{window.name}' via close button")
        else:
            logger.error(f"No close button available for '{window.name}'")

    def list_monitors(self) -> List[Monitor]:
        return sorted(self.get_monitors(), key=lambda m: m.name)

    def get_active_window(self) -> Optional[Window]:
        active_window_id = self.get_active_window_id()
        return self.get_window_by_id(active_window_id)

    def verify_window_positions(self) -> bool:
        return True

    def get_window_by_name(self, name: str) -> Optional[Window]:
        window_list = self.list_windows()
        for window in window_list:
            if window.name == name:
                return window
        return None

    def get_monitor_by_id(self, monitor_id: int) -> Optional[Monitor]:
        monitors = self.get_monitors()
        for monitor in monitors:
            if monitor.id == monitor_id:
                return monitor
        return None

    def is_window_maximized(self, window: Window) -> bool:
        monitor = self.get_monitor_for_window(window)
        if monitor:
            return (
                window.x == monitor.x
                and window.y == monitor.y
                and window.width == monitor.width
                and window.height == monitor.height
            )
        return False

    def unmaximize_window(self, window: Window) -> None:
        restore = self._restore_bounds.get(window.id)
        if restore:
            x, y, width, height = restore
            self.move_window_to_position(window, x, y)
            self.resize_window(window, width, height)
            return
        # Fallback size if we do not know the original bounds
        self.resize_window(window, max(800, window.width // 2), max(600, window.height // 2))

    def can_control_window(self, window: Window) -> bool:
        return self._find_ax_window(window, log_missing=False) is not None
