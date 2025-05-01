import functools
import os
import shlex
import subprocess
from collections import deque, defaultdict
from typing import Optional
import heapq

import src.terminal as terminal
from src.config import Configuration
from src.storage import STATE_DIR

CACHE_NAME = os.path.join(STATE_DIR, "git_cache")
PRECACHE_NAME = os.path.join(STATE_DIR, "git_precache")

_commit_time_cache = {}
_neighbor_cache = {}
_diff_cache = {}
_diff_precache = {}
_already_fetched = False
_cache_loaded = True


def load_cache() -> None:
    _load_cache(PRECACHE_NAME)
    _load_cache(CACHE_NAME)
    global _cache_loaded
    _cache_loaded = True


def _load_cache(cache_file_name: str) -> None:
    try:
        with open(cache_file_name, "r") as cache_file:
            section = 0
            for line in cache_file:
                if line.startswith("#"):
                    section += 1
                    continue
                parts = line.strip().split()
                if section == 0:
                    _commit_time_cache[parts[0]] = int(parts[1])
                elif section == 1:
                    src_commit, dst_commit = parts[0], parts[1]
                    if src_commit > dst_commit:
                        src_commit, dst_commit = dst_commit, src_commit
                    if src_commit not in _diff_cache:
                        _diff_cache[src_commit] = {}
                    _diff_cache[src_commit][dst_commit] = int(parts[2])
                elif section == 2:
                    _neighbor_cache[parts[0]] = set(parts[1:])
    except FileNotFoundError:
        pass


def save_cache() -> None:
    if not _cache_loaded:
        return
    with open(CACHE_NAME, "w") as cache_file:
        for commit, timestamp in _commit_time_cache.items():
            cache_file.write(f"{commit} {timestamp}\n")
        cache_file.write("#\n")
        for src_commit, dsts in _diff_cache.items():
            for dst_commit, size in dsts.items():
                cache_file.write(f"{src_commit} {dst_commit} {size}\n")
        cache_file.write("#\n")
        for commit, neighbors in _neighbor_cache.items():
            cache_file.write(f"{commit} {' '.join(neighbors)}\n")


def update_neighbors(commits: Optional[set[str]] = None) -> None:
    neighbors = defaultdict(set)
    if commits is None or any(commit not in _neighbor_cache for commit in commits):
        lines = get_git_output(["rev-list", "--parents", "--all"]).splitlines()
        lines += get_git_output(["rev-list", "--children", "--all"]).splitlines()
        for line in lines:
            parts = line.split()
            neighbors[parts[0]].update(set(parts[1:]))

        for commit in neighbors:
            _neighbor_cache[commit] = neighbors[commit]
        
        save_cache()


def get_bulk_neighbors(all_commits: list[str]) -> dict[str, set[str]]:
    update_neighbors(set(all_commits))
    return {commit: _neighbor_cache[commit] for commit in all_commits}


def get_neighbors(commit: str) -> set[str]:
    update_neighbors([commit])
    return _neighbor_cache[commit]


def sort_commits(commits_to_sort: list[str]) -> list[str]:
    possible_commits = set(commits_to_sort)
    visited = set()
    sorted_commits = []
    for commit in commits_to_sort:
        curr = commit
        while curr is not None and curr not in visited:
            possible_commits.remove(curr)
            sorted_commits.append(curr)
            curr = get_similar_commit(curr, possible_commits, visited)
    return sorted_commits


def get_similar_commit(target_commit: str, possible_commits: set[str]) -> str:
    # Performs a Dijkstra-like search to find the commit with the smallest diff size
    # to the target commit, excluding the commits in exclude_commits. Uses the diffs
    # between commits as the edge weights, which may overestimate the distance, but
    # it gets results within a few percent of optimal on godot.
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
        if curr in possible_commits:
            best_diff = total
            best = curr
            continue
        for neighbor in get_neighbors(curr):
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
    global _already_fetched
    get_git_output(["fetch", "--tags", "--prune", "origin"])
    cache_clear()
    _already_fetched = True


def get_git_output(args: list[str]) -> str:
    try:
        output = subprocess.check_output(["git", "-C", Configuration.WORKSPACE_PATH] + args).strip().decode("utf-8")
        if len(output) > 0 and output[0] == '"' and output[-1] == '"':
            output = output[1:-1]
        return output
    except subprocess.CalledProcessError:
        return ""


def get_commit_time(ref: str) -> int:
    commit = resolve_ref(ref)
    if commit not in _commit_time_cache:
        try:
            time_output = get_git_output(["show", "-s", "--format=%ct", commit])
            _commit_time_cache[commit] = int(time_output)
        except:
            return -1
    return _commit_time_cache[commit]


