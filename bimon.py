#!/usr/bin/env python

from git import Repo
import os
import subprocess
import sys
import time

#############################################
#               Configuration               #
#############################################
# The path to your Godot workspace. Can be absolute or relative to the BiMon directory.
WORKSPACE_PATH = "../godot"
# The earliest commit you care about. This is used to fetch the commit list and to bookend
START_COMMIT =  "4.0-stable" # The earliest commit you care about
ENABLE_DYNAMIC_LIBS = True # Use builtin_*=no compiler flags. Requires the libs to be installed.
ENABLE_SHORTCUTS = False # Enable system wide keyboard shortcuts. Requires running as root.
EXIT_AFTER_THIS_HOTKEY = "ctrl+e"
MARK_GOOD_HOTKEY = "ctrl+g"
MARK_BAD_HOTKEY = "ctrl+b"
REVISION_DENSITY = 1

# The following values have been specifically chosen
# and should not be changed unless you know what you're doing
COMPRESS_PACK_SIZE = 20
COMPILER_FLAGS = "platform=linuxbsd use_llvm=yes linker=mold dev_build=no scu_build=yes optimize=none"
LIBRARY_FLAGS = (
  "builtin_enet=no builtin_libogg=no builtin_libtheora=no builtin_libvorbis=no builtin_libwebp=no" 
  + " builtin_mbedtls=no builtin_miniupnpc=no builtin_pcre2=no builtin_zstd=no builtin_harfbuzz=no" 
  + " builtin_libpng=no builtin_freetype=no builtin_graphite=no builtin_zlib=no"
)
COMPRESS_MAP = "compress.map"

_should_exit = False
_is_good = False
_is_bad = False


def main():
    if not os.path.exists(WORKSPACE_PATH):
        print("The workspace path '{}' does not exist.".format(WORKSPACE_PATH))
        sys.exit(1)
    if not os.path.exists("versions"):
        os.mkdir("versions")
    if ENABLE_SHORTCUTS:
        import keyboard
        keyboard.add_hotkey(EXIT_AFTER_THIS_HOTKEY, mark_exit)

    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    if len(sys.argv) not in [2, 3]:
        print("Usage: bimon.py [function]")
        sys.exit(1)
    function = sys.argv[1]
    if function == "bisect":
        bisect(sys.argv[2])
    elif function == "update":
        update(sys.argv[2] if len(sys.argv) >= 3 else None)
    elif function == "compile":
        compile_entry(sys.argv[2] if len(sys.argv) >= 3 else None)
    elif function == "fetch":
        fetch()
    elif function == "purge":
        purge()
    elif function == "compress":
        compress()
    elif function == "help":
        print("Porcelain commands:")
        print("  update [cut_rev] - Fetch and compile missing commits, working back from [cut_rev]. Defaults to the latest.")
        print("  bisect [project] - Bisect history to find a regression's commit.")
        print("  purge - Delete any uncompressed binaries that are also present in compressed form.")
        print("  help - Show this help message.")
        print()
        print("Plumbing commands:")
        print("  fetch - Fetch the latest commits.")
        print("  compile [rev] - Compile and store a specific rev. Defaults to HEAD.")
        print("  compress - Pack completed bundles.")
    else:
        print("Unknown function {}. See help for options.".format(function))
        sys.exit(1)


def get_diff_size(commit1, commit2):
    diff = os.popen("git -C {} diff {}..{} --stat".format(WORKSPACE_PATH, commit1, commit2)).read()
    lines = sum([line.split(",") for line in diff.split("\n")], [])
    size = 0
    for line in lines:
        if line.strip() == "":
            continue
        if "insertions(+)" in line or "deletions(-)" in line:
            size += int(line.split()[0])
    return size


