import os
import re
import time
from enum import Enum
from typing import Optional

from src import execution
from src import git
from src import storage
from src import terminal
from src.config import Configuration

_WARN_TIME: int = 60 * 60 * 24 * 7
_ORIGINAL_WD: str = os.getcwd()


class Bisector:
    class CommandResult(Enum):
        SUCCESS = 0
        ERROR = 1
        EXIT = 2


    def __init__(
            self,
            discard: bool,
            cached_only: bool,
            execution_args: str,
            path_spec: Optional[str],
            end_timestamp: int,
            wd: str = "",
            initial_goods: set[str] = set(),
            initial_bads: set[str] = set(),
            initial_skips: set[str] = set()) -> None:
        self._end_timestamp = end_timestamp
        self._path_spec = path_spec
        self._discard = discard
        self._cache_only = cached_only
        self._execution_args = execution_args

        self._present_versions = storage.get_present_versions()
        self._ignored_commits = storage.get_ignored_commits()
        self._old_error_commits = set()
        if not Configuration.IGNORE_OLD_ERRORS:
            self._old_error_commits = storage.get_compiler_error_commits()
        self._goods: set[str] = set()
        self._bads: set[str] = set()
        self._current_bad: Optional[str] = None
        self._skips: set[str] = set()
        self._started = False
        self._phase_two = False
        self._has_unstarted = False
        self._has_exited = False
        self._wd = wd

        self._automate_good = None
        self._automate_good_regex = None
        self._automate_bad = None
        self._automate_bad_regex = None
        self._automate_crash = None
        self._automate_exit = None
        self._automate_script = None

        self._bisect_commits: Optional[list[str]] = None

        self._handle_time_warnings()

        storage.init_decompress_queue()

        if not self.add_commit_sets(initial_goods, initial_bads, initial_skips):
            raise ValueError(
                "Invalid initial commit sets, use mark_command if you want error handling."
            )

        if len(self._goods) > 0 and len(self._bads) > 0:
            self._current_commit, status = self.get_next_commit(silent=False)
            if status == Bisector.CommandResult.ERROR:
                raise ValueError(
                    "Initial conditions for bisect resulted in an unexpected error."
                )
        else:
            self._current_commit = None


    def add_commit_sets(
            self,
            new_goods: set[str] = set(),
            new_bads: set[str] = set(),
            new_skips: set[str] = set(),
            new_unmarks: set[str] = set(),
            silent: bool = False) -> bool:
        if len(new_goods | new_bads | new_skips | new_unmarks) == 0:
            return True

        temp_goods = set(self._goods)
        temp_bads = set(self._bads)

        temp_goods.difference_update(new_unmarks)
        temp_bads.difference_update(new_unmarks)

        temp_goods.update(new_goods)
        temp_bads.update(new_bads)

        short_bads = git.minimal_parents(temp_bads)
        short_goods = git.minimal_children(temp_goods)
        covered = git.minimal_children(temp_bads | temp_goods)
        if len(short_bads) > 0 and len(short_goods) > 0 and len(short_goods - covered) == 0:
            if not silent:
                commit = git.get_merge_base(
                    list(sorted(short_goods))[0], 
                    list(sorted(short_bads))[0])
                if commit != "":
                    commit = git.get_short_name(commit)
                    print(terminal.warn("Good commits weren't ancestors of bad commits."
                        + " If the number of possible commits seems too high,"
                        + f" try {commit} instead."))

        for bad in short_bads:
            for good in short_goods:
                if git.is_ancestor(bad, good):
                    good_name = git.get_short_name(good)
                    bad_name = git.get_short_name(bad)
                    print(terminal.error(f"{bad_name} (bad) is an ancestor of {good_name} (good)."))
                    print(terminal.error(
                        "This makes the bisect impossible, please check your commits. Ignoring."))
                    return False

        self._goods = temp_goods
        self._bads = temp_bads
        if len(short_bads) == 1:
            self._current_bad = list(short_bads)[0]
        else:
            self._current_bad = None
        self._skips.difference_update(new_unmarks)
        self._skips.update(new_skips)
        self._bisect_commits = None

        return True


    def get_next_commit(
            self,
            silent: bool,
            cached: bool = True) -> tuple[Optional[str], CommandResult]:
        if len(self._goods) == 0 or len(self._bads) == 0:
            if not silent:
                types = " or ".join((["good"] if len(self._goods) == 0 else [])
                                  + (["bad"] if len(self._bads) == 0 else []))
                print(f"No {types} commits marked, can't calculate a bisect commit.")
            return None, Bisector.CommandResult.SUCCESS

        bads = list(sorted(git.minimal_parents(self._bads)))
        for i, bad in enumerate(bads):
            for j in range(i + 1, len(bads)):
                merge_base = git.get_merge_base(bad, bads[j])
                if len(merge_base) > 0:
                    if not silent:
                        print(terminal.warn("Bad commits weren't ancestors of each other,"
                            + " testing the merge base just in case."))
                        print("This is very likely a bad commit.")
                    return merge_base, Bisector.CommandResult.SUCCESS

        if self._current_bad is None:
            if not silent:
                print(terminal.warn("Multiple bad targets possible, using the first one."))
                print(terminal.warn("Please report, I didn't expect this to happen."))
            bad = bads[0]
        else:
            bad = self._current_bad

        if cached:
            if self._bisect_commits is None:
                self._bisect_commits = self.get_bisect_commits(self._goods, bad)
            bisect_commits = self._bisect_commits
        else:
            bisect_commits = self.get_bisect_commits(self._goods, bad)
        if len(bisect_commits) == 0:
            if not silent:
                print(terminal.error("No possible commits found for those good and bad commits."))
            return None, Bisector.CommandResult.ERROR
        bisect_commits = [commit for commit in bisect_commits if commit not in self._bads]
        if len(bisect_commits) == 0:
            for parent in git.get_parents(bad):
                if not self._path_spec and parent not in self._goods and not any(
                        git.is_ancestor(parent, good) for good in self._goods):
                    print(terminal.error("Only one commit left, but some of its parents are untested."))
                    print(terminal.error("This shouldn't have happened and the results may be wrong."))
            return bad, Bisector.CommandResult.EXIT

        if self._path_spec is not None and self._path_spec != "":
            all_bisect_commits = set(self.get_bisect_commits(self._goods, bad, ""))
            all_bisect_commits = (
                (all_bisect_commits & self._present_versions)
                - self._ignored_commits
                - self._old_error_commits
                - self._skips
                - self._bads
                - self._goods
            )
            most_similar = git.get_similar_commit(bisect_commits[0], all_bisect_commits)
            if most_similar != "":
                return most_similar, Bisector.CommandResult.SUCCESS

        bisect_commits = self._filter_ignored_errored_skipped(bisect_commits, silent)

        present_bisect_versions = [
            commit for commit in bisect_commits
            if commit in self._present_versions
        ]
        if len(present_bisect_versions) > 0:
            if self._phase_two and not silent:
                print("Precompiled commits are back inside the possible range.")
                print("Switching back to searching precompiled commits.")
                self._phase_two = False
            bisect_commits = present_bisect_versions
        elif not silent and not self._phase_two:
            print("No more useful precompiled commits to test.")
            if self._cache_only:
                print("Cache only mode, exiting.")
                return None, Bisector.CommandResult.EXIT
            print("Switching to compiling versions as needed.")
            self._phase_two = True

        return bisect_commits[0], Bisector.CommandResult.SUCCESS


    def get_bisect_commits(
            self,
            goods: set[str],
            bad: str,
            path_spec: Optional[str] = None) -> list[str]:
        if path_spec is None:
            path_spec = self._path_spec

        return git.get_bisect_commits(
            good_refs=goods,
            bad_ref=bad,
            path_spec=path_spec,
            before=self._end_timestamp)


    def mark_command(
            self,
            command: str,
            args: list[str],
            no_launch: bool = False) -> CommandResult:
        commit_sets = self._get_sets_from_command([command] + args)
        success = self.add_commit_sets(*commit_sets)
        if not success:
            return Bisector.CommandResult.ERROR

        self._current_commit, status = self.get_next_commit(silent=False)
        if status != Bisector.CommandResult.SUCCESS:
            return status

        if self._current_commit is not None:
            if not no_launch:
                self.queue_decompress_nexts()
            self.print_status_message()
            if self._started and not no_launch:
                return self._launch()

        return Bisector.CommandResult.SUCCESS


    def automate_command(
            self,
            good: Optional[str] = None,
            bad: Optional[str] = None,
            crash: Optional[str] = None,
            exit: Optional[str] = None,
            script: Optional[str] = None,
            regex: bool = False) -> CommandResult:
        if good is None:
            self._automate_good = None
        else:
            if regex:
                try:
                    self._automate_good_regex = re.compile(good)
                except re.error:
                    print(terminal.error(f"Invalid regex for good: {good}."))
                    return Bisector.CommandResult.ERROR
            else:
                self._automate_good = good

        if bad is None:
            self._automate_bad = None
        else:
            if regex:
                try:
                    self._automate_bad_regex = re.compile(bad)
                except re.error:
                    print(terminal.error(f"Invalid regex for bad: {bad}."))
                    return Bisector.CommandResult.ERROR
            else:
                self._automate_bad = bad

        options = ["good", "bad", "skip", "unmark"]
        if crash is None:
            self._automate_crash = None
        else:
            new_crash_options = [option for option in options if option.startswith(crash)]
            if len(new_crash_options) == 0:
                print(terminal.error(f"Invalid crash option: {crash} is not a valid"
                    + " way to mark commits (good/bad)."))
                return Bisector.CommandResult.ERROR
            self._automate_crash = new_crash_options[0]

        if exit is None:
            self._automate_exit = None
        else:
            new_exit_options = [option for option in options if option.startswith(exit)]
            if len(new_exit_options) == 0:
                print(terminal.error(f"Invalid crash option: {exit} is not a valid"
                    + " way to mark commits (good/bad)."))
                return Bisector.CommandResult.ERROR
            self._automate_exit = new_exit_options[0]

        if script is None:
            self._automate_script = None
        else:
            resolved_script = storage.resolve_relative_to(script, _ORIGINAL_WD)
            if not os.path.exists(resolved_script):
                if Configuration.COLOR_ENABLED:
                    script = terminal.color_key(script)
                else:
                    script = f'"{script}"'
                print(terminal.error(f"Invalid script option: script {script} does not exist."))
                return Bisector.CommandResult.ERROR
            self._automate_script = resolved_script

        if self._current_commit is None:
            self._started = True
            print("No current commit to test. Automatic testing will start once one is set.")
            return Bisector.CommandResult.SUCCESS

        if not self._started:
            print("Starting automatic testing.")
            self._started = True

        return self._launch()


    def pause_command(self) -> CommandResult:
        if self._started:
            self._started = False
            print("Automatic testing paused.")
        else:
            print("Automatic testing is already paused.")
        return Bisector.CommandResult.SUCCESS


    def exit_command(self) -> CommandResult:
        return Bisector.CommandResult.EXIT


    def run_command(self, refs: Optional[list[str]]) -> CommandResult:
        if refs is None or len(refs) == 0:
            if self._current_commit is None:
                print(terminal.error("Invalid command: No arguments were provided but there is"
                    + " no current commit to use."))
                return Bisector.CommandResult.ERROR
            refs = [self._current_commit]
        commits = []
        for ref in refs:
            commit = git.resolve_ref(ref)
            if commit == "":
                print(terminal.error(f"Invalid ref: {ref}"))
                return Bisector.CommandResult.ERROR
            commits.append(commit)

        for i, commit in enumerate(commits):
            if commit in self._old_error_commits:
                print(terminal.warn("That commit has had compiler errors in the past."
                    + " Trying to run anyways."))
            elif commit in storage.get_ignored_commits():
                print(terminal.warn("That commit is in ignored_commits. Trying to run anyways."))
            self._current_commit = commit
            if i == len(commits) - 1:
                self.queue_decompress_nexts()
            launch_result = self._launch(single_launch=i < len(commits) - 1)
            if launch_result != Bisector.CommandResult.SUCCESS:
                return launch_result
        return Bisector.CommandResult.SUCCESS


    def list_command(self, short: bool, best: bool) -> CommandResult:
        if len(self._goods) == 0:
            print(terminal.error("No good commits marked, can't calculate a commit list."))
            return Bisector.CommandResult.ERROR
        elif len(self._bads) == 0:
            print(terminal.error("No bad commits marked, can't calculate a commit list."))
            return Bisector.CommandResult.ERROR

        bad = list(sorted(self._bads))[0] if self._current_bad is None else self._current_bad
        if self._bisect_commits is None:
            self._bisect_commits = self.get_bisect_commits(self._goods, bad)
        commits = self._bisect_commits
        if not best:
            time_order_commits = git.get_commit_list("", bad, self._path_spec, self._end_timestamp)
            bisect_commit_set = set(commits)
            commits = [commit for commit in time_order_commits if commit in bisect_commit_set]
        if short:
            print(" ".join([git.get_short_name(commit, plain=True) for commit in commits]))
        else:
            if len(commits) == 0:
                print(terminal.error("No possible commits found."))
                return Bisector.CommandResult.ERROR
            print(f"Possible commits ({len(commits)}):")
            for commit in commits:
                print(git.get_short_log(commit))
        return Bisector.CommandResult.SUCCESS


    def status_command(self, short: bool) -> CommandResult:
        self.print_status_message(short)
        return Bisector.CommandResult.SUCCESS


    def print_exit_message(self) -> None:
        if len(self._goods) == 0 or len(self._bads) == 0:
            print()
            return

        bad = list(sorted(self._bads))[0] if self._current_bad is None else self._current_bad
        if self._bisect_commits is None:
            self._bisect_commits = self.get_bisect_commits(self._goods, bad)
        remaining = set(self._bisect_commits)
        if len(remaining) == 1:
            bad_commit = list(remaining)[0]
            print(terminal.color_good("Only one commit left,"), 
                " it must be " + git.get_short_name(bad_commit))
            print(terminal.color_key("https://github.com/godotengine/godot/commit/" + bad_commit))
            print(git.get_short_log(bad_commit))

        print("\nExiting bisect interactive mode.")

        if len(remaining) > 1:
            print(f"There are {terminal.color_key(str(len(remaining)))} remaining possible commits.")
            if len(self._goods | self._bads | self._skips) > 0:
                print("You can resume with:")
                self._print_resume_sets()


    def set_arguments_command(self, arguments: str) -> CommandResult:
        self._execution_args = arguments
        return Bisector.CommandResult.SUCCESS


    def print_status_message(self, short: bool = True) -> None:
        if len(self._goods) > 0 and len(self._bads) > 0:
            bad = list(sorted(self._bads))[0] if self._current_bad is None else self._current_bad

            if self._bisect_commits is None:
                self._bisect_commits = self.get_bisect_commits(self._goods, bad)
            remaining = set(self._bisect_commits)

            print(f"There are {terminal.color_key(str(len(remaining)))} remaining possible commits.")
            steps_left = git.get_bisect_steps_from_remaining((len(remaining)))
            steps_text = f"~{steps_left:.01f} steps"
            print(terminal.color_key(steps_text) + " remaining. Next commit to test:")
        elif len(self._goods) > 0:
            print("Waiting for initial bad commit.")
        elif len(self._bads) > 0:
            print("Waiting for initial good commit.")
        else:
            print("Waiting for initial good and bad commits.")

        if self._current_commit is None:
            print("No current commit set.")
        else:
            print(git.get_short_log(self._current_commit))

        if not short and len(self._goods | self._bads | self._skips) > 0:
            print()
            print("Minimal sets of marked commits:")
            self._print_resume_sets()
        print()


    def queue_decompress_nexts(self) -> None:
        if self._current_commit is None:
            return

        to_decompress = []
        layers = Configuration.BACKGROUND_DECOMPRESSION_LAYERS

        queue = [(self._current_commit, 0, set(), set())]
        while queue:
            current_commit, current_layer, inherited_goods, inherited_bads = queue.pop(0)
            if current_commit in self._goods | self._bads | self._skips:
                continue
            if current_layer >= layers:
                continue

            new_goods = inherited_goods | {current_commit}
            if git.minimal_parents(new_goods) != git.minimal_parents(self._goods):
                self._goods |= new_goods
                good_next_commit, status = self.get_next_commit(silent=True, cached=False)
                succeeded = good_next_commit is not None and status == Bisector.CommandResult.SUCCESS
                if succeeded and good_next_commit not in to_decompress:
                    to_decompress.append(good_next_commit)
                    queue.append((good_next_commit, current_layer + 1, new_goods, inherited_bads))
                self._goods -= new_goods

            old_current_bad = self._current_bad
            new_bads = inherited_bads | {current_commit}
            if git.minimal_parents(new_bads) != git.minimal_parents(self._bads):
                self._bads |= new_bads
                self._current_bad = current_commit
                bad_next_commit, status = self.get_next_commit(silent=True, cached=False)
                succeeded = bad_next_commit is not None and status == Bisector.CommandResult.SUCCESS
                if succeeded and bad_next_commit not in to_decompress:
                    to_decompress.append(bad_next_commit)
                    queue.append((bad_next_commit, current_layer + 1, inherited_goods, new_bads))
                self._bads -= new_bads
                self._current_bad = old_current_bad

        storage.set_decompress_queue(to_decompress)


    def _get_sets_from_command(
            self,
            command: list[str]) -> tuple[set[str], set[str], set[str], set[str]]:
        commands = ["good", "bad", "skip", "unmark"]
        new_sets: dict[str, set[str]] = {command[0]: set() for command in commands}

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
                    print(terminal.error(f"Invalid command: {sentence[0]} has no arguments"
                        + " but there is no current commit to use."))
                    return (set(), set(), set(), set())
                sentence.append(self._current_commit)
            sentence_key = sentence[0][0].lower()
            commits = {git.resolve_ref(ref) for ref in sentence[1:]}
            for commit in commits:
                if commit == "":
                    bad_refs = [ref for ref in sentence[1:] if git.resolve_ref(ref) == ""]
                    print(terminal.error("Unresolvable ref(s): " + " ".join(bad_refs)))
                    return (set(), set(), set(), set())
            for key, value in new_sets.items():
                if key != sentence_key and len(commits.intersection(value)) > 0:
                    print(terminal.error(
                        "Invalid command: Some commits were marked multiple times."))
                    return (set(), set(), set(), set())
            new_sets[sentence_key].update(commits)

        already_marked = set()
        for new_commits, old_commits in [
            (self._goods, new_sets["b"]),
            (self._goods, new_sets["s"]),
            (self._bads, new_sets["g"]),
            (self._bads, new_sets["s"]),
            (self._skips, new_sets["g"]),
            (self._skips, new_sets["b"]),
        ]:
            already_marked.update(new_commits.intersection(old_commits))
        already_marked.difference_update(new_sets["u"])
        if len(already_marked) > 0:
            if len(sentences) > 1 or len(sentences[0]) > 2:
                prefix = f"{len(already_marked)} of those commits were"
            else:
                prefix = "That commit was"
            print(terminal.warn(prefix + " already marked as something else. Updating anyways."))

        return tuple(new_sets[command[0]] for command in commands)


    def _launch(self, single_launch: bool = False) -> CommandResult:
        if self._current_commit is None:
            print(terminal.error("No current commit is set, nothing to run."))
            return Bisector.CommandResult.ERROR
        result = execution.launch_with_automation(
            self._current_commit,
            self._execution_args,
            self._present_versions,
            self._discard,
            self._cache_only,
            self._wd,
            self._automate_good,
            self._automate_good_regex,
            self._automate_bad,
            self._automate_bad_regex,
            self._automate_crash,
            self._automate_exit,
            self._automate_script)

        automark_command = []
        for name, prefix, suffix in [
                ("good", "marking commit", " as " + terminal.color_good("good") + "."),
                ("bad", "marking commit", " as " + terminal.color_bad("bad") + "."),
                ("skip", "marking commit", " as skipped."),
                ("unmark", "unmarking commit", "."),
            ]:
            if result == name:
                automark_command = [name, self._current_commit]
                print(f"Automatically {prefix} {git.get_short_name(self._current_commit)}{suffix}")
                break

        if result == "error":
            if self._started:
                print(terminal.warn("Automatic testing failed due to an error while executing. Pausing."))
                self._started = False
            return Bisector.CommandResult.ERROR
        elif len(automark_command) > 0:
            return self.mark_command(
                automark_command[0],
                automark_command[1:],
                no_launch=single_launch)

        return Bisector.CommandResult.SUCCESS


    def _handle_time_warnings(self) -> None:
        commit_list = self._commit_list(path_spec="", before=-1)
        if len(commit_list) == 0:
            print(terminal.error("No commits found in the repository. This should never happen."))
            raise RuntimeError("No commits found in the repository.")

        latest_known_time = git.get_commit_time(commit_list[-1])
        time_since = time.time() - latest_known_time
        if time_since > _WARN_TIME:
            days_old = int(time_since / 60 / 60 / 24)
            print(terminal.warn(f"The latest known commit is {days_old} days old."))

        latest_present_version = None
        for commit in commit_list[::-1]:
            if commit in self._present_versions:
                latest_present_version = commit
                break

        if latest_present_version is None:
            print(terminal.warn("No cached versions found."))
            return

        latest_present_time = git.get_commit_time(latest_present_version)
        time_since = time.time() - latest_present_time
        if time_since > _WARN_TIME:
            days_old = int(time_since / 60 / 60 / 24)
            print(terminal.warn(f"The latest cached version is {days_old} days old."))
            print(terminal.warn("You may want to test some newer commits manually with \"run\"."))


    def _commit_list(
            self,
            start: Optional[str] = None,
            end: Optional[str] = None,
            path_spec: Optional[str] = None,
            before: Optional[int] = None) -> list[str]:
        if start is None:
            start = ""
        if end is None:
            end = ""
        if path_spec is None:
            path_spec = self._path_spec
        if before is None:
            before = self._end_timestamp

        return git.get_commit_list(
            start,
            end,
            path_spec=path_spec,
            before=before)


    def _filter_ignored_errored_skipped(
            self,
            possible_next_commits: list[str],
            silent: bool) -> list[str]:
        output = ""
        possible_unskipped_commits = [
            commit for commit in possible_next_commits
            if commit not in self._skips
        ]
        all_skipped = len(possible_unskipped_commits) == 0
        if all_skipped:
            output += "Every remaining commit is marked as skipped.\n"
        else:
            possible_next_commits = possible_unskipped_commits

        possible_unignored_commits = [
            commit for commit in possible_next_commits
            if commit not in self._ignored_commits
        ]
        all_ignored = len(possible_unignored_commits) == 0
        if all_ignored:
            if output == "":
                output += "Every remaining commit is in ignored_commits.\n"
        else:
            possible_next_commits = possible_unignored_commits

        possible_unerrored_commits = [
            commit for commit in possible_next_commits
            if commit not in self._old_error_commits
        ]
        all_errored = len(possible_unerrored_commits) == 0
        if all_errored:
            if output == "":
                output += "Every remaining commit failed to build in the past.\n"
        else:
            possible_next_commits = possible_unerrored_commits

        if output != "":
            output += "Picking one to test next anyways, but it may be untestable.\n"

            if not silent and not self._has_unstarted and self._started:
                output += "Disabling automate to avoid autocompiling untestable commits."
                output += " It can be turned back on if you wanted it.\n"
                self._started = False
                self._has_unstarted = True

            if not silent:
                print(terminal.warn(output), end="")

        return possible_next_commits


    def _print_resume_sets(self) -> None:
        for commit_set, name in [
            (git.minimal_children(self._goods), "good"),
            (git.minimal_parents(self._bads), "bad"),
            (self._skips, "skip"),
        ]:
            if len(commit_set) > 0:
                commit_set = {git.get_short_name(commit) for commit in commit_set}
                print(f"{name} " + " ".join(git.get_short_name(commit) for commit in commit_set))
