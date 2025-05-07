import os
import sys
from argparse import ArgumentParser, SUPPRESS
from typing import Optional

from src import commands
from src.config import PrintMode


_BIMON_COMMANDS = [
    "init", "update", "repro", "bisect",
    "compile", "compress", "extract", 
    "create", "export", 
    "clean", "help",
    "write-precache",
]
_BISECT_COMMANDS = [
    "good", "bad", "skip", "unmark", 
    "set-params", "run", "automate", "pause",
    "list", "status", "help", "exit", "quit", 
]
_HIDDEN_COMMANDS = {"write-precache"}


def get_bimon_parser() -> ArgumentParser:
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

    parser = ArgumentParser(description=
        "BiMon: A tool for speeding up bug triage, mostly during bisecting.",
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
    parser.set_defaults(print_mode=PrintMode.LIVE if sys.stdout.isatty() else PrintMode.VERBOSE)
    add_messages("", parser)

    subparsers = parser.add_subparsers(
        dest="command", required=True, help="", description=SUPPRESS, title="Available commands")
    subparsers.metavar = ""

    # Porcelain commands
    init_parser = subparsers.add_parser("init",
        help="Initializes the workspace and runs some basic checks.",
        description="This command ensures that the workspace is set up correctly and not"
        + " much else. Running it on first set up will help make sure everything is in order.",
        add_help=False)
    init_parser.add_argument("-y", action="store_true", help=
        "Don't ask for confirmation before setting up the workspace.")
    add_messages("init", init_parser)
    init_parser.set_defaults(func=lambda _: 
        commands.init_command())

    update_parser = subparsers.add_parser("update",
        help="Fetch, compile, and cache missing commits.",
        description="This command compiles and caches any commits that are missing from the" 
        + " specified or configured ranges. Fetches from the remote repository before running.",
        add_help=False)
    update_parser.add_argument("-r", "--range", type=str, action="append", help=
        "The commit range(s) to compile, format \"start..end\"."
        + " Defaults to the range in config file.")
    update_parser.add_argument("-n", type=int, help=
        "Only compile and cache 1 in every N commits.")
    update_parser.add_argument("cursor_ref", nargs="?", help=
        "The commit to begin with. Accepts any git reference, defaults to HEAD.")
    add_messages("update", update_parser)
    update_parser.set_defaults(func=lambda args: 
        commands.update_command(args.n, args.cursor_ref, args.range))

    create_parser = subparsers.add_parser("create", help=
        "Creates a new project.",
        add_help=False)
    create_parser.add_argument("-t", "--title", type=str, help=
        "The title to add to the project file.")
    create_parser.add_argument("name", help=
        "The name of the project to create.")
    add_messages("create", create_parser)
    create_parser.set_defaults(func=lambda args: 
        commands.create_command(args.name, args.title))

    repro_parser = subparsers.add_parser("repro", help=
        "Try to reproduce an issue with some convenience options.",
        add_help=False)
    project_flag_description = (
        "The project to use for testing."
        + " Can be a project name, directory, project.godot, zip file, or link.")
    repro_parser.add_argument("-p", "--project", type=str, help=
        project_flag_description)
    repro_parser.add_argument("-i", "--issue", type=str, help=
        "The issue number or URL to try reproducing.")
    repro_parser.add_argument("-r", "--ref", "--commit", "--pr", type=str, help=
        "The commit to test with. Also accepts git references or PRs.")
    repro_parser.add_argument("-e", "--execution-parameters", type=str, help=
        "The parameters to pass to the executable. See the config.ini comments for details.")
    repro_parser.add_argument("-d", "--discard", action="store_true", help=
        "Prevents caching anything that gets compiled.")
    repro_parser.add_argument("flexible_args", nargs="*", help=
        "Accepts the same things as project, issue, and -r. Autodetects which is which.")
    add_messages("repro", repro_parser)
    repro_parser.set_defaults(func=lambda args: 
        commands.repro_command(
            execution_parameters=args.execution_parameters, 
            discard=args.discard,
            issue=args.issue, 
            project=args.project, 
            ref=args.ref,
            flexible_args=args.flexible_args))

    bisect_parser = subparsers.add_parser("bisect", help=
        "Bisect history to find a regression's commit via an interactive mode.",
        add_help=False)
    bisect_parser.add_argument("-p", "--project", type=str, default=None, help=
        project_flag_description)
    bisect_parser.add_argument("-i", "--issue", type=str, default=None, help=
        "The issue to test. Can be a link or number.")
    bisect_parser.add_argument("-r", "--range", type=str, default=None, help=
        "An initial, already confirmed range to bisect over, format \"good_ref..bad_ref\".")
    bisect_parser.add_argument("-e", "--execution-parameters", type=str, help=
        "The parameters to pass to the executable. See the config.ini comments for details.")
    bisect_parser.add_argument("-d", "--discard", action="store_true", help=
        "Prevents caching binaries compiled during a bisect.")
    bisect_parser.add_argument("--cached-only", action="store_true", help=
        "Prevents compiling at all during a bisect.")
    bisect_parser.add_argument("--ignore-date", action="store_true", help=
        "Don't use the issue open date to cut down the commit range.")
    bisect_parser.add_argument("--path-spec", type=str, default=None, help=
        "Limit the bisect to commits with specific files. See git bisect's path_spec for details.")
    bisect_parser.add_argument("flexible_args", nargs="*", help=
        "Accepts the same things as project, issue, and range. Autodetects which is which.")
    add_messages("bisect", bisect_parser)
    bisect_parser.set_defaults(func=lambda args: 
        commands.bisect_command(
            execution_parameters=args.execution_parameters, 
            discard=args.discard, 
            cached_only=args.cached_only, 
            ignore_date=args.ignore_date, 
            path_spec=args.path_spec, 
            project=args.project, 
            issue=args.issue, 
            flexible_args=args.flexible_args, 
            ref_range=args.range))

    clean_parser = subparsers.add_parser("clean", help=
        "Delete unneeded files.",
        add_help=False)
    clean_parser.add_argument("-d", "--duplicates", action="store_true", help=
        "Delete uncompressed versions that are duplicates of compressed versions.")
    clean_parser.add_argument("-b", "--build", "--build-artifacts", action="store_true", help=
        "Runs scons --clean.")
    clean_parser.add_argument("-c", "--caches", action="store_true", help=
        "Delete internal caches, mostly stored git information.")
    clean_parser.add_argument("-t", "--temp-files", action="store_true", help=
        "Delete the temporary storage locations used by various commands.")
    clean_parser.add_argument("--projects", action="store_true", help=
        "Delete all repro projects, including ones you've made. Use with caution.")
    clean_parser.add_argument("--downloads", action="store_true", help=
        "Delete all downloaded projects. Use with caution.")
    clean_parser.add_argument("--loose-files", action="store_true", help=
        "Delete any unrecognized files in the versions directory. Use with caution.")
    clean_parser.add_argument("--dry-run", action="store_true", help=
        "")
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
    compile_parser = subparsers.add_parser("compile", help=
        "Compile and store specific commits.",
        add_help=False)
    compile_parser.add_argument("ref_ranges", nargs="*", default="HEAD", help=
        "The commits to compile. Accepts references, ranges, or PRs. Uses HEAD if not provided.")
    add_messages("compile", compile_parser)
    compile_parser.set_defaults(func=lambda args: 
        commands.compile_command(args.refs))

    compress_parser = subparsers.add_parser("compress", help=
        "Archive completed bundles.",
        add_help=False)
    compress_parser.add_argument("-a", "--all", action="store_true", help=
        "Forces all files into bundles even if it makes suboptimal bundles.")
    add_messages("compress", compress_parser)
    compress_parser.set_defaults(func=lambda args: 
        commands.compress_command(args.all))

    extract_parser = subparsers.add_parser("extract", help=
        "Extract a specific version that's already built from storage.",
        add_help=False)
    extract_parser.add_argument("ref", help=
        "The version to extract. Can be a reference that resolves to a version.")
    extract_parser.add_argument("folder", nargs="?", help=
        "The folder to extract to.")
    add_messages("extract", extract_parser)
    extract_parser.set_defaults(func=lambda args: 
        commands.extract_command(args.ref, args.folder))

    export_parser = subparsers.add_parser("export", help=
        "Export a zipped version of a project for easy uploading.",
        add_help=False)
    export_parser.add_argument("-t", "--title", type=str, default=None, help=
        "Sets the exported project's title.")
    export_parser.add_argument("name", help=
        "The name of the project to export. Often an issue number.")
    export_parser.add_argument("target", help=
        "The location to extract to.")
    add_messages("export", export_parser)
    export_parser.set_defaults(func=lambda args: 
        commands.export_command(args.name, args.target, args.title))

    write_precache_parser = subparsers.add_parser("write-precache",
        add_help=False)
    write_precache_parser.add_argument("-h", "--help", action="help", default=SUPPRESS, help=
        "Show this help message and exit.")
    write_precache_parser.set_defaults(func=lambda _: 
        commands.write_precache_command())

    parser_help = subparsers.add_parser("help", help=
        "Print detailed help on some or all commands.",
        add_help=False)
    parser_help.add_argument("command_prefix", nargs="?", help=
        "Optional command to show help for.")
    add_messages("help", parser_help)
    parser_help.set_defaults(func=lambda args: 
        commands.help_command(help_messages, args.command_prefix))

    return parser


def get_bisect_parser() -> ArgumentParser:
    parser = ArgumentParser()
    add_bisect_parser(parser, interactive=True)
    return parser


def add_bisect_parser(parent_parser: ArgumentParser, interactive: bool) -> None:
    help_messages = []
    def add_messages(command: str, parser: ArgumentParser) -> None:
        parser.add_argument("-h", "--help", action="help", default=SUPPRESS, help=
            "Show this help message and exit.")
        help_messages.append((command, parser.format_usage(), parser.format_help()))

    parser = parent_parser.add_subparsers(
        dest=("" if interactive else "sub") + "command",
        required=not interactive, 
        description=("Any command that takes commits also accepts any git reference"
        + " and defaults to the current commit if none are provided."
        + " Good, bad, skip, and unmark may be combined in one line."
        + "Any unique prefix of a command is acceptable."
        + " For example, \"g\" is equivalent to \"good\"."))

    if not interactive:
        parser_start = parser.add_parser("start", add_help=False, help=
            "Starts a new bisect.")
        add_messages("start", parser_start)
        parser_start.set_defaults(func=lambda bisector, args: 
            print("TODO: Unimplemented"))

        parser_reset = parser.add_parser("reset", add_help=False, help=
            "Clears any existing bisects.")
        add_messages("reset", parser_reset)
        parser_reset.set_defaults(func=lambda bisector, args: 
            print("TODO: Unimplemented"))

    if interactive:
        parser_automate = parser.add_parser("automate", add_help=False, help=
            "Enable automatic opening of versions.")
        parser_automate.add_argument("-g", "--good", type=str, default=None, help=
            "A string that indicates this is a good commit if printed during execution.")
        parser_automate.add_argument("-b", "--bad", type=str, default=None, help=
            "A string that indicates this is a good commit if printed during execution.")
        parser_automate.add_argument("-c", "--crash", type=str, default=None, help=
            "Can be good, bad, or skip."
            + " Indicates what to mark commits if the program exits non-zero.")
        parser_automate.add_argument("-e", "--exit", type=str, default=None, help=
            "Can be good, bad, or skip. Indicates what to mark commits if the program exits zero.")
        parser_automate.add_argument("-s", "--script", type=str, default=None, help=
            "If provided, runs the script instead of the executable."
            + " The command that would've run otherwise is passed to the script.")
        parser_automate.add_argument("-r", "--regex", type=bool, default=False, help=
            "If provided, the --good and --bad arguments are treated as regexes.")
        add_messages("automate", parser_automate)
        parser_automate.set_defaults(func=lambda bisector, args: 
            bisector.automate_command(
                args.good, 
                args.bad, 
                args.crash, 
                args.exit, 
                args.script, 
                args.regex))

        parser_pause = parser.add_parser("pause", add_help=False, help=
            "Pause automatic opening of versions.")
        add_messages("pause", parser_pause)
        parser_pause.set_defaults(func=lambda bisector, _: 
            bisector.pause_command())

        parser_exit = parser.add_parser("exit", add_help=False, help=
            "Exit interactive bisect.")
        add_messages("exit", parser_exit)
        parser_exit.set_defaults(func=lambda bisector, _: 
            bisector.exit_command())
        parser_quit = parser.add_parser("quit", add_help=False, help=SUPPRESS)
        parser_quit.set_defaults(func=lambda bisector, _: 
            bisector.exit_command())

    parser_good = parser.add_parser("good", add_help=False, help=
        "Mark commits as good.")
    parser_good.add_argument("commits", nargs="*", help=
        "The commits to mark as good.")
    add_messages("good", parser_good)
    parser_good.set_defaults(func=lambda bisector, args: 
        bisector.mark_command("good", args.commits))

    parser_bad = parser.add_parser("bad", add_help=False, help=
        "Mark commits as bad.")
    parser_bad.add_argument("commits", nargs="*", help=
        "The commits to mark as bad.")
    add_messages("bad", parser_bad)
    parser_bad.set_defaults(func=lambda bisector, args: 
        bisector.mark_command("bad", args.commits))

    parser_skip = parser.add_parser("skip", add_help=False, help=
        "Mark commits as untestable.")
    parser_skip.add_argument("commits", nargs="*", help=
        "The commits to mark as untestable.")
    add_messages("skip", parser_skip)
    parser_skip.set_defaults(func=lambda bisector, args: 
        bisector.mark_command("skip", args.commits))

    parser_unmark = parser.add_parser("unmark", add_help=False, help=
        "Unmark commits.")
    parser_unmark.add_argument("commits", nargs="*", help=
        "The commits to unmark.")
    add_messages("unmark", parser_unmark)
    parser_unmark.set_defaults(func=lambda bisector, args: 
        bisector.mark_command("unmark", args.commits))

    parser_run = parser.add_parser("run", add_help=False, help=
        "Runs a specific commit.")
    parser_run.add_argument("commit", nargs="*", help=
        "The commit or git reference to open. Defaults to the current commit.")
    add_messages("run", parser_run)
    parser_run.set_defaults(func=lambda bisector, args: 
        bisector.run_command(args.commit))

    parser_list = parser.add_parser("list", add_help=False, help=
        "List all remaining possible commits.")
    parser_list.add_argument("-s", "--short", action="store_true", help=
        "Print only shortened commit SHAs and no log information.")
    add_messages("list", parser_list)
    parser_list.set_defaults(func=lambda bisector, args: 
        bisector.list_command(args.short))

    parser_status = parser.add_parser("status", add_help=False, help=
        "Print information about the state of the current bisect.")
    parser_status.add_argument("-s", "--short", action="store_true", help=
        "Don't print the minimal commit sets.")
    add_messages("status", parser_status)
    parser_status.set_defaults(func=lambda bisector, args: 
        bisector.status_command(args.short))

    parser_set_params = parser.add_parser("set-params", add_help=False, help=
        "Replaces the current execution parameters.")
    parser_set_params.add_argument("execution_parameters", help=
        "The new execution parameters to pass into binaries when running them.")
    add_messages("set-params", parser_set_params)
    parser_set_params.set_defaults(func=lambda bisector, args: 
        bisector.set_parameters_command(args.execution_parameters))

    parser_help = parser.add_parser("help", add_help=False, help=
        "Print a help message.")
    parser_help.add_argument("command_prefix", nargs="?", help=
        "Optional command to show help for.")
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
    
    matches = [
        cmd for cmd in commands 
        if cmd.startswith(args[0].lower())
        and (cmd not in _HIDDEN_COMMANDS or cmd == args[0].lower())
    ]
    if len(matches) == 1:
        args[0] = matches[0]
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