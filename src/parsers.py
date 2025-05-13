import os
import sys
from argparse import ArgumentParser, SUPPRESS
from typing import Optional

from src import commands
from src.config import PrintMode


_BIMON_COMMANDS = [
    "init", "update", "run", "bisect",
    "compile", "compress", "extract",
    "create", "export",
    "clean", "help",
    "write-precache",
]
_BISECT_COMMANDS = [
    "good", "bad", "skip", "unmark",
    "set-arguments", "run", "automate", "pause",
    "list", "status", "help", "exit", "quit",
]
_HIDDEN_COMMANDS = {"write-precache"}


def get_bimon_parser(base_command: Optional[str] = None) -> ArgumentParser:
    platform = "linux"
    if sys.platform.lower() == "darwin":
        platform = "mac"
    elif os.name == "nt":
        platform = "windows"
    help_messages = []
    def add_messages(command: str, parser: ArgumentParser) -> None:
        parser.add_argument("-h", "--help", action="help", default=SUPPRESS, help=
            "Show this help message and exit.")
        help_messages.append((command, parser.format_usage(), parser.format_help()))

    parser = ArgumentParser(
        description="BiMon: A tool for speeding up bug triage, mostly during bisecting.",
        epilog="For detailed information on a command, run \"bimon.py <command> --help\".\n\n",
        usage="bimon.py [-h] [-q/v/l] [--color [yes/no]] [--config PATH] [-i] command ...",
        add_help=False)
    parser.add_argument("-q", "--quiet",
        action="store_const", const=PrintMode.QUIET, dest="print_mode", help=
        "Hides output from long-running subprocesses.")
    parser.add_argument("-v", "--verbose",
        action="store_const", const=PrintMode.VERBOSE, dest="print_mode", help=
        "Shows full output from subprocesses.")
    parser.add_argument("-l", "--live",
        action="store_const", const=PrintMode.LIVE, dest="print_mode", help=
        "Show a small updating display of subprocess output.")
    parser.add_argument("--color", nargs="?", const=True, default=None, type=bool_arg_parse, help=
        "Enables/disables colored output (yes/no).")
    parser.add_argument("--config", type=str, help=
        "Path to the configuration file."
        + f" Defaults to config.ini, falls back to default_{platform}_config.ini.")
    parser.add_argument("-i", "--ignore-old-errors", action="store_true", help=
        "Don't skip commits even if they have been unbuildable in the past")
    live_default = sys.stdout.isatty() and os.name != "nt"
    parser.set_defaults(print_mode=PrintMode.LIVE if live_default else PrintMode.VERBOSE)
    add_messages("", parser)

    subparsers = parser.add_subparsers(
        dest="command", required=True, help="", description=SUPPRESS, title="Available commands")
    subparsers.metavar = ""

    # Porcelain commands
    init_parser = subparsers.add_parser("init",
        help="Sets up the workspaces needed and runs some basic checks.",
        description=("This command ensures that the workspaces are cloned and does a few basic checks."
            + " Running it on first set up is recommended."),
        usage="bimon.py init [-y]",
        add_help=False)
    init_parser.add_argument("-y", action="store_true", help=
        "Don't ask for confirmation before cloning repositories.")
    add_messages("init", init_parser)
    init_parser.set_defaults(func=lambda _:
        commands.init_command())

    update_parser = subparsers.add_parser("update",
        help="Fetch, compile, and cache missing commits.",
        description="This command compiles and caches any commits that are missing from the"
        + " specified or configured ranges. Fetches from the remote repository before running.",
        usage="bimon.py update [-r START_REF..END_REF]... [-n N] [CURSOR_REF]",
        add_help=False)
    update_parser.add_argument("-r", "--range", type=str, action="append", help=
        "The commit range(s) to compile, format \"start..end\"."
        + " Defaults to the range in config file.")
    update_parser.add_argument("-n", type=int, help=
        "Only compile and cache 1 in every N commits, roughly evenly spaced")
    update_parser.add_argument("cursor_ref", nargs="?", help=
        "The commit to start compiling from. Defaults to HEAD.")
    add_messages("update", update_parser)
    update_parser.set_defaults(func=lambda args:
        commands.update_command(args.n, args.cursor_ref, args.range))

    run_parser = subparsers.add_parser("run",
        help="Runs the requested version of Godot.",
        description="Runs godot with some convenience features to help reproduce issues.",
        usage="bimon.py run [-p PROJECT] [-i ISSUE] [-r COMMIT] [-e EXECUTION_ARGS] [-d] [FLEXIBLE_ARGS]...",
        add_help=False)
    project_flag_description = "The project to use as a working directory when launching Godot"
    issue_flag_description = "The issue number or link to reproduce. Looks for associated projects locally and on the issue page."
    discard_flag_description = "Don't store the result of any builds that occur"
    execution_flag_description = "The arguments to pass to the executable. See the config.ini comments for details."
    run_parser.add_argument("-p", "--project", type=str, help=project_flag_description)
    run_parser.add_argument("-i", "--issue", type=str, help=issue_flag_description)
    run_parser.add_argument("-r", "--ref", type=str, help=
        "The commit to run. Accepts any git references or PRs.")
    run_parser.add_argument("-e", "--execution-arguments", type=str, help=execution_flag_description)
    run_parser.add_argument("-d", "--discard", action="store_true", help=discard_flag_description)
    run_parser.add_argument("flexible_args", nargs="*", help=
        "Accepts the same things as -p, -i, and -r. Autodetects which is which.")
    add_messages("run", run_parser)
    run_parser.set_defaults(func=lambda args:
        commands.run_command(
            execution_args=args.execution_arguments,
            discard=args.discard,
            issue=args.issue,
            project=args.project,
            ref=args.ref,
            flexible_args=args.flexible_args))

    bisect_parser = subparsers.add_parser("bisect",
        help="Bisect history to find which commit introduced a regression.",
        description="Bisect history to find which commit introduced a regression via an interactive mode.",
        usage="bimon.py bisect [-p PROJECT] [-i ISSUE] [-r GOOD_REF..BAD_REF] [-e EXECUTION_ARGS] [-d] [--cached-only] [--path-spec SPEC] [FLEXIBLE_ARGS]...",
        add_help=False)
    bisect_parser.add_argument("-p", "--project", type=str, default=None, help=project_flag_description)
    bisect_parser.add_argument("-i", "--issue", type=str, default=None, help=issue_flag_description)
    bisect_parser.add_argument("-r", "--range", type=str, default=None, help=
        "A starting range to bisect down, format \"good_ref..bad_ref\".")
    bisect_parser.add_argument("-e", "--execution-arguments", type=str, help=execution_flag_description)
    bisect_parser.add_argument("-d", "--discard", action="store_true", help=discard_flag_description)
    bisect_parser.add_argument("--cached-only", action="store_true", help=
        "Only bisect using precompiled versions, stopping when compiles would be required")
    bisect_parser.add_argument("--ignore-date", action="store_true", help=
        "Don't use the issue open date to cut down the commit range.")
    bisect_parser.add_argument("--path-spec", type=str, default=None, help=
        "Limit the search to commits with specific files. See git bisect's path spec for details.")
    bisect_parser.add_argument("flexible_args", nargs="*", help=
        "Accepts the same things as project, issue, and range. Autodetects which is which.")
    add_messages("bisect", bisect_parser)
    bisect_parser.set_defaults(func=lambda args:
        commands.bisect_command(
            execution_args=args.execution_arguments,
            discard=args.discard,
            cached_only=args.cached_only,
            ignore_date=args.ignore_date,
            path_spec=args.path_spec,
            project=args.project,
            issue=args.issue,
            flexible_args=args.flexible_args,
            ref_range=args.range))

    create_parser = subparsers.add_parser("create",
        help="Creates a new project.",
        description="Creates a new named project you can use in the run and bisect commands.",
        usage="bimon.py create [-t TITLE] [-3] NAME",
        add_help=False)
    create_parser.add_argument("-t", "--title", type=str, help=
        "The initial title for the project")
    create_parser.add_argument("-3", dest="three", action="store_true", help=
        "Create a Godot 3.x project. Defaults to 4.x.")
    create_parser.add_argument("name", help=
        "The name to refer to this project by for other commands")
    add_messages("create", create_parser)
    create_parser.set_defaults(func=lambda args:
        commands.create_command(args.name, args.three, args.title))

    export_parser = subparsers.add_parser("export",
        help="Export a zipped version of a project for easy uploading.",
        description="Exports a project from the \"projects\" folder to a zip for uploading.",
        usage="bimon.py export [-t TITLE] [-a] NAME TARGET",
        add_help=False)
    export_parser.add_argument("-t", "--title", type=str, default=None, help=
        "Overwrite the project title with \"TITLE\" on the exported project")
    export_parser.add_argument("-a", "--as-is", action="store_true", help=
        "Include the .godot folder, which is excluded by default")
    export_parser.add_argument("name", help=
        "The name of the project to export. Often an issue number.")
    export_parser.add_argument("target", help=
        "The destination zip to export to")
    add_messages("export", export_parser)
    export_parser.set_defaults(func=lambda args:
        commands.export_command(args.name, args.target, args.title, args.as_is))

    clean_parser = subparsers.add_parser("clean",
        help="Delete unneeded files.",
        description="Offers a variety of ways to clean up potentially wasted space.",
        usage="bimon.py clean [-d] [-b] [-c] [-t] [--projects] [--loose-files] [--dry-run]",
        add_help=False)
    clean_parser.add_argument("-d", "--duplicates", action="store_true", help=
        "Delete uncompressed versions that are duplicates of compressed versions")
    clean_parser.add_argument("-b", "--build-artifacts", action="store_true", help=
        "Delete build files with \"scons --clean.\"")
    clean_parser.add_argument("-c", "--caches", action="store_true", help=
        "Delete internal caches, mostly stored git information that may take a while to regenerate")
    clean_parser.add_argument("-t", "--temp-files", action="store_true", help=
        "Delete temporary files used during processing")
    clean_parser.add_argument("--projects", action="store_true", help=
        "Delete all projects, including ones you've made. Use with caution.")
    clean_parser.add_argument("--loose-files", action="store_true", help=
        "Delete any unrecognized files in the versions directory. Use with caution.")
    clean_parser.add_argument("--dry-run", action="store_true", help=
        "Prints information about what would be deleted but does nothing. Use without caution.")
    add_messages("clean", clean_parser)
    clean_parser.set_defaults(func=lambda args:
        commands.clean_command(
            projects=args.projects,
            duplicates=args.duplicates,
            caches=args.caches,
            temp_files=args.temp_files,
            loose_files=args.loose_files,
            build_artifacts=args.build_artifacts,
            dry_run=args.dry_run))

    # Plumbing commands
    compile_parser = subparsers.add_parser("compile",
        help="Compile and store specific commits.",
        description="Compile and store specific commits. Very similar to update.",
        usage="bimon.py compile [COMMIT_OR_RANGE]...",
        add_help=False)
    compile_parser.add_argument("refs", nargs="*", default=["HEAD"], help=
        "The commits to compile. Accepts references, ranges, or PRs. Uses HEAD if not provided.")
    add_messages("compile", compile_parser)
    compile_parser.set_defaults(func=lambda args:
        commands.compile_command(args.refs))

    compress_parser = subparsers.add_parser("compress",
        help="Compresses completed versions into bundles.",
        description="Packs uncompressed versions into compressed bundles.",
        usage="bimon.py compress [-a]",
        add_help=False)
    compress_parser.add_argument("-a", "--all", action="store_true", help=
        "Force all versions to be compressed even if it creates undersized or poorly optimized bundles")
    add_messages("compress", compress_parser)
    compress_parser.set_defaults(func=lambda args:
        commands.compress_command(args.all))

    extract_parser = subparsers.add_parser("extract",
        help="Extract a specific version that's already built from storage.",
        description="Extracts the build artifacts for the requested version to a location of your choice.",
        usage="bimon.py extract REF [FOLDER]",
        add_help=False)
    extract_parser.add_argument("ref", help=
        "The version to extract the build artifacts for")
    extract_parser.add_argument("folder", nargs="?", help=
        "The target output folder for the files to be extracted into. Defaults to \"versions/COMMIT_SHA\".")
    add_messages("extract", extract_parser)
    extract_parser.set_defaults(func=lambda args:
        commands.extract_command(args.ref, args.folder))

    write_precache_parser = subparsers.add_parser("write-precache",
        add_help=False)
    write_precache_parser.add_argument("-h", "--help", action="help", default=SUPPRESS, help=
        "Show this help message.")
    write_precache_parser.set_defaults(func=lambda _:
        commands.write_precache_command())

    parser_help = subparsers.add_parser("help",
        help="Print detailed help on some or all commands.",
        description="Print detailed help on some or all commands.",
        usage="bimon.py help [COMMAND_PREFIX]",
        add_help=False)
    parser_help.add_argument("command_prefix", nargs="?", help=
        "Optional command to show help for.")
    add_messages("help", parser_help)
    parser_help.set_defaults(func=lambda args:
        commands.help_command(help_messages, args.command_prefix))

    return parser


