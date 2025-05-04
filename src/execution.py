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
    loose_versions = set() # TODO
    used_order = [commit] + [c for c in used_order if c != commit and c in loose_versions]
    storage.save_state(_CACHE_NAME, " ".join(used_order))


def launch(
        ref: str, 
        execution_parameters: str, 
        present_versions: set[str], 
        discard: bool, 
        cached_only: bool, 
        wd: str = "") -> bool:
    commit = git.resolve_ref(ref)
    if commit == "":
        print(f"Invalid ref: \"{ref}\" could not be resolved.")
        return False

    if commit not in present_versions:
        if cached_only:
            print(f"Commit {git.get_short_name(commit)} is not cached."
                + " Skipping due to --cached-only.")
            return False
            
        if not factory.compile_uncached(commit):
            print(f"Failed to compile commit {git.get_short_name(commit)}.")
            return False

        if discard:
            return _launch_folder(Configuration.WORKSPACE_PATH, execution_parameters, wd)
        
        factory.cache()
        present_versions.add(commit)
        
    if not storage.extract_version(commit):
        print(f"Failed to extract commit {git.get_short_name(commit)}.")
        return False
    
    _mark_used(commit)
    return _launch_folder(storage.get_version_folder(commit), execution_parameters, wd)


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


def _launch_folder(workspace_path: str, execution_parameters: str, wd: str) -> bool:
    executable_path = _find_executable(
        workspace_path, Configuration.EXECUTABLE_PATH, Configuration.BACKUP_EXECUTABLE_REGEX
    )
    if executable_path is None:
        print(f"Executable not found in {workspace_path}.")
        return False
    executable_path = str(Path(executable_path).resolve())

    return terminal.execute_in_subwindow(
        command=[executable_path] + shlex.split(execution_parameters),
        title="godot", 
        rows=Configuration.SUBWINDOW_ROWS,
        eat_kill=True,
        cwd=wd,
    )