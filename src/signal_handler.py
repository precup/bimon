import signal
import sys

SHOULD_EXIT = False

def _signal_handler(__sig, __frame) -> None:
    global SHOULD_EXIT
    if SHOULD_EXIT:
        print("Second kill signal received, exiting immediately...")
        sys.exit(0)
    SHOULD_EXIT = True
    print("Kill signal received, exiting after this step finishes. Send a second to exit immediately.")


def install() -> None:
    signal.signal(signal.SIGINT, _signal_handler)