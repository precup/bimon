# Godot BiMon
BiMon is a tool for speeding up bisecting during bug triage for the Godot engine.

Using git bisect to find regressions is extremely inefficient even on a decent computer. Before I optimized my build times, a clean build took about 10 minutes on my computer. Even after optimizing, it takes about 3 minutes. During bisection, most commits used will be far from each other and require effectively a clean build. Bisecting from 4.0-stable to 4.5-dev1 takes about 14 bisections, which means that bisecting a single bug involves upwards of 30 minutes of just waiting on compiles. 

Even if we assume a better computer, this time is clearly lopsided. For many bugs, actually checking whether a commit is good or bad once the project is built takes a fraction of a minute, and even if compilation were 10x faster than on my machine that would still be the majority of the total time spent.

The answer is simple: precompile every single commit, so you can just skip that time. BiMon also manages the bisection process to shave off further overhead.

## Why build everything? Do I have to?
My original plan wasn't to build every commit, but to rather build a fraction of the repo, perhaps only 1 out of 8 commits, so that typically only ~3 compilations would be necessary. This would be much faster to compile and not make my storage cry, I reasoned.

After testing, however, I realized two things:
- Incremental builds mean that compiling neighboring commits is cheap. I tested compiling every commit vs every 8th, and on my machine every commit is about 2x as slow for 8x the builds, a tradeoff I felt was worth it.
- Bundling multiple versions of the Godot binary into the same compressed archive can result in such efficient packing I *can* store all the versions.

From 4.0-stable to 4.5-dev1, there are about 20k commits. The current official Linux build is compressed to about 58 MB by itself, which would mean this would require ~1.2 TB of storage. However, there's a lot of similarity between Godot binaries, and packing 20 of them into each archive gets them down to about 13 MB apiece! 20k versions is currently on track to take about 250 GB of space on my machine. Incremental builds take about 25% of the time of a clean build on average from my testing, which meant for me 20k versions of Godot takes about 10 days to compile. 10 days and 250 GB are both manageable numbers, so there was no need for me to *ever* compile during bisection. 

That said, I'm sure there are those of you who would rather just have a cheap accelerator for the broad phases at the start of bisection. If you configure REVISION_DENSITY to be larger than 1 during startup, BiMon will instead only precompile 1 out of every REVISION_DENSITY commits, and during bisection BiMon will use only the precompiled commits to narrow down the range as much as possible before optionally switching to compiling the commits as needed. BiMon is not smart about the choice of which commits to compile, and chooses only a bit better than randomly. When using sparse precompiled versions, more bisect steps may be needed. Your call on whether the extra steps vs the compile time are worth it.

## Speeding up bisection further
Once you have your commits prebuilt, BiMon manages your actual bisections for you by wrapping around git. While bisecting a revision, BiMon asks git for what the two next possible commits are and begins to extract them from the archives to avoid waiting for decompression. Once you've told BiMon whether the tested version was good or bad, it launches the project you're testing in the next version automatically. Since switching back to the terminal after each test is a small inefficiency, global hotkeys are also supported if you're willing to run the script as root. Hitting Ctrl+B or Ctrl+G for bad and good while bisecting will automatically close the launched editor, mark the commit, launch the next appropriate version, and begin decompressing the versions after that. Without root these shortcuts still work, but only when the terminal is in focus.

## Requirements
- Time. Compiling all the commits you want up front will take a long time. If you want to compile 20k commits, measure your clean build time in minutes and multiply it by 3.5. It'll take roughly that many *days* to finish. 
- Space. It'll take about 12.5 GB per 1k commits in the range you want to cover. 4.0 to 4.4 is about 250 GB.
- This was made for Linux. If you want it on other platforms, PRs are welcome. 
- TODO Dependencies from the `Optimizing your builds section` like clang, mold, and system libraries. Follow [Godot's guide](https://docs.godotengine.org/en/latest/contributing/development/compiling/compiling_for_linuxbsd.html) to install these. You can skip this by editing the compiler flags in the config so they aren't used, but doing so will likely slow the build process down significantly.

## Setup
1) Check out a new copy of the `godotengine/godot` repo in a location outside the BiMon folder since they have separate git setups. This will be called your workspace folder. Using a dedicated clone of godot instead of reusing the one you do dev work on is strongly recommended.
2) Optimize the builds in your workspace folder. See "Optimizing your builds" below for information on how to do so.
3) In the BiMon folder, run `cp default_config.py config.py` and then open `config.py` in your editor of choice. There are a variety of config options present that are commented with descriptions and instructions.
4) Run `bimon.py update` to begin the long process of compiling all your versions. This is safe to stop or force quit at any point and running it again later will continue the process, although it may waste a bit of progress. If it's killed while performing certain operations, it may litter the workspace folder with changes and refuse to run. Running with `-f` will discard those changes.
5) Wait a while for your compiles to be done.
6) Add `bimon.py update -f` to your task scheduler of choice so it executes once a day overnight. This is all you need to do to keep the version cache up to date as Godot is developed.

TODO something about setting up the venv and installing python deps??

## Optimizing your builds
Out of the box, compiling Godot is a long and slow process. I was getting about 10 minute clean compiles. At that pace, 20k commits would take about 35 days. That's not completely unreasonable, and since it works backwards from HEAD and you can still bisect the most recent regressions before it's done, but you can do better. [This page](https://docs.godotengine.org/en/latest/contributing/development/compiling/compiling_for_linuxbsd.html) has instructions you should follow. The clang, mold, and system libraries were all helpful. The system libraries also reduce the storage required.

BiMon runs with `dev_build=no optimize=none scu_build=yes`. `dev_build=no` should be on to reduce sizes since the symbol table isn't needed. `optimize=none` is then needed to speed the builds back up. `scu_build=yes` is just a massive time savings.

## Running BiMon
Before executing BiMon you'll need to `source venv/bin/activate`. Then, you can run it with `./bimon.py COMMAND [COMMAND_ARG...]`.

Porcelain commands:
- `update [-f] [CUT_REV]` - Fetch and compile missing commits, working back from `CUT_REV` until it hits config.py's `START_COMMIT`. `CUT_REV` defaults to the latest commit. If `-f` is provided, uncommitted changes in the workspace directory will be discarded instead of preventing the update.
- `bisect PROJECT` - Bisect history to find a regression's commit. Launches the project at the given path repeatedly until the regression is found. `PROJECT` may be the root folder of the project or the path of a `project.godot` file.
- `purge` - Delete any uncompressed binaries that are also present in compressed form. These may have been littered around if BiMon encounters an error or is forced to close while working.
- `help` - Show this info.

Plumbing commands (handled by `update`):
- `fetch` - Fetch the latest commits and update the processing lists.
- `compile [REV]` - Compile and store a specific revision `REV`. `REV` defaults to `HEAD`.
- `compress` - Pack completed bundles.

## Using `bisect`
When you run the `bisect` command, the program will enter an interactive mode. At the prompt you can type `good`, `bad`, `list`, or `start`. Only the first letter is necessary.
- `good` and `bad` mark the launched commit as such. They can instead be used with an argument to mark a different commit, like `g 4.2-stable`.
- `list` prints out a list of all the commits that could still contain the regression.
- `start` begins the bisection process, repeatedly opening editors until the regression is found.

TODO more workflow info

## TODO
- Finish this document x2
- config.py support
- other OS support?
- Hotkeys
- Launching
- Autoextract nexts
- Actual bisection
- update -f support
- repo cleanup and creation
- update help message
- partial bisection support
- make errors actually stop the compilations
- better output, remove times?
- ability to test a specific commit if you're suspicious or want to shortcut