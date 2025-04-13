
import os
import subprocess
from typing import Iterable
from .config import Configuration

WORKSPACE = Configuration.WORKSPACE_PATH


def get_git_output(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", "-C", WORKSPACE] + args).strip().decode("utf-8")
    except subprocess.CalledProcessError:
        return ""


def resolve_ref(ref: str) -> str:
    return get_git_output(["rev-parse", ref])


def query_rev_list(start_ref: str, end_ref: str) -> list[str]:
    output = get_git_output(["rev-list", "--reverse", f"{start_ref}..{end_ref}"])
    return [k.strip() for k in output.split() if k.strip() != ""]


def get_bisect_commits(good_commits: set[str], bad_commits: set[str]) -> list[str]:
    output = get_git_output(["rev-list", "--bisect-all"] + [f"^{commit}" for commit in good_commits] + list(bad_commits))
    return [line.strip() for line in output.splitlines() if len(line.strip()) > 0]