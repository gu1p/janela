import subprocess
from typing import List, Optional

from AppKit import NSScreen, NSWorkspace
from Quartz import (
    kCGWindowListOptionOnScreenOnly,
    kCGNullWindowID,
    CGWindowListCopyWindowInfo,
    kCGWindowListOptionAll,
)

from janela.interfaces import WindowManager
from janela.interfaces.models import Monitor, Window


class MacOSWindowManager(WindowManager):
    def get_monitors(self) -> List[Monitor]:
        monitors = []
        for screen in NSScreen.screens():
            frame = screen.frame()
            monitor = Monitor(
                wm=self,
                id=screen.deviceDescription()["NSScreenNumber"],
                name=screen.localizedName(),
                width=int(frame.size.width),
                height=int(frame.size.height),
                x=int(frame.origin.x),
                y=int(frame.origin.y),
            )
            monitors.append(monitor)
        return monitors

    def get_active_window_id(self) -> str:
        window_info = NSWorkspace.sharedWorkspace().frontmostApplication()
        pid = window_info.processIdentifier()
        window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
        for window in window_list:
            if window["kCGWindowOwnerPID"] == pid:
                return str(window["kCGWindowNumber"])
        return ""

    def list_windows(self) -> List[Window]:
        windows = []
        window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
        for win in window_list:
            owner_name = win.get("kCGWindowOwnerName", "")
            window_name = win.get("kCGWindowName", "")
            bounds = win.get("kCGWindowBounds", {})
            window = Window(
                wm=self,
                id=str(win["kCGWindowNumber"]),
                name=window_name or owner_name,
                x=int(bounds.get("X", 0)),
                y=int(bounds.get("Y", 0)),
                width=int(bounds.get("Width", 0)),
                height=int(bounds.get("Height", 0)),
            )
            windows.append(window)
        return windows

    def get_monitor_for_window(self, window: Window) -> Optional[Monitor]:
        monitors = self.get_monitors()
        for monitor in monitors:
            if (
                monitor.x <= window.x < monitor.x + monitor.width
                and monitor.y <= window.y < monitor.y + monitor.height
            ):
                return monitor
        return None

    def move_window_to_position(self, window: Window, x: int, y: int):
        script = f'''
        tell application "System Events"
            set frontmost of the first process whose unix id is {window.pid} to true
            tell application process "{window.name}"
                set position of windows to {{{x}, {y}}}
            end tell
        end tell
        '''
        subprocess.call(["osascript", "-e", script])

    def resize_window(self, window: Window, width: int, height: int):
        script = f'''
        tell application "System Events"
            set frontmost of the first process whose unix id is {window.pid} to true
            tell application process "{window.name}"
                set size of windows to {{{width}, {height}}}
            end tell
        end tell
        '''
        subprocess.call(["osascript", "-e", script])

    def minimize_window(self, window: Window):
        script = f'''
        tell application "System Events"
            set miniaturized of windows of (first process whose unix id is {window.pid}) to true
        end tell
        '''
        subprocess.call(["osascript", "-e", script])

    def maximize_window(self, window: Window):
        # macOS doesn't have a direct maximize command; we can simulate it by resizing
        monitor = self.get_monitor_for_window(window)
        if monitor:
            self.move_window_to_position(window, monitor.x, monitor.y)
            self.resize_window(window, monitor.width, monitor.height)

    def move_to_monitor(self, window: Window, monitor: Monitor):
        self.move_window_to_position(window, monitor.x, monitor.y)

    def verify_window_move(
        self, window: Window, target_monitor: Monitor, expected_x: int, expected_y: int
    ) -> bool:
        # Refresh window information
        updated_window = self.get_window_by_id(window.id)
        if updated_window:
            return updated_window.x == expected_x and updated_window.y == expected_y
        return False

    def get_window_by_id(self, window_id: str) -> Optional[Window]:
        window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
        for win in window_list:
            if str(win["kCGWindowNumber"]) == window_id:
                owner_name = win.get("kCGWindowOwnerName", "")
                window_name = win.get("kCGWindowName", "")
                bounds = win.get("kCGWindowBounds", {})
                window = Window(
                    id=str(win["kCGWindowNumber"]),
                    name=window_name or owner_name,
                    x=int(bounds.get("X", 0)),
                    y=int(bounds.get("Y", 0)),
                    width=int(bounds.get("Width", 0)),
                    height=int(bounds.get("Height", 0)),
                    wm=self,
                )
                return window
        return None

    def focus_window(self, window: Window):
        script = f'''
        tell application "System Events"
            set frontmost of the first process whose unix id is {window.pid} to true
        end tell
        '''
        subprocess.call(["osascript", "-e", script])

    def close_window(self, window: Window):
        script = f'''
        tell application "System Events"
            tell (first process whose unix id is {window.pid})
                tell window 1 to close
            end tell
        end tell
        '''
        subprocess.call(["osascript", "-e", script])

    def list_monitors(self) -> List[Monitor]:
        return sorted(self.get_monitors(), key=lambda m: m.name)

    def get_active_window(self) -> Optional[Window]:
        active_window_id = self.get_active_window_id()
        return self.get_window_by_id(active_window_id)

    def verify_window_positions(self) -> bool:
        # Implement a method to verify all window positions if necessary
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
        # macOS doesn't have a direct unmaximize; you may need to restore to a default size
        self.resize_window(window, 800, 600)  # Example default size
