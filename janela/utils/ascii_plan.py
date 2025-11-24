"""ASCII renderer for display/tile layouts."""

from dataclasses import dataclass
from typing import List


@dataclass
class TilePlan:
    """Represents a tile within a display."""

    index: int
    x: int
    y: int
    width: int
    height: int
    label: str


@dataclass
class DisplayPlan:
    """Represents an entire display with tiles, and can render to ASCII."""

    width: int
    height: int
    tiles: List[TilePlan]

    def render_ascii(self, max_width: int = 80, max_height: int = 40) -> str:
        if self.width <= 0 or self.height <= 0 or not self.tiles:
            return ""

        scale = min(max_width / self.width, max_height / self.height)
        width_chars = max(20, int(round(self.width * scale)))
        height_chars = max(10, int(round(self.height * scale)))

        canvas = [[" " for _ in range(width_chars + 2)] for _ in range(height_chars + 2)]

        # Draw outer display border.
        self._draw_border(canvas, 0, 0, width_chars + 2, height_chars + 2)

        for tile in self.tiles:
            sx = 1 + int(round(tile.x * scale))
            sy = 1 + int(round(tile.y * scale))
            ex = sx + max(2, int(round(tile.width * scale)))
            ey = sy + max(2, int(round(tile.height * scale)))

            sx = max(1, min(sx, width_chars))
            sy = max(1, min(sy, height_chars))
            ex = max(sx + 1, min(ex, width_chars + 1))
            ey = max(sy + 1, min(ey, height_chars + 1))

            self._draw_border(canvas, sx, sy, ex - sx, ey - sy)

            # Place label once near the center.
            label = tile.label or str(tile.index)
            label_x = sx + max(1, (ex - sx - len(label)) // 2)
            label_y = sy + max(0, (ey - sy) // 2)
            for i, ch in enumerate(label):
                pos_x = label_x + i
                if pos_x < ex - 1:
                    canvas[label_y][pos_x] = ch

        lines = ["".join(row).rstrip() for row in canvas]
        return "\n".join(lines)

    @staticmethod
    def _draw_border(canvas: List[List[str]], x: int, y: int, width: int, height: int) -> None:
        max_y = len(canvas) - 1
        max_x = len(canvas[0]) - 1 if canvas else 0
        end_x = min(x + width - 1, max_x)
        end_y = min(y + height - 1, max_y)

        for xx in range(x, end_x + 1):
            if 0 <= y <= max_y:
                canvas[y][xx] = "-" if canvas[y][xx] == " " else canvas[y][xx]
            if 0 <= end_y <= max_y:
                canvas[end_y][xx] = "-" if canvas[end_y][xx] == " " else canvas[end_y][xx]

        for yy in range(y, end_y + 1):
            if 0 <= yy <= max_y:
                if 0 <= x <= max_x:
                    canvas[yy][x] = "|" if canvas[yy][x] == " " else canvas[yy][x]
                if 0 <= end_x <= max_x:
                    canvas[yy][end_x] = "|" if canvas[yy][end_x] == " " else canvas[yy][end_x]

        if 0 <= y <= max_y and 0 <= x <= max_x:
            canvas[y][x] = "+"
        if 0 <= y <= max_y and 0 <= end_x <= max_x:
            canvas[y][end_x] = "+"
        if 0 <= end_y <= max_y and 0 <= x <= max_x:
            canvas[end_y][x] = "+"
        if 0 <= end_y <= max_y and 0 <= end_x <= max_x:
            canvas[end_y][end_x] = "+"