def get_bisect_parser() -> ArgumentParser:
    parser = ArgumentParser(exit_on_error=False)
    add_bisect_parser(parser)
    return parser


def add_bisect_parser(parent_parser: ArgumentParser) -> None:
    help_messages = []
    def add_messages(command: str, parser: ArgumentParser) -> None:
        parser.add_argument("-h", "--help", action="help", default=SUPPRESS, help=
            "Show this help message.")
        help_messages.append((command, parser.format_usage(), parser.format_help()))

    parser = parent_parser.add_subparsers(
        dest="command",
        required=False,
        description=("Any command that takes commits also accepts any git reference"
        + " and defaults to the current commit if none are provided."
        + " Good, bad, skip, and unmark may be combined in one line."
        + "Any unique prefix of a command is acceptable."
        + " For example, \"g\" is equivalent to \"good\"."))

    parser_good = parser.add_parser("good", add_help=False,
        help="Mark commits as good.",
        description="Mark commits as good. Can be combined with bad, skip, or unmark.",
        usage="good [COMMIT]...")
    parser_good.add_argument("commits", nargs="*", help=
        "The commits to mark as good. Accepts any git reference.")
    add_messages("good", parser_good)
    parser_good.set_defaults(func=lambda bisector, args:
        bisector.mark_command("good", args.commits))

    parser_bad = parser.add_parser("bad", add_help=False,
        help="Mark commits as bad.",
        description="Mark commits as bad. Can be combined with good, skip, or unmark.",
        usage="bad [COMMIT]...")
    parser_bad.add_argument("commits", nargs="*", help=
        "The commits to mark as bad. Accepts any git reference.")
    add_messages("bad", parser_bad)
    parser_bad.set_defaults(func=lambda bisector, args:
        bisector.mark_command("bad", args.commits))

    parser_skip = parser.add_parser("skip", add_help=False,
        help="Mark commits as untestable.",
        description="Mark commits as untestable. Can be combined with good, bad, or unmark.",
        usage="skip [COMMIT]...")
    parser_skip.add_argument("commits", nargs="*", help=
        "The commits to mark as untestable. Accepts any git reference.")
    add_messages("skip", parser_skip)
    parser_skip.set_defaults(func=lambda bisector, args:
        bisector.mark_command("skip", args.commits))

    parser_unmark = parser.add_parser("unmark", add_help=False,
        help="Unmark commits.",
        description="Unmark commits as good, bad, or skipped. Can be combined with good, bad, or skip.",
        usage="unmark [COMMIT]...")
    parser_unmark.add_argument("commits", nargs="*", help=
        "The commits to unmark as good, bad, or skipped. Accepts any git reference.")
    add_messages("unmark", parser_unmark)
    parser_unmark.set_defaults(func=lambda bisector, args:
        bisector.mark_command("unmark", args.commits))

    parser_automate = parser.add_parser("automate", add_help=False,
        help="Enable automatic opening and marking of versions.",
        description="Starts opening commits automatically with options to help mark commit automatically, as well.",
        usage="automate [-g GOOD_TEXT] [-b BAD_TEXT] [-c good/bad/skip] [-e good/bad/skip] [-r REGEX] [-s SCRIPT]",)
    parser_automate.add_argument("-g", "--good", type=str, default=None, help=
        "If this text is printed during execution, mark the commit as good")
    parser_automate.add_argument("-b", "--bad", type=str, default=None, help=
        "If this text is printed during execution, mark the commit as bad")
    parser_automate.add_argument("-c", "--crash", type=str, default=None, help=
        "What to mark commits that crash during execution (good/bad/skip)")
    parser_automate.add_argument("-e", "--exit", type=str, default=None, help=
        "What to mark commits that exit normally (good/bad/skip)")
    parser_automate.add_argument("-r", "--regex", action="store_true", help=
        "If provided, --good and --bad are treated as regexes")
    parser_automate.add_argument("-s", "--script", type=str, default=None, help=
        "Run this script instead of the executable, passing it the executable location and arguments")
    add_messages("automate", parser_automate)
    parser_automate.set_defaults(func=lambda bisector, args:
        bisector.automate_command(
            args.good,
            args.bad,
            args.crash,
            args.exit,
            args.script,
            args.regex))

    parser_pause = parser.add_parser("pause", add_help=False,
        help="Pause automatic opening of versions.",
        description="Stops automatically opening commits; other automation remains active.",
        usage="pause")
    add_messages("pause", parser_pause)
    parser_pause.set_defaults(func=lambda bisector, _:
        bisector.pause_command())

    parser_exit = parser.add_parser("exit", add_help=False,
        help="Exit interactive bisect.",
        description="Exits the interactive bisect and prints a final status message.",
        usage="exit")
    add_messages("exit", parser_exit)
    parser_exit.set_defaults(func=lambda bisector, _:
        bisector.exit_command())
    parser_quit = parser.add_parser("quit", add_help=False, help=SUPPRESS)
    parser_quit.set_defaults(func=lambda bisector, _:
        bisector.exit_command())

    parser_run = parser.add_parser("run", add_help=False,
        help="Runs godot.",
        description="Runs the given commits in order.",
        usage="run [COMMIT]...",)
    parser_run.add_argument("commit", nargs="*", help=
        "The commit or git reference to open. Defaults to the current commit.")
    add_messages("run", parser_run)
    parser_run.set_defaults(func=lambda bisector, args:
        bisector.run_command(args.commit))

    parser_list = parser.add_parser("list", add_help=False,
        help="Lists all remaining possible commits.",
        description="",
        usage="list [-s] [-b]",)
    parser_list.add_argument("-s", "--short", action="store_true", help=
        "Print only shortened commit SHAs and no log information.")
    parser_list.add_argument("-b", "--best", action="store_true", help=
        "Sort by best bisect score instead of commit date.")
    add_messages("list", parser_list)
    parser_list.set_defaults(func=lambda bisector, args:
        bisector.list_command(args.short, args.best))

    parser_status = parser.add_parser("status", add_help=False,
        help="Prints summary information about the current bisect.",
        description="Prints summary information about the current bisect.",
        usage="status [-s]")
    parser_status.add_argument("-s", "--short", action="store_true", help=
        "Print only the primary status information.")
    add_messages("status", parser_status)
    parser_status.set_defaults(func=lambda bisector, args:
        bisector.status_command(args.short))

    parser_set_args = parser.add_parser("set-arguments", add_help=False,
        help="Updates the arguments Godot will be run with.",
        description="Updates the arguments Godot will be run with.",
        usage="set-arguments EXECUTION_ARGUMENTS")
    parser_set_args.add_argument("arguments", help=
        "The new arguments to pass into binaries when running them.")
    add_messages("set-args", parser_set_args)
    parser_set_args.set_defaults(func=lambda bisector, args:
        bisector.set_arguments_command(args.execution_args))

    parser_help = parser.add_parser("help", add_help=False,
        help="Print detailed help on some or all commands.",
        description="Print detailed help on some or all commands.",
        usage="help [COMMAND_PREFIX]")
    parser_help.add_argument("command_prefix", nargs="?", help=
        "Show help for all commands that match the given prefix")
    add_messages("help", parser_help)
    parser_help.set_defaults(func=lambda _, args:
        commands.help_command(help_messages, args.command_prefix, {"exit": ["quit"]}))


