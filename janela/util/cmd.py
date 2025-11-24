"""Utility helpers for running subprocess commands."""

import subprocess
from typing import List

from janela.logger import logger


def run_command(command: List[str]) -> str:
    """Run a command and return its stdout as text; logs errors."""
    try:
        return subprocess.check_output(command, stderr=subprocess.STDOUT).decode(
            "utf-8"
        )
    except subprocess.CalledProcessError as exc:
        logger.error("Command failed: %s", " ".join(command))
        logger.error("Error output: %s", exc.output.decode("utf-8"))
        return ""
