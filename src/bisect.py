from typing import Optional
from .config import Configuration
import os
import time
import shlex
import sys
from math import log2

import src.mrp_manager as mrp_manager
import src.storage as storage
import src.git as git

_should_pause = False
_is_good = False
_is_bad = False
WARN_TIME = 60 * 60 * 24 * 7  # 1 week
TMP_DIR = "tmp"


def _input_with_cancel(prompt, cancel_event):
    user_input = [None]

    def get_input():
        try:
            user_input[0] = input(prompt)
        except EOFError:
            pass  # Handle cases where input is interrupted

    input_thread = threading.Thread(target=get_input)
    input_thread.start()

    while input_thread.is_alive():
        if cancel_event.is_set():
            return None
        time.sleep(0.05)

    return user_input[0]


def _mark_good() -> None:
    # TODO close project if open
    _is_good = True


def _mark_bad() -> None:
    # TODO close project if open
    _is_bad = True


def print_exit_message(goods: set[str], bads: set[str], skips: set[str], remaining: set[str]) -> None:
    goods = unique_goods(goods)
    bads = unique_bads(bads)
    if len(remaining) == 1:
        bad_commit = next(remaining)
        print("Only one revision left, must be " + git.get_short_name(bad_commit))
        print("https://github.com/godotengine/godot/commit/" + bad_commit)
        print(git.get_short_log(bad_commit))
    print("\nExiting bisect interactive mode.")
    if len(remaining) > 0 and (len(goods) > 0 or len(bads) > 0 or len(skips) > 0):
        print(f"There are {len(remaining)} remaining commits.")
        print("You can resume with:")
        if len(goods) > 0:
            print(f"good {' '.join(goods)}")
        if len(bads) > 0:
            print(f"bad {' '.join(bads)}")
        if len(skips) > 0:
            print(f"skip {' '.join(skips)}")


def unique_bads(bads: set[str]) -> set[str]:
    return set(
        commit for commit in bads 
        if all(
            not git.is_ancestor(test_commit, commit) 
            for test_commit in bads if commit != test_commit
        )
    )


def unique_goods(goods: set[str]) -> set[str]:
    return set(
        commit for commit in goods
        if all(
            not git.is_ancestor(commit, test_commit) 
            for test_commit in goods if commit != test_commit
        )
    )


def print_status_message(goods: set[str], bads: set[str], skips: set[str], remaining: set[str], current: str, long: bool = False) -> None:
    merged = set(remaining)
    merged.update(unique_bads(bads))
    steps_left = int(log2(len(merged)))
    if len(goods) == 0:
        steps_left += 1
    if len(bads) == 0:
        steps_left += 1
    print(f"Approximately {steps_left} tests remaining. Next commit to test:")
    print(git.get_short_log(current))
    if long and (len(goods) > 0 or len(bads) > 0 or len(skips) > 0):
        temp_goods = unique_goods(goods)
        temp_bads = unique_bads(bads)
        print("Minimal sets of marked commits:")
        if len(temp_goods) > 0:
            print(f"good {' '.join(temp_goods)}")
        if len(temp_bads) > 0:
            print(f"bad {' '.join(temp_bads)}")
        if len(skips) > 0:
            print(f"skip {' '.join(skips)}")


