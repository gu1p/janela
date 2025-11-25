"""Resize a random opened window up and then down, with pauses."""

import random
import time

from janela import Janela


def resize_random_window(step: int = 120, levels: int = 3, pause_seconds: float = 0.5) -> None:
    """Interactively pick a controllable window and resize it up then back down."""
    ja = Janela()
    windows = [w for w in ja.list_windows() if ja.can_control_window(w)]
    if not windows:
        print("No controllable windows found.")
        return

    windows = sorted(windows, key=lambda w: (w.name or "").lower())
    print("Available windows (sorted):")
    for idx, w in enumerate(windows):
        name = w.name or "<unnamed>"
        print(f"[{idx}] {name} ({w.id})")

    selection = input("Enter window index (blank for random): ").strip()
    window = random.choice(windows)
    if selection:
        try:
            idx = int(selection)
            if 0 <= idx < len(windows):
                window = windows[idx]
            else:
                print(f"Index {idx} out of range, using random.")
        except ValueError:
            print(f"Invalid index '{selection}', using random.")

    print(f"Selected '{window.name}' ({window.id}).")

    if window.is_maximized():
        window.unmaximize()
    window.focus()

    base_width, base_height = window.width, window.height

    # Increase size.
    for i in range(1, levels + 1):
        window.resize(base_width + i * step, base_height + i * step)
        time.sleep(pause_seconds)

    # Decrease back to the original.
    for i in range(levels - 1, -1, -1):
        window.resize(base_width + i * step, base_height + i * step)
        time.sleep(pause_seconds)


if __name__ == "__main__":
    resize_random_window()
