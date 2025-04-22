# Godot BiMon
BiMon is a tool for speeding up bug triage for the Godot engine bugsquad. It has some nice convenience features, but the really major selling point is ability to precompile and cache godot binaries.

Compiling repeatedly during bisection is inefficient even on a fairly beefy computer. Even after optimizing my build time as much as I could, it still takes about 2 minutes for clean build. Bisecting from 4.0-stable to 4.5-dev1 takes about 14 bisections, which meant that bisecting a single bug involved upwards of 20 minutes of just waiting on compiles. 

For many bugs, actually checking whether a commit is good or bad once the project is built takes a matter of seconds, and even if compilation were 10x faster than on my machine it would still be a relatively large slowdown to my workflow.

The answer is simple: precompile commits when you're not bisecting so you can skip that time when you are.

### How many commits get precompiled?
I recommend running the initial compilation stage in two passes. BiMon supports having only a subset of commits precompiled, and during bisection it will narrow down the range as much as possible using precompiled versions first. Then, it will begin to compile the commits and add them to the cache so they can be used as future search points.

This means you could precompile just 1 in N commits and only need to do an average of log2(N) compilations for a bisect. Then, as a second pass, you precompile *every* version walking back from HEAD. Recommended values of N are 128 or 64.

That said, if you're limited on some resource, running only 1 in N mode works fine.

### What's the performance like?
From 4.0-stable to 4.5-dev1, there are about 20k commits, so I'll be using that number in all my examples.

At first, it seems absurd to compile and store 20k versions of Godot. However, there are a couple things working in our favor:
- Incremental builds can save you a lot of time. 
- Godot binaries have large amounts of overlap and compress well when stored with each other.

When I first started looking into this, my clean builds were taking 10 minutes. The current official Linux build is compressed to about 58 MB by itself. Multiplying those by 20k gives you 140 days and 1.2 TB, which spooked me. 

After optimizing my build, my clean build takes 2 minutes, my incremental builds average 45 seconds, and the binaries compress to an average of 13 MB each. Multiplying those by 20k gives you 10 days and 260 GB, large but not unmanageable numbers.

My initial 1 in 128 pass from 4.0-stable to 4.5-dev1 took about 6 hours and dropped the average number of compiles from 14 to 7. I chose 128 for N since it was around sqrt(20k).

### Bisecting with BiMon
Once you have your commits prebuilt, BiMon manages your actual bisections for you by wrapping around git. While you're testing a revision, BiMon determines which versions could be needed next and begins to extract them from the archives to hide the decompression time. Once you've told BiMon whether the tested version was good or bad, it launches the project you're testing in the next version automatically. Since switching back to the terminal after each test is a small inefficiency, global hotkeys are also supported if you're willing to run the script as root. Hitting Ctrl+B or Ctrl+G for bad and good while bisecting will automatically close the launched editor, mark the commit, launch the next appropriate version, and begin decompressing the versions after that. Without root these shortcuts still work, but only when the terminal is in focus.

### Integrating with `git bisect run` workflows
If you've got a fancy `git bisect run` based workflow, you can have it retrieve the binary from BiMon by running `bimon.py extract COMMIT DESTINATION_FILEPATH` in your script. It will exit 2 if it doesn't have that commit on hand.

### Requirements
- Time. Compiling all the commits you want up front will take a long time. If you want to compile 20k commits, take your clean build time in minutes and multiply it by 2. It'll take roughly that many *days* to finish. 
- Space. It'll take about 13 GB per 1k commits in the range you want to cover. 4.0 to 4.4 is about 260 GB.
- This was made for Linux. If you want it on other platforms, PRs are welcome.
- A reasonably recent version of git on your PATH

### Setup
1) Check out a new copy of the `godotengine/godot` repo in a location outside the BiMon folder since they have separate git setups. This will be called your workspace folder. Using a dedicated clone of godot instead of reusing the one you do dev work on is strongly recommended.
2) Optimize the builds in your workspace folder. See "Optimizing your builds" below for information on how to do so.
3) In the BiMon folder, copy `default_config.py` to `config.py` and then open `config.py` in your editor of choice. There are a variety of options present that are commented with descriptions and instructions.
4) Run `bimon.py update` to begin the long process of compiling all your versions. This is safe to stop or force quit at any point and running it again later will continue the process, although it may waste a bit of progress. If it's killed while performing certain operations, it may litter the workspace folder with changes and refuse to run to avoid overwriting real changes. In that case, running with `-f` will discard any changes and get you back on your way.
5) Wait a while for your compiles to be done.
6) Add `bimon.py update -f` to your task scheduler of choice so it executes once a day overnight. This is all you need to do to keep the version cache up to date as Godot is developed.

