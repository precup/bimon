import functools
import heapq
import os
import shlex
import subprocess
from collections import deque
from typing import Optional

from src import storage
from src import terminal
from src.config import Configuration, PrintMode

_CACHE_NAME = "git_cache"
_PRECACHE_NAME = "git_precache"
_UPDATES_PER_SAVE = 100

_commit_time_precache: dict[str, int] = {}
_parent_precache: dict[str, set[str]] = {}
_diff_precache: dict[str, dict[str, int]] = {}

_commit_time_cache: dict[str, int] = {}
_child_cache: dict[str, set[str]] = {}
_parent_cache: dict[str, set[str]] = {}
_diff_cache: dict[str, dict[str, int]] = {}
_already_fetched = False
_cache_loaded = True
_cache_updates = 0



def load_cache() -> None:
    _load_cache(_PRECACHE_NAME, _commit_time_precache, _parent_precache, _diff_precache)
    _load_cache(_CACHE_NAME, _commit_time_cache, _parent_cache, _diff_cache)
    global _cache_loaded
    _cache_loaded = True


def _load_cache(
        cache_name: str, 
        commit_time_cache: dict[str, int], 
        parent_cache: dict[str, set[str]], 
        diff_cache: dict[str, dict[str, int]]) -> None:
    cache_str = storage.load_state(cache_name)
    section = 0
    for line in cache_str.splitlines():
        if line.startswith("#"):
            section += 1
            continue
        parts = line.strip().split()
        if section == 0:
            commit_time_cache[parts[0]] = int(parts[1])
        elif section == 1:
            src_commit, dst_commit = parts[0], parts[1]
            if src_commit > dst_commit:
                src_commit, dst_commit = dst_commit, src_commit
            if src_commit not in diff_cache:
                diff_cache[src_commit] = {}
            diff_cache[src_commit][dst_commit] = int(parts[2])
        elif section == 2:
            _child_cache[parts[0]] = set(parts[1:])
        elif section == 3:
            parent_cache[parts[0]] = set(parts[1:])


def save_cache(overwrite: bool = False) -> None:
    if not _cache_loaded and not overwrite:
        return
    _cache_updates = 0
    _save_cache(_CACHE_NAME, _commit_time_cache, _diff_cache, _child_cache, _parent_cache)


def delete_cache(dry_run: bool = False) -> int:
    deleted = 0
    def delete_single_cache(cache_name: str) -> None:
        nonlocal deleted
        cache_path = storage.get_state_filename(cache_name)
        if os.path.exists(cache_path):
            if dry_run:
                print(f"Would delete {cache_path}")
            else:
                if Configuration.PRINT_MODE != PrintMode.QUIET:
                    print(f"Deleting {cache_path}")
                os.remove(cache_path)
            deleted += 1

    delete_single_cache(_CACHE_NAME)
    # delete_single_cache(_PRECACHE_NAME)
    return deleted


def save_precache() -> None:
    merged_diff_cache = dict(_diff_cache)
    merged_diff_cache.update(_diff_precache)
    merged_time_cache = dict(_commit_time_cache)
    merged_time_cache.update(_commit_time_precache)
    merged_parent_cache = dict(_parent_cache)
    merged_parent_cache.update(_parent_precache)
    _save_cache(_PRECACHE_NAME, merged_time_cache, merged_diff_cache, {}, merged_parent_cache)


def _save_cache(
        cache_name: str,
        commit_time_cache: dict[str, int],
        diff_cache: dict[str, dict[str, int]],
        child_cache: dict[str, set[str]],
        parent_cache: dict[str, set[str]]) -> None:
    cache_str = ""
    for commit, timestamp in commit_time_cache.items():
        cache_str += f"{commit} {timestamp}\n"
    cache_str += "#\n"
    for src_commit, dsts in diff_cache.items():
        for dst_commit, size in dsts.items():
            cache_str += f"{src_commit} {dst_commit} {size}\n"
    cache_str += "#\n"
    for commit, neighbors in child_cache.items():
        cache_str += f"{commit} " + " ".join(neighbors) + "\n"
    cache_str += "#\n"
    for commit, neighbors in parent_cache.items():
        cache_str += f"{commit} " + " ".join(neighbors) + "\n"
    storage.save_state(cache_name, cache_str)


