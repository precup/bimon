import os
import re
import shutil
import sys
import zipfile
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

from src import git
from src import storage
from src import terminal
from src.config import Configuration, PrintMode

INVALID_NAME_CHARS = r'<>:"/\|?*'
GITHUB_URL = "https://github.com/godotengine/godot/"
ISSUES_URL = GITHUB_URL + "issues/"
PULLS_URL = GITHUB_URL + "pull/"

_PROJECT_FOLDER = "projects"
_SANDBOX_NAME = "sandbox"
_TEMPORARY_ZIP = os.path.join(_PROJECT_FOLDER, "bimon-temp-download-location.zip")
_TEMPORARY_PROJECT_FILE = os.path.join(_PROJECT_FOLDER, "bimon-temp-project-file-location.godot")

if not os.path.exists(_PROJECT_FOLDER):
    os.mkdir(_PROJECT_FOLDER)


def is_valid_project_name(name: str) -> bool:
    return name != "" and all(c not in name for c in INVALID_NAME_CHARS)


def get_project_path(project_name: str) -> str:
    if project_name == "":
        project_name = _SANDBOX_NAME
    if not is_valid_project_name(project_name):
        return ""
    project_path = os.path.join(_PROJECT_FOLDER, project_name)
    return project_path

def get_issue_number(flexible_arg: str) -> int:
    issue_number, is_issue = get_github_number(flexible_arg)
    if issue_number == -1 or not is_issue:
        return -1
    return issue_number


def get_pull_number(flexible_arg: str) -> int:
    pull_number, is_issue = get_github_number(flexible_arg)
    if pull_number == -1 or is_issue:
        return -1
    return pull_number


def get_github_number(flexible_arg: str) -> tuple[int, bool]:
    flexible_arg = flexible_arg.strip().lower()
    if flexible_arg.endswith(".zip"):
        return -1, False

    for base_url, is_issue in [(ISSUES_URL, True), (PULLS_URL, False)]:
        if base_url in flexible_arg:
            flexible_arg = flexible_arg[flexible_arg.index(base_url) + len(base_url):]
            if flexible_arg == "" or not flexible_arg[0].isdigit():
                print(terminal.error("No number found in argument despite"
                    + " it being a non-zip URL.", "UNRECOVERABLE INTERNAL ERROR"))
                sys.exit(1)
            number = ""
            for char in flexible_arg:
                if char.isdigit():
                    number += char
                else:
                    break
            return int(number), is_issue

    if flexible_arg.startswith("#"):
        flexible_arg = flexible_arg[1:]
    if flexible_arg.isdigit():
        github_number = int(flexible_arg)
        create_time = get_approx_issue_creation_time(github_number)
        if create_time is None:
            return -1, False
        return github_number, create_time != -1
    return -1, False


def get_approx_issue_creation_time(issue: int) -> Optional[int]:
    # Can't easily get the actual time, just a date, so we actually just
    # return the timestamp a couple days after that to avoid any timezone issues
    url = ISSUES_URL + str(issue)
    response = requests.get(url, timeout=60)
    if response.status_code != 200:
        return None
    soup = BeautifulSoup(response.content, "html.parser")
    body_divs = soup.find_all("div")
    for div in body_divs:
        if any("issue-body" in class_name for class_name in div.attrs.get("class", [])):
            date_div = div.find("relative-time")
            if date_div is not None:
                date_text = date_div.text.strip()
                try:
                    issue_day = datetime.strptime(date_text[3:], "%b %d, %Y")
                    return int((issue_day + timedelta(days=2)).timestamp())
                except ValueError:
                    pass
    return -1


def clean(projects: bool = False, temp_files: bool = False, dry_run: bool = False) -> int:
    clean_count = 0

    if projects and os.path.exists(_PROJECT_FOLDER):
        if dry_run or Configuration.PRINT_MODE != PrintMode.QUIET:
            for project in os.listdir(_PROJECT_FOLDER):
                project_path = os.path.join(_PROJECT_FOLDER, project)
                if dry_run:
                    print(f"Would delete {project_path}")
                    clean_count += storage.get_recursive_file_count(project_path)
                else:
                    print(f"Deleting {project_path}")
                    clean_count += storage.rm(project_path)
        else:
            clean_count += storage.rm(_PROJECT_FOLDER)
            os.mkdir(_PROJECT_FOLDER)

    elif temp_files and os.path.exists(_TEMPORARY_ZIP):
        if dry_run:
            print(f"Would delete {_TEMPORARY_ZIP}")
            clean_count += 1
        else:
            if Configuration.PRINT_MODE != PrintMode.QUIET:
                print(f"Deleting {_TEMPORARY_ZIP}")
            clean_count += storage.rm(_TEMPORARY_ZIP)

    return clean_count


