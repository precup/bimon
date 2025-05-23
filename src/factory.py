import glob
import os
import shlex
import shutil
import time
from typing import Optional

from src import git
from src import signal_handler
from src import storage
from src import terminal
from src.config import Configuration, PrintMode

_MIN_SUCCESSES = 3

_bundles_packed = 0
_compress_time = 0.0


def compress(
        compiled_versions: list[str],
        retry: bool = False,
        compress_all: bool = False) -> bool:
    global _bundles_packed, _compress_time
    start_time = time.time()
    compiled_versions_set = set(compiled_versions)
    unbundled_versions = set(storage.get_unbundled_versions())
    version_list = [version for version in compiled_versions if version in unbundled_versions]
    version_list += [
        version for version in unbundled_versions if version not in compiled_versions_set
    ]
    if len(version_list) == 0:
        return True

    version_list = git.sort_commits(version_list)

    bundles = []
    for i in range(0, len(version_list), Configuration.BUNDLE_SIZE):
        bundle = version_list[i:i + Configuration.BUNDLE_SIZE]
        if len(bundle) == Configuration.BUNDLE_SIZE or compress_all:
            bundles.append(bundle)
    _compress_time += time.time() - start_time

    for i, bundle in enumerate(bundles):
        start_time = time.time()
        bundle_id = bundle[0] + ".tar.zst"
        print("Compressing bundle", terminal.color_key(f"{i + 1} / {len(bundles)}"))
        bundled = storage.compress_bundle(bundle_id, bundle)
        if not bundled:
            if retry:
                print(terminal.warn(f"Retrying compression of bundle {bundle_id} once."))
                bundled = storage.compress_bundle(bundle_id, bundle)
            if not bundled:
                print(terminal.error("Failed to compress all bundles."))
                return False
        _bundles_packed += 1
        _compress_time += time.time() - start_time
        if signal_handler.soft_killed():
            break
    return True


def _handle_local_changes() -> None:
    if git.has_local_changes():
        git.clear_local_changes()


def compile_uncached(ref: str) -> bool:
    _handle_local_changes()

    git.check_out(ref)
    return _run_scons()


def _split_list(lst: list, x: int) -> list[list]:
    avg_size = len(lst) // x
    remainder = len(lst) % x

    parts = []
    start = 0

    for i in range(x):
        end = start + avg_size + (1 if i < remainder else 0)
        parts.append(lst[start:end])
        start = end

    return parts


def _get_remaining_time_str(job_count: int, average_time: float, processed_count: int) -> str:
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
        remaining_time_str = terminal.color_key(remaining_time_str)
    return remaining_time_str


def _print_compile_status(
        tags: list[str],
        full_commit_list: list[str],
        present_versions: set[str],
        processed_count: int,
        job_commits: int,
        times: dict[str, float],
        error_commits: set[str],
        current_commit: str) -> None:
    if Configuration.PRINT_MODE == PrintMode.QUIET:
        current_commit = git.get_short_name(current_commit)
        count_info = f"({terminal.color_key(str(processed_count + 1))} of {terminal.color_key(str(job_commits))})"
        print(f"Compiling commit {current_commit} {count_info}")
        return
    cols = terminal.get_cols()
    title = terminal.color_ref(" Compiling ") + terminal.color_key(f"#{processed_count + 1}") + " of " + terminal.color_key(f"{job_commits} ")
    print(terminal.box_top(title=title))

    average_time = 0.0
    if len(times) > 0:
        average_time = sum(times.values()) / len(times)
        average_time += _compress_time / max(1, _bundles_packed) / Configuration.BUNDLE_SIZE
    average_time_str = "--:--"
    if average_time > 0:
        seconds = int(round(average_time)) % 60
        minutes = int(round(average_time)) // 60
        average_time_str = f"{minutes:02}:{seconds:02}"
        average_time_str = terminal.color_key(average_time_str)
    remaining_time_str = _get_remaining_time_str(job_commits, average_time, processed_count)
    error_str = terminal.color_good("0")
    if len(error_commits) > 0:
        error_str = terminal.color_bad(f"{len(error_commits)}")
    print(terminal.box_content(f"Average time: {average_time_str},"
        + f" Remaining time: {remaining_time_str}, Errors: {error_str}"))

    print(terminal.box_content(
        terminal.trim_to_line(f"Current commit: {git.get_short_log(current_commit)}", cols - 4)
    ))

    fraction_done = float(processed_count) / max(1, job_commits)
    progress_bar = "Job progress (" + terminal.color_key(f"{int(fraction_done * 100):2d}%") + "): "
    progress_bar_length = cols - 4 - terminal.non_ansi_len(progress_bar)
    progress_bar += terminal.progress_bar(progress_bar_length, fraction_done)
    print(terminal.box_content(""))
    print(terminal.box_content(progress_bar))
    print(terminal.box_content(""))

    current_bucket = _print_histogram(
        cols=cols,
        full_commit_list=full_commit_list,
        tags=tags,
        current_commit=current_commit,
        present_versions=present_versions)

    bottom = terminal.box_bottom()
    if current_bucket is not None:
        bottom = (
            bottom[:current_bucket + 2]
            + terminal.color_key("^")
            + bottom[current_bucket + 3:]
        )
    print(bottom)


