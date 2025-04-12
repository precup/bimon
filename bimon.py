#!/usr/bin/env python

import argparse
import configparser
import os
import subprocess
import sys
import time

from enum import Enum
from typing import Optional

import src.signal_handler as signal_handler
import src.storage as storage

from src.config import Configuration

class PrintMode(Enum):
    QUIET = 1
    LIVE = 2
    VERBOSE = 3

_print_mode = PrintMode.LIVE

WORKSPACE = Configuration.WORKSPACE_PATH

############################################################
#                      Initialization                      #
############################################################


def main() -> None:
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    storage.init_storage()
    signal_handler.install()
    if not os.path.exists(WORKSPACE):
        print(f"BiMon requires a Godot workspace at path '{WORKSPACE}'.")
        clone_response = input(f"Clone one there now? (y/n): ").strip().lower()
        if clone_response.startswith('y'):
            os.system(f"git clone https://github.com/godotengine/godot.git {WORKSPACE}")
        else:
            print("BiMon requires a Godot workspace to function. Exiting.")
            sys.exit(1)

    if len(sys.argv) < 2:
        print_short_help()
        sys.exit(1)
    
    process_command_and_arguments()


def process_command_and_arguments() -> None:
    parser = argparse.ArgumentParser(description="BiMon: A tool for speeding up bisecting during bug triage for the Godot engine bugsquad.")
    parser.add_argument("-q", "--quiet", action="store_const", const=PrintMode.QUIET, dest="print_mode", help="Quiet mode hides output from long-running subprocesses.")
    parser.add_argument("-v", "--verbose", action="store_const", const=PrintMode.VERBOSE, dest="print_mode", help="Verbose mode prints output from subprocesses.")
    parser.add_argument("-l", "--live", action="store_const", const=PrintMode.LIVE, dest="print_mode", help="Live mode shows a live updating display of subprocess output.")
    parser.set_defaults(print_mode=PrintMode.LIVE if sys.stdout.isatty() else PrintMode.VERBOSE)

    subparsers = parser.add_subparsers(dest="command", required=True, help="Available commands")

    # Porcelain commands
    update_parser = subparsers.add_parser("update", help="Fetch, compile, and cache missing commits.")
    update_parser.add_argument("-f", "--force", action="store_true", help="Discard uncommitted changes in the workspace directory.")
    update_parser.add_argument("-n", type=int, help="Only compile and cache 1 in every N commits.")
    update_parser.add_argument("cut_rev", nargs="?", help="The revision to start working back from.")
    update_parser.set_defaults(func=lambda args: update_command(args.force, args.n, args.cut_rev, args.print_mode))

    bisect_parser = subparsers.add_parser("bisect", help="Bisect history to find a regression's commit via an interactive mode.")
    bisect_parser.add_argument("-d", "--discard", action="store_true", help="Prevent caching binaries compiled during a bisect.")
    bisect_parser.add_argument("-c", "--cached-only", action="store_true", help="Prevent compiling at all during a bisect.")
    bisect_parser.add_argument("project", nargs="?", help="Path to the project or project.godot file.")
    bisect_parser.set_defaults(func=lambda args: bisect_command(args.discard, args.cache_only, args.project, args.print_mode))

    extract_parser = subparsers.add_parser("extract", help="Extract the binary for a specific revision.")
    extract_parser.add_argument("rev", help="The revision name to extract.")
    extract_parser.add_argument("file_path", nargs="?", help="The file path to extract the binary to.")
    extract_parser.set_defaults(func=lambda args: extract_command(args.rev, args.file_path, args.print_mode))

    purge_parser = subparsers.add_parser("purge", help="Delete uncompressed binaries that are also present in compressed form.")
    purge_parser.set_defaults(func=lambda args: purge_command(args.print_mode))

    help_parser = subparsers.add_parser("help", help="Show help information.")
    help_parser.set_defaults(func=lambda args: help_command())

    # Plumbing commands
    fetch_parser = subparsers.add_parser("fetch", help="Fetch the latest commits and update the processing lists.")
    fetch_parser.set_defaults(func=lambda args: fetch_command(args.print_mode))

    compile_parser = subparsers.add_parser("compile", help="Compile and store a specific revision.")
    compile_parser.add_argument("rev", nargs="?", default="HEAD", help="The revision to compile. Defaults to HEAD.")
    compile_parser.set_defaults(func=lambda args: compile_command(args.rev, args.print_mode))

    compress_parser = subparsers.add_parser("compress", help="Pack completed bundles.")
    compress_parser.add_argument("-n", type=int, help="Allow gaps of size N - 1 while bundling.")
    compress_parser.set_defaults(func=lambda args: compress_command(args.print_mode, args.n))

    args = parser.parse_args()
    args.func(args)


############################################################
#                         Commands                         #
############################################################


def update_command(force: bool, n: Optional[int], cut_rev: Optional[str], print_mode: PrintMode) -> None:
    global _print_mode
    _print_mode = print_mode
    update(force, n if n is not None else 1, cut_rev)


def update(force: bool, n: int, cut_commit: str) -> None:
    fetch(n)
    rev_list = storage.get_rev_list()
    missing_commits = list(get_missing_commits(n)[::-1])
    if cut_commit is not None:
        cut_commit = resolve_ref(cut_commit.strip())
        if cut_commit == "":
            print(f"Invalid cut commit {cut_commit}.")
            sys.exit(1)

        cut = rev_list.index(cut_commit)
        while cut > 0 and rev_list[cut] not in missing_commits:
            cut -= 1
        if cut < 0:
            print("Unknown cut commit.")
            sys.exit(1)

        cut = missing_commits.index(rev_list[cut])
        missing_commits = missing_commits[cut:] + missing_commits[:cut]

    compile(missing_commits, should_compress=True)


