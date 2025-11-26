"""Run the mosaic-around-active-window layout example."""

from janela import Janela
from janela.effects import mosaic_around_window


if __name__ == "__main__":
    ja = Janela()
    mosaic_around_window(ja)