def get_commit_times(refs: list[str]) -> dict[str, int]:
    ref_commits = {
        ref: resolve_ref(ref) for ref in refs
    }
    missing_refs = [ref for ref, commit in ref_commits.items() if commit not in _commit_time_cache]
    if len(missing_refs) > 0:
        try:
            lines = get_git_output(["show", "-s", "--format=%ct"] + refs).split()
            for i, line in enumerate(lines):
                _commit_time_cache[ref_commits[refs[i]]] = int(line)
        except:
            return {}
    return {
        ref: _commit_time_cache[commit] 
        for ref, commit in ref_commits.items()
    }


@functools.lru_cache
def get_short_name(ref: str, plain: bool = False) -> str:
    commit = resolve_ref(ref)
    if len(commit) == 0:
        return ref if plain else terminal.color_bad(ref)
    short_name = get_git_output(["log", '--pretty=format:"%h"', commit, "-n", "1", "--abbrev-commit"])
    return short_name if plain else terminal.color_ref(short_name)


@functools.lru_cache
def get_short_log(ref: str) -> str:
    commit_message = get_git_output(["log", '--pretty=format:"%s"', ref, "-n", "1", "--abbrev-commit"])
    return get_short_name(ref) + " " + commit_message


@functools.lru_cache(maxsize=None)
def resolve_ref(ref: str, fetch_if_missing: bool = False) -> str:
    commit = get_git_output(["rev-parse", "--revs-only", ref.strip()])
    if fetch_if_missing and len(commit) == 0 and not _already_fetched:
        print(f"Resolving \"{ref}\" failed, fetching in case it's too recent...")
        fetch()
        return resolve_ref(ref, False)
    return commit


def get_commit_list(start_ref: str, end_ref: str, path_spec: str = "", before: int = -1) -> list[str]:
    return list(_get_commit_list(start_ref, end_ref, path_spec, before))


@functools.lru_cache
def _get_commit_list(start_ref: str, end_ref: str, path_spec: str = "", before: int = -1) -> list[str]:
    command = ["rev-list", "--reverse", f"{start_ref}..{end_ref}"]
    if before >= 0:
        command += [f"--before={before}"]
    if path_spec != "":
        command += ["--"] + shlex.split(path_spec)
    output = get_git_output(command)
    commit_list = [k for k in output.split() if k != ""]
    start_commit = resolve_ref(start_ref)
    if start_commit != "" and (len(commit_list) > 0 or start_commit == resolve_ref(end_ref)):
        commit_list = commit_list.insert(0, start_commit)
    return commit_list


def get_bisect_commits(good_refs: set[str], bad_refs: set[str], path_spec: str = "", before: int = -1) -> list[str]:
    command = ["rev-list", "--bisect-all"] + [f"^{commit}" for commit in good_refs] + list(bad_refs)
    if before >= 0:
        command += [f"--before={before}"]
    if path_spec != "":
        command += ["--"] + shlex.split(path_spec)
    output = get_git_output(command)
    return [line.strip().split()[0] for line in output.splitlines() if len(line.strip()) > 0]


def get_local_changes() -> bool:
    return [line.strip() for line in get_git_output(["add", "-An"]).strip().splitlines()]


def has_local_changes() -> bool:
    return len("".join(get_local_changes())) > 0


def clear_local_changes() -> None:
    get_git_output(["reset", "--hard", "HEAD"])
    get_git_output(["clean", "-df"])


def get_tags() -> list[str]:
    return [line.strip() for line in get_git_output(["tag", "-l"]).splitlines()]


def is_ancestor(possible_ancestor_ref: str, possible_descendant_ref: str) -> bool:
    return len(get_commit_list(possible_ancestor_ref, possible_descendant_ref)) > 0


_diffs_added = 0
def get_diff_size(commit_src: str, commit_dst: str) -> int:
    if commit_src > commit_dst:
        commit_src, commit_dst = commit_dst, commit_src
    
    for cache in (_diff_precache, _diff_cache):
        if commit_src in cache:
            if commit_dst in cache[commit_src]:
                return cache[commit_src][commit_dst]

    retval = _get_diff_size(commit_src, commit_dst)
    if commit_src not in _diff_cache:
        _diff_cache[commit_src] = {}
    _diff_cache[commit_src][commit_dst] = retval

    global _diffs_added
    _diffs_added += 1
    if _diffs_added % 100 == 0:
        save_cache()

    return retval


def _get_diff_size(ref_src: str, ref_dst: str) -> int:
    output = get_git_output(["diff", "--shortstat", ref_src, ref_dst])
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
    # TODO doesn't properly clear neighbor cache which may need new children
    _get_commit_list.cache_clear()
    get_short_name.cache_clear()
    get_short_log.cache_clear()
    resolve_ref.cache_clear()