from config import Configuration, PrintMode
import sys
import subprocess
import os
import re


def execute_in_subwindow(command: list[str], wait: bool, title: str, rows: int) -> None:
    if Configuration.print_mode == PrintMode.LIVE:
        pass
    else:
        if Configuration.print_mode == PrintMode.ERROR_ONLY:
          stdout = subprocess.DEVNULL
          stderr = sys.stderr
        elif Configuration.print_mode == PrintMode.QUIET:
          stdout = subprocess.DEVNULL
          stderr = subprocess.DEVNULL
        elif Configuration.print_mode == PrintMode.VERBOSE:
          stdout = sys.stdout
          stderr = sys.stderr
        else:
          print(f"Unrecoverable internal error: unknown print mode {Configuration.print_mode}.")
          sys.exit(1)
        process = subprocess.Popen(
            command,
            stdout=stdout,
            stderr=stderr,
            text=True
        )
        if wait:
            process.wait()


def split_to_display_lines(text: str, columns: int) -> list[tuple[list[str], str]]:
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    re_matches = ansi_escape.findall(text)
    matches = [(m.start(), m.end()) for m in re_matches]
    lines = []
    current_line = ""
    current_line_length = 0
    ansi_stack = []
    for i in range(len(text)):
        if i < len(matches) and matches[i][0] == i:
            # Skip the ANSI escape sequence
            continue
        current_line += text[i]
        current_line_length += 1
        if text[i] == '\r':
            current_line_length = 0
        if text[i] == "\n" or len(current_line) > columns:
            lines.append((list(ansi_stack)), current_line[:-1]))
            current_line = current_line[-1:]



def print_box_top(bold: bool = True, title: str = "") -> None:
    columns = os.get_terminal_size().columns

    # Adjust the width for the box borders
    inner_width = columns - 3 - len(title)
    chars = "┌─┐"
    if bold:
        chars = "┏━┓"

    print(chars[:2] + title + chars[1] * (inner_width) + chars[2])


def print_box_bottom(bold: bool = True) -> None:
    columns = os.get_terminal_size().columns

    # Adjust the width for the box borders
    inner_width = columns - 2
    chars = "└─┘"
    if bold:
        chars = "┗━┛"

    print(chars[0] + chars[1] * (inner_width) + chars[2])


def move_rows_up(n: int) -> None:
    if Configuration.print_mode == PrintMode.LIVE:
        print(f"\033[{n}A", end="")
        sys.stdout.flush()


def move_rows_down(n: int) -> None:
    if Configuration.print_mode == PrintMode.LIVE:
        print(f"\033[{n}B", end="")
        sys.stdout.flush()


def move_cursor_to(x: int, y: int) -> None:
    if Configuration.print_mode == PrintMode.LIVE:
        print(f"\033[{y};{x}H", end="")
        sys.stdout.flush()


def move_to_column(n: int) -> None:
    if Configuration.print_mode == PrintMode.LIVE:
        print(f"\033[{n}G", end="")
        sys.stdout.flush()


def clear_line() -> None:
    if Configuration.print_mode == PrintMode.LIVE:
        print("\033[2K", end="")
        sys.stdout.flush()


def color(text: str, color: str) -> str:
    color_map = {
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
    if len(color) > 1 and color[-1].isdigit():
        color = color
    elif color in color_map:
        color = color_map[color]
    else:
        print(f"Recoverable internal error: unknown color {color} requested.")
        return text
    return color_by_code(text, color_map[color])


def color_by_code(text: str, color_code: str) -> str:
    if not Configuration.COLOR_ENABLED:
        return text
    return f"\033[{color_code}m{text}\033[0m"


def color_bad(text: str, color_enabled: bool = True) -> str:
    return color(text, "bold red")


def color_rev(text: str, color_enabled: bool = True) -> str:
    return color(text, "dark yellow")


def color_code(text: str, color_enabled: bool = True) -> str:
    return color(text, "bold blue")