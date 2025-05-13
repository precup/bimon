import argparse
import os
import shlex
import string
import sys
from typing import Optional

from src import bisect
from src import execution
from src import factory
from src import git
from src import project_manager
from src import parsers
from src import signal_handler
from src import storage
from src import terminal
from src.config import Configuration, PrintMode

_ORIGINAL_WD = os.getcwd()


def init_command() -> None:
    git.load_cache()

    print("Attempting to perform a fetch...")
    git.fetch()

    print("Checking config file...")
    config_range_error = _get_range_error(
        Configuration.RANGE_START, Configuration.RANGE_END, allow_empty=True
    )
    if config_range_error is not None:
        print(terminal.error("Problem found with range_start/range_end in the config:"))
        print(terminal.error(config_range_error))
        sys.exit(1)

    print("Basic checks passed.")


def update_command(
        n: Optional[int],
        cursor_ref: Optional[str],
        update_ranges: Optional[list[str]]) -> None:
    git.load_cache()
    if Configuration.PRINT_MODE == PrintMode.LIVE:
        signal_handler.install()

    print("Fetching...")
    git.fetch()

    commit_list = _get_commit_list_from_ranges(update_ranges)

    if cursor_ref is not None and len(cursor_ref) > 0:
        cursor_commit = git.resolve_ref(cursor_ref)
        if cursor_commit == "":
            cursor_ref = terminal.color_ref(cursor_ref)
            print(terminal.error(f"The cursor ref {cursor_ref} could not be found."))
            sys.exit(1)
    else:
        head_commit = git.resolve_ref("HEAD")
        if head_commit != "":
            cursor_commit = git.get_similar_commit(head_commit, set(commit_list))
        if cursor_commit == "" or cursor_commit is None:
            cursor_commit = commit_list[-1]

    missing_commits = list(_get_missing_commits(commit_list, 1 if n is None else n))
    if len(missing_commits) == 0:
        print("All the requested commits are already cached or ignored.")
        sys.exit(0)

    cut_commit = git.get_similar_commit(cursor_commit, set(missing_commits))
    if cut_commit == "":
        cut_commit = missing_commits[-1]
    cut_index = missing_commits.index(cut_commit)
    missing_commits = missing_commits[cut_index:] + missing_commits[:cut_index]

    if not factory.compile(missing_commits[::-1]):
        sys.exit(1)


def run_command(
        execution_args: Optional[str],
        discard: bool,
        issue: Optional[str],
        project: Optional[str],
        ref: Optional[str],
        flexible_args: list[str],
        cached_only: bool=False) -> None:
    _handle_autoclean()
    execution_args, project, _, commit, _, _ = _parse_flexible_args(
        flexible_args,
        execution_args,
        project=project,
        issue=issue,
        single_ref_mode=True,
        ref=ref)
    present_versions = storage.get_present_versions()
    ignored_commits = storage.get_ignored_commits()
    compiler_error_commits = storage.get_compiler_error_commits()

    if commit is None:
        commit_list = git.get_commit_list("", "")
        usable_versions = present_versions - ignored_commits
        commit_list = [commit for commit in commit_list if commit in usable_versions]
        if len(commit_list) > 0:
            print("Using the most recent cached version.")
            commit = commit_list[-1]
        elif cached_only:
            print(terminal.error("No cached versions found to run."))
            print(terminal.error("Try running without --cached-only or running"
                + " an update or compile."))
            sys.exit(1)
        else:
            print("No cached versions found to run.")
            print("Using the workspace HEAD.")
            commit = git.resolve_ref("HEAD")
    else:
        if commit not in present_versions and cached_only:
            commit = git.get_short_name(commit)
            print(terminal.error(f"Commit {commit} is not cached."))
            print(terminal.error("Try running without --cached-only or"
                + " running an update or compile."))
            sys.exit(1)
        if commit in ignored_commits:
            short_name = git.get_short_name(commit)
            print(terminal.warn(f"Commit {short_name} is ignored. Continuing anyway."))
        elif commit in compiler_error_commits:
            short_name = git.get_short_name(commit)
            print(terminal.warn(f"Commit {short_name} has had compiler errors in the past."
                  + " Continuing anyway."))
                  
    success = execution.launch(
        ref=commit,
        execution_arguments=execution_args,
        present_versions=present_versions,
        discard=discard,
        cached_only=False,
        wd=project,
        no_subwindow=True)

    if not success:
        sys.exit(1)


