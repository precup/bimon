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
from src.config import Configuration

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
        print("Problem found with range_start/range_end in the config:")
        print(config_range_error)
        sys.exit(1)

    print("Basic checks passed.")


def update_command(
        n: Optional[int], 
        cursor_ref: Optional[str], 
        update_ranges: Optional[list[str]]) -> None:
    git.load_cache()
    signal_handler.install()

    if update_ranges is None or len(update_ranges) == 0:
        update_ranges = [f"{Configuration.RANGE_START}..{Configuration.RANGE_END}"]

    print("Fetching...")
    git.fetch()
    parsed_ranges = []
    for update_range in update_ranges:
        parsed_ranges.append(_get_range_parts(update_range, allow_empty=True))
        
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

    missing_commits = list(_get_missing_commits(commit_list, 1 if n is None else n))
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
        cached_only: bool=False) -> None:
    _handle_autoclean()
    execution_parameters, project, _, commit, _, _ = _parse_flexible_args(
        flexible_args, 
        execution_parameters, 
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
            print("No cached versions found to repro with.")
            print("Try running without --cached-only or running an update or compile.")
            sys.exit(1)
        else:
            print("No cached versions found to repro with.")
            print("Using the workspace HEAD.")
            commit = git.resolve_ref("HEAD")
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

    success = execution.launch(
        ref=commit, 
        execution_parameters=execution_parameters, 
        present_versions=present_versions, 
        discard=discard, 
        cached_only=False, 
        wd=project)
    
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
        ref_range: Optional[str]) -> None:
    _handle_autoclean()
    last_fetch_time = git.get_last_fetch_time()
    if last_fetch_time == -1 or last_fetch_time < 7 * 24 * 60 * 60:
        print("Trying to fetch since it's been a while...")
        try: 
            git.fetch()
        except git.GitError:
            print("Failed to fetch. This is probably fine, continuing anyway.")
            pass
    git.update_neighbors()

    execution_parameters, project, issue_number, _, goods, bads = _parse_flexible_args(
        flexible_args, 
        execution_parameters, 
        project=project, 
        issue=issue, 
        single_ref_mode=False, 
        ref_range=ref_range)
    
    issue_time = -1
    if issue_number >= 0 and not ignore_date:
        issue_time = project_manager.get_approx_issue_creation_time(issue_number)

    bisector = bisect.Bisector(
        discard=discard, 
        cached_only=cached_only, 
        execution_parameters=execution_parameters, 
        path_spec=path_spec, 
        end_timestamp=issue_time, 
        wd=project, 
        initial_goods=goods, 
        initial_bads=bads)

    print("Entering bisect interactive mode. Type \"help\" for a list of commands.")
    bisector.queue_decompress_nexts()
    bisector.print_status_message()
    terminal.set_command_completer(parsers.bisect_command_completer)
    parser = parsers.get_bisect_parser()
    while True:
        try:
            command = input("bisect> ").strip()
            if command == "":
                continue
            terminal.add_to_history(command)
            try:
                split_args = shlex.split(command, posix='nt' != os.name)
                clean_args = parsers.preparse_bisect_command(split_args)
                has_help = '--help' in split_args or '-h' in split_args
                if clean_args.lower() == "set-params" and not has_help:
                    # Bit of a hack, but we don't want to require this to be escaped
                    # so we bypass argparse
                    execution_parameters = command.strip()
                    while len(execution_parameters) > 0 and not execution_parameters[0].isspace():
                        execution_parameters = execution_parameters[1:]
                    execution_parameters = execution_parameters.strip()
                    bisector.set_parameters_command(execution_parameters)
                    continue

                if len(clean_args) == 0:
                    if len(split_args) > 0:
                        print(f"Unrecognized command: {split_args[0]}, use \"help\" for help.")
                    continue
                args = parser.parse_args(clean_args)
                if args.func(bisector, args) == bisect.Bisector.CommandResult.EXIT:
                    break
            except AttributeError as e:
                print("Invalid command or option:", e)
            except SystemExit as e:
                # TODO the help message on this is awful formatting wise
                pass
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
        print(f"Invalid ref: {ref} could not be resolved.")
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
    clean_count = 0
    if duplicates:
        clean_count += storage.clean_duplicate_files(dry_run=dry_run)
    if projects:
        clean_count += project_manager.clean(projects=True, dry_run=dry_run)
    if temp_files:
        clean_count += project_manager.clean(temp_files=True, dry_run=dry_run)
    if build_artifacts:
        if dry_run:
            print("Build artifacts will be deleted.")
        else:
            factory.clean_build_artifacts()
    if caches:
        clean_count += git.delete_cache(dry_run)
        clean_count += execution.delete_cache(dry_run)
    if loose_files:
        clean_count += storage.clean_loose_files(dry_run)
    
    if not build_artifacts or clean_count > 0:
        print(f"Purged {clean_count} items.")


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
                print(f"Invalid commit: {who_knows} was not found.")
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
                if should_pad:
                    print("-" * 80)
                print()
                should_pad = True
                print(help_message)
                break

    if not should_pad:
        print("That command is unknown.")
        print("Available commands:")
        for key_command, _, _ in help_messages:
            print("  " + "/".join([key_command] + aliases.get(key_command, [])))


def export_command(project_name: str, export_path: str, title: Optional[str] = None) -> None:
    if any(c in project_name for c in project_manager.INVALID_NAME_CHARS):
        print("Invalid project name: may not contain any of the following characters: "
            + "".join(project_manager.INVALID_NAME_CHARS))
        sys.exit(1)
    project_manager.export_project(project_name, export_path, title=title)


