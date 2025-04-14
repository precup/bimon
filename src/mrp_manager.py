from bs4 import BeautifulSoup
import os
import re
import requests
import shutil
import sys

ISSUES_URL = "github.com/godotengine/godot/issues/"
MRP_FOLDER = "mrps"
UNZIP_FOLDER = os.path.join("mrps", "unzip")

def get_issue_number(project: str) -> int:
    project = project.strip().lower()
    if ISSUES_URL in project:
        project = project[project.index(ISSUES_URL) + len(ISSUES_URL):]
        number = ""
        for char in project:
            if char.isdigit():
                number += char
            else:
                break
        if number:
            return int(number)
        else:
            print("No issue number found in the project string despite it being a URL.")
            sys.exit(1)
    elif project.isdigit():
        return int(project)
    elif project.startswith("#") and project[1:].isdigit():
        return int(project[1:])
    return -1


def purge_all(verbose: bool) -> int:
    purge_count = 0
    if os.path.exists(MRP_FOLDER):
        for filename in os.listdir(MRP_FOLDER):
            if re.match(r'\d+\.zip', filename):
                os.remove(os.path.join(MRP_FOLDER, filename))
                if verbose:
                    print(f"Deleted {filename}")
                purge_count += 1
    if os.path.exists(UNZIP_FOLDER):
        shutil.rmtree(UNZIP_FOLDER)
        if verbose:
            print(f"Deleted {UNZIP_FOLDER}")
        purge_count += 1
    return purge_count


def extract_mrp(zip_filename: str) -> str:
    if os.path.exists(UNZIP_FOLDER):
        shutil.rmtree(UNZIP_FOLDER)
    os.mkdir(UNZIP_FOLDER)
    os.system(f"unzip {zip_filename} -d {UNZIP_FOLDER}")
    project_file = find_project_file(UNZIP_FOLDER)
    if not project_file:
        print("MRP extraction failed, project.godot file not found in the extracted folder.")
        sys.exit(1)
    return project_file


def find_project_file(folder: str) -> str:
    if folder.endswith("project.godot"):
        return folder if os.path.exists(folder) else ""
    
    project_files = []
    for root, _, files in os.walk(folder):
        for file in files:
            if file == "project.godot":
                project_files.append(os.path.join(root, file))

    if len(project_files) > 1:
        print("Multiple project.godot files found in the extracted folder. Please specify which one to use.")
        for i, file in enumerate(project_files):
            print(f"{i}: {file[file.index(UNZIP_FOLDER) + len(UNZIP_FOLDER):]}")
        choice = input("Enter the number of the project.godot file to use, or n if none look right: ").lower().strip()
        while True:
            if choice.startswith('n'):
                return ""
            if choice.isdigit() and int(choice) < len(project_files):
                break
            choice = input("Invalid choice. Please enter a valid number or 'n':")
        return project_files[int(choice)]
    elif len(project_files) == 1:
        return project_files[0]
    return ""

    
def get_mrp(issue: int) -> str:
    zip_filename = os.path.join(MRP_FOLDER, f"{issue}.zip")
    if os.path.exists(zip_filename):
        return zip_filename

    url = f"https://{ISSUES_URL}{issue}"
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')
    # Find the first link that is a child of a div with a class containing "issue-body"
    body_divs = soup.find_all('div', class_=re.compile('.*issue-body.*'))

    # Find the first zip file link that is a child of a body_div
    zip_links = list()
    for div in body_divs:
        for zip_link in div.find_all('a', href=lambda x: x and x.endswith('.zip')):
            zip_links.append(zip_link['href'])
    zip_links = list(set(zip_links))
    
    if len(zip_links) == 0:
        return ""
    zip_link = zip_links[0]
    if len(zip_links) > 1:
        print("Multiple zip files found in the issue body. Please specify which one to download.")
        for i, link in enumerate(zip_links):
            print(f"{i}: {link}")
        choice = input("Enter the number of the zip file to download, or n if none look right: ").lower().strip()
        while True:
            if choice.startswith('n'):
                return ""
            if choice.isdigit() and int(choice) < len(zip_links):
                break
            choice = input("Invalid choice. Please enter a valid number or 'n':")
        zip_link = zip_links[int(choice)]

    print(f"Downloading zip file from {zip_link}")
    response = requests.get(zip_link)
    if not os.path.exists(MRP_FOLDER):
        os.mkdir(MRP_FOLDER)
    with open(zip_filename, 'wb') as f:
        f.write(response.content)
    print(f"Downloaded zip file to {zip_filename}")
    return zip_filename