def bisect_command(
        execution_args: Optional[str],
        discard: bool,
        cached_only: bool,
        ignore_date: bool,
        path_spec: Optional[str],
        project: Optional[str],
        issue: Optional[str],
        flexible_args: list[str],
        ref_range: Optional[str]) -> None:
    _handle_autoclean()
    last_fetch_time = git.get_last_fetch_time()
    if last_fetch_time == -1 or last_fetch_time < 7 * 24 * 60 * 60:
        print("Trying to fetch since it's been a while...")
        git.fetch()

    execution_args, project, issue_number, _, goods, bads = _parse_flexible_args(
        flexible_args,
        execution_args,
        project=project,
        issue=issue,
        single_ref_mode=False,
        ref_range=ref_range)

    issue_time = -1
    if issue_number >= 0 and not ignore_date:
        poss_issue_time = project_manager.get_approx_issue_creation_time(issue_number)
        if poss_issue_time is None:
            print(terminal.error("Unexpected error acquiring issue creation time, trying to continue anyways."))
        else:
            issue_time = poss_issue_time

    bisector = bisect.Bisector(
        discard=discard,
        cached_only=cached_only,
        execution_args=execution_args,
        path_spec=path_spec,
        end_timestamp=issue_time,
        wd=project,
        initial_goods=goods,
        initial_bads=bads)

    print()
    print("Entering bisect interactive mode. Type", 
        terminal.color_key("help"), "for a list of commands.")
    bisector.queue_decompress_nexts()
    bisector.print_status_message()
    terminal.set_command_completer(parsers.bisect_command_completer)
    parser = parsers.get_bisect_parser()
    while True:
        try:
            command = input(terminal.color_key("bisect> ")).strip()
            if command == "":
                continue
            terminal.add_to_history(command)
            try:
                split_args = shlex.split(command)
                clean_args = parsers.preparse_bisect_command(split_args)

                if len(clean_args) == 0:
                    if len(split_args) > 0:
                        print(terminal.error(f"unrecognized command: {split_args[0]}"))
                    continue

                has_help = '--help' in split_args or '-h' in split_args
                if clean_args[0] == "set-arguments" and not has_help:
                    # Bit of a hack, but we don't want to require this to be escaped
                    # so we bypass argparse
                    execution_args = command.strip()
                    while len(execution_args) > 0 and not execution_args[0].isspace():
                        execution_args = execution_args[1:]
                    execution_args = execution_args.strip()
                    bisector.set_arguments_command(execution_args)
                    continue

                args = parser.parse_args(clean_args)
                if args.func(bisector, args) == bisect.Bisector.CommandResult.EXIT:
                    break

            except AttributeError as e:
                print(terminal.error("Invalid command or option: " + str(e)))
            except argparse.ArgumentError as e:
                print(terminal.error(str(e)))
            except SystemExit:
                pass # argparse --help does this for some stupid reason
        except KeyboardInterrupt:
            print()
            continue
        except EOFError:
            break

    bisector.print_exit_message()


def extract_command(ref: str, folder: Optional[str]) -> None:
    pull_number = project_manager.get_pull_number(ref)
    if pull_number != -1:
        pull_ref = git.get_pull_branch_name(pull_number)
        if git.resolve_ref(pull_ref) != "":
            ref = pull_ref

    version = git.resolve_ref(ref)
    if version == "":
        ref = terminal.color_ref(ref)
        print(terminal.error(f"Invalid ref: {ref} could not be resolved."))
        sys.exit(1)

    if folder is None:
        folder = storage.get_version_folder(version)
    else:
        folder = storage.resolve_relative_to(folder, _ORIGINAL_WD)

    if not storage.extract_version(version, folder):
        sys.exit(1)


