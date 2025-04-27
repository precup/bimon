import functools
import os
import shlex
import subprocess
from collections import deque
import heapq

import src.terminal as terminal
from src.config import Configuration

CACHE_NAME = "git_cache"
# TODO precache loading is kinda inefficient
PRECACHE_NAME = "git_precache"

_commit_time_cache = {}
_parent_cache = {}
_diff_cache = {}
_diff_precache = {}


def load_cache() -> None:
    try:
        with open(CACHE_NAME, "r") as cache_file:
            segment = 0
            for line in cache_file:
                if line.startswith("#"):
                    segment += 1
                    continue
                parts = line.strip().split()
                if segment == 0:
                    _commit_time_cache[parts[0]] = int(parts[1])
                elif segment == 1:
                    src, dst = parts[0], parts[1]
                    if src > dst:
                        src, dst = dst, src
                    if src not in _diff_cache:
                        _diff_cache[src] = {}
                    _diff_cache[src][dst] = int(parts[2])
                elif segment == 2:
                    _parent_cache[parts[0]] = set(parts[1:])
    except FileNotFoundError:
        pass

load_cache()

def save_cache() -> None:
    with open(CACHE_NAME, "w") as cache_file:
        for commit, timestamp in _commit_time_cache.items():
            cache_file.write(f"{commit} {timestamp}\n")
        cache_file.write("#\n")
        for src, val in _diff_cache.items():
            for dst, size in val.items():
                cache_file.write(f"{src} {dst} {size}\n")
        cache_file.write("#\n")
        for commit, parents in _parent_cache.items():
            cache_file.write(f"{commit} {' '.join(parents)}\n")


def get_neighbors(all_commits: list[str]) -> dict[str, set[str]]:
    # TODO can this get_parents call be batched?
    neighbors = {
        commit: get_parents(commit) for commit in all_commits
    }
    for commit in neighbors:
        to_remove = set()
        for neighbor in neighbors[commit]:
            if neighbor in neighbors:
                neighbors[neighbor].add(commit)
            else:
                to_remove.add(neighbor)
        neighbors[commit] -= to_remove
    return neighbors


def sort_commits(commits_to_sort: set[str], all_commits: list[str]) -> list[str]:
    neighbors = get_neighbors(all_commits)
    save_cache()
    visited = set()
    sorted_commits = []
    for commit in all_commits[::-1]:
        if commit not in commits_to_sort:
            continue
        curr = commit
        while curr is not None and curr not in visited:
            visited.add(curr)
            sorted_commits.append(curr)
            curr = get_similar_commit(curr, all_commits, commits_to_sort, neighbors, visited)
    return sorted_commits


def get_similar_commit(target_commit: str, all_commits: list[str], possible_commits: set[str], neighbors: dict[str, set[str]] = {}, not_possible_commits: set[str] = set()) -> str:
    if neighbors == {}:
        neighbors = get_neighbors(all_commits)

    queue = []
    heapq.heappush(queue, (0, target_commit))
    best_per = {}
    best = None
    best_diff = -1
    while queue:
        total, curr = heapq.heappop(queue)
        if curr in best_per and total > best_per[curr]:
            continue
        best_per[curr] = total
        if best is not None and total >= best_diff:
            continue
        if curr in possible_commits and curr not in not_possible_commits:
            best_diff = total
            best = curr
            continue
        for neighbor in neighbors[curr]:
            diff = get_diff_size(curr, neighbor)
            neighbor_total = total + diff
            if best is not None and neighbor_total >= best_diff:
                continue
            if neighbor not in best_per or neighbor_total < best_per[neighbor]:
                best_per[neighbor] = neighbor_total
                heapq.heappush(queue, (neighbor_total, neighbor))
    return best


def clone(repository: str, target: str) -> bool:
    try:
        os.system(f"git clone {repository} {target}")
        return True
    except:
        return False


def check_out(rev: str) -> None:
    get_git_output(["checkout", "-q", rev])


