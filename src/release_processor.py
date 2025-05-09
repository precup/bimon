import json
import os

from src import git
from src import terminal
from src.config import Configuration

_RELEASES_FOLDER = os.path.join(Configuration.SECONDARY_WORKSPACE_PATH, "releases")


def add_any_new_release_tags(force: bool = False) -> None:
    if not force and not git.fetch(repository=Configuration.SECONDARY_WORKSPACE_PATH):
        return

    releases = _get_releases()
    if len(releases) > 0:
        tags = {}
        for i in range(len(releases)):
            if '-' not in releases[i][0]:
                continue
            resolved = git.resolve_ref(releases[i][1])
            if resolved == "":
                print(terminal.warn(f"Release {terminal.color_ref(releases[i][0])} points to a"
                    + " commit that no longer exists. Can't add tag."))
            else:
                tags[releases[i][0]] = resolved

        git.add_tags(tags)


def _get_releases() -> list[list[str]]:
    releases = []
    for filename in os.listdir(_RELEASES_FOLDER):
        if filename.endswith(".json"):
            with open(os.path.join(_RELEASES_FOLDER, filename), "r") as file:
                release_info = json.load(file)
                releases.append([release_info["name"], release_info["git_reference"]])

    return releases
