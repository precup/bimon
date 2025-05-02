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


def compress(
        compiled_versions: list[str], 
        retry: bool = False, 
        compress_all: bool = False) -> bool:
    compiled_versions_set = set(compiled_versions)
    unbundled_versions = set(storage.get_unbundled_versions())
    version_list = [version for version in compiled_versions if version in unbundled_versions]
    # TODO rework, this still kind of sucks
    # commit_list += [
    #     version for version in unbundled_versions if version not in compiled_versions_set
    # ]
    if len(version_list) == 0:
        return True

    version_list = git.sort_commits(version_list)

    bundles = []
    for i in range(0, len(version_list), Configuration.BUNDLE_SIZE):
        bundle = version_list[i:i + Configuration.BUNDLE_SIZE]
        if len(bundle) == Configuration.BUNDLE_SIZE or compress_all:
            bundles.append(bundle)
            
    for i, bundle in enumerate(bundles):
        bundle_id = bundle[0] + ".tar.zst"
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


def _handle_local_changes() -> None:
    if git.has_local_changes():
        git.clear_local_changes()


def compile_uncached(ref: str) -> bool:
    _handle_local_changes()

    git.check_out(ref)
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


def get_remaining_time_str(job_count: int, average_time: float, processed_count: int) -> str:
    remaining_time_str = "--:--"
    if average_time > 0:
        remaining_time = int(average_time * (job_count - processed_count) + 0.99)
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


def print_compile_status(
        tags: list[str], 
        full_commit_list: list[str], 
        present_versions: set[str], 
        processed_count: int, 
        job_commits: list[str], 
        times: dict[str, float], 
        error_commits: list[str], 
        current_commit: str) -> None:
    cols = terminal.get_cols()
    title = terminal.color_key(f" Compiling #{processed_count + 1} of {len(job_commits)} ")
    print(terminal.box_top(title=title))
    
    average_time = sum(times.values()) / len(times) if times else 0
    average_time_str = "--:--"
    if average_time > 0:
        seconds = int(round(average_time)) % 60
        minutes = int(round(average_time)) // 60
        average_time_str = f"{minutes:02}:{seconds:02}"
    remaining_time_str = get_remaining_time_str(len(job_commits), average_time, processed_count)
    error_str = terminal.color_good("0")
    if len(error_commits) > 0:
        error_str = terminal.color_bad(f"{len(error_commits)}")
    print(terminal.box_fit(f"Average time: {average_time_str},"
        + f" Remaining time: {remaining_time_str}, Errors: {error_str}"))

    print(terminal.box_fit(
        terminal.trim_str(f"Current commit: {git.get_short_log(current_commit)}", cols - 4)
    ))

    fraction_done = float(processed_count) / max(1, len(job_commits))
    progress_bar = "Job progress (" + terminal.color_key(f"{int(fraction_done * 100):2d}%") + "): "
    progress_bar_length = cols - 4 - terminal.escape_len(progress_bar)
    progress_bar += terminal.progress_bar(progress_bar_length, fraction_done)
    print(terminal.box_fit(progress_bar))
    print(terminal.box_fit(""))

    current_bucket = print_histogram(
        cols=cols, 
        full_commit_list=full_commit_list, 
        tags=tags, 
        current_commit=current_commit, 
        present_versions=present_versions
    )
    bottom = terminal.box_bottom()
    if current_bucket is not None:
        bottom = (
            bottom[:current_bucket + 2] 
            + terminal.color_key("^") 
            + bottom[current_bucket + 3:]
        )
    print(bottom)


def get_fraction_completed(commit_list: list[str], present_versions: set[str]) -> float:
    if not Configuration.IGNORE_OLD_ERRORS:
        compiler_error_commits = storage.get_compiler_error_commits()
        commit_list = [commit for commit in commit_list if commit not in compiler_error_commits]
    present_commits = [commit for commit in commit_list if commit in present_versions]
    return len(present_commits) / max(1, len(commit_list))


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
            tag_output += terminal.color_ref(tag) + " "
            tag_output_len += len(tag) + 1
    return tag_output


def print_histogram(
        cols: int, 
        full_commit_list: list[str], 
        tags: list[str], 
        current_commit: str, 
        present_versions: set[str]) -> Optional[int]:
    ignored_commits = storage.get_ignored_commits()
    full_commit_list = [commit for commit in full_commit_list if commit not in ignored_commits]
    if len(full_commit_list) == 0:
        return None
    full_percent = get_fraction_completed(full_commit_list, present_versions) * 100
    print(terminal.box_middle(title=f" Full Range Histogram ({full_percent:.1f}%)"))

    bucket_commits = split_list(full_commit_list, cols - 4)
    bucket_fractions = [
        len(set(bucket_commits[j]) & present_versions) / max(1, len(bucket_commits[j]))
        for j in range(len(bucket_commits))
    ]
    bucket_fractions += [0] * max(0, cols - 4 - len(bucket_fractions))

    if Configuration.SHOW_TAGS_ON_HISTOGRAM:
        tags = [tag for tag in tags if tag.find(".") == tag.rfind(".")]
        tag_times = git.get_commit_times(tags)
        tag_times = {
            tag[:tag.find("-")]: tag_time
            for tag, tag_time in tag_times.items()
        }
        commit_times = git.get_commit_times(
            [bucket[0] for bucket in bucket_commits if len(bucket) > 0]
        )
        bucket_times = [
            commit_times[bucket[0]] if len(bucket) > 0 else -1
            for bucket in bucket_commits
        ]
        tag_line = build_tag_line(tag_times, bucket_times)
        print(terminal.box_fit(tag_line))
        current_commit_time = git.get_commit_time(current_commit)
        possible_current_buckets = [
            i for i in range(len(bucket_fractions)) 
            if bucket_times[i] != -1 and bucket_times[i] <= current_commit_time
            and (i == len(bucket_fractions) - 1 or bucket_times[i + 1] > current_commit_time)
        ]
    else:
        possible_current_buckets = [
            i for i, revs in enumerate(bucket_commits) if current_commit in revs
        ]

    print(terminal.box_fit(terminal.histogram_height(bucket_fractions)))
    if Configuration.COLOR_ENABLED:
        print(terminal.box_fit(terminal.histogram_color(bucket_fractions)))
    return possible_current_buckets[0] if len(possible_current_buckets) > 0 else None