def fetch() -> None:
    get_git_output(["fetch", "--tags", "--prune", "origin"])
    cache_clear()


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
    short_name = get_git_output(["log", '--pretty=format:"%h"', commit, "-n", "1", "--abbrev-commit"])
    return terminal.color_rev(short_name)


@functools.lru_cache
def get_plain_short_name(commit: str) -> str:
    resolved = resolve_ref(commit)
    if len(resolved) == 0:
        return commit
    return get_git_output(["log", '--pretty=format:"%h"', commit, "-n", "1", "--abbrev-commit"])


@functools.lru_cache
def get_short_log(commit: str) -> str:
    commit_msg = get_git_output(["log", '--pretty=format:"%s"', commit, "-n", "1", "--abbrev-commit"])
    return get_short_name(commit) + " " + commit_msg


@functools.lru_cache(maxsize=None)
def resolve_ref(ref: str) -> str:
    return get_git_output(["rev-parse", "--revs-only", ref.strip()]).strip()


def query_rev_list(start_ref: str, end_ref: str, path_spec: str = "", before: int = -1) -> list[str]:
    return list(_query_rev_list(start_ref, end_ref, path_spec, before))


@functools.lru_cache
def _query_rev_list(start_ref: str, end_ref: str, path_spec: str = "", before: int = -1) -> list[str]:
    command = ["rev-list", "--reverse", f"{start_ref}..{end_ref}"]
    if before >= 0:
        command += [f"--before={before}"]
    if path_spec:
        command += ["--"] + shlex.split(path_spec)
    output = get_git_output(command)
    rev_list = [k.strip() for k in output.split() if k.strip() != ""]
    if len(rev_list) > 0:
        resolved_start_ref = resolve_ref(start_ref)
        resolved_end_ref = resolve_ref(end_ref)
        if resolved_start_ref != resolved_end_ref:
            rev_list.insert(0, resolved_start_ref)
    return rev_list


def get_bisect_commits(good_commits: set[str], bad_commits: set[str], path_spec: str = "", before: int = -1) -> list[str]:
    command = ["rev-list", "--bisect-all"] + [f"^{commit}" for commit in good_commits] + list(bad_commits)
    if before >= 0:
        command += [f"--before={before}"]
    if path_spec:
        command += ["--"] + shlex.split(path_spec)
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


def get_parents(commit: str) -> set[str]:
    if commit in _parent_cache:
        return _parent_cache[commit]
    output = get_git_output(["log", "--pretty=format:%P", commit, "-n", "1"])
    retval = {line.strip() for line in output.split() if len(line.strip()) > 0}
    _parent_cache[commit] = retval
    return retval


_diffs_added = 0
def get_diff_size(commit_src: str, commit_dst: str) -> int:
    global _diffs_added
    if commit_src > commit_dst:
        commit_src, commit_dst = commit_dst, commit_src
    if commit_src in _diff_precache:
        if commit_dst in _diff_precache[commit_src]:
            return _diff_precache[commit_src][commit_dst]
    if commit_src in _diff_cache:
        if commit_dst in _diff_cache[commit_src]:
            return _diff_cache[commit_src][commit_dst]
    retval = _get_diff_size(commit_src, commit_dst)
    if commit_src not in _diff_cache:
        _diff_cache[commit_src] = {}
    _diff_cache[commit_src][commit_dst] = retval
    _diffs_added += 1
    if _diffs_added % 100 == 0:
        save_cache()
    return retval


def _get_diff_size(commit_src: str, commit_dst: str) -> int:
    output = get_git_output(["diff", "--shortstat", commit_src, commit_dst])
    if len(output) == 0:
        return 0
    try:
        output_parts = output.split()
        if len(output_parts) < 4:
            print("bad parts", output_parts)
            return 0
        retval = int(output_parts[3])
        if len(output_parts) >= 6:
            retval = max(retval, int(output_parts[5]))
        return retval
    except ValueError:
        return 0


def cache_clear() -> None:
    _query_rev_list.cache_clear()
    get_short_name.cache_clear()
    get_plain_short_name.cache_clear()
    get_short_log.cache_clear()
    resolve_ref.cache_clear()