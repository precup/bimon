import os
import re
import shutil
import string
import tarfile
from typing import Optional

from pyzstd import CParameter, DParameter, ZstdFile

from src import git
from src import terminal
from src.config import Configuration, PrintMode
from src.pooled_executor import PooledExecutor

_VERSIONS_DIR = "versions"
_STATE_DIR = "state"
_IGNORE_FILE = "ignored_commits"
_COMPILE_ERROR_FILE = "compile_error_commits"
_BUNDLE_MAP_NAME = "bundle_map"

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
    if not os.path.exists(_VERSIONS_DIR):
        os.mkdir(_VERSIONS_DIR)


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
    state_str = ""
    for version, bundle_id in bundle_map.items():
        state_str += f"{version}\n{bundle_id}\n"
    save_state(_BUNDLE_MAP_NAME, state_str)


def _read_bundle_map() -> dict[str, str]:
    lines = load_state(_BUNDLE_MAP_NAME).splitlines()
    bundle_map = {}
    for i in range(0, len(lines), 2):
        version = lines[i].strip()
        bundle_id = lines[i + 1].strip()
        bundle_map[version] = bundle_id
    return bundle_map


def get_present_versions() -> set[str]:
    return (
        {version for version in os.listdir(_VERSIONS_DIR)
        if git.resolve_ref(version) == version}
        | set(_read_bundle_map().keys())
    )


def get_recursive_file_count(folder: str) -> int:
    if not os.path.exists(folder):
        return 0
    if os.path.isfile(folder):
        return 1
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
    version_path = os.path.join(_VERSIONS_DIR, version)
    if not os.path.exists(version_path):
        bundle_id = _read_bundle_map().get(version)
        if bundle_id is None:
            version = terminal.color_ref(version)
            print(terminal.error(f"Extraction failed, version {version} not found in storage."))
            return False

        if not _extract_with_zstd(os.path.join(_VERSIONS_DIR, bundle_id), version, _VERSIONS_DIR):
            return False
    if target != "" and target != version_path:
        shutil.copytree(version_path, target, dirs_exist_ok=True)
    return True


def find_executable(
        base_folder: str,
        likely_location: Optional[str],
        backup_path_regex: Optional[re.Pattern]) -> Optional[str]:
    if likely_location is not None:
        likely_location = os.path.join(base_folder, likely_location)
        if os.path.exists(likely_location):
            return likely_location

    if backup_path_regex is not None:
        for root, _, files in os.walk(base_folder):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, base_folder)
                re_match = backup_path_regex.match(rel_path)
                if re_match is not None and re_match.end() == len(rel_path):
                    return full_path

    return None


def clean_duplicate_files(
        protected_versions: set[str] = set(),
        dry_run: bool = False,
        keep_count: int = 0) -> int:
    clean_count = 0
    bundle_map = _read_bundle_map()
    versions_to_clean = {
        version for version in os.listdir(_VERSIONS_DIR) if version in bundle_map
    } - protected_versions
    versions_to_clean -= set(get_mru(versions_to_clean, keep_count))

    for version in versions_to_clean:
        version_path = os.path.join(_VERSIONS_DIR, version)
        if dry_run:
            print(f"Would delete {version_path}")
            clean_count += get_recursive_file_count(version_path)
        else:
            if Configuration.PRINT_MODE != PrintMode.QUIET and keep_count == 0:
                print(f"Deleting {version_path}")
            clean_count += rm(version_path)
    return clean_count


def _compress_with_zstd(folders: list[str], output_path: str) -> bool:
    paths = {
        os.path.abspath(folder): os.path.basename(folder)
        for folder in folders
    }
    return _compress_with_zstd_by_name(paths, output_path)


def _compress_with_zstd_by_name(paths: dict[str, str], output_path: str) -> bool:
    if os.path.exists(output_path):
        # Check if the file is already in the bundle map.
        # That should never occur, but it might've once.
        # Defending just in case since it's very bad to allow.
        bundle_map = _read_bundle_map()
        output_filename = os.path.basename(output_path)
        col_filename = terminal.color_ref(output_filename)
        if output_filename in bundle_map.values():
            print(terminal.error(f"Archive {col_filename} has already been added."))
            print(terminal.error("This should never occur, please report. Skipping."))
            return False
        else:
            print(f"Archive {col_filename} already exists, but seems invalid. Overwriting.")
            rm(output_path)
    try:
        with ZstdTarFile(output_path, mode="w") as tar:
            for input_path, arcname in paths.items():
                tar.add(input_path, arcname=arcname)
        return True
    except Exception as e:
        print(terminal.error(f"Compression failed with error: {e}"))
        return False