def bisect_command(discard: bool, cache_only: bool, project: Optional[str], print_mode: PrintMode) -> None:
    global _print_mode
    _print_mode = print_mode
    # TODO ignores print mode
    bisect(project)


def bisect(project) -> None:
    pass


def extract_command(rev: str, file_path: Optional[str], print_mode: PrintMode) -> None:
    global _print_mode
    _print_mode = print_mode
    # TODO ignores print mode
    storage.extract_commit(rev, file_path if file_path else os.path.join(VERSIONS_DIR, rev))


def purge_command(print_mode: PrintMode) -> None:
    global _print_mode
    _print_mode = print_mode
    purge_count = storage.purge_duplicate_files({resolve_ref(Configuration.START_COMMIT)})
    print(f"Purged {purge_count} files.")


def fetch_command(print_mode: PrintMode) -> None:
    global _print_mode
    _print_mode = print_mode
    fetch()


def fetch(n: int = 1) -> None:
    print("Fetching...")
    os.system(f"git -C {WORKSPACE} fetch origin")
    os.system(f"git -C {WORKSPACE} rev-list --reverse {Configuration.START_COMMIT}..origin/master > {storage.REV_LIST}")

    print(f"{len(get_missing_commits(n))} commits are waiting to be compiled.")


def compile_command(rev: str, print_mode: PrintMode) -> None:
    global _print_mode
    _print_mode = print_mode
    commit = resolve_ref(rev)
    compile([commit], should_compress=True)


def compile(commits: list[str], should_compress: bool = True) -> None:
    start_time = time.time()
    times = []
    for i, commit in enumerate(commits):
        print(f"({i + 1} / {len(commits)}: {int(i / len(commits) * 100)}%) Compiling commit {commit}")
        print("Times:", times)
        os.system(f"(cd {WORKSPACE} && git checkout {commit})")
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


def compress_command(print_mode: PrintMode, n: Optional[int]) -> None:
    global _print_mode
    _print_mode = print_mode
    compress(n if n is not None else 1)


def compress(n: int = 1) -> None:
    bundles = compute_bundles(n)
    for i, bundle in enumerate(bundles):
        bundle_id = bundle[0]
        print(f"Compressing bundle {i + 1} / {len(bundle)}: {int(i / len(bundle) * 100)}%")
        bundled = storage.compress_bundle(bundle_id, bundle)
        if not bundled:
            print(f"Error while compressing bundle {bundle_id}.")
            sys.exit(1)
        if signal_handler.SHOULD_EXIT:
            break


def help_command() -> None:
    # TODO
    print("""Usage: bimon.py [-q/--quiet] [-v/--verbose] [-l/--live] COMMAND [COMMAND_ARG...]""")
    
def print_short_help() -> None:
    # TODO
    print("""Usage: bimon.py [-q/--quiet] [-v/--verbose] [-l/--live] COMMAND [COMMAND_ARG...]""")
    


###########################################################
#                        Utilities                        #
###########################################################


def compute_bundles(n: int) -> list[list[str]]:
    rev_list = storage.get_rev_list()
    compress_map = storage.read_compress_map()
    unbundled_versions = [rev for rev in rev_list if rev not in compress_map]
    ready_to_bundle = storage.get_unbundled_files()
    bundle_start_i = 0
    bundles = []
    while bundle_start_i < len(unbundled_versions):
        if unbundled_versions[bundle_start_i] not in ready_to_bundle:
            bundle_start_i += 1
            continue
        bundle = [ready_to_bundle[bundle_start_i]]
        not_ready_seen = 0
        bundle_start_i += 1
        bad_bundle = False
        while bundle_start_i < len(unbundled_versions):
            if unbundled_versions[bundle_start_i] in ready_to_bundle:
                not_ready_seen = 0
                bundle.append(unbundled_versions[bundle_start_i])
                if len(bundle) >= COMPRESS_PACK_SIZE:
                    break
            else:
                not_ready_seen += 1
                if not_ready_seen >= n:
                    bad_bundle = True
                    break
            bundle_start_i += 1
        
        if not bad_bundle:
            bundles.append(bundle)

    return bundles


def resolve_ref(ref: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", WORKSPACE, "rev-parse", ref]).strip().decode("utf-8")
    except subprocess.CalledProcessError:
        return ""


def query_rev_list(start_ref: str, end_ref: str) -> list[str]:
    try:
        return [k.strip() for k in subprocess.check_output(["git", "-C", WORKSPACE, "rev-list", "--reverse", f"{start_ref}..{end_ref}"]).strip().decode("utf-8").split() if k.strip() != ""]
    except subprocess.CalledProcessError:
        return []


def get_missing_commits(n: int) -> list[str]:
    rev_list = storage.get_rev_list()
    present_commits = set(storage.get_present_commits())
    missing_commits = []
    sequential_missing = 0
    for i in range(len(rev_list)):
        if rev_list[i] not in present_commits:
            sequential_missing += 1
        if sequential_missing >= n:
            missing_commits.append(rev_list[i])
            sequential_missing = 0
    return missing_commits


def cache(current_commit: str = None) -> None:
    if current_commit is None:
        current_commit = os.popen(f"git -C {WORKSPACE} rev-parse HEAD").read().strip()
    print(f"Caching commit {current_commit}")
    compiled_path = os.path.join(WORKSPACE, "bin", Configuration.BINARY_NAME)
    storage_path = os.path.join("versions", current_commit)
    os.system(f"mv {compiled_path} {storage_path}")
    os.system(f"chmod +x {storage_path}")


def single() -> int:
    return os.system(f"(cd {WORKSPACE} && scons {Configuration.COMPILER_FLAGS})")


###########################################################
#                                                         #
###########################################################


if __name__ == "__main__":
    main()