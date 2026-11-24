"""Plan LAN transfers using the same rsync selection and conflict rules as the copy."""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue

from .transfer import CopyCancelled, check_cancel, rsync_copy


BATCH_BYTES = 8 * 1024 * 1024
OCTAL = re.compile(rb"\\#([0-3][0-7]{2})")


def copy_lan(session, push, paths, dest, update, report, cancel):
    roots = {os.fsencode(os.path.basename(p)): os.path.dirname(p) for p in paths}
    files = []
    directories = False
    safe = True

    def read_item(line):
        nonlocal directories, safe
        # An unchanged item's itemization starts with a dot or spaces (-ii).
        # Parse the fixed-width %i field before the variable-length fields.
        item = line[:11]
        parts = line[12:].split(b" ", 2)
        if len(item) != 11 or len(parts) != 3 or not parts[0].isdigit():
            safe = False
            return
        length, permissions, encoded = parts
        if item[1:2] == b"d":
            directories = True
            # A single receiver owns the temporary chmod for read-only dirs.
            if not permissions.startswith(b"rwx"):
                safe = False
        if item[:2] not in (b"<f", b">f"):
            return
        name = OCTAL.sub(lambda m: bytes([int(m[1], 8)]), encoded)
        components = name.split(b"/")
        if components[0] not in roots or any(p in (b"", b".", b"..") for p in components):
            safe = False
            return
        # --relative's /./ cut keeps exactly the originally selected basename.
        source = os.path.join(roots[components[0]], ".", os.fsdecode(name))
        files.append((source, int(length)))

    def quiet(*_):
        pass

    def run(items, callback, stop, **kwargs):
        rsync_copy(session, push, items, dest, update, callback, stop, **kwargs)

    run(paths, quiet, cancel, options=("--dry-run", "-ii", "--out-format=PTO %i %l %B %n"), record=read_item)
    check_cancel(cancel)
    if not safe or len(files) < 2:
        run(paths, report, cancel)
        return

    if directories:
        # Establish real directories once before two receivers write into them.
        run(paths, quiet, cancel, options=("--include=*/", "--exclude=*"))
    batches = Queue()
    files.sort(key=lambda item: item[1], reverse=True)
    limit = min(256, (len(files) + 1) // 2)
    batch, size = [], 0
    for path, length in files:
        if batch and (size + length > BATCH_BYTES or len(batch) >= limit):
            batches.put((batch, size))
            batch, size = [], 0
        batch.append(path)
        size += length
    if batch:
        batches.put((batch, size))

    total = sum(length for _, length in files)
    stop = threading.Event()
    lock = threading.Lock()
    active = [0, 0]
    completed = 0
    moved = 0
    last_count = 0
    started = last_time = time.monotonic()
    speed = "—"

    class Stop:
        def is_set(self):
            return cancel.is_set() or stop.is_set()

        def wait(self, seconds):
            return stop.wait(seconds) or cancel.is_set()

    combined_stop = Stop()

    def emit():
        nonlocal last_time, last_count, speed
        now = time.monotonic()
        count = moved + sum(active)
        if now - last_time >= 0.25:
            speed = f"{(count - last_count) / (now - last_time) / 1048576:.1f} MiB/s"
            last_count, last_time = count, now
        pct = min(99, (completed + sum(active)) * 100 // max(total, 1))
        report(count, pct, speed, "")

    def worker(index):
        nonlocal completed, moved

        def progress(count, percent, rate, name):
            with lock:
                active[index] = count
                emit()
        try:
            while not combined_stop.is_set():
                try:
                    items, planned = batches.get_nowait()
                except Empty:
                    return
                run(items, progress, combined_stop, options=("--relative", "--no-recursive", "--omit-dir-times"))
                with lock:
                    moved += active[index]
                    active[index] = 0
                    completed += planned
                    emit()
            raise CopyCancelled()
        except BaseException:
            stop.set()
            raise

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, index) for index in range(2)]
    for future in futures:
        error = future.exception()
        if error is not None and not isinstance(error, CopyCancelled):
            raise error
    check_cancel(cancel)
    # Reconcile empty dirs, links, skipped/changed entries and final dir mtimes
    # only after both receivers have stopped modifying the destination.
    final_count = 0

    def reconcile_progress(count, percent, rate, name):
        nonlocal final_count
        final_count = count
        if count:
            report(moved + count, 99, rate, name)

    run(paths, reconcile_progress, cancel)
    moved += final_count
    elapsed = max(time.monotonic() - started, 0.000001)
    report(moved, 100, f"{moved / elapsed / 1048576:.1f} MiB/s", "")