def create_command(project_name: str, title: Optional[str] = None) -> None:
    if any(c in project_name for c in project_manager.INVALID_NAME_CHARS):
        print("Invalid project name: may not contain any of the following characters: "
            + "".join(project_manager.INVALID_NAME_CHARS))
        sys.exit(1)
    project_manager.create_project(project_name, title=title)


############################################################################3


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
    prefix = f"flexible_args detected {article} {typename} \"{who_knows}\" passed to it"
    if item is not None:
        print(prefix + f", but --{typename} is already set.")
        sys.exit(1)
    elif item_internal is not None and item_internal != -1:
        if reason == "":
            reason = item_internal
        print(prefix + f", but another {typename} \"{reason}\" was already autodetected.")
        sys.exit(1)


def _parse_flexible_args(
        flexible_args: list[str],
        execution_parameters: Optional[str],
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

    goods: set[str] = set()
    bads: set[str] = set()
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
        start_commit, end_commit = _get_range_parts(
            ref_range, allow_empty=True, allow_nonancestor=True
        )
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
                    possible_issue_number = github_number
                else:
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

        if ".." in who_knows:
            add_range(who_knows)

        flipped = who_knows.startswith("^")
        if flipped:
            who_knows = who_knows[1:]
        if all(c in string.hexdigits for c in who_knows) and len(who_knows) > 7:
            git.resolve_ref(who_knows, fetch_if_missing=True)
        commit = git.resolve_ref(who_knows)
        if commit != "":
            if single_ref_mode:
                _exit_if_duplicate(ref, ref_flexible, "ref", who_knows)
                ref_flexible = who_knows
                continue
            else:
                if flipped:
                    add_to_bads(commit)
                else:
                    add_to_goods(commit)
                continue

        _exit_if_duplicate(project, project_flexible, "project", who_knows)
        project_flexible = who_knows

    commit = None
    if issue is not None:
        issue_number = project_manager.get_issue_number(issue)
    if project_flexible is not None:
        project = project_flexible
    if single_ref_mode:
        if ref_flexible is not None:
            ref = ref_flexible
        if ref is not None:
            possible_pull_number = project_manager.get_pull_number(ref)
            if possible_pull_number != -1:
                pull_number = possible_pull_number
                git.check_out_pull(pull_number)
                ref = git.get_pull_branch_name(pull_number)
            commit = git.resolve_ref(ref, fetch_if_missing=True)
            if commit == "":
                print(f"Invalid ref: {ref} was not found.")
                sys.exit(1)

    if execution_parameters is None:
        execution_parameters = Configuration.DEFAULT_EXECUTION_PARAMETERS

    if project is None or project == "":
        project = project_manager.get_mrp(issue_number)
        if project == "" and "{PROJECT}" in execution_parameters:
            print("Nothing to do.")
            sys.exit(0)
        else:
            project = project_manager.get_project_path("")
            if not os.path.exists(project):
                os.mkdir(project)
    elif not project.startswith("http"):
        project_name_path = project_manager.get_project_path(project)
        if project_name_path != "" and os.path.exists(project_name_path):
            project = project_name_path
        else:
            project = storage.resolve_relative_to(project, _ORIGINAL_WD)
            if not os.path.exists(project):
                print(f"Project \"{project}\" does not exist.")
                response = input("Create one there now? [y/N]: ")
                if response.strip().lower().startswith("y"):
                    os.mkdir(project)
                    project_manager.create_project_file(project)
                else:
                    sys.exit(1)

    if project.endswith(".zip"):
        if project.startswith("http"):
            if not project_manager.download_zip(project, project_manager.TEMPORARY_ZIP):
                print("Failed to download zip file.")
                sys.exit(1)
            project = project_manager.TEMPORARY_ZIP
        project = project_manager.extract_mrp(project, issue_number)
        if project == "":
            sys.exit(1)
    elif project.endswith("project.godot"):
        project = project[:-len("project.godot")]

    if "{PROJECT}" in execution_parameters:
        execution_parameters = execution_parameters.replace("{PROJECT}", "./")

    # TODO this organization is a bit awkward
    if issue_number != -1:
        print("Issue link:", project_manager.ISSUES_URL + str(issue_number))
    if pull_number != -1:
        print("Pull request link:", project_manager.PULLS_URL + str(issue_number))

    return execution_parameters, project, issue_number, commit, goods, bads


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
        if allow_nonancestor:
            return f"Invalid range: start ({start_ref}) is not an ancestor of end ({end_ref})."
        else:
            print("Range start is not an ancestor of range end."
                + " This is probably fine for a bisect, continuing.")
    return None


def _get_range_parts(
        ref_range: str, 
        allow_empty: bool = False, 
        allow_nonancestor: bool = False) -> tuple[str, str]:
    if ref_range.count("..") != 1:
        print("Range must be in the format \"start_ref..end_ref\".")
        sys.exit(1)
    start_ref, end_ref = tuple(part.strip() for part in ref_range.split(".."))
    range_error = _get_range_error(start_ref, end_ref, allow_empty)
    if range_error is not None:
        print(range_error)
        sys.exit(1)
    return (
        git.resolve_ref(start_ref) if start_ref != "" else "",
        git.resolve_ref(end_ref) if end_ref != "" else "",
    )