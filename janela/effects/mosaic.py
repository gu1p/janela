import math
from typing import Tuple

from janela.interfaces.interface import Janela
from janela.interfaces.models import Monitor
from janela.logger import logger

_FULL_HD_RESOLUTION = (1920, 1080)
_QHD_RESOLUTION = (2560, 1440)
_UHD_RESOLUTION = (3840, 2160)

__all__ = ["mosaic"]


def mosaic(ja: Janela):
    """
    Arrange windows in a mosaic pattern across all monitors.

    :param ja: An instance of WindowManager to manage windows.
    """
    # Get all monitors
    monitors = ja.list_monitors()

    for monitor in monitors:
        try:
            # Get windows for this monitor
            windows = [
                window
                for window in ja.list_windows()
                if window.monitor == monitor
            ]
            # Filter out windows we cannot control (e.g., AX-inaccessible apps on macOS).
            windows = [w for w in windows if ja.can_control_window(w)]
            if not windows:
                continue  # Skip monitors with no windows

            # Sort windows alphabetically, handle cases where window name might be None
            windows = sorted(windows, key=lambda w: (w.name or "").lower())

            logger.debug(
                f"Processing {len(windows)} windows on monitor '{monitor.name}'."
            )

            # If there's only one window, maximize it
            if len(windows) == 1:
                window = windows[0]
                if not ja.is_window_maximized(window):
                    ja.maximize_window(window)
                continue

            placements = []

            # Special-case vertical monitors with two windows: stack top/bottom.
            if monitor.is_vertical() and len(windows) == 2:
                heights = [
                    monitor.height // 2 + (monitor.height % 2),
                    monitor.height // 2,
                ]
                y = monitor.y
                for window, height in zip(windows, heights):
                    if ja.is_window_maximized(window):
                        ja.unmaximize_window(window)
                    ja.resize_window(window, monitor.width, height)
                    ja.move_window_to_position(window, monitor.x, y)
                    placements.append((window, monitor.x, y, monitor.width, height))
                    y += height
            else:
                # Calculate the ideal number of rows and columns for the mosaic
                rows, columns = get_number_of_rows_columns(len(windows), monitor)

                # Precompute row heights distributing any remainder pixels to the first rows
                base_height = monitor.height // rows
                extra_height = monitor.height % rows

                idx = 0
                current_y = monitor.y
                for row in range(rows):
                    remaining = len(windows) - idx
                    cols_this_row = min(columns, remaining)
                    if cols_this_row <= 0:
                        break

                    row_height = base_height + (1 if row < extra_height else 0)
                    base_width = monitor.width // cols_this_row
                    extra_width = monitor.width % cols_this_row

                    current_x = monitor.x
                    for col in range(cols_this_row):
                        window = windows[idx]
                        idx += 1
                        try:
                            if ja.is_window_maximized(window):
                                ja.unmaximize_window(window)

                            width = base_width + (1 if col < extra_width else 0)
                            height = row_height
                            x = current_x
                            y = current_y
                            current_x += width

                            logger.debug(
                                f"Resizing and moving window '{window.name}' to ({x}, {y}) with size ({width}, {height})"
                            )

                            ja.resize_window(window, width, height)
                            ja.move_window_to_position(window, x, y)
                            placements.append((window, x, y, width, height))
                        except Exception as e:
                            logger.exception(f"Error processing window '{window.name}': {e}")

                    current_y += row_height

            # Verify placements; retry once for any that failed to land correctly.
            retry_targets = []
            for window, x, y, width, height in placements:
                updated = ja.get_window_by_id(window.id)
                if updated is None:
                    retry_targets.append((window, x, y, width, height))
                    continue
                tolerance = 5
                if (
                    abs(updated.x - x) > tolerance
                    or abs(updated.y - y) > tolerance
                    or abs(updated.width - width) > tolerance
                    or abs(updated.height - height) > tolerance
                ):
                    retry_targets.append((updated, x, y, width, height))

            for window, x, y, width, height in retry_targets:
                try:
                    if ja.is_window_maximized(window):
                        ja.unmaximize_window(window)
                    ja.resize_window(window, width, height)
                    ja.move_window_to_position(window, x, y)
                except Exception as e:
                    logger.exception(f"Retry failed for window '{window.name}': {e}")

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
