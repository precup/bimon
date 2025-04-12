import configparser
import os

CONFIG_PATH = "config.ini"
DEFAULT_CONFIG_PATH = "default_config.ini"

class Configuration:
    """
    This class reads and provides access to the configuration settings from config.ini.
    If config.ini is not present, it falls back to default_config.ini.
    """
    config = configparser.ConfigParser()

    config_path = os.path.join(os.path.dirname(__file__), "config.ini")
    default_config_path = os.path.join(os.path.dirname(__file__), "default_config.ini")
    if os.path.exists(CONFIG_PATH):
        config.read(CONFIG_PATH)
    elif os.path.exists(DEFAULT_CONFIG_PATH):
        print("config.ini not found. Falling back to default_config.ini.")
        config.read(DEFAULT_CONFIG_PATH)

    # General settings
    START_COMMIT = config.get("General", "start_commit")
    WORKSPACE_PATH = config.get("General", "workspace_path")

    # Hotkey settings
    ENABLE_HOTKEYS = config.getboolean("Hotkeys", "enable_hotkeys")
    MARK_GOOD_HOTKEY = config.get("Hotkeys", "mark_good")
    MARK_BAD_HOTKEY = config.get("Hotkeys", "mark_bad")

    # Compression settings
    COMPRESS_PACK_SIZE = config.getint("Compression", "pack_size")

    # Compilation settings
    COMPILER_FLAGS = config.get("Compilation", "compiler_flags")
    COMPILER_FLAGS += " " + config.get("Compilation", "library_flags")
    BINARY_NAME = config.get("Compilation", "binary_name")