def update_neighbors(commits: Optional[set[str]] = None) -> None:
    should_update = False
    if commits is None:
        print("Updating git cache...")
        should_update = True
    else:
        should_update = not all(
            (commit in _parent_cache or commit in _parent_precache) and commit in _child_cache
            for commit in commits
        )
    if should_update:
        updates_made = 0
        for flag, cache in [("--children", _child_cache), ("--parents", _parent_cache)]:
            lines = get_git_output(["rev-list", "--use-bitmap-index", flag, "--all"]).splitlines()
            for line in lines:
                parts = line.split()
                if flag != "--parents" or parts[0] not in _parent_precache:
                    cache[parts[0]] = set(parts[1:])
                    updates_made += 1

        if commits is None:
            save_cache()
        elif updates_made > 0:
            _mark_cache_update(updates_made)


def get_neighbors(commit: str) -> set[str]:
    update_neighbors({commit})
    parent_cache = _parent_precache if commit in _parent_precache else _parent_cache
    return _child_cache[commit] | parent_cache[commit]


def sort_commits(commits_to_sort: list[str]) -> list[str]:
    possible_commits = set(commits_to_sort)
    sorted_commits = []
    for commit in commits_to_sort:
        curr = commit
        while curr is not None and curr in possible_commits:
            possible_commits.remove(curr)
            sorted_commits.append(curr)
            curr = get_similar_commit(curr, possible_commits)
    return sorted_commits


def get_similar_commit(target_commit: str, possible_commits: set[str]) -> str:
    # Performs a Dijkstra-like search to find the commit with the smallest diff size
    # to the target commit, excluding the commits in exclude_commits. Uses the diffs
    # between commits as the edge weights, which may overestimate the distance, but
    # it gets results within a few percent of optimal on godot.
    if len(possible_commits) == 0:
        return ""
    if len(possible_commits) == 1:
        return list(possible_commits)[0]
    queue: list[tuple[int, str]] = []
    heapq.heappush(queue, (0, target_commit))
    best_per: dict[str, int] = {}
    best = ""
    best_diff = -1
    while queue:
        total, curr = heapq.heappop(queue)
        if curr in best_per and total > best_per[curr]:
            continue
        best_per[curr] = total
        if best != "" and total >= best_diff:
            continue
        if curr in possible_commits:
            best_diff = total
            best = curr
            continue
        for neighbor in get_neighbors(curr):
            diff = get_diff_size(curr, neighbor)
            neighbor_total = total + diff
            if best != "" and neighbor_total >= best_diff:
                continue
            if neighbor not in best_per or neighbor_total < best_per[neighbor]:
                best_per[neighbor] = neighbor_total
                heapq.heappush(queue, (neighbor_total, neighbor))
    return best


def clone(repository: str, target: str) -> bool:
    try:
        os.system(f"git clone {repository} {target}")
        if not 'builds' in repository:
            _cache_clear()
            _already_fetched = True
        return True
    except Exception:
        return False


def get_pull_branch_name(pull_number: int) -> str:
    return f"pr-{pull_number}"


def check_out(rev: str) -> None:
    get_git_output(["checkout", "-q", rev])


def check_out_pull(pull_number: int, branch_name: Optional[str] = None) -> None:
    if branch_name is None:
        branch_name = get_pull_branch_name(pull_number)
    get_git_output(["checkout", "--detach"])
    get_git_output(["fetch", "origin", f"+pull/{pull_number}/head:" + branch_name])
    check_out(branch_name)


def fetch(repository: str = Configuration.WORKSPACE_PATH) -> bool:
    global _already_fetched
    if Configuration.WORKSPACE_PATH == repository and _already_fetched:
        return False
    fetch_command = ["fetch", "--tags", "--prune", "origin"]
    fetch_output = get_git_output(fetch_command, include_err=True)
    if len(fetch_output) > 0:
        print()
        print(fetch_output)
        print()
        _cache_clear()

    if Configuration.WORKSPACE_PATH == repository:
        _already_fetched = True

    return len(fetch_output) > 0


def get_last_fetch_time() -> int:
    fetch_time = get_git_output(["show", "-s", "--format=%ct", "FETCH_HEAD"])
    return int(fetch_time) if fetch_time.isdigit() else -1


def get_git_output(
        args: list[str],
        include_err: bool = False,
        input_str: Optional[str] = None,
        repository: str = Configuration.WORKSPACE_PATH) -> str:
    try:
        command = ["git", "-C", repository] + args
        output_bytes = subprocess.check_output(
            command,
            stderr=subprocess.STDOUT if include_err else None,
            input=input_str.encode("utf-8") if input_str is not None else None)
        output = output_bytes.decode("utf-8").strip()
        if len(output) > 0 and output[0] == '"' and output[-1] == '"':
            output = output[1:-1]
        return output
    except subprocess.CalledProcessError:
        return ""


