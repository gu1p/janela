"""Window tiling effects."""

# pylint: disable=too-many-branches,too-many-statements,too-many-nested-blocks,R0914,broad-except
# pylint: disable=logging-fstring-interpolation
import math
from typing import Dict, List, Optional, Tuple

from janela.interfaces.interface import Janela
from janela.interfaces.models import Monitor
from janela.logger import logger
from janela.utils.ascii_plan import DisplayPlan, TilePlan

_FULL_HD_RESOLUTION = (1920, 1080)
_QHD_RESOLUTION = (2560, 1440)
_UHD_RESOLUTION = (3840, 2160)

__all__ = ["mosaic", "mosaic_around_window"]


class _LayoutArea:
    """Simple rectangle used for tiling computations."""

    __slots__ = ("name", "x", "y", "width", "height")

    def __init__(self, name: str, bounds: Tuple[int, int, int, int]):
        self.name = name
        self.x, self.y, self.width, self.height = bounds

    def is_vertical(self) -> bool:
        """True when height exceeds width."""
        return self.height > self.width

    def area(self) -> int:
        """Computed area for allocation decisions."""
        return self.width * self.height


def mosaic(ja: Janela, unminimize_minimized: bool = True):
    """
    Arrange windows in a mosaic pattern across all monitors.

    :param ja: An instance of WindowManager to manage windows.
    :param unminimize_minimized: When True (default), restore minimized windows before tiling.
    """
    monitors = ja.list_monitors()
    all_windows = ja.list_windows()
    windows_by_monitor = _group_windows_by_monitor(monitors, all_windows)

    for monitor in monitors:
        try:
            windows = windows_by_monitor.get(monitor.id, [])
            # Filter out windows we cannot control (e.g., AX-inaccessible apps on macOS).
            windows = [w for w in windows if ja.can_control_window(w)]
            if unminimize_minimized:
                for window in list(windows):
                    try:
                        if ja.is_window_minimized(window):
                            ja.unminimize_window(window)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.debug(
                            "Could not unminimize '%s': %s",
                            getattr(window, "name", window.id),
                            exc,
                        )
                # Refresh window list to capture updated bounds after unminimize.
                all_windows = ja.list_windows()
                windows_by_monitor = _group_windows_by_monitor(monitors, all_windows)
                windows = [
                    w for w in windows_by_monitor.get(monitor.id, []) if ja.can_control_window(w)
                ]
            else:
                # Drop minimized windows to avoid allocating tiles for invisible windows.
                windows = [w for w in windows if not ja.is_window_minimized(w)]

            if not windows:
                continue  # Skip monitors with no windows

            # Sort windows alphabetically, handle cases where window name might be None
            windows = sorted(windows, key=lambda w: (w.name or "").lower())
            start_state = {
                w.id: (w.x, w.y, w.width, w.height)
                for w in windows
            }

            logger.debug("Processing %d windows on monitor '%s'.", len(windows), monitor.name)

            placements = []
            # If there's only one window, place it full screen but still log the layout.
            if len(windows) == 1:
                window = windows[0]
                try:
                    if ja.is_window_maximized(window):
                        ja.unmaximize_window(window)
                    ja.resize_window(window, monitor.width, monitor.height)
                    ja.move_window_to_position(window, monitor.x, monitor.y)
                    start_rect = start_state.get(
                        window.id,
                        (window.x, window.y, window.width, window.height),
                    )
                    placements.append(
                        (window, start_rect, (monitor.x, monitor.y, monitor.width, monitor.height))
                    )
                except Exception as e:  # pylint: disable=broad-except
                    logger.exception("Error processing window '%s': %s", window.name, e)
            else:
                # Special-case vertical monitors with two windows: stack top/bottom.
                if monitor.is_vertical() and len(windows) == 2:
                    heights = [
                        monitor.height // 2 + (monitor.height % 2),
                        monitor.height // 2,
                    ]
                    top_y = monitor.y
                    bottom_y = monitor.y + heights[0]

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
                                y = monitor.y + top_offset
                                current_x += width

                                logger.debug(
                                    "Resize/move '%s' to (%d, %d) size (%d, %d)",
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


def mosaic_around_window(ja: Janela, unminimize_minimized: bool = True):
    """
    Arrange windows in a mosaic pattern around the active window.

    The monitor containing the active window is tiled using the remaining
    space around the active window. Other monitors use the normal mosaic
    layout.

    :param ja: An instance of WindowManager to manage windows.
    :param unminimize_minimized: When True (default), restore minimized windows before tiling.
    """
    active_window = ja.get_active_window()
    if active_window is None:
        logger.info("No active window detected; falling back to standard mosaic.")
        mosaic(ja, unminimize_minimized)
        return

    monitors = ja.list_monitors()
    active_monitor = active_window.monitor or _best_monitor_for_window(active_window, monitors)
    if active_monitor is None:
        logger.info("Active window not assigned to a monitor; using standard mosaic.")
        mosaic(ja, unminimize_minimized)
        return

    all_windows = ja.list_windows()
    windows_by_monitor = _group_windows_by_monitor(monitors, all_windows)

    for monitor in monitors:
        try:
            windows = windows_by_monitor.get(monitor.id, [])
            windows = [w for w in windows if ja.can_control_window(w)]

            if unminimize_minimized:
                for window in list(windows):
                    try:
                        if ja.is_window_minimized(window):
                            ja.unminimize_window(window)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.debug(
                            "Could not unminimize '%s': %s",
                            getattr(window, "name", window.id),
                            exc,
                        )
                all_windows = ja.list_windows()
                windows_by_monitor = _group_windows_by_monitor(monitors, all_windows)
                windows = [
                    w for w in windows_by_monitor.get(monitor.id, []) if ja.can_control_window(w)
                ]
            else:
                windows = [w for w in windows if not ja.is_window_minimized(w)]

            if not windows:
                continue

            windows = sorted(windows, key=lambda w: (w.name or "").lower())
            start_state = {w.id: (w.x, w.y, w.width, w.height) for w in windows}

            # Monitors without the active window use normal mosaic.
            if monitor != active_monitor:
                placements = _standard_layout_for_monitor(
                    ja,
                    monitor,
                    windows,
                    start_state,
                )
                _finalize_monitor_layout(ja, monitor, placements)
                continue

            # Monitor containing the active window.
            active = next((w for w in windows if w.id == active_window.id), None)
            if active is None:
                logger.debug(
                    "Active window not controllable on monitor '%s'; tiling normally.",
                    monitor.name,
                )
                placements = _standard_layout_for_monitor(
                    ja,
                    monitor,
                    windows,
                    start_state,
                )
                _finalize_monitor_layout(ja, monitor, placements)
                continue

            other_windows = [w for w in windows if w.id != active.id]
            if not other_windows:
                logger.debug(
                    "Only active window on monitor '%s'; leaving as is.",
                    monitor.name,
                )
                continue

            placements = [
                (
                    active,
                    start_state.get(
                        active.id,
                        (active.x, active.y, active.width, active.height),
                    ),
                    (active.x, active.y, active.width, active.height),
                )
            ]

            areas = _areas_around_active(monitor, active)
            if not areas:
                logger.debug(
                    "No tilable area around active window on monitor '%s'; tiling normally.",
                    monitor.name,
                )
                placements.extend(
                    _standard_layout_for_monitor(
                        ja,
                        monitor,
                        other_windows,
                        start_state,
                    )
                )
                _finalize_monitor_layout(ja, monitor, placements)
                continue

            allocations = _assign_windows_to_areas(other_windows, areas)
            for area in areas:
                group = allocations.get(area, [])
                if not group:
                    continue
                placements.extend(
                    _layout_windows_in_area(
                        ja,
                        area,
                        group,
                        start_state,
                    )
                )

            _finalize_monitor_layout(ja, monitor, placements)
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


def _best_monitor_for_window(window, monitors: List[Monitor]) -> Optional[Monitor]:
    """
    Pick the monitor that overlaps the largest area of the window.

    Falls back to nearest monitor center when areas tie or are zero.
    """
    if not monitors:
        return None

    wx1, wy1 = window.x, window.y
    wx2 = wx1 + max(window.width, 1)
    wy2 = wy1 + max(window.height, 1)
    cx = (wx1 + wx2) / 2
    cy = (wy1 + wy2) / 2

    best_monitor: Optional[Monitor] = None
    best_area = -1
    best_distance = None

    for monitor in monitors:
        area = monitor.overlap_area(window)
        m_cx = monitor.x + monitor.width / 2
        m_cy = monitor.y + monitor.height / 2
        dist = (cx - m_cx) ** 2 + (cy - m_cy) ** 2
        if area > best_area or (
            area == best_area and (best_distance is None or dist < best_distance)
        ):
            best_area = area
            best_distance = dist
            best_monitor = monitor

    return best_monitor


def _group_windows_by_monitor(monitors: List[Monitor], windows: List) -> Dict[int, List]:
    """
    Robustly bucket windows by monitor, even when backend monitor lookup is flaky.
    """
    grouped: Dict[int, List] = {m.id: [] for m in monitors}
    if not monitors:
        return grouped

    for window in windows:
        monitor = None
        try:
            monitor = window.monitor
        except Exception:  # pylint: disable=broad-except
            monitor = None

        chosen = None
        if monitor is not None:
            chosen = next((m for m in monitors if m.id == monitor.id), None)
        if chosen is None:
            chosen = _best_monitor_for_window(window, monitors)

        if chosen is not None:
            grouped.setdefault(chosen.id, []).append(window)

    return grouped


def _standard_layout_for_monitor(
    ja: Janela,
    monitor: Monitor,
    windows: List,
    start_state: dict,
) -> List[tuple]:
    """
    Apply the standard mosaic layout to a single monitor.
    """
    placements: List[tuple] = []

    if len(windows) == 1:
        window = windows[0]
        if ja.is_window_maximized(window):
            ja.unmaximize_window(window)
        ja.resize_window(window, monitor.width, monitor.height)
        ja.move_window_to_position(window, monitor.x, monitor.y)
        start_rect = start_state.get(
            window.id,
            (window.x, window.y, window.width, window.height),
        )
        placements.append(
            (window, start_rect, (monitor.x, monitor.y, monitor.width, monitor.height))
        )
        return placements

    if monitor.is_vertical() and len(windows) == 2:
        heights = [
            monitor.height // 2 + (monitor.height % 2),
            monitor.height // 2,
        ]
        top_y = monitor.y
        bottom_y = monitor.y + heights[0]

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
        return placements

    rows, columns = get_number_of_rows_columns(len(windows), monitor)
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
                y = monitor.y + top_offset
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

    return placements


def _layout_windows_in_area(
    ja: Janela,
    area: _LayoutArea,
    windows: List,
    start_state: dict,
) -> List[tuple]:
    """
    Tile a set of windows inside a rectangular area.
    """
    placements: List[tuple] = []
    if not windows:
        return placements

    if len(windows) == 1:
        window = windows[0]
        if ja.is_window_maximized(window):
            ja.unmaximize_window(window)
        ja.resize_window(window, area.width, area.height)
        ja.move_window_to_position(window, area.x, area.y)
        start_rect = start_state.get(
            window.id,
            (window.x, window.y, window.width, window.height),
        )
        placements.append(
            (
                window,
                start_rect,
                (area.x, area.y, area.width, area.height),
            )
        )
        return placements

    if area.is_vertical() and len(windows) == 2:
        heights = [
            area.height // 2 + (area.height % 2),
            area.height // 2,
        ]
        top_y = area.y
        bottom_y = area.y + heights[0]

        top_window, bottom_window = windows[0], windows[1]
        targets = [
            (top_window, area.x, top_y, area.width, heights[0]),
            (bottom_window, area.x, bottom_y, area.width, heights[1]),
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
        return placements

    rows, columns = get_number_of_rows_columns(len(windows), area)
    base_height = area.height // rows
    extra_height = area.height % rows

    idx = 0
    top_offset = 0
    for row in range(rows):
        remaining = len(windows) - idx
        cols_this_row = min(columns, remaining)
        if cols_this_row <= 0:
            break

        row_height = base_height + (1 if row < extra_height else 0)
        base_width = area.width // cols_this_row
        extra_width = area.width % cols_this_row

        current_x = area.x
        for col in range(cols_this_row):
            window = windows[idx]
            idx += 1
            try:
                if ja.is_window_maximized(window):
                    ja.unmaximize_window(window)

                width = base_width + (1 if col < extra_width else 0)
                height = row_height
                x = current_x
                y = area.y + top_offset
                current_x += width

                logger.debug(
                    "Resizing and moving window '%s' to (%d, %d) with size (%d, %d) in area %s",
                    window.name,
                    x,
                    y,
                    width,
                    height,
                    area.name,
                )

                ja.resize_window(window, width, height)
                ja.move_window_to_position(window, x, y)
                start_rect = start_state.get(
                    window.id,
                    (window.x, window.y, window.width, window.height),
                )
                placements.append((window, start_rect, (x, y, width, height)))
            except Exception as e:  # pylint: disable=broad-except
                logger.exception(
                    "Error processing window '%s' in area %s: %s",
                    window.name,
                    area.name,
                    e,
                )

        top_offset += row_height

    return placements


def _areas_around_active(monitor: Monitor, active_window) -> List[_LayoutArea]:
    """
    Build rectangular regions around the active window within the monitor bounds.
    """
    active_left = max(monitor.x, active_window.x)
    active_top = max(monitor.y, active_window.y)
    active_right = min(
        monitor.x + monitor.width,
        active_window.x + active_window.width,
    )
    active_bottom = min(
        monitor.y + monitor.height,
        active_window.y + active_window.height,
    )

    areas: List[_LayoutArea] = []

    top_height = active_top - monitor.y
    if top_height > 0:
        areas.append(
            _LayoutArea("top", (monitor.x, monitor.y, monitor.width, top_height))
        )

    bottom_height = (monitor.y + monitor.height) - active_bottom
    if bottom_height > 0:
        areas.append(
            _LayoutArea(
                "bottom",
                (
                    monitor.x,
                    active_bottom,
                    monitor.width,
                    bottom_height,
                ),
            )
        )

    middle_height = active_bottom - active_top
    left_width = active_left - monitor.x
    if left_width > 0 and middle_height > 0:
        areas.append(
            _LayoutArea("left", (monitor.x, active_top, left_width, middle_height))
        )

    right_width = (monitor.x + monitor.width) - active_right
    if right_width > 0 and middle_height > 0:
        areas.append(
            _LayoutArea(
                "right",
                (
                    active_right,
                    active_top,
                    right_width,
                    middle_height,
                ),
            )
        )

    return areas


def _assign_windows_to_areas(windows: List, areas: List[_LayoutArea]) -> dict:
    """
    Distribute windows to available areas, preferring larger free areas.
    """
    if not areas:
        return {}

    allocations = {area: [] for area in areas}
    for window in windows:
        best_area = max(
            areas,
            key=lambda area: (area.width * area.height)
            / (len(allocations[area]) + 1),
        )
        allocations[best_area].append(window)

    return allocations


def _finalize_monitor_layout(
    ja: Janela,
    monitor: Monitor,
    placements: List[tuple],
):
    """
    Verify placements and log an ASCII plan for the monitor.
    """
    if not placements:
        return

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


def _build_display_plan(monitor: Monitor, placements: List[tuple]) -> "Optional[DisplayPlan]":
    if monitor.width <= 0 or monitor.height <= 0 or not placements:
        return None

    tiles: List[TilePlan] = []
    for idx, (window, _, target_rect) in enumerate(placements, start=1):
        x, y, w, h = target_rect
        rel_x = x - monitor.x
        top_y = y - monitor.y
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
