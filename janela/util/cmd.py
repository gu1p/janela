import subprocess
from typing import List

from janela.logger import logger


def run_command(command: List[str]) -> str:
    try:
        return subprocess.check_output(command, stderr=subprocess.STDOUT).decode(
            "utf-8"
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {' '.join(command)}")
        logger.error(f"Error output: {e.output.decode('utf-8')}")
        return ""