def get_all_descendants(ref: str) -> set[str]:
    return _get_all_relative_types(ref, _child_cache, {})


def get_all_ancestors(ref: str) -> set[str]:
    return _get_all_relative_types(ref, _parent_cache, _parent_precache)


def _get_all_relative_types(
        ref: str, 
        relative_cache: dict[str, set[str]], 
        relative_precache: dict[str, set[str]]) -> set[str]:
    commit = resolve_ref(ref)
    seen = {commit}
    queue = deque([commit])
    while queue:
        curr = queue.popleft()
        update_neighbors({curr})
        cache = relative_precache if curr in relative_precache else relative_cache
        for parent in cache[curr]:
            if parent not in seen:
                seen.add(parent)
                queue.append(parent)
    return seen


def get_commit_time(ref: str) -> int:
    commit = resolve_ref(ref)
    if commit in _commit_time_precache:
        return _commit_time_precache[commit]

    if commit not in _commit_time_cache:
        time_output = get_git_output(["show", "-s", "--format=%ct", commit])
        if time_output.isdigit():
            _commit_time_cache[commit] = int(time_output)
            _mark_cache_update()
        else:
            return -1
    return _commit_time_cache[commit]


def get_commit_times(refs: list[str]) -> dict[str, int]:
    refs = list(set(refs))
    ref_commits = {
        ref: resolve_ref(ref) for ref in refs
    }
    missing_refs = [
        ref for ref, commit in ref_commits.items() 
        if commit not in _commit_time_cache and commit not in _commit_time_precache
    ]
    if len(missing_refs) > 0:
        lines = get_git_output(["show", "-s", "--format=%ct"] + refs).split()
        for i, line in enumerate(lines):
            if line.isdigit():
                _commit_time_cache[ref_commits[refs[i]]] = int(line)
            else:
                return {}
        _mark_cache_update(len(lines))

    return {
        ref: _commit_time_precache[commit]
        if commit in _commit_time_precache
        else _commit_time_cache.get(commit, -1)
        for ref, commit in ref_commits.items()
    }


@functools.lru_cache
def get_short_name(ref: str, plain: bool = False) -> str:
    commit = resolve_ref(ref)
    if len(commit) == 0:
        return ref if plain else terminal.color_bad(ref)

    command = ["log", "--pretty=format:\"%h\"", commit, "-n", "1", "--abbrev-commit"]
    short_name = get_git_output(command)
    return short_name if plain else terminal.color_ref(short_name)


@functools.lru_cache
def get_short_log(ref: str) -> str:
    command = ["log", "--pretty=format:\"%s\"", ref, "-n", "1", "--abbrev-commit"]
    commit_message = get_git_output(command)
    return get_short_name(ref) + " " + terminal.color_log(commit_message)


# TODO this should probably return an optional but whatever
def resolve_ref(ref: str, fetch_if_missing: bool = False, use_cache: bool = True) -> str:
    if use_cache and ref != "HEAD" and "pull" not in ref:
        commit = _resolve_ref_cached(ref)
    else:
        commit = _resolve_ref_uncached(ref)

    if fetch_if_missing and len(commit) == 0 and not _already_fetched:
        print(terminal.warn(f"Resolving \"{ref}\" failed, fetching in case it's too recent..."))
        fetch()
        return resolve_ref(ref, False, use_cache)
    return commit


def _resolve_ref_uncached(ref: str) -> str:
    ref = ref.strip()
    output = get_git_output(["rev-parse", "--revs-only", ref + "^{commit}"], include_err=True)
    ref_lines = [line.strip() for line in output.splitlines() if ref in line]
    if len(ref) > 0 and not ref.isdigit() and len(ref_lines) > 1:
        print(terminal.error("Potentially ambiguous reference requested."))
        return ""
    return output


@functools.lru_cache(maxsize=None)
def _resolve_ref_cached(ref: str) -> str:
    if ref in _commit_time_cache or ref in _commit_time_precache:
        return ref
    return _resolve_ref_uncached(ref)


# TODO the refs should be optional, empty string sentinels are ugly here
def get_commit_list(
        start_ref: str,
        end_ref: str,
        path_spec: Optional[str] = None,
        before: int = -1) -> list[str]:
    return list(_get_commit_list(start_ref, end_ref, path_spec, before))


