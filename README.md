# BiMon
BiMon is a tool for speeding up bug triage for the Godot engine bugsquad. It has some nice convenience features, like downloading MRPs from issues and automating bisects based on output or error codes, but the main focus is its ability to precompile and cache Godot binaries for use in later bisects.

### How many commits get precompiled?
You can precompile whatever ranges you choose, every commit or 1 out of every N commits. You can also mix and match the two as you like.
I initially did a 1 in 128 pass to cut down the number of compiles a bit and then did a full pass from today (4.5-dev1) back to 4.0-stable. The first pass produced ~120 versions, the second ~20,000 versions. Doing a hybrid where you do 1 in N for a longer time range and every commit for the last minor version or so also works well; I did that on a second machine.

### What's the performance like?
**TL;DR: With the right compiler flags, I'm averaging 37 seconds and 7.8 MB per version on Linux.** A clean build with no flags takes 6-7 minutes for me, for reference. My Windows builds take ~50% longer across the board but are otherwise similar.

I wanted to be able to cover everything back to the last major release, which at the moment is 20,000 versions. If you're more reasonable and only go back to the previous minor version, this won't be as much of a concern.

Compiling and storing 20,000 versions of Godot seems a bit ludicrous at first glance. However, similar commits build very quickly and have a lot of overlap that compresses extremely well.

Multiplying my clean, flagless build (6.5 minutes) and the current official compressed Linux build size (about 58 MB) by 20k gives you 90 days and 1.2 TB, neither of which are acceptable. 

However, reality is much kinder. With the right flags and incremental builds, I'm averaging 37 seconds and 7.8 MB per version. Multiplying those by 20k gives you 9 days and 160 GB, which is a lot more manageable. For the ~2500 commits back to the last minor version, that's just over a day and about 20 GB, pretty reasonable.

1 in N passes have significantly worse performance on both speed and size because the commits are less similar, so it's hard to justify for small N (<= 8) compared to just compiling everything.

### How do I use the precompiled commits?
Once you have your commits prebuilt, BiMon manages your actual bisections for you by wrapping around git. The interactive bisect mode handles extracting and launching cached commits for you and prioritizes those to narrow down the range as much as possible first. This is a very similar process to bisecting with git normally, but with a lot more automation around building and running Godot. There are also options that can be used to automate bisection by marking commits automatically if they print certain output or crash.

BiMon also supports running individual versions if you're just trying to reproduce a bug instead of bisect it. 

> [!TIP]
> There are project management features built in, so you can just run `bimon.py run 1d33a9bc1 106106` and it will download the project zip off the GitHub page for issue #106106 and launch commit `1d33a9bc1` of Godot, compiling if needed.

## Requirements
- Python 3.12+
- A virtualenv with the packages from `requirements.txt`
- A somewhat recent `git` on your `PATH`
- Time. Expect this to take on the order of days to run for large jobs.
- Space. Depending on mode and compiler flags, I'd expect between 5 and 20 MB per version; I've been getting ~8 MB.
> [!WARNING]
> I only have a Linux and Windows machine. This is heavily used on Linux, lightly tested on Windows, and a mystery on Mac. I tried to account for it, but I'm not quite sure how app bundles work.

## Setup
1) Clone the BiMon repository and set up a virtualenv:
```
git clone https://github.com/precup/bimon.git bimon
cd bimon
python -m venv venv
```
2) Install python dependencies
```
# Linux/MacOS
source venv/bin/activate
# Windows
venv\Scripts\activate

pip install -r requirements.txt
```
3) Copy the config file that matches your OS to `config.ini` and edit it as desired
```
# Linux
cp default_linux_config.ini config.ini
# MacOS
cp default_macos_config.ini config.ini
# Windows
copy default_windows_config.ini config.ini

# Take a look at config.ini!
```
4) Run some initial configuration and setup checks
```
python bimon.py init -y
```
5) You're ready to go! You can perform some initial precompiles, and then keep them up to date by running it via a scheduler.
```
python bimon.py update # + whatever args you'd like
```

# Commands
To run BiMon, use `./bimon.py [-q/-v/-l] [--color=yes/no] [--config=FILE] [-i] COMMAND [COMMAND_ARG...]`.

### Main commands:
- `init` - Sets up the workspaces needed and runs some basic checks.
    - `-y`: Don't ask for permission to clone repositories.

