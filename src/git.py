
import os
import subprocess
from typing import Iterable
from src.config import Configuration
import src.terminal as terminal

def clone(repository, location) -> None:
    try:
        os.system(f"git clone {repository} {location}")
        return True
    except:
        return False


def check_out(rev: str) -> None:
    get_git_output(["checkout", "-q", rev])


def fetch() -> None:
    get_git_output(["fetch", "--tags", "--prune", "origin"])


def get_git_output(args: list[str]) -> str:
    try:
        output = subprocess.check_output(["git", "-C", Configuration.WORKSPACE_PATH] + args).strip().decode("utf-8")
        if len(output) > 0 and output[0] == '"' and output[-1] == '"':
            output = output[1:-1]
        return output
    except subprocess.CalledProcessError:
        return ""


def get_commit_time(commit: str) -> int:
    try:
        return int(get_git_output(["show", "-s", "--format=%ct", commit]))
    except:
        return -1


def get_commit_times(commits: list[str]) -> dict[str, int]:
    if len(commits) == 0:
        return {}
    try:
        lines = get_git_output(["show", "-s", "--format=%ct"] + commits).split()
        return {commits[i]: int(lines[i]) for i in range(len(commits))}
    except:
        return {}


def get_short_name(commit: str) -> str:
    resolved = resolve_ref(commit)
    if len(resolved) == 0:
        return terminal.color_bad(commit)
    short_name = get_git_output(["log", f'--pretty=format:"%h"', commit, "-n", "1", "--abbrev-commit"])
    return terminal.color_rev(short_name)


def get_plain_short_name(commit: str) -> str:
    resolved = resolve_ref(commit)
    if len(resolved) == 0:
        return commit
    return get_git_output(["log", f'--pretty=format:"%h"', commit, "-n", "1", "--abbrev-commit"])


def get_short_log(commit: str) -> str:
    commit_msg = get_git_output(["log", f'--pretty=format:"%s"', commit, "-n", "1", "--abbrev-commit"])
    return get_short_name(commit) + " " + commit_msg


def resolve_ref(ref: str) -> str:
    return get_git_output(["rev-parse", ref])


def query_rev_list(start_ref: str, end_ref: str, path_spec: str = "", before: int = -1) -> list[str]:
    command = ["rev-list", "--reverse", f"{start_ref}..{end_ref}"]
    if before >= 0:
        command += [f"--before={before}"]
    if path_spec.strip() != "":
        command += [f"--", path_spec]
    output = get_git_output(command)
    return [k.strip() for k in output.split() if k.strip() != ""]


def get_bisect_commits(good_commits: set[str], bad_commits: set[str], path_spec: str, before: int = -1) -> list[str]:
    command = ["rev-list", "--bisect-all"] + [f"^{commit}" for commit in good_commits] + list(bad_commits)
    if before >= 0:
        command += [f"--before={before}"]
    if path_spec.strip() != "":
        command += [f"--", path_spec]
    output = get_git_output(command)
    return [line.strip().split()[0].strip() for line in output.splitlines() if len(line.strip()) > 0]


def get_local_changes() -> bool:
    return [line.strip() for line in get_git_output(["add", "-An"]).strip().splitlines()]


def has_local_changes() -> bool:
    return len("".join(get_local_changes())) > 0


def clear_local_changes() -> None:
    get_git_output(["reset", "--hard", "HEAD"])
    get_git_output(["clean", "-df"])


def get_tags() -> list[str]:
    return [line.strip() for line in get_git_output(["tag", "-l"]).splitlines()]