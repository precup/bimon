import signal
import sys

_dying = False
_should_exit = False
_should_print = True
_MESSAGE = "Interrupt received, exiting after this step. Send another to exit now."
_SECOND_MESSAGE = "Second interrupt received, exiting immediately..."


def _signal_handler(__sig, __frame) -> None:
    global _should_exit, _dying
    if _should_exit:
        _dying = True
        _thread_print(_SECOND_MESSAGE)
        sys.exit(1)
    _should_exit = True
    if _should_print:
        _thread_print(_MESSAGE)


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


def get_status() -> str:
    if _dying:
        return _SECOND_MESSAGE
    elif _should_exit:
        return _MESSAGE
    else:
        return ""


def clear() -> None:
    global _should_exit, _dying
    _should_exit = False
    _dying = False


def soft_killed() -> bool:
    return _should_exit


def hard_killed() -> bool:
    return _dying