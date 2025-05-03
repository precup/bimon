#!/usr/bin/env python

import argparse
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
    args = parser.parse_args()

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

    workspace = Configuration.WORKSPACE_PATH
    if not os.path.exists(workspace):
        print(f"BiMon requires a Godot workspace at path \"{workspace}\".")
        should_clone = False
        if hasattr(args, "y") and args.y:
            print("Cloning one there now...")
            should_clone = True
        else:
            response = input("Clone one there now? [y/N]: ")
            should_clone = response.strip().lower().startswith("y")

        if should_clone:
            git.clone("https://github.com/godotengine/godot.git", workspace)
        else:
            print("BiMon requires a Godot workspace to function. Exiting.")
            sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