- `update` - Fetches, compiles, and caches missing commits.
    - `--range UPDATE_RANGE`: The range(s) to process. Defaults to the values in the config file.
    - `-n N`: Only process 1 in every N commits, roughly evenly spaced
    - `CURSOR_REF`: The ref to start compiling from. The order after that is based on diff sizes and cannot be configured.

- `run` - Runs the requested version of Godot.
    - `--project PROJECT`: The project to use as a working directory when launching Godot
    - `--issue ISSUE`: The issue number or link to reproduce. Looks for associated projects locally and on the issue page.
    - `--ref REF`: The commit to run. May be a PR or any git reference.
    - `--execution-params PARAMS`: The arguments to pass to the Godot executable. Defaults to the value in the config file.
    - `--discard`: Don't store the result of any builds that occur
    - `FLEXIBLE_ARGS...`: Accepts anything `--project`, `--issue`, and `--ref` does and figures out which is which

- `bisect` - Bisects history to find a which commit introduced a regression. Enters an interactive mode detailed below.
    - `--project PROJECT`: The project to use as a working directory when launching Godot
    - `--issue ISSUE`: The issue number or link to reproduce. Looks for associated projects locally and on the issue page.
    - `--range GOOD_REF..BAD_REF`: A starting range to bisect down
    - `--execution-params PARAMS`: The arguments to pass to the Godot executable. See the config.ini comments for details.
    - `--discard`: Don't store the result of any builds that occur
    - `--cached-only`: Only bisect using precompiled versions, stopping when compiles would be required
    - `--ignore-date`: If an issue is provided, its timestamp is used to roughly bound the bisect range. This disables that behavior.
    - `--path-spec`: Limits the search to commits with certain files. See `git bisect`'s path spec for details.
    - `FLEXIBLE_ARGS...`: Accepts anything `--project`, `--issue`, and `--range` does and figures out which is which

- `create` - Creates a new project in the `projects` folder you can use with `run` and `bisect`.
    - `--title TITLE`: The initial title for the project
    - `-3`: Create a 3.x project instead of a 4.x project
    - `NAME`: The name to refer to this project by for other commands

- `export NAME ZIP` - Exports a project from the `projects` folder to a zip for uploading.
    - `--title TITLE`: Overwrite the project title with `TITLE` on the exported project
    - `--as-is`: Include the `.godot` folder, which is excluded by default
    - `NAME`: The name of the project to export. Often an issue number.
    - `ZIP`: The destination zip to export to

- `clean` - Offers a variety of ways to clean up potentially wasted space.
    - `--duplicates`: Delete uncompressed versions that are duplicates of compressed versions
    - `--build-artifacts`: Delete build files with `scons --clean`
    - `--caches`: Delete internal caches, mostly stored git information that may take a long time to regenerate
    - `--temp-files`: Delete temporary files used during processing
    - `--projects`: Delete all projects. Use with caution.
    - `--loose-files`: Delete any unrecognized files in the versions directory. Use with caution.
    - `--dry-run`: Prints information about what would be deleted but does nothing. Use without caution.

- `help [COMMAND]` - Shows detailed help.
    - `COMMAND`: Only show info on commands beginning with `COMMAND`

### Plumbing commands (usually handled by other commands):
- `compile REF_OR_RANGE...` - Compile and store specific commits. Very similar to `update`.
    - `REF_OR_RANGE`: The refs, ranges, or PRs to be compiled. If none are provided, uses the workspace `HEAD`.

- `compress` - Packs uncompressed versions into compressed bundles.
    - `--all`: Force all versions to be compressed even if it creates undersized or poorly optimized bundles

- `extract REF [FOLDER]` - Extracts the build artifacts for the requested version to a location of your choice.
    - `REF`: The version to extract the build artifacts for
    - `FOLDER`: The target output folder for the files to be extracted into. Defaults to `versions/COMMIT_SHA`.

