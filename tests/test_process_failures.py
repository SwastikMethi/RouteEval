"""Exercise supervisor failure paths with local Python processes, never model CLIs."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from routebench import process, watchdog


async def ignore_line(line, stderr):
    pass


def child_script(state, *, exit_leader=False):
    return '\n'.join([
        'import json, os, subprocess, sys, time',
        'from pathlib import Path',
        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])',
        f'Path({str(state)!r}).write_text(json.dumps({{"leader": os.getpid(), "child": child.pid, "group": os.getpgrp()}}))',
        'print("ready", flush=True)',
        'sys.exit(0)' if exit_leader else 'time.sleep(60)',
    ])


async def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail('Process did not reach the expected state before the guard deadline')
        await asyncio.sleep(.02)


def running(pid):
    status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True)
    return status.returncode == 0 and bool(status.stdout.strip()) and not status.stdout.strip().startswith('Z')


async def assert_stopped(state):
    pids = json.loads(state.read_text())
    await wait_until(lambda: not any(running(pid) for pid in pids.values()))


def invoke(tmp_path, script, callback=ignore_line, cancel=None):
    return process.supervise([sys.executable, '-c', script], tmp_path, {}, 20,
                             cancel or asyncio.Event(), tmp_path / 'stdout', tmp_path / 'stderr',
                             callback, 100_000)


async def test_artifact_open_failure_never_launches_and_closes_prior_writer(tmp_path, monkeypatch):
    original = process.BoundedArtifact
    opened = []
    launches = []

    def writer(path, limit):
        if opened:
            raise OSError('second artifact cannot open')
        result = original(path, limit)
        opened.append(result)
        return result

    async def launch(*args, **kwargs):
        launches.append(args)
        raise AssertionError('CLI must never launch after artifact setup fails')

    monkeypatch.setattr(process, 'BoundedArtifact', writer)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    with pytest.raises(OSError, match='second artifact'):
        await invoke(tmp_path, 'raise AssertionError("must not execute")')
    assert not launches and opened[0].file.closed


def test_artifact_permission_failure_closes_open_file(tmp_path, monkeypatch):
    opened = []
    real_open = Path.open

    def track(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        opened.append(stream)
        return stream

    def fail(*args):
        raise PermissionError('cannot restrict artifact mode')

    monkeypatch.setattr(Path, 'open', track)
    monkeypatch.setattr(os, 'chmod', fail)
    with pytest.raises(PermissionError):
        process.BoundedArtifact(tmp_path / 'artifact', 100)
    assert opened and all(stream.closed for stream in opened)


async def test_spawn_failure_closes_both_artifacts(tmp_path, monkeypatch):
    original = process.BoundedArtifact
    opened = []

    def writer(path, limit):
        item = original(path, limit)
        opened.append(item)
        return item

    async def fail(*args, **kwargs):
        assert Path(args[2]).is_absolute() and Path(args[2]).name == 'watchdog.py'
        assert args[3] == '--parent-pid'
        raise OSError('spawn failed')

    monkeypatch.setattr(process, 'BoundedArtifact', writer)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fail)
    with pytest.raises(OSError, match='spawn failed'):
        await invoke(tmp_path, 'pass')
    assert len(opened) == 2 and all(item.file.closed for item in opened)


async def test_pre_cancelled_attempt_does_not_launch(tmp_path, monkeypatch):
    cancel = asyncio.Event()
    cancel.set()

    async def forbidden(*args, **kwargs):
        raise AssertionError('A cancelled attempt must not start a CLI')

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    result = await invoke(tmp_path, 'pass', cancel=cancel)
    assert result['cancelled'] and result['exit_code'] is None


@pytest.mark.parametrize('failure', ['callback', 'artifact_write', 'cancel'])
async def test_reader_failures_and_outer_cancellation_stop_the_whole_group(tmp_path, monkeypatch, failure):
    state = tmp_path / 'state.json'
    original_write = process.BoundedArtifact.write

    async def callback(line, stderr):
        if failure == 'callback' and line == 'ready':
            raise RuntimeError('callback failed')

    if failure == 'artifact_write':
        def fail_write(self, text):
            if 'ready' in text:
                raise OSError('artifact disk full')
            original_write(self, text)
        monkeypatch.setattr(process.BoundedArtifact, 'write', fail_write)
    task = asyncio.create_task(invoke(tmp_path, child_script(state), callback))
    await wait_until(state.exists)
    if failure == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises((RuntimeError, OSError), match='callback failed|artifact disk full'):
            await task
    await assert_stopped(state)


async def test_cancellation_during_spawn_still_owns_the_created_process(tmp_path, monkeypatch):
    original = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    release = asyncio.Event()
    state = tmp_path / 'state.json'

    async def delayed_spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        spawned.set()
        await release.wait()
        return child

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed_spawn)
    task = asyncio.create_task(invoke(tmp_path, child_script(state)))
    await spawned.wait()
    await wait_until(state.exists)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await assert_stopped(state)


async def test_leader_exit_cleans_children_holding_output_pipes(tmp_path):
    state = tmp_path / 'state.json'
    result = await asyncio.wait_for(invoke(tmp_path, child_script(state, exit_leader=True)), 10)
    assert result['exit_code'] == 0 and not result['timed_out'] and not result['cancelled']
    await assert_stopped(state)


async def test_repeated_cancellation_cannot_interrupt_group_cleanup(tmp_path, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    original = process.stop_group
    state = tmp_path / 'state.json'

    async def held_cleanup(child, grace=2.):
        entered.set()
        await release.wait()
        await original(child, grace)

    monkeypatch.setattr(process, 'stop_group', held_cleanup)
    task = asyncio.create_task(invoke(tmp_path, child_script(state)))
    await wait_until(state.exists)
    task.cancel()
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await assert_stopped(state)


async def test_watchdog_terminates_group_after_real_backend_process_is_killed(tmp_path):
    state = tmp_path / 'state.json'
    script = child_script(state)
    backend_script = '\n'.join([
        'import asyncio, sys',
        'from pathlib import Path',
        'from routebench.process import supervise',
        'async def line(value, stderr): pass',
        f'asyncio.run(supervise([sys.executable, "-c", {script!r}], Path({str(tmp_path)!r}), {{}}, 60, asyncio.Event(), Path({str(tmp_path / "backend.out")!r}), Path({str(tmp_path / "backend.err")!r}), line, 100000))',
    ])
    backend = subprocess.Popen([sys.executable, '-I', '-c', backend_script], start_new_session=True)
    try:
        await wait_until(state.exists)
        pids = json.loads(state.read_text())
        assert running(pids['leader']) and running(pids['child'])
        backend.kill()
        assert await asyncio.to_thread(backend.wait, timeout=5) == -signal.SIGKILL
        await assert_stopped(state)
    finally:
        if backend.poll() is None:
            backend.kill()
            backend.wait(timeout=5)
        if state.exists():
            try:
                os.killpg(json.loads(state.read_text())['group'], signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_watchdog_does_not_spawn_if_expected_parent_already_died(monkeypatch):
    monkeypatch.setattr(os, 'getppid', lambda: 1)

    def forbidden(*args, **kwargs):
        raise AssertionError('Orphaned watchdog must not start the paid command')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert watchdog.main(['--parent-pid', '12345', '--', 'paid-cli']) == 125


def test_watchdog_accepts_old_supervisors_without_parent_flag(monkeypatch):
    calls = []

    class Completed:
        def poll(self):
            return 0

    def fake_spawn(command, **kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(os, 'getppid', lambda: 12345)
    monkeypatch.setattr(subprocess, 'Popen', fake_spawn)
    assert watchdog.main(['legacy-cli', '--flag']) == 0
    assert calls == [['legacy-cli', '--flag']]


async def test_signal_exit_is_an_error_and_internal_parent_env_is_not_forwarded(tmp_path):
    result = await invoke(tmp_path, 'import os, signal; os.kill(os.getpid(), signal.SIGTERM)')
    assert result['exit_code'] == 128 + signal.SIGTERM
    assert not result['timed_out'] and not result['cancelled']
    await process.supervise([sys.executable, '-c', 'import os; print(os.environ.get("ROUTEBENCH_PARENT_PID", "absent"))'],
                            tmp_path, {'ROUTEBENCH_PARENT_PID': 'internal'}, 10, asyncio.Event(),
                            tmp_path / 'env.out', tmp_path / 'env.err', ignore_line, 1000)
    assert (tmp_path / 'env.out').read_text().strip() == 'absent'


@pytest.mark.parametrize('disappears', [True, False])
async def test_group_permission_race_retries_but_real_denial_remains_an_error(monkeypatch, disappears):
    attempts = []

    def signal_group(pid, sig):
        attempts.append((pid, sig))
        if disappears and len(attempts) > 1:
            raise ProcessLookupError('group disappeared')
        raise PermissionError('group cannot be signalled')

    monkeypatch.setattr(os, 'killpg', signal_group)
    if disappears:
        await process._signal_group(12345, signal.SIGKILL)
    else:
        with pytest.raises(PermissionError, match='cannot be signalled'):
            await process._signal_group(12345, signal.SIGKILL)
    assert len(attempts) > 1
