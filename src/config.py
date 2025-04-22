import configparser
import os
import sys
from enum import Enum

CONFIG_PATH = "config.ini"
DEFAULT_CONFIG_PATH = "default_config.ini"
DEFAULT_WINDOWS_CONFIG_PATH = "default_windows_config.ini"
DEFAULT_MACOS_CONFIG_PATH = "default_macos_config.ini"


class PrintMode(Enum):
    QUIET = 1
    ERROR_ONLY = 2
    LIVE = 3
    VERBOSE = 4


class Configuration:
    """
    This class reads and provides access to the configuration settings from config.ini.
    If config.ini is not present, it falls back to the platform default.
    """
    # General settings
    RANGE_START: str
    RANGE_END: str
    WORKSPACE_PATH: str
    FORCE: bool = False
    IGNORE_OLD_ERRORS: bool = False
    PATH_SPEC: str = ""

    # Hotkey settings
    ENABLE_HOTKEYS: bool
    MARK_GOOD_HOTKEY: str
    MARK_BAD_HOTKEY: str

    # Compression settings
    COMPRESS_PACK_SIZE: int

    # Compilation settings
    COMPILER_FLAGS: str
    BINARY_NAME: str

    # Execution settings
    DEFAULT_EXECUTION_PARAMETERS: str

    # Output settings
    PRINT_MODE: PrintMode = PrintMode.VERBOSE
    SHOW_TAGS_ON_HISTOGRAM: bool = True
    COLOR_ENABLED: bool
    MESSAGE_COLOR: str
    IMPORTANT_COLOR: str
    COMMIT_COLOR: str
    GOOD_COLOR: str
    ERROR_COLOR: str
    WARNING_COLOR: str
    PROGRESS_FOREGROUND_COLOR: str
    PROGRESS_BACKGROUND_COLOR: str
    HEATMAP_COLORS: list[str]
    
    def load_from(filepath: str = ""):
        config = configparser.ConfigParser()

        if filepath == "":
            default_config_path = DEFAULT_CONFIG_PATH
            if os.name == 'nt':
                default_config_path = DEFAULT_WINDOWS_CONFIG_PATH
            elif sys.platform == 'darwin':
                default_config_path = DEFAULT_MACOS_CONFIG_PATH

            if os.path.exists(CONFIG_PATH):
                filepath = CONFIG_PATH
            elif os.path.exists(default_config_path):
                print(f"config.ini not found. Falling back to {default_config_path}.")
                filepath = default_config_path
            else:
                print(f"Neither config.ini nor {default_config_path} found and no --config provided. Exiting.")
                sys.exit(1)
        elif not os.path.exists(filepath):
            print(f"The file passed to --config could not be opened. Exiting.")
            sys.exit(1)
        config.read(filepath)

        # General settings
        Configuration.RANGE_START = config.get("General", "range_start")
        Configuration.RANGE_END = config.get("General", "range_end")
        Configuration.WORKSPACE_PATH = config.get("General", "workspace_path")
        Configuration.FORCE = Configuration._is_subdirectory(Configuration.WORKSPACE_PATH, os.getcwd())

        # Output settings
        Configuration.SUBWINDOW_ROWS = config.getint("Output", "subwindow_rows")
        Configuration.SHOW_TAGS_ON_HISTOGRAM = config.getboolean("Output", "show_tags_on_histogram")
        Configuration.COLOR_ENABLED = config.getboolean("Output", "color_enabled")
        Configuration.MESSAGE_COLOR = config.get("Output", "message_color")
        Configuration.IMPORTANT_COLOR = config.get("Output", "important_color")
        Configuration.COMMIT_COLOR = config.get("Output", "commit_color")
        Configuration.GOOD_COLOR = config.get("Output", "good_color")
        Configuration.ERROR_COLOR = config.get("Output", "error_color")
        Configuration.WARNING_COLOR = config.get("Output", "warning_color")
        Configuration.PROGRESS_FOREGROUND_COLOR = config.get("Output", "progress_foreground_color")
        Configuration.PROGRESS_BACKGROUND_COLOR = config.get("Output", "progress_background_color")
        Configuration.HEATMAP_COLORS = config.get("Output", "heatmap_colors").split()

        # Hotkey settings
        Configuration.ENABLE_HOTKEYS = config.getboolean("Hotkeys", "enable_hotkeys")
        Configuration.MARK_GOOD_HOTKEY = config.get("Hotkeys", "mark_good")
        Configuration.MARK_BAD_HOTKEY = config.get("Hotkeys", "mark_bad")

        # Compression settings
        Configuration.COMPRESS_PACK_SIZE = config.getint("Compression", "pack_size")

        # Compilation settings
        Configuration.COMPILER_FLAGS = config.get("Compilation", "compiler_flags")
        Configuration.COMPILER_FLAGS += " " + config.get("Compilation", "library_flags")
        Configuration.BINARY_NAME = config.get("Compilation", "binary_name")

        # Execution settings
        Configuration.DEFAULT_EXECUTION_PARAMETERS = config.get("Execution", "execution_parameters")


    def _is_subdirectory(path: str, directory: str) -> bool:
        path = os.path.abspath(path)
        directory = os.path.abspath(directory)
        return os.path.commonpath([path, directory]) == directory