def _get_fraction_completed(commit_list: list[str], present_versions: set[str]) -> float:
    if not Configuration.IGNORE_OLD_ERRORS:
        compiler_error_commits = storage.get_compiler_error_commits()
        commit_list = [commit for commit in commit_list if commit not in compiler_error_commits]
    present_commits = [commit for commit in commit_list if commit in present_versions]
    return len(present_commits) / max(1, len(commit_list))


def _build_tag_line(tags: list[str], endpoint: str, bucket_times: list[int]) -> str:
    tags = [tag for tag in tags if tag.find(".") == tag.rfind(".") and "stable" in tag]
    tag_times = git.get_commit_times(tags)
    import time
    start_time = time.time()
    tag_times = {
        tag[:tag.find("-")]: git.get_commit_time(git.get_merge_base(tag, endpoint))
        for tag in tags
        if tag_times[tag] != -1 and tag_times[tag] >= bucket_times[0]
        and tag_times[tag] <= bucket_times[-1]
    }
    tag_buckets = {
        tag: [
            i for i in range(len(bucket_times))
            if bucket_times[i] != -1 and bucket_times[i] <= tag_time
            and (i == len(bucket_times) - 1 or bucket_times[i + 1] > tag_time)
        ]
        for tag, tag_time in tag_times.items()
        if tag_time != -1
    }
    tag_first_buckets = {
        tag: i[0]
        for tag, i in tag_buckets.items()
        if len(i) > 0
    }
    tag_sorted = list(sorted(tag_first_buckets.keys()))
    bucket_tags: list[Optional[str]] = [None] * len(bucket_times)
    for tag in tag_sorted[::-1]:
        bucket_tags[tag_first_buckets[tag]] = tag
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


def _print_histogram(
        cols: int,
        full_commit_list: list[str],
        tags: list[str],
        current_commit: str,
        present_versions: set[str]) -> Optional[int]:
    ignored_commits = storage.get_ignored_commits()
    full_commit_list = [commit for commit in full_commit_list if commit not in ignored_commits]
    if len(full_commit_list) == 0:
        return None
    full_percent = _get_fraction_completed(full_commit_list, present_versions) * 100
    full_percent_str = terminal.color_key(f"{full_percent:.1f}%")
    print(terminal.box_middle(title=f" Full Range Histogram ({full_percent_str})"))

    bucket_commits = _split_list(full_commit_list, cols - 4)
    bucket_fractions = [
        len(set(bucket_commits[j]) & present_versions) / max(1, len(bucket_commits[j]))
        for j in range(len(bucket_commits))
    ]
    bucket_fractions += [0] * max(0, cols - 4 - len(bucket_fractions))

    if Configuration.SHOW_TAGS_ON_HISTOGRAM and len(full_commit_list) > 0:
        commit_times = git.get_commit_times(
            [bucket[0] for bucket in bucket_commits if len(bucket) > 0]
        )
        bucket_times = [
            commit_times[bucket[0]] if len(bucket) > 0 else -1
            for bucket in bucket_commits
        ]

        print(terminal.box_content(_build_tag_line(tags, full_commit_list[-1], bucket_times)))

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

    print(terminal.box_content(terminal.histogram_height(bucket_fractions)))
    if Configuration.COLOR_ENABLED:
        print(terminal.box_content(terminal.histogram_color(bucket_fractions)))
    return possible_current_buckets[0] if len(possible_current_buckets) > 0 else None


