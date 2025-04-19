import os
import shutil
import sys
import time
import shlex

from src.config import Configuration, PrintMode
import src.git as git
import src.signal_handler as signal_handler
import src.storage as storage
import src.terminal as terminal

successes = 0
MIN_SUCCESSES = 3
error_commits = set()


def compress(n: int = 1, retry: bool = False, all: bool = False) -> bool:
    bundles = compute_bundles(n, all)
    for i, bundle in enumerate(bundles):
        bundle_id = bundle[0]
        print(f"Compressing bundle {i + 1} / {len(bundles)}")
        bundled = storage.compress_bundle(bundle_id, bundle)
        if not bundled:
            if retry:
                print(f"Retrying compression of bundle {bundle_id} once.")
                bundled = storage.compress_bundle(bundle_id, bundle)
            if not bundled:
                print(f"Failed to compress all bundles.")
                return False
        if signal_handler.SHOULD_EXIT:
            break
    return True


def _handle_local_changes(ask: bool) -> None:
    if git.has_local_changes():
        print("Local changes detected in the godot workspace.")
        if Configuration.FORCE:
            print("Discarding them in preparation for compilation.")
            git.clear_local_changes()
        else:
            for line in git.get_local_changes():
                print("\t" + line)
            if ask:
                print("Do you want to discard them? (y/n)")
                answer = input().strip().lower()
                if answer == "y":
                    print("Discarding. Run with -f/--force to prevent this question in the future.")
                    git.clear_local_changes()
                    return
                print("Please commit or stash your changes before trying again.")
            else:
                print("Please commit or stash them before running this script.")
                print("If the changes were left behind by a previous error, you can rerun the command with -f/--force to discard them.")
            sys.exit(1)


def compile_uncached(commit: str) -> bool:
    _handle_local_changes(True)

    git.check_out(commit)
    error_code = single()
    return error_code == 0


def split_list(lst: list, x: int) -> list[list]:
    avg_size = len(lst) // x
    remainder = len(lst) % x

    parts = []
    start = 0

    for i in range(x):
        end = start + avg_size + (1 if i < remainder else 0)
        parts.append(lst[start:end])
        start = end

    return parts


def print_compile_status(tags: list[str], rev_list: list[str], present_commits: set[str], current_i: int, commits: list[str], times: dict[str, float], error_commits: list[str]) -> None:
    tags = [tag for tag in tags if tag.find(".") == tag.rfind(".")]
    tag_times = git.get_commit_times(tags)
    tag_times = {
        tag[:tag.find("-")]: tag_time
        for tag, tag_time in tag_times.items()
    }
    box_side = "┃"
    cols = terminal.get_cols()
    print(terminal.box_top(title=terminal.color_key(f" Compiling #{current_i + 1} of {len(commits)} ")))
    
    percent_done = int(current_i / len(commits) * 100)
    average_time = sum(times.values()) / len(times) if times else 0
    average_time_str = "--:--"
    if average_time > 0:
        seconds = int(round(average_time)) % 60
        minutes = int(round(average_time)) // 60
        average_time_str = f"{minutes:02}:{seconds:02}"
    remaining_time_str = "--:--"
    if average_time > 0:
        remaining_time = int(average_time * (len(commits) - current_i) + 0.99)
        seconds = remaining_time % 60
        minutes = remaining_time // 60
        hours = minutes // 60
        minutes = minutes % 60
        if hours >= 72:
            remaining_time_str = f"{remaining_time / 60.0 / 60 / 24:.1f} days"
        elif hours >= 1:
            remaining_time_str = f"{remaining_time / 60.0 / 60:.1f} hours"
        else:
            remaining_time_str = f"{minutes:02}:{seconds:02}"
    remaining_time_str = terminal.color_key(remaining_time_str)
    error_str = terminal.color_bad(f"{len(error_commits)}") if len(error_commits) > 0 else terminal.color_good("0")
    terminal.print_fit_line(
        f"Average time: {average_time_str}, Remaining time: {remaining_time_str}, Errors: {error_str}",
        start=box_side, end=box_side
    )
    terminal.print_fit_line(
        terminal.trim_str(f"Current commit: {git.get_short_log(commits[current_i])}", cols - 4),
        start=box_side, end=box_side
    )
    progress_start = box_side + " Job progress (" + terminal.color_key(f"{percent_done:2d}%") + "): "
    print(progress_start, end="")
    terminal.print_progress_bar(cols - terminal.escape_len(progress_start) - 2, float(current_i) / len(commits))
    print(" " + box_side)
    terminal.print_fit_line("", start=box_side, end=box_side)
    print(terminal.box_middle(title=" Full Range Timeline Histogram "))
    split_revs = split_list(rev_list, cols - 4)
    buckets = [
        sum(1 for rev in split_revs[j] if rev in present_commits) / len(split_revs[j])
        for j in range(len(split_revs))
    ]
    buckets += [0] * max(0, cols - 4 - len(buckets))
    commit_times = git.get_commit_times(
        [split_revs[i][0] for i in range(len(buckets)) if len(split_revs[i]) > 0]
    )
    bucket_times = [
        commit_times[split_revs[i][0]] if len(split_revs[i]) > 0 else -1
        for i in range(len(buckets))
    ]
    tag_buckets = {
        tag: [
            i for i in range(len(buckets)) 
            if bucket_times[i] != -1 and bucket_times[i] <= tag_time
            and (i == len(buckets) - 1 or bucket_times[i + 1] > tag_time)
        ]
        for tag, tag_time in tag_times.items()
        if tag_time != -1
    }
    tag_buckets = {
        tag: i[0]
        for tag, i in tag_buckets.items()
        if len(i) > 0
    }
    tag_sorted = list(sorted(tag_buckets.keys()))
    bucket_tags = [None] * len(buckets)
    for tag in tag_sorted[::-1]:
        bucket_tags[tag_buckets[tag]] = tag
    tag_output = ""
    tag_output_len = 0
    for i, tag in enumerate(bucket_tags):
        if tag_output_len > i:
            continue
        if tag is None:
            if tag_output_len != i:
                tag_output += " "
                tag_output_len += 1
        elif tag_output_len + len(tag) + 1 < cols - 4:
            tag_output += terminal.color_rev(tag) + " "
            tag_output_len += len(tag) + 1
    terminal.print_fit_line(tag_output, start=box_side, end=box_side)
    print(box_side + " ", end="")
    terminal.print_histogram_height(buckets)
    print(" " + box_side)
    print(box_side + " ", end="")
    terminal.print_histogram_color(buckets)
    print(" " + box_side)
    bottom = terminal.box_bottom()
    current_commit_time = git.get_commit_time(commits[current_i])
    current_bucket = [
        i for i in range(len(buckets)) 
        if bucket_times[i] != -1 and bucket_times[i] <= current_commit_time
        and (i == len(buckets) - 1 or bucket_times[i + 1] > current_commit_time)
    ]
    current_bucket = current_bucket[0] if len(current_bucket) > 0 else None
    if current_bucket:
        bottom = bottom[:current_bucket + 2] + terminal.color_key("^") + bottom[current_bucket + 3:]
    print(bottom)


