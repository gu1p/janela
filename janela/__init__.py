"""Janela package entrypoint."""

import platform
import shutil

from .interfaces.interface import Janela as JanelaInterface
from .interfaces.models import Monitor, Window

__all__ = ["Janela", "Monitor", "Window", "effects"]


def Janela() -> JanelaInterface:  # pylint: disable=invalid-name
    """Factory that returns the platform-specific window manager implementation."""
    if platform.system().lower() == "windows":
        raise NotImplementedError("Windows is not supported yet")

    if platform.system().lower() == "linux":
        # pylint: disable=import-outside-toplevel
        from ._impl.wmctrl_xdotool_xlib import LinuxImpl

        xdotool_path = shutil.which("xdotool")
        wmctrl_path = shutil.which("wmctrl")

        if not xdotool_path:
            raise FileNotFoundError("xdotool not found")
        if not wmctrl_path:
            raise FileNotFoundError("wmctrl not found")

        return LinuxImpl(xdotool_path, wmctrl_path)

    if platform.system().lower() == "darwin":
        # pylint: disable=import-outside-toplevel
        from ._impl.mac import MacOSImpl

        return MacOSImpl()

    raise NotImplementedError("Unsupported platform")
