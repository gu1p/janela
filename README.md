# Janela

_Window handling for humans_


[![PyPI version](https://badge.fury.io/py/janela.svg)](https://badge.fury.io/py/janela)
[![License](https://img.shields.io/pypi/l/janela.svg)](https://pypi.org/project/janela/)
[![Python Version](https://img.shields.io/pypi/pyversions/janela.svg)](https://pypi.org/project/janela/)
[![Platform](https://img.shields.io/badge/platform-linux-lightgrey.svg)](https://pypi.org/project/janela/)

A simple Python package for managing and manipulating application windows on **GUI** environments. **janela** provides an easy-to-use interface for moving, resizing, maximizing, minimizing, and arranging windows across multiple monitors.

**Note:** Currently, Janela supports **Linux** platforms. Support for **Windows** and **macOS** is in progress and will be available in future releases.

## Features

- **Move windows between monitors**: Easily transfer windows from one monitor to another.
- **Resize windows**: Adjust the size of windows programmatically.
- **Maximize, minimize, and restore windows**: Control window states effortlessly.
- **Focus on windows**: Bring specific windows to the foreground.
- **Close windows**: Programmatically close application windows.
- **Arrange windows in mosaic patterns**: Organize your windows in a mosaic layout across your monitors.

## Table of Contents

- [Installation](#installation)
  - [Linux](#linux)
  - [Windows (Coming Soon)](#windows-coming-soon)
  - [macOS](#macos)
- [Requirements](#requirements)
- [Usage](#usage)
  - [Basic Example](#basic-example)
  - [Moving a Window to Another Monitor](#moving-a-window-to-another-monitor)
  - [Resizing and Moving Windows](#resizing-and-moving-windows)
  - [Maximizing and Minimizing Windows](#maximizing-and-minimizing-windows)
  - [Focusing and Closing Windows](#focusing-and-closing-windows)
- [API Reference](#api-reference)
  - [WindowManager](#windowmanager)
  - [Window](#window)
  - [Monitor](#monitor)
- [Tested Platforms](#tested-platforms)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgements](#acknowledgements)

## Installation

### Linux

First, ensure that you have the required system dependencies installed.

#### Install `xdotool` and `wmctrl`

On **Debian/Ubuntu-based systems**:

```bash
sudo apt-get update
sudo apt-get install xdotool wmctrl
```

#### Install Janela

You can install Janela from PyPI:

```bash
pip install janela
```

### Windows (Coming Soon)

Support for **Windows** platforms is currently under development. Stay tuned for upcoming releases that will include Windows support.

### macOS

Install from PyPI (this pulls the necessary PyObjC/Quartz bindings automatically):

```bash
pip install janela
```

On first run, macOS will prompt you for **Accessibility** permission for the Python interpreter or terminal you are using. Approve that prompt (or pre-authorize via *System Settings → Privacy & Security → Accessibility*). Janela will request this permission automatically when needed.

## Requirements

- **Python 3.6 or higher**
- **Linux**:
  - System packages: [`xdotool`](https://www.semicomplete.com/projects/xdotool/), [`wmctrl`](http://tomas.styblo.name/wmctrl/)
  - Python packages: [`python-xlib`](https://pypi.org/project/python-xlib/)
- **macOS**:
  - Python packages: [`pyobjc-core`](https://pypi.org/project/pyobjc-core/), [`pyobjc-framework-Cocoa`](https://pypi.org/project/pyobjc-framework-Cocoa/), [`pyobjc-framework-ApplicationServices`](https://pypi.org/project/pyobjc-framework-ApplicationServices/), [`pyobjc-framework-Quartz`](https://pypi.org/project/pyobjc-framework-Quartz/)
  - Permissions: Accessibility (Janela will prompt automatically) and Screen Recording (needed for listing/managing other apps' windows)

## Usage

**Note:** The following usage examples are applicable to **Linux** platforms. Support for Windows and macOS is coming soon.

### Basic Example

Arrange all windows in a mosaic pattern across all monitors:

```python
from janela import Janela, effects

# Initialize the window manager
ja = Janela()

# Arrange windows in a mosaic pattern
effects.mosaic(ja)
```

Arrange all other windows around the active window on its monitor (while other monitors use normal mosaic):

```python
from janela import Janela, effects

ja = Janela()
effects.mosaic_around_window(ja)
```

### Moving a Window to Another Monitor

```python
from janela import Janela

# Initialize the window manager
ja = Janela()

# Get a window by name
window = ja.get_window_by_name("Mozilla Firefox")

# Get the target monitor (e.g., monitor with ID 1)
target_monitor = ja.get_monitor_by_id(1)

# Move the window to the target monitor
if window and target_monitor:
  window.move_to_monitor(target_monitor)
```

### Resizing and Moving Windows

```python
from janela import Janela

ja = Janela()

# Get the active window
window = ja.get_active_window()

if window:
  # Move the window to position (100, 100)
  window.set_position(100, 100)

  # Resize the window to width 800 and height 600
  window.resize(800, 600)

  # Grow 120px to the right without leaving the current monitor
  window.resize_right(120)

  # Grow 80px upward while keeping the bottom edge fixed
  window.resize_top(80)
```

### Maximizing and Minimizing Windows

```python
from janela import Janela

ja = Janela()

# Get a window by name
window = ja.get_window_by_name("Terminal")

if window:
  # Maximize the window
  window.maximize()

  # Check if the window is maximized
  if window.is_maximized():
    print(f"Window '{window.name}' is maximized.")

  # Minimize the window
  window.minimize()

  # Restore a minimized window (falls back to a sane size if no cached bounds)
  if window.is_minimized():
    window.unminimize()
```

### Focusing and Closing Windows

```python
from janela import Janela

ja = Janela()

# Get a window by name
window = ja.get_window_by_name("Text Editor")

if window:
  # Focus the window
  window.focus()

  # Close the window
  window.close()
```

## API Reference

### WindowManager

The `WindowManager` class is the main interface to manage windows and monitors.

**Methods:**

- `list_windows()`: List all open windows.
- `list_monitors()`: List all available monitors.
- `get_active_window()`: Get the currently active window.
- `get_window_by_name(name)`: Get a window by its name.
- `get_monitor_by_id(monitor_id)`: Get a monitor by its ID.
- `is_window_minimized(window)`: Check if a window is minimized.
- `unminimize_window(window)`: Restore a minimized window (uses cached bounds when available).

### Window

Represents a window in the system.

**Attributes:**

- `id`: The window ID.
- `name`: The window name.
- `x`, `y`: The position of the window.
- `width`, `height`: The dimensions of the window.
- `is_active`: Whether the window is currently active.

**Methods:**

- `set_position(x, y)`: Move the window to the specified position.
- `move(x, y)`: Move the window by the specified offset.
- `resize(width, height)`: Resize the window.
- `resize_left(delta)`: Increase width toward the left within the current monitor.
- `resize_right(delta)`: Increase width toward the right within the current monitor.
- `resize_top(delta)`: Increase height upward within the current monitor.
- `resize_down(delta)`: Increase height downward within the current monitor.
- `minimize()`: Minimize the window.
- `maximize()`: Maximize the window.
- `move_to_monitor(monitor)`: Move the window to the specified monitor.
- `is_maximized()`: Check if the window is maximized.
- `unmaximize()`: Unmaximize the window.
- `is_minimized()`: Check if the window is minimized.
- `unminimize()`: Restore a minimized window.
- `focus()`: Bring the window to the foreground.
- `close()`: Close the window.

### Monitor

Represents a monitor connected to the system.

**Attributes:**

- `id`: The monitor ID.
- `name`: The monitor name.
- `x`, `y`: The position of the monitor.
- `width`, `height`: The dimensions of the monitor.

**Methods:**

- `list_windows()`: List all windows on this monitor.
- `contains(x, y)`: Check if a point is within this monitor.
- `is_vertical()`: Check if the monitor is vertically oriented.
- `is_horizontal()`: Check if the monitor is horizontally oriented.
- `aspect_ratio()`: Get the aspect ratio of the monitor.

## Tested Platforms

The following table lists the operating systems and distributions where **Janela** has been tested. This helps track where the library works and where it might encounter issues.

| Operating System | Distribution | Version(s)              | Status           |
|------------------|--------------|-------------------------|------------------|
| **Linux**        | Ubuntu (KDE) | 20.04 LTS, 22.04 LTS    | ✅ Works          |
| **Windows**      | -            | -                       | 🚧 In Progress    |
| **macOS**        | -            | -                       | 🚧 In Progress    |

*Note: As the library is designed specifically for Linux systems at the moment, Windows and macOS support is currently under development. We are actively working on bringing Janela's capabilities to these platforms in future releases.*

## Contributing

Contributions are welcome! Please submit a pull request or open an issue on [GitHub](https://github.com/yourusername/janela).

If you're interested in helping us bring support for Windows and macOS platforms, feel free to contribute to the project.

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.

## Acknowledgements

- [xdotool](https://www.semicomplete.com/projects/xdotool/) - Simulate keyboard input and mouse activity.
- [wmctrl](http://tomas.styblo.name/wmctrl/) - Interact with an X Window Manager.
- [python-xlib](https://pypi.org/project/python-xlib/) - Python interface to the X11 protocol client library.
