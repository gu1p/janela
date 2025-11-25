# pylint: disable=duplicate-code
"""Example: minimize all controllable windows, wait, then restore them."""

import time

from janela import Janela


def main():
    """Minimize all controllable windows, pause, then restore them."""
    ja = Janela()

    # Snapshot windows before minimizing; minimized ones may not show up in later listings.
    windows = [w for w in ja.list_windows() if ja.can_control_window(w)]
    if not windows:
        print("No controllable windows found.")
        return

    print(f"Minimizing {len(windows)} window(s)...")
    for window in windows:
        try:
            window.minimize()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Failed to minimize '{window.name}': {exc}")

    time.sleep(2)

    print("Restoring windows...")
    for window in windows:
        try:
            if window.is_minimized():
                window.unminimize()
            else:
                # If the window did not report minimized, still attempt a restore.
                window.unminimize()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Failed to restore '{window.name}': {exc}")

    print("Done.")


if __name__ == "__main__":
    main()
