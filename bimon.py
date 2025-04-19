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
import src.terminal as terminal

from src.bisect import bisect, launch_any
from src.config import Configuration, PrintMode

############################################################
#                      Initialization                      #
############################################################


def main() -> None:
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    storage.init_storage()
    terminal.init_terminal()
    signal_handler.install()

    if len(sys.argv) < 2:
        print_short_help()
        sys.exit(1)
    
    process_command_and_arguments()


def global_args(args):
    Configuration.load_from(args.config if args.config else "")
    if args.ignore_old_errors is not None:
        Configuration.IGNORE_OLD_ERRORS = args.ignore_old_errors
    if args.force is not None:
        Configuration.FORCE = args.force
    if args.color is None:
        args.color = sys.stdout.isatty() and os.getenv("TERM") != "dumb"
    if args.color is not None:
        Configuration.COLOR_ENABLED = args.color
    if args.print_mode is not None:
        Configuration.PRINT_MODE = args.print_mode


def process_command_and_arguments() -> None:
    platform = "mac" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"
    parser = argparse.ArgumentParser(description="BiMon: A tool for speeding up bisecting during bug triage for the Godot engine bugsquad.")
    parser.add_argument("-q", "--quiet", action="store_const", const=PrintMode.QUIET, dest="print_mode", help="Quiet mode hides output from long-running subprocesses.")
    parser.add_argument("-v", "--verbose", action="store_const", const=PrintMode.VERBOSE, dest="print_mode", help="Verbose mode prints output from subprocesses.")
    parser.add_argument("-l", "--live", action="store_const", const=PrintMode.LIVE, dest="print_mode", help="Live mode shows a live updating display of subprocess output.")
    parser.add_argument("-f", "--force", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Discard uncommitted changes in the workspace directory if they're in the way.")
    parser.add_argument("--color", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Enable or disable colored output (default: auto-detect).")
    parser.add_argument("-c", "--config", type=str, help="Path to the configuration file. Defaults to config.ini, falls back to default_{platform}_config.ini.")
    parser.add_argument("-i", "--ignore-old-errors", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Path to the configuration file. Defaults to config.ini, falls back to default_{platform}_config.ini.")
    parser.set_defaults(print_mode=PrintMode.LIVE if sys.stdout.isatty() else PrintMode.VERBOSE)

    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    init_parser = subparsers.add_parser("init", help="Initializes the workspace and sets up python deps.")
    init_parser.set_defaults(func=lambda args: init_command())

    # Porcelain commands
    update_parser = subparsers.add_parser("update", help="Fetch, compile, and cache missing commits.")
    update_parser.add_argument("-n", type=int, help="Only compile and cache 1 in every N commits.")
    update_parser.add_argument("cut_rev", nargs="?", help="The revision to start working back from.")
    update_parser.set_defaults(func=lambda args: update_command(args.n, args.cut_rev))

    repro_parser = subparsers.add_parser("repro", help="Reproduce an issue using the specified parameters.")
    repro_parser.add_argument("-d", "--discard", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Prevent caching binaries compiled during a bisect.")
    repro_parser.add_argument("-c", "--cached-only", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Prevent compiling at all during a bisect.")
    repro_parser.add_argument("-e", "--execution-parameters", type=str, help="The parameters to pass to the Godot executable. See the config.ini comments for details.")
    repro_parser.add_argument("--project", type=str, help="The project name or path to use as an MRP.")
    repro_parser.add_argument("--issue", type=str, help="The issue number or URL to reproduce. Used to download an MRP.")
    repro_parser.add_argument("project_or_issue", nargs="?", help="Accepts the same things as --project or --issue. Autodetects which it is.")
    repro_parser.set_defaults(func=lambda args: repro_command(args.execution_parameters, args.issue, args.project, args.project_or_issue))

    bisect_parser = subparsers.add_parser("bisect", help="Bisect history to find a regression's commit via an interactive mode.")
    bisect_parser.add_argument("-d", "--discard", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Prevent caching binaries compiled during a bisect.")
    bisect_parser.add_argument("-c", "--cached-only", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Prevent compiling at all during a bisect.")
    bisect_parser.add_argument("-e", "--execution-parameters", type=str, help="The parameters to pass to the Godot executable. See the config.ini comments for details.")
    bisect_parser.add_argument("--ignore-date", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Don't use the issue open date to cut down the commit range.")
    bisect_parser.add_argument("--path-spec", type=str, default=None, help="Limit the bisect to commits with specific files. See git bisect's path_spec behavior for full details.")
    bisect_parser.add_argument("--project", type=str, default=None, help="The project to use for testing. Can be a directory, project.godot, or zip file.")
    bisect_parser.add_argument("--issue", type=str, default=None, help="The issue to test. Can be a link or issue number. Used to autodownload MRPs and restrict the commit range by date.")
    bisect_parser.add_argument("project_or_issue", nargs="*", help="Accepts the same things as --project and --issue, autodetecting which one each is.")
    bisect_parser.set_defaults(func=
        lambda args: bisect_command(args.execution_parameters, args.discard, args.cached_only, args.ignore_date, args.path_spec, args.project, args.issue, args.project_or_issue))

    extract_parser = subparsers.add_parser("extract", help="Extract the binary for a specific revision.")
    extract_parser.add_argument("rev", help="The revision name to extract.")
    extract_parser.add_argument("file_path", help="The file path to extract the binary to.")
    extract_parser.set_defaults(func=lambda args: extract_command(args.rev, args.file_path))

    purge_parser = subparsers.add_parser("purge", help="Delete uncompressed binaries that are also present in compressed form.")
    compress_parser.add_argument("-m", "--mrps", nargs="?", const=True, default=True, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Bundle and compress all loose files, regardless of whether they're optimally similar.")
    compress_parser.add_argument("-d", "--duplicates", nargs="?", const=True, default=True, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Bundle and compress all loose files, regardless of whether they're optimally similar.")
    purge_parser.set_defaults(func=lambda args: purge_command(args.mrps, args.duplicates))

    help_parser = subparsers.add_parser("help", help="Show help information.")
    help_parser.set_defaults(func=lambda args: help_command())

    # Plumbing commands
    fetch_parser = subparsers.add_parser("fetch", help="Fetch the latest commits and update the processing lists.")
    fetch_parser.set_defaults(func=lambda args: fetch_command())

    compile_parser = subparsers.add_parser("compile", help="Compile and store specific revisions.")
    compile_parser.add_argument("revs", nargs="*", default="HEAD", help="The revisions to compile. Defaults to the workspace HEAD if none are provided.")
    compile_parser.set_defaults(func=lambda args: compile_command(args.revs))

    compress_parser = subparsers.add_parser("compress", help="Pack completed bundles.")
    compress_parser.add_argument("-n", type=int, help="Allow gaps of size N - 1 while bundling. Useful for 1 in N updates.")
    compress_parser.add_argument("-a", "--all", nargs="?", const=True, default=None, type=lambda x: x.lower() in ("true", "1", "yes", ""), help="Bundle and compress all loose files, regardless of whether they're optimally similar.")
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


def init_command() -> None:
    print("You're good to go.")


def update_command(n: Optional[int], cut_rev: Optional[str]) -> None:
    signal_handler.SHOULD_INSTADIE = False
    update(n if n is not None else 1, cut_rev)


def update(n: int, cut_commit: str) -> None:
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

    if not factory.compile(missing_commits, should_compress=True, n=n, retry_compress=True):
        sys.exit(1)


def repro_command(execution_parameters: Optional[str], discard: bool, cached_only: bool, issue: Optional[str], project: Optional[str], commit: Optional[str], project_or_issue_or_commit: list[str]) -> None:
    _, commit, execution_parameters, project = bisect.determine_execution_parameters(
        project, issue, commit, project_or_issue_or_commit, execution_parameters)
    cached_commits = storage.get_present_commits()
    if commit is None:
        rev_list = [rev for rev in storage.read_rev_list() if rev in cached_commits]
        if len(rev_list) > 0:
            commit = rev_list[-1]
        elif cached_only:
            print("No cached commits found to repro with.")
            print("Try running without --cached-only or running an update or compile.")
            sys.exit(1)
        else:
            commit = Configuration.TRACKED_BRANCH
    else:
        commit = git.resolve_ref(commit.strip())
        if commit not in cached_commits and cached_only:
            print(f"Commit {commit} is not cached.")
            print("Try running without --cached-only or running an update or compile.")
            sys.exit(1)

    launch_any(commit, execution_parameters, cached_commits, discard=discard, cache_only=False, wd=project)


def bisect_command(execution_parameters: Optional[str], discard: bool, cached_only: bool, ignore_date: bool, path_spec: Optional[str], project: Optional[str], issue: Optional[str], project_or_issue: list[str]) -> None:
    issue_time, _, execution_parameters, project = bisect.determine_execution_parameters(
        project, issue, "", project_or_issue_or_commit, execution_parameters, commits=False)
    if project is None:
        project = ""
    bisect(discard, cached_only, ignore_date, project, execution_parameters, path_spec, issue_time)


def extract_command(rev: str, file_path: str) -> None:
    storage.extract_commit(rev, file_path)


def purge_command(mrps: bool, duplicates: bool) -> None:
    if duplicates:
        purge_count = storage.purge_duplicate_files({git.resolve_ref(Configuration.START_COMMIT)})
    if os.path.exists(bisect.TMP_DIR):
        purge_count += len(os.listdir(bisect.TMP_DIR))
        shutil.rmtree(bisect.TMP_DIR)
    if mrps:
        purge_count += mrp_manager.purge_all()
    print(f"Purged {purge_count} items.")


def fetch_command() -> None:
    fetch()


def fetch(n: int = 1) -> None:
    print("Fetching...")
    git.fetch()
    rev_list = git.query_rev_list(Configuration.START_COMMIT, Configuration.TRACKED_BRANCH, path_spec="", before=n)
    storage.write_rev_list(rev_list)

    print(f"{len(get_missing_commits(n))} commits are waiting to be compiled.")


def compile_command(revs: list[str]) -> None:
    signal_handler.SHOULD_INSTADIE = False
    if len(revs) == 0:
        revs.append("HEAD")
    commits = [git.resolve_ref(rev) for rev in revs]
    if not factory.compile(commits, should_compress=True, retry_compress=True):
        sys.exit(1)


def compress_command(n: Optional[int], all: bool) -> None:
    signal_handler.SHOULD_INSTADIE = False
    if not factory.compress(n if n is not None else 1, retry=True, all=all):
        sys.exit(1)


def help_command() -> None:
    print("""Usage: bimon.py [-q/--quiet] [-v/--verbose] [-l/--live] COMMAND [COMMAND_ARG...]""")
    
def print_short_help() -> None:
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