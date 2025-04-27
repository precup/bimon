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
NEW_COMPRESS_MAP = "new_compress_map"
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


def write_compress_map(compress_map: dict[str, str], newmap: bool = False) -> None:
    with open(NEW_COMPRESS_MAP if newmap else COMPRESS_MAP, "w") as f:
        for key, value in compress_map.items():
            f.write(f"{key}\n{value}\n")


def read_compress_map(newmap: bool = False) -> dict[str, str]:
    if not os.path.exists(NEW_COMPRESS_MAP if newmap else COMPRESS_MAP):
        return {}
    with open(NEW_COMPRESS_MAP if newmap else COMPRESS_MAP, "r") as f:
        lines = f.readlines()
        compress_map = {}
        for i in range(0, len(lines), 2):
            key = lines[i].strip()
            value = lines[i + 1].strip()
            compress_map[key] = value
    return compress_map


def add_to_compress_map(commit: str, location: str, newmap: bool = False) -> None:
    compress_map = read_compress_map(newmap)
    compress_map[commit] = location
    write_compress_map(compress_map, newmap=newmap)


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

    try:
        with tarfile.open(os.path.join(VERSIONS_DIR, f"{bundle_id}.tar.xz"), mode="r:xz") as tar:
            if len(desired_files) > 1:
                tar.extractall(path=wd, members=[os.path.join(VERSIONS_DIR, commit) for commit in desired_files])
            else:
                tar.extract(member=os.path.join(VERSIONS_DIR, commit), path=wd)
    except tarfile.TarError as e:
        print(f"Extraction failed during decompression with error {e}.")
        return False
    except KeyError:
        try:
            with tarfile.open(os.path.join(VERSIONS_DIR, f"{bundle_id}.tar.xz"), mode="r:xz") as tar:
                if len(desired_files) > 1:
                    tar.extractall(path=wd, members=desired_files)
                else:
                    tar.extract(member=commit, path=wd)
        except tarfile.TarError as e:
            try:
                with tarfile.open(os.path.join(VERSIONS_DIR, f"{bundle_id}.tar.xz"), mode="r:xz") as tar:
                    if len(desired_files) > 1:
                        tar.extractall(path=wd, members=[os.path.join("bin", commit) for commit in desired_files])
                    else:
                        tar.extract(member=os.path.join("bin", commit), path=wd)
            except tarfile.TarError as e:
                print(f"Extraction failed during decompression with error {e}.")
                return False
    
    inner_dir = os.path.join(wd, VERSIONS_DIR)
    if os.path.exists(inner_dir):
        for path in os.listdir(inner_dir):
            if not os.path.exists(os.path.join(wd, path)):
                shutil.move(os.path.join(inner_dir, path), os.path.join(wd, path))
        shutil.rmtree(inner_dir)
    inner_dir = os.path.join(wd, "bin")
    if os.path.exists(inner_dir):
        for path in os.listdir(inner_dir):
            if not os.path.exists(os.path.join(wd, path)):
                shutil.move(os.path.join(inner_dir, path), os.path.join(wd, path))
        shutil.rmtree(inner_dir)

    # TODO shouldn't be needed, old bundles need it though
    # try:
    #     os.chmod(tar_output_file, os.stat(tar_output_file).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # except:
    #     pass

    # if target != tar_output_file:
    #     shutil.move(tar_output_file, target)
    return True


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


def extract_with_zstd(archive_path: str, file_path: str, target_dir: str) -> bool:
    if not os.path.exists(archive_path):
        print(f"Archive {archive_path} does not exist.")
        return False
    with ZstdTarFile(archive_path, mode="r") as tar:
        try:
            tar.extract(file_path, target_dir)
            return True
        except KeyError:
            print(f"File {file_path} not found in archive.")
            return False