def clean_command(
        projects: Optional[bool],
        duplicates: Optional[bool],
        caches: Optional[bool],
        temp_files: Optional[bool],
        loose_files: Optional[bool],
        build_artifacts: Optional[bool],
        dry_run: bool = False) -> None:
    if not (projects or duplicates or caches or temp_files or loose_files or build_artifacts):
        print("No options provided, nothing to be done")
        return

    clean_count = 0
    if duplicates:
        clean_count += storage.clean_duplicate_files(dry_run=dry_run)
    if projects:
        clean_count += project_manager.clean(projects=True, dry_run=dry_run)
    if temp_files:
        clean_count += project_manager.clean(temp_files=True, dry_run=dry_run)
    if build_artifacts:
        if dry_run:
            print("Would delete build artifacts")
        else:
            factory.clean_build_artifacts()
    if caches:
        clean_count += git.delete_cache(dry_run)
        clean_count += execution.delete_cache(dry_run)
    if loose_files:
        clean_count += storage.clean_loose_files(dry_run)

    if not build_artifacts or clean_count > 0:
        if clean_count == 0:
            print("Nothing to clean")
        else:
            item_str = "item" if clean_count == 1 else "items"
            if dry_run:
                print("Would delete", terminal.color_key(str(clean_count)), item_str)
            else:
                print("Deleted", terminal.color_key(str(clean_count)), item_str)


def compile_command(ref_ranges: list[str]) -> None:
    git.load_cache()
    signal_handler.install()
    if len(ref_ranges) == 0:
        ref_ranges.append("HEAD")
    commits_to_compile = []
    direct_compile = []
    seen = set()
    for who_knows in ref_ranges:
        if ".." in who_knows:
            commit_list = git.get_commit_list(*_get_range_parts(who_knows, allow_empty=True))
        else:
            pull_number = project_manager.get_pull_number(who_knows)
            if pull_number != -1:
                git.check_out_pull(pull_number)
                pull_ref = git.get_pull_branch_name(pull_number)
                direct_compile.append(git.resolve_ref(pull_ref))
                continue

            commit = git.resolve_ref(who_knows, fetch_if_missing=True)
            if commit == "":
                who_knows = terminal.color_ref(who_knows)
                print(terminal.error(f"Invalid commit: {who_knows} was not found."))
                sys.exit(1)
            commit_list = [commit]

        for commit in commit_list:
            if commit not in seen:
                seen.add(commit)
                commits_to_compile.append(commit)

    if not factory.compile(commits_to_compile, direct_compile=direct_compile):
        sys.exit(1)


def compress_command(compress_all: bool) -> None:
    git.load_cache()
    signal_handler.install()
    if not factory.compress([], retry=True, compress_all=compress_all):
        sys.exit(1)


def write_precache_command() -> None:
    git.load_cache()
    git.update_neighbors(None)
    for commit in git.get_commit_list("", ""):
        for neighbor in git.get_neighbors(commit):
            git.get_diff_size(commit, neighbor)
        git.get_commit_time(commit)
    git.save_precache()


def help_command(
        help_messages: list[tuple[str, str, str]],
        command_prefix: Optional[str],
        aliases: dict[str, list[str]] = {}) -> None:
    if command_prefix is None:
        command_prefix = ""
    command_prefix = command_prefix.lower().strip()

    should_pad = False
    for key_command, _, help_message in help_messages:
        for alias in aliases.get(key_command, []) + [key_command]:
            if alias.startswith(command_prefix):
                print(terminal.color_log("-" * 80))
                print()
                should_pad = True
                print(help_message)
                break

    if should_pad:
        print(terminal.color_log("-" * 80))
    else:
        print(terminal.error("That command is unknown."))
        print("Available commands:")
        for key_command, _, _ in help_messages:
            print("  " + "/".join([key_command] + aliases.get(key_command, [])))