### Optimizing your builds
Out of the box, compiling Godot is a long and slow process. I was getting about 10 minute clean compiles. At that pace, 20k commits would take about 35 days. That's not completely unreasonable, and since it works backwards from HEAD and you can still bisect the most recent regressions before it's done, but you can do better. [This page](https://docs.godotengine.org/en/latest/contributing/development/compiling/compiling_for_linuxbsd.html) has instructions you should follow. The clang, mold, and system libraries were all helpful. The system libraries also reduce the storage required.

BiMon runs with `dev_build=no optimize=none scu_build=yes`. `dev_build=no` should be on to reduce sizes since the symbol table isn't needed. `optimize=none` is then needed to speed the builds back up. `scu_build=yes` is just a massive time savings.

8df2dbe2f6e95852c858d6831fa8e8ef04455f4a is an example of a bad builtin_miniupnpc=no commit. Had to skip embree because distro as well.

TODO this section sucks

### Running BiMon
Before executing BiMon you'll need to `source venv/bin/activate`. Then, you can run it with `./bimon.py [-q] [-v] [-l] COMMAND [COMMAND_ARG...]`.

Porcelain commands:
- `update [-f/--force] [-n N] [CUT_REV]` - Fetch, compile, and cache missing commits, working back from `CUT_REV` until it hits config.py's `START_REV`. `CUT_REV` defaults to the latest commit. If `-f` is provided, uncommitted changes in the workspace directory will be discarded instead of preventing the update. If `-n` is provided, only 1 in every `N` commits will be compiled and cached.
- `bisect [-d/--discard] [-c/--cached-only] [PROJECT]` - Bisect history to find a regression's commit. Enters an interactive mode detailed below. `PROJECT` may be the root folder of the project or the path of a `project.godot` file. If no project is provided, the Project Manager will be launched.
- `extract REV [FILE_PATH]` - Extracts the binary for the commit for the provided revision name `REV` to `FILE_PATH`. If `FILE_PATH` is not provided, the commit SHA is used instead. Exits 2 if that commit isn't present in the cache.
- `purge` - Delete any uncompressed binaries that are also present in compressed form. These may have been littered around if BiMon encounters an error or is forced to close while working, or the `extract` command is used.
- `help` - Show this info.

Plumbing commands (usually handled by `update`):
- `fetch` - Fetch the latest commits and update the processing lists.
- `compile [REV]` - Compile and store a specific revision `REV`. `REV` defaults to `HEAD`.
- `compress [-n N] ` - Pack completed bundles. If `-n` is provided, bundles will be packed even if they have gaps of up to size N - 1.

Print Flags:
All commands accept the `-q`/`--quiet`, `-l`/`--live`, and `-v`/`--verbose` flags, which are mutually exclusive and specify the print mode.
- Quiet mode hides any output from long running subprocesses, like scons. 
- Verbose mode prints the output from those subprocesses.
- Live mode shows a live updating display of the tail of those subprocesses. 

If no print mode is specified, live mode is used for TTYs and verbose mode is used otherwise.


### Using `bisect`
When you run the `bisect` command, the program will enter an interactive mode. At the prompt you can type the following commands. Only the first letter is necessary.
- `good`, `bad`, and `skip` mark revisions as good, bad, or skipped. Arguments can be provided to specify a rev, like `g 4.2-stable`. Multiple of these options can be mixed on one line, like `g 4.0-stable 3.6-stable b 4.1-stable g 4.1-dev1`. If no arguments are provided, the current revision is used. The no argument versions are not valid commands until after `start` has been called.
- `start` begins the bisection process, repeatedly opening editors until the regression is found.
- `try REV` switches the current revision to `REV` and launches it for testing.
- `retry` relaunches the editor for the current revision in case you closed it or got it into a bad state.
- `list` prints out a list of all the commits that could still contain the regression.
- `visualize` displays the result of running `git bisect visualize` and accepts the same options.
- `help` displays this list.
- `exit` or `quit` exits bisection and prints a safe commit range to continue bisecting with later.

Bisection proceeds in two phases, where the range is first narrowed down as much as possible using cached builds, and then that range is bisected by actually compiling and caching the commits. When the bisect is started, the `-d`/`--discard` and `-c`/`--cached-only` flags may be passed to alter this behavior. Passing `--discard` prevents caching the binaries compiled in phase two and is useful if you're limited on storage.
Passing `--cached-only` prevents the second phase entirely and prevents ever compiling during a bisect.  

The intended workflow to bisect an issue is to get an MRP, run `bimon.py bisect MRP_PATH`, enter any versions listed in the issue using `good` or `bad`, and then run `start`.

Once the editor opens, you should attempt to reproduce the bug. Then, either hit the MARK_GOOD or MARK_BAD hotkeys or switch back to your terminal and submit `g` or `b`. Repeat with each new editor that opens. 

### TODO

bisect:340 no more commits to test (expand range?)

src/bisect.py:185:            sys.exit(1)
src/bisect.py:222:        sys.exit(1)
src/bisect.py:250:                sys.exit(0)
src/bisect.py:253:            sys.exit(0)
src/bisect.py:536:            sys.exit(0)
src/mrp_manager.py:32:            sys.exit(1)
src/mrp_manager.py:212:            sys.exit(1)
bisect needs to handle its own error codes

48 Functional
  21 Major
  - 3 Hotkeys
  - 2 MacOS support
        Support storing folders instead of binaries
        Support running nested file
  - 4 Optimize decompression times, redo bundles to match
  - 4 Autoextract next possible commits in threads while bisecting
        get_next_commits needs updating
  - 8 zip mode
        conversion command
  27 Minor
      Bugs
      - 1 defend against size 0 ranges
      - 1 audit behavior for extremely small commit ranges (1 commit, etc)
      - 1 fix bugs with start_commit not being included in rev-lists
      - 1 progress bar clips onto next line, blank line before histogram line??
      - 1 ctrl c jumps up by 1 and overwrites the bottom bar of the compiler output
      - 1 signal handler death message is incorrect for instadie modes
      - 1 check that I don't assume range_start and range_end are resolved
      - 1 couldn't find that one MRP in the comments
      - 1 check that is_ancestor being false for self isn't a problem
      Arguments
      - 2 use the commit range arguments everywhere
            add initial range argument to bisect
      - 1 allow for ranges in commit lists
      - 1 boolean arguments shouldn't require the positionals, really awkward
      - 1 startup check for range validity
      Input/Output
      - 1 print link to the issue page on repro
      - 1 add issue number to project title
      - 1 some inputs should allow enter for y probably
      - 1 fallback bar if no unicode and no color
      - 1 color bar shouldn't show if color is disabled/not supported
      - 1 non unicode support
      - 1 256 color histogram support??
      Misc
      - 1 bisect error codes
      - 1 cache before using so you don't have to reunpack on ctrl c
      - 1 Should more things fetch?
      --
      - 1 bisect:320 no more commits to test (expand range?)
      - 1 fast way to upload mrp
      - 1 keep track of a latest commit for repro instead of using the range?

10 Finishing touches
- 1 Add anything else useful to the init command
      validate config
      commit range should be extant
      binary name
- 1 Finalize requirements.txt
- 2 Support command completion
- 2 Better output, colors, text decorations?
- 2 handle non live printmodes
- 1 progress bar feels a bit cluttered

Clean up
- Code clean up pass
- Repo organization pass
- Use types consistently (dict vs Dict?)
- Run a linter
- Use correct ref/rev for git stuff
- Consistent use of is not None vs truthiness

Testing
- General
- Check which arguments are required and which aren't
- Non LLVM build outputs
- Windows
      Needed to manually install pyreadline3, pywinpty
      can't scroll while subcommand is printing
      unicode support is garbage

Documentation
- Write README.md
- Write main help command
    defaults don't show up properly in help
- Update argparser help strings
- Write help subcommand

Someday?
- Terminal window resizing support
- Find commits mentioned in the issue
- Fullscreen update mode?
- SIGINT doesn't print if triggered in the middle of another print
- path spec should allow you to test commits outside that set if that's the only precompiled
- Corrupted binaries aren't handled at all
    Does killing the extraction threads litter the version space?
- Non interactive bisect mode
- Make more general so this isn't just a godot specific tool