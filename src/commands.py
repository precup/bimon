import os
import sys
from typing import Optional

from src import factory
from src import git
from src import mrp_manager
from src import signal_handler
from src import storage
from src.bisect import Bisect
from src.config import Configuration

_original_wd = os.getcwd()


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


def update_command(
        n: Optional[int], 
        cursor_ref: Optional[str], 
        update_ranges: Optional[list[str]]) -> None:
    git.load_cache()
    signal_handler.SHOULD_INSTADIE = False

    if update_ranges is None or len(update_ranges) == 0:
        update_ranges = [f"{Configuration.RANGE_START}..{Configuration.RANGE_END}"]

    git.fetch()
    parsed_ranges = []
    for update_range in update_ranges:
        parsed_ranges.append(get_range_parts(update_range, allow_empty=True))
        
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

    if cursor_ref is not None and len(cursor_ref) > 0:
        cursor_commit = git.resolve_ref(cursor_ref)
        if cursor_commit == "":
            print(f"The cursor ref {cursor_ref} could not be found.")
            sys.exit(1)
    else:
        head_commit = git.resolve_ref("HEAD")
        if head_commit != "":
            cursor_commit = git.get_similar_commit(head_commit, set(commit_list))
        if cursor_commit == "" or cursor_commit is None:
            cursor_commit = commit_list[-1]

    cut = commit_list.index(cursor_commit)
    if cut < 0:
        print(f"The cursor commit {cursor_commit} was not in the commit range(s).")
        sys.exit(1)

    missing_commits = list(get_missing_commits(commit_list, 1 if n is None else n))
    if len(missing_commits) == 0:
        print("All the requested commits are already cached or ignored.")
        sys.exit(0)

    while commit_list[cut] not in missing_commits:
        cut -= 1
    cut = missing_commits.index(commit_list[cut]) + 1
    missing_commits = missing_commits[cut:] + missing_commits[:cut]

    if not factory.compile(missing_commits[::-1]):
        sys.exit(1)


def repro_command(
        execution_parameters: Optional[str],
        discard: bool,
        issue: Optional[str],
        project: Optional[str],
        ref: Optional[str],
        flexible_args: list[str],
        cached_only: bool=False,
    ) -> None:
    execution_parameters, project, _, commit, _, _ = parse_flexible_args(
        flexible_args, 
        execution_parameters, 
        project=project, 
        issue=issue, 
        single_ref_mode=True, 
        ref=ref
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
            print(f"WARNING: Commit {commit} has had compiler errors in the past."
                  + " Continuing anyway.")

    success = _launch_any(
        commit=commit, 
        execution_parameters=execution_parameters, 
        present_versions=present_versions, 
        discard=discard, 
        cache_only=False, 
        wd=project
    )
    if not success:
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
        ref_range: Optional[str],
    ) -> None:
    execution_parameters, project, issue_number, _, goods, bads = parse_flexible_args(
        flexible_args, 
        execution_parameters, 
        project=project, 
        issue=issue, 
        single_ref_mode=False, 
        ref_range=ref_range
    )
    issue_time = -1
    if issue_number >= 0 and not ignore_date:
        issue_time = mrp_manager.get_approx_issue_creation_time(issue_number)

    Bisect(
        discard=discard, 
        cached_only=cached_only, 
        execution_parameters=execution_parameters, 
        path_spec=path_spec, 
        end_timestamp=issue_time, 
        wd=project, 
        initial_goods=goods, 
        initial_bads=bads,
    ).run()


def extract_command(ref: str, folder: Optional[str]) -> None:
    version = git.resolve_ref(ref)
    if version == "":
        print(f"Invalid ref: {ref} could not be resolved.")
        sys.exit(1)
    
    if folder is not None:
        folder = storage.resolve_relative_to(folder, _original_wd)

    if not storage.extract_version(version, folder):
        sys.exit(1)


def purge_command(
        projects: Optional[bool], 
        downloads: Optional[bool], 
        duplicates: Optional[bool], 
        caches: Optional[bool], 
        temp_files: Optional[bool], 
        loose_files: Optional[bool], 
        build_artifacts: Optional[bool]) -> None:
    purge_count = 0
    if duplicates:
        purge_count += storage.purge_duplicate_files()
    if os.path.exists(Bisect.TMP_DIR):
        purge_count += storage.get_file_count(Bisect.TMP_DIR)
        storage.rm(Bisect.TMP_DIR)
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
            commit_list = git.get_commit_list(get_range_parts(ref_range, allow_empty=True))
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

    if not factory.compile(commits_to_compile):
        sys.exit(1)


def compress_command(compress_all: bool) -> None:
    git.load_cache()
    signal_handler.SHOULD_INSTADIE = False
    if not factory.compress([], retry=True, compress_all=compress_all):
        sys.exit(1)


def write_precache_command() -> None:
    git.load_cache()
    git.save_precache()


def help_command(
        help_messages: list[tuple[str, str, str]], 
        command_prefix: Optional[str], 
        aliases: dict[str, list[str]] = {}) -> None:
    if command_prefix is None:
        command_prefix = ""
    command_prefix = command_prefix.lower().strip()
    
    should_pad = False
    for key_command, usage_message, help_message in help_messages:
        for alias in aliases.get(key_command, []) + [key_command]:
            if alias.startswith(command_prefix):
                if should_pad:
                    print()
                should_pad = True
                print(usage_message)
                print(help_message)
                break

    if not should_pad:
        print("That command is unknown.")
        print("Available commands:")
        for key_command, _, _ in help_messages:
            print(f"  {'/'.join([key_command] + aliases.get(key_command, []))}")