def export_command(project_name: str, export_path: str, title: Optional[str] = None, as_is: bool = False) -> None:
    _validate_project_name(project_name)
    project_manager.export_project(project_name, export_path, title=title, as_is=as_is)


def create_command(project_name: str, three_x: bool, title: Optional[str] = None) -> None:
    _validate_project_name(project_name)
    commit = "3.6-stable" if three_x else "4.0-stable"
    project_manager.create_project(project_name, title=title, commit=commit)


############################################################################3


def _validate_project_name(project_name: str) -> None:
    if any(c in project_name for c in project_manager.INVALID_NAME_CHARS):
        print(terminal.error("Invalid project name: may not contain any of the following characters: "
            + terminal.color_key("".join(project_manager.INVALID_NAME_CHARS))))
        sys.exit(1)


def _handle_autoclean() -> None:
    if Configuration.AUTOPURGE_DUPLICATES:
        storage.clean_duplicate_files(keep_count=Configuration.AUTOPURGE_LIMIT)


def _get_missing_commits(commit_list: list[str], n: int) -> list[str]:
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


def _exit_if_duplicate(
        item: Optional[str|int],
        item_internal: Optional[str],
        typename: str,
        who_knows: str,
        reason: str = "") -> None:
    article = "an" if typename.startswith("i") else "a"
    prefix = f"flexible_args detected {article} {typename} {terminal.color_key(
        who_knows)} passed to it"
    if item is not None:
        print(terminal.error(prefix + f", but --{typename} is already set."))
        sys.exit(1)
    elif item_internal is not None and item_internal != -1:
        if reason == "":
            reason = item_internal
        print(terminal.error(prefix + f", but another {typename} {terminal.color_key(
            reason)} was already autodetected."))
        sys.exit(1)


def _determine_flexible_args(
        flexible_args: list[str],
        single_ref_mode: bool = True,
        project: Optional[str] = None,
        issue: Optional[str] = None,
        ref: Optional[str] = None,
        ref_range: Optional[str] = None
        ) -> tuple[Optional[str], Optional[int], Optional[str], set[str], set[str]]:
    goods: set[str] = set()
    bads: set[str] = set()
    def add_to_goods(good_commit: str) -> None:
        if any(git.is_ancestor(bad_commit, good_commit) for bad_commit in bads):
            good_commit = git.get_short_name(good_commit)
            print(terminal.error(
                f"Invalid range: a known bad commit is an ancestor of {good_commit}."))
            sys.exit(1)
        goods.add(good_commit)

    def add_to_bads(bad_commit: str) -> None:
        if any(git.is_ancestor(bad_commit, good_commit) for good_commit in goods):
            bad_commit = git.get_short_name(bad_commit)
            print(terminal.error(
                f"Invalid range: {bad_commit} is an ancestor of known good commit."))
            sys.exit(1)
        bads.add(bad_commit)

    def add_range(ref_range: str) -> None:
        start_commit, end_commit = _get_range_parts(ref_range, allow_empty=True)
        if start_commit != "":
            add_to_goods(start_commit)
        if end_commit != "":
            add_to_bads(end_commit)

    if ref_range is not None:
        add_range(ref_range)

    issue_number: int = -1
    pull_number: int = -1
    project_flexible: Optional[str] = None
    issue_flexible: Optional[str] = None
    ref_flexible: Optional[str] = None

    for who_knows in flexible_args:
        possible_issue_number = -1
        if single_ref_mode:
            github_number, is_issue = project_manager.get_github_number(who_knows)
            if github_number != -1:
                if is_issue:
                    print("Interpreting", who_knows, "as an issue number.")
                    print("Issue link:", 
                        terminal.color_key(project_manager.ISSUES_URL + str(github_number)))
                    possible_issue_number = github_number
                else:
                    print("Interpreting", who_knows, "as a pull request number.")
                    print("Pull request link:", 
                        terminal.color_key(project_manager.PULLS_URL + str(github_number)))
                    pull_number = github_number
                    git.check_out_pull(pull_number)
                    pull_ref = git.get_pull_branch_name(pull_number)
                    _exit_if_duplicate(ref, ref_flexible, "ref", who_knows)
                    ref_flexible = pull_ref
                    continue
        else:
            possible_issue_number = project_manager.get_issue_number(who_knows)

        if possible_issue_number != -1 and not who_knows.endswith(".zip"):
            _exit_if_duplicate(issue, issue_number, "issue", who_knows, issue_flexible)
            issue_number = possible_issue_number
            issue_flexible = who_knows
            continue

        if ".." in who_knows and not any(
                bad in who_knows for bad in ["../", "/..", "..\\", "\\.."]):
            add_range(who_knows)
            continue

        flipped = who_knows.startswith("^")
        if flipped:
            who_knows = who_knows[1:]
        if all(c in string.hexdigits for c in who_knows) and len(who_knows) > 7:
            git.resolve_ref(who_knows, fetch_if_missing=True)
        who_knows_commit = git.resolve_ref(who_knows)
        if who_knows_commit != "":
            if single_ref_mode:
                _exit_if_duplicate(ref, ref_flexible, "ref", who_knows)
                ref_flexible = who_knows
                continue
            else:
                if flipped:
                    add_to_bads(who_knows_commit)
                else:
                    add_to_goods(who_knows_commit)
                continue

        _exit_if_duplicate(project, project_flexible, "project", who_knows)
        if who_knows.isdigit():
            print(terminal.warn(f"Interpreting {who_knows} as a project name"
                + " since it doesn't appear to be an issue or PR."))
        project_flexible = who_knows

    if project_flexible is not None:
        project = project_flexible
    if single_ref_mode:
        if ref_flexible is not None:
            ref = ref_flexible
    if issue is not None:
        issue_number = project_manager.get_issue_number(issue)

    return project, issue_number, ref, goods, bads


