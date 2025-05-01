# This is patching https://github.com/andfoy/pywinpty/blob/main/winpty/ptyprocess.py
# because EOF reading is broken and I don't want to wait for the release with fixes

# The original license is below:
"""
MIT License

Copyright (c) 2017 Spyder IDE

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import os
import socket
import threading
import time

from winpty.winpty import PTY
from winpty import PtyProcess as PtyProcessSource


class PtyProcess(PtyProcessSource):
    def __init__(self, pty):
        assert isinstance(pty, PTY)
        self.pty = pty
        self.pid = pty.pid
        # self.fd = pty.fd

        self.read_blocking = bool(int(os.environ.get('PYWINPTY_BLOCK', 1)))
        self.closed = False
        self.flag_eof = False

        # Used by terminate() to give kernel time to update process status.
        # Time in seconds.
        self.delayafterterminate = 0.1
        # Used by close() to give kernel time to update process status.
        # Time in seconds.
        self.delayafterclose = 0.1

        # Set up our file reader sockets.
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        address = self._server.getsockname()
        self._server.listen(1)

        # Read from the pty in a thread.
        self._thread = threading.Thread(target=_read_in_thread, args=(address, self.pty))
        self._thread.daemon = True
        self._thread.start()

        self.fileobj, _ = self._server.accept()
        self.fd = self.fileobj.fileno()


def _read_in_thread(address, pty):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.connect(address)

        while pty.isalive():
            try:
                data = pty.read(4096, blocking=False)
                if len(data) == 0:
                    time.sleep(0.1)
                else:
                    client.send(bytes(data, 'utf-8'))
            except Exception as e:
                break