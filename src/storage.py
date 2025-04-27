import lzma
import os
import shutil
import stat
import tarfile
import zipfile

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
    if not os.path.exists(NEW_COMPRESS_MAP):
        Path(NEW_COMPRESS_MAP).touch()


def init_decompress_queue() -> None:
    global _use_decompress_queue, _decompress_queue
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


def _extract_commit(commit: str, desired_files: set[str], wd = "") -> bool:
    if wd == "":
        wd = VERSIONS_DIR
    
    bundle_id = read_compress_map().get(commit)
    if not bundle_id:
        print(f"Extraction failed, commit {commit} not found in storage.")
        return False

    return extract_with_zstd(os.path.join(VERSIONS_DIR, bundle_id), commit, wd)


def purge_duplicate_files(protected_commits: set[str]) -> int:
    purge_count = 0
    compress_map = read_compress_map()
    for path in os.listdir(VERSIONS_DIR):
        if path in compress_map and path not in protected_commits:
            os.remove(os.path.join(VERSIONS_DIR, path))
            purge_count += 1
    return purge_count


import tarfile
from pyzstd import CParameter, DParameter, ZstdFile


class ZstdTarFile(tarfile.TarFile):
    BASE_COPTIONS = {
        CParameter.compressionLevel: 1,
        CParameter.nbWorkers: 0,
        CParameter.windowLog: 30,
        CParameter.strategy: 8,
        CParameter.chainLog: 6,
        CParameter.minMatch: 3,
        CParameter.targetLength: 16,
        CParameter.enableLongDistanceMatching: 1,
        CParameter.ldmHashLog: 27,
        CParameter.ldmMinMatch: 4,
        CParameter.ldmHashRateLog: 4,
        CParameter.ldmBucketSizeLog: 1,
    }
    BASE_DOPTIONS = {
        DParameter.windowLogMax: 30,
    }
    def __init__(self, name, mode='r', **kwargs):
        options = self.BASE_DOPTIONS if 'r' in mode else self.BASE_COPTIONS
        self.zstd_file = ZstdFile(name, mode, level_or_option=options)
        try:
            super().__init__(fileobj=self.zstd_file, mode=mode, **kwargs)
        except:
            self.zstd_file.close()
            raise
    def close(self):
        try:
            super().close()
        finally:
            self.zstd_file.close()


def compress_with_zstd(folders: list[str], output_path: str) -> bool:
    if os.path.exists(output_path):
        os.remove(output_path)
    try:
        with ZstdTarFile(output_path, mode="w") as tar:
            for folder in folders:
                folder = os.path.abspath(folder)
                tar.add(folder, arcname=os.path.basename(folder))
        return True
    except Exception as e:
        print(f"Compression failed with error: {e}")
        return False


def compress_with_zstd_by_name(paths: dict[str, str], output_path: str) -> bool:
    if os.path.exists(output_path):
        os.remove(output_path)
    try:
        with ZstdTarFile(output_path, mode="w") as tar:
            for input_path, arcname in paths.items():
                tar.add(input_path, arcname=arcname)
        return True
    except Exception as e:
        print(f"Compression failed with error: {e}")
        return False


def extract_with_zstd(archive_path: str, file_prefix: str, target_dir: str) -> bool:
    global num_possible
    if not os.path.exists(archive_path):
        print(f"Archive {archive_path} does not exist.")
        return False
    num_possible = 0
    def path_filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
        if member.name.startswith(file_prefix):
            global num_possible
            num_possible += 1
            return tarfile.data_filter(member, path)
        return None
    with ZstdTarFile(archive_path, mode="r") as tar:
        tar.extractall(target_dir, filter=path_filter)
    if num_possible > 0:
        return True
    print(f"File {file_prefix} not found in archive.")
    return False


def compress_bundle(bundle_id: str, bundle: list[str]) -> bool:
    bundle_path = os.path.join(VERSIONS_DIR, bundle_id)
    if os.path.exists(bundle_path):
        valid_bundles = read_compress_map().values()
        if bundle_id in valid_bundles:
            print(f"Bundle {bundle_id} already exists. Skipping.")
            return False
        print("Bundle already exists but is invalid, removing and rebuilding.")
        os.remove(bundle_path)
    commit_paths = [os.path.join(VERSIONS_DIR, commit) for commit in bundle]
    try:
        compress_with_zstd(commit_paths, bundle_path)
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
                result.update(set(line.strip().split()))
        return result


def add_compiler_error_commits(commits: list[str]) -> None:
    old_errors = get_compiler_error_commits()
    new_errors = set(commits) - old_errors
    with open(COMPILE_ERROR_FILE, "a") as f:
        for commit in new_errors:
            f.write(f"{commit}\n")