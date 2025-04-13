from typing import Optional
from .config import Configuration
import os
import sys

import mrp_manager
import storage
import git

_should_pause = False
_is_good = False
_is_bad = False


def _mark_good() -> None:
    # TODO close project if open
    _is_good = True


def _mark_bad() -> None:
    # TODO close project if open
    _is_bad = True


def print_exit_message(goods: set[str], bads: set[str], skips: set[str], remaining: set[str]) -> None:
    if len(remaining) == 1:
        print("Bad revision found:", bisect_commits[0])
        print("https://github.com/godotengine/godot/commit/" + bisect_commits[0])
        print(git.get_short_log(bisect_commits[0]))
    print("\nExiting bisect interactive mode.")
    if last_good_revision or last_bad_revision:
        print("Resume with " + (f"g {last_good_revision}" if last_good_revision else "") + \
            (f" b {last_bad_revision}" if last_bad_revision else ""))


def bisect(discard: bool, cache_only: bool, project: str, execution_parameters: str = "", verbose: bool = False) -> None:
    if Configuration.ENABLE_SHORTCUTS:
        import keyboard
        keyboard.add_hotkey(Configuration.MARK_GOOD_HOTKEY, _mark_good)
        keyboard.add_hotkey(Configuration.MARK_BAD_HOTKEY, _mark_bad)
    
    if execution_parameters == "":
        execution_parameters = "-e {PATH}"
    if "{PATH}" in execution_parameters:
        issue_number = mrp_manager.get_issue_number(project)
        if issue_number == -1:
            original_project = project
            if project.endswith(".zip"):
                project = mrp_manager.extract_mrp(project)
            project = mrp_manager.find_project_file(project)
            if not project:
                print(f"No project file found in {original_project}")
                sys.exit(1)
        else:
            mrp_zip = mrp_manager.get_mrp(issue_number)
            if not mrp_zip:
                print(f"No MRP found in issue #{issue_number}.")
                sys.exit(1)
            project = mrp_manager.extract_mrp(mrp_zip)
        if project.endswith("project.godot"):
            project = project[:-len("project.godot")]
        execution_parameters = execution_parameters.replace("{PATH}", project)

    print("Entering bisect interactive mode. Type 'help' for a list of commands.")
    started = False
    current_revision = None
    good_revisions = set()
    bad_revisions = set()
    skipped_revisions = set()
    present_commits = storage.get_present_commits()
    rev_list = storage.get_rev_list()
    remaining_revisions = list(rev_list)
    phase_two = False

    while True:
        try:
            command = input("bisect> ").strip()
            if not command:
                continue

            parts = command.split()
            cmd = parts[0].lower()
            args = parts[1:]

            # used letters: beghlqprstuv
            not_start = False
            if cmd == "s":
                if len(args) > 0:
                    not_start = True
                else:
                    print("No argument 's' is ambiguous between skip and start, use a longer prefix.")
                    continue
            if "start".startswith(cmd) and not not_start:
                started = True
                if current_revision is None:
                    if len(bad_revisions) == 0:
                        print("No bad revisions marked. Trying the latest, expects failure.")
                        cu
                        continue

            elif "pause".startswith(cmd):
                started = False

            elif any(phrase.startswith(cmd) for phrase in ["good", "bad", "skip", "unmark"]):
                bisect_commits = _update_revision_sets_from_command(
                    parts, current_revision, good_revisions, bad_revisions, skipped_revisions)
                if len(bisect_commits) == 0:
                    continue
                if len(bisect_commits) == 1:
                    remaining_revisions = [bisect_commits[0]]
                    break
                bisect_commit_set = set(bisect_commits)
                remaining_revisions = [commit for commit in rev_list if commit in bisect_commit_set]
                possible_next_commits = [commit for commit in bisect_commits if commit not in skipped_revisions]
                possible_present_commits = [commit for commit in possible_next_commits if commit in present_commits]
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
                            print_exit_message()
                            break
                        print("Switching to compiling versions as needed.")
                        phase_two = True
                if len(possible_next_commits) == 0:
                    print("No more commits to test.")
                    continue
                current_revision = possible_next_commits[0]
                print(f"Next commit to test: {git.get_short_name(current_revision)}")
                if started:
                    launch_commit(current_revision, execution_parameters)

            elif "test".startswith(cmd):
                if len(args) == 0:
                    if current_revision is None:
                        print("Invalid command: No arguments were provided but there is no current commit to use.")
                        continue
                    args = [current_revision]
                if len(args) != 1:
                    print("Invalid command: 'test' accepts at most one argument.")
                    continue
                if git.resolve_ref(args[0]) == "":
                    print(f"Invalid commit: {args[0]}")
                    continue
                current_revision = args[0]
                launch_commit(current_revision, execution_parameters)

            elif "list".startswith(cmd):
                pass

            elif "visualize".startswith(cmd):
                pass

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
    print("  test [commit?]: Sets the current commit to launch the given commit to test. If no commit is given, the current commit is used.")
    print("  list: List all commits.")
    print("  visualize: Visualize the bisect process.")
    print("  help: Show this help message. Use help [command] for more info on a specific command.")
    print("  exit/quit: Exit bisect interactive mode.")
    print("good, bad, skip, and unmark can be combined onto the same line.")


def _update_revision_sets_from_command(command: list[str], current_commit: Optional[str], goods: set[str], bads: set[str], skips: set[str]) -> list[str]:
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
                return
            sentence.append(current_commit)
        sentence_key = sentence[0][0].lower()
        for key, value in new_sets.items():
            if key != sentence_key and len(sentence[1:].intersection(value)) > 0:
                print(f"Invalid command: Some commits were marked multiple times.")
                return
        new_sets[sentence_key].update(sentence[1:])

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

    bisect_commits = git.get_bisect_commit(temp_goods, temp_bads)
    if len(bisect_commits) == 0:
        print("That would result in no possible remaining commits. Ignoring.")
        return []
    goods.difference_update(new_unmarkeds)
    bads.difference_update(new_unmarkeds)
    skips.difference_update(new_unmarkeds)
    goods.update(temp_goods)
    bads.update(temp_bads)
    skips.update(temp_skips)
    return bisect_commits


def launch_commit(commit: str, execution_parameters: str) -> bool:
    executable_path = os.path.join(storage.VERSIONS_DIR, commit)
    if not storage.extract_commit(commit, executable_path):
        print(f"Failed to extract commit {commit}.")
        return False
    os.system(f"{executable_path} {execution_parameters}")
    return True
    