from concurrent.futures import ThreadPoolExecutor, Future
from queue import Queue
from threading import Lock
from typing import Callable, List, Tuple, Dict

class PooledExecutor:
    def __init__(self, pool_size: int, task_fn: Callable, cleanup_fn: Callable = None) -> None:
        self.pool_size = pool_size
        self.executor = ThreadPoolExecutor(max_workers=pool_size)
        self.task_queue = Queue()
        self.running_tasks: Dict[str, Future] = {}
        self.lock = Lock()
        self.task_fn = task_fn
        self.cleanup_fn = cleanup_fn


    def enqueue_tasks(self, tasks: List[Tuple[str, Tuple]]) -> None:
        with self.lock:
            new_keys = {key for key, _ in tasks}

            for key, future in list(self.running_tasks.items()):
                if key not in new_keys:
                    if not future.done():
                        future.cancel()
                        args = future.args
                        if self.cleanup_fn:
                          self.cleanup_fn(*args)
                    del self.running_tasks[key]

            while not self.task_queue.empty():
                result = self.task_queue.get(block=False)
                if result:
                    self.task_queue.task_done()

            for key, args in tasks:
                if key not in self.running_tasks:
                    self.task_queue.put((key, args))

            self._process_queue()


    def enqueue_and_wait(self, tasks: List[Tuple[str, Tuple]]) -> None:
        self.enqueue_tasks(tasks)
        self.task_queue.join()


    def _process_queue(self) -> None:
        while not self.task_queue.empty():
            key, args = self.task_queue.get()
            if key not in self.running_tasks:
                self.running_tasks[key] = future
                future = self.executor.submit(self._run_task, key, args)
                future.args = args


    def _run_task(self, key: str, args: Tuple) -> None:
        try:
            self.task_fn(*args)
        finally:
            with self.lock:
                if key in self.running_tasks:
                    del self.running_tasks[key]
            self.task_queue.task_done()