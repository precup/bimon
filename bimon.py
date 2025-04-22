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
# TODO _ import??
from src.bisect import BisectRunner, _launch_any
from src.config import Configuration, PrintMode

############################################################
#                      Initialization                      #
############################################################

def main() -> None:
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    storage.init_storage()
    terminal.init_terminal()
    signal_handler.install()   
    process_command_and_arguments()


def process_command_and_arguments() -> None:
    platform = "mac" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"

    parser = argparse.ArgumentParser(description=
        "BiMon: A tool for speeding up bisecting during bug triage for the Godot engine bugsquad.",
        epilog="For detailed information on a command, run 'bimon.py <command> --help'.\n\n")
    parser.add_argument("-q", "--quiet", action="store_const", const=PrintMode.QUIET, dest="print_mode", help=
        "Hide output from long-running subprocesses")
    parser.add_argument("-v", "--verbose", action="store_const", const=PrintMode.VERBOSE, dest="print_mode", help=
        "Print full output from subprocesses")
    parser.add_argument("-l", "--live", action="store_const", const=PrintMode.LIVE, dest="print_mode", help=
        "Show a small updating display of subprocess output")
    parser.add_argument("-f", "--force", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Discard uncommitted changes in the workspace directory if they're in the way")
    parser.add_argument("--color", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Enable colored output")
    parser.add_argument("-c", "--config", type=str, help=
        "Path to the configuration file. Defaults to config.ini, falls back to default_{platform}_config.ini.")
    parser.add_argument("-i", "--ignore-old-errors", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Don't skip commits even if they have been unbuildable in the past")
    parser.set_defaults(print_mode=PrintMode.LIVE if sys.stdout.isatty() else PrintMode.VERBOSE)

    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # Porcelain commands
    init_parser = subparsers.add_parser("init",
        help="Initializes the workspace and checks that things are set up properly.",
        description="This command ensures that the workspace is set up correctly and does nothing else. "
        + "Running it on first set up will help make sure everything is in order.")
    init_parser.set_defaults(func=lambda args: init_command())

    update_parser = subparsers.add_parser("update", 
        help="Fetch, compile, and cache missing commits.",
        description="This command fetches the latest commits from the remote repository, "
        + "compiles them if they are missing, and caches the compiled binaries. ")
    update_parser.add_argument("-n", type=int, help=
        "Only compile and cache 1 in every N commits.")
    update_parser.add_argument("-r", "--range", type=str, help=
        "The commit range to compile, format 'start..end'. Defaults to the values in config file.")
    update_parser.add_argument("cursor_rev", nargs="?", help=
        "The revision to start working back from.")
    update_parser.set_defaults(func=lambda args: update_command(args.n, args.cursor_rev, args.range))

    repro_parser = subparsers.add_parser("repro", help=
        "Reproduce an issue using the specified parameters.")
    repro_parser.add_argument("-d", "--discard", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Prevent caching binaries compiled during a bisect.")
    repro_parser.add_argument("-c", "--cached-only", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Prevent compiling at all during a bisect.")
    repro_parser.add_argument("-e", "--execution-parameters", type=str, help=
        "The parameters to pass to the Godot executable. See the config.ini comments for details.")
    repro_parser.add_argument("--project", type=str, help=
        "The project name or path to use as an MRP.")
    repro_parser.add_argument("--issue", type=str, help=
        "The issue number or URL to reproduce. Used to download an MRP.")
    repro_parser.add_argument("--commit", type=str, help=
        "The revision to launch for testing.")
    repro_parser.add_argument("project_or_issue_or_commit", nargs="*", help=
        "Accepts the same things as --project, --issue, or --commit. Autodetects which it is.")
    repro_parser.set_defaults(func=lambda args: repro_command(args.execution_parameters, 
        args.discard, args.cached_only, args.issue, args.project, args.commit, 
        args.project_or_issue_or_commit))

    bisect_parser = subparsers.add_parser("bisect", help=
        "Bisect history to find a regression's commit via an interactive mode.")
    bisect_parser.add_argument("-d", "--discard", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Prevent caching binaries compiled during a bisect.")
    bisect_parser.add_argument("-c", "--cached-only", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Prevent compiling at all during a bisect.")
    bisect_parser.add_argument("-e", "--execution-parameters", type=str, help=
        "The parameters to pass to the Godot executable. See the config.ini comments for details.")
    bisect_parser.add_argument("--ignore-date", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Don't use the issue open date to cut down the commit range.")
    bisect_parser.add_argument("--path-spec", type=str, default=None, help=
        "Limit the bisect to commits with specific files. See git bisect's path_spec behavior for full details.")
    bisect_parser.add_argument("--project", type=str, default=None, help=
        "The project to use for testing. Can be a directory, project.godot, or zip file.")
    bisect_parser.add_argument("--issue", type=str, default=None, help=
        "The issue to test. Can be a link or issue number. Used to autodownload MRPs and restrict the commit range by date.")
    bisect_parser.add_argument("project_or_issue", nargs="*", help=
        "Accepts the same things as --project and --issue, autodetecting which one each is.")
    bisect_parser.set_defaults(func=lambda args: bisect_command(args.execution_parameters, 
        args.discard, args.cached_only, args.ignore_date, args.path_spec, args.project, 
        args.issue, args.project_or_issue))

    purge_parser = subparsers.add_parser("purge", help=
        "Delete uncompressed binaries that are also present in compressed form.")
    purge_parser.add_argument("-m", "--mrps", nargs="?", const=True, default=True, type=bool_arg_parse, help=
        "Bundle and compress all loose files, regardless of whether they're optimally similar.")
    purge_parser.add_argument("-d", "--duplicates", nargs="?", const=True, default=True, type=bool_arg_parse, help=
        "Bundle and compress all loose files, regardless of whether they're optimally similar.")
    purge_parser.set_defaults(func=lambda args: purge_command(args.mrps, args.duplicates))

    # Plumbing commands
    compile_parser = subparsers.add_parser("compile", help=
        "Compile and store specific revisions.")
    compile_parser.add_argument("revs", nargs="*", default="HEAD", help=
        "The revisions to compile. Defaults to the workspace HEAD if none are provided.")
    compile_parser.set_defaults(func=lambda args: compile_command(args.revs))

    compress_parser = subparsers.add_parser("compress", help=
        "Pack completed bundles.")
    compress_parser.add_argument("-n", type=int, help=
        "Allow gaps of size N - 1 while bundling. Useful for 1 in N updates.")
    compress_parser.add_argument("-a", "--all", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Bundle and compress all loose files, regardless of whether they're optimally similar.")
    compress_parser.set_defaults(func=lambda args: compress_command(args.n, args.all))

    extract_parser = subparsers.add_parser("extract", help="Extract the binary for a specific revision.")
    extract_parser.add_argument("rev", help="The revision name to extract.")
    extract_parser.add_argument("file_path", help="The file path to extract the binary to.")
    extract_parser.set_defaults(func=lambda args: extract_command(args.rev, args.file_path))

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


def bool_arg_parse(arg: str) -> bool:
    return arg.lower() in ("true", "1", "yes", "")


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


############################################################
#                         Commands                         #
############################################################


def init_command() -> None:
    print("You're good to go.")


def update_command(n: Optional[int], cursor_rev: Optional[str], update_range: Optional[str]) -> None:
    signal_handler.SHOULD_INSTADIE = False

    if update_range is None or update_range == "":
        start_commit = git.resolve_ref(Configuration.RANGE_START)
        end_commit = git.resolve_ref(Configuration.RANGE_END)
        update_range = f"{start_commit}..{end_commit}"

    if ".." not in update_range:
        print("The update range must be in the format 'start_commit..end_commit'.")
        sys.exit(1)

    update(n if n is not None else 1, cursor_rev, update_range)


def update(n: int, cursor_commit: Optional[str], update_range: str) -> None:
    git.fetch()

    start_commit, end_commit = update_range.split("..", 1)
    start_commit = git.resolve_ref(start_commit.strip())
    if start_commit == "":
        print(f"Invalid range: start commit {update_range.split('..', 1)[0]} was not found.")
        sys.exit(1)
    end_commit = git.resolve_ref(end_commit.strip())
    if end_commit == "":
        print(f"Invalid range: end commit {update_range.split('..', 1)[1]} was not found.")
        sys.exit(1)

    if cursor_commit is not None and len(cursor_commit) > 0:
        cursor_commit_tmp = git.resolve_ref(cursor_commit.strip())
        if cursor_commit_tmp == "":
            print(f"The cursor commit {cursor_commit} could not be found.")
            sys.exit(1)
        cursor_commit = cursor_commit_tmp
    else:
        cursor_commit = end_commit

    rev_list = git.query_rev_list(start_commit, end_commit)
    if len(rev_list) == 0:
        print("Invalid range: there were no commits found in that range.")
        sys.exit(1)
    cut = rev_list.index(cursor_commit)
    if cut < 0:
        print(f"The cursor commit {cursor_commit} was not in the commit range {update_range}.")
        sys.exit(1)

    missing_commits = list(get_missing_commits(rev_list, n)[::-1])
    if len(missing_commits) == 0:
        print("All the requested commits are already cached or ignored.")
        sys.exit(0)

    while rev_list[cut] not in missing_commits:
        cut -= 1
    cut = missing_commits.index(rev_list[cut])
    missing_commits = missing_commits[cut:] + missing_commits[:cut]

    if not factory.compile(missing_commits, should_compress=True, n=n, retry_compress=True):
        sys.exit(1)


def get_missing_commits(rev_list: list[str], n: int) -> list[str]:
    present_commits = set(storage.get_present_commits())
    present_commits.update(storage.get_ignored_commits())
    if not Configuration.IGNORE_OLD_ERRORS:
        present_commits.update(storage.get_compiler_error_commits())
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


def repro_command(
        execution_parameters: Optional[str], 
        discard: bool, 
        cached_only: bool, 
        issue: Optional[str], 
        project: Optional[str], 
        commit: Optional[str], 
        project_or_issue_or_commit: list[str]
    ) -> None:
    _, commit, execution_parameters, project = determine_execution_parameters(
        project, issue, commit, project_or_issue_or_commit, execution_parameters
    )
    cached_commits = storage.get_present_commits()
    ignored_commits = storage.get_ignored_commits()
    compiler_error_commits = storage.get_compiler_error_commits()

    if commit is None:
        rev_list = git.query_rev_list(Configuration.RANGE_START, Configuration.RANGE_END)
        rev_list = [rev for rev in rev_list if rev in cached_commits]
        if len(rev_list) > 0:
            print("Using the most recent cached commit in the range.")
            culled_rev_list = [
                commit for commit in rev_list 
                if commit not in ignored_commits and commit not in compiler_error_commits
            ]
            commit = rev_list[-1] if len(culled_rev_list) == 0 else culled_rev_list[-1]
        elif cached_only:
            print("No cached commits found to repro with.")
            print("Try running without --cached-only or running an update or compile.")
            sys.exit(1)
        else:
            print("No cached commits found to repro with.")
            print("Using the last commit in the range.")
            commit = Configuration.RANGE_END
    else:
        commit = git.resolve_ref(commit)
        if commit not in cached_commits and cached_only:
            print(f"Commit {commit} is not cached.")
            print("Try running without --cached-only or running an update or compile.")
            sys.exit(1)
        if commit in ignored_commits:
            print(f"WARNING: Commit {commit} is ignored. Continuing anyway.")
        elif commit in compiler_error_commits:
            print(f"WARNING: Commit {commit} has had compiler errors in the past. Continuing anyway.")

    if not _launch_any(commit, execution_parameters, cached_commits, discard=discard, cache_only=False):
        sys.exit(1)


def bisect_command(
        execution_parameters: Optional[str], 
        discard: bool, 
        cached_only: bool, 
        ignore_date: bool, 
        path_spec: Optional[str], 
        project: Optional[str], 
        issue: Optional[str], 
        project_or_issue: list[str]
    ) -> None:
    issue_time, _, execution_parameters, project = determine_execution_parameters(
        project, issue, "", project_or_issue, execution_parameters, commits=False
    )
    if project is None:
        project = ""
    bisect_runner = BisectRunner(
        discard, cached_only, ignore_date, project, execution_parameters, path_spec, issue_time
    )
    bisect_runner.run()


def extract_command(rev: str, file_path: str) -> None:
    if not storage.extract_commit(rev, file_path):
        sys.exit(1)


def purge_command(mrps: bool, duplicates: bool) -> None:
    if duplicates:
        purge_count = storage.purge_duplicate_files()
    if os.path.exists(bisect.TMP_DIR):
        purge_count += len(os.listdir(bisect.TMP_DIR))
        shutil.rmtree(bisect.TMP_DIR)
    if mrps:
        purge_count += mrp_manager.purge_all()
    print(f"Purged {purge_count} items.")


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


def exit_if_duplicate(item: Optional, item_internal: Optional, typename: str, who_knows: str, reason: str = "", commits: bool = True) -> None:
    if reason == "":
        reason = item_internal
    prefix = f"project_or_issue{'_or_commit' if commits else ''} detected a{'n' if typename.startswith('i') else ''} {typename} '{who_knows}' passed to it"
    if item is not None:
        print(prefix + f", but --{typename} is already set.")
        sys.exit(1)
    elif item_internal is not None and item_internal != -1:
        print(prefix + f", but another {typename} '{reason}' was already autodetected.")
        sys.exit(1)


def determine_execution_parameters(
        project: Optional[str],
        issue: Optional[str],
        commit: Optional[str],
        project_or_issue_or_commit: list[str],
        execution_parameters: Optional[str],
        commits: bool = True
    ) -> (str, str):
    if project == "":
        project = None
    if issue == "":
        issue = None
    if commit == "":
        commit = None
    issue_number: int = -1
    issue_reason: Optional[str] = None
    project_internal: Optional[str] = None
    commit_internal: Optional[str] = None

    for who_knows in project_or_issue_or_commit:
        if commits and (not who_knows.isdigit() or len(who_knows) > 7):
            ref = git.resolve_ref(who_knows)
            if ref != "":
                exit_if_duplicate(commit, commit_internal, "commit", who_knows, commits=commits)
                commit_internal = who_knows
                continue
        issue_num_temp = mrp_manager.get_issue_number(who_knows)
        if issue_num_temp == -1 or who_knows.endswith(".zip"):
            exit_if_duplicate(project, project_internal, "project", who_knows, commits=commits)
            project_internal = who_knows
        else:
            exit_if_duplicate(issue, issue_number, "issue", who_knows, issue_reason, commits=commits)
            issue_number = issue_num_temp
            issue_reason = who_knows

    if issue is not None:
        issue_number = mrp_manager.get_issue_number(issue)
    if project_internal is not None:
        project = project_internal
    if commits:
        if commit_internal is not None:
            commit = commit_internal
        if commit is not None:
            commit = git.resolve_ref(commit)
            if commit == "":
                print(f"Invalid commit: {commit_internal} was not found.")
                sys.exit(1)
        else:
            present_commits = storage.get_present_commits()
            rev_list = git.query_rev_list(Configuration.RANGE_START, Configuration.RANGE_END)
            for rev in rev_list[::-1]:
                if rev in present_commits:
                    commit = rev
                    break

    cached_commits = storage.get_present_commits()
    if execution_parameters is None:
        execution_parameters = Configuration.DEFAULT_EXECUTION_PARAMETERS
    if "{PROJECT}" in execution_parameters:
        if project is None or project == "":
            project = mrp_manager.get_mrp(issue_number)
            if project == "":
                print("Nothing to do.")
                sys.exit(0)

    if project.endswith(".zip"):
        if project.startswith("http"):
            target_path = os.path.join(mrp_manager.MRP_FOLDER, "temp.zip")
            if os.path.exists(target_path):
                os.remove(target_path)
            if not mrp_manager.download_zip(project, target_path):
                print("Failed to download zip file.")
                sys.exit(1)
            project = target_path
        project = mrp_manager.extract_mrp(project, issue_number)
        if project == "":
            sys.exit(1)
    if project.endswith("project.godot"):
        project = project[:-len("project.godot")]

    if "{PROJECT}" in execution_parameters:
        execution_parameters = execution_parameters.replace("{PROJECT}", project)

    issue_time = -1
    if issue_number >= 0:
        issue_time = mrp_manager.get_approx_issue_creation_time(issue_number)
    return issue_time, commit, execution_parameters, project


if __name__ == "__main__":
    main()