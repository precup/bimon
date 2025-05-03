import os
import shutil
import tarfile
from pathlib import Path

from pyzstd import CParameter, DParameter, ZstdFile

from src import git
from src.config import Configuration
from src.pooled_executor import PooledExecutor

VERSIONS_DIR = "versions"
STATE_DIR = "state"

_IGNORE_FILE = "ignored_commits"
_COMPILE_ERROR_FILE = "compile_error_commits"
_BUNDLE_MAP_FILE = os.path.join(STATE_DIR, "bundle_map")

_use_decompress_queue = False
_decompress_queue = None


class ZstdTarFile(tarfile.TarFile):
    # These are carefully tuned and should only be touched
    # if you know what you're doing or are running out of RAM.
    # Dropping windowLog by 1 or 2 if you're low on RAM isn't
    # too bad, but it will result in worse compression ratios.
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

    def __init__(self, name, mode="r", **kwargs):
        options = self.BASE_DOPTIONS if "r" in mode else self.BASE_COPTIONS
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


def init_storage() -> None:
    if not os.path.exists(VERSIONS_DIR):
        os.mkdir(VERSIONS_DIR)


def init_decompress_queue() -> None:
    global _use_decompress_queue, _decompress_queue
    if not _use_decompress_queue and Configuration.BACKGROUND_DECOMPRESSION_LAYERS > 0:
        _decompress_queue = PooledExecutor(
            task_fn=_extract_version, pool_size=Configuration.EXTRACTION_POOL_SIZE
        )
        _use_decompress_queue = True


def set_decompress_queue(versions: list[str]) -> None:
    if not _use_decompress_queue:
        return
    _decompress_queue.enqueue_tasks(
        [(version, (version,)) for version in versions]
    )


def _write_bundle_map(bundle_map: dict[str, str]) -> None:
    with open(_BUNDLE_MAP_FILE, "w") as f:
        for version, bundle_id in bundle_map.items():
            f.write(f"{version}\n{bundle_id}\n")


def _read_bundle_map() -> dict[str, str]:
    if not os.path.exists(_BUNDLE_MAP_FILE):
        return {}
    with open(_BUNDLE_MAP_FILE, "r") as f:
        lines = f.readlines()
        bundle_map = {}
        for i in range(0, len(lines), 2):
            version = lines[i].strip()
            bundle_id = lines[i + 1].strip()
            bundle_map[version] = bundle_id
    return bundle_map


def get_present_versions() -> set[str]:
    return (
        {version for version in os.listdir(VERSIONS_DIR) if git.resolve_ref(version) == version}
        | set(_read_bundle_map().keys())
    )


def get_recursive_file_count(folder: str) -> int:
    if not os.path.exists(folder):
        return 0
    file_count = 0
    for root, _, files in os.walk(folder):
        file_count += len(files)
    return file_count


def rm(path: str) -> int:
    if os.path.exists(path):
        if os.path.isdir(path):
            file_count = get_recursive_file_count(path)
            shutil.rmtree(path)
            return file_count
        else:
            os.remove(path)
            return 1
    return 0


def extract_version(version: str, target: str = "") -> bool:
    if _use_decompress_queue:
        _decompress_queue.wait_for(version)
    return _extract_version(version, target)


def _extract_version(version: str, target: str) -> bool:
    version_path = os.path.join(VERSIONS_DIR, version)
    if not os.path.exists(version_path):
        bundle_id = _read_bundle_map().get(version)
        if bundle_id is None:
            print(f"Extraction failed, version {version} not found in storage.")
            return False

        if not _extract_with_zstd(os.path.join(VERSIONS_DIR, bundle_id), version, VERSIONS_DIR):
            return False
    if target != "" and target != version_path:
        shutil.copytree(version_path, target, dirs_exist_ok=True)
    return True


def purge_duplicate_files(protected_versions: set[str] = set()) -> int:
    purge_count = 0
    bundle_map = _read_bundle_map()
    for version in os.listdir(VERSIONS_DIR):
        if version in bundle_map and version not in protected_versions:
            rm(os.path.join(VERSIONS_DIR, version))
            purge_count += 1
    return purge_count


def _compress_with_zstd(folders: list[str], output_path: str) -> bool:
    paths = {
        os.path.abspath(folder): os.path.basename(folder)
        for folder in folders
    }
    return _compress_with_zstd_by_name(paths, output_path)


def _compress_with_zstd_by_name(paths: dict[str, str], output_path: str) -> bool:
    rm(output_path)
    try:
        with ZstdTarFile(output_path, mode="w") as tar:
            for input_path, arcname in paths.items():
                tar.add(input_path, arcname=arcname)
        return True
    except Exception as e:
        print(f"Compression failed with error: {e}")
        return False


def _extract_with_zstd(bundle_path: str, file_prefix: str, output_dir: str) -> bool:
    if not os.path.exists(bundle_path):
        print(f"Archive {bundle_path} does not exist.")
        return False
    extracted = []
    def path_filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
        if member.name.startswith(file_prefix):
            extracted.append(member.name)
            return tarfile.data_filter(member, path)
        return None
    with ZstdTarFile(bundle_path, mode="r") as tar:
        tar.extractall(output_dir, filter=path_filter)
    if len(extracted) > 0:
        return True
    print(f"File {file_prefix} not found in archive.")
    return False


def compress_bundle(bundle_id: str, bundle: list[str]) -> bool:
    bundle_path = os.path.join(VERSIONS_DIR, bundle_id)
    if os.path.exists(bundle_path):
        valid_bundles = _read_bundle_map().values()
        if bundle_id in valid_bundles:
            print(f"Bundle {bundle_id} already exists. Skipping.")
            return False
        print("Bundle already exists but is invalid, removing and rebuilding.")
        rm(bundle_path)

    version_paths = [os.path.join(VERSIONS_DIR, version) for version in bundle]
    try:
        _compress_with_zstd(version_paths, bundle_path)
    except Exception as e:
        print(f"Compressing bundle {bundle_id} failed with error: {e}")
        print(bundle_path, version_paths)
        return False

    bundle_map = _read_bundle_map()
    for version, version_path in zip(bundle, version_paths):
        bundle_map[version] = bundle_id
    _write_bundle_map(bundle_map)

    for version_path in version_paths:
        rm(version_path)
    return True


def get_unbundled_versions() -> list[str]:
    bundle_map = _read_bundle_map()
    return [
        path for path in os.listdir(VERSIONS_DIR) 
        if path not in bundle_map and git.resolve_ref(path) == path
    ]


def get_ignored_commits() -> set[str]:
    if not os.path.exists(_IGNORE_FILE):
        return set()
    with open(_IGNORE_FILE, "r") as f:
        result = set()
        for line in f.readlines():
            if len(line.strip()) > 0:
                result += set(line.strip().split())
        return result


def get_compiler_error_commits() -> set[str]:
    if not os.path.exists(_COMPILE_ERROR_FILE):
        return set()
    with open(_COMPILE_ERROR_FILE, "r") as f:
        result = set()
        for line in f.readlines():
            if len(line.strip()) > 0:
                result.update(set(line.strip().split()))
        return result


def add_compiler_error_commits(commits: list[str]) -> None:
    old_errors = get_compiler_error_commits()
    new_errors = set(commits) - old_errors
    with open(_COMPILE_ERROR_FILE, "a") as f:
        for commit in new_errors:
            f.write(f"{commit}\n")


def resolve_relative_to(path: str, wd: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(wd, path)