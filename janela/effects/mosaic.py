"""Window tiling effects."""

# pylint: disable=too-many-branches,too-many-statements,too-many-nested-blocks,R0914,broad-except
# pylint: disable=logging-fstring-interpolation
import math
from typing import List, Optional, Tuple

from janela.interfaces.interface import Janela
from janela.interfaces.models import Monitor
from janela.logger import logger
from janela.utils.ascii_plan import DisplayPlan, TilePlan

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
    all_windows = ja.list_windows()

    for monitor in monitors:
        try:
            # Get windows for this monitor
            windows = [
                window for window in all_windows if window.monitor == monitor
            ]
            # Filter out windows we cannot control (e.g., AX-inaccessible apps on macOS).
            windows = [w for w in windows if ja.can_control_window(w)]
            if not windows:
                continue  # Skip monitors with no windows

            # Sort windows alphabetically, handle cases where window name might be None
            windows = sorted(windows, key=lambda w: (w.name or "").lower())
            start_state = {
                w.id: (w.x, w.y, w.width, w.height)
                for w in windows
            }

            logger.debug("Processing %d windows on monitor '%s'.", len(windows), monitor.name)

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
                top_y = monitor.y + monitor.height - heights[0]
                bottom_y = monitor.y

                top_window, bottom_window = windows[0], windows[1]
                targets = [
                    (top_window, monitor.x, top_y, monitor.width, heights[0]),
                    (bottom_window, monitor.x, bottom_y, monitor.width, heights[1]),
                ]
                for window, x, y, w, h in targets:
                    if ja.is_window_maximized(window):
                        ja.unmaximize_window(window)
                    ja.resize_window(window, w, h)
                    ja.move_window_to_position(window, x, y)
                    start_rect = start_state.get(
                        window.id,
                        (window.x, window.y, window.width, window.height),
                    )
                    placements.append((window, start_rect, (x, y, w, h)))
            else:
                # Calculate the ideal number of rows and columns for the mosaic
                rows, columns = get_number_of_rows_columns(len(windows), monitor)

                # Precompute row heights distributing any remainder pixels to the first rows
                base_height = monitor.height // rows
                extra_height = monitor.height % rows

                idx = 0
                top_offset = 0
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
                            y_top = top_offset
                            y = monitor.y + monitor.height - (y_top + height)
                            current_x += width

                            logger.debug(
                                "Resizing and moving window '%s' to (%d, %d) with size (%d, %d)",
                                window.name,
                                x,
                                y,
                                width,
                                height,
                            )

                            ja.resize_window(window, width, height)
                            ja.move_window_to_position(window, x, y)
                            start_rect = start_state.get(
                                window.id,
                                (window.x, window.y, window.width, window.height),
                            )
                            placements.append((window, start_rect, (x, y, width, height)))
                        except Exception as e:  # pylint: disable=broad-except
                            logger.exception("Error processing window '%s': %s", window.name, e)

                    top_offset += row_height

            # Verify placements; retry once for any that failed to land correctly.
            retry_targets = []
            for window, _, target_rect in placements:
                x, y, width, height = target_rect
                updated = ja.get_window_by_id(window.id)
                if updated is None:
                    retry_targets.append((window, target_rect))
                    continue
                tolerance = 5
                if (
                    abs(updated.x - x) > tolerance
                    or abs(updated.y - y) > tolerance
                    or abs(updated.width - width) > tolerance
                    or abs(updated.height - height) > tolerance
                ):
                    retry_targets.append((updated, target_rect))

            for window, target_rect in retry_targets:
                x, y, width, height = target_rect
                try:
                    if ja.is_window_maximized(window):
                        ja.unmaximize_window(window)
                    ja.resize_window(window, width, height)
                    ja.move_window_to_position(window, x, y)
                except Exception as e:  # pylint: disable=broad-except
                    logger.exception("Retry failed for window '%s': %s", window.name, e)

            logger.info("")
            logger.info("Display %s (%dx%d pixels):", monitor.name, monitor.width, monitor.height)
            for idx, (window, start_rect, target_rect) in enumerate(placements, start=1):
                names = getattr(window, "group_members", [window.name])
                logger.info(
                    "Tile %d: [%s] - start: (%d,%d,%d,%d) -> final: (%d,%d,%d,%d)",
                    idx,
                    ", ".join(names),
                    start_rect[0],
                    start_rect[1],
                    start_rect[2],
                    start_rect[3],
                    target_rect[0],
                    target_rect[1],
                    target_rect[2],
                    target_rect[3],
                )
            plan = _build_display_plan(monitor, placements)
            ascii_plan = plan.render_ascii() if plan else ""
            if ascii_plan:
                logger.info("ASCII plan for %s:\n%s", monitor.name, ascii_plan)
                legend = _legend_lines(placements)
                if legend:
                    logger.info("Legend:\n%s", "\n".join(legend))

        except Exception as e:  # pylint: disable=broad-except
            logger.exception("Error processing monitor '%s': %s", monitor.name, e)


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
        if window_count == 3 and monitor.width >= _QHD_RESOLUTION[0]:
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


def _build_display_plan(monitor: Monitor, placements: List[tuple]) -> "Optional[DisplayPlan]":
    if monitor.width <= 0 or monitor.height <= 0 or not placements:
        return None

    tiles: List[TilePlan] = []
    for idx, (window, _, target_rect) in enumerate(placements, start=1):
        x, y, w, h = target_rect
        rel_x = x - monitor.x
        rel_y_bottom = y - monitor.y
        top_y = monitor.height - (rel_y_bottom + h)
        label = f"{idx}"
        names = getattr(window, "group_members", [window.name])
        if names:
            label = f"{idx}:{names[0][:10]}"
        tiles.append(
            TilePlan(
                index=idx,
                x=rel_x,
                y=top_y,
                width=w,
                height=h,
                label=label,
            )
        )

    return DisplayPlan(width=monitor.width, height=monitor.height, tiles=tiles)


def _legend_lines(placements: List[tuple]) -> List[str]:
    lines: List[str] = []
    for idx, (window, _, _) in enumerate(placements, start=1):
        names = getattr(window, "group_members", [window.name])
        proc = getattr(window, "process_name", None)
        proc_text = f" ({proc})" if proc else ""
        lines.append(f"Tile {idx}: [{', '.join(names)}]{proc_text}")
    return lines