def compress():
    rev_list = get_rev_list()
    compress_map = read_compress_map()
    to_compress = [rev for rev in rev_list if rev not in compress_map]
    packs = [to_compress[i:i + COMPRESS_PACK_SIZE] for i in range(0, len(to_compress), COMPRESS_PACK_SIZE)]
    packs = [pack for pack in packs if all(os.path.exists("versions/{}".format(commit)) for commit in pack)]

    for i in range(len(packs)):
        pack = packs[i]
        pack_id = pack[0]
        print("Compressing pack {} / {}: {}%".format(i + 1, len(packs), int(i / len(packs) * 100)))
        if os.path.exists("versions/{}.tar.xz".format(pack_id)):
            print("Pack {} already exists. Skipping.".format(pack_id))
            to_compress = to_compress[COMPRESS_PACK_SIZE:]
            continue
        compress_result = os.system("tar cvf versions/{}.tar.xz -I 'xz -9 -T 2 --lzma2=dict=512M,mode=fast,mf=hc4 --memory=16G' {}".format(pack_id, " ".join(["versions/" + commit for commit in pack])))
        if compress_result != 0:
            print("Error while compressing pack {}.".format(pack_id))
            sys.exit(1)
        for commit in pack:
            add_to_compress_map(commit, pack_id)
            if os.path.exists("versions/" + commit):
                os.remove("versions/" + commit)
        to_compress = to_compress[COMPRESS_PACK_SIZE:]
        if _should_exit:
            break


def write_compress_map(compress_map):
    with open(COMPRESS_MAP, "w") as f:
        for key, value in compress_map.items():
            f.write("{}\n{}\n".format(key, value))


def read_compress_map():
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


def add_to_compress_map(commit, location):
    compress_map = read_compress_map()
    compress_map[commit] = location
    write_compress_map(compress_map)


def resolve_ref(ref):
    try:
        return subprocess.check_output(["git", "-C", WORKSPACE_PATH, "rev-parse", ref]).strip().decode("utf-8")
    except subprocess.CalledProcessError:
        return ""


def query_rev_list(start_ref, end_ref):
    try:
        return [k.strip() for k in subprocess.check_output(["git", "-C", WORKSPACE_PATH, "rev-list", "--reverse", "{}..{}".format(start_ref, end_ref)]).strip().decode("utf-8").split() if k.strip() != ""]
    except subprocess.CalledProcessError:
        return []


def get_rev_list():
    with open("rev_list", "r") as f:
        return [line.strip() for line in f.readlines() if len(line.strip()) > 0]


def _get_present_commits():
    return (
        [version for version in os.listdir("versions") if '.' not in version]
        + list(read_compress_map().keys())
    )


def _get_missing_commits():
    rev_list = get_rev_list()
    present_commits = set(_get_present_commits())
    missing_commits = []
    sequential_missing = 0
    for i in range(len(rev_list)):
        if rev_list[i] not in present_commits:
            sequential_missing += 1
        if sequential_missing >= REVISION_DENSITY:
            missing_commits.append(rev_list[i])
            sequential_missing = 0
    return missing_commits


def fetch():
    print("Fetching...")
    repo = Repo.init(WORKSPACE_PATH)
    repo.remotes.origin.fetch()
    os.system("git -C {} rev-list --reverse {}..origin/master > rev_list".format(WORKSPACE_PATH, START_COMMIT))

    print("{} commits are waiting to be compiled.".format(len(_get_missing_commits())))


def update(cut_commit=None):
    fetch()
    rev_list = get_rev_list()
    missing_commits = list(_get_missing_commits()[::-1])
    if cut_commit is not None:
        cut_commit = resolve_ref(cut_commit.strip())
        if cut_commit == "":
            print("Invalid cut commit {}.".format(cut_commit))
            sys.exit(1)
        cut = rev_list.index(cut_commit)
        while cut > 0 and rev_list[cut] not in missing_commits:
            cut -= 1
        if cut >= 0:
            cut = missing_commits.index(rev_list[cut])
    else:
        cut = len(missing_commits)

    if cut < 0:
        print("Unknown cut commit.")
        sys.exit(1)
    missing_commits = missing_commits[cut:] + missing_commits[:cut]

    compile(missing_commits, should_compress=True)


def compile_entry(commit=None):
    commit = resolve_ref(commit if commit else "HEAD")
    compile([commit], should_compress=True)


def compile(commits, should_compress=False):
    start_time = time.time()
    times = []
    for i, commit in enumerate(commits):
        print("({} / {}: {}%) Compiling commit {}".format(i + 1, len(commits), int(i / len(commits) * 100), commit))
        print("Times:", times)
        os.system("(cd {} && git checkout {})".format(WORKSPACE_PATH, commit))
        error_code = single()
        if error_code != 0:
            print("Error while compiling commit {}. Skipping.".format(commit))
            time.sleep(0.1)
            start_time = time.time()
            continue
        cache(commit)
        times.append((commit, time.time() - start_time))

        if i % (COMPRESS_PACK_SIZE * 2) == 0 and i > 0 and not _should_exit and should_compress:
            compress()

        start_time = time.time()
        if _should_exit:
            break

    if not _should_exit and should_compress:
        compress()


