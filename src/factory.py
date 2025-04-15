import os
import shutil
import sys
import time

from .config import Configuration
import src.git as git
import src.signal_handler as signal_handler
import src.storage as storage


def compress(n: int = 1, force: bool = False) -> None:
    bundles = compute_bundles(n, force)
    for i, bundle in enumerate(bundles):
        bundle_id = bundle[0]
        print(f"Compressing bundle {i + 1} / {len(bundles)}: {int(i / len(bundles) * 100)}%")
        bundled = storage.compress_bundle(bundle_id, bundle)
        if not bundled:
            print(f"Error while compressing bundle {bundle_id}.")
            sys.exit(1)
        if signal_handler.SHOULD_EXIT:
            break


def _handle_local_changes(force: bool, ask: bool) -> None:
    if git.has_local_changes():
        print("Local changes detected in the godot workspace.")
        if force:
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


def compile_uncached(commit: str, force: bool) -> bool:
    _handle_local_changes(force, True)

    git.check_out(commit)
    error_code = single()
    return error_code == 0


def compile(commits: list[str], should_compress: bool = True, n: int = 1, force: bool = False) -> None:
    _handle_local_changes(force, True)

    start_time = time.time()
    times = []
    for i, commit in enumerate(commits):
        print(f"({i + 1} / {len(commits)}: {int(i / len(commits) * 100)}%) Compiling commit {commit}")
        print("Times:", times)
        git.check_out(commit)
        error_code = single()
        if error_code != 0:
            print(f"Error while compiling commit {commit}. Skipping.")
            time.sleep(0.1)
            start_time = time.time()
            continue
        cache(commit)
        times.append((commit, time.time() - start_time))

        if i % (Configuration.COMPRESS_PACK_SIZE * 2) == 0 and i > 0 and not signal_handler.SHOULD_EXIT and should_compress:
            compress()

        start_time = time.time()
        if signal_handler.SHOULD_EXIT:
            break

    if not signal_handler.SHOULD_EXIT and should_compress:
        compress()


def compute_bundles(n: int, force: bool) -> list[list[str]]:
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
                if not_ready_seen >= n and not force:
                    break
            bundle_start_i += 1
        
        if force or len(bundle) >= Configuration.COMPRESS_PACK_SIZE:
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


def single() -> int:
    return os.system(f"(cd {Configuration.WORKSPACE_PATH} && scons {Configuration.COMPILER_FLAGS})")