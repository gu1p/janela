# pylint: disable=duplicate-code
"""Tests for directional window resizing against monitor bounds."""

import unittest

from janela.interfaces.models import Monitor, Window


class FakeWM:
    """Minimal window manager stub for directional resize tests."""

    def __init__(self):
        self.monitor = None
        self.moves = []
        self.resizes = []

    def set_monitor(self, monitor: Monitor):
        """Attach a monitor to the fake WM."""
        self.monitor = monitor

    def get_monitor_for_window(self, window: Window):
        """Return the configured monitor for any window."""
        del window
        return self.monitor

    def move_window_to_position(self, window: Window, x: int, y: int):
        """Record a move and update window coordinates."""
        self.moves.append((window.id, x, y))
        window.x, window.y = x, y

    def resize_window(self, window: Window, width: int, height: int):
        """Record a resize and update window dimensions."""
        self.resizes.append((window.id, width, height))
        window.width, window.height = width, height


def make_window(x: int, y: int, width: int, height: int):
    """Build a window attached to a fake WM with a single monitor."""
    wm = FakeWM()
    monitor = Monitor(id=1, name="primary", x=0, y=0, width=1920, height=1080, wm=wm)
    wm.set_monitor(monitor)
    window = Window(
        id="0x1",
        name="demo",
        x=x,
        y=y,
        width=width,
        height=height,
        is_active=False,
        wm=wm,
    )
    return window, wm


class WindowDirectionalResizeTests(unittest.TestCase):
    """Directional resize behavior stays within monitor bounds."""

    def test_resize_left_clamped_to_monitor(self):
        """Grow leftward but clamp to monitor origin."""
        window, wm = make_window(x=500, y=100, width=400, height=300)

        window.resize_left(200)

        self.assertEqual(window.x, 300)
        self.assertEqual(window.width, 600)
        self.assertEqual(wm.moves, [("0x1", 300, 100)])
        self.assertEqual(wm.resizes, [("0x1", 600, 300)])

    def test_resize_left_hits_monitor_edge(self):
        """Clamp when growth left would exceed monitor."""
        window, _ = make_window(x=50, y=100, width=400, height=300)

        window.resize_left(200)

        self.assertEqual(window.x, 0)
        self.assertEqual(window.width, 450)

    def test_resize_right_clamped_to_monitor(self):
        """Grow right but limit to remaining monitor width."""
        window, wm = make_window(x=1700, y=100, width=150, height=300)

        window.resize_right(200)

        self.assertEqual(window.x, 1700)
        self.assertEqual(window.width, 220)  # only 70px available to the right
        self.assertEqual(wm.moves, [])
        self.assertEqual(wm.resizes, [("0x1", 220, 300)])

    def test_resize_top_clamped_to_monitor(self):
        """Grow upward but clamp to monitor origin."""
        window, wm = make_window(x=100, y=50, width=400, height=200)

        window.resize_top(120)

        self.assertEqual(window.y, 0)
        self.assertEqual(window.height, 250)
        self.assertEqual(wm.moves, [("0x1", 100, 0)])
        self.assertEqual(wm.resizes, [("0x1", 400, 250)])

    def test_resize_down_clamped_to_monitor(self):
        """Grow downward but limit to available space."""
        window, wm = make_window(x=100, y=900, width=400, height=150)

        window.resize_down(200)

        self.assertEqual(window.y, 900)
        self.assertEqual(window.height, 180)  # only 30px available downward
        self.assertEqual(wm.moves, [])
        self.assertEqual(wm.resizes, [("0x1", 400, 180)])


if __name__ == "__main__":
    unittest.main()
