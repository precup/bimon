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
    except KeyError:
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


def duse_actual_tar() -> None:
    target_file = "099b9b2e85b0749cf5de546dbdc40975238a7c3d"
    for filepath in os.listdir("tmp"):
        if 'godot' in filepath:
            continue
        if os.path.exists(target_file):
            if os.path.isdir(target_file):
                shutil.rmtree(target_file)
            else:
                os.remove(target_file)
        if filepath.startswith("bundle_") and filepath.endswith(".tar.gz") and "items" in filepath:
            start_time = time.time()
            os.system(f"tar -xf {filepath} {target_file}")
            print(f"Extraction took {time.time() - start_time:.2f} seconds.")


def time_bundles() -> None:
    target_file = "099b9b2e85b0749cf5de546dbdc40975238a7c3d"
    for filepath in os.listdir("."):
        if os.path.isdir(target_file):
            shutil.rmtree(target_file)
        else:
            os.remove(target_file)
        if filepath.startswith("bundle_") and filepath.endswith(".zpaq") and "items" in filepath:
            start_time = time.time()
            os.system(f"zpaq x {tarname} -only {" ".join(k[:i])}")
            print(f"Extraction took {time.time() - start_time:.2f} seconds.")


def test_bundles() -> None:
    for i in range(1, 1 + len(k)):
        for compress_level in range(0, 6):
            tarname = f"bundle_{i}_items_zpaq{compress_level}.zpaq"
            start_time = time.time()
            os.system(f"zpaq a {tarname} {" ".join(k[:i])} -method {compress_level}")
            print(f"Bundle {i} c{compress_level}: {os.path.getsize(tarname) / i / 1000000:.1f}, {time.time() - start_time:.2f} seconds.")


def test_system_lzma() -> None:
    options = "dict=256MiB,mode=fast,mf=hc4"
    start_time = time.time()
    tarname = "bundle_32.system.tar.xz"
    versions = 14
    if os.path.exists(tarname):
        os.remove(tarname)
    command = f"xz --lzma2={options} -z -c bundle_olds.tar > {tarname}"
    os.system(command)
    print(f"System LZMA: {os.path.getsize(tarname) / versions / 1000000:.1f} {time.time() - start_time:.2f} seconds.")


import tarfile
from pyzstd import CParameter, ZstdFile


class ZstdTarFile(tarfile.TarFile):
    BASE_OPTIONS = {
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
    def __init__(self, name, mode='r', **kwargs):
        self.zstd_file = pyzstd.ZstdFile(name, mode, level_or_option=BASE_OPTIONS)
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


def compress_with_zstd(bundle_path: str, output_path: str, **kwarg) -> None:
    compressor = pyzstd.ZstdCompressor(level_or_option=option_dict)
    if os.path.exists(output_path):
        os.remove(output_path)
    with open(bundle_path, "rb") as f_in, open(output_path, "wb") as f_out:
        f_out.write(compressor.compress(f_in.read()))
    print(f"size {os.path.getsize(output_path) / 1e6 :.1f} MB")


def compress_with_zstd2(bundle_path: str, output_path: str, **kwargs) -> None:
    import zstandard as zstd
    compression_options = {
        "strategy": 8,
        "window_log": 30,
        "chain_log": 6,
        "min_match": 3,
        "target_length": 16,
        "enable_ldm": True,
        "ldm_hash_log": 27,
        "ldm_min_match": 4,
        "ldm_hash_rate_log": 4,
        "ldm_bucket_size_log": 1,
    }
    compression_options.update(kwargs)
    compression_level = kwargs.pop("compression_level", 1)
    params = zstd.ZstdCompressionParameters.from_level(compression_level, **compression_options)
    compressor = zstd.ZstdCompressor(compression_params=params)
    if os.path.exists(output_path):
        os.remove(output_path)
    with open(bundle_path, "rb") as f_in, open(output_path, "wb") as f_out:
        f_out.write(compressor.compress(f_in.read()))
    print(f"size {os.path.getsize(output_path) / 1e6 :.1f} MB")


def tes(bundle,suffix, **kwargs) -> None:
    start_time = time.time()
    compress_with_zstd(f'tmp2/bundle_{bundle}.tar', f'tmp2/bundle_{bundle}_{suffix}.tar.zst', **kwargs)
    print(f"Time: {time.time() - start_time:.2f} seconds.")


def test_zstd(suffix) -> None:
    for i in ['3', '13', 'olds', '20_same']:
        compress_with_zstd(f'tmp2/bundle_{i}.tar', f'tmp2/bundle_{i}_{suffix}.tar.zst')


def test_lzma(suffix) -> None:
    for i in ['3', '13', 'olds', '20_same']:
        compress_with_lzma(f'tmp2/bundle_{i}.tar.xz', [f'tmp2/bundle_{i}.tar'])


def compress_with_lzma(bundle_path: str, file_paths: list[str], filters: dict = {}) -> None:
    # Configure custom LZMA filters to take advantage of large overlaps
    lzma_filters = [
        {
            "id": lzma.FILTER_LZMA2,
            "dict_size": 512 * 1024 * 1024,  # 512 MB
            "mode": lzma.MODE_FAST,
            "mf": lzma.MF_HC4,
        }
    ]
    for key, value in filters.items():
        lzma_filters[0][key] = value
    with open(bundle_path, "wb") as f:
        with lzma.open(f, mode="w", format=lzma.FORMAT_XZ, filters=lzma_filters) as lzma_file:
            with tarfile.open(fileobj=lzma_file, mode="w") as tar:
                for file_path in file_paths:
                    if os.path.exists(file_path):
                        tar.add(file_path, arcname=os.path.basename(file_path))


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



def compress_with_zip(bundle_path: str, file_paths: list[str]) -> None:
    start_time = time.time()
    with zipfile.ZipFile(bundle_path + ".lzma.zip", mode="w", compression=zipfile.ZIP_LZMA) as zipf:
        for file_path in file_paths:
            if os.path.exists(file_path):
                zipf.write(file_path, arcname=os.path.basename(file_path))
    print(f"LZMA compression took {time.time() - start_time:.2f} seconds.")
    for i in range(1, 10):
        start_time = time.time()
        with zipfile.ZipFile(bundle_path + f".deflated{i}.zip", mode="w", compresslevel=i, compression=zipfile.ZIP_DEFLATED) as zipf:
            for file_path in file_paths:
                if os.path.exists(file_path):
                    zipf.write(file_path, arcname=os.path.basename(file_path))
        print(f"DEFLATED compression (level {i}) took {time.time() - start_time:.2f} seconds.")
    for i in range(1, 10):
        start_time = time.time()
        with zipfile.ZipFile(bundle_path + f".bz{i}.zip", mode="w", compresslevel=i, compression=zipfile.ZIP_BZIP2) as zipf:
            for file_path in file_paths:
                if os.path.exists(file_path):
                    zipf.write(file_path, arcname=os.path.basename(file_path))
        print(f"BZIP2 compression (level {i}) took {time.time() - start_time:.2f} seconds.")


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