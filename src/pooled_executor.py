from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Callable


class PooledExecutor:
    def __init__(self, pool_size: int, task_fn: Callable) -> None:
        self.executor = ThreadPoolExecutor(max_workers=pool_size)
        self.submitted_tasks: dict[str, Future] = {}
        self.lock = Lock()
        self.task_fn = task_fn


    def enqueue_tasks(self, tasks: list[tuple[str, tuple[str]]]) -> None:
        with self.lock:
            new_keys = {key for key, _ in tasks}

            for key, future in list(self.submitted_tasks.items()):
                if key not in new_keys:
                    if not future.done():
                        future.cancel()
                    del self.submitted_tasks[key]

            for key, args in tasks:
                if key not in self.submitted_tasks:
                    future = self.executor.submit(self._run_task, key, list(args))
                    self.submitted_tasks[key] = future


    def wait_for(self, task_key: str) -> None:
        to_wait_for = None
        with self.lock:
            if task_key in self.submitted_tasks:
                future = self.submitted_tasks[task_key]
                if not future.done():
                    to_wait_for = future

        if to_wait_for is not None:
            to_wait_for.result()

        with self.lock:
            if task_key in self.submitted_tasks:
                del self.submitted_tasks[task_key]


    def _run_task(self, key: str, args: list) -> None:
        try:
            self.task_fn(*args)
        finally:
            with self.lock:
                if key in self.submitted_tasks:
                    del self.submitted_tasks[key]