def tes(suffix, **kwargs) -> None:
    cs = ['215acd52e82f4c575abb715e25e54558deeef998', 'addab4f00152e74eeb10a1554244b38f03a283be', '0d267e7b1e7ac1ede02c07ea7ffc3fc8b2e8fe90', '7b9c5122faafb6b4a655e31ae27bcffeb5cf254f', '565f1514cf2ad8409f0f2c3922076123f05733ef', 'e8311840e40bcad22c8e01198cdf3697c9bc9037', '133d7a8d6f8c83f51cc1afe9d5cd833d9fe98bcd', 'c7e9dc96a4b86f0d96f201fbf6baf9ec79925eee', 'f7edc729fff1b644851f1c082a2db44fad79c727', 'b711d72e8f8edf6f2619f713901c40128c8678fa', '759fb58636737b1c697e9ed0aeba15240e007d19', '98c204a8f0607450ce64f0faa7e56f6f5ef33ee8', '608e7a27eb686b6302482ac0881d46ca29995f1e', 'd236bd863364fe805353196eadc3c29457eafb27', '9b3e445e471b0816b415bc9163757e1f3768379c', 'b546680e962d68e858906e5216e31a542642b171', 'c4297d8817ff1c728dca08f7e20d826299bac39a', 'aad0bdd7301f22002b7e40e554c192d91a743ca8', '4972a524fcd7383e2701cc9a5e8c4c133ead86b0', '0964badc0513179fa6397a01a70893fdfaa1dc32', 'defcb2bb24fd42e23fefde69d676a62a70736f9f', 'fc370a35eb6f4226ff59f90de6ec130c7e1dc881', '0d07a6330a958ecbb6883e1b15900e19d2ec1035', 'e90fb666a2212085ba32628bf801f5d358feaf78', '3bcc45617b17303915ad3e8352f3b9b8631e6cf2', '717df3ee883d59ee5df036d95109009f3488b64c', '334006b501fa6be13d6ced714f928fd56513840c', '297650a912c5eea6e443b14bba2c9143b7ddc24a', 'c45ca4ae042786b78be6166bd6f957f208adf3bd', '31bb3be5a6ce9e972adfded863563384f8688870']
    for i in range(1, len(cs) + 1):
        filenames = cs[:i]
        files = [os.path.join(storage.VERSIONS_DIR, file) for file in filenames]
        output_path = f'tmp2/bundle_{i}_{suffix}.tar.xz'
        if os.path.exists(output_path):
            os.remove(output_path)
        start_time = time.time()
        storage.compress_with_zstd(files[:i], output_path, **kwargs)
        compress_time = time.time() - start_time
        print(f"Compress time: {compress_time / i:.1f} seconds (total {compress_time:.0f}).")
        print(f"Average size: {os.path.getsize(output_path) / i / 1e6:.1f} MB.")
        start_time = time.time()
        storage.extract_with_zstd(output_path, filenames[0], 'tmp2')
        print(f"Extract time: {time.time() - start_time:.2f} seconds.")


def time_extractions() -> None:
    for file in os.listdir("."):
        if file.startswith("bundle"):
            start_time = time.time()
            target_dir = os.path.join(".", file[:-4 if file.endswith(".zip") else len(file)])
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            os.mkdir(target_dir)
            print(file[6:-4 if file.endswith(".zip") else len(file)] + ":", end=" ")
            extract_from_zip(os.path.join(".", file), target_dir)


def extract_from_zip(bundle_path: str, target: str) -> None:
    start_time = time.time()
    with zipfile.ZipFile(bundle_path, mode="r") as zipf:
        for file in zipf.namelist():
            if file.endswith(".tar.xz"):
                zipf.extract(file, target)
                extracted_file = os.path.join(target, file)
                with tarfile.open(extracted_file, mode="r:xz") as tar:
                    tar.extractall(path=target)
                os.remove(extracted_file)
            else:
                zipf.extract(file, target)
    print(f"Extraction took {time.time() - start_time:.2f} seconds.")


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
                result.update(set(line.strip().split()))
        return result


def add_compiler_error_commits(commits: list[str]) -> None:
    old_errors = get_compiler_error_commits()
    new_errors = set(commits) - old_errors
    with open(COMPILE_ERROR_FILE, "a") as f:
        for commit in new_errors:
            f.write(f"{commit}\n")