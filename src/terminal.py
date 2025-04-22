import atexit
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

import src.signal_handler as signal_handler
from src.config import Configuration, PrintMode

if os.name == 'nt':
    from pyreadline3 import Readline
    readline = Readline()
    from src.winpty import PtyProcess
else:
    import readline
    from ptyprocess import PtyProcessUnicode as PtyProcess

ADD_TO_HISTORY = os.name == 'nt'
DEFAULT_OUTPUT_WIDTH = 80
HISTORY_FILE = "history"
BAR_EIGHTHS = " ▏▎▍▌▋▊▉█"
HEIGHT_EIGHTHS = " ▁▂▃▄▅▆▇█"
ANSI_RESET = "\033[0m"
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
TELEPORT_RE = re.compile(r'\x1b\[\d+;(\d+)H')
ANSI_COLOR_MAP = {
    "black": "0;30",
    "red": "0;91",
    "green": "0;92",
    "yellow": "0;93",
    "blue": "0;94",
    "purple": "0;95",
    "cyan": "0;96",
    "white": "0;97",
    "bold black": "1;30",
    "bold red": "1;91",
    "bold green": "1;92",
    "bold yellow": "1;93",
    "bold blue": "1;94",
    "bold purple": "1;95",
    "bold cyan": "1;96",
    "bold white": "1;97",
    "dark black": "0;30",
    "dark red": "0;31",
    "dark green": "0;32",
    "dark yellow": "0;33",
    "dark blue": "0;34",
    "dark purple": "0;35",
    "dark cyan": "0;36",
    "dark white": "0;37",
}
ANSI_BG_COLOR_MAP = {
    "black": "0;100",
    "red": "0;101",
    "green": "0;102",
    "yellow": "0;103",
    "blue": "0;104",
    "purple": "0;105",
    "cyan": "0;106",
    "white": "0;107",
    "dark black": "0;40",
    "dark red": "0;41", 
    "dark green": "0;42",
    "dark yellow": "0;43",
    "dark blue": "0;44",
    "dark purple": "0;45",
    "dark cyan": "0;46",
    "dark white": "0;47",
}


def init_terminal() -> None:
    if sys.stdin.isatty():
        if os.name == "nt":
            _windows_enable_ANSI(1)
            _windows_enable_ANSI(2)
        try:
            readline.read_history_file(HISTORY_FILE)
        except FileNotFoundError:
            pass
        atexit.register(readline.write_history_file, HISTORY_FILE)
        if not ADD_TO_HISTORY:
            readline.set_auto_history(True)


