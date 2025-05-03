import os
import shlex
from pathlib import Path
from typing import Optional

from src import factory
from src import git
from src import mrp_manager
from src import storage
from src import terminal
from src.config import Configuration

NO_PROJECT_FOLDER = os.path.join(mrp_manager.MRP_FOLDER, "no_project_folder")


def launch(
        ref: str, 
        execution_parameters: str, 
        present_versions: set[str], 
        discard: bool, 
        cached_only: bool, 
        wd: str = "") -> bool:
    if wd == "":
        storage.rm(NO_PROJECT_FOLDER)
        os.makedirs(NO_PROJECT_FOLDER, exist_ok=True)
        wd = NO_PROJECT_FOLDER
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
        
    if not storage.extract_commit(commit):
        print(f"Failed to extract commit {git.get_short_name(commit)}.")
        return False
    
    return _launch_folder(os.path.join(storage.VERSIONS_DIR, commit), execution_parameters, wd)


def _find_executable(
        base_folder: str, 
        likely_location: str, 
        backup_path_regex: str) -> Optional[str]:
    likely_location = os.path.join(base_folder, likely_location)
    if os.path.exists(likely_location):
        return likely_location

    for root, _, files in os.walk(base_folder):
        for file in files:
            full_path = os.path.join(root, file)
            if backup_path_regex.match(full_path):
                return full_path

    return None


def _launch_folder(workspace_path: str, execution_parameters: str, wd: str) -> bool:
    executable_path = _find_executable(
        workspace_path, Configuration.EXECUTABLE_PATH, Configuration.BACKUP_EXECUTABLE_REGEX
    )
    executable_path = str(Path(executable_path).resolve())

    return terminal.execute_in_subwindow(
        command=[executable_path] + shlex.split(execution_parameters),
        title="godot", 
        rows=Configuration.SUBWINDOW_ROWS,
        eat_kill=True,
        cwd=wd,
    )