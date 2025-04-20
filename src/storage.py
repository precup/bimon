import lzma
import os
import shutil
import stat
import tarfile

from pathlib import Path

import src.git as git

from src.pooled_executor import PooledExecutor

IGNORE_FILE = "ignored_commits"
COMPILE_ERROR_FILE = "compile_error_commits"
COMPRESS_MAP = "compress_map"
VERSIONS_DIR = "versions"

_use_decompress_queue = False
_decompress_queue = None


def init_storage() -> None:
    if not os.path.exists(VERSIONS_DIR):
        os.mkdir(VERSIONS_DIR)
    if not os.path.exists(COMPRESS_MAP):
        Path(COMPRESS_MAP).touch()


def init_decompress_queue() -> None:
    if not _use_decompress_queue:
        _decompress_queue = PooledExecutor(
            task_fn=_extract_commit, pool_size=2
        )
        _use_decompress_queue = True


def set_decompress_queue(commits: list[str]) -> None:
    if not _use_decompress_queue:
        return
    _decompress_queue.enqueue_tasks(
        [(commit, (commit,)) for commit in commits]
    )


def write_compress_map(compress_map: dict[str, str]) -> None:
    with open(COMPRESS_MAP, "w") as f:
        for key, value in compress_map.items():
            f.write(f"{key}\n{value}\n")


def read_compress_map() -> dict[str, str]:
    if not os.path.exists(COMPRESS_MAP):
        return {}
    with open(COMPRESS_MAP, "r") as f:
        lines = f.readlines()
        compress_map = {}
        for i in range(0, len(lines), 2):
            key = lines[i].strip()
            value = lines[i + 1].strip()
            compress_map[key] = value
    return compress_map


def add_to_compress_map(commit: str, location: str) -> None:
    compress_map = read_compress_map()
    compress_map[commit] = location
    write_compress_map(compress_map)


def get_present_commits() -> set[str]:
    return set(
        [version for version in os.listdir(VERSIONS_DIR) if '.' not in version]
        + list(read_compress_map().keys())
    )


def extract_commit(commit: str, target: str = "") -> bool:
    if _use_decompress_queue:
        _decompress_queue.enqueue_and_wait([(commit, (commit,))])
    return _extract_commit(commit, target)


def _extract_commit(commit: str, target: str = "") -> bool:
    tar_output_file = os.path.join(VERSIONS_DIR, commit)
    if target == "":
        target = tar_output_file
    if target != tar_output_file and os.path.exists(target):
        print("Extraction failed, target already exists.")
        return False
    
    if os.path.exists(tar_output_file):
        if target != tar_output_file:
            shutil.copyfile(tar_output_file, target)
        return True
    
    bundle_id = read_compress_map().get(commit)
    if not bundle_id:
        print(f"Extraction failed, commit {commit} not found in storage.")
        return False

    try:
        with tarfile.open(os.path.join(VERSIONS_DIR, f"{bundle_id}.tar.xz"), mode="r:xz") as tar:
            tar.extract(member=os.path.join(VERSIONS_DIR, commit), path=VERSIONS_DIR)
    except tarfile.TarError as e:
        print(f"Extraction failed during decompression with error {e}.")
        return False
    except KeyError as e:
        print("Falling back to old decompression method.")
        # TODO remove this once I've cleaned up old bundles
        # TODO test whether this can be made faster
        try:
            with tarfile.open(os.path.join(VERSIONS_DIR, f"{bundle_id}.tar.xz"), mode="r:xz") as tar:
                tar.extract(member=commit, path=VERSIONS_DIR)
        except tarfile.TarError as e:
            print(f"Extraction failed during decompression with error {e}.")
            return False
    
    inner_dir = os.path.join(VERSIONS_DIR, VERSIONS_DIR)
    if os.path.exists(inner_dir):
        for path in os.listdir(inner_dir):
            if not os.path.exists(os.path.join(VERSIONS_DIR, path)):
                shutil.move(os.path.join(inner_dir, path), os.path.join(VERSIONS_DIR, path))
        shutil.rmtree(inner_dir)

    # TODO shouldn't be needed, old bundles need it though
    try:
        os.chmod(tar_output_file, os.stat(tar_output_file).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except:
        pass

    if target != tar_output_file:
        shutil.move(tar_output_file, target)
    return True


def purge_duplicate_files(protected_commits: set[str]) -> int:
    purge_count = 0
    compress_map = read_compress_map()
    for path in os.listdir(VERSIONS_DIR):
        if path in compress_map and path not in protected_commits:
            os.remove(os.path.join(VERSIONS_DIR, path))
            purge_count += 1
    return purge_count


def compress_with_lzma(bundle_path: str, file_paths: list[str]) -> None:
    # Configure custom LZMA filters to take advantage of large overlaps
    lzma_filters = [
        {
            "id": lzma.FILTER_LZMA2,
            "dict_size": 512 * 1024 * 1024,  # 512 MB
            "mode": lzma.MODE_FAST,
            "mf": lzma.MF_HC4,
        }
    ]

    with open(bundle_path, "wb") as f:
        with lzma.open(f, mode="w", format=lzma.FORMAT_XZ, filters=lzma_filters) as lzma_file:
            with tarfile.open(fileobj=lzma_file, mode="w") as tar:
                for file_path in file_paths:
                    if os.path.exists(file_path):
                        tar.add(file_path, arcname=os.path.basename(file_path))


def compress_bundle(bundle_id: str, bundle: list[str]) -> bool:
    bundle_path = os.path.join(VERSIONS_DIR, f"{bundle_id}.tar.xz")
    if os.path.exists(bundle_path):
        valid_bundles = read_compress_map().values()
        if bundle_id in valid_bundles:
            print(f"Bundle {bundle_id} already exists. Skipping.")
            return False
        print("Bundle already exists but is invalid, removing and rebuilding.")
        os.remove(bundle_path)
    commit_paths = [os.path.join(VERSIONS_DIR, commit) for commit in bundle]
    try:
        compress_with_lzma(bundle_path, commit_paths)
    except Exception as e:
        print(f"Compressing bundle {bundle_id} failed with error: {e}")
        print(bundle_path, commit_paths)
        return False

    for commit, commit_path in zip(bundle, commit_paths):
        add_to_compress_map(commit, bundle_id)
        if os.path.exists(commit_path):
            os.remove(commit_path)
    return True


def get_unbundled_files() -> list[str]:
    compress_map = read_compress_map()
    return [
        path for path in os.listdir(VERSIONS_DIR) 
        if path not in compress_map and git.resolve_ref(path) == path
    ]


def get_ignored_commits() -> set[str]:
    if not os.path.exists(IGNORE_FILE):
        return set()
    with open(IGNORE_FILE, "r") as f:
        result = set()
        for line in f.readlines():
            if len(line.strip()) > 0:
                result += set(line.strip().split())
        return result


def get_compiler_error_commits() -> set[str]:
    if not os.path.exists(COMPILE_ERROR_FILE):
        return set()
    with open(COMPILE_ERROR_FILE, "r") as f:
        result = set()
        for line in f.readlines():
            if len(line.strip()) > 0:
                result += set(line.strip().split())
        return result


def add_compiler_error_commits(commits: list[str]) -> None:
    old_errors = get_compiler_error_commits()
    new_errors = set(commits) - old_errors
    with open(COMPILE_ERROR_FILE, "a") as f:
        for commit in new_errors:
            f.write(f"{commit}\n")