def compile(commits: list[str], should_compress: bool = True, n: int = 1, retry_compress: bool = False, fatal_compress: bool = False) -> bool:
    _handle_local_changes(True)

    present_commits = storage.get_present_commits()
    rev_list = storage.read_rev_list()
    tags = git.get_tags()

    start_time = time.time()
    times = {}
    backoff = 1
    for i, commit in enumerate(commits):
        if Configuration.PRINT_MODE == PrintMode.QUIET:
            print(f"Compiling commit {commit} ({i + 1} / {len(commits)})")
        else:
            print_compile_status(tags, rev_list, present_commits, i, commits, times, error_commits)

        git.check_out(commit)
        did_compile = single()
        if not did_compile:
            if successes >= MIN_SUCCESSES:
                print(f"Error while compiling commit {commit}.")
                print("Adding to the compile_error_commit file so it's skipped in the future.")
                print("If you fix its build, you should remove it from the file or run with --ignore-old-errors.")
                mark_error(commit)
            print(f"Error while compiling commit {commit}. Skipping.")
            error_commits.add(commit)
            start_time = time.time()
            continue
        cache(commit)
        present_commits.add(commit)
        successes += 1
        if successes == MIN_SUCCESSES and len(error_commits) > 0:
            print("Enough successful compilations have occurred to show that errors are specific to certain commits.")
            print("The following commits will be skipped in the future:")
            for commit in error_commits:
                print("\t" + commit)
                mark_error(commit)
            print("If you fix their builds, you should remove them from compile_error_commit or run with --ignore-old-errors.")
                
        times[commit] = time.time() - start_time

        if i % (Configuration.COMPRESS_PACK_SIZE * 2) == 0 and i > 0 and not signal_handler.SHOULD_EXIT and should_compress:
            if not compress(n, retry_compress):
                if fatal_compress:
                    print("Terminating compilation due to compression failure.")
                    return False
                else:
                    print("WARNING: Compression failed, continuing compilation anyways.")

        start_time = time.time()
        if signal_handler.SHOULD_EXIT:
            return True

    return signal_handler.SHOULD_EXIT or not should_compress or compress(n, retry_compress)


def mark_error(commit: str) -> None:
    pass


def compute_bundles(n: int, all: bool = False) -> list[list[str]]:
    rev_list = storage.read_rev_list()
    compress_map = storage.read_compress_map()
    unbundled_versions = [rev for rev in rev_list if rev not in compress_map]
    ready_to_bundle = storage.get_unbundled_files()
    bundle_start_i = 0
    bundles = []
    while bundle_start_i < len(unbundled_versions):
        if unbundled_versions[bundle_start_i] not in ready_to_bundle:
            bundle_start_i += 1
            continue
        bundle = [unbundled_versions[bundle_start_i]]
        not_ready_seen = 0
        bundle_start_i += 1
        while bundle_start_i < len(unbundled_versions):
            if unbundled_versions[bundle_start_i] in ready_to_bundle:
                not_ready_seen = 0
                bundle.append(unbundled_versions[bundle_start_i])
                if len(bundle) >= Configuration.COMPRESS_PACK_SIZE:
                    bundle_start_i += 1
                    break
            else:
                not_ready_seen += 1
                if not_ready_seen >= n and not all:
                    break
            bundle_start_i += 1
        
        if all or len(bundle) >= Configuration.COMPRESS_PACK_SIZE:
            bundles.append(bundle)

    return bundles


def get_compiled_path() -> str:
    return os.path.join(Configuration.WORKSPACE_PATH, "bin", Configuration.BINARY_NAME)


def cache(current_commit: str = None) -> None:
    if current_commit is None:
        current_commit = git.resolve_ref("HEAD")
    print(f"Caching commit {current_commit}")
    compiled_path = get_compiled_path()
    storage_path = os.path.join("versions", current_commit)
    shutil.move(compiled_path, storage_path)
    try:
        os.chmod(storage_path, os.stat(storage_path).st_mode | os.stat.S_IXUSR | os.stat.S_IXGRP | os.stat.S_IXOTH)
    except:
        pass


def single() -> bool:
    return terminal.execute_in_subwindow(
        command=["scons"] + shlex.split(Configuration.COMPILER_FLAGS),
        title="scons", 
        rows=Configuration.SUBWINDOW_ROWS,
        cwd=Configuration.WORKSPACE_PATH,
    )