import argparse
import os
import sys

import src.commands as commands
from src.config import PrintMode


def get_bimon_parser() -> argparse.ArgumentParser:
    platform = "linux"
    if sys.platform.lower() == "darwin":
        platform = "mac"
    elif os.name == "nt":
        platform = "windows"
    help_messages = []
    def add_messages(command: str, parser: argparse.ArgumentParser) -> None:
        help_messages.append((command, parser.format_usage(), parser.format_help()))

    parser = argparse.ArgumentParser(description=
        "BiMon: A tool for speeding up bisecting during bug triage for the Godot engine bugsquad.",
        epilog="For detailed information on a command, run 'bimon.py <command> --help'.\n\n")
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
        "Enables colored output.")
    parser.add_argument("-c", "--config", type=str, help=
        "Path to the configuration file."
        + f" Defaults to config.ini, falls back to default_{platform}_config.ini.")
    parser.add_argument("-i", "--ignore-old-errors", action="store_true", help=
        "Don't skip commits even if they have been unbuildable in the past")
    parser.set_defaults(print_mode=PrintMode.LIVE if sys.stdout.isatty() else PrintMode.VERBOSE)

    subparsers = parser.add_subparsers(dest="command", required=True, help=
		"Available commands")

    # Porcelain commands
    init_parser = subparsers.add_parser("init",
        help="Initializes the workspace and runs some basic checks.",
        description="This command ensures that the workspace is set up correctly and not"
        + " much else. Running it on first set up will help make sure everything is in order.")
    init_parser.add_argument("-y", action="store_true", help=
        "Don't ask for confirmation before setting up the workspace.")
    add_messages("init", init_parser)
    init_parser.set_defaults(func=lambda _: 
        commands.init_command())

    update_parser = subparsers.add_parser("update",
        help="Fetch, compile, and cache missing commits.",
        description="This command compiles and caches any commits that are missing from the" 
        + " specified or configured ranges. Fetches from the remote repository before running.")
    update_parser.add_argument("-n", type=int, help=
        "Only compile and cache 1 in every N commits.")
    update_parser.add_argument("-r", "--range", type=str, action="append", help=
        "The commit range(s) to compile, format 'start..end'."
        + " Defaults to the range in config file.")
    update_parser.add_argument("cursor_ref", nargs="?", help=
        "The commit to begin with. Accepts any git reference, defaults to HEAD.")
    add_messages("update", update_parser)
    update_parser.set_defaults(func=lambda args: 
        commands.update_command(args.n, args.cursor_ref, args.range))

    repro_parser = subparsers.add_parser("repro", help=
        "Try to reproduce an issue using the specified parameters.")
    repro_parser.add_argument("-d", "--discard", action="store_true", help=
        "Prevents caching anything that gets compiled.")
    repro_parser.add_argument("-e", "--execution-parameters", type=str, help=
        "The parameters to pass to the executable. See the config.ini comments for details.")
    repro_parser.add_argument("--project", type=str, help=
        "The project to use as an MRP. Can be a directory, project.godot, zip file, or link.")
    repro_parser.add_argument("--issue", type=str, help=
        "The issue number or URL to try reproducing.")
    repro_parser.add_argument("--ref", "--commit", type=str, help=
        "The commit to test with. Also accepts git references.")
    repro_parser.add_argument("flexible_args", nargs="*", help=
        "Accepts the same things as the previous three options. Autodetects which each is.")
    add_messages("repro", repro_parser)
    repro_parser.set_defaults(func=lambda args: 
        commands.repro_command(
            execution_parameters=args.execution_parameters, 
            discard=args.discard,
            issue=args.issue, 
            project=args.project, 
            version=args.version, 
            ref=args.ref,
            flexible_args=args.flexible_args))

    bisect_parser = subparsers.add_parser("bisect", help=
        "Bisect history to find a regression's commit via an interactive mode.")
    bisect_parser.add_argument("-d", "--discard", action="store_true", help=
        "Prevents caching binaries compiled during a bisect.")
    bisect_parser.add_argument("-c", "--cached-only", action="store_true", help=
        "Prevents compiling at all during a bisect.")
    bisect_parser.add_argument("-e", "--execution-parameters", type=str, help=
        "The parameters to pass to the executable. See the config.ini comments for details.")
    bisect_parser.add_argument("--ignore-date", action="store_true", help=
        "Don't use the issue open date to cut down the commit range.")
    bisect_parser.add_argument("--path-spec", type=str, default=None, help=
        "Limit the bisect to commits with specific files. See git bisect's path_spec for details.")
    bisect_parser.add_argument("--project", type=str, default=None, help=
        "The project to use for testing. Can be a directory, project.godot, zip file, or link.")
    bisect_parser.add_argument("--issue", type=str, default=None, help=
        "The issue to test. Can be a link or number.")
    bisect_parser.add_argument("-r", "--range", type=str, default=None, help=
        "An initial, already confirmed range to bisect over, format 'good_ref..bad_ref'.")
    bisect_parser.add_argument("flexible_args", nargs="*", help=
        "Accepts the same things as the previous three options. Autodetects which each is.")
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

    purge_parser = subparsers.add_parser("purge", help=
        "Delete unneeded files.")
    purge_parser.add_argument("--projects", action="store_true", help=
        "Delete all repro projects, including ones you've made. Use with caution.")
    purge_parser.add_argument("--downloads", action="store_true", help=
        "Delete all downloaded projects. Use with caution.")
    purge_parser.add_argument("--duplicates", action="store_true", help=
        "Delete uncompressed versions that are duplicates of compressed versions.")
    purge_parser.add_argument("--caches", action="store_true", help=
        "Delete internal caches, mostly stored git information.")
    purge_parser.add_argument("--temp-files", action="store_true", help=
        "Delete the temporary storage locations used by various commands.")
    purge_parser.add_argument("--loose-files", action="store_true", help=
        "Delete any unrecognized files in the versions directory. Use with caution.")
    purge_parser.add_argument("--build", "--build-artifacts", action="store_true", help=
        "Runs scons --clean.")
    add_messages("purge", purge_parser)
    purge_parser.set_defaults(func=lambda args: 
        commands.purge_command(
            projects=args.projects, 
            downloads=args.downloads, 
            duplicates=args.duplicates, 
            caches=args.caches, 
            temp_files=args.temp_files, 
            loose_files=args.loose_files, 
            build_artifacts=args.build_artifacts))

    # Plumbing commands
    compile_parser = subparsers.add_parser("compile", help=
        "Compile and store specific commits.")
    compile_parser.add_argument("ref_ranges", nargs="*", default="HEAD", help=
        "The commits to compile. Accepts references or ranges. Uses HEAD if not provided.")
    add_messages("compile", compile_parser)
    compile_parser.set_defaults(func=lambda args: 
        commands.compile_command(args.refs))

    compress_parser = subparsers.add_parser("compress", help=
        "Archive completed bundles.")
    compress_parser.add_argument("-a", "--all", action="store_true", help=
        "Forces all files into bundles even if it makes suboptimal bundles.")
    add_messages("compress", compress_parser)
    compress_parser.set_defaults(func=lambda args: 
        commands.compress_command(args.n, args.all))

    extract_parser = subparsers.add_parser("extract", help=
			"Extract a specific version.")
    extract_parser.add_argument("ref", help=
			"The version to extract. Can be a reference that resolves to a version.")
    extract_parser.add_argument("folder", nargs="?", help=
			"The folder to extract to.")
    add_messages("extract", extract_parser)
    extract_parser.set_defaults(func=lambda args: 
        commands.extract_command(args.ref, args.folder))

    write_precache_parser = subparsers.add_parser("write-precache")
    write_precache_parser.set_defaults(func=lambda _: 
        commands.write_precache_command())

    parser_help = parser.add_parser("help", help=
        "Print a help message.")
    parser_help.add_argument("command_prefix", nargs="?", help=
        "Optional command to show help for.")
    add_messages("help", parser_help)
    parser_help.set_defaults(func=lambda args: 
        commands.help_command(help_messages, args.command_prefix))

    return parser


