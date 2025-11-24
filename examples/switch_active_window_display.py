"""Move the active window to the next monitor."""

from janela import Janela


def switch_active_window_to_next_display() -> None:
    ja = Janela()

    active_window = ja.get_active_window()
    if active_window is None:
        print("No active window found.")
        return

    if not ja.can_control_window(active_window):
        print(f"Active window '{active_window.name}' cannot be controlled on this platform.")
        return

    monitors = ja.list_monitors()
    if len(monitors) < 2:
        print("Need at least two monitors to switch the active window.")
        return

    ordered_monitors = sorted(monitors, key=lambda m: (m.x, m.y, m.id))
    current_monitor = active_window.monitor
    current_index = -1
    if current_monitor is not None:
        for idx, monitor in enumerate(ordered_monitors):
            if monitor.id == current_monitor.id:
                current_index = idx
                break

    target_monitor = ordered_monitors[(current_index + 1) % len(ordered_monitors)]
    active_window.move_to_monitor(target_monitor)
    active_window.focus()
    print(
        f"Moved '{active_window.name}' to {target_monitor.name} "
        f"({target_monitor.width}x{target_monitor.height} @ "
        f"{target_monitor.x},{target_monitor.y})"
    )


if __name__ == "__main__":
    switch_active_window_to_next_display()
