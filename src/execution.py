import os
import re
import shlex
from pathlib import Path
from typing import Optional

from src import factory
from src import git
from src import storage
from src import terminal
from src.config import Configuration

_CACHE_NAME = "execution_cache"
_MAX_CACHE_SIZE = 1000


def delete_cache(dry_run: bool = False) -> int:
    path_exists = os.path.exists(storage.get_state_filename(_CACHE_NAME))
    if dry_run:
        if path_exists:
            print(f"Cache \"{_CACHE_NAME}\" would be deleted.")
            return 1
        return 0
    if Configuration.PRINT_MODE == Configuration.PrintMode.VERBOSE and path_exists:
        print(f"Deleting cache \"{_CACHE_NAME}\".")
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
        wd: str = "") -> bool:
    return launch_with_automation(
        ref,
        execution_arguments,
        present_versions,
        discard,
        cached_only,
        wd) != "error"


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
        automate_script: Optional[str] = None) -> str:
    commit = git.resolve_ref(ref)
    if commit == "":
        print(f"Invalid ref: \"{ref}\" could not be resolved.")
        return "error"

    if commit not in present_versions:
        if cached_only:
            print(f"Commit {git.get_short_name(commit)} is not cached."
                + " Skipping due to --cached-only.")
            return "error"

        if not factory.compile_uncached(commit):
            print(f"Failed to compile commit {git.get_short_name(commit)}.")
            return "error"

        if discard:
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
                automate_script)

        if not factory.cache():
            return "error"
        present_versions.add(commit)

    if not storage.extract_version(commit):
        print(f"Failed to extract commit {git.get_short_name(commit)}.")
        return "error"

    _mark_used(commit)
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
        automate_script)


def _find_executable(
        base_folder: str,
        likely_location: str,
        backup_path_regex: str) -> Optional[str]:
    likely_location = os.path.join(base_folder, likely_location)
    if os.path.exists(likely_location):
        return likely_location

    backup_path_re = re.compile(backup_path_regex)
    for root, _, files in os.walk(base_folder):
        for file in files:
            full_path = os.path.join(root, file)
            if backup_path_re.match(full_path):
                return full_path

    return None


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
        automate_script: Optional[str]) -> str:
    executable_path = _find_executable(
        workspace_path, Configuration.EXECUTABLE_PATH, Configuration.BACKUP_EXECUTABLE_REGEX
    )
    if executable_path is None:
        print(f"Executable not found in {workspace_path}.")
        return "error"
    executable_path = str(Path(executable_path).resolve())

    command = [executable_path] + shlex.split(execution_arguments, posix='nt' != os.name)
    if automate_script is not None:
        command = [automate_script] + command

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