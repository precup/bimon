#!/usr/bin/env python

import os
import sys

from src import git
from src import parsers
from src import storage
from src import terminal
from src.config import Configuration


def main() -> None:
    original_wd = os.getcwd()
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    storage.init_storage()
    terminal.init_terminal()
    
    parser = parsers.get_bimon_parser()
    clean_args = parsers.preparse_bimon_command(sys.argv[1:])
    if len(clean_args) == 0:
        if len(sys.argv) == 1:
            clean_args = ["--help"]
        else:
            print(f"Unrecognized command {sys.argv[1]}. Use --help for help.")
            return
        
    args = parser.parse_args(clean_args)
    _setup_configuration(args, original_wd)
    _ensure_workspace(args)
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


def _ensure_workspace(args) -> None:
    workspace = Configuration.WORKSPACE_PATH
    if not os.path.exists(workspace):
        print(f"BiMon requires a Godot workspace at path \"{workspace}\".")
        should_clone = False
        if hasattr(args, "y") and args.y:
            print("Cloning one there now...")
            should_clone = True
        else:
            response = input("Clone one there now? [Y/n]: ")
            should_clone = not response.strip().lower().startswith("n")

        if should_clone:
            git.clone("https://github.com/godotengine/godot.git", workspace)
        else:
            print("BiMon requires a Godot workspace to function. Exiting.")
            sys.exit(1)


if __name__ == "__main__":
    main()
