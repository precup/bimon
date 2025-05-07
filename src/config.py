import configparser
import os
import re
import shlex
import sys

from enum import Enum

_CONFIG_PATH = "config.ini"
_DEFAULT_LINUX_CONFIG_PATH = "default_linux_config.ini"
_DEFAULT_WINDOWS_CONFIG_PATH = "default_windows_config.ini"
_DEFAULT_MACOS_CONFIG_PATH = "default_macos_config.ini"


class PrintMode(Enum):
    QUIET = 1
    ERROR_ONLY = 2
    LIVE = 3
    VERBOSE = 4


class Configuration:
    WORKSPACE_PATH: str = "./workspace"
    FORCE: bool = True

    @staticmethod
    def _get_configuration_path() -> str:
        default_config_path = _DEFAULT_LINUX_CONFIG_PATH
        if os.name == "nt":
            default_config_path = _DEFAULT_WINDOWS_CONFIG_PATH
        elif sys.platform.lower() == "darwin":
            default_config_path = _DEFAULT_MACOS_CONFIG_PATH

        if os.path.exists(_CONFIG_PATH):
            filepath = _CONFIG_PATH
        elif os.path.exists(default_config_path):
            print(f"config.ini not found. Falling back to {default_config_path}.")
            filepath = default_config_path
        else:
            print(f"Neither config.ini nor {default_config_path} found"
                + " and no --config provided. Exiting.")
            return ""
        return filepath

    @staticmethod
    def load_from(filepath: str = ""):
        config = configparser.ConfigParser()

        if filepath == "":
            filepath = Configuration._get_configuration_path()
            if filepath == "":
                sys.exit(1)
        elif not os.path.exists(filepath):
            print("The file passed to --config could not be opened. Exiting.")
            sys.exit(1)
        config.read(filepath)

        # General settings
        Configuration.RANGE_START = config.get(
			"General", "range_start")
        Configuration.RANGE_END = config.get(
			"General", "range_end")
        Configuration.AUTOUPDATE_PROJECT_TITLES = config.getboolean(
            "General", "autoupdate_project_titles")

        # Output settings
        Configuration.SUBWINDOW_ROWS = config.getint(
            "Output", "subwindow_rows")
        Configuration.SHOW_TAGS_ON_HISTOGRAM = config.getboolean(
            "Output", "show_tags_on_histogram")
        Configuration.UNICODE_ENABLED = config.getboolean(
            "Output", "unicode_enabled")
        Configuration.COLOR_ENABLED = config.getboolean(
            "Output", "color_enabled")

        Configuration.MESSAGE_COLOR = config.get(
            "Output", "message_color")
        Configuration.IMPORTANT_COLOR = config.get(
            "Output", "important_color")
        Configuration.REFERENCE_COLOR = config.get(
			"Output", "reference_color")
        Configuration.GOOD_COLOR = config.get(
			"Output", "good_color")
        Configuration.ERROR_COLOR = config.get(
			"Output", "error_color")
        Configuration.WARNING_COLOR = config.get(
			"Output", "warning_color")
        Configuration.PROGRESS_FOREGROUND_COLOR = config.get(
			"Output", "progress_foreground_color")
        Configuration.PROGRESS_BACKGROUND_COLOR = config.get(
			"Output", "progress_background_color")
        Configuration.HEATMAP_COLORS = config.get(
			"Output", "heatmap_colors").split()

        # Compilation settings
        Configuration.COMPILER_FLAGS = config.get(
			"Compilation", "compiler_flags")
        Configuration.COMPILER_FLAGS += " " + config.get(
			"Compilation", "library_flags")

        # Archiving settings
        Configuration.COMPRESSION_ENABLED = config.getboolean(
            "Archiving", "compression_enabled")
        Configuration.EXECUTABLE_PATH = config.get(
			"Archiving", "executable_path")
        Configuration.ARCHIVE_PATHS = shlex.split(config.get(
			"Archiving", "archive_paths"), posix=False)
        Configuration.ARCHIVE_PATHS = [
            path.replace("{EXECUTABLE_PATH}", Configuration.EXECUTABLE_PATH)
            for path in Configuration.ARCHIVE_PATHS
        ]
        Configuration.COPY_ON_CACHE = config.getboolean(
            "Archiving", "copy_on_cache")
        Configuration.BUNDLE_SIZE = config.getint(
            "Archiving", "bundle_size")

        # Execution settings
        Configuration.BACKGROUND_DECOMPRESSION_LAYERS = config.getint(
            "Execution", "background_decompression_layers")
        Configuration.EXTRACTION_POOL_SIZE = config.getint(
            "Execution", "extraction_pool_size")
        Configuration.DEFAULT_EXECUTION_PARAMETERS = config.get(
            "Execution", "default_execution_parameters")
        Configuration.BACKUP_EXECUTABLE_REGEX = re.compile(config.get(
            "Execution", "backup_executable_regex"))
        Configuration.AUTOPURGE_DUPLICATES = config.getboolean(
            "Execution", "autoclean_duplicates")
        Configuration.AUTOPURGE_LIMIT = config.getint(
            "Execution", "autoclean_limit")