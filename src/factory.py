import glob
import os
import shlex
import shutil
import sys
import time
import stat
from typing import Optional

import src.git as git
import src.signal_handler as signal_handler
import src.storage as storage
import src.terminal as terminal
from src.config import Configuration, PrintMode

MIN_SUCCESSES = 3
error_commits = set()


def compress(n: int = 1, retry: bool = False, compress_all: bool = False) -> bool:
    rev_list = git.query_rev_list(Configuration.RANGE_START, Configuration.RANGE_END)
    rev_list = git.sort_commits(rev_list)
    bundles = compute_bundles(rev_list, n, compress_all)
    for i, bundle in enumerate(bundles):
        bundle_id = bundle[0]
        print(f"Compressing bundle {i + 1} / {len(bundles)}")
        bundled = storage.compress_bundle(bundle_id, bundle)
        if not bundled:
            if retry:
                print(f"Retrying compression of bundle {bundle_id} once.")
                bundled = storage.compress_bundle(bundle_id, bundle)
            if not bundled:
                print("Failed to compress all bundles.")
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
                answer = input("Do you want to discard them? [y/N]").strip().lower()
                if answer.startswith("y"):
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


def get_remaining_time_str(commit_count: int, average_time: float, current_i: int) -> str:
    remaining_time_str = "--:--"
    if average_time > 0:
        remaining_time = int(average_time * (commit_count - current_i) + 0.99)
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
    return terminal.color_key(remaining_time_str)


def print_compile_status(tags: list[str], rev_list: list[str], present_commits: set[str], current_i: int, commits: list[str], times: dict[str, float], error_commits: list[str]) -> None:
    cols = terminal.get_cols()
    print(terminal.box_top(title=terminal.color_key(f" Compiling #{current_i + 1} of {len(commits)} ")))
    
    average_time = sum(times.values()) / len(times) if times else 0
    average_time_str = "--:--"
    if average_time > 0:
        seconds = int(round(average_time)) % 60
        minutes = int(round(average_time)) // 60
        average_time_str = f"{minutes:02}:{seconds:02}"
    remaining_time_str = get_remaining_time_str(len(commits), average_time, current_i)
    error_str = terminal.color_bad(f"{len(error_commits)}") if len(error_commits) > 0 else terminal.color_good("0")
    print(terminal.box_fit(
        f"Average time: {average_time_str}, Remaining time: {remaining_time_str}, Errors: {error_str}"
    ))

    print(terminal.box_fit(
        terminal.trim_str(f"Current commit: {git.get_short_log(commits[current_i])}", cols - 4)
    ))

    percent_done = current_i * 100.0 / max(1, len(commits))
    progress_bar = "Job progress (" + terminal.color_key(f"{int(percent_done):2d}%") + "): "
    progress_bar += terminal.progress_bar(cols - terminal.escape_len(progress_bar) - 4, percent_done)
    print(terminal.box_fit(progress_bar))
    print(terminal.box_fit(""))

    current_bucket = print_histogram(cols, rev_list, tags, commits[current_i], present_commits)
    bottom = terminal.box_bottom()
    if current_bucket is not None:
        bottom = bottom[:current_bucket + 2] + terminal.color_key("^") + bottom[current_bucket + 3:]
    print(bottom)


def get_fraction_completed(rev_list: list[str], present_commits: set[str]) -> float:
    if not Configuration.IGNORE_OLD_ERRORS:
        compiler_error_commits = storage.get_compiler_error_commits()
        rev_list = [rev for rev in rev_list if rev not in compiler_error_commits]
    return len([rev for rev in rev_list if rev in present_commits]) / max(1, len(rev_list))


