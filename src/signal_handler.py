import signal
import sys

SHOULD_EXIT = False
SHOULD_PRINT = True
SHOULD_INSTADIE = True
MESSAGE = "Interrupt received, exiting after this step. Send another to exit now."
SECOND_MESSAGE = "Second interrupt received, exiting immediately..."


def _signal_handler(__sig, __frame) -> None:
    global SHOULD_EXIT
    if SHOULD_EXIT or SHOULD_INSTADIE:
        _thread_print(MESSAGE if SHOULD_INSTADIE else SECOND_MESSAGE)
        sys.exit(1)
    SHOULD_EXIT = True
    if SHOULD_PRINT:
        _thread_print(MESSAGE)


def _thread_print(message: str) -> None:
    try:
        print(message)
    except RuntimeError:
        # This may happen if the interrupt is received
        # while in the middle of a print call since 
        # print does not allow re-entrance
        # TODO: Inform the user somehow anyways
        pass


def install() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