def extract_project(
        zip_filename: str, 
        project_name: str, 
        title: Optional[str] = None, 
        commit: Optional[str] = None) -> str:
    if project_name == "":
        print("Extracting to the sandbox folder since no project name or issue was provided.")

    project_folder = get_project_path(project_name)

    if os.path.exists(project_folder):
        if project_name == "":
            print("Sandbox folder already exists. Overwriting.")
        else:
            print(f"Project folder for \"{project_name}\" already exists. Overwriting.")
        shutil.rmtree(project_folder)

    os.mkdir(project_folder)
    with zipfile.ZipFile(zip_filename, "r") as f:
        f.extractall(project_folder)

    project_file = find_project_file(project_folder)
    if project_file is None:
        print(terminal.error(
            "Project extraction failed, project.godot file not found in the extracted folder."))
        return ""
    if title is not None:
        set_project_title(project_file, title, commit=commit)
    elif Configuration.AUTOUPDATE_PROJECT_TITLES:
        set_project_title(project_file, project_name, prepend_existing=True, commit=commit)
    return project_file


def set_project_title(
        project_file: str, 
        title: str, 
        prepend_existing: bool = False,
        commit: Optional[str] = None) -> None:
    if len(title.strip()) == 0 and prepend_existing:
        return

    with open(project_file, "r") as f:
        lines = f.readlines()

    # Find existing line numbers
    title_line_index = -1
    application_line_index = -1
    version_line_index = -1
    for i, line in enumerate(lines):
        if line.startswith("config/name="):
            title_line_index = i
        if line.startswith("config_version="):
            version_line_index = i
        if "[application]" in line:
            application_line_index = i

    # Add an initial version if we need to
    if version_line_index == -1:
        version = 5
        if (commit is not None and 
            git.get_merge_base(git.resolve_ref(commit), "4.0-alpha1") 
            != git.resolve_ref("4.0-alpha1")):
            version = 4
        lines.insert(0, f"config_version={version}\n")
        version_line_index = 0
        if title_line_index != -1:
            title_line_index += 1
        if application_line_index != -1:
            application_line_index += 1

    # Add the config section if we need to
    if application_line_index == -1:
        application_line_index = len(lines)
        lines.append("[application]\n")

    # Update the actual title line
    if title_line_index == -1:
        title_line_index = application_line_index + 1
        lines.insert(title_line_index, f"config/name=\"{title}\"\n")
    else:
        line_parts = lines[title_line_index].split("=", 1)
        line_parts[1] = line_parts[1].strip()
        if line_parts[1].startswith("\"") and line_parts[1].endswith("\""):
            line_parts[1] = line_parts[1][1:-1]

        if line_parts[1].lower() == title.lower() or (
            line_parts[1].startswith(title) and prepend_existing):
            return

        if prepend_existing:
            line_parts[1] = f"{title} - " + line_parts[1]
        else:
            line_parts[1] = title
        line_parts[1] = "\"" + line_parts[1] + "\"\n"
        lines[title_line_index] = "=".join(line_parts)

    with open(project_file, "w") as f:
        f.writelines(lines)


def find_project_file(folder: str, silent: bool = False) -> Optional[str]:
    if folder.endswith("project.godot"):
        return folder if os.path.exists(folder) else None

    project_files = []
    all_files_prefix = None
    for root, _, files in os.walk(folder):
        if len(files) > 0:
            if all_files_prefix is None:
                all_files_prefix = root
            else:
                all_files_prefix = os.path.commonprefix([all_files_prefix, root])
        for file in files:
            if file == "project.godot":
                project_files.append(os.path.join(root, file))
    if silent:
        return project_files[0] if len(project_files) >= 1 else None

    if len(project_files) > 1:
        print("Multiple project.godot files found in the project folder."
            + " Please specify which one to use.")
        if all_files_prefix is None:
            all_files_prefix = folder
        for i, file in enumerate(project_files):
            print(terminal.color_key(f"{i + 1}:"), 
                file[file.index(all_files_prefix) + len(all_files_prefix):])
        choice = _get_menu_choice(
            "Enter the number of the project.godot file to use, or "
            + terminal.color_key("n") + " if none look right ["
            + terminal.color_key("1") + "]: ",
            "Invalid choice. Please enter a valid number or "
            + terminal.color_key("n") + " [" + terminal.color_key("1") + "]: ",
            set([str(i + 1) for i in range(len(project_files))] + ["none"]),
            default="1")

        if choice.startswith("n"):
            return None
        return project_files[int(choice) - 1]

    elif len(project_files) == 1:
        return project_files[0]

    print("No project.godot file found in the project folder.")
    response = input("Would you like to create a new one? ["
        + terminal.color_key("Y") + "/n]: ").strip().lower()
    if response.startswith("n"):
        return None
    else:
        if all_files_prefix is None:
            all_files_prefix = ""
        return create_project_file(os.path.join(folder, all_files_prefix))