def build_tag_line(tag_times: dict[str, int], bucket_times: list[int]) -> str:
    tag_buckets = {
        tag: [
            i for i in range(len(bucket_times)) 
            if bucket_times[i] != -1 and bucket_times[i] <= tag_time
            and (i == len(bucket_times) - 1 or bucket_times[i + 1] > tag_time)
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
    bucket_tags = [None] * len(bucket_times)
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
        elif tag_output_len + len(tag) + 1 < len(bucket_times):
            tag_output += terminal.color_rev(tag) + " "
            tag_output_len += len(tag) + 1
    return tag_output


def print_histogram(cols: int, rev_list: list[str], tags: list[str], current_commit: str, present_commits: set[str]) -> Optional[int]:
    tags = [tag for tag in tags if tag.find(".") == tag.rfind(".")]
    tag_times = git.get_commit_times(tags)
    tag_times = {
        tag[:tag.find("-")]: tag_time
        for tag, tag_time in tag_times.items()
    }
    ignored_commits = storage.get_ignored_commits()
    rev_list = [rev for rev in rev_list if rev not in ignored_commits]
    if len(rev_list) == 0:
        return None
    full_percent = get_fraction_completed(rev_list, present_commits) * 100
    print(terminal.box_middle(title=f" Full Range Histogram ({full_percent:.1f}%)"))

    split_revs = split_list(rev_list, cols - 4)
    buckets = [
        sum(1 for rev in split_revs[j] if rev in present_commits) / max(1, len(split_revs[j]))
        for j in range(len(split_revs))
    ]
    buckets += [0] * max(0, cols - 4 - len(buckets))
    commit_times = git.get_commit_times(
        [split_revs[i][0] for i in range(len(buckets)) if len(split_revs[i]) > 0]
    )

    if Configuration.SHOW_TAGS_ON_HISTOGRAM:
        bucket_times = [
            commit_times[split_revs[i][0]] if len(split_revs[i]) > 0 else -1
            for i in range(len(buckets))
        ]
        tag_line = build_tag_line(tag_times, bucket_times)
        print(terminal.box_fit(tag_line))

    print(terminal.box_fit(terminal.histogram_height(buckets)))
    if Configuration.COLOR_ENABLED:
        print(terminal.box_fit(terminal.histogram_color(buckets)))

    if Configuration.SHOW_TAGS_ON_HISTOGRAM:
        current_commit_time = git.get_commit_time(current_commit)
        possible_current_buckets = [
            i for i in range(len(buckets)) 
            if bucket_times[i] != -1 and bucket_times[i] <= current_commit_time
            and (i == len(buckets) - 1 or bucket_times[i + 1] > current_commit_time)
        ]
    else:
        possible_current_buckets = [i for i, revs in enumerate(split_revs) if current_commit in revs]
    return possible_current_buckets[0] if len(possible_current_buckets) > 0 else None


def compile(commits: list[str], should_compress: bool = True, n: int = 1, retry_compress: bool = False, fatal_compress: bool = False) -> bool:
    _handle_local_changes(True)

    present_commits = storage.get_present_commits()
    rev_list = git.query_rev_list(Configuration.RANGE_START, Configuration.RANGE_END)
    tags = git.get_tags()

    times = {}
    successes = 0
    for i, commit in enumerate(commits):
        start_time = time.time()
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
                storage.add_compiler_error_commits([commit])
            print(f"Error while compiling commit {commit}. Skipping.")
            error_commits.add(commit)
            continue
        cache(commit)
        present_commits.add(commit)
        successes += 1
        if successes == MIN_SUCCESSES and len(error_commits) > 0:
            print("Enough successful compilations have occurred to show that errors are specific to certain commits.")
            print("The following commits will be skipped in the future:")
            for commit in error_commits:
                print("\t" + commit)
            
            storage.add_compiler_error_commits(error_commits)
            print("If you fix their builds, you should remove them from compile_error_commit or run with --ignore-old-errors.")
                
        times[commit] = time.time() - start_time

        if i % (Configuration.COMPRESS_PACK_SIZE * 2) == 0 and i > 0 and not signal_handler.SHOULD_EXIT and should_compress:
            if not compress(n, retry_compress):
                if fatal_compress:
                    print("Terminating compilation due to compression failure.")
                    return False
                else:
                    print("WARNING: Compression failed, continuing compilation anyways.")

        if signal_handler.SHOULD_EXIT:
            return True

    return signal_handler.SHOULD_EXIT or not should_compress or compress(n, retry_compress)


def compute_bundles(rev_list: list[str], n: int, compress_all: bool = False) -> list[list[str]]:
    compress_map = storage.read_compress_map()
    unbundled_versions = [rev for rev in rev_list if rev not in compress_map]
    ready_to_bundle = storage.get_unbundled_files()
    if compress_all:
        for unbundled in ready_to_bundle:
            if unbundled not in unbundled_versions:
                unbundled_versions.append(unbundled)
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
                if not_ready_seen >= n and not compress_all:
                    break
            bundle_start_i += 1
        
        if compress_all or len(bundle) >= Configuration.COMPRESS_PACK_SIZE:
            bundles.append(bundle)

    return bundles


def get_compiled_path() -> str:
    return os.path.join(Configuration.WORKSPACE_PATH, "bin", Configuration.BINARY_NAME)


def cache(current_commit: str = None) -> None:
    if current_commit is None:
        current_commit = git.resolve_ref("HEAD")
    print(f"Caching commit {current_commit}")

    storage_path = os.path.join(Configuration.WORKSPACE_PATH, "versions", current_commit)
    if os.path.exists(storage_path):
        print(f"Cached version '{storage_path}' already exists. Overwriting it.")
        shutil.rmtree(storage_path)
    os.makedirs(storage_path, exist_ok=True)

    for archive_path in Configuration.ARCHIVE_PATHS:
        full_glob_path = os.path.join(Configuration.WORKSPACE_PATH, archive_path)
        for file_path in glob.glob(full_glob_path, recursive=True):
            abs_file_path = os.path.abspath(file_path)
            if not abs_file_path.startswith(os.path.abspath(Configuration.WORKSPACE_PATH)):
                print(f"Error: Attempted to copy a file outside the workspace directory: {file_path}")
                sys.exit(1)

            relative_path = os.path.relpath(abs_file_path, Configuration.WORKSPACE_PATH)
            destination_path = os.path.join(storage_path, relative_path)
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
            shutil.move(abs_file_path, destination_path)

    print(f"Commit {current_commit} has been successfully cached.")


def single() -> bool:
    return terminal.execute_in_subwindow(
        command=["scons"] + shlex.split(Configuration.COMPILER_FLAGS),
        title="scons",
        rows=Configuration.SUBWINDOW_ROWS,
        cwd=Configuration.WORKSPACE_PATH,
    )