@functools.lru_cache
def _get_commit_list(
        start_ref: str,
        end_ref: str,
        path_spec: Optional[str] = None,
        before: int = -1) -> list[str]:
    command = ["rev-list", "--use-bitmap-index", "--reverse"]
    if before >= 0:
        command += [f"--before={before}"]
    if end_ref == "":
        command += ["--all"]
        if start_ref != "":
            command += [f"^{start_ref}"]
    else:
        command += [(f"{start_ref}.." if start_ref != "" else "") + end_ref]
    if path_spec is not None and path_spec != "":
        command += ["--"] + shlex.split(path_spec, posix='nt' != os.name)

    output = get_git_output(command)
    commit_list = [k for k in output.split() if k != ""]
    start_commit = resolve_ref(start_ref)
    if start_commit != "" and (len(commit_list) > 0 or start_commit == resolve_ref(end_ref)):
        commit_list.insert(0, start_commit)
    return commit_list


def get_parents(ref: str) -> set[str]:
    commit = resolve_ref(ref)
    if commit in _parent_precache:
        return _parent_precache[commit]
    if commit not in _parent_cache:
        update_neighbors({commit})
    return _parent_cache[commit]


def get_bisect_commits(
        good_refs: set[str],
        bad_ref: str,
        path_spec: Optional[str] = None,
        before: int = -1) -> list[str]:
    command = (
        ["rev-list", "--use-bitmap-index", "--bisect-all", bad_ref]
        + [f"^{commit}" for commit in good_refs]
    )
    if before >= 0:
        command += [f"--before={before}"]
    if path_spec is not None and path_spec != "":
        command += ["--"] + shlex.split(path_spec, posix='nt' != os.name)
    output = get_git_output(command)
    return [line.strip().split()[0] for line in output.splitlines() if len(line.strip()) > 0]


def get_bisect_steps_from_remaining(remaining: int) -> float:
    if remaining <= 0:
        return 0

    steps = 0
    while True:
        if remaining == 3:
            return 5/3 + steps
        elif remaining == 2:
            return 1 + steps
        elif remaining <= 1:
            return steps
        steps += 1
        remaining = (remaining + 1) // 2


def has_local_changes() -> bool:
    return len(get_git_output(["add", "-An"]).strip()) > 0


def clear_local_changes() -> None:
    get_git_output(["reset", "--hard", "HEAD"])
    get_git_output(["clean", "-df"])


def get_tags() -> list[str]:
    return [line.strip() for line in get_git_output(["tag", "-l"]).splitlines()]


def is_ancestor(possible_ancestor_ref: str, possible_descendant_ref: str) -> bool:
    if possible_ancestor_ref == "" or possible_descendant_ref == "":
        return False
    possible_ancestor_ref = resolve_ref(possible_ancestor_ref)
    if possible_ancestor_ref == "":
        return False
    merge_base = get_merge_base(possible_ancestor_ref, possible_descendant_ref)
    return merge_base == possible_ancestor_ref


def get_diff_size(commit_src: str, commit_dst: str) -> int:
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

    _mark_cache_update()

    return retval


def _mark_cache_update(count: int = 1) -> None:
    global _cache_updates
    _cache_updates += count
    if _cache_updates % _UPDATES_PER_SAVE == 0:
        save_cache()


def _get_diff_size(ref_src: str, ref_dst: str) -> int:
    output = get_git_output(["diff", "--shortstat", ref_src, ref_dst])
    if len(output) == 0:
        return 0
    try:
        output_parts = output.split()
        if len(output_parts) < 4:
            print(terminal.error("please report: bad parts", "UNEXPECTED INTERNAL ERROR"),
                output_parts)
            return 0

        retval = int(output_parts[3])
        if len(output_parts) >= 6:
            retval = max(retval, int(output_parts[5]))
        return retval
    except ValueError:
        return 0


def minimal_parents(parents: set[str]) -> set[str]:
    return {
        commit for commit in parents
        if not any(
            is_ancestor(test_commit, commit)
            for test_commit in parents if commit != test_commit
        )
    }


def minimal_children(children: set[str]) -> set[str]:
    return {
        commit for commit in children
        if not any(
            is_ancestor(commit, test_commit)
            for test_commit in children if commit != test_commit
        )
    }


@functools.lru_cache(maxsize=None)
def get_merge_base(commit_a: str, commit_b: str) -> str:
    if commit_a == commit_b:
        return commit_a
    return get_git_output(["merge-base", commit_a, commit_b])


def add_tags(tags: dict[str, str]) -> None:
    existing_tags = get_tags()
    for tag, commit in tags.items():
        if tag not in existing_tags:
            get_git_output(["tag", tag, commit])


def _cache_clear() -> None:
    _child_cache.clear()
    update_neighbors(None)
    _get_commit_list.cache_clear()
    get_short_name.cache_clear()
    get_short_log.cache_clear()
    get_merge_base.cache_clear()
    _resolve_ref_cached.cache_clear()