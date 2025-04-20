import functools
import shlex
import subprocess

import src.terminal as terminal

from src.config import Configuration

_commit_time_cache = {}


def clone(repository: str, target: str) -> None:
    try:
        os.system(f"git clone {repository} {target}")
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
    if commit in _commit_time_cache:
        return _commit_time_cache[commit]
    try:
        retval = int(get_git_output(["show", "-s", "--format=%ct", commit]))
    except:
        retval = -1
    _commit_time_cache[commit] = retval
    return retval


def get_commit_times(commits: list[str]) -> dict[str, int]:
    result = {
        commit: _commit_time_cache[commit] for commit in commits
        if commit in _commit_time_cache
    }
    commits = [commit for commit in commits if commit not in result]
    if len(commits) == 0:
        return result
    try:
        lines = get_git_output(["show", "-s", "--format=%ct"] + commits).split()
        result.update({commits[i]: int(lines[i]) for i in range(len(commits))})
        _commit_time_cache.update(result)
        return result
    except:
        return {}


@functools.lru_cache
def get_short_name(commit: str) -> str:
    resolved = resolve_ref(commit)
    if len(resolved) == 0:
        return terminal.color_bad(commit)
    short_name = get_git_output(["log", f'--pretty=format:"%h"', commit, "-n", "1", "--abbrev-commit"])
    return terminal.color_rev(short_name)


@functools.lru_cache
def get_plain_short_name(commit: str) -> str:
    resolved = resolve_ref(commit)
    if len(resolved) == 0:
        return commit
    return get_git_output(["log", f'--pretty=format:"%h"', commit, "-n", "1", "--abbrev-commit"])


@functools.lru_cache
def get_short_log(commit: str) -> str:
    commit_msg = get_git_output(["log", f'--pretty=format:"%s"', commit, "-n", "1", "--abbrev-commit"])
    return get_short_name(commit) + " " + commit_msg


@functools.lru_cache(maxsize=None)
def resolve_ref(ref: str) -> str:
    return get_git_output(["rev-parse", "--revs-only", ref])


def query_rev_list(start_ref: str, end_ref: str, path_spec: str = "", before: int = -1) -> list[str]:
    return list(_query_rev_list(start_ref, end_ref, path_spec, before))


@functools.lru_cache
def _query_rev_list(start_ref: str, end_ref: str, path_spec: str = "", before: int = -1) -> list[str]:
    command = ["rev-list", "--reverse", f"{start_ref}..{end_ref}"]
    if before >= 0:
        command += [f"--before={before}"]
    if path_spec:
        command += [f"--"] + shlex.split(path_spec)
    output = get_git_output(command)
    return [k.strip() for k in output.split() if k.strip() != ""]


def get_bisect_commits(good_commits: set[str], bad_commits: set[str], path_spec: str = "", before: int = -1) -> list[str]:
    command = ["rev-list", "--bisect-all"] + [f"^{commit}" for commit in good_commits] + list(bad_commits)
    if before >= 0:
        command += [f"--before={before}"]
    if path_spec:
        command += [f"--"] + shlex.split(path_spec)
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


def is_ancestor(possible_ancestor: str, commit: str) -> bool:
    return len(query_rev_list(possible_ancestor, commit)) > 0