import os
import shlex
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


def _minimal_bads(bads: set[str]) -> set[str]:
    return set(
        commit for commit in bads 
        if all(
            not git.is_ancestor(test_commit, commit) 
            for test_commit in bads if commit != test_commit
        )
    )


def _minimal_goods(goods: set[str]) -> set[str]:
    return set(
        commit for commit in goods
        if all(
            not git.is_ancestor(commit, test_commit) 
            for test_commit in goods if commit != test_commit
        )
    )


def _find_executable(base_folder: str, likely_location: str, backup_path_regex: str) -> Optional[str]:
    likely_location = os.path.join(base_folder, likely_location)
    if os.path.exists(likely_location):
        return likely_location

    for root, _, files in os.walk(base_folder):
        for file in files:
            full_path = os.path.join(root, file)
            if backup_path_regex.match(full_path):
                return full_path

    return None


def _launch_any(ref: str, execution_parameters: str, present_versions: set[str], discard: bool, cached_only: bool, wd: str = "") -> bool:
    commit = git.resolve_ref(ref)
    if commit == "":
        print(f"Invalid ref: \"{ref}\" could not be resolved.")
        return False

    if commit not in present_versions:
        if cached_only:
            print(f"Commit {git.get_short_name(commit)} is not cached. Skipping due to --cached-only.")
            return False
            
        if not factory.compile_uncached(commit):
            print(f"Failed to compile commit {git.get_short_name(commit)}.")
            return False

        if discard:
            return _launch(Configuration.WORKSPACE_PATH, execution_parameters, wd)
        
        factory.cache()
        present_versions.add(commit)

    return _launch_cached(commit, execution_parameters, wd)


def _launch_cached(commit: str, execution_parameters: str, wd: str = "") -> bool:
    if not storage.extract_commit(commit):
        print(f"Failed to extract commit {git.get_short_name(commit)}.")
        return False
    
    return _launch(os.join(storage.VERSIONS_DIR, commit), execution_parameters, wd)


def _launch(workspace_path: str, execution_parameters: str, wd: str = "") -> bool:
    executable_path = _find_executable(workspace_path, Configuration.EXECUTABLE_PATH, Configuration.BACKUP_EXECUTABLE_REGEX)
    executable_path = str(Path(executable_path).resolve())

    return terminal.execute_in_subwindow(
        command=[executable_path] + shlex.split(execution_parameters),
        title="godot", 
        rows=Configuration.SUBWINDOW_ROWS,
        eat_kill=True,
        cwd=wd,
    )