def _extract_with_zstd(bundle_path: str, file_prefix: str, output_dir: str) -> bool:
    if not os.path.exists(bundle_path):
        print(f"Archive {bundle_path} does not exist.")
        return False
    extracted = []
    def path_filter(member: tarfile.TarInfo, path: str) -> Optional[tarfile.TarInfo]:
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
    bundle_path = os.path.join(_VERSIONS_DIR, bundle_id)
    if os.path.exists(bundle_path):
        valid_bundles = _read_bundle_map().values()
        if bundle_id in valid_bundles:
            print(f"Bundle {bundle_id} already exists. Skipping.")
            return False
        print("Bundle already exists but is invalid, removing and rebuilding.")
        rm(bundle_path)

    version_paths = [os.path.join(_VERSIONS_DIR, version) for version in bundle]
    try:
        if not _compress_with_zstd(version_paths, bundle_path):
            return False
    except Exception as e:
        print(f"Compressing bundle {bundle_id} failed with error: {e}")
        print(bundle_path, version_paths)
        return False

    if not os.path.exists(bundle_path):
        # This is a band aid for a bug that occurred a single time that I can't reproduce.
        # Two bundles were reported successfully created and then one compile occurred.
        # After the compile, the first of the two bundles was not found in storage.
        # I don't know whether it was never created or if it was deleted.
        # I found one issue that might've fixed it but one thing doesn't match properly.
        # This will prevent it from deleting versions if it happens again, at least.
        print(f"Recoverable internal error, please report: Bundle {bundle_id} was not created.")
        print("Version paths:", version_paths)
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
        path for path in os.listdir(_VERSIONS_DIR)
        if path not in bundle_map and git.resolve_ref(path) == path
    ]


def get_ignored_commits() -> set[str]:
    if not os.path.exists(_IGNORE_FILE):
        return set()
    with open(_IGNORE_FILE, "r") as f:
        result: set[str] = set()
        for line in f.readlines():
            if len(line.strip()) > 0:
                result |= set(line.strip().split())
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


def add_compiler_error_commits(commits: set[str]) -> None:
    old_errors = get_compiler_error_commits()
    new_errors = commits - old_errors
    with open(_COMPILE_ERROR_FILE, "a") as f:
        for commit in new_errors:
            f.write(f"{commit}\n")


def resolve_relative_to(path: str, wd: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(wd, path)


def save_state(state_name: str, state: str) -> None:
    state_path = get_state_filename(state_name)
    if not os.path.exists(_STATE_DIR):
        os.mkdir(_STATE_DIR)
    with open(state_path, "w") as f:
        f.write(state)


def load_state(state_name: str) -> str:
    state_path = get_state_filename(state_name)
    if not os.path.exists(state_path):
        return ""
    with open(state_path, "r") as f:
        return f.read()


def delete_state(state_name: str) -> int:
    state_path = get_state_filename(state_name)
    if os.path.exists(state_path):
        os.remove(state_path)
        return 1
    return 0


def get_state_filename(state_name: str) -> str:
    return os.path.join(_STATE_DIR, state_name)


def clean_loose_files(dry_run: bool = False) -> int:
    loose_files = set(os.listdir(_VERSIONS_DIR))
    loose_files -= set(_read_bundle_map().values())
    loose_files = {
        file for file in loose_files
        if len(file) != 40 or not all(c in string.hexdigits for c in file)
    }
    cleaned = 0
    for file in loose_files:
        path = os.path.join(_VERSIONS_DIR, file)
        if dry_run:
            print(f"Would delete {path}")
            cleaned += get_recursive_file_count(path)
        else:
            if Configuration.PRINT_MODE != PrintMode.QUIET:
                print(f"Deleting {path}")
            cleaned += rm(path)
    return cleaned


def get_version_folder(version: str) -> str:
    return os.path.join(_VERSIONS_DIR, version)


# TODO this is really hacky but I'm tired
# avoiding a circular import is annoying
def get_mru(commits: set[str], max_count: int) -> list[str]:
    mru: list[str] = []
    used_order = load_state("execution_cache")
    for commit in used_order.split():
        if len(mru) >= max_count:
            break
        if commit in commits:
            mru.append(commit)

    return mru