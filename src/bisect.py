from typing import Optional
from config import Configuration

_should_pause = False
_is_good = False
_is_bad = False

def _mark_pause():
    _should_pause = True


def _mark_good():
    # TODO close project if open
    _is_good = True


def _mark_bad():
    # TODO close project if open
    _is_bad = True


def print_exit_message(last_good_revision: Optional[str], last_bad_revision: Optional[str]) -> None:
    print("\nExiting bisect interactive mode.")
    if last_good_revision or last_bad_revision:
        print("Resume with " + (f"g {last_good_revision}" if last_good_revision else "") + \
               (f" b {last_bad_revision}" if last_bad_revision else ""))


def bisect(discard: bool, cache_only: bool, project: Optional[str], verbose: bool = False) -> None:
    if Configuration.ENABLE_SHORTCUTS:
        import keyboard
        keyboard.add_hotkey(Configuration.MARK_GOOD_HOTKEY, _mark_good)
        keyboard.add_hotkey(Configuration.MARK_BAD_HOTKEY, _mark_bad)
        keyboard.add_hotkey(Configuration.EXIT_AFTER_THIS_HOTKEY, _mark_pause)
    print("Entering bisect interactive mode. Type 'help' for a list of commands.")
    started = False
    current_revision = None
    last_good_revision = None
    last_bad_revision = None

    while True:
        try:
            command = input("bisect> ").strip()
            if not command:
                continue

            parts = command.split()
            cmd = parts[0].lower()
            args = parts[1:]

            # used letters: beghlqrstv
            # using s twice is fine because they're never both valid at the same time
            if "start".startswith(cmd) and (len(cmd) > 1 or not started):
                started = True
            elif "good".startswith(cmd) or "bad".startswith(cmd) or "skip".startswith(cmd):
                pass
            elif "try".startswith(cmd):
                pass
            elif "retry".startswith(cmd):
                pass
            elif "list".startswith(cmd):
                pass
            elif "visualize".startswith(cmd):
                pass
            elif "help".startswith(cmd):
                pass
            elif "exit".startswith(cmd) or "quit".startswith(cmd):
                print_exit_message()
                break
            else:
                print(f"Unknown command: {cmd}. Type 'help' for a list of commands.")
        except KeyboardInterrupt:
            print_exit_message()
            break


def launch_commit(commit, project):
    if os.path.exists("godot"):
        os.remove("godot")
        
    with py7zr.SevenZipFile(f"versions/{commit}.7z", mode='r') as archive:
        archive.extract(targets=["godot"])
    os.system(f"./godot {project}")