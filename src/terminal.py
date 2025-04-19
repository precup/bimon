import signal
from src.config import Configuration, PrintMode
import src.signal_handler as signal_handler
import sys
import subprocess
import os
import re
import readline
import atexit
from typing import Optional
import queue
import time
import threading
import shutil
if os.name != 'nt':
    from ptyprocess import PtyProcessUnicode

HISTORY_FILE = "history"
BAR_EIGHTHS = " ▏▎▍▌▋▊▉█"
HEIGHT_EIGHTHS = " ▁▂▃▄▅▆▇█"
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
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
ANSI_RESET = "\033[0m"

def init_terminal() -> None:
    if sys.stdin.isatty():
        try:
            readline.read_history_file(HISTORY_FILE)
        except FileNotFoundError:
            pass
        atexit.register(readline.write_history_file, HISTORY_FILE)
        readline.set_auto_history(True)


def get_cols() -> int:
    if sys.stdin.isatty():
        return os.get_terminal_size().columns
    else:
        return 80


def execute_in_subwindow(command: list[str], title: str, rows: int, cwd: Optional[str] = None, eat_kill: bool = False) -> bool:
    if cwd == "":
        cwd = None

    def enqueue_output(pipe, output_queue):
        for line in iter(pipe.readline, ''):
            output_queue.put(line)
        pipe.close()

    if Configuration.PRINT_MODE == PrintMode.LIVE:
        to_close = []
        if os.name == 'nt':
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
            )
            stdout_queue = queue.Queue()
            stderr_queue = queue.Queue()

            stdout_thread = threading.Thread(target=enqueue_output, args=(process.stdout, stdout_queue))
            stderr_thread = threading.Thread(target=enqueue_output, args=(process.stderr, stderr_queue))

            stdout_thread.start()
            stderr_thread.start()
            output_lines = []
            stderr_lines = []
            lines_printed = 0
            while True:
                cols = get_cols()
                try:
                    stdout_line = stdout_queue.get_nowait()
                    print(stdout_line)
                    # TODO handle ANSI escape sequences
                    output_lines += [line for _, line in split_to_display_lines(stdout_line, cols)]
                except queue.Empty:
                    pass

                try:
                    stderr_line = stderr_queue.get_nowait()
                    output_lines += [line for _, line in split_to_display_lines(stderr_line, cols)]
                    stderr_lines += [line for _, line in split_to_display_lines(stderr_line, cols)]
                except queue.Empty:
                    pass

                if process.poll() is not None and stdout_queue.empty() and stderr_queue.empty():
                    break

                if lines_printed < rows:
                    while lines_printed < rows and lines_printed < len(output_lines):
                        # print(output_lines[lines_printed])
                        lines_printed += 1
                else:
                    move_rows_up(rows)
                    output = "\n".join("\033[2K" + output_lines[-rows + i] for i in range(rows))
                    # print(output)
                # print([repr(line) for line in output_lines])

                time.sleep(0.05)

            stdout_thread.join()
            stderr_thread.join()
            process.wait()
            if len(stderr_lines) > 0:
                print("Execution errors:")
                print("\n".join(stderr_lines))
            for fd in to_close:
                os.close(fd)
            return process.returncode == 0
        
        else:
            process = PtyProcessUnicode.spawn(command, cwd=cwd)
            cols = get_cols()
            # process.setwinsize(rows, cols)
            output_lines = [""]
            lines_printed = 0
            top = box_top(bold=False, title=title)
            if eat_kill and signal_handler.SHOULD_EXIT:
                signal_handler.SHOULD_EXIT = False
                process.kill(signal.SIGINT)
            should_exit = False
            bottom = box_bottom(bold=False, title=signal_handler.MESSAGE if should_exit else "")
            rows -= 2

            while process.isalive():
                try:
                    # Read output from the process
                    stdout_chunk = process.read(1024)
                    if stdout_chunk:
                        lines = stdout_chunk.split('\n')
                        output_lines[-1] += lines[0]
                        output_lines += lines[1:]
                except EOFError:
                    break
                
                window_lines = []
                for line in output_lines[::-1]:
                    split_lines = split_to_display_lines(line, cols)
                    if len(split_lines) > rows - len(window_lines):
                        if rows > len(window_lines):
                            window_lines = [split_lines[:rows - len(window_lines)]] + window_lines
                        break
                    window_lines = split_lines + window_lines
                while len(window_lines) < rows:
                    window_lines.append(([], ""))
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
                output = ANSI_RESET + top + "\n" + output + ANSI_RESET + "\n" + bottom
                print(output)
            process.wait()
            if process.exitstatus != 0:
                print("Dumping full process log because an error occurred:")
                print("\n".join(output_lines))
            return process.exitstatus == 0
    else:
        if Configuration.PRINT_MODE == PrintMode.ERROR_ONLY:
          stdout = subprocess.DEVNULL
          stderr = sys.stderr
        elif Configuration.PRINT_MODE == PrintMode.QUIET:
          stdout = subprocess.DEVNULL
          stderr = subprocess.DEVNULL
        elif Configuration.PRINT_MODE == PrintMode.VERBOSE:
          stdout = sys.stdout
          stderr = sys.stderr
        else:
          print(f"Unrecoverable internal error: unknown print mode {Configuration.PRINT_MODE}.")
          sys.exit(1)
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
    re_matches = ANSI_ESCAPE.finditer(text)
    matches = [(m.start(), m.end()) for m in re_matches]
    lines = []
    current_line = ""
    current_line_length = 0
    ansi_stack = []
    for i in range(len(text)):
        current_line += text[i]
        if any(i >= match[0] and i < match[1] for match in matches):
            continue
        current_line_length += 1
        if text[i] == '\r':
            current_line_length = 0
        if text[i] == "\n" or current_line_length > columns:
            lines.append((list(ansi_stack), current_line[:-1]))
            current_line = current_line[-1:]
            if current_line == "\n":
                current_line = ""
    if current_line != "":
        lines.append((list(ansi_stack), current_line))
    return lines