def preparse_bisect_command(args: list[str]) -> list[str]:
    return _preparse_command(args, _BISECT_COMMANDS)


def preparse_bimon_command(args: list[str]) -> list[str]:
    return _preparse_command(args, _BIMON_COMMANDS)


def _preparse_command(args: list[str], commands: list[str]) -> list[str]:
    if len(args) == 0:
        return args

    command_index = 0
    while command_index < len(args) and args[command_index].startswith("-"):
        if args[command_index].startswith("--c") and "=" not in args[command_index]:
            command_index += 1
        command_index += 1
    if command_index >= len(args):
        return args

    matches = [
        cmd for cmd in commands
        if cmd.startswith(args[command_index].lower())
        and (cmd not in _HIDDEN_COMMANDS or cmd == args[command_index].lower())
    ]
    if len(matches) == 1:
        args[command_index] = matches[0]
        return args
    elif len(matches) > 1:
        print("Ambiguous command. Possible completions:")
        for cmd in matches:
            print(f"  {cmd}")
    return []


def bisect_command_completer(self, text: str, state: int) -> Optional[str]:
    matches = [cmd for cmd in _BISECT_COMMANDS if cmd.startswith(text)]
    return matches[state] if state < len(matches) else None


def bool_arg_parse(arg: str) -> bool:
    return arg.lower() in ("true", "1", "yes", "")