############################################################################3


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


def exit_if_duplicate(
        item: Optional[str|int], 
        item_internal: Optional[str], 
        typename: str, 
        who_knows: str, 
        reason: str = "") -> None:
    if reason == "":
        reason = item_internal
    prefix = f"flexible_args detected a{'n' if typename.startswith('i') else ''} {typename} '{who_knows}' passed to it"
    if item is not None:
        print(prefix + f", but --{typename} is already set.")
        sys.exit(1)
    elif item_internal is not None and item_internal != -1:
        print(prefix + f", but another {typename} '{reason}' was already autodetected.")
        sys.exit(1)


def parse_flexible_args(
        flexible_args: list[str],
        execution_parameters: Optional[str],
        single_ref_mode: bool = True,
        project: Optional[str] = None,
        issue: Optional[str] = None,
        ref: Optional[str] = None,
        ref_range: Optional[str] = None,
    ) -> tuple[str, str, int, Optional[str], set[str], set[str]]:
    if len(project.strip()) == "":
        project = None
    if len(issue.strip()) == "":
        issue = None
    if len(ref.strip()) == "":
        ref = None
    if len(ref_range.strip()) == "":
        ref_range = None

    goods = set()
    bads = set()
    def add_to_goods(good_commit: str) -> None:
        if any(git.is_ancestor(bad_commit, good_commit) for bad_commit in bads):
            print(f"Invalid range: a known bad commit is an ancestor of {good_commit}.")
            sys.exit(1)
        goods.add(good_commit)

    def add_to_bads(bad_commit: str) -> None:
        if any(git.is_ancestor(bad_commit, good_commit) for good_commit in goods):
            print(f"Invalid range: {bad_commit} is an ancestor of known good commit.")
            sys.exit(1)
        bads.add(bad_commit)
    
    def add_range(ref_range: str) -> None:
        start_commit, end_commit = get_range_parts(ref_range, allow_empty=True)
        if start_commit != "":
            add_to_goods(start_commit)
        if end_commit != "":
            add_to_bads(end_commit)

    if ref_range is not None:
        add_range(ref_range)

    issue_number: int = -1
    project_flexible: Optional[str] = None
    issue_flexible: Optional[str] = None
    ref_flexible: Optional[str] = None

    for who_knows in flexible_args:
        possible_issue_number = mrp_manager.get_issue_number(who_knows)
        if possible_issue_number != -1 and not who_knows.endswith(".zip"):
            exit_if_duplicate(issue, issue_number, "issue", who_knows, issue_flexible)
            issue_number = possible_issue_number
            issue_flexible = who_knows
            continue

        if ".." in who_knows:
            add_range(who_knows)

        flipped = who_knows.startswith("^")
        if flipped:
            who_knows = who_knows[1:]
        if all(c in "0123456789abcdef" for c in who_knows.lower()) and len(who_knows) > 7:
            git.resolve_ref(who_knows, fetch_if_missing=True)
        commit = git.resolve_ref(who_knows)
        if commit != "":
            if single_ref_mode:
                exit_if_duplicate(ref, ref_flexible, "ref", who_knows)
                ref_flexible = who_knows
                continue
            else:
                if flipped:
                    add_to_bads(commit)
                else:
                    add_to_goods(commit)
                continue

        exit_if_duplicate(project, project_flexible, "project", who_knows)
        project_flexible = who_knows

    commit = None
    if issue is not None:
        issue_number = mrp_manager.get_issue_number(issue)
    if project_flexible is not None:
        project = project_flexible
    if single_ref_mode:
        if ref_flexible is not None:
            ref = ref_flexible
        if ref is not None:
            commit = git.resolve_ref(ref, fetch_if_missing=True)
            if commit == "":
                print(f"Invalid ref: {ref} was not found.")
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
    elif project.endswith("project.godot"):
        project = project[:-len("project.godot")]

    if "{PROJECT}" in execution_parameters:
        execution_parameters = execution_parameters.replace("{PROJECT}", project)

    if issue_number != -1:
        print("Issue link:", mrp_manager.ISSUES_URL + str(issue_number))

    return execution_parameters, project, issue_number, commit, goods, bads


def get_range_error(start_ref: str, end_ref: str, allow_empty: bool) -> Optional[str]:
    start_ref = start_ref.strip()
    if start_ref == "":
        if not allow_empty:
            return "Invalid range: no range start was provided."
    else:
        start_commit = git.resolve_ref(start_ref, fetch_if_missing=True)
        if start_commit == "":
            return f"Invalid range: start commit ({start_ref}) was not found."

    end_ref = end_ref.strip()
    if end_ref == "":
        if not allow_empty:
            return "Invalid range: no range end was provided."
    else:
        end_commit = git.resolve_ref(end_ref, fetch_if_missing=True)
        if end_commit == "":
            return f"Invalid range: end commit ({end_ref}) was not found."

    if start_ref != "" and end_ref != "" and not git.is_ancestor(start_commit, end_commit):
        return f"Invalid range: start ({start_ref}) is not an ancestor of end ({end_ref})."
    return None


def get_range_parts(ref_range: str, allow_empty: bool = False) -> tuple[str, str]:
    if ref_range.count("..") != 1:
        print("Range must be in the format 'start_ref..end_ref'.")
        sys.exit(1)
    start_ref, end_ref = tuple(part.strip() for part in ref_range.split(".."))
    range_error = get_range_error(start_ref, end_ref, allow_empty)
    if range_error is not None:
        print(range_error)
        sys.exit(1)
    return (
        git.resolve_ref(start_ref) if start_ref != "",
        git.resolve_ref(end_ref) if end_ref != "",
    )