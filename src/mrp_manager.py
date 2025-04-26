import os
import re
import shutil
import sys
import zipfile
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from src.config import Configuration, PrintMode

ISSUES_URL = "https://github.com/godotengine/godot/issues/"
MRP_FOLDER = "mrps"
UNZIP_FOLDER = os.path.join("mrps", "unzip")

if not os.path.exists(MRP_FOLDER):
    os.mkdir(MRP_FOLDER)


def get_issue_number(project: str) -> int:
    project = project.strip().lower()
    if ISSUES_URL in project:
        project = project[project.index(ISSUES_URL) + len(ISSUES_URL):]
        if project == "" or not project[0].isdigit():
            print("Unrecoverable internal error: No issue number found in argument despite it being an issue URL.")
            sys.exit(1)
        number = ""
        for char in project:
            if char.isdigit():
                number += char
            else:
                break
        return int(number)
    elif project.isdigit():
        return int(project)
    elif project.startswith("#") and project[1:].isdigit():
        return int(project[1:])
    return -1


def get_approx_issue_creation_time(issue: int) -> int:
    # Can't easily get the actual time, just a date, so we actually just
    # return the timestamp a couple days after that to avoid any timezone issues
    url = ISSUES_URL + str(issue)
    response = requests.get(url, timeout=60)
    soup = BeautifulSoup(response.content, 'html.parser')
    body_divs = soup.find_all('div', class_=re.compile('.*issue-body.*'))
    for div in body_divs:
        date_div = div.find('relative-time')
        if date_div:
            date_text = date_div.text.strip()
            try:
                issue_day = datetime.strptime(date_text[3:], "%b %d, %Y")
                return int((issue_day + timedelta(days=2)).timestamp())
            except ValueError:
                pass
    return -1


def purge_all() -> int:
    purge_count = 0

    if os.path.exists(MRP_FOLDER):
        for filename in os.listdir(MRP_FOLDER):
            if re.match(r'^\d+\.zip$', filename):
                os.remove(os.path.join(MRP_FOLDER, filename))
                if Configuration.PRINT_MODE == PrintMode.VERBOSE:
                    print(f"Deleted {filename}")
                purge_count += 1
            elif re.match(r'^\d+$', filename):
                shutil.rmtree(os.path.join(MRP_FOLDER, filename))
                if Configuration.PRINT_MODE == PrintMode.VERBOSE:
                    print(f"Deleted {filename}")
                purge_count += 1

    if os.path.exists(UNZIP_FOLDER):
        shutil.rmtree(UNZIP_FOLDER)
        if Configuration.PRINT_MODE == PrintMode.VERBOSE:
            print(f"Deleted {UNZIP_FOLDER}")
        purge_count += 1

    return purge_count


def extract_mrp(zip_filename: str, issue_number: int) -> str:
    issue_mrp_folder = os.path.join(MRP_FOLDER, str(issue_number))

    if issue_number < 0:
        print("Extracting to the sandbox folder since no issue was provided.")
        issue_mrp_folder = UNZIP_FOLDER

    if os.path.exists(issue_mrp_folder):
        if issue_number < 0:
            print("Sandbox folder already exists. Overwriting.")
        else:
            print(f"MRP folder for issue {issue_number} already exists. Overwriting.")
        shutil.rmtree(issue_mrp_folder)

    os.mkdir(issue_mrp_folder)
    with zipfile.ZipFile(zip_filename, 'r') as f:
        f.extractall(issue_mrp_folder)

    project_file = find_project_file(issue_mrp_folder)
    if not project_file:
        print("MRP extraction failed, project.godot file not found in the extracted folder.")
        return ""
    update_project_title(project_file, issue_number)
    return project_file


def update_project_title(project_file: str, issue_number: int) -> None:
    if Configuration.ADD_ISSUE_TO_TITLE and issue_number >= 0:
        with open(project_file, 'r') as f:
            lines = f.readlines()
        title_line_index = -1
        application_line_index = -1
        version_line_index = -1
        needs_update = False
        for i, line in enumerate(lines):
            if line.startswith("config/name="):
                title_line_index = i
            if line.startswith("config_version="):
                version_line_index = i
            if "[application]" in line:
                application_line_index = i
        if version_line_index == -1:
            lines.insert(0, "config_version=5\n")
            needs_update = True
            version_line_index = 0
            if title_line_index != -1:
                title_line_index += 1
            if application_line_index != -1:
                application_line_index += 1
        if application_line_index == -1:
            application_line_index = len(lines)
            lines.append("[application]\n")
            needs_update = True
        if title_line_index == -1:
            title_line_index = application_line_index + 1
            lines.insert(title_line_index, f"config/name=Issue #{issue_number} MRP\n")
            needs_update = True
        if str(issue_number) not in lines[title_line_index]:
            line_parts = lines[title_line_index].split("=", 1)
            line_parts[1] = f"[{issue_number}] " + line_parts[1]
            lines[title_line_index] = "=".join(line_parts)
            needs_update = True

        if needs_update:
            with open(project_file, 'w') as f:
                for line in lines:
                    if line.startswith("title="):
                        line = f"title=MRP #{issue_number}\n"
                    f.write(line)


