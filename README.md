# Universal Linux Macro Studio

A powerful, low-level macro creation and automation tool for Linux systems. This application allows you to record keyboard shortcuts as triggers and execute complex sequences of keyboard and mouse actions.

## Features

- **Global Hotkey Triggers**: Bind any combination of keys (including modifiers like Ctrl, Alt, Shift) to trigger a macro.
- **Virtual Input Injection**: Uses `uinput` to simulate real hardware input, making it compatible with almost all Linux applications and games.
- **Complex Sequences**: Create macros that include:
    - **Key Inputs**: Single keys or combinations.
    - **Wait/Delays**: Precise timing between actions.
    - **Mouse Movements**: Relative mouse movement.
    - **Mouse Clicks**: Left-click simulation.
- **Repeat Logic**: Set macros to repeat a specific number of times.
- **Persistent Storage**: Macros are saved to `data.json` and loaded automatically on startup.
- **Smart Binding**: Intelligent key recording that distinguishes between modifiers and terminal keys.

## Prerequisites

- **Operating System**: Linux (requires `uinput` kernel module support).
- **Python**: 3.x
- **Libraries**: `evdev`, `tkinter` (usually comes with Python).

## Installation

1. Install the `evdev` library:
   ```bash
   pip install evdev
   ```

2. Ensure your user has access to `/dev/uinput` or run the application with `sudo`.

## Usage

Since the application interacts directly with input device files and creates a virtual device, it **must be run with root privileges**:

```bash
sudo python main.py
```

### Creating a Macro

1. **Trigger Combo**: Click the "Click to bind..." button and press the keys you want to use as a trigger (e.g., `Ctrl+Shift+M`).
2. **Add Actions**:
    - Select an **Action Type** (Key Input, Wait, or Mouse Move).
    - For **Key Input**, click "Record Keystrokes" and press the keys to be simulated.
    - Click **Add Action** to add it to the sequence.
3. **Repeat Count**: Set how many times the sequence should run.
4. **Save**: Click **SAVE MACRO**. It will appear in the "Active Macros" dashboard.

## Technical Details

- **Engine**: Built on top of the `evdev` library for high-performance input event handling.
- **Virtual Device**: Creates a virtual keyboard/mouse device named `MacroStudio-Virtual-Device`.
- **Concurrency**: Uses Python threading to ensure the GUI remains responsive while macros are executing or the engine is listening for triggers.

## Security Warning

This application requires root privileges because it reads raw input from your hardware devices. Use only with trusted macro configurations.
