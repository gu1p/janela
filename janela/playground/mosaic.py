import math
from typing import Tuple

from janela.interfaces.interface import WindowManager
from janela.interfaces.models import Monitor
from janela.logger import logger

_FULL_HD_RESOLUTION = (1920, 1080)
_QHD_RESOLUTION = (2560, 1440)
_UHD_RESOLUTION = (3840, 2160)

__all__ = ["mosaic"]


def mosaic(window_manager: WindowManager):
    """
    Arrange windows in a mosaic pattern across all monitors.

    :param window_manager: An instance of WindowManager to manage windows.
    """
    # Get all monitors
    monitors = window_manager.list_monitors()

    for monitor in monitors:
        try:
            # Get windows for this monitor
            windows = [window for window in window_manager.list_windows() if window.monitor == monitor]
            if not windows:
                continue  # Skip monitors with no windows

            # Sort windows alphabetically, handle cases where window name might be None
            windows = sorted(windows, key=lambda w: (w.name or '').lower())

            logger.debug(f"Processing {len(windows)} windows on monitor '{monitor.name}'.")

            # If there's only one window, maximize it
            if len(windows) == 1:
                window = windows[0]
                if not window_manager.is_window_maximized(window):
                    window_manager.maximize_window(window)
                continue

            # Calculate the ideal number of rows and columns for the mosaic
            rows, columns = get_number_of_rows_columns(len(windows), monitor)

            # Calculate the ideal window size
            window_width = monitor.width // columns
            window_height = monitor.height // rows

            # Move and resize windows
            for i, window in enumerate(windows):
                try:
                    # Unmaximize the window if it's maximized
                    if window_manager.is_window_maximized(window):
                        window_manager.unmaximize_window(window)

                    # Calculate the position for this window
                    row = i // columns
                    col = i % columns
                    x = monitor.x + col * window_width
                    y = monitor.y + row * window_height

                    # Resize and position the window
                    window_manager.resize_window(window, window_width, window_height)
                    window_manager.move_window_to_position(window, x, y)
                except Exception as e:
                    logger.exception(f"Error processing window '{window.name}': {e}")
        except Exception as e:
            logger.exception(f"Error processing monitor '{monitor.name}': {e}")


def get_number_of_rows_columns(window_count: int, monitor: Monitor) -> Tuple[int, int]:
    """
    Calculate the ideal number of rows and columns for arranging windows on a monitor.

    :param window_count: The total number of windows to arrange.
    :param monitor: The monitor on which windows are to be arranged.
    :return: A tuple containing the number of rows and columns.
    """
    if window_count <= 0:
        raise ValueError("window_count must be greater than zero")

    if monitor.height == 0:
        raise ValueError("Monitor height cannot be zero")

    # Predefined layouts for specific cases
    aspect_ratio = monitor.width / monitor.height if monitor.height != 0 else 1
    if aspect_ratio == 16 / 9 and monitor.width >= _FULL_HD_RESOLUTION[0]:
        if window_count == 2:
            return 1, 2
        elif window_count == 3 and monitor.width >= _QHD_RESOLUTION[0]:
            return 1, 3

    # Calculate the aspect ratio of the monitor
    monitor_ratio = aspect_ratio

    # Determine the number of rows and columns
    columns = max(1, math.ceil(math.sqrt(window_count * monitor_ratio)))
    rows = max(1, math.ceil(window_count / columns))

    # Ensure we have enough cells for all windows
    while rows * columns < window_count:
        if monitor.width < monitor.height:
            rows += 1
        else:
            columns += 1

    return rows, columns
