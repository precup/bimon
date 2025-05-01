#!/usr/bin/env python

import argparse
import os
import sys
from typing import Optional

import src.factory as factory
import src.git as git
import src.mrp_manager as mrp_manager
import src.signal_handler as signal_handler
import src.storage as storage
import src.terminal as terminal

from src.bisect import BisectRunner, _launch_any
from src.config import Configuration, PrintMode

############################################################
#                      Initialization                      #
############################################################

_original_wd = os.getcwd()

def main() -> None:
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    storage.init_storage()
    terminal.init_terminal()
    signal_handler.install()   
    process_command_and_arguments()


def process_command_and_arguments() -> None:
    platform = "mac" if sys.platform.lower() == "darwin" else "windows" if os.name == "nt" else "linux"

    parser = argparse.ArgumentParser(description=
        "BiMon: A tool for speeding up bisecting during bug triage for the Godot engine bugsquad.",
        epilog="For detailed information on a command, run 'bimon.py <command> --help'.\n\n")
    parser.add_argument("-q", "--quiet", action="store_const", const=PrintMode.QUIET, dest="print_mode", help=
        "Hide output from long-running subprocesses")
    parser.add_argument("-v", "--verbose", action="store_const", const=PrintMode.VERBOSE, dest="print_mode", help=
        "Print full output from subprocesses")
    parser.add_argument("-l", "--live", action="store_const", const=PrintMode.LIVE, dest="print_mode", help=
        "Show a small updating display of subprocess output")
    parser.add_argument("--color", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Enable colored output")
    parser.add_argument("-c", "--config", type=str, help=
        f"Path to the configuration file. Defaults to config.ini, falls back to default_{platform}_config.ini.")
    parser.add_argument("-i", "--ignore-old-errors", action="store_true", help=
        "Don't skip commits even if they have been unbuildable in the past")
    parser.set_defaults(print_mode=PrintMode.LIVE if sys.stdout.isatty() else PrintMode.VERBOSE)

    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # Porcelain commands
    init_parser = subparsers.add_parser("init",
        help="Initializes the workspace and checks that things are set up properly.",
        description="This command ensures that the workspace is set up correctly and does nothing else. "
        + "Running it on first set up will help make sure everything is in order.")
    init_parser.add_argument("-y", action="store_true", help=
        "Don't ask for confirmation before setting up the workspace.")
    init_parser.set_defaults(func=lambda _: init_command())

    update_parser = subparsers.add_parser("update",
        help="Fetch, compile, and cache missing commits.",
        description="This command fetches the latest commits from the remote repository, "
        + "compiles them if they are missing, and caches the compiled binaries. ")
    update_parser.add_argument("-n", type=int, help=
        "Only compile and cache 1 in every N commits.")
    update_parser.add_argument("-r", "--range", type=str, action="append", help=
        "The commit range(s) to compile, format 'start..end'. Defaults to the values in config file.")
    update_parser.add_argument("cursor_ref", nargs="?", help=
        "The git reference to start working back from.")
    update_parser.set_defaults(func=lambda args: update_command(args.n, args.cursor_ref, args.range))

    repro_parser = subparsers.add_parser("repro", help=
        "Reproduce an issue using the specified parameters.")
    repro_parser.add_argument("-d", "--discard", action="store_true", help=
        "Prevent caching binaries compiled during a bisect.")
    repro_parser.add_argument("-c", "--cached-only", action="store_true", help=
        "Prevent compiling at all during a bisect.")
    repro_parser.add_argument("-e", "--execution-parameters", type=str, help=
        "The parameters to pass to the Godot executable. See the config.ini comments for details.")
    repro_parser.add_argument("--project", type=str, help=
        "The project name or path to use as an MRP. Can be a directory, project file, zip file, or link to a zip file.")
    repro_parser.add_argument("--issue", type=str, help=
        "The issue number or URL to reproduce.")
    repro_parser.add_argument("--commit", type=str, help=
        "The commit to launch. Also accepts git references.")
    repro_parser.add_argument("flexible_args", nargs="*", help=
        "Accepts the same things as the previous three options. Autodetects which each is.")
    repro_parser.set_defaults(func=lambda args: repro_command(args.execution_parameters, 
        args.discard, args.cached_only, args.issue, args.project, args.version, args.flexible_args))

    bisect_parser = subparsers.add_parser("bisect", help=
        "Bisect history to find a regression's commit via an interactive mode.")
    bisect_parser.add_argument("-d", "--discard", action="store_true", help=
        "Prevent caching binaries compiled during a bisect.")
    bisect_parser.add_argument("-c", "--cached-only", action="store_true", help=
        "Prevent compiling at all during a bisect.")
    bisect_parser.add_argument("-e", "--execution-parameters", type=str, help=
        "The parameters to pass to the Godot executable. See the config.ini comments for details.")
    bisect_parser.add_argument("--ignore-date", action="store_true", help=
        "Don't use the issue open date to cut down the commit range.")
    bisect_parser.add_argument("--path-spec", type=str, default=None, help=
        "Limit the bisect to commits with specific files. See git bisect's path_spec behavior for full details.")
    bisect_parser.add_argument("--project", type=str, default=None, help=
        "The project to use for testing. Can be a directory, project.godot, or zip file.")
    bisect_parser.add_argument("--issue", type=str, default=None, help=
        "The issue to test. Can be a link or issue number. Used to autodownload MRPs and restrict the commit range by date.")
    bisect_parser.add_argument("-r", "--range", type=str, default=None, help=
        "The commit range to bisect, format 'good..bad'.")
    bisect_parser.add_argument("flexible_args", nargs="*", help=
        "Accepts the same things as the previous three options. Autodetects which each is.")
    bisect_parser.set_defaults(func=lambda args: bisect_command(args.execution_parameters, 
        args.discard, args.cached_only, args.ignore_date, args.path_spec, args.project, 
        args.issue, args.flexible_args, args.range))

    purge_parser = subparsers.add_parser("purge", help=
        "Delete uncompressed binaries that are also present in compressed form.")
    purge_parser.add_argument("--projects", nargs="?", const=True, default=True, type=bool_arg_parse, help=
        "")
    purge_parser.add_argument("--downloads", nargs="?", const=True, default=True, type=bool_arg_parse, help=
        "")
    purge_parser.add_argument("--duplicates", nargs="?", const=True, default=True, type=bool_arg_parse, help=
        "")
    purge_parser.add_argument("--caches", nargs="?", const=True, default=False, type=bool_arg_parse, help=
        "")
    purge_parser.add_argument("--temp-files", nargs="?", const=True, default=True, type=bool_arg_parse, help=
        "")
    purge_parser.add_argument("--loose-files", nargs="?", const=True, default=False, type=bool_arg_parse, help=
        "")
    purge_parser.add_argument("--all", action="store_true", help=
        "")
    purge_parser.add_argument("--all-except-cache", action="store_true", help=
        "")
    purge_parser.set_defaults(func=lambda args: purge_command(args.projects, args.downloads, args.duplicates, args.caches, args.temp_files, args.loose_files, args.all, args.all_except_cache))

    # Plumbing commands
    compile_parser = subparsers.add_parser("compile", help=
        "Compile and store specific commits.")
    compile_parser.add_argument("ref_ranges", nargs="*", default="HEAD", help=
        "The commits to compile. Accepts any git reference or ranges in the form start..end. Defaults to the workspace HEAD if not provided.")
    compile_parser.set_defaults(func=lambda args: compile_command(args.refs))

    compress_parser = subparsers.add_parser("compress", help=
        "Archive completed bundles.")
    compress_parser.add_argument("-a", "--all", action="store_true", help=
        "Bundle and compress all loose files, regardless of whether there are enough for a full bundle.")
    compress_parser.set_defaults(func=lambda args: compress_command(args.n, args.all))

    extract_parser = subparsers.add_parser("extract", help="Extract the binary for a specific revision.")
    extract_parser.add_argument("ref", help="The commit to extract.")
    extract_parser.add_argument("folder", nargs="?", help="The folder to extract to.")
    extract_parser.set_defaults(func=lambda args: extract_command(args.ref, args.folder))

    args = parser.parse_args()
    global_args(args)

    workspace = Configuration.WORKSPACE_PATH
    if not os.path.exists(workspace):
        print(f"BiMon requires a Godot workspace at path '{workspace}'.")
        should_clone = False
        if hasattr(args, "y") and args.y:
            print("Cloning one there now...")
            should_clone = True
        else:
            should_clone = input("Clone one there now? [y/N]: ").strip().lower().startswith('y')
        if should_clone:
            git.clone("https://github.com/godotengine/godot.git", workspace)
        else:
            print("BiMon requires a Godot workspace to function. Exiting.")
            sys.exit(1)

    args.func(args)


def bool_arg_parse(arg: str) -> bool:
    return arg.lower() in ("true", "1", "yes", "")


def global_args(args):
    config_path = storage.resolve_relative_to(args.config, _original_wd) if args.config is not None else ""
    Configuration.load_from(config_path)
    if args.ignore_old_errors is not None:
        Configuration.IGNORE_OLD_ERRORS = args.ignore_old_errors
    if args.color is None:
        args.color = sys.stdout.isatty() and os.getenv("TERM") != "dumb"
    Configuration.COLOR_ENABLED = args.color
    if args.print_mode is not None:
        Configuration.PRINT_MODE = args.print_mode


def get_range_error(start_ref: str, end_ref: str) -> Optional[str]:
    start_ref = start_ref.strip()
    end_ref = end_ref.strip()
    if start_ref == "":
        return "Invalid range: no range start was provided."
    if end_ref == "":
        return "Invalid range: no range end was provided."
    start_commit = git.resolve_ref(start_ref, fetch_if_missing=True)
    if start_commit == "":
        return f"Invalid range: start commit ({start_ref}) was not found."
    end_commit = git.resolve_ref(end_ref, fetch_if_missing=True)
    if end_commit == "":
        return f"Invalid range: end commit ({end_ref}) was not found."
    if not git.is_ancestor(start_commit, end_commit):
        return f"Invalid range: start ({start_ref}) is not an ancestor of end ({end_ref})."
    return None


############################################################
#                         Commands                         #
############################################################


def init_command() -> None:
    git.load_cache()

    print("Attempting to perform a fetch...")
    git.fetch()

    print("Checking config file...")
    config_range_error = get_range_error(Configuration.RANGE_START, Configuration.RANGE_END)
    if config_range_error is not None:
        print("Problem found with range_start/range_end in the config:")
        print(config_range_error)
        sys.exit(1)

    print("Basic checks passed.")


def update_command(n: Optional[int], cursor_ref: Optional[str], update_ranges: Optional[list[str]]) -> None:
    git.load_cache()
    signal_handler.SHOULD_INSTADIE = False

    if update_ranges is None or len(update_ranges) == 0:
        update_ranges = [f"{Configuration.RANGE_START}..{Configuration.RANGE_END}"]

    git.fetch()
    parsed_ranges = []
    for update_range in update_ranges:
        if update_range.count("..") != 1:
            print("Range must be in the format 'start_commit..end_commit'.")
            sys.exit(1)
        start_ref, end_ref = update_range.split("..")
        range_error = get_range_error(start_ref, end_ref)
        if range_error is not None:
            print(range_error)
            sys.exit(1)
        start_commit = git.resolve_ref(start_ref)
        end_commit = git.resolve_ref(end_ref)
        parsed_ranges.append((start_commit, end_commit))

    if cursor_ref is not None and len(cursor_ref) > 0:
        cursor_commit = git.resolve_ref(cursor_ref)
        if cursor_commit == "":
            print(f"The cursor ref {cursor_ref} could not be found.")
            sys.exit(1)
    else:
        cursor_commit = parsed_ranges[-1][1]

    if n is None:
        n = 1
        
    commit_list = []
    seen = set()
    for start, end in parsed_ranges:
        for commit in git.get_commit_list(start, end):
            if commit not in seen:
                seen.add(commit)
                commit_list.append(commit)
    if len(commit_list) == 0:
        print("Invalid range: there were no commits found in the update range(s).")
        sys.exit(1)

    cut = commit_list.index(cursor_commit)
    if cut < 0:
        print(f"The cursor commit {cursor_commit} was not in the commit range(s).")
        sys.exit(1)

    missing_commits = list(get_missing_commits(commit_list, n))
    if len(missing_commits) == 0:
        print("All the requested commits are already cached or ignored.")
        sys.exit(0)

    while commit_list[cut] not in missing_commits:
        cut -= 1
    cut = missing_commits.index(commit_list[cut]) + 1
    missing_commits = missing_commits[cut:] + missing_commits[:cut]

    if not factory.compile(missing_commits[::-1], should_compress=Configuration.COMPRESSION_ENABLED, n=n, retry_compress=True):
        sys.exit(1)


def get_missing_commits(commit_list: list[str], n: int) -> list[str]:
    not_missing_commits = set(storage.get_present_versions())
    not_missing_commits.update(storage.get_ignored_commits())
    if not Configuration.IGNORE_OLD_ERRORS:
        not_missing_commits.update(storage.get_compiler_error_commits())
    missing_commits = []
    sequential_missing = 0
    for commit in commit_list:
        if commit in not_missing_commits:
            sequential_missing = 0
        else:
            sequential_missing += 1
        if sequential_missing >= n:
            missing_commits.append(commit)
            sequential_missing = 0
    return missing_commits


def repro_command(
        execution_parameters: Optional[str],
        discard: bool,
        cached_only: bool,
        issue: Optional[str],
        project: Optional[str],
        ref: Optional[str],
        flexible_args: list[str]
    ) -> None:
    _, commit, execution_parameters, project, _, _ = determine_execution_parameters(
        project, issue, ref, flexible_args, execution_parameters
    )
    present_versions = storage.get_present_versions()
    ignored_commits = storage.get_ignored_commits()
    compiler_error_commits = storage.get_compiler_error_commits()

    if commit is None:
        range_error = get_range_error(Configuration.RANGE_START, Configuration.RANGE_END)
        if range_error is not None:
            print("Problem found with range_start/range_end in the config:")
            print(range_error)
            sys.exit(1)
        commit_list = git.get_commit_list(Configuration.RANGE_START, Configuration.RANGE_END)
        commit_list = [commit for commit in commit_list if commit in present_versions]
        if len(commit_list) > 0:
            print("Using the most recent cached version in the range.")
            commit = commit_list[-1]
            for possible_commit in commit_list:
                if possible_commit not in (ignored_commits | compiler_error_commits):
                    commit = possible_commit
        elif cached_only:
            print("No cached versions found to repro with.")
            print("Try running without --cached-only or running an update or compile.")
            sys.exit(1)
        else:
            print("No cached versions found to repro with.")
            print("Using the last commit in the range.")
            commit = git.resolve_ref(Configuration.RANGE_END, fetch_if_missing=True)
    else:
        if commit not in present_versions and cached_only:
            print(f"Commit {commit} is not cached.")
            print("Try running without --cached-only or running an update or compile.")
            sys.exit(1)
        if commit in ignored_commits:
            print(f"WARNING: Commit {commit} is ignored. Continuing anyway.")
        elif commit in compiler_error_commits:
            print(f"WARNING: Commit {commit} has had compiler errors in the past. Continuing anyway.")

    if not _launch_any(commit, execution_parameters, present_versions, discard=discard, cache_only=False):
        sys.exit(1)


def bisect_command(
        execution_parameters: Optional[str],
        discard: bool,
        cached_only: bool,
        ignore_date: bool,
        path_spec: Optional[str],
        project: Optional[str],
        issue: Optional[str],
        flexible_args: list[str],
        range: Optional[str],
    ) -> None:    
    issue_time, _, execution_parameters, project, goods, bads = determine_execution_parameters(
        project, issue, "", flexible_args, execution_parameters, single_ref=False, range=range
    )
    if project is None:
        project = ""
    bisect_runner = BisectRunner(
        discard, cached_only, ignore_date, execution_parameters, path_spec, issue_time, project, goods, bads,
    )
    bisect_runner.run()


def extract_command(ref: str, folder: Optional[str]) -> None:
    version = git.resolve_ref(ref)
    if version == "":
        print(f"Invalid ref: {ref} could not be resolved.")
        sys.exit(1)
    
    if folder is not None:
        folder = storage.resolve_relative_to(folder, _original_wd)

    if not storage.extract_version(version, folder):
        sys.exit(1)


def purge_command(projects: bool, downloads: bool, duplicates: bool, caches: bool, temp_files: bool, loose_files: bool, all: bool, all_except_cache: bool) -> None:
    if all or all_except_cache:
        projects = downloads = duplicates = caches = temp_files = loose_files = True
        if all_except_cache:
            caches = False
    purge_count = 0
    if duplicates:
        purge_count += storage.purge_duplicate_files()
    if os.path.exists(BisectRunner.TMP_DIR):
        purge_count += storage.get_file_count(BisectRunner.TMP_DIR)
        storage.rm(BisectRunner.TMP_DIR)
    if projects:
        purge_count += mrp_manager.purge_all()
    elif downloads:
        purge_count += mrp_manager.purge_downloads() # TODO implement
    print(f"Purged {purge_count} items.")


def compile_command(ref_ranges: list[str]) -> None:
    git.load_cache()
    signal_handler.SHOULD_INSTADIE = False
    if len(ref_ranges) == 0:
        ref_ranges.append("HEAD")
    commits_to_compile = []
    seen = set()
    for ref_range in ref_ranges:
        if ".." in ref_range:
            if ref_range.count("..") != 1:
                print("Range must be in the format 'start_commit..end_commit'.")
                sys.exit(1)
            start_ref, end_ref = tuple(part.strip() for part in ref_range.split(".."))
            range_error = get_range_error(start_ref, end_ref)
            if range_error is not None:
                print(range_error)
                sys.exit(1)
            commit_list = git.get_commit_list(start_ref, end_ref)
        else:
            commit = git.resolve_ref(ref_range, fetch_if_missing=True)
            if commit == "":
                print(f"Invalid commit: {ref_range} was not found.")
                sys.exit(1)
            commit_list = [commit]

        for commit in commit_list:
            if commit not in seen:
                seen.add(commit)
                commits_to_compile.append(commit)

    if not factory.compile(commits_to_compile, should_compress=Configuration.COMPRESSION_ENABLED, retry_compress=True):
        sys.exit(1)


def compress_command(compress_all: bool) -> None:
    git.load_cache()
    signal_handler.SHOULD_INSTADIE = False
    if not factory.compress([], retry=True, compress_all=compress_all):
        sys.exit(1)


def exit_if_duplicate(item: Optional[str|int], item_internal: Optional[str], typename: str, who_knows: str, reason: str = "") -> None:
    if reason == "":
        reason = item_internal
    prefix = f"flexible_args detected a{'n' if typename.startswith('i') else ''} {typename} '{who_knows}' passed to it"
    if item is not None:
        print(prefix + f", but --{typename} is already set.")
        sys.exit(1)
    elif item_internal is not None and item_internal != -1:
        print(prefix + f", but another {typename} '{reason}' was already autodetected.")
        sys.exit(1)


def determine_execution_parameters(
        project: Optional[str],
        issue: Optional[str],
        ref: Optional[str],
        flexible_args: list[str],
        execution_parameters: Optional[str],
        single_ref: bool = True,
        range: Optional[str] = None,
    ) -> tuple[int, str, str, str]:
    if len(project.strip()) == "":
        project = None
    if len(issue.strip()) == "":
        issue = None
    if len(ref.strip()) == "":
        ref = None
    if len(range.strip()) == "":
        range = None

    goods = set()
    bads = set()
    if range is not None:
        if range.count("..") != 1:
            print("Range must be in the format 'start_ref..end_ref'.")
            sys.exit(1)
        start, end = tuple(part.strip() for part in range.split(".."))
        range_error = get_range_error(start, end)
        if range_error is not None:
            print(range_error)
            sys.exit(1)
        goods.add(start)
        bads.add(end)

    issue_number: int = -1
    issue_reason: Optional[str] = None
    project_internal: Optional[str] = None
    ref_internal: Optional[str] = None

    for who_knows in flexible_args:
        # TODO : Add a check for the commit hash length + hex that uses fetch_if_missing=True
        commit = git.resolve_ref(who_knows)
        if commit != "":
            exit_if_duplicate(ref, ref_internal, "ref", who_knows)
            ref_internal = who_knows
            continue
        issue_num_temp = mrp_manager.get_issue_number(who_knows)
        if issue_num_temp == -1 or who_knows.endswith(".zip"):
            exit_if_duplicate(project, project_internal, "project", who_knows)
            project_internal = who_knows
        else:
            exit_if_duplicate(issue, issue_number, "issue", who_knows, issue_reason)
            issue_number = issue_num_temp
            issue_reason = who_knows

    if issue is not None:
        issue_number = mrp_manager.get_issue_number(issue)
    if project_internal is not None:
        project = project_internal
    if single_ref:
        if commit_internal is not None:
            commit = commit_internal
        if commit is not None:
            commit = git.resolve_ref(commit, fetch_if_missing=True)
            if commit == "":
                commit_str = commit_internal if commit_internal is not None else commit
                print(f"Invalid commit: {commit_str} was not found.")
                sys.exit(1)

    if execution_parameters is None:
        execution_parameters = Configuration.DEFAULT_EXECUTION_PARAMETERS
    if project is None or project == "":
        project = mrp_manager.get_mrp(issue_number)
        if project == "" and "{PROJECT}" in execution_parameters:
            print("Nothing to do.")
            sys.exit(0)
    elif not project.startswith("http"):
        project = storage.resolve_relative_to(project, _original_wd)

    if project.endswith(".zip"):
        if project.startswith("http"):
            if not mrp_manager.download_zip(project, mrp_manager.TEMPORARY_ZIP):
                print("Failed to download zip file.")
                sys.exit(1)
            project = mrp_manager.TEMPORARY_ZIP
        project = mrp_manager.extract_mrp(project, issue_number)
        if project == "":
            sys.exit(1)
    if project.endswith("project.godot"):
        project = project[:-len("project.godot")]

    if "{PROJECT}" in execution_parameters:
        execution_parameters = execution_parameters.replace("{PROJECT}", project)

    if issue_number != -1:
        print("Issue link:", mrp_manager.ISSUES_URL + str(issue_number))

    issue_time = -1
    if issue_number >= 0:
        issue_time = mrp_manager.get_approx_issue_creation_time(issue_number)
    return issue_time, commit, execution_parameters, project, goods, bads


if __name__ == "__main__":
    main()