def get_zip_links_from_issue(issue: int) -> tuple[list[str], int]:
    url = ISSUES_URL + str(issue)
    response = requests.get(url, timeout=60)
    soup = BeautifulSoup(response.content, "html.parser")
    zip_links = list()

    for div in soup.find_all("div", class_=re.compile(".*issue-body.*")):
        for zip_link in div.find_all("a", href=lambda x: x and x.endswith(".zip")):
            if zip_link["href"] not in zip_links:
                zip_links.append(zip_link["href"])
    body_links_len = len(zip_links)

    scan_position = 0
    page_content = response.content.decode("utf-8")
    while scan_position < len(page_content):
        match = re.search(r"https://[^ ]+?\.zip", page_content[scan_position:])
        if match is None:
            break
        zip_link = match.group(0)
        if zip_link not in zip_links:
            zip_links.append(zip_link)
        scan_position += match.start() + len(zip_link)

    return zip_links, body_links_len


def download_project(zip_link: str, project_name: str, title: Optional[str] = None) -> bool:
    print(f"Downloading zip file from {zip_link}")
    storage.rm(_TEMPORARY_ZIP)

    try:
        response = requests.get(zip_link, timeout=60)
        with open(_TEMPORARY_ZIP, "wb") as f:
            f.write(response.content)
        print("Download complete, extracting...")
    except requests.exceptions.RequestException as e:
        print(terminal.error(f"Error downloading zip file: {e}"))
        return False

    return extract_project(_TEMPORARY_ZIP, project_name, title) != ""


def create_project_file(location: str) -> str:
    if not location.endswith("project.godot"):
        project_file = os.path.join(location, "project.godot")
    with open(project_file, "a"):
        os.utime(project_file, None)
    return project_file


def create_project(
        project_name: str = "",
        issue_number: int = -1,
        title: Optional[str] = None,
        force: bool = False,
        commit: Optional[str] = None) -> str:
    if project_name == "":
        if issue_number != -1:
            project_name = str(issue_number)
        else:
            print("No project name or issue number provided. Using the sandbox instead.")
            project_name = _SANDBOX_NAME
    project_folder = get_project_path(project_name)

    if os.path.exists(project_folder):
        project_file = find_project_file(project_folder, True)
        if issue_number < 0 and project_file != "":
            if project_name != _SANDBOX_NAME:
                print("An existing project was found at that location.")
            if not force:
                query = "Would you like to use it?"
                if project_name == _SANDBOX_NAME:
                    query = "Do you want to use the existing sandbox project?"
                response = input(query + " [" + terminal.color_key("Y") + "/n]: ").strip().lower()
                if not response.startswith("n"):
                    project_file = find_project_file(project_folder)
                    return project_file if project_file is not None else ""
            print("Overwriting it with a new one.")
        storage.rm(project_folder)

    os.mkdir(project_folder)
    project_file = create_project_file(project_folder)
    if title is not None:
        set_project_title(project_file, title, commit=commit)
    elif Configuration.AUTOUPDATE_PROJECT_TITLES:
        set_project_title(project_file, project_name, commit=commit)
    return project_file


def _get_temp_project_file(source_file: str, title: str, commit: Optional[str] = None) -> str:
    storage.rm(_TEMPORARY_PROJECT_FILE)
    shutil.copy(source_file, _TEMPORARY_PROJECT_FILE)
    set_project_title(_TEMPORARY_PROJECT_FILE, title, commit=commit)
    return _TEMPORARY_PROJECT_FILE


