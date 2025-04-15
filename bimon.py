#!/usr/bin/env python

import argparse
import configparser
import os
import shutil
import subprocess
import sys
import time

from typing import Optional

import src.signal_handler as signal_handler
import src.storage as storage
import src.git as git
import src.factory as factory

from src.config import Configuration, PrintMode

############################################################
#                      Initialization                      #
############################################################


def main() -> None:
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    storage.init_storage()
    signal_handler.install()

    if len(sys.argv) < 2:
        print_short_help()
        sys.exit(1)
    
    process_command_and_arguments()


def global_args(args):
    Configuration.load_from(args.config if args.config else "")
    Configuration.FORCE = args.force
    if args.color is None:
        args.color = sys.stdout.isatty() and os.getenv("TERM") != "dumb"
    Configuration.COLOR = args.color
    if args.print_mode is not None:
        Configuration.PRINT_MODE = args.print_mode


def process_command_and_arguments() -> None:
    parser = argparse.ArgumentParser(description="BiMon: A tool for speeding up bisecting during bug triage for the Godot engine bugsquad.")
    parser.add_argument("-q", "--quiet", action="store_const", const=PrintMode.QUIET, dest="print_mode", help="Quiet mode hides output from long-running subprocesses.")
    parser.add_argument("-v", "--verbose", action="store_const", const=PrintMode.VERBOSE, dest="print_mode", help="Verbose mode prints output from subprocesses.")
    parser.add_argument("-l", "--live", action="store_const", const=PrintMode.LIVE, dest="print_mode", help="Live mode shows a live updating display of subprocess output.")
    parser.add_argument("-f", "--force", action="store_true", help="Discard uncommitted changes in the workspace directory if they're in the way.")
    parser.add_argument("--color", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Enable or disable colored output (default: auto-detect).")
    parser.add_argument("--config", type=str, help="Path to the configuration file. Defaults to config.ini, falls back to default_{platform}_config.ini.")
    parser.set_defaults(print_mode=PrintMode.LIVE if sys.stdout.isatty() else PrintMode.VERBOSE)

    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # Porcelain commands
    update_parser = subparsers.add_parser("update", help="Fetch, compile, and cache missing commits.")
    update_parser.add_argument("-n", type=int, help="Only compile and cache 1 in every N commits.")
    update_parser.add_argument("cut_rev", nargs="?", help="The revision to start working back from.")
    update_parser.set_defaults(func=lambda args: update_command(args.force, args.n, args.cut_rev))

    bisect_parser = subparsers.add_parser("bisect", help="Bisect history to find a regression's commit via an interactive mode.")
    bisect_parser.add_argument("-d", "--discard", action="store_true", help="Prevent caching binaries compiled during a bisect.")
    bisect_parser.add_argument("-c", "--cached-only", action="store_true", help="Prevent compiling at all during a bisect.")
    bisect_parser.add_argument("--ignore-date", action="store_true", help="Don't use the issue open date to cut down the commit range.")
    bisect_parser.add_argument("--path-spec", type=str, default=None, help="Limit the bisect to commits with specific files. See git bisect's path_spec behavior for full details.")
    bisect_parser.add_argument("project", nargs="?", help="Path to the project or project.godot file.")
    bisect_parser.set_defaults(func=lambda args: bisect_command(args.discard, args.cache_only, args.ignore_date, args.path_spec, args.project))

    extract_parser = subparsers.add_parser("extract", help="Extract the binary for a specific revision.")
    extract_parser.add_argument("rev", help="The revision name to extract.")
    extract_parser.add_argument("file_path", help="The file path to extract the binary to.")
    extract_parser.set_defaults(func=lambda args: extract_command(args.rev, args.file_path))

    purge_parser = subparsers.add_parser("purge", help="Delete uncompressed binaries that are also present in compressed form.")
    purge_parser.set_defaults(func=lambda args: purge_command())

    help_parser = subparsers.add_parser("help", help="Show help information.")
    help_parser.set_defaults(func=lambda args: help_command())

    # Plumbing commands
    fetch_parser = subparsers.add_parser("fetch", help="Fetch the latest commits and update the processing lists.")
    fetch_parser.set_defaults(func=lambda args: fetch_command())

    compile_parser = subparsers.add_parser("compile", help="Compile and store specific revisions.")
    compile_parser.add_argument("revs", nargs="*", default="HEAD", help="The revisions to compile. Defaults to the workspace HEAD if none are provided.")
    compile_parser.set_defaults(func=lambda args: compile_command(args.revs, args.force))

    compress_parser = subparsers.add_parser("compress", help="Pack completed bundles.")
    compress_parser.add_argument("-n", type=int, help="Allow gaps of size N - 1 while bundling. Useful for 1 in N updates.")
    compress_parser.add_argument("-a", "--all", action="store_true", help="Bundle and compress all loose files, regardless of whether they're optimally similar.")
    compress_parser.set_defaults(func=lambda args: compress_command(args.n, args.all))

    args = parser.parse_args()
    global_args(args)

    workspace = Configuration.WORKSPACE_PATH
    if not os.path.exists(workspace):
        print(f"BiMon requires a Godot workspace at path '{workspace}'.")
        clone_response = input(f"Clone one there now? (y/n): ").strip().lower()
        if clone_response.startswith('y'):
            git.clone("https://github.com/godotengine/godot.git", workspace)
        else:
            print("BiMon requires a Godot workspace to function. Exiting.")
            sys.exit(1)

    args.func(args)


############################################################
#                         Commands                         #
############################################################


def update_command(force: bool, n: Optional[int], cut_rev: Optional[str]) -> None:
    update(force, n if n is not None else 1, cut_rev)


def update(force: bool, n: int, cut_commit: str) -> None:
    fetch(n)
    rev_list = storage.read_rev_list()
    missing_commits = list(get_missing_commits(n)[::-1])
    if cut_commit is not None:
        cut_commit = git.resolve_ref(cut_commit.strip())
        if cut_commit == "":
            print(f"Invalid cut commit {cut_commit}.")
            sys.exit(1)

        cut = rev_list.index(cut_commit)
        while cut > 0 and rev_list[cut] not in missing_commits:
            cut -= 1
        if cut < 0:
            print("Unknown cut commit.")
            sys.exit(1)

        cut = missing_commits.index(rev_list[cut])
        missing_commits = missing_commits[cut:] + missing_commits[:cut]

    factory.compile(missing_commits, should_compress=True, n=n, force=force)


def bisect_command(discard: bool, cache_only: bool, ignore_date: bool, path_spec: Optional[str], project: Optional[str]) -> None:
    bisect(discard, cache_only, ignore_date, project, Configuration.DEFAULT_EXECUTION_PARAMETERS, path_spec)


def extract_command(rev: str, file_path: str) -> None:
    storage.extract_commit(rev, file_path)


def purge_command() -> None:
    purge_count = storage.purge_duplicate_files({git.resolve_ref(Configuration.START_COMMIT)})
    if os.path.exists(bisect.TMP_DIR):
        purge_count += len(os.listdir(bisect.TMP_DIR))
        shutil.rmtree(bisect.TMP_DIR)
    print(f"Purged {purge_count} files.")


def fetch_command() -> None:
    fetch()


def fetch(n: int = 1) -> None:
    print("Fetching...")
    git.fetch()
    rev_list = git.query_rev_list(Configuration.START_COMMIT, "origin/master")
    storage.write_rev_list(rev_list)

    print(f"{len(get_missing_commits(n))} commits are waiting to be compiled.")


def compile_command(revs: list[str], force: Optional[bool]) -> None:
    if len(revs) == 0:
        revs.append("HEAD")
    commits = [git.resolve_ref(rev) for rev in revs]
    factory.compile(commits, should_compress=True, force=force if force is not None else False)


def compress_command(n: Optional[int], force: Optional[bool]) -> None:
    factory.compress(n if n is not None else 1, force if force is not None else False)


def help_command() -> None:
    # TODO
    print("""Usage: bimon.py [-q/--quiet] [-v/--verbose] [-l/--live] COMMAND [COMMAND_ARG...]""")
    
def print_short_help() -> None:
    # TODO
    print("""Usage: bimon.py [-q/--quiet] [-v/--verbose] [-l/--live] COMMAND [COMMAND_ARG...]""")
    


###########################################################
#                        Utilities                        #
###########################################################


def get_missing_commits(n: int) -> list[str]:
    rev_list = storage.read_rev_list()
    present_commits = set(storage.get_present_commits())
    missing_commits = []
    sequential_missing = 0
    for i in range(len(rev_list)):
        if rev_list[i] in present_commits:
            sequential_missing = 0
        else:
            sequential_missing += 1
        if sequential_missing >= n:
            missing_commits.append(rev_list[i])
            sequential_missing = 0
    return missing_commits


###########################################################
#                                                         #
###########################################################


if __name__ == "__main__":
    main()