def determine_execution_parameters(
    project: Optional[str],
    issue: Optional[str],
    commit: Optional[str],
    project_or_commit_or_issues: list[str],
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
        if commits:
            ref = git.resolve_ref(who_knows)
            if ref != "":
                if commit is not None:
                    print(f"project_or_issue_or_commit detected a commit '{who_knows}' passed to it, but --commit is already set.")
                    sys.exit(1)
                elif commit_internal is not None:
                    print(f"project_or_issue_or_commit detected a commit '{who_knows}' passed to it, but another commit '{commit_internal}' was already autodetected.")
                    sys.exit(1)
                commit_internal = who_knows
                continue
        issue_num_temp = mrp.get_issue_number(who_knows)
        if issue_num_temp == -1:
            if project is not None:
                print(f"project_or_issue_or_commit detected a project '{who_knows}' passed to it, but --project is already set.")
                sys.exit(1)
            elif project_internal is not None:
                print(f"project_or_issue_or_commit detected a project '{who_knows}' passed to it, but another project '{project_internal}' was already autodetected.")
                sys.exit(1)
            project_internal = who_knows
        else:
            if issue is not None:
                print(f"project_or_issue_or_commit detected an issue '{who_knows}' passed to it, but --issue is already set.")
                sys.exit(1)
            elif issue_number is not None:
                print(f"project_or_issue_or_commit detected an issue '{who_knows}' passed to it, but another issue '{issue_reason}' was already autodetected.")
                sys.exit(1)
            issue_number = issue_num_temp
            issue_reason = who_knows
    if issue is not None:
        issue_number = mrp.get_issue_number(issue)
    if project_internal is not None:
        project = project_internal
    if commits:
        if commit_internal is not None:
            commit = git.resolve_ref(commit_internal)
        elif commit is not None:
            commit = git.resolve_ref(commit)
        else:
            present_commits = storage.get_present_commits()
            rev_list = git.query_rev_list(Configuration.START_COMMIT, Configuration.TRACKED_BRANCH)
            commit = next(commit for commit in rev_list[::-1] if commit in present_commits)
    
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
        project = mrp_manager.extract_mrp(project, issue_number)
        if project == "":
            sys.exit(1)
    if project.endswith("project.godot"):
        project = project[:-len("project.godot")]
    if "{PROJECT}" in execution_parameters:
        execution_parameters = execution_parameters.replace("{PROJECT}", project)
    issue_time = -1
    if commit:
        issue_time = git.get_commit_time(commit)
    return issue_time, commit, execution_parameters, project


def bisect(discard: bool, cache_only: bool, ignore_date: bool, project: str, execution_parameters: str, path_spec: str, issue_timestamp: int) -> None:
    try:
        git.fetch()
    except:
        pass
    if ignore_date:
        issue_timestamp = -1
    if Configuration.ENABLE_HOTKEYS:
        pass # TODO
    if not os.path.exists(TMP_DIR):
        os.mkdir(TMP_DIR)
    if path_spec is None:
        path_spec = ""

    print("Entering bisect interactive mode. Type 'help' for a list of commands.")
    started = False
    good_revisions = set()
    bad_revisions = set()
    skipped_revisions = set()
    present_commits = storage.get_present_commits()
    old_error_commits = set()
    if not Configuration.IGNORE_OLD_ERRORS:
        old_error_commits = storage.get_compiler_error_commits()
    ignored_commits = storage.get_ignored_commits()
    rev_list = git.query_rev_list(Configuration.START_COMMIT, Configuration.TRACKED_BRANCH, path_spec=path_spec, before=issue_timestamp)
    if len(rev_list) == 0:
        print("No matching commits found going back to the start of the commit range.")
        response = input("Would you like to set an earlier start commit? (y/n): ")
        if response.lower().startswith("y"):
            while True:
                new_start_commit = input("Enter a new start commit: ")
                if git.resolve_ref(new_start_commit) == "":
                    print(f"Invalid commit: {new_start_commit}")
                    continue
                Configuration.START_COMMIT = new_start_commit
                rev_list = git.query_rev_list(Configuration.START_COMMIT, Configuration.TRACKED_BRANCH, path_spec=path_spec, before=issue_timestamp)
                if len(rev_list) > 0:
                    break
                print("No matching commits found going back to that commit, either.")
                response = input("Would you like to try another start commit? (y/n): ")
                if not response.lower().startswith("y"):
                    break
    if len(rev_list) == 0:
        if len(git.query_rev_list(Configuration.START_COMMIT, Configuration.TRACKED_BRANCH, path_spec="", before=issue_timestamp)) > 0:
            print("Perhaps your path spec is too restrictive.")
            response = input("Would you like to continue without it? (y/n): ")
            if response.lower().startswith("y"):
                path_spec = ""
                rev_list = git.query_rev_list(Configuration.START_COMMIT, Configuration.TRACKED_BRANCH, before=issue_timestamp)
            else:
                print("Nothing to be done, then.")
                sys.exit(0)
        else:
            print("Nothing to be done, then.")
            sys.exit(0)

    latest_present_commit = next(
        commit for commit in rev_list[::-1] if commit in present_commits
    )
    current_revision = latest_present_commit
    latest_present_time = git.get_commit_time(latest_present_commit)
    latest_known_time = git.get_commit_time(rev_list[-1])
    time_since = time.time() - latest_known_time
    if time_since > WARN_TIME:
        print(f"Warning: The latest known commit is {int(time_since / 60 / 60 / 24)} days old.")
    time_since = time.time() - latest_present_time
    if time_since > WARN_TIME:
        print(f"Warning: The latest cached commit is {int(time_since / 60 / 60 / 24)} days old.")
        # check if the user would like to compile the latest one or use the cached one
        while not cache_only:
            response = input("Would you like to compile the latest commit to initially test against instead? (y/n): ")
            if response.lower().startswith("y"):
                current_revision = rev_list[-1]
                print(f"The latest commit will be compiled for testing before precompiled versions are used.")
            elif response.lower().startswith("n"):
                print("Using the cached commit instead. If you can't reproduce the issue, try using a newer commit.")

    remaining_revisions = list(rev_list)
    phase_two = False
    has_unstarted = False

    print_status_message(good_revisions, bad_revisions, skipped_revisions, remaining_revisions, current_revision)
    while True:
        try:
            command = input("bisect> ").strip()
            if not command:
                continue

            parts = command.split()
            cmd = parts[0].lower()
            args = parts[1:]

            # used letters: abeghlopqrsuv
            not_start = False
            if cmd == "s":
                if len(args) > 0:
                    not_start = True
                else:
                    print("No argument 's' is ambiguous between skip and status, use a longer prefix.")
                    continue
            if "autoopen".startswith(cmd) and not not_start:
                if started:
                    print("Automatic opening is already on, not sure what to do. Use 'pause' to stop it. Use 'open' to reopen the current commit.")
                    continue
                started = True
                print(f"Starting automatic testing. Launching {git.get_short_name(current_revision)}.")
                launch_any(current_revision, execution_parameters, present_commits, discard, cache_only, project)

            elif "pause".startswith(cmd):
                started = False

            elif any(phrase.startswith(cmd) for phrase in ["good", "bad", "skip", "unmark"]):
                bisect_commits, path_spec = _update_revision_sets_from_command(
                    parts, current_revision, good_revisions, bad_revisions, skipped_revisions, path_spec, issue_timestamp)
                if len(bisect_commits) == 0:
                    continue
                if len(bisect_commits) == 1:
                    remaining_revisions = [bisect_commits[0]]
                    break
                bisect_commit_set = set(bisect_commits)
                remaining_revisions = [commit for commit in rev_list if commit in bisect_commit_set]
                possible_next_commits = [commit for commit in bisect_commits if commit not in skipped_revisions]
                possible_present_commits = [commit for commit in possible_next_commits if commit in present_commits]
                phase_zero = len(good_revisions) == 0 or len(bad_revisions) == 0
                if not phase_zero:
                    if phase_two:
                        if len(possible_present_commits) > 0:
                            print("Precompiled commits are back inside the possible range.")
                            print("Switching back to searching precompiled commits.")
                            phase_two = False
                            possible_next_commits = possible_present_commits
                    else:
                        if len(possible_present_commits) > 0:
                            possible_next_commits = possible_present_commits
                        else:
                            print("No more useful precompiled commits to test.")
                            if cache_only:
                                print("Cache only mode, exiting.")
                                break
                            print("Switching to compiling versions as needed.")
                            phase_two = True
                if len(possible_next_commits) == 0:
                    print("No more commits to test.")
                    continue
                possible_unignored_commits = [commit for commit in possible_next_commits if commit not in ignored_commits]
                possible_unerrored_commits = [commit for commit in possible_next_commits if commit not in old_error_commits]
                possible_both_commits = [commit for commit in possible_unignored_commits if commit not in old_error_commits]
                if len(possible_unerrored_commits) == 0:
                    print("Every remaining commit failed to build in the past.")
                    print("Picking one to test next anyways, but errors are likely.")
                elif len(possible_unignored_commits) == 0:
                    print("Every remaining commit is in ignored_commits.")
                    print("Picking one to test next anyways, but it may be untestable.")
                elif len(possible_both_commits) == 0:
                    print("Every remaining commit is ignored or failed to build in the past.")
                else:
                    possible_next_commits = possible_both_commits
                if len(possible_both_commits) == 0 and not has_unstarted and started:
                    print("Disabling autoopen to avoid autocompiling untestable commits. It can be turned back on if you wanted it.")
                    started = False
                    has_unstarted = True
                current_revision = possible_next_commits[0]
                print_status_message(good_revisions, bad_revisions, skipped_revisions, remaining_revisions, current_revision)
                if started:
                    launch_any(current_revision, execution_parameters, present_commits, discard, cache_only, project)

            elif "open".startswith(cmd):
                if len(args) == 0:
                    if current_revision is None:
                        print("Invalid command: No arguments were provided but there is no current commit to use.")
                        continue
                    args = [current_revision]
                if len(args) != 1:
                    print("Invalid command: 'open' accepts at most one argument.")
                    continue
                resolved = git.resolve_ref(args[0])
                if resolved == "":
                    print(f"Invalid commit: {args[0]}")
                    continue
                if resolved in old_error_commits:
                    print("Warning: That commit has had compiler errors in the past. Trying to open anyways.")
                elif resolved in ignored_commits:
                    print("Warning: That commit is in ignored_commits. Trying to open anyways.")
                current_revision = resolved
                print("Opening commit", git.get_short_name(current_revision))
                launch_any(current_revision, execution_parameters, present_commits, discard, cache_only, project)

            elif "list".startswith(cmd):
                if len(good_revisions) == 0:
                    print("No good commits marked, can't calculate a revision list.")
                    continue
                elif len(bad_revisions) == 0:
                    print("No bad commits marked, can't calculate a revision list.")
                    continue
                bisect_commits = git.get_bisect_commits(good_revisions, bad_revisions, path_spec)
                args = {arg.lower() for arg in args}
                short = "--short" in args or "-s" in args
                if short:
                    print(" ".join(git.get_plain_short_name(commit)))
                else:
                    if len(bisect_commits) == 0:
                        print("No possible commits found.")
                        continue
                    print(f"Possible commits ({len(bisect_commits)}):")
                    for commit in bisect_commits:
                        print(git.get_short_log(commit))

            elif "status".startswith(cmd):
                print_status_message(good_revisions, bad_revisions, skipped_revisions, remaining_revisions, current_revision, long=True)

            elif "help".startswith(cmd):
                pass

            elif "exit".startswith(cmd) or "quit".startswith(cmd):
                break

            else:
                print(f"Unknown command: {cmd}. Type 'help' for a list of commands.")
        except KeyboardInterrupt:
            break
    print_exit_message(good_revisions, bad_revisions, skipped_revisions, remaining_revisions)


def _print_help(args: list[str]) -> None:
    print("Marking commands:")
    print("Marking commands may be combined on the same line.")
    print("If no commits are provided to them, the current commit is used.")
    print("  good [commit...]: Mark the given commit as good.")
    print("  bad [commit...]: Mark the given commit as bad.")
    print("  skip [commit...]: Mark the given commit as untestable.")
    print("  unmark [commit...]: Unmark the given commit (in case you made a mistake).")
    print()
    print("Testing commands:")
    print("  start: Automatically launch the next godot version to test.")
    print("  pause: Stop automatically launching godot versions.")
    print("  test [commit?]: Sets the current commit to. Launch the given commit to test. If no commit is given, the current commit is used.")
    print("  list: List all commits.")
    print("  visualize: Visualize the bisect process.")
    print("  help: Show this help message. Use help [command] for more info on a specific command.")
    print("  exit/quit: Exit bisect interactive mode.")
    print("good, bad, skip, and unmark can be combined onto the same line.")


def _update_revision_sets_from_command(command: list[str], current_commit: Optional[str], goods: set[str], bads: set[str], skips: set[str], path_spec: str, issue_timestamp: int) -> (list[str], str):
    sentences = []
    sentence = [command[0]]
    for arg in command[1:]:
        argi = arg.lower()
        if any(phrase.startswith(argi) for phrase in ["good", "bad", "skip", "unmark"]):
            sentences.append(sentence)
            sentence = [arg]
        else:
            sentence.append(arg)
    sentences.append(sentence)

    temp_goods = set(goods)
    temp_bads = set(bads)
    temp_skips = set(skips)
    new_goods = set()
    new_bads = set()
    new_skips = set()
    new_unmarkeds = set()
    new_sets = {
        "g": new_goods,
        "b": new_bads,
        "s": new_skips,
        "u": new_unmarkeds,
    }
    for sentence in sentences:
        if len(sentence) == 1:
            if current_commit is None:
                print(f"Invalid command: {sentence[0]} has no arguments but there is no current commit to use.")
                return [], path_spec
            sentence.append(current_commit)
        sentence_key = sentence[0][0].lower()
        commits = {git.resolve_ref(arg) for arg in sentence[1:]}
        for key, value in new_sets.items():
            if key != sentence_key and len(commits.intersection(value)) > 0:
                print(f"Invalid command: Some commits were marked multiple times.")
                return [], path_spec
        new_sets[sentence_key].update(commits)

    temp_goods.difference_update(new_unmarkeds)
    temp_bads.difference_update(new_unmarkeds)
    temp_skips.difference_update(new_unmarkeds)

    already_marked = set()
    for new_revisions, old_revisions in [
        (new_goods, temp_bads),
        (new_goods, temp_skips),
        (new_bads, temp_goods),
        (new_bads, temp_skips),
        (new_skips, temp_goods),
        (new_skips, temp_bads),
    ]:
        already_marked.update(new_revisions.intersection(old_revisions))
    if len(already_marked) > 0:
        if len(sentences) > 1 or len(sentences[0]) > 2:
            print(f"Warning: {len(already_marked)} of those commits were already marked as something else. Updating anyways.")
        else:
            print(f"Warning: That commit was already marked as something else. Updating anyways.")

    temp_goods.update(new_goods)
    temp_bads.update(new_bads)
    temp_skips.update(new_skips)

    if len(temp_bads) == 0:
        range_end = git.resolve_ref(Configuration.TRACKED_BRANCH)
        if range_end in temp_goods:
            print("The last commit in the range got marked as good, so there's no possible end point for the bisect. Ignoring command.")
            print("Perhaps the issue has already been fixed?")
            return [], path_spec
        print("No bad commits found yet. Using the latest to try finding one.")
        bisect_commits = rev_list
        for good in temp_goods:
            query_revs = set(git.query_rev_list(Configuration.START_COMMIT, good, path_spec=path_spec, before=issue_timestamp))
            bisect_commits = [commit for commit in bisect_commits if commit not in query_revs]
    elif len(temp_goods) == 0:
        if git.resolve_ref(Configuration.START_COMMIT) in temp_bads:
            print("The first commit in the range is marked as bad, so there's no possible start point for the bisect.")
            response = input("Would you like to set an earlier start commit? (y/n): ")
            if response.lower().startswith("y"):
                while True:
                    new_start_commit = input("Enter the new start commit: ")
                    if git.resolve_ref(new_start_commit) == "":
                        print(f"Invalid commit: {new_start_commit}")
                        continue
                    Configuration.START_COMMIT = new_start_commit
                    rev_list = git.query_rev_list(Configuration.START_COMMIT, Configuration.TRACKED_BRANCH, path_spec=path_spec, before=issue_timestamp)
                    if len(rev_list) > 0:
                        return rev_list, path_spec
                    
                    print("No matching commits found going back to that commit, either.")
                    response = input("Would you like to try another start commit? (y/n): ")
                    if not response.lower().startswith("y"):
                        break
            print("Nothing to be done, then.")
            sys.exit(0)
        print("No good commits found yet. Using early commits to try finding one.")
        bisect_commits = git.query_rev_list(Configuration.START_COMMIT, Configuration.TRACKED_BRANCH, path_spec=path_spec, before=issue_timestamp)
        for bad in temp_bads:
            query_revs = set(git.query_rev_list(Configuration.START_COMMIT, bad, path_spec=path_spec, before=issue_timestamp))
            bisect_commits = [commit for commit in bisect_commits if commit in query_revs]
    else:
        bisect_commits = git.get_bisect_commits(temp_goods, temp_bads, path_spec)
    if len(bisect_commits) == 0:
        if path_spec != "":
            print("That would result in no possible remaining commits.")
            bisect_commits = git.get_bisect_commits(temp_goods, temp_bads, "")
            if len(bisect_commits) > 0:
                response = input("Perhaps your path spec is too restrictive. Would you like to continue without it? (y/n): ")
                if response.lower().startswith("y"):
                    path_spec = ""
                else:
                    print("Ignoring command.")
                    return [], path_spec
            else:
                print("That would result in no possible remaining commits. Ignoring.")
                return [], path_spec
        else:
            print("That would result in no possible remaining commits. Ignoring.")
            return [], path_spec
    goods.difference_update(new_unmarkeds)
    bads.difference_update(new_unmarkeds)
    skips.difference_update(new_unmarkeds)
    goods.update(temp_goods)
    bads.update(temp_bads)
    skips.update(temp_skips)
    return bisect_commits, path_spec


def get_next_commits(good_commits: set[str], bad_commits: set[str], possible_commits: set[str], current_revision: str, path_spec: str) -> dict[str, str]:
    bad_commits2 = set(bad_commits)
    bad_commits2.add(current_revision)

    good_commits2 = set(good_commits)
    good_commits2.add(current_revision)
    return {
        "bad": get_next_commit(good_commits, bad_commits2, possible_commits, path_spec),
        "good": get_next_commit(good_commits2, bad_commits, possible_commits, path_spec),
    }


def get_next_commit(good_commits: set[str], bad_commits: set[str], possible_commits: set[str], path_spec: str) -> str:
    return next(
        commit
        for commit in git.get_bisect_commits(good_commits, tmp_set, path_spec)
        if commit in possible_commits
    )


def launch_any(commit: str, execution_parameters: str, cached_commits: set[str], discard: bool, cache_only: bool, wd: str = "") -> bool:
    if commit in storage.get_present_commits():
        return launch_cached(commit, execution_parameters, wd)
    
    if cache_only:
        print(f"Commit {git.get_short_name(commit)} is not cached. Skipping due to --cache-only.")
        return False
        
    if not factory.compile_uncached(commit):
        print(f"Failed to compile commit {git.get_short_name(commit)}.")
        return False

    executable_path = factory.get_compiled_path()
    result = launch(executable_path, execution_parameters, wd)
    if not discard:
        factory.cache()
    return result


def launch_cached(commit: str, execution_parameters: str, wd: str = "") -> bool:
    executable_path = os.path.join(TMP_DIR, Configuration.BINARY_NAME)
    if os.path.exists(executable_path):
        os.remove(executable_path)
    if not storage.extract_commit(commit, executable_path):
        print(f"Failed to extract commit {git.get_short_name(commit)}.")
        return False
    return launch(executable_path, execution_parameters, wd)


def launch(executable_path: str, execution_parameters: str, wd: str = "") -> bool:
    return terminal.execute_in_subwindow(
        command=[executable_path] + shlex.split(execution_parameters),
        title="godot", 
        rows=Configuration.SUBWINDOW_ROWS,
        eat_kill=True,
        cwd=wd,
    )