class Bisect:
    WARN_TIME: int = 60 * 60 * 24 * 7
    TMP_DIR: str = "tmp"

    def to_file(self) -> str:
        pass


    @staticmethod
    def from_file(filepath: str) -> Bisect:
        pass


    def __init__(
            self, 
            discard: bool, 
            cached_only: bool, 
            execution_parameters: str, 
            path_spec: str, 
            end_timestamp: int,
            wd: str = "",
            initial_goods: set[str] = set(),
            initial_bads: set[str] = set(),
            initial_skips: set[str] = set(),
        ) -> None:
        self._end_timestamp = end_timestamp
        self._path_spec = path_spec
        self._discard = discard
        self._cache_only = cached_only
        self._execution_parameters = execution_parameters

        self._present_versions = storage.get_present_versions()
        self._ignored_commits = storage.get_ignored_commits()
        self._old_error_commits = set() if Configuration.IGNORE_OLD_ERRORS else storage.get_compiler_error_commits()

        self._goods = set()
        self._bads = set()
        self._skips = set()
        self._started = False
        self._phase_two = False
        self._has_unstarted = False
        self._wd = wd

        commit_list = self._commit_list()
        if len(commit_list) == 0:
            start_change_list = self._possibly_change_start_commit()
            if len(start_change_list) > 0:
                commit_list = start_change_list
            else:
                no_spec_list = self._possibly_remove_path_spec()
                if len(no_spec_list) > 0:
                    commit_list = no_spec_list
                else:
                    commit_list = []
                    print("Nothing to be done, then.")
                    sys.exit(0)
        
        latest_present_version = None
        if len(self._present_versions) > 0:
            for commit in commit_list[::-1]:
                if commit in self._present_versions:
                    latest_present_version = commit
                    break

        self._handle_time_warnings(commit_list, latest_present_version)
        
        if not os.path.exists(Bisect.TMP_DIR):
            os.mkdir(Bisect.TMP_DIR)

        storage.init_decompress_queue()

        self._current_commit = self.add_commit_sets(initial_goods, initial_bads, initial_skips)


    def _handle_time_warnings(self, commit_list: list[str], latest_present_version: Optional[str]) -> None:
        if len(commit_list) > 0:
            latest_known_time = git.get_commit_time(commit_list[-1])
            time_since = time.time() - latest_known_time
            if time_since > Bisect.WARN_TIME:
                print(terminal.warn(f"The latest known commit is {int(time_since / 60 / 60 / 24)} days old."))
        
        if latest_present_version is None:
            if len(commit_list) > 0:
                print(terminal.warn("No cached version found in the range."))
            return

        latest_present_time = git.get_commit_time(latest_present_version)
        time_since = time.time() - latest_present_time
        if time_since > Bisect.WARN_TIME:
            print(terminal.warn(f"The latest cached version is {int(time_since / 60 / 60 / 24)} days old."))
            if not self._cache_only and len(commit_list) > 0:
                response = input("Would you like to compile the latest commit to initially test against instead? [y/N]: ").strip().lower()
                if response.startswith("y"):
                    self._current_commit = commit_list[-1]
                    print("The latest commit will be compiled for testing before precompiled versions are used.")


    def _possibly_change_start_commit(self) -> list[str]:
        print("No matching commits found going back to the start of the range.")
        response = input("Would you like to set an earlier start point? [y/N]: ").strip().lower()
        if response.startswith("y"):
            while True:
                new_start_ref = input("Enter a new start point: ").strip()
                if git.resolve_ref(new_start_ref) == "":
                    print(f"Invalid commit: {new_start_ref}")
                    continue
                Configuration.RANGE_START = new_start_ref
                commit_list = self._commit_list()
                if len(commit_list) > 0:
                    return commit_list
                print("No matching commits found going back to that start point, either.")
                response = input("Would you like to try another start point? [y/N]: ").strip().lower()
                if not response.startswith("y"):
                    return []
        return []


    def _possibly_remove_path_spec(self) -> list[str]:
        if self._path_spec != "":                
            no_spec_list = self._commit_list(path_spec="")
            if len(no_spec_list) > 0:
                print("Perhaps your path spec is too restrictive.")
                response = input("Would you like to continue without it? [y/N]: ")
                if response.lower().startswith("y"):
                    self._path_spec = ""
                    return no_spec_list

        return []


    def _commit_list(
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
            before = self._end_timestamp
        return git.get_commit_list(
            start, 
            end, 
            path_spec=path_spec, 
            before=before,
        )


    def print_exit_message(self) -> None:
        remaining = set(self.get_bisect_commits(self._goods, self._bads, dry_run=True))
        if len(remaining) == 1:
            bad_commit = next(remaining)
            print("Only one commit left, must be " + git.get_short_name(bad_commit))
            print("https://github.com/godotengine/godot/commit/" + bad_commit)
            print(git.get_short_log(bad_commit))

        print("\nExiting bisect interactive mode.")

        if len(remaining) > 1:
            if len(self._goods) > 0 and len(self._bads) > 0:
                print(f"There are {len(remaining)} remaining possible commits.")
            if len(self._goods | self._bads | self._skips) > 0:
                print("You can resume with:")
                self.print_resume_sets()


    def print_status_message(self, long: bool = False) -> None:
        remaining = set(self.get_bisect_commits(self._goods, self._bads, dry_run=True))
        steps_left = int(log2(len(remaining | _minimal_bads(self._bads))))
        steps_left += len(self._goods) == 0
        steps_left += len(self._bads) == 0
        print(f"Approximately {steps_left} tests remaining. Next commit to test:")

        if self._current_commit is None:
            print("No current commit set.")
        else:
            print(git.get_short_log(self._current_commit))

        if long and len(self._goods | self._bads | self._skips) > 0:
            print("Minimal sets of marked commits:")
            self.print_resume_sets()


    def print_resume_sets(self) -> None:
        for commit_set, name in [
            (_minimal_goods(self._goods), "good"),
            (_minimal_bads(self._bads), "bad"),
            (self._skips, "skip"),
        ]:
            if len(commit_set) > 0:
                commit_set = {git.get_short_name(commit) for commit in commit_set}
                print(f"{name} {' '.join(git.get_short_name(commit) for commit in commit_set)}")


    def queue_decompress_nexts(self) -> None:
        to_decompress = []
        layers = Configuration.BACKGROUND_DECOMPRESSION_LAYERS

        queue = [(self._current_commit, 0, set(), set())]
        while queue:
            current_commit, current_layer, inherited_goods, inherited_bads = queue.pop(0)
            if current_layer >= layers:
                break

            new_goods = inherited_goods | {current_commit}
            good_next_commit = self.add_commit_sets(new_goods=new_goods, new_bads=inherited_bads, dry_run=True)
            if good_next_commit is not None and good_next_commit not in to_decompress:
                to_decompress.append(good_next_commit)
                queue.append((good_next_commit, current_layer + 1, new_goods, inherited_bads))

            new_bads = inherited_bads | {current_commit}
            bad_next_commit = self.add_commit_sets(new_goods=inherited_goods, new_bads=new_bads, dry_run=True)
            if bad_next_commit is not None and bad_next_commit not in to_decompress:
                to_decompress.append(bad_next_commit)
                queue.append((bad_next_commit, current_layer + 1, inherited_goods, new_bads))

        storage.set_decompress_queue(to_decompress)


    def quiet_print(self, dry_run: bool, *args, **kwargs):
        if not dry_run:
            print(*args, **kwargs)


    def get_new_start_commit(self) -> list[str]:
        print("The first commit in the range is marked as bad, so there's no possible start point for the bisect.")
        response = input("Would you like to set an earlier start commit? [y/N]: ").strip().lower()
        if response.startswith("y"):
            while True:
                new_start_ref = input("Enter the new start point: ").strip()
                new_start_commit = git.resolve_ref(new_start_ref)
                if new_start_commit == "":
                    print(f"Invalid ref: {new_start_ref}")
                    continue
                if new_start_commit in self._bads:
                    print(f"Invalid commit: {new_start_ref} is already marked as bad.")
                    continue
                if any(git.is_ancestor(bad, new_start_commit) for bad in self._bads):
                    print(f"Invalid commit: {new_start_ref} is the descendant of a bad commit.")
                    continue

                Configuration.RANGE_START = new_start_ref
                commit_list = self._commit_list()
                if len(commit_list) > 0:
                    return commit_list
                
                print("No matching commits found going back to that commit, either.")
                response = input("Would you like to try another start commit? [y/N]: ").strip().lower()
                if not response.startswith("y"):
                    return []


    def get_bisect_commits(
            self,
            goods: set[str],
            bads: set[str],
            silent: bool = False,
        ) -> list[str]:
        if len(bads) == 0:
            range_end = git.resolve_ref(Configuration.RANGE_END)
            if range_end in goods:
                self.quiet_print(silent, "The last commit in the range got marked as good, so there's no possible end point for the bisect. Ignoring command.")
                self.quiet_print(silent, "Perhaps the issue has already been fixed?")
                return []
            self.quiet_print(silent, "No bad commits found yet. Using the latest to try finding one.")
            bisect_commits = self._commit_list()
            for good in goods:
                # TODO this could be done via arguments to the initial rev-list
                commit_set = set(self._commit_list(end=good))
                bisect_commits = [commit for commit in bisect_commits if commit not in commit_set]
            return bisect_commits

        elif len(goods) == 0:
            if git.resolve_ref(Configuration.RANGE_START) in bads:
                if silent:
                    return []
                bisect_commits = self.get_new_start_commit()
                if len(bisect_commits) == 0:
                    print("Nothing to be done, then.")
                    sys.exit(0)
                else:
                    return bisect_commits
            self.quiet_print(silent, "No good commits found yet. Using early commits to try finding one.")
            bisect_commits = self._commit_list()
            for bad in bads:
                # TODO this could be done via arguments to the initial rev-list
                # TODO this seems straight up wrong
                commit_list = set(self._commit_list(end=bad))
                bisect_commits = [commit for commit in bisect_commits if commit in commit_set]
            return bisect_commits

        else:
            return git.get_bisect_commits(goods, bads, path_spec=self._path_spec, before=self._end_timestamp)


    # TODO some way to return exit vs continue
    def add_commit_sets(
            self,
            new_goods: set[str] = set(),
            new_bads: set[str] = set(),
            new_skips: set[str] = set(),
            new_unmarks: set[str] = set(),
            dry_run: bool = False,
        ) -> Optional[str]:
        if len(new_goods) == 0 and len(new_bads) == 0 and len(new_skips) == 0 and len(new_unmarks) == 0:
            return self._current_commit

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
        possible_present_versions = [commit for commit in possible_next_commits if commit in self._present_versions]
        phase_zero = len(temp_goods) == 0 or len(temp_bads) == 0
        if dry_run:
            # TODO why is this no phase_zero? isn't that fine?
            if not phase_zero and len(possible_present_versions) > 0:
                possible_next_commits = possible_present_versions
        elif not phase_zero:
            if self._phase_two:
                if len(possible_present_versions) > 0:
                    print("Precompiled commits are back inside the possible range.")
                    print("Switching back to searching precompiled commits.")
                    self._phase_two = False
                    possible_next_commits = possible_present_versions
            elif len(possible_present_versions) > 0:
                possible_next_commits = possible_present_versions
            else:
                print("No more useful precompiled commits to test.")
                if self._cache_only:
                    print("Cache only mode, exiting.")
                    return None
                print("Switching to compiling versions as needed.")
                self._phase_two = True

        if len(possible_next_commits) == 0:
            self.quiet_print(dry_run, "No more commits to test.")
            return None

        possible_next_commits = self._filter_ignored_errored(possible_next_commits, dry_run)
        return possible_next_commits[0]


    def _filter_ignored_errored(self, possible_next_commits: list[str], dry_run: bool) -> list[str]:
        possible_unignored_commits = [commit for commit in possible_next_commits if commit not in self._ignored_commits]
        possible_unerrored_commits = [commit for commit in possible_next_commits if commit not in self._old_error_commits]
        possible_both_commits = [commit for commit in possible_unignored_commits if commit not in self._old_error_commits]
        
        if len(possible_unerrored_commits) == 0:
            self.quiet_print(dry_run, "Every remaining commit failed to build in the past.")
            self.quiet_print(dry_run, "Picking one to test next anyways, but errors are likely.")
        elif len(possible_unignored_commits) == 0:
            self.quiet_print(dry_run, "Every remaining commit is in ignored_commits.")
            self.quiet_print(dry_run, "Picking one to test next anyways, but it may be untestable.")
        elif len(possible_both_commits) == 0:
            self.quiet_print(dry_run, "Every remaining commit is ignored or failed to build in the past.")
        else:
            possible_next_commits = possible_both_commits

        if not dry_run and len(possible_both_commits) == 0 and not self._has_unstarted and self._started:
            print("Disabling autoopen to avoid autocompiling untestable commits. It can be turned back on if you wanted it.")
            self._started = False
            self._has_unstarted = True

        return possible_next_commits


    def _get_sets_from_command(self, command: list[str]) -> tuple[set[str], set[str], set[str], set[str]]:
        commands = ["good", "bad", "skip", "unmark"]
        new_sets = {command[0]: set() for command in commands}

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
                if self._current_commit is None:
                    print(f"Invalid command: {sentence[0]} has no arguments but there is no current commit to use.")
                    return (set(), set(), set(), set())
                sentence.append(self._current_commit)
            sentence_key = sentence[0][0].lower()
            commits = {git.resolve_ref(ref) for ref in sentence[1:]}
            for commit in commits:
                if commit == "":
                    print(f"Unresolvable ref(s): {" ".join(ref for ref in sentence[1:] if git.resolve_ref(ref) == "")}")
                    return (set(), set(), set(), set())
            for key, value in new_sets.items():
                if key != sentence_key and len(commits.intersection(value)) > 0:
                    print("Invalid command: Some commits were marked multiple times.")
                    return (set(), set(), set(), set())
            new_sets[sentence_key].update(commits)

        already_marked = set()
        for new_commits, old_commits in [
            (self._goods, new_sets["bad"]),
            (self._goods, new_sets["skip"]),
            (self._bads, new_sets["good"]),
            (self._bads, new_sets["skip"]),
            (self._skips, new_sets["good"]),
            (self._skips, new_sets["bad"]),
        ]:
            already_marked.update(new_commits.intersection(old_commits))
        already_marked.difference_update(new_sets["unmark"])
        if len(already_marked) > 0:
            if len(sentences) > 1 or len(sentences[0]) > 2:
                print(f"Warning: {len(already_marked)} of those commits were already marked as something else. Updating anyways.")
            else:
                print("Warning: That commit was already marked as something else. Updating anyways.")

        return tuple(new_sets[command[0]] for command in commands)


    def _launch(self) -> bool:
        return _launch_any(
            self._current_commit,
            self._execution_parameters,
            self._present_versions,
            self._discard,
            self._cache_only,
            self._wd,
        )


    def process_command(self, command: list[str]) -> bool:
        cmd = command[0].lower()
        args = command[1:]

        # used letters: abeghlopqsu
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
            next_commit = self.add_commit_sets(*self._get_sets_from_command(command))
            if next_commit is not None:
                self._current_commit = next_commit
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
        if self._current_commit is None:
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
            print(prefix + f"Launching {git.get_short_name(self._current_commit)}.")
            return self._launch()
        else:
            self._started = False


    def open_command(self, ref: Optional[str]) -> bool:
        if ref is None:
            if self._current_commit is None:
                print("Invalid command: No arguments were provided but there is no current commit to use.")
                return False
            ref = self._current_commit
        commit = git.resolve_ref(ref)
        if commit == "":
            print(f"Invalid ref: {ref}")
            return False
        if commit in self._old_error_commits:
            print("Warning: That commit has had compiler errors in the past. Trying to open anyways.")
        elif commit in storage.get_ignored_commits():
            print("Warning: That commit is in ignored_commits. Trying to open anyways.")
        self._current_commit = commit
        self.queue_decompress_nexts()
        print("Opening commit", git.get_short_name(commit))
        return self._launch()


    def list_command(self, short: bool) -> bool:
        if len(self._goods) == 0:
            print("No good commits marked, can't calculate a commit list.")
            return False
        elif len(self._bads) == 0:
            print("No bad commits marked, can't calculate a commit list.")
            return False

        bisect_commits = git.get_bisect_commits(self._goods, self._bads)
        if short:
            print(" ".join([git.get_short_name(commit, plain=True) for commit in bisect_commits]))
        else:
            if len(bisect_commits) == 0:
                print("No possible commits found.")
                return False
            print(f"Possible commits ({len(bisect_commits)}):")
            for commit in bisect_commits:
                print(git.get_short_log(commit))
        return True


    def help_command(self, subcommand: Optional[str]) -> bool:
        return True