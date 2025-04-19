import signal
import sys

SHOULD_EXIT = False
SHOULD_PRINT = True
SHOULD_INSTADIE = True
MESSAGE = "Interrupt received, exiting after this step. Send another to exit now."

def _signal_handler(__sig, __frame) -> None:
    global SHOULD_EXIT
    if SHOULD_EXIT or SHOULD_INSTADIE:
        _thread_print("Second interrupt received, exiting immediately...")
        sys.exit(1)
    SHOULD_EXIT = True
    if SHOULD_PRINT:
        _thread_print(MESSAGE)


def _thread_print(message: str) -> None:
    try:
        print(message)
    except RuntimeError:
        # This may happen if the interrupt is received
        # while printing since print is not thread-safe
        # TODO: Inform the user somehow anyways
        pass



def install() -> None:
    signal.signal(signal.SIGINT, _signal_handler)