def _parse_flexible_args(
        flexible_args: list[str],
        execution_args: Optional[str],
        single_ref_mode: bool = True,
        project: Optional[str] = None,
        issue: Optional[str] = None,
        ref: Optional[str] = None,
        ref_range: Optional[str] = None,
    ) -> tuple[str, str, int, Optional[str], set[str], set[str]]:
    if project is not None and len(project.strip()) == "":
        project = None
    if issue is not None and len(issue.strip()) == "":
        issue = None
    if ref is not None and len(ref.strip()) == "":
        ref = None
    if ref_range is not None and len(ref_range.strip()) == "":
        ref_range = None

    project, issue_number, ref, goods, bads = _determine_flexible_args(
        flexible_args,
        single_ref_mode=single_ref_mode,
        project=project,
        issue=issue,
        ref=ref,
        ref_range=ref_range)

    commit = None
    if single_ref_mode:
        if ref is not None:
            possible_pull_number = project_manager.get_pull_number(ref)
            if possible_pull_number != -1:
                pull_number = possible_pull_number
                git.check_out_pull(pull_number)
                ref = git.get_pull_branch_name(pull_number)
            commit = git.resolve_ref(ref, fetch_if_missing=True)
            if commit == "":
                ref = terminal.color_ref(ref)
                print(terminal.error(f"Invalid ref: {ref} was not found."))
                sys.exit(1)

    if execution_args is None:
        execution_args = Configuration.DEFAULT_EXECUTION_ARGS

    if project is None or project == "":
        project = project_manager.get_mrp(issue_number, commit)
        if project == "":
            print(terminal.error("Unexpected error acquiring a project."))
            sys.exit(1)
    elif project.endswith(".zip"):
        project_name = project_manager.get_project_name_from_issue_or_file(issue_number, project)
        if project.startswith("http"):
            if not project_manager.download_project(project, project_name):
                print(terminal.error("Failed to download zip file."))
                sys.exit(1)
            project = project_manager.get_project_path(project_name)
        else:
            project = project_manager.extract_project(project, project_name)
        if project == "":
            sys.exit(1)
    else:
        project_name_path = project_manager.get_project_path(project)
        if project_name_path != "" and os.path.exists(project_name_path):
            project = project_name_path
        elif os.path.exists(storage.resolve_relative_to(project, _ORIGINAL_WD)):
            project = storage.resolve_relative_to(project, _ORIGINAL_WD)
            project_file = project_manager.find_project_file(project)
            if project_file != "" and project_file is not None:
                project = project_file
        else:
            if "/" in project or "\\" in project:
                print(f"Could not find a project at {terminal.color_key(project)}.")
                response = input("Create one there now? [y/"
                    + terminal.color_key("N") + "]: ")
                if response.strip().lower().startswith("y"):
                    project = storage.resolve_relative_to(project, _ORIGINAL_WD)
                    os.mkdir(project)
                    project_manager.create_project_file(project)
                else:
                    sys.exit(1)
            else:
                print(f"Could not find a project named {terminal.color_key(project)}.")
                response = input("Create one now? [y/"
                    + terminal.color_key("N") + "]: ")
                if response.strip().lower().startswith("y"):
                    project = project_manager.create_project(project, commit=commit)
                    if project == "":
                        print(terminal.error("Failed to create project."))
                        sys.exit(1)
                else:
                    sys.exit(1)

    if project.endswith("project.godot"):
        project = project[:-len("project.godot")]

    return execution_args, project, issue_number, commit, goods, bads