def compile(commits: list[str], retry_compress: bool = True, fatal_compress: bool = True) -> bool:
    if len(commits) == 0:
        return True

    _handle_local_changes()

    present_versions = storage.get_present_versions()
    full_commit_list = git.get_commit_list(Configuration.RANGE_START, Configuration.RANGE_END)
    tags = git.get_tags()

    times = {}
    compiled_versions = []
    processable_commits = set(commits) - present_versions
    commit = commits[0]
    while len(error_commits) + len(compiled_versions) < len(commits):
        i = len(error_commits) + len(compiled_versions)
        if i > 0:
            print("Finding a similar commit to compile next...")
        commit = git.get_similar_commit(commit, processable_commits)
        start_time = time.time()
        if Configuration.PRINT_MODE == PrintMode.QUIET:
            print(f"Compiling commit {commit} ({i + 1} / {len(commits)})")
        else:
            print_compile_status(
                tags=tags, 
                full_commit_list=full_commit_list, 
                present_versions=present_versions, 
                processed_count=i, 
                job_commits=commits, 
                times=times, 
                error_commits=error_commits, 
                current_commit=commit
            )

        processable_commits.remove(commit)
        git.check_out(commit)
        did_compile = single()
        if not did_compile:
            if len(compiled_versions) >= MIN_SUCCESSES:
                print(f"Error while compiling commit {commit}.")
                print("Adding to the compile_error_commit file so it's skipped in the future.")
                print("If you fix its build, you should remove it from the file"
                    + " or run with --ignore-old-errors.")
                storage.add_compiler_error_commits([commit])
            print(f"Error while compiling commit {commit}. Skipping.")
            error_commits.add(commit)
            continue
        cache()
        present_versions.add(commit)
        compiled_versions.append(commit)
        if len(compiled_versions) == MIN_SUCCESSES and len(error_commits) > 0:
            print("Enough successful compilations have occurred to show that errors"
                + " are specific to certain commits.")
            print("The following commits will be skipped in the future:")
            for commit in error_commits:
                print("\t" + commit)
            
            storage.add_compiler_error_commits(error_commits)
            print("If you fix their builds, you should remove them from"
                + " compile_error_commit or run with --ignore-old-errors.")
                
        times[commit] = time.time() - start_time

        if signal_handler.SHOULD_EXIT:
            return True

        enough_compiled_for_compress = i % (Configuration.BUNDLE_SIZE * 2) == 0 and i > 0
        if enough_compiled_for_compress and Configuration.COMPRESSION_ENABLED:
            if not compress(compiled_versions, retry_compress):
                if fatal_compress:
                    print("Terminating compilation due to compression failure.")
                    return False
                else:
                    print("WARNING: Compression failed, continuing compilation anyways.")

        if signal_handler.SHOULD_EXIT:
            return True

    return not Configuration.COMPRESSION_ENABLED or compress(compiled_versions, retry_compress)


def cache() -> None:
    version_name = git.resolve_ref("HEAD")
    print(f"Caching version {version_name}")

    version_path = os.path.join(storage.VERSIONS_DIR, version_name)
    if os.path.exists(version_path):
        print("Version to cache already exists, overwriting it.")
        storage.rm(version_path)
    os.makedirs(version_path, exist_ok=True)

    abs_workspace_path = os.path.abspath(Configuration.WORKSPACE_PATH)
    for archive_path in Configuration.ARCHIVE_PATHS:
        full_glob_path = os.path.join(Configuration.WORKSPACE_PATH, archive_path)
        for file_path in glob.glob(full_glob_path, recursive=True):
            abs_file_path = os.path.abspath(file_path)
            if not abs_file_path.startswith(abs_workspace_path):
                print("Error: Attempted to copy a file outside"
                    + f" the workspace directory: {file_path}")
                sys.exit(1)

            relative_path = os.path.relpath(abs_file_path, abs_workspace_path)
            destination_path = os.path.join(version_path, relative_path)
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
            if Configuration.COPY_ON_CACHE:
                shutil.copy2(abs_file_path, destination_path)
            else:
                shutil.move(abs_file_path, destination_path)

    print(f"Version {version_name} has been successfully cached.")


def single() -> bool:
    return terminal.execute_in_subwindow(
        command=["scons"] + shlex.split(Configuration.COMPILER_FLAGS),
        title="scons",
        rows=Configuration.SUBWINDOW_ROWS,
        cwd=Configuration.WORKSPACE_PATH,
    )
