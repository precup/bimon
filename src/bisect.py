import os
import shlex
import stat
import sys
import time
from math import log2
from pathlib import Path
from typing import Optional

import src.factory as factory
import src.git as git
import src.storage as storage
import src.terminal as terminal
from src.config import Configuration


def _unique_bads(bads: set[str]) -> set[str]:
    return set(
        commit for commit in bads 
        if all(
            not git.is_ancestor(test_commit, commit) 
            for test_commit in bads if commit != test_commit
        )
    )


def _unique_goods(goods: set[str]) -> set[str]:
    return set(
        commit for commit in goods
        if all(
            not git.is_ancestor(commit, test_commit) 
            for test_commit in goods if commit != test_commit
        )
    )


def _launch_any(commit: str, execution_parameters: str, cached_commits: set[str], discard: bool, cache_only: bool, wd: str = "") -> bool:
    resolved = git.resolve_ref(commit)
    if resolved != "":
        commit = resolved
    if commit in cached_commits:
        return _launch_cached(commit, execution_parameters, wd)
    
    if cache_only:
        print(f"Commit {git.get_short_name(commit)} is not cached. Skipping due to --cache-only.")
        return False
        
    if not factory.compile_uncached(commit):
        print(f"Failed to compile commit {git.get_short_name(commit)}.")
        return False

    executable_path = factory.get_compiled_path()
    result = False
    try:
        result = _launch(executable_path, execution_parameters, wd)
    finally:
        if not discard:
            factory.cache()
            cached_commits.add(commit)
    return result


def _launch_cached(commit: str, execution_parameters: str, wd: str = "") -> bool:
    executable_path = os.path.join(BisectRunner.TMP_DIR, Configuration.BINARY_NAME)
    if os.path.exists(executable_path):
        os.remove(executable_path)
    if not storage.extract_commit(commit, executable_path):
        print(f"Failed to extract commit {git.get_short_name(commit)}.")
        return False
    return _launch(executable_path, execution_parameters, wd)


