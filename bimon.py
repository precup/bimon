#!/usr/bin/env python

import os
import sys

from src import git
from src import parsers
from src import release_processor
from src import storage
from src import terminal
from src.config import Configuration


def main() -> None:
    if sys.version_info < (3, 12):
        print(terminal.error("BiMon requires Python 3.12 or higher. The future is now."))
        sys.exit(1)

    original_wd = os.getcwd()
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    storage.init_storage()
    terminal.init_terminal()

    parser = parsers.get_bimon_parser(sys.argv[1] if len(sys.argv) > 1 else None)
    clean_args = parsers.preparse_bimon_command(sys.argv[1:])
    if len(clean_args) == 0:
        if len(sys.argv) == 1:
            clean_args = ["--help"]
        else:
            print(terminal.error(f"Unrecognized command {sys.argv[1]}. Use --help for help."))
            return

    args = parser.parse_args(clean_args)
    _setup_configuration(args, original_wd)

    update_tags = _ensure_workspace(
        args,
        Configuration.WORKSPACE_PATH,
        "https://github.com/godotengine/godot.git")
    if Configuration.SECONDARY_WORKSPACE_PATH != "":
        update_tags = _ensure_workspace(
            args,
            Configuration.SECONDARY_WORKSPACE_PATH,
            "https://github.com/godotengine/godot-builds.git") or update_tags
    release_processor.add_any_new_release_tags(force=update_tags)

    args.func(args)


def _setup_configuration(args, original_wd: str) -> None:
    config_path = ""
    if args.config is not None:
        config_path = storage.resolve_relative_to(args.config, original_wd)
    Configuration.load_from(config_path)

    if args.ignore_old_errors is not None:
        Configuration.IGNORE_OLD_ERRORS = args.ignore_old_errors
    if args.color is None:
        args.color = sys.stdout.isatty() and os.getenv("TERM") != "dumb"
    Configuration.COLOR_ENABLED = args.color
    if args.print_mode is not None:
        Configuration.PRINT_MODE = args.print_mode


def _ensure_workspace(args, workspace: str, git_address: str) -> bool:
    if not os.path.exists(workspace):
        print(f"BiMon requires a godot workspace at path \"{workspace}\".")
        should_clone = False
        if hasattr(args, "y") and args.y:
            print("Cloning one there now...")
            should_clone = True
        else:
            default_option = terminal.color_key("Y")
            response = input(f"Clone one there now? [{default_option}/n]: ")
            should_clone = not response.strip().lower().startswith("n")

        if should_clone:
            git.clone(git_address, workspace)
            return True
        else:
            print(terminal.error("BiMon requires a Godot workspace to function. Exiting."))
            sys.exit(1)
    return False


if __name__ == "__main__":
    main()