# https://stackoverflow.com/questions/36760127/how-to-use-the-new-support-for-ansi-escape-sequences-in-the-windows-10-console
def _windows_enable_ANSI(std_id):
    """Enable Windows 10 cmd.exe ANSI VT Virtual Terminal Processing."""
    from ctypes import byref, POINTER, windll, WINFUNCTYPE
    from ctypes.wintypes import BOOL, DWORD, HANDLE

    GetStdHandle = WINFUNCTYPE(
        HANDLE,
        DWORD)(('GetStdHandle', windll.kernel32))

    GetFileType = WINFUNCTYPE(
        DWORD,
        HANDLE)(('GetFileType', windll.kernel32))

    GetConsoleMode = WINFUNCTYPE(
        BOOL,
        HANDLE,
        POINTER(DWORD))(('GetConsoleMode', windll.kernel32))

    SetConsoleMode = WINFUNCTYPE(
        BOOL,
        HANDLE,
        DWORD)(('SetConsoleMode', windll.kernel32))

    if std_id == 1:       # stdout
        h = GetStdHandle(-11)
    elif std_id == 2:     # stderr
        h = GetStdHandle(-12)
    else:
        return False

    if h is None or h == HANDLE(-1):
        return False

    FILE_TYPE_CHAR = 0x0002
    if (GetFileType(h) & 3) != FILE_TYPE_CHAR:
        return False

    mode = DWORD()
    if not GetConsoleMode(h, byref(mode)):
        return False

    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    if (mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING) == 0:
        SetConsoleMode(h, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    return True


def add_to_history(command: str) -> None:
    if ADD_TO_HISTORY:
        readline.add_history(command)


def get_cols() -> int:
    if sys.stdin.isatty():
        return os.get_terminal_size().columns
    else:
        return DEFAULT_OUTPUT_WIDTH


def _execute_in_subwindow_pty(command: list[str], title: str, rows: int, cwd: Optional[str], eat_kill: bool) -> bool:
    process = PtyProcess.spawn(command, cwd=cwd)
    cols = get_cols()
    output_lines = [""]
    lines_printed = 0
    top = box_top(bold=False, title=title)
    if eat_kill and signal_handler.SHOULD_EXIT:
        signal_handler.SHOULD_EXIT = False
        process.kill(signal.SIGINT)
    should_exit = False
    bottom = box_bottom(bold=False, title=signal_handler.MESSAGE if should_exit else "")
    rows -= 2
    ansi_codes_seen = set()

    while process.isalive():
        try:
            stdout_chunk = process.read(4)
            if stdout_chunk:
                lines = stdout_chunk.split('\n')
                output_lines[-1] += lines[0]
                output_lines += lines[1:]
            else:
                time.sleep(0.1)
        except EOFError:
            break
        
        window_lines = []
        for line in output_lines[::-1]:
            split_lines = split_to_display_lines(line, cols)
            if len(split_lines) > rows - len(window_lines):
                if rows > len(window_lines):
                    window_lines = split_lines[:rows - len(window_lines)] + window_lines
                break
            window_lines = split_lines + window_lines

        if signal_handler.SHOULD_EXIT and not should_exit:
            if eat_kill:
                signal_handler.SHOULD_EXIT = False
                process.kill(signal.SIGINT)
            else:
                should_exit = signal_handler.SHOULD_EXIT
                bottom = box_bottom(bold=False, title=signal_handler.MESSAGE)
                for i in range(2):
                    clear_line()
                    move_rows_up(1)
                clear_line()

        move_rows_up(lines_printed)
        lines_printed = len(window_lines) + 2
        output = "\n".join(
            "\033[2K" + "".join(ansi_stack) + line_text 
            for ansi_stack, line_text in window_lines
        )
        ansi_codes_seen.update({match.group() for match in ANSI_ESCAPE.finditer(output)})
        output = ANSI_RESET + top + "\n" + output + ANSI_RESET + "\n" + bottom
        print(output)

    process.wait()
    # print("Ansicodes seen:", ansi_codes_seen)
    if process.exitstatus != 0:
        print("Dumping full process log because an error occurred:")
        print("\n".join(output_lines))
    return process.exitstatus == 0


def execute_in_subwindow(command: list[str], title: str, rows: int, cwd: Optional[str] = None, eat_kill: bool = False) -> bool:
    if cwd == "":
        cwd = None
    if len(command) > 0 and not os.path.exists(command[0]):
        path_locator = shutil.which(command[0])
        if path_locator:
            command[0] = path_locator

    if Configuration.PRINT_MODE == PrintMode.LIVE:
        return _execute_in_subwindow_pty(command, title, rows, cwd, eat_kill)
    elif Configuration.PRINT_MODE == PrintMode.QUIET:
        stdout = subprocess.DEVNULL
        stderr = subprocess.DEVNULL
    else:
        if Configuration.PRINT_MODE != PrintMode.VERBOSE:
            print(f"Internal error: unknown print mode {Configuration.PRINT_MODE}. Falling back to VERBOSE.")
        stdout = sys.stdout
        stderr = sys.stderr

    if len(command) > 0 and not os.path.exists(command[0]):
        path_locator = shutil.which(command[0])
        if path_locator:
            command[0] = path_locator
    process = subprocess.Popen(
        command,
        stdout=stdout,
        stderr=stderr,
        text=True,
        cwd=cwd,
    )
    process.wait()
    return process.returncode == 0


def split_to_display_lines(text: str, columns: int) -> list[tuple[list[str], str]]:
    # TODO do I care about re.match(r"\x1b\[\d+C", line)
    text = TELEPORT_RE.sub('\n', text)
    text = text.replace("\x1b[2J", "\x1b[2K")
    text = text.replace("\x1b[H", "\r")
    re_matches = ANSI_ESCAPE.finditer(text)
    matches = [(m.start(), m.end()) for m in re_matches]
    match_i = 0
    lines = []
    ansi_stack = []

    current_line = ""
    current_line_length = 0
    for i in range(len(text)):
        current_line += text[i]

        while match_i < len(matches) and i >= matches[match_i][1]:
            match_i += 1
        if match_i < len(matches) and i >= matches[match_i][0]:
            continue

        current_line_length += 1
        if text[i] == '\r':
            current_line_length = 0
        elif text[i] == "\n" or current_line_length > columns:
            lines.append((list(ansi_stack), current_line[:-1]))
            current_line = current_line[-1:]
            if current_line == "\n":
                current_line = ""
            current_line_length = min(current_line_length, len(current_line))
    if current_line != "":
        lines.append((list(ansi_stack), current_line))

    return lines


def trim_str(text: str, columns: int) -> str:
    re_matches = ANSI_ESCAPE.finditer(text)
    matches = [(m.start(), m.end()) for m in re_matches]
    match_i = 0
    current_line = ""
    current_line_length = 0
    for i in range(len(text)):
        current_line += text[i]

        while match_i < len(matches) and i >= matches[match_i][1]:
            match_i += 1
        if match_i < len(matches) and i >= matches[match_i][0]:
            continue

        current_line_length += 1
        if text[i] == '\r':
            current_line_length = 0
        if text[i] == "\n" or current_line_length > columns:
            current_line = current_line[:-1]
            break
    return current_line + (ANSI_RESET if len(matches) > 0 else "")


def fit_line(text: str, columns: int = 0, start: str = "", end: str = "") -> str:
    if columns == 0:
        columns = get_cols()

    if len(start) > 0:
        start += " "
    if len(end) > 0:
        end = " " + end
    columns -= escape_len(start) + escape_len(end)

    lines = split_to_display_lines(text, columns)
    escaped_lines = []
    for line in lines:
        ansi_stack, line_text = line
        line_text = "".join(ansi_stack) + line_text
        escaped_lines.append(start + line_text + " " * (columns - escape_len(line_text)) + end)
    return "\n".join(escaped_lines)


def box_fit(text: str, columns: int = 0, bold: bool = True) -> str:
    box_side = "┃" if bold else "│"
    return fit_line(text, columns, box_side, box_side)


def box_top(bold: bool = True, title: str = "") -> str:
    chars = "┌─┐"
    if bold:
        chars = "┏━┓"
    return box_generic(chars, title)


def box_middle(bold: bool = True, title: str = "") -> str:
    chars = "├─┤"
    if bold:
        chars = "┣━┫"
    return box_generic(chars, title)


def box_generic(lmr_chars: str, title: str = "") -> str:
    columns = get_cols()
    # Adjust the width for the box borders
    inner_width = columns - 3 - escape_len(title)
    return lmr_chars[:2] + title + lmr_chars[1] * (inner_width) + lmr_chars[2]


def box_bottom(bold: bool = True, title: str = "") -> str:
    chars = "└─┘"
    if bold:
        chars = "┗━┛"
    return box_generic(chars, title)


def escape_len(text: str) -> int:
    return len(ANSI_ESCAPE.sub("", text))


def progress_bar(cols: int, fraction: float) -> str:
    fraction = max(min(1, fraction), 0)
    start_chars = int(cols * fraction)
    center_eighths = int(cols * fraction * 8) % 8
    end_chars = cols - start_chars - 1
    bar_text = BAR_EIGHTHS[8] * start_chars + BAR_EIGHTHS[center_eighths] + BAR_EIGHTHS[0] * end_chars
    bar_text = color(bar_text, Configuration.PROGRESS_FOREGROUND_COLOR)
    return color_bg(bar_text, Configuration.PROGRESS_BACKGROUND_COLOR)


def histogram_height(fractions: list[float]) -> str:
    output = ""
    for i, fraction in enumerate(fractions):
        eighths = int(max(min(1, fraction), 0) * 7)
        if fraction > 0:
            eighths += 1
        output += HEIGHT_EIGHTHS[eighths]
    output = color(output, Configuration.PROGRESS_FOREGROUND_COLOR)
    return color_bg(output, Configuration.PROGRESS_BACKGROUND_COLOR)


def histogram_color(fractions: list[float]) -> str:
    output = ""
    colors = Configuration.HEATMAP_COLORS
    if len(colors) == 0:
        colors = ["white", "white"]
    elif len(colors) == 1:
        colors = [colors[0], colors[0]]
    for i, fraction in enumerate(fractions):
        color_index = int(max(min(1, fraction), 0) * (len(colors) - 2))
        if fraction > 0:
            color_index += 1
        output += color(HEIGHT_EIGHTHS[8], colors[color_index])
    return output


def move_rows_up(n: int) -> None:
    if Configuration.PRINT_MODE == PrintMode.LIVE and n > 0:
        print(f"\033[{n}A", end="")


def move_rows_down(n: int) -> None:
    if Configuration.PRINT_MODE == PrintMode.LIVE and n > 0:
        print(f"\033[{n}B", end="")


def move_cursor_to(x: int, y: int) -> None:
    if Configuration.PRINT_MODE == PrintMode.LIVE:
        print(f"\033[{y};{x}H", end="")


def move_to_column(n: int) -> None:
    if Configuration.PRINT_MODE == PrintMode.LIVE:
        print(f"\033[{n}G", end="")


def clear_line() -> None:
    if Configuration.PRINT_MODE == PrintMode.LIVE:
        print("\033[2K", end="")


def color(text: str, color: str) -> str:
    if text == "":
        return text
    if len(color) > 1 and color[-1].isdigit():
        color = color
    elif color in ANSI_COLOR_MAP:
        color = ANSI_COLOR_MAP[color]
    else:
        print(f"Recoverable internal error: unknown color {color} requested.")
        return text
    return color_by_code(text, color)


def color_bg(text: str, color: str) -> str:
    if text == "":
        return text
    if len(color) > 1 and color[-1].isdigit():
        color = color
    elif color in ANSI_BG_COLOR_MAP:
        color = ANSI_BG_COLOR_MAP[color]
    else:
        print(f"Recoverable internal error: unknown color {color} requested.")
        return text
    return color_by_code(text, color)


def color_by_code(text: str, color_code: str) -> str:
    if not Configuration.COLOR_ENABLED:
        return text
    return f"\033[{color_code}m{text}\033[0m"


def color_bad(text: str, color_enabled: bool = True) -> str:
    return color(text, Configuration.ERROR_COLOR)


def color_good(text: str, color_enabled: bool = True) -> str:
    return color(text, Configuration.GOOD_COLOR)


def color_rev(text: str, color_enabled: bool = True) -> str:
    return color(text, Configuration.COMMIT_COLOR)


def color_key(text: str, color_enabled: bool = True) -> str:
    return color(text, Configuration.IMPORTANT_COLOR)


def color_code(text: str, color_enabled: bool = True) -> str:
    return color(text, "bold blue")


def warn(text: str) -> str:
    return color("[WARNING]", Configuration.WARNING_COLOR) + " " + text


def error(text: str) -> str:
    return color("[WARNING]", Configuration.ERROR_COLOR) + " " + text