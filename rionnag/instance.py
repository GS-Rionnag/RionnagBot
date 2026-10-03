"""OS file lock prevents two bot instances from creating duplicate tickets."""

import os


class SingleInstance:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.file = self.path.open("a+b")
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("RionnagBot is already running.") from exc
        return self

    def __exit__(self, *_):
        self.file.close()
