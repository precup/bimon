import os
import re
import shlex
from pathlib import Path
from typing import Optional

from src import factory
from src import git
from src import storage
from src import terminal
from src.config import Configuration, PrintMode

_CACHE_NAME = "execution_cache"
_MAX_CACHE_SIZE = 1000


def delete_cache(dry_run: bool = False) -> int:
    path_exists = os.path.exists(storage.get_state_filename(_CACHE_NAME))
    if dry_run:
        if path_exists:
            print(f"Would delete cache \"{_CACHE_NAME}\"")
            return 1
        return 0
    if Configuration.PRINT_MODE != PrintMode.QUIET and path_exists:
        print(f"Deleting cache \"{_CACHE_NAME}\"")
    return storage.delete_state(_CACHE_NAME)


def _mark_used(commit: str) -> None:
    used_order_str = storage.load_state(_CACHE_NAME)
    used_order = used_order_str.split()
    used_order = [commit] + [c for c in used_order if c != commit][:_MAX_CACHE_SIZE]
    storage.save_state(_CACHE_NAME, " ".join(used_order))


def launch(
        ref: str,
        execution_arguments: str,
        present_versions: set[str],
        discard: bool,
        cached_only: bool,
        wd: str = "",
        no_subwindow: bool = False) -> bool:
    return launch_with_automation(
        ref,
        execution_arguments,
        present_versions,
        discard,
        cached_only,
        wd,
        no_subwindow=no_subwindow) != "error"


def launch_with_automation(
        ref: str,
        execution_arguments: str,
        present_versions: set[str],
        discard: bool,
        cached_only: bool,
        wd: str = "",
        automate_good: Optional[str] = None,
        automate_good_regex: Optional[re.Pattern] = None,
        automate_bad: Optional[str] = None,
        automate_bad_regex: Optional[re.Pattern] = None,
        automate_crash: Optional[str] = None,
        automate_exit: Optional[str] = None,
        automate_script: Optional[str] = None,
        no_subwindow: bool = False) -> str:
    commit = git.resolve_ref(ref)
    if commit == "":
        ref = terminal.color_ref(ref)
        print(terminal.error(f"Invalid ref: \"{ref}\" could not be resolved."))
        return "error"
    short_name = git.get_short_name(commit)

    if commit not in present_versions:
        if cached_only:
            print(terminal.error(f"Commit {short_name} is not cached."
                + " Skipping due to --cached-only."))
            return "error"

        print(f"Compiling commit {short_name}...")
        if not factory.compile_uncached(commit):
            print(terminal.error(f"Failed to compile commit {short_name}."))
            return "error"

        if discard:
            print(f"Launching commit {short_name}...")
            return _launch_folder(
                Configuration.WORKSPACE_PATH,
                execution_arguments,
                wd,
                automate_good,
                automate_good_regex,
                automate_bad,
                automate_bad_regex,
                automate_crash,
                automate_exit,
                automate_script,
                no_subwindow)

        print(f"Caching commit...")
        if not factory.cache():
            return "error"
        present_versions.add(commit)

    if not storage.extract_version(commit):
        print(terminal.error(f"Failed to extract commit {short_name}."))
        return "error"

    _mark_used(commit)
    print(f"Launching commit {short_name}...")
    return _launch_folder(
        storage.get_version_folder(commit),
        execution_arguments,
        wd,
        automate_good,
        automate_good_regex,
        automate_bad,
        automate_bad_regex,
        automate_crash,
        automate_exit,
        automate_script,
        no_subwindow)


def _launch_folder(
        workspace_path: str,
        execution_arguments: str,
        wd: str,
        automate_good: Optional[str],
        automate_good_regex: Optional[re.Pattern],
        automate_bad: Optional[str],
        automate_bad_regex: Optional[re.Pattern],
        automate_crash: Optional[str],
        automate_exit: Optional[str],
        automate_script: Optional[str],
        no_subwindow: bool = False) -> str:
    executable_path = storage.find_executable(
        workspace_path, Configuration.EXECUTABLE_PATH, Configuration.EXECUTABLE_REGEX
    )
    if executable_path is None:
        workspace_path = terminal.color_key(workspace_path)
        print(terminal.error(f"Executable not found in {workspace_path}."))
        return "error"
    executable_path = str(Path(executable_path).resolve())

    command = [executable_path] + shlex.split(execution_arguments, posix='nt' != os.name)
    if automate_script is not None:
        command = [automate_script] + command

    print(f"Command: {terminal.color_key(' '.join(command))}")
    print(f"Working directory: {terminal.color_key(wd)}")

    return terminal.execute_in_subwindow_with_automation(
        command=command,
        title="godot",
        rows=Configuration.SUBWINDOW_ROWS,
        eat_kill=True,
        cwd=wd,
        automate_good=automate_good,
        automate_good_regex=automate_good_regex,
        automate_bad=automate_bad,
        automate_bad_regex=automate_bad_regex,
        automate_crash=automate_crash,
        automate_exit=automate_exit)