> [!TIP]
> - Any unique prefix of a command is also accepted, as with flags. 
> - Commands that accept a "ref" may be passed any git reference that resolves to a single commit, such as a branch or tag.
> - Commands that accept a range use the format `START_REF..END_REF`. If either is omitted, that side of the range is unbounded. Ranges are inclusive unless bisecting.
> - Commands that accept a project can take a project name, a directory, `project.godot` filepath, `.zip` filepath, or link to a `.zip`. 
> - If no project is provided but an issue is, the issue page will be scanned for projects to use. Project names are either the value passed to `create` or the issue number if downloaded via issue number.
> - `run` can accept PR numbers in place of a commit. The PR will be end up in a branch named something like `pr-104224`.
> - PR and issue numbers don't overlap, so you don't need to clarify which is which.
> - Projects are just folders in the `projects` directory, it's safe to delete them yourself or add your own through other means. The project name is just the folder name.
> - Unstable builds (such as `4.5-dev1`) are automatically added as tags on the main workspace.

### Print flags:
The `-q`/`--quiet`, `-l`/`--live`, and `-v`/`--verbose` flags may be provided before the command; they are mutually exclusive and specify the print mode.
- Live mode shows a live updating display of the tail of long running subprocesses, like scons. Does not work on Windows, falls back to -v.
- Quiet mode hides any output from those subprocesses. 
- Verbose mode prints the output from those subprocesses.

If no print mode is specified, live mode is used for non Windows TTYs and verbose mode is used otherwise. If you have issues with subprocesses, use something other than live mode.

### Compiler errors
If a commit has compile errors but several successful other compiles have occurred, BiMon will assume there's something wrong with the commit itself and add it to the `compile_error_commits` file. These commits will be skipped for most purposes in the future unless specifically requested or the `-i`/`--ignore-old-errors` flag is provided.

If you want commits to be ignored for even more purposes, you can also create a file named `ignored_commits` and add the SHAs to it with one per line.

# Using `bisect`
When you run the `bisect` command, the program will enter an interactive mode. At the prompt you can use the following commands:
- `good/bad/skip/unmark [REF...]` - Marks (or removes marks) from commits and then updates the current commit.
    - `REF`: The commits to mark or unmark. If not provided, uses the current commit.
- `automate` - Starts opening commits automatically with options to help mark commit automatically, as well.
    - `--good GOOD_STR`: If this text is printed during execution, mark the commit as good
    - `--bad BAD_STR`: If this text is printed during execution, mark the commit as bad
    - `--crash good/bad/skip`: What to mark commits that crash during execution
    - `--exit good/bad/skip`: What to mark commits that exit normally
    - `--regex`: If provided, `--good` and `--bad` are treated as regexes
    - `--script SCRIPT_PATH`: Run this script instead of the executable, passing it the executable location and args
- `pause` - Stops automatically opening commits; other automation remains active.
- `exit/quit` - Exits the interactive bisect and prints a final status message.
- `run [REF...]` - Runs the given commits in order.
    - `REF`: The version to run. If not provided, defaults to the current commit.
- `list` - Lists all remaining possible commits.
    - `--short`: Print only shortened commit SHAs and no log information
    - `--best`: Sort by best bisect score instead of commit date
- `status` - Prints summary information about the current bisect.
    - `--short`: Print only the primary status information
- `set-params EXECUTION_PARAMETERS` - Updates the parameters Godot will be run with.
    - `EXECUTION_PARAMETERS`: The parameters to run Godot with
- `help [COMMAND]` - Shows detailed help.
    - `COMMAND`: Only show info on commands beginning with `COMMAND`

Bisection proceeds in two phases, where the range is first narrowed down as much as possible using cached builds, and then that range is bisected by actually compiling and caching the commits.

> [!TIP]
> - `good`, `bad`, `skip`, and `unmark` can be combined on the same line (`g 4.4-dev1 b 4.5-stable`)
> - `automate` with no arguments is still useful since you won't need to use `run` anymore
> - `automate` calls overwrite each other
> - For an example of automation with a script, try `auto -s example_script.py -g "mark good" -b "mark bad"`
> - Multiple files in the path spec can be given with `--path-spec="filename1 filename2"`

## TODO

#### Known Issues
- Live mode does not work on Windows, falls back to VERBOSE.
- Multiple instances running at once is not safe
- Corrupted build artifacts/extractions aren't handled at all
- Killing the extraction threads might litter the version folder, but it hasn't been a problem *yet*
- SIGINT doesn't print if triggered in the middle of another print
- Minor formatting issues on SIGINT while in live mode

#### Maybe Someday
- Non interactive bisect mode
- Make more general so this isn't just a Godot specific tool
- Live mode on Windows
- Sentinel usage cleanup
- git.py and bisect.py cleanup
- Switch path-spec to being input with -- like git
- Lifetime stats :D