def find_project_file(folder: str, silent: bool = False) -> str:
    if folder.endswith("project.godot"):
        return folder if os.path.exists(folder) else ""
    
    project_files = []
    for root, _, files in os.walk(folder):
        for file in files:
            if file == "project.godot":
                project_files.append(os.path.join(root, file))
    if silent:
        return project_files[0] if len(project_files) >= 1 else ""

    if len(project_files) > 1:
        print("Multiple project.godot files found in the extracted folder. Please specify which one to use.")
        for i, file in enumerate(project_files):
            print(f"{i}: {file[file.index(UNZIP_FOLDER) + len(UNZIP_FOLDER):]}")
        choice = input("Enter the number of the project.godot file to use, or n if none look right: ").strip().lower()
        while True:
            if choice.startswith('n'):
                return ""
            if choice.isdigit() and int(choice) < len(project_files):
                break
            choice = input("Invalid choice. Please enter a valid number or 'n': ").strip().lower()
        return project_files[int(choice)]
    elif len(project_files) == 1:
        return project_files[0]
    return ""


def create_mrp(issue_number: int = -1) -> str:
    folder_name = os.path.join(MRP_FOLDER, f"{issue_number}")
    if issue_number < 0:
        folder_name = UNZIP_FOLDER
    if os.path.exists(folder_name):
        project_file = find_project_file(folder_name, True)
        if issue_number < 0 and project_file != "":
            print("A temporary sandbox project already exists.")
            response = input("Would you like to use it? [Y/n]: ").strip().lower()
            if not response.startswith("n"):
                return find_project_file(folder_name)
            print("Overwriting it with a new one.")
        shutil.rmtree(folder_name)
    os.mkdir(folder_name)

    project_file = os.path.join(folder_name, "project.godot")
    with open(project_file, 'a'):
        os.utime(project_file, None)
    update_project_title(project_file, issue_number)
    return project_file


def get_zip_links_from_issue(issue: int) -> tuple[list[str], int]:
    url = ISSUES_URL + str(issue)
    response = requests.get(url, timeout=60)
    soup = BeautifulSoup(response.content, 'html.parser')
    zip_links = list()

    for div in soup.find_all('div', class_=re.compile('.*issue-body.*')):
        for zip_link in div.find_all('a', href=lambda x: x and x.endswith('.zip')):
            if zip_link['href'] not in zip_links:
                zip_links.append(zip_link['href'])
    body_links_len = len(zip_links)

    for div in soup.find_all('div', class_=re.compile('.*comments-container.*')):
        for zip_link in div.find_all('a', href=lambda x: x and x.endswith('.zip')):
            if zip_link['href'] not in zip_links:
                zip_links.append(zip_link['href'])

    return zip_links, body_links_len


def download_zip(zip_link: str, filename: str) -> bool:
    print(f"Downloading zip file from {zip_link}")

    try:
        response = requests.get(zip_link, timeout=60)
        with open(filename, 'wb') as f:
            f.write(response.content)
    except requests.exceptions.RequestException as e:
        print(f"Error downloading zip file: {e}")
        return False

    print(f"Downloaded zip file to {filename}")
    return True

    
def get_mrp(issue: int) -> str:
    if issue == -1:
        print("Execution parameters request a {PROJECT} but no project or issue was provided.")
        response = input("Would you like to use a temporary sandbox project? [Y/n]: ").strip().lower()
        if response.startswith("n"):
            return ""
        else:
            return create_mrp()

    folder_name = os.path.join(MRP_FOLDER, f"{issue}")
    zip_filename = os.path.join(MRP_FOLDER, f"{issue}.zip")
    if os.path.exists(folder_name) or os.path.exists(zip_filename):
        print(f"Previously used MRP found for issue {issue}.")
        response = input("Would you like to use it? [Y/n]: ").strip().lower()
        if not response.startswith("n"):
            return find_project_file(folder_name) if os.path.exists(folder_name) else zip_filename
            
    print("Attempting to find projects in the issue.")
    zip_links, body_links_len = get_zip_links_from_issue(issue)

    if len(zip_links) > 0:        
        print("Zip file(s) found in issue. Please select an option.")
        for i, link in enumerate(zip_links):
            print(f"{i}" + (f" (in {'issue' if i < body_links_len else 'comment'})" if len(zip_links) > body_links_len else '') + f": {link}")
        print("c: Create a new blank project")
        choice = input("Enter the number of the zip file to download, or c to create a blank project: ").strip().lower()

        while True:
            if choice.startswith('c') or (choice.isdigit() and int(choice) < len(zip_links)):
                break
            choice = input("Invalid choice. Please enter a valid number or 'c': ").strip().lower()

        if not choice.startswith('c'):
            zip_link = zip_links[int(choice)]
            return zip_filename if download_zip(zip_link, zip_filename) else ""
    else:
        print(f"No MRP found in issue #{issue}.")
        response = input("Would you like to create a blank project to use? [Y/n]: ").strip().lower()
        if response.startswith("n"):
            return ""

    return create_mrp(issue)