def add_bisect_parser(parent_parser: argparse.ArgumentParser, interactive: bool) -> None:
    parser = parent_parser.add_subparsers(
        dest=("" if interactive else "sub") + "command",
        required=not interactive, 
        description="Any command that takes commits also accepts any git reference"
        + " and defaults to the current commit if none are provided."
        + " Good, bad, skip, and unmark may be combined in one line.",
        epilog="Any unique prefix of a command is acceptable."
        + " For example, \"g\" is equivalent to \"good\"."
    )
    help_messages = []
    def add_messages(command: str, parser: argparse.ArgumentParser) -> None:
        help_messages.append((command, parser.format_usage(), parser.format_help()))

    if not interactive:
        parser_start = parser.add_parser("start", help=
            "Starts a new bisect.")
        add_messages("start", parser_start)
        parser_start.set_defaults(func=lambda runner, args: 
            print("TODO: Unimplemented"))

        parser_reset = parser.add_parser("reset", help=
            "Clears any existing bisects.")
        add_messages("reset", parser_reset)
        parser_reset.set_defaults(func=lambda runner, args: 
            print("TODO: Unimplemented"))

    if interactive:
        parser_autoopen = parser.add_parser("autoopen", help=
            "Enable automatic opening of versions.")
        add_messages("autoopen", parser_autoopen)
        parser_autoopen.set_defaults(func=lambda runner, args: 
            print("TODO: Unimplemented"))

        parser_pause = parser.add_parser("pause", help=
            "Pause automatic opening of versions.")
        add_messages("pause", parser_pause)
        parser_pause.set_defaults(func=lambda runner, args: 
            print("TODO: Unimplemented"))

        parser_exit = parser.add_parser("exit", "quit", help=
            "Exit interactive bisect.")
        add_messages("exit", parser_exit)
        parser_exit.set_defaults(func=lambda runner, args: 
            print("TODO: Unimplemented"))

    parser_good = parser.add_parser("good", help=
        "Mark commits as good.")
    parser_good.add_argument("commits", nargs="*", help=
        "The commits to mark as good.")
    add_messages("good", parser_good)
    parser_good.set_defaults(func=lambda runner, args: 
        print("TODO: Unimplemented"))

    parser_bad = parser.add_parser("bad", help=
        "Mark commits as bad.")
    parser_bad.add_argument("commits", nargs="*", help=
        "The commits to mark as bad.")
    add_messages("bad", parser_bad)
    parser_bad.set_defaults(func=lambda runner, args: 
        print("TODO: Unimplemented"))

    parser_skip = parser.add_parser("skip", help=
        "Mark commits as untestable.")
    parser_skip.add_argument("commits", nargs="*", help=
        "The commits to mark as untestable.")
    add_messages("skip", parser_skip)
    parser_skip.set_defaults(func=lambda runner, args: 
        print("TODO: Unimplemented"))

    parser_unmark = parser.add_parser("unmark", help=
        "Unmark commits.")
    parser_unmark.add_argument("commits", nargs="*", help=
        "The commits to unmark.")
    add_messages("unmark", parser_unmark)
    parser_unmark.set_defaults(func=lambda runner, args: 
        print("TODO: Unimplemented"))

    parser_open = parser.add_parser("open", help=
        "Open a specific version.")
    parser_open.add_argument("version", nargs="?", help=
        "The version to open.")
    add_messages("open", parser_open)
    parser_open.set_defaults(func=lambda runner, args: 
        print("TODO: Unimplemented"))

    parser_list = parser.add_parser("list", help=
        "List all remaining possible commits.")
    parser_list.add_argument("--short", "-s", action="store_true", help=
        "Print only shortened commit SHAs and no log information.")
    add_messages("list", parser_list)
    parser_list.set_defaults(func=lambda runner, args: 
        print("TODO: Unimplemented"))

    parser_status = parser.add_parser("status", help=
        "Print information about the state of the current bisect.")
    add_messages("status", parser_status)
    parser_status.set_defaults(func=lambda runner, args: 
        print("TODO: Unimplemented"))

    parser_help = parser.add_parser("help", help=
        "Print a help message.")
    parser_help.add_argument("command_prefix", nargs="?", help=
        "Optional command to show help for.")
    add_messages("help", parser_help)
    parser_help.set_defaults(func=lambda _, args: 
        commands.help_command(help_messages, args.command_prefix, {'exit': ['quit']}))


def bool_arg_parse(arg: str) -> bool:
    return arg.lower() in ("true", "1", "yes", "")