def single():
    flags = COMPILER_FLAGS
    if ENABLE_DYNAMIC_LIBS:
        flags += " " + LIBRARY_FLAGS
    return os.system("(cd {} && scons {})".format(WORKSPACE_PATH, flags))


def cache(current_commit=None):
    if current_commit is None:
        current_commit = os.popen("git -C {} rev-parse HEAD".format(WORKSPACE_PATH)).read().strip()
    print("Caching commit {}".format(current_commit))
    compiled_path = os.path.join(WORKSPACE_PATH, "bin", "godot.linuxbsd.editor.x86_64.llvm")
    storage_path = os.path.join("versions", "{}".format(current_commit))
    os.system("mv {} {}".format(compiled_path, storage_path))
    os.system("chmod +x {}".format(storage_path))


def ask_for_commit(query_text):
    bad_commit = input(query_text)
    bad_commit = resolve_ref(bad_commit.strip())
    if bad_commit == "":
        print("Invalid commit.")
        return False
    return True


def purge():
    purge_count = 0
    compress_map = read_compress_map()
    start_commit = resolve_ref(START_COMMIT)
    for path in os.listdir("versions"):
        if path in compress_map and path != start_commit:
            os.remove(os.path.join("versions", path))
            purge_count += 1

    print("Purged {} files.".format(purge_count))



def mark_exit():
    _should_exit = True


def mark_good():
    # TODO close project if open
    _is_good = True


def mark_bad():
    # TODO close project if open
    _is_bad = True


def bisect(project):
    # TODO respect _should_exit
    if ENABLE_SHORTCUTS:
        import keyboard
        keyboard.add_hotkey(MARK_GOOD_HOTKEY, mark_good)
        keyboard.add_hotkey(MARK_BAD_HOTKEY, mark_bad)
    present_commits = _get_present_commits()
    compress_map = read_compress_map()
    rev_list = get_rev_list()
    if len(rev_list) == 0:
        print("No revisions to bisect.")
        sys.exit(1)
    start = rev_list[0]
    end = rev_list[-1]

    while start + 1 < end:
        command = input("Please enter a command ([s]tart, [g]ood, [b]ad, [l]ist): ")
        if command.lower().startswith("s"):
            # TODO find a mid with a commit that is present
            mid = (start + end) // 2
            launch_commit(rev_list[mid], project)
            was_good = False
            if ENABLE_SHORTCUTS:
                print("Waiting for hotkey...")
                while not _is_good and not _is_bad:
                    time.sleep(0.05)
                was_good = _is_good
            while True:
                result = input("How did it go? ([g]ood, [b]ad): ")
                if result.lower().startswith("g"):
                    was_good = True
                    break
                elif result.lower().startswith("b"):
                    was_good = False
                    break
                print("Invalid choice.")
        elif command.lower().startswith("g"):
            good_commit = ask_for_commit("Enter the good commit: ")
            good_index = max([-1] + [i for i, rev in enumerate(rev_list) if rev.startswith(good_commit)])
            if good_index < 0:
                print("Invalid commit.")
                continue
            if good_index >= end:
                print("No possible range exists.")
                continue
            start = max(start, good_index)
        elif command.lower().startswith("b"):
            bad_commit = ask_for_commit("Enter the bad commit: ")
            bad_index = max([-1] + [i for i, rev in enumerate(rev_list) if rev.startswith(bad_commit)])
            if bad_index < 0:
                print("Invalid commit.")
                continue
            if bad_index <= start:
                print("No possible range exists.")
                continue
            end = min(end, bad_index)
        elif command.lower().startswith("l"):
            print("{}..{}".format(rev_list[start], rev_list[end - 1]))
        else:
            print("Invalid command.")
            continue


def launch_commit(commit, project):
    if os.path.exists("godot"):
        os.remove("godot")
        
    with py7zr.SevenZipFile("versions/{}.7z".format(commit), mode='r') as archive:
        archive.extract(targets=["godot"])
    os.system("./godot {}".format(project))


if __name__ == "__main__":
    main()