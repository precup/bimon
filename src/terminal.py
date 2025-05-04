import atexit
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Optional

from src import signal_handler
from src import storage
from src.config import Configuration, PrintMode

if os.name == "nt":
    from pyreadline3 import Readline
    readline = Readline()
    from src.winpty import PtyProcess
else:
    import readline
    from ptyprocess import PtyProcessUnicode as PtyProcess

DEFAULT_OUTPUT_WIDTH = 80
ANSI_RESET = "\033[0m"
ANSI_CLEAR_LINE = "\033[2K"

_HISTORY_FILE = storage.get_state_filename("history")
_UNICODE_BAR_PARTS = " ▏▎▍▌▋▊▉█"
_C437_BAR_PARTS = " ▌█"
_HEIGHT_EIGHTHS = " ▁▂▃▄▅▆▇█"
_UNICODE_HEIGHT_PARTS = " ▁▂▃▄▅▆▇█"
_C437_HEIGHT_PARTS = " ░▒▓█"
_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_TELEPORT_RE = re.compile(r"\x1b\[\d+;(\d+)H")
_ANSI_COLOR_MAP = {
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
_ANSI_BG_COLOR_MAP = {
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
        try:
            readline.read_history_file(_HISTORY_FILE)
        except FileNotFoundError:
            pass
        atexit.register(readline.write_history_file, _HISTORY_FILE)
        if os.name == "nt":
            _windows_enable_ANSI(1)
            _windows_enable_ANSI(2)
        else:
            readline.set_auto_history(True)


def set_command_completer(completer: callable) -> None:
    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")
    readline.parse_and_bind("set show-all-if-ambiguous on")


# https://stackoverflow.com/questions/36760127/how-to-use-the-new-support-for-ansi-escape-sequences-in-the-windows-10-console
def _windows_enable_ANSI(std_id):
    """Enable Windows 10 cmd.exe ANSI VT Virtual Terminal Processing."""
    from ctypes import byref, POINTER, windll, WINFUNCTYPE
    from ctypes.wintypes import BOOL, DWORD, HANDLE

    GetStdHandle = WINFUNCTYPE(
        HANDLE,
        DWORD)(("GetStdHandle", windll.kernel32))

    GetFileType = WINFUNCTYPE(
        DWORD,
        HANDLE)(("GetFileType", windll.kernel32))

    GetConsoleMode = WINFUNCTYPE(
        BOOL,
        HANDLE,
        POINTER(DWORD))(("GetConsoleMode", windll.kernel32))

    SetConsoleMode = WINFUNCTYPE(
        BOOL,
        HANDLE,
        DWORD)(("SetConsoleMode", windll.kernel32))

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
    if os.name == "nt":
        readline.add_history(command)


def get_cols() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return DEFAULT_OUTPUT_WIDTH


def _execute_in_subwindow_pty(
        command: list[str], 
        title: str, 
        rows: int, 
        cwd: Optional[str], 
        eat_kill: bool) -> bool:
    process = PtyProcess.spawn(command, cwd=cwd)
    cols = get_cols()
    output_lines = [""]
    lines_printed = 0
    top = box_top(bold=False, title=title)
    bottom = box_bottom(bold=False, title=signal_handler.status())
    rows -= 2
    ansi_codes_seen = set()
    already_soft_killed = False

    while process.isalive():
        try:
            stdout_chunk = process.read(1024)
            if len(stdout_chunk) > 0:
                lines = stdout_chunk.split("\n")
                if len(lines) == 1:
                    old_lines = split_to_display_lines(output_lines[-1], cols)
                    new_lines = split_to_display_lines(output_lines[-1] + lines[0], cols)
                    if len(old_lines) == len(new_lines):
                        output_lines[-1] += lines[0]
                        line_text = "".join(new_lines[-1][0]) + new_lines[-1][1]
                        print(ANSI_RESET + ANSI_CLEAR_LINE + line_text, end="")
                        sys.stdout.flush()
                        continue
                output_lines[-1] += lines[0]
                output_lines += lines[1:]
            else:
                time.sleep(0.1)
                continue
        except EOFError:
            break
        
        window_lines: list[tuple[list[str], str]] = []
        last_non_blank = -1
        while last_non_blank >= -len(output_lines) and output_lines[last_non_blank].strip() == "":
            last_non_blank -= 1
        for line in output_lines[last_non_blank::-1]:
            split_lines = split_to_display_lines(line, cols)
            if len(split_lines) > rows - len(window_lines):
                if rows > len(window_lines):
                    window_lines = split_lines[:rows - len(window_lines)] + window_lines
                break
            window_lines = split_lines + window_lines

        if signal_handler.soft_killed() and not already_soft_killed:
            if eat_kill:
                signal_handler.clear()
                process.kill(signal.SIGINT)
            elif not signal_handler.hard_killed():
                already_soft_killed = True
                bottom = box_bottom(bold=False, title=signal_handler.status())
                print(move_rows_up(1), end="\r")
                sys.stdout.flush()
            else:
                print()

        prev_lines_printed = lines_printed
        lines_printed = max(1, len(window_lines))
        center = "\n".join(
            ANSI_CLEAR_LINE + "".join(ansi_stack) + line_text 
            for ansi_stack, line_text in window_lines
        )
        ansi_codes_seen.update({match.group() for match in _ANSI_ESCAPE.finditer(center)})
        output = (
            (f"\033[{prev_lines_printed}A\r" if prev_lines_printed > 0 else "") 
            + ANSI_RESET + top + "\n" 
            + center + ANSI_RESET + "\n" 
            + bottom + "\r\033[1A"
        )
        print(output, end="")
        sys.stdout.flush()

    process.wait()
    if lines_printed > 0:
        print("\n")
    # print("Ansicodes seen:", ansi_codes_seen)
    if process.exitstatus != 0:
        print("Dumping full process log because an error occurred:")
        print("\n".join(output_lines))
    return process.exitstatus == 0


def execute_in_subwindow(
        command: list[str], 
        title: str, 
        rows: int, 
        cwd: Optional[str] = None, 
        eat_kill: bool = False) -> bool:
    if cwd == "":
        cwd = None
    if len(command) > 0:
        if os.path.exists(command[0]):
            try:
                exec_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                os.chmod(command[0], os.stat(command[0]).st_mode | exec_bits)
            except:
                pass
        else:
            path_locator = shutil.which(command[0])
            if path_locator is not None:
                command[0] = path_locator

    if Configuration.PRINT_MODE == PrintMode.LIVE:
        return _execute_in_subwindow_pty(command, title, rows, cwd, eat_kill)
    elif Configuration.PRINT_MODE == PrintMode.QUIET:
        stdout = subprocess.DEVNULL
        stderr = subprocess.DEVNULL
    else:
        if Configuration.PRINT_MODE != PrintMode.VERBOSE:
            print(f"Internal error: unknown print mode {Configuration.PRINT_MODE}."
                  + " Falling back to VERBOSE.")
        # TODO mypy yellin about this
        stdout = sys.stdout
        stderr = sys.stderr

    if len(command) > 0 and not os.path.exists(command[0]):
        path_locator = shutil.which(command[0])
        if path_locator is not None:
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
    # Returns a list of tuples, where each tuple is a list of ANSI codes and a line
    # The list of ANSI codes is the stack of codes that are currently active at the 
    # start of the line
    if text == "":
        return [([], "")]
    # re.match(r"\x1b\[\d+C", line) happens sometimes too but I don't really care
    text = _TELEPORT_RE.sub("\n", text)
    text = text.replace("\033[2J", ANSI_CLEAR_LINE)
    text = text.replace("\033[H", "\r")
    re_matches = _ANSI_ESCAPE.finditer(text)
    matches = [(m.start(), m.end()) for m in re_matches]
    match_i = 0
    lines = []
    ansi_stack: list[str] = []

    current_line = ""
    current_line_length = 0
    for i, c in enumerate(text):
        current_line += c

        while match_i < len(matches) and i >= matches[match_i][1]:
            match_i += 1
        if match_i < len(matches) and i >= matches[match_i][0]:
            continue

        current_line_length += 1
        if c == "\r":
            current_line_length = 0
        elif c == "\n" or current_line_length > columns:
            lines.append((list(ansi_stack), current_line[:-1]))
            current_line = current_line[-1:]
            if current_line == "\n":
                current_line = ""
            current_line_length = min(current_line_length, len(current_line))
    if current_line != "":
        lines.append((list(ansi_stack), current_line))

    return lines


def trim_to_line(text: str, columns: int) -> str:
    re_matches = _ANSI_ESCAPE.finditer(text)
    matches = [(m.start(), m.end()) for m in re_matches]
    match_i = 0
    current_line = ""
    current_line_length = 0
    for i, c in enumerate(text):
        current_line += c

        while match_i < len(matches) and i >= matches[match_i][1]:
            match_i += 1
        if match_i < len(matches) and i >= matches[match_i][0]:
            continue

        current_line_length += 1
        if c == "\r":
            current_line_length = 0
        if c == "\n" or current_line_length > columns:
            current_line = current_line[:-1]
            break
    return current_line + (ANSI_RESET if len(matches) > 0 else "")


def box_content(text: str, columns: int = 0, bold: bool = True) -> str:
    box_side = "┃" if bold else "│"
    start = box_side + " "
    end = " " + box_side

    if columns == 0:
        columns = get_cols()
    columns -= 4

    lines = split_to_display_lines(text, columns)
    escaped_lines = []
    for line in lines:
        ansi_stack, line_text = line
        line_text = "".join(ansi_stack) + line_text
        escaped_lines.append(start + line_text + " " * (columns - non_ansi_len(line_text)) + end)
    return "\n".join(escaped_lines)


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


def box_bottom(bold: bool = True, title: str = "") -> str:
    chars = "└─┘"
    if bold:
        chars = "┗━┛"
    return box_generic(chars, title)


def box_generic(lmr_chars: str, title: str = "") -> str:
    columns = get_cols()
    # Adjust the width for the box borders
    inner_width = columns - 3 - non_ansi_len(title)
    return lmr_chars[:2] + title + lmr_chars[1] * (inner_width) + lmr_chars[2]


def non_ansi_len(text: str) -> int:
    return len(_ANSI_ESCAPE.sub("", text))


def progress_bar(cols: int, fraction: float) -> str:
    fraction = max(min(1, fraction), 0)
    start_chars = int(cols * fraction)
    bar_parts = _UNICODE_BAR_PARTS if Configuration.UNICODE_ENABLED else _C437_BAR_PARTS
    segments = len(bar_parts) - 1
    center_part = int(cols * fraction * segments) % segments
    end_chars = cols - start_chars - 1
    bar_text = bar_parts[-1] * start_chars + bar_parts[center_part] + bar_parts[0] * end_chars
    bar_text = color(bar_text, Configuration.PROGRESS_FOREGROUND_COLOR)
    return color_bg(bar_text, Configuration.PROGRESS_BACKGROUND_COLOR)


def histogram_height(fractions: list[float]) -> str:
    bar_parts = _UNICODE_HEIGHT_PARTS if Configuration.UNICODE_ENABLED else _C437_HEIGHT_PARTS
    output = ""
    segments = len(bar_parts) - 1
    for fraction in fractions:
        part = int(max(min(1, fraction), 0) * (segments - 1))
        if fraction > 0:
            part += 1
        output += bar_parts[part]
    output = color(output, Configuration.PROGRESS_FOREGROUND_COLOR)
    return color_bg(output, Configuration.PROGRESS_BACKGROUND_COLOR)


def _blend_colors(color_low: str, color_high: str, fraction: float) -> str:
    if "38;2" in color_low and "38;2" in color_high:
        color1 = color_low.split(";")
        color2 = color_high.split(";")
        r1, g1, b1 = int(color1[2]), int(color1[3]), int(color1[4])
        r2, g2, b2 = int(color2[2]), int(color2[3]), int(color2[4])
        r = int(r1 + (r2 - r1) * fraction)
        g = int(g1 + (g2 - g1) * fraction)
        b = int(b1 + (b2 - b1) * fraction)
        return f"38;2;{r};{g};{b}"
    return color_low


def histogram_color(fractions: list[float]) -> str:
    output = ""
    colors = Configuration.HEATMAP_COLORS
    if len(colors) < 2:
        colors = ["white" if len(colors) == 0 else colors[0]] * 2
    use_first = "38;2" in colors[0] and "38;2" in colors[1]
    for fraction in fractions:
        color_index = max(min(1, fraction), 0) * (len(colors) - 1 - (0 if use_first else 1))
        if fraction > 0 and not use_first:
            color_index += 1
        blend_index = min(len(colors) - 1, int(color_index) + 1)
        bucket_color = _blend_colors(
            colors[int(color_index)], 
            colors[blend_index], 
            color_index % 1
        )
        output += color(_C437_HEIGHT_PARTS[-1], bucket_color)
    return output


def move_rows_up(n: int) -> str:
    return f"\033[{n}A"


def color(text: str, color_str: str) -> str:
    if text == "":
        return text
    if len(color_str) > 1 and color_str[-1].isdigit():
        pass # ANSI code, pass directly in
    elif color_str in _ANSI_COLOR_MAP:
        color_str = _ANSI_COLOR_MAP[color_str]
    else:
        print(f"Recoverable internal error: unknown color {color_str} requested.")
        return text
    return _color_by_code(text, color_str)


def color_bg(text: str, color_str: str) -> str:
    if text == "":
        return text
    if len(color_str) > 1 and color_str[-1].isdigit():
        pass # ANSI code, pass directly in
    elif color_str in _ANSI_BG_COLOR_MAP:
        color = _ANSI_BG_COLOR_MAP[color_str]
    else:
        print(f"Recoverable internal error: unknown color {color_str} requested.")
        return text
    return _color_by_code(text, color_str)


def _color_by_code(text: str, color_code: str) -> str:
    if not Configuration.COLOR_ENABLED:
        return text
    return f"\033[{color_code}m{text}" + ANSI_RESET


def color_bad(text: str) -> str:
    return color(text, Configuration.ERROR_COLOR)


def color_good(text: str) -> str:
    return color(text, Configuration.GOOD_COLOR)


def color_ref(text: str) -> str:
    return color(text, Configuration.REFERENCE_COLOR)


def color_key(text: str) -> str:
    return color(text, Configuration.IMPORTANT_COLOR)


def warn(text: str) -> str:
    return color("[WARNING]", Configuration.WARNING_COLOR) + " " + text


def error(text: str) -> str:
    return color("[ERROR]", Configuration.ERROR_COLOR) + " " + text