def export_project(project_name: str, export_path: str, title: Optional[str] = None, as_is: bool = False) -> bool:
    if project_name == "":
        project_name = _SANDBOX_NAME

    if not is_valid_project_name(project_name):
        print(terminal.error(f"Invalid project name \"{project_name}\". Cannot export."))
        return False

    project_folder = get_project_path(project_name)
    if not os.path.exists(project_folder):
        print(terminal.error(f"Project folder for \"{project_name}\" does not exist. Cannot export."))
        return False

    if os.path.exists(export_path):
        if os.path.isdir(export_path):
            export_path = os.path.join(export_path, project_name)
    else:
        folder = os.path.dirname(export_path)
        if folder != "" and not os.path.exists(folder):
            print(terminal.error(f"Export folder \"{folder}\" does not exist. Cannot export."))
            return False

    with zipfile.ZipFile(export_path, "w") as f:
        for root, dirs, files in os.walk(project_folder):
            skip = False
            root_processed = root
            while len(root_processed) > 0:
                if os.path.basename(root_processed) == ".godot":
                    skip = True
                    break
                root_processed = os.path.dirname(root_processed)
            if skip and not as_is:
                continue

            for dir in dirs:
                dir_path = os.path.join(root, dir)
                if len(os.listdir(dir_path)) == 0:
                    f.mkdir(os.path.relpath(dir_path, project_folder))

            for file in files:
                file_path = os.path.join(root, file)
                output_file_path = file_path
                if file == "project.godot" and title is not None:
                    file_path = _get_temp_project_file(file_path, title)
                f.write(file_path, os.path.relpath(output_file_path, project_folder))
    return True


def get_project_name_from_issue_or_file(issue: Optional[int], file_name: str) -> str:
    if issue != -1 and issue is not None:
        return str(issue)

    project_name = os.path.splitext(os.path.basename(file_name))[0]
    if os.path.exists(os.path.join(_PROJECT_FOLDER, project_name)):
        print(terminal.warn(f"Project {terminal.color_key(project_name)} already exists."))
        response = input("Would you like to overwrite it? [y/"
            + terminal.color_key("N") + "]: ").strip().lower()
        if response.startswith("y"):
            shutil.rmtree(os.path.join(_PROJECT_FOLDER, project_name))
        else:
            suffix = 1
            while os.path.exists(os.path.join(_PROJECT_FOLDER, f"{project_name}-{suffix}")):
                suffix += 1
            project_name = f"{project_name}-{suffix}"
            print("Using project name " + terminal.color_key(project_name) + " instead.")
    return project_name


def get_mrp(issue: Optional[int], commit: Optional[str]) -> str:
    if issue == -1 or issue is None:
        return create_project(commit=commit)

    force_create = False
    folder_name = os.path.join(_PROJECT_FOLDER, f"{issue}")
    zip_filename = os.path.join(_PROJECT_FOLDER, f"{issue}.zip")
    if os.path.exists(folder_name) or os.path.exists(zip_filename):
        print(f"Previously used MRP found for issue {issue}.")
        response = input("Would you like to use it? ["
            + terminal.color_key("Y") + "/n]: ").strip().lower()
        if response.startswith("n"):
            force_create = True
        else:
            if os.path.exists(folder_name):
                project_file = find_project_file(folder_name)
                return project_file if project_file is not None else ""
            return zip_filename

    print("Attempting to find projects in the issue.")
    zip_links, body_links_len = get_zip_links_from_issue(issue)

    if len(zip_links) > 0:
        print("Zip file(s) found in issue. Please select an option.")
        for i, link in enumerate(zip_links):
            body_info = ""
            if len(zip_links) > body_links_len:
                source_type = "issue" if i < body_links_len else "comment"
                body_info = f" (in {source_type})"
            print(terminal.color_key(str(i + 1) + body_info + ":"), link)
        print(terminal.color_key("c:") + " Create a new blank project")
        default_option = os.path.basename(zip_links[0])
        if len(default_option) > 25:
            default_option = default_option[:25] + "..."
        choice = _get_menu_choice(
            "Enter an option from above [" + terminal.color_key(default_option) + "]: ",
            "Invalid choice. Please enter a valid option [" + terminal.color_key(default_option) + "]: ",
            set([str(i + 1) for i in range(len(zip_links))] + ["create", "none"]),
            default="1")

        if choice == "none":
            return ""
        if choice != "create":
            zip_link = zip_links[int(choice) - 1]

            if download_project(zip_link, str(issue)):
                project_file = find_project_file(get_project_path(str(issue)))
                if project_file is None:
                    print(terminal.error("Project extraction failed."))
                    return ""
                return project_file
            print(terminal.error("Error downloading project."))
            return ""

    else:
        print(f"No MRP found in issue #{issue}.")

    return create_project(str(issue), force=force_create, commit=commit)


def _get_menu_choice(
        prompt: str,
        error_prompt: str,
        valid_choices: set[str],
        default: Optional[str] = None) -> str:
    choice = input(prompt)
    while True:
        choice = choice.strip().lower()
        if choice == "" and default is not None:
            choice = default
        if choice != "":
            if choice in valid_choices:
                return choice

            matches = [c for c in valid_choices if c.startswith(choice)]
            if len(matches) == 1:
                return matches[0]
            elif len(matches) > 1:
                print(terminal.error("Ambiguous choice. Please enter a more specific choice."))

        choice = input(error_prompt)