def _get_range_error(
        start_ref: str,
        end_ref: str,
        allow_empty: bool,
        allow_nonancestor: bool = False) -> Optional[str]:
    start_ref = start_ref.strip()
    if start_ref == "":
        if not allow_empty:
            return "Invalid range: no range start was provided."
    else:
        start_commit = git.resolve_ref(start_ref, fetch_if_missing=True)
        if start_commit == "":
            start_ref = terminal.color_ref(start_ref)
            return f"Invalid range: start commit ({start_ref}) was not found."

    end_ref = end_ref.strip()
    if end_ref == "":
        if not allow_empty:
            return "Invalid range: no range end was provided."
    else:
        end_commit = git.resolve_ref(end_ref, fetch_if_missing=True)
        if end_commit == "":
            end_ref = terminal.color_ref(end_ref)
            return f"Invalid range: end commit ({end_ref}) was not found."

    if start_ref != "" and end_ref != "" and not git.is_ancestor(start_commit, end_commit):
        if allow_nonancestor:
            start_ref = git.get_short_name(start_ref)
            end_ref = git.get_short_name(end_ref)
            return f"Invalid range: start ({start_ref}) is not an ancestor of end ({end_ref})."
        else:
            print(terminal.warn("Range start is not an ancestor of range end."
                + " This is probably fine for a bisect, continuing."))
    return None


def _get_range_parts(
        ref_range: str,
        allow_empty: bool = False,
        allow_nonancestor: bool = False) -> tuple[str, str]:
    if ref_range.count("..") != 1:
        print(terminal.error("Range must be in the format "
            + terminal.color_key("START_REF..END_REF") + "."))
        sys.exit(1)
    start_ref, end_ref = tuple(part.strip() for part in ref_range.split(".."))
    range_error = _get_range_error(start_ref, end_ref, allow_empty)
    if range_error is not None:
        print(terminal.error(range_error))
        sys.exit(1)
    return (
        git.resolve_ref(start_ref) if start_ref != "" else "",
        git.resolve_ref(end_ref) if end_ref != "" else "",
    )


def _get_commit_list_from_ranges(ref_ranges: Optional[list[str]]) -> list[str]:
    if ref_ranges is None or len(ref_ranges) == 0:
        ref_ranges = [f"{Configuration.RANGE_START}..{Configuration.RANGE_END}"]
    parsed_ranges = []
    for update_range in ref_ranges:
        parsed_ranges.append(_get_range_parts(update_range, allow_empty=True))

    commit_list = []
    seen = set()
    for start, end in parsed_ranges:
        for commit in git.get_commit_list(start, end):
            if commit not in seen:
                seen.add(commit)
                commit_list.append(commit)

    if len(commit_list) == 0:
        print(terminal.error(
            "Invalid range: there were no commits found in the update range(s)."))
        sys.exit(1)

    return commit_list