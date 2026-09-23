"""Drain both pipes and own the complete child process group."""

import asyncio
import codecs
import os
import signal
import sys
import time
from contextlib import ExitStack, suppress
from pathlib import Path

from .security import StreamRedactor


class BoundedArtifact:
    def __init__(self, path: Path, limit: int):
        self.file = path.open("wb")
        try:
            os.chmod(path, 0o600)
        except BaseException:
            self.file.close()
            raise
        self.limit = limit
        self.size = 0
        self.truncated = False

    def write(self, text):
        data = text.encode("utf-8")
        if len(data) > self.limit - self.size:
            self.truncated = True
        data = data[: max(0, self.limit - self.size)]
        self.file.write(data)
        self.file.flush()
        self.size += len(data)

    def close(self):
        self.file.close()


async def _leader_exited(process):
    # Process.wait() also waits for inherited pipes; descendants can keep them open.
    while process.returncode is None:
        await asyncio.sleep(0.025)
    return process.returncode


async def _discard(pipe):
    while await pipe.read(16384):
        pass


async def _signal_group(pid, sig):
    for attempt in range(5):
        try:
            os.killpg(pid, sig)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            # macOS can briefly report EPERM while a terminated group disappears.
            # Persistent denial still propagates; live processes are never assumed dead.
            if attempt == 4:
                raise
            await asyncio.sleep(0.025)


async def stop_group(process, grace=2.0):
    await _signal_group(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(_leader_exited(process), grace)
    except asyncio.TimeoutError:
        pass
    finally:
        # The group can still have live children after its leader exits.
        await _signal_group(process.pid, signal.SIGKILL)
    await _leader_exited(process)


async def supervise(
    command, workspace, environment, timeout, cancel, stdout_path, stderr_path, on_line, limit, secrets=()
):
    with ExitStack() as resources:
        writers = []
        # Resource setup must succeed before anything capable of spending money starts.
        for path in (stdout_path, stderr_path):
            writer = BoundedArtifact(path, limit)
            resources.callback(writer.close)
            writers.append(writer)
        redactors = [StreamRedactor(secrets), StreamRedactor(secrets)]
        start = time.monotonic()
        first_event = None
        timed_out = False
        interrupted = cancel.is_set()
        detected = False
        process = None
        launch = None
        tasks = []

        async def consume(pipe, index):
            nonlocal first_event, detected
            decoder = codecs.getincrementaldecoder("utf-8")("replace")

            async def emit(clean):
                nonlocal first_event, detected
                writers[index].write(clean)
                if redactors[index].detected:
                    detected = True
                    cancel.set()
                for line in clean.split("\n"):
                    if line:
                        if index == 0 and first_event is None:
                            first_event = int((time.monotonic() - start) * 1000)
                        await on_line(line, index == 1)

            while chunk := await pipe.read(16384):
                await emit(redactors[index].feed(decoder.decode(chunk)))
            await emit(redactors[index].feed(decoder.decode(b"", final=True)) + redactors[index].flush())

        async def cleanup():
            nonlocal process
            if process is None and launch is not None:
                # A cancelled spawn await may still have created the watchdog.
                try:
                    process = await launch
                except Exception:
                    pass
            try:
                if process is not None:
                    await stop_group(process, 0.1)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if process is not None:
                    # Cancelled readers can leave a paused pipe transport holding descriptors.
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(asyncio.gather(
                            _discard(process.stdout), _discard(process.stderr)
                        ), 1)

        try:
            if not interrupted:
                launch = asyncio.create_task(asyncio.create_subprocess_exec(
                    sys.executable, "-I", str(Path(__file__).with_name("watchdog.py")),
                    "--parent-pid", str(os.getpid()), "--", *command,
                    cwd=workspace, env=environment, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                ))
                process = await asyncio.shield(launch)
                readers = [asyncio.create_task(consume(process.stdout, 0)),
                           asyncio.create_task(consume(process.stderr, 1))]
                finished = asyncio.create_task(_leader_exited(process))
                cancelled = asyncio.create_task(cancel.wait())
                tasks.extend([finished, cancelled, *readers])
                deadline = start + timeout
                while not finished.done() and not cancelled.done():
                    for reader in readers:
                        if reader.done():
                            reader.result()  # Propagate drain, callback, and artifact-write failures.
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    done, _ = await asyncio.wait(
                        [task for task in tasks if not task.done()],
                        timeout=remaining, return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        timed_out = True
                        break
                interrupted = cancel.is_set()
                await stop_group(process, 2.0 if timed_out or interrupted else 0.1)
                await asyncio.wait_for(asyncio.gather(*readers), 5)
                interrupted |= cancel.is_set()
        finally:
            finishing = asyncio.create_task(cleanup())
            cancelled_during_cleanup = False
            while not finishing.done():
                try:
                    await asyncio.shield(finishing)
                except asyncio.CancelledError:
                    cancelled_during_cleanup = True
            finishing.result()
            if cancelled_during_cleanup:
                raise asyncio.CancelledError
        return {
            "exit_code": process.returncode if process is not None else None,
            "timed_out": timed_out,
            "cancelled": interrupted,
            "secret_detected": detected,
            "agent_duration_ms": int((time.monotonic() - start) * 1000),
            "first_event_ms": first_event,
            "stdout_truncated": writers[0].truncated,
            "stderr_truncated": writers[1].truncated,
        }