def _launch(executable_path: str, execution_parameters: str, wd: str = "") -> bool:
    try:
        os.chmod(executable_path, os.stat(executable_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:
        print(e)
    executable_path = str(Path(executable_path).resolve())
    return terminal.execute_in_subwindow(
        command=[executable_path] + shlex.split(execution_parameters),
        title="godot", 
        rows=Configuration.SUBWINDOW_ROWS,
        eat_kill=True,
        cwd=wd,
    )


class BisectRunner:
    WARN_TIME = 60 * 60 * 24 * 7  # 1 week
    TMP_DIR = "tmp"

    def __init__(
            self, 
            discard: bool, 
            cache_only: bool, 
            ignore_date: bool, 
            execution_parameters: str, 
            path_spec: str, 
            issue_timestamp: int
        ) -> None:
        self._issue_timestamp = -1 if ignore_date else issue_timestamp
        self._path_spec = path_spec
        self._discard = discard
        self._cache_only = cache_only
        self._execution_parameters = execution_parameters

        self._present_commits = storage.get_present_commits()
        self._ignored_commits = storage.get_ignored_commits()
        self._old_error_commits = set() if Configuration.IGNORE_OLD_ERRORS else storage.get_compiler_error_commits()

        self._goods = set()
        self._bads = set()
        self._skips = set()
        self._started = False
        self._phase_two = False
        self._has_unstarted = False

        rev_list = self._rev_list()
        if len(rev_list) == 0:
            start_change_list = self._possibly_change_start_commit()
            if len(start_change_list) > 0:
                rev_list = start_change_list
            else:
                no_spec_list = self._possibly_remove_path_spec()
                if len(no_spec_list) > 0:
                    rev_list = no_spec_list
                else:
                    print("Nothing to be done, then.")
                    sys.exit(0)
        
        self._current_revision = None
        latest_present_commit = None
        if len(self._present_commits) > 0:
            for commit in rev_list[::-1]:
                if commit in self._present_commits:
                    self._current_revision = commit 
                    latest_present_commit = commit
                    break
        elif len(rev_list) > 0:
            self._current_revision = rev_list[-1]

        self._handle_time_warnings(rev_list, latest_present_commit)

        try:
            git.fetch()
        except:
            pass # it's just best effort to help resolve refs
        
        if not os.path.exists(BisectRunner.TMP_DIR):
            os.mkdir(BisectRunner.TMP_DIR)

        storage.init_decompress_queue()


    def _handle_time_warnings(self, rev_list: list[str], latest_present_commit: Optional[str]) -> None:
        latest_known_time = git.get_commit_time(rev_list[-1])
        time_since = time.time() - latest_known_time
        if time_since > BisectRunner.WARN_TIME:
            print(terminal.warn(f"The latest known commit is {int(time_since / 60 / 60 / 24)} days old."))
        
        if latest_present_commit is None:
            print(terminal.warn("No cached commits found in the range."))
            return

        latest_present_time = git.get_commit_time(latest_present_commit)
        time_since = time.time() - latest_present_time
        if time_since > BisectRunner.WARN_TIME:
            print(terminal.warn(f"The latest cached commit is {int(time_since / 60 / 60 / 24)} days old."))
            if not self._cache_only:
                response = input("Would you like to compile the latest commit to initially test against instead? [y/N]: ").strip().lower()
                if response.startswith("y"):
                    self._current_revision = rev_list[-1]
                    print("The latest commit will be compiled for testing before precompiled versions are used.")


    def _possibly_change_start_commit(self) -> list[str]:
        print("No matching commits found going back to the start of the commit range.")
        response = input("Would you like to set an earlier start commit? [y/N]: ").strip().lower()
        if response.startswith("y"):
            while True:
                new_start_commit = input("Enter a new start commit: ").strip()
                if git.resolve_ref(new_start_commit) == "":
                    print(f"Invalid commit: {new_start_commit}")
                    continue
                Configuration.RANGE_START = new_start_commit
                rev_list = self._rev_list()
                if len(rev_list) > 0:
                    return rev_list
                print("No matching commits found going back to that commit, either.")
                response = input("Would you like to try another start commit? [y/N]: ").strip().lower()
                if not response.startswith("y"):
                    return []
        return []


    def _possibly_remove_path_spec(self) -> list[str]:
        if self._path_spec != "":                
            no_spec_list = self._rev_list(path_spec="")
            if len(no_spec_list) > 0:
                print("Perhaps your path spec is too restrictive.")
                response = input("Would you like to continue without it? [y/N]: ")
                if response.lower().startswith("y"):
                    self._path_spec = ""
                    return no_spec_list

        return []


    def _rev_list(
            self, 
            start: Optional[str] = None, 
            end: Optional[str] = None, 
            path_spec: Optional[str] = None,
            before: Optional[int] = None
        ) -> list[str]:
        if start is None:
            start = Configuration.RANGE_START
        if end is None:
            end = Configuration.RANGE_END
        if path_spec is None:
            path_spec = self._path_spec
        if before is None:
            before = self._issue_timestamp
        return git.query_rev_list(
            start, 
            end, 
            path_spec=path_spec, 
            before=before,
        )


    def print_exit_message(self) -> None:
        remaining = set(self.get_bisect_commits(self._goods, self._bads, dry_run=True))
        temp_goods = _unique_goods(self._goods)
        temp_bads = _unique_bads(self._bads)
        if len(remaining) == 1:
            bad_commit = next(remaining)
            print("Only one revision left, must be " + git.get_short_name(bad_commit))
            print("https://github.com/godotengine/godot/commit/" + bad_commit)
            print(git.get_short_log(bad_commit))
        print("\nExiting bisect interactive mode.")
        if len(remaining) > 0 and (len(temp_goods) > 0 or len(temp_bads) > 0 or len(self._skips) > 0):
            print(f"There are {len(remaining)} remaining commits.")
            print("You can resume with:")
            if len(temp_goods) > 0:
                print(f"good {' '.join(temp_goods)}")
            if len(temp_bads) > 0:
                print(f"bad {' '.join(temp_bads)}")
            if len(self._skips) > 0:
                print(f"skip {' '.join(self._skips)}")


    def print_status_message(self, long: bool = False) -> None:
        remaining = set(self.get_bisect_commits(self._goods, self._bads, dry_run=True))
        merged = set(remaining)
        merged.update(_unique_bads(self._bads))
        steps_left = int(log2(len(merged)))
        if len(self._goods) == 0:
            steps_left += 1
        if len(self._bads) == 0:
            steps_left += 1
        print(f"Approximately {steps_left} tests remaining. Next commit to test:")
        if self._current_revision is None:
            print("No current revision set.")
        else:
            print(git.get_short_log(self._current_revision))
        if long and (len(self._goods) > 0 or len(self._bads) > 0 or len(self._skips) > 0):
            temp_goods = _unique_goods(self._goods)
            temp_bads = _unique_bads(self._bads)
            print("Minimal sets of marked commits:")
            if len(temp_goods) > 0:
                print(f"good {' '.join(temp_goods)}")
            if len(temp_bads) > 0:
                print(f"bad {' '.join(temp_bads)}")
            if len(self._skips) > 0:
                print(f"skip {' '.join(self._skips)}")


    def queue_decompress_nexts(self) -> None:
        relevant_commits = []
        layers = Configuration.BACKGROUND_DECOMPRESSION_LAYERS

        queue = [(self._current_revision, 0, set(), set())]
        while queue:
            current_commit, current_layer, inherited_goods, inherited_bads = queue.pop(0)

            if current_layer >= layers:
                break

            new_goods = inherited_goods | {current_commit}
            new_good_commit = self.get_next_revision(new_goods=new_goods, new_bads=inherited_bads)
            if new_good_commit and new_good_commit not in relevant_commits:
                relevant_commits.append(new_good_commit)
                queue.append((new_good_commit, current_layer + 1, new_goods, inherited_bads))

            new_bads = inherited_bads | {current_commit}
            new_bad_commit = self.get_next_revision(new_goods=inherited_goods, new_bads=new_bads)
            if new_bad_commit and new_bad_commit not in relevant_commits:
                relevant_commits.append(new_bad_commit)
                queue.append((new_bad_commit, current_layer + 1, inherited_goods, new_bads))

        storage.set_decompress_queue(relevant_commits)


    def dry_print(self, dry_run: bool, *args, **kwargs):
        if not dry_run:
            print(*args, **kwargs)


    def get_new_start_commit(self) -> list[str]:
        print("The first commit in the range is marked as bad, so there's no possible start point for the bisect.")
        response = input("Would you like to set an earlier start commit? [y/N]: ").strip().lower()
        if response.startswith("y"):
            while True:
                new_start_commit = input("Enter the new start commit: ").strip()
                resolved = git.resolve_ref(new_start_commit)
                if resolved == "":
                    print(f"Invalid commit: {new_start_commit}")
                    continue
                if resolved in self._bads:
                    print(f"Invalid commit: {new_start_commit} is already marked as bad.")
                    continue
                if any(git.is_ancestor(bad, resolved) for bad in self._bads):
                    print(f"Invalid commit: {new_start_commit} is the descendant of a bad commit.")
                    continue

                Configuration.RANGE_START = new_start_commit
                rev_list = self._rev_list()
                if len(rev_list) > 0:
                    return rev_list
                
                print("No matching commits found going back to that commit, either.")
                response = input("Would you like to try another start commit? [y/N]: ").strip().lower()
                if not response.startswith("y"):
                    return []


    def get_bisect_commits(
            self,
            goods: set[str],
            bads: set[str],
            dry_run: bool = False,
        ) -> list[str]:
        if len(bads) == 0:
            range_end = git.resolve_ref(Configuration.RANGE_END)
            if range_end in goods:
                self.dry_print(dry_run, "The last commit in the range got marked as good, so there's no possible end point for the bisect. Ignoring command.")
                self.dry_print(dry_run, "Perhaps the issue has already been fixed?")
                return []
            self.dry_print(dry_run, "No bad commits found yet. Using the latest to try finding one.")
            bisect_commits = self._rev_list()
            for good in goods:
                query_revs = set(self._rev_list(end=good))
                bisect_commits = [commit for commit in bisect_commits if commit not in query_revs]
            return bisect_commits

        elif len(goods) == 0:
            if git.resolve_ref(Configuration.RANGE_START) in bads:
                if dry_run:
                    return []
                bisect_commits = self.get_new_start_commit()
                if len(bisect_commits) == 0:
                    print("Nothing to be done, then.")
                    sys.exit(0)
                else:
                    return bisect_commits
            self.dry_print(dry_run, "No good commits found yet. Using early commits to try finding one.")
            bisect_commits = self._rev_list()
            for bad in bads:
                query_revs = set(self._rev_list(end=bad))
                bisect_commits = [commit for commit in bisect_commits if commit in query_revs]
            return bisect_commits

        else:
            return git.get_bisect_commits(goods, bads, path_spec=self._path_spec, before=self._issue_timestamp)


    # TODO some way to return exit vs continue
    def get_next_revision(
            self,
            new_goods: set[str] = set(),
            new_bads: set[str] = set(),
            new_skips: set[str] = set(),
            new_unmarks: set[str] = set(),
            dry_run: bool = True,
        ) -> Optional[str]:
        if len(new_goods) == 0 and len(new_bads) == 0 and len(new_skips) == 0 and len(new_unmarks) == 0:
            return self._current_revision

        temp_goods = set(self._goods)
        temp_bads = set(self._bads)
        temp_skips = set(self._skips)

        temp_goods.difference_update(new_unmarks)
        temp_bads.difference_update(new_unmarks)
        temp_skips.difference_update(new_unmarks)

        temp_goods.update(new_goods)
        temp_bads.update(new_bads)
        temp_skips.update(new_skips)

        bisect_commits = self.get_bisect_commits(temp_goods, temp_bads, dry_run)

        if len(bisect_commits) == 0:
            if dry_run:
                return None
                
            if self._path_spec != "":
                print("That would result in no possible remaining commits.")
                bisect_commits = self._possibly_remove_path_spec()

            if len(bisect_commits) == 0:
                print("That would result in no possible remaining commits. Ignoring.")
                return None

        if not dry_run:
            self._goods.difference_update(new_unmarks)
            self._bads.difference_update(new_unmarks)
            self._skips.difference_update(new_unmarks)

            self._goods.update(temp_goods)
            self._bads.update(temp_bads)
            self._skips.update(temp_skips)

        if len(bisect_commits) == 1:
            return bisect_commits[0]
            # TODO signal that it's time to stop?
        
        possible_next_commits = [commit for commit in bisect_commits if commit not in temp_skips]
        possible_present_commits = [commit for commit in possible_next_commits if commit in self._present_commits]
        phase_zero = len(temp_goods) == 0 or len(temp_bads) == 0
        if dry_run:
            # TODO why is this no phase_zero? isn't that fine?
            if not phase_zero and len(possible_present_commits) > 0:
                possible_next_commits = possible_present_commits
        elif not phase_zero:
            if self._phase_two:
                if len(possible_present_commits) > 0:
                    print("Precompiled commits are back inside the possible range.")
                    print("Switching back to searching precompiled commits.")
                    self._phase_two = False
                    possible_next_commits = possible_present_commits
            elif len(possible_present_commits) > 0:
                possible_next_commits = possible_present_commits
            else:
                print("No more useful precompiled commits to test.")
                if self._cache_only:
                    print("Cache only mode, exiting.")
                    return None
                print("Switching to compiling versions as needed.")
                self._phase_two = True

        if len(possible_next_commits) == 0:
            self.dry_print(dry_run, "No more commits to test.")
            return None

        possible_next_commits = self._filter_ignored_errored(possible_next_commits, dry_run)
        return possible_next_commits[0]


    def _filter_ignored_errored(self, possible_next_commits: list[str], dry_run: bool) -> list[str]:
        possible_unignored_commits = [commit for commit in possible_next_commits if commit not in self._ignored_commits]
        possible_unerrored_commits = [commit for commit in possible_next_commits if commit not in self._old_error_commits]
        possible_both_commits = [commit for commit in possible_unignored_commits if commit not in self._old_error_commits]
        
        if len(possible_unerrored_commits) == 0:
            self.dry_print(dry_run, "Every remaining commit failed to build in the past.")
            self.dry_print(dry_run, "Picking one to test next anyways, but errors are likely.")
        elif len(possible_unignored_commits) == 0:
            self.dry_print(dry_run, "Every remaining commit is in ignored_commits.")
            self.dry_print(dry_run, "Picking one to test next anyways, but it may be untestable.")
        elif len(possible_both_commits) == 0:
            self.dry_print(dry_run, "Every remaining commit is ignored or failed to build in the past.")
        else:
            possible_next_commits = possible_both_commits

        if not dry_run and len(possible_both_commits) == 0 and not self._has_unstarted and self._started:
            print("Disabling autoopen to avoid autocompiling untestable commits. It can be turned back on if you wanted it.")
            self._started = False
            self._has_unstarted = True

        return possible_next_commits


    def _get_sets_from_command(self, command: list[str]) -> (set[str], set[str], set[str], set[str]):
        commands = ["good", "bad", "skip", "unmark"]
        new_sets = {command[0]: set() for commands in commands}

        sentences = []
        sentence = [command[0]]
        for arg in command[1:]:
            argi = arg.lower().strip()
            if argi == "":
                continue
            if any(phrase.startswith(argi) for phrase in commands):
                sentences.append(sentence)
                sentence = [arg]
            else:
                sentence.append(arg)
        sentences.append(sentence)

        for sentence in sentences:
            if len(sentence) == 1:
                if self._current_revision is None:
                    print(f"Invalid command: {sentence[0]} has no arguments but there is no current commit to use.")
                    return (set(), set(), set(), set())
                sentence.append(self._current_revision)
            sentence_key = sentence[0][0].lower()
            commits = {git.resolve_ref(arg) for arg in sentence[1:]}
            for key, value in new_sets.items():
                if key != sentence_key and len(commits.intersection(value)) > 0:
                    print("Invalid command: Some commits were marked multiple times.")
                    return (set(), set(), set(), set())
            new_sets[sentence_key].update(commits)

        already_marked = set()
        for new_revisions, old_revisions in [
            (self._goods, new_sets["bad"]),
            (self._goods, new_sets["skip"]),
            (self._bads, new_sets["good"]),
            (self._bads, new_sets["skip"]),
            (self._skips, new_sets["good"]),
            (self._skips, new_sets["bad"]),
        ]:
            already_marked.update(new_revisions.intersection(old_revisions))
        already_marked.difference_update(new_sets["unmark"])
        if len(already_marked) > 0:
            if len(sentences) > 1 or len(sentences[0]) > 2:
                print(f"Warning: {len(already_marked)} of those commits were already marked as something else. Updating anyways.")
            else:
                print("Warning: That commit was already marked as something else. Updating anyways.")

        return tuple(new_sets[command[0]] for command in commands)


    def _launch(self) -> bool:
        return _launch_any(
            self._current_revision,
            self._execution_parameters,
            self._present_commits,
            self._discard,
            self._cache_only
        )


    def process_command(self, command: list[str]) -> bool:
        cmd = command[0].lower()
        args = command[1:]

        # used letters: abeghlopqrsuv
        not_start = False
        if cmd == "s":
            if len(args) > 0:
                not_start = True
            else:
                print("No argument 's' is ambiguous between skip and status, use a longer prefix.")
                return True

        if "autoopen".startswith(cmd) and not not_start:
            self.autoopen_command(True)
        elif "pause".startswith(cmd):
            self.autoopen_command(False)
        elif any(phrase.startswith(cmd) for phrase in ["good", "bad", "skip", "unmark"]):
            next_revision = self.get_next_revision(*self._get_sets_from_command(command), dry_run=False)
            if next_revision:
                self._current_revision = next_revision
                self.queue_decompress_nexts()
                self.print_status_message()
                if self._started:
                    self._launch()
        elif "open".startswith(cmd):
            if len(args) > 1:
                print("Invalid command: 'open' accepts at most one argument.")
                return True
            self.open_command(args[0] if len(args) > 0 else None)
        elif "list".startswith(cmd):
            args = [arg.lower() for arg in args]
            short = any("--short".startswith(arg) for arg in args if len(arg) > 2) or "-s" in args
            self.list_command(short)
        elif "status".startswith(cmd):
            self.print_status_message(long=True)
        elif "help".startswith(cmd):
            subcommand = args[0].lower() if len(args) > 0 else None
            self.help_command(subcommand)
        elif "exit".startswith(cmd) or "quit".startswith(cmd):
            return False
        else:
            print(f"Unknown command: {cmd}. Type 'help' for a list of commands.")

        return True


    def _command_completer(self, text: str, state: int) -> Optional[str]:
        commands = [
            "autoopen", "pause", "good", "bad",
            "skip", "unmark", "open", "list",
            "status", "help", "exit", "quit",
        ]
        matches = [cmd for cmd in commands if cmd.startswith(text)]
        return matches[state] if state < len(matches) else None


    def run(self) -> None:
        if self._current_revision is None:
            return

        print("Entering bisect interactive mode. Type 'help' for a list of commands.")
        self.queue_decompress_nexts()
        self.print_status_message()
        terminal.set_command_completer(self._command_completer)

        while True:
            try:
                command = input("bisect> ").strip()
                if command == "":
                    continue
                terminal.add_to_history(command)
                if not self.process_command(shlex.split(command)):
                    break
            except KeyboardInterrupt:
                break

        self.print_exit_message()


    def autoopen_command(self, on: bool) -> bool:
        if on:
            prefix = "Starting automatic testing. " if not self._started else ""
            self._started = True
            print(prefix + f"Launching {git.get_short_name(self._current_revision)}.")
            return self._launch()
        else:
            self._started = False


    def open_command(self, commit: Optional[str]) -> bool:
        if commit is None:
            if self._current_revision is None:
                print("Invalid command: No arguments were provided but there is no current commit to use.")
                return False
            commit = self._current_revision
        resolved = git.resolve_ref(commit)
        if resolved == "":
            print(f"Invalid commit: {commit}")
            return False
        if resolved in self._old_error_commits:
            print("Warning: That commit has had compiler errors in the past. Trying to open anyways.")
        elif resolved in storage.get_ignored_commits():
            print("Warning: That commit is in ignored_commits. Trying to open anyways.")
        self._current_revision = resolved
        self.queue_decompress_nexts()
        print("Opening commit", git.get_short_name(commit))
        return self._launch()


    def list_command(self, short: bool) -> bool:
        if len(self._goods) == 0:
            print("No good commits marked, can't calculate a revision list.")
            return False
        elif len(self._bads) == 0:
            print("No bad commits marked, can't calculate a revision list.")
            return False

        bisect_commits = git.get_bisect_commits(self._goods, self._bads)
        if short:
            print(" ".join([git.get_plain_short_name(commit) for commit in bisect_commits]))
        else:
            if len(bisect_commits) == 0:
                print("No possible commits found.")
                return False
            print(f"Possible commits ({len(bisect_commits)}):")
            for commit in bisect_commits:
                print(git.get_short_log(commit))
        return True


    def help_command(self, subcommand: Optional[str]) -> bool:
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
        return True