import os
import shutil

COMPRESS_MAP = "compress_map"
REV_LIST = "rev_list"
VERSIONS_DIR = "versions"

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


def get_rev_list() -> list[str]:
    if not os.path.exists(REV_LIST):
        return {}
    with open(REV_LIST, "r") as f:
        return [line.strip() for line in f.readlines() if len(line.strip()) > 0]


def get_present_commits() -> list[str]:
    return (
        [version for version in os.listdir(VERSIONS_DIR) if '.' not in version]
        + list(read_compress_map().keys())
    )


def extract_commit(commit: str, target: str) -> bool:
    if os.path.exists(target):
        print("Extraction failed, target already exists.")
        return False
    
    tar_output_file = os.path.join(VERSIONS_DIR, commit)
    if os.path.exists(tar_output_file):
        if target != tar_output_file:
            shutil.copyfile(tar_output_file, target)
        return True
    
    bundle_id = read_compress_map().get(commit)
    if not bundle_id:
        print(f"Extraction failed, commit {commit} not found in storage.")
        return False
        
    decompress_result = os.system(f"tar xf {bundle_id}.tar.xz -C {VERSIONS_DIR} {commit}")
    if decompress_result != 0:
        print(f"Extraction failed during decompression with exit code {decompress_result}.")
        return False
    
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


def compress_bundle(bundle_id: str, bundle: list[str]) -> bool:
    bundle_path = os.path.join(VERSIONS_DIR, f"{bundle_id}.tar.xz")
    if os.path.exists(bundle_path):
        print(f"Bundle {bundle_id} already exists. Skipping.")
        return False
    commit_paths = [os.path.join(VERSIONS_DIR, commit) for commit in bundle]
    compression_flags = "xz -9 -T 2 --lzma2=dict=512M,mode=fast,mf=hc4 --memory=16G"
    # TODO PrintMode the output and -v ness
    compress_result = os.system(f"tar cvf {bundle_path} -I '{compression_flags}' " + " ".join(commit_paths))
    if compress_result != 0:
        print(f"Error while compressing bundle {bundle_id}.")
        return False
    for commit, commit_path in zip(bundle, commit_paths):
        add_to_compress_map(commit, bundle_id)
        if os.path.exists(commit_path):
            os.remove(commit_path)


def get_unbundled_files() -> list[str]:
    rev_list = get_rev_list()
    compress_map = read_compress_map()
    return [
        path for path in os.listdir(VERSIONS_DIR) 
        if path in rev_list and path not in compress_map
    ]