def compile(
        commits: list[str],
        retry_compress: bool = True,
        fatal_compress: bool = True,
        direct_compile: list[str] = []) -> bool:
    total_versions = len(commits) + len(direct_compile)
    if total_versions == 0:
        return True
    direct_compile = direct_compile[:]

    _handle_local_changes()

    present_versions = storage.get_present_versions()
    full_commit_list = git.get_commit_list(Configuration.RANGE_START, Configuration.RANGE_END)
    tags = git.get_tags()

    times: dict[str, float] = {}
    compiled_versions: list[str] = []
    processable_commits = set(commits) - present_versions
    error_commits: set[str] = set()
    commit = ""
    while len(error_commits) + len(compiled_versions) < total_versions:
        # Figure out next commit
        i = len(error_commits) + len(compiled_versions)
        if i > len(direct_compile):
            print("Finding a similar commit to compile next...")
            commit = git.get_similar_commit(commit, processable_commits)
        elif i < len(direct_compile):
            commit = direct_compile[i]
        else:
            commit = commits[0]

        start_time = time.time()

        # Prepare to compile
        if total_versions > 1:
            _print_compile_status(
                tags=tags,
                full_commit_list=full_commit_list,
                present_versions=present_versions,
                processed_count=i,
                job_commits=total_versions,
                times=times,
                error_commits=error_commits,
                current_commit=commit)
        if i >= len(direct_compile) and commit not in present_versions:
            processable_commits.remove(commit)
        git.check_out(commit)

        # Compile
        did_compile = _run_scons()
        if not did_compile:
            short_name = git.get_short_name(commit)
            if len(compiled_versions) >= _MIN_SUCCESSES:
                print(terminal.error(f"Error while compiling commit {short_name}."))
                print(terminal.warn("Adding to the compile_error_commit file so it's skipped in the future."))
                print(terminal.warn("If you fix its build, you should remove it from the file"
                    + " or run with --ignore-old-errors."))
                storage.add_compiler_error_commits({commit})
            print(terminal.error(f"Error while compiling commit {short_name}. Skipping."))
            error_commits.add(commit)
            continue

        # Process the compiled commit
        if not cache():
            commit = git.get_short_name(commit)
            print(terminal.error(f"Error while caching commit {commit}."))
            return False
        present_versions.add(commit)
        compiled_versions.append(commit)
        if len(compiled_versions) == _MIN_SUCCESSES and len(error_commits) > 0:
            print("Enough successful compilations have occurred to show that errors"
                + " are specific to certain commits.")
            print("The following commits will be skipped in the future:")
            for commit in error_commits:
                print("\t" + commit)

            storage.add_compiler_error_commits(error_commits)
            print("If you fix their builds, you should remove them from"
                + " compile_error_commit or run with --ignore-old-errors.")

        times[commit] = time.time() - start_time

        if signal_handler.soft_killed():
            return True

        enough_compiled_for_compress = i % (Configuration.BUNDLE_SIZE * 2) == 0 and i > 0
        if enough_compiled_for_compress and Configuration.COMPRESSION_ENABLED:
            if not compress(compiled_versions, retry_compress):
                if fatal_compress:
                    print(terminal.error("Terminating compilation due to compression failure."))
                    return False
                else:
                    print(terminal.warn("Compression failed, continuing compilation anyways."))

        if signal_handler.soft_killed():
            return True

    return not Configuration.COMPRESSION_ENABLED or compress(compiled_versions, retry_compress)


def _get_paths_from_artifact_paths() -> Optional[list[str]]:
    abs_workspace_path = os.path.abspath(Configuration.WORKSPACE_PATH)
    paths = []
    for archive_path in Configuration.ARTIFACT_PATHS:
        if "{EXECUTABLE_PATH}" in archive_path:
            executable_path = storage.find_executable(
                abs_workspace_path, Configuration.EXECUTABLE_PATH, Configuration.EXECUTABLE_REGEX
            )
            if executable_path is None:
                print(terminal.error("Executable path not found after compilation."))
                return None
            archive_path = archive_path.replace("{EXECUTABLE_PATH}", glob.escape(executable_path))
        if glob.escape(archive_path) == archive_path:
            resolved_archive_path = os.path.join(Configuration.WORKSPACE_PATH, archive_path)
            if not os.path.exists(resolved_archive_path):
                archive_path = terminal.color_key(archive_path)
                print(terminal.error(
                    f"while archiving, requested file {archive_path} did not exist."))
                return None
            paths.append(resolved_archive_path)
        else:
            for file_path in glob.glob(
                    archive_path, recursive=True, root_dir=Configuration.WORKSPACE_PATH
                ):
                abs_file_path = os.path.abspath(os.path.join(Configuration.WORKSPACE_PATH, file_path))
                if not abs_file_path.startswith(abs_workspace_path):
                    print(terminal.error("Attempted to copy a file from outside"
                        + f" of the workspace directory: {terminal.color_key(file_path)}"))
                    return None
                paths.append(abs_file_path)
    return paths


def cache() -> bool:
    version_name = git.resolve_ref("HEAD")
    short_name = git.get_short_name(version_name)
    print(f"Caching version {short_name}")

    version_path = storage.get_version_folder(version_name)
    if os.path.exists(version_path):
        print(terminal.warn("Version to cache already exists, overwriting it."))
        storage.rm(version_path)
    os.makedirs(version_path, exist_ok=True)

    abs_workspace_path = os.path.abspath(Configuration.WORKSPACE_PATH)
    transfers = _get_paths_from_artifact_paths()
    if transfers is None:
        return False

    for transfer_path in transfers:
        relative_path = os.path.relpath(transfer_path, abs_workspace_path)
        destination_path = os.path.join(version_path, relative_path)
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        if Configuration.COPY_ON_CACHE:
            shutil.copy2(transfer_path, destination_path)
        else:
            shutil.move(transfer_path, destination_path)

    print(f"Version {short_name} has been successfully cached.")
    return True


def _run_scons(args: Optional[list[str]] = None) -> bool:
    if args is None:
        args = shlex.split(Configuration.COMPILER_FLAGS, posix='nt' != os.name)
    return terminal.execute_in_subwindow(
        command=["scons"] + args,
        title="scons",
        rows=Configuration.SUBWINDOW_ROWS,
        cwd=Configuration.WORKSPACE_PATH)


def clean_build_artifacts() -> None:
    print("Cleaning build artifacts...")
    if _run_scons(["--clean"]):
        print("Build artifacts cleaned.")
    else:
        print(terminal.error("Failed to clean build artifacts."))
        return