def trim_str(text: str, columns: int) -> str:
    re_matches = ANSI_ESCAPE.finditer(text)
    matches = [(m.start(), m.end()) for m in re_matches]
    current_line = ""
    current_line_length = 0
    for i in range(len(text)):
        current_line += text[i]
        if any(i >= match[0] and i < match[1] for match in matches):
            continue
        current_line_length += 1
        if text[i] == '\r':
            current_line_length = 0
        if text[i] == "\n" or current_line_length > columns:
            current_line = current_line[:-1]
            break
    return current_line + (ANSI_RESET if len(matches) > 0 else "")



def print_fit_line(text: str, columns: int = 0, start: str = "", end: str = "") -> None:
    if columns == 0:
        columns = get_cols()
    if len(start) > 0:
        start += " "
    if len(end) > 0:
        end = " " + end
    columns -= escape_len(start) + escape_len(end)
    lines = split_to_display_lines(text, columns)
    for line in lines:
        ansi_stack, line_text = line
        line_text = "".join(ansi_stack) + line_text
        line_text = start + line_text + " " * (columns - escape_len(line_text)) + end
        print(line_text)


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


def print_progress_bar(cols: int, fraction: float) -> None:
    fraction = max(min(1, fraction), 0)
    start_chars = int(cols * fraction)
    center_eighths = int(cols * fraction * 8) % 8
    end_chars = cols - start_chars - 1
    bar_text = BAR_EIGHTHS[8] * start_chars + BAR_EIGHTHS[center_eighths] + BAR_EIGHTHS[0] * end_chars
    bar_text = color(bar_text, Configuration.PROGRESS_FOREGROUND_COLOR)
    print(color_bg(bar_text, Configuration.PROGRESS_BACKGROUND_COLOR), end="")


def print_histogram_height(fractions: list[float]) -> None:
    output = ""
    for i, fraction in enumerate(fractions):
        eighths = int(max(min(1, fraction), 0) * 7)
        if fraction > 0:
            eighths += 1
        output += HEIGHT_EIGHTHS[eighths]
    output = color(output, Configuration.PROGRESS_FOREGROUND_COLOR)
    print(color_bg(output, Configuration.PROGRESS_BACKGROUND_COLOR), end="")


def print_histogram_color(fractions: list[float]) -> None:
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
    print(output, end="")


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
    return color(text, "bold red")


def color_good(text: str, color_enabled: bool = True) -> str:
    return color(text, "bold green")


def color_rev(text: str, color_enabled: bool = True) -> str:
    return color(text, Configuration.COMMIT_COLOR)


def color_key(text: str, color_enabled: bool = True) -> str:
    return color(text, Configuration.IMPORTANT_COLOR)


def color_code(text: str, color_enabled: bool = True) -> str:
    return color(text, "bold blue")