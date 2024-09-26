import platform
import shutil

from ._impl.wmctrl_xdotool_xlib import WindowManagerImpl
from .interfaces.interface import WindowManager as WM, Monitor, Window


__all__ = ["WindowManager", "Monitor", "Window", "playground"]


def WindowManager() -> WM:
    if platform.system().lower() == "windows":
        raise NotImplementedError("Windows is not supported yet")

    if platform.system().lower() == "linux":
        xdotool_path = shutil.which("xdotool")
        wmctrl_path = shutil.which("wmctrl")

        if not xdotool_path:
            raise FileNotFoundError("xdotool not found")
        if not wmctrl_path:
            raise FileNotFoundError("wmctrl not found")

        return WindowManagerImpl(xdotool_path, wmctrl_path)

    if platform.system().lower() == "darwin":
        raise NotImplementedError("Mac is not supported yet")

    raise NotImplementedError("Unsupported platform")
