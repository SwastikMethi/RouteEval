"""Workspace audit, private grading, and failure-provenance regression checks."""
import asyncio
import copy
import json
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from routebench import grading, workspace
from routebench.config import ROOT, Settings, tree_hash


@pytest.fixture
def context(tmp_path):
    loaded = Settings(ROOT / 'routebench.example.yaml')
    settings = SimpleNamespace(workspaces=tmp_path / 'workspaces',
                               execution={'grader_timeout_seconds': 10, 'max_artifact_bytes': 1024 * 1024})
    yield loaded.suites['python-core']['cases'], settings
    for path in list(workspace._BASELINES):
        if path.is_relative_to(tmp_path):
            workspace.release(path)


def prepared(case, settings, suffix='attempt'):
    return workspace.prepare(case, settings.workspaces, 'run', suffix)


def custom_case(case, tmp_path, source=None, command=None, partial=False):
    result = copy.deepcopy(case)
    hidden = tmp_path / 'hidden'
    hidden.mkdir(exist_ok=True)
    (hidden / 'test_private.py').write_text(source or 'def test_private():\n    assert True\n')
    result['hidden_graders'] = str(hidden)
    result['hidden_hash'] = tree_hash(hidden)
    result['scoring'] = [{'id': 'private', 'component': 'functional', 'weight': 1.,
                          'mandatory': True, 'partial_credit': partial,
                          'command': command or ['{python}', '-m', 'pytest', '-q', '__hidden__/test_private.py']}]
    return result


def test_canonical_patch_tracks_untracked_ignored_deleted_and_committed_files(context):
    cases, settings = context
    candidate = prepared(cases[0], settings)
    initial = workspace.capture(candidate)
    assert initial['diff'] == '' and initial['files_changed'] == 0
    assert initial['baseline_tree'] == initial['post_tree']
    (candidate / '.gitignore').write_text('ignored.py\n')
    (candidate / 'ignored.py').write_text('VALUE = 3\n')
    (candidate / 'tests/test_invoice.py').unlink()
    subprocess.run(['git', 'add', '-A'], cwd=candidate, check=True, capture_output=True)
    # Agent commits cannot erase the authoritative pre-run comparison.
    subprocess.run(['git', '-c', 'user.name=Agent', '-c', 'user.email=agent@local',
                    '-c', 'commit.gpgsign=false', 'commit', '-m', 'candidate commit'],
                   cwd=candidate, check=True, capture_output=True)
    patch = workspace.capture(candidate)
    assert set(patch['changed_paths']) == {'.gitignore', 'ignored.py', 'tests/test_invoice.py'}
    assert patch['files_changed'] == 3 and patch['lines_added'] >= 2 and patch['lines_removed'] > 0
    assert 'new file mode' in patch['diff'] and 'deleted file mode' in patch['diff']
    assert workspace.MARKER not in patch['diff']
    assert patch['baseline_tree'] == initial['baseline_tree'] != patch['post_tree']
    assert workspace.capture(candidate) == patch
    assert not list(settings.workspaces.glob('.capture-*'))
    workspace.release(candidate)
    with pytest.raises(ValueError, match='No prepared baseline'):
        workspace.capture(candidate)


def test_prepare_ignores_global_hooks_and_candidate_filters(context, tmp_path, monkeypatch):
    cases, settings = context
    home = tmp_path / 'home'
    hooks = tmp_path / 'template/hooks'
    hooks.mkdir(parents=True)
    marker = tmp_path / 'hook-executed'
    hook = hooks / 'post-commit'
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    hook.chmod(0o755)
    home.mkdir()
    (home / '.gitconfig').write_text(f'[init]\n templateDir = {hooks.parent}\n[core]\n hooksPath = {hooks}\n')
    monkeypatch.setenv('HOME', str(home))
    candidate = prepared(cases[0], settings)
    assert not marker.exists()
    with (candidate / '.git/config').open('a') as config:
        config.write(f'[filter "hostile"]\n clean = touch "{marker}"\n required = true\n')
    (candidate / '.gitattributes').write_text('*.py filter=hostile\n')
    (candidate / 'new.py').write_text('hello = 1\n')
    assert 'new.py' in workspace.capture(candidate)['changed_paths']
    assert not marker.exists()


def test_hash_symlink_and_capture_limits(context, tmp_path, monkeypatch):
    cases, settings = context
    changed = {**cases[0], 'fixture_hash': 'changed'}
    with pytest.raises(ValueError, match='hash changed'):
        prepared(changed, settings)
    candidate = prepared(cases[0], settings)
    (candidate / 'escape').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='Symlinks'):
        workspace.capture(candidate)
    (candidate / 'escape').unlink()
    monkeypatch.setattr(workspace, 'MAX_FILE_BYTES', 2)
    with pytest.raises(ValueError, match='limit'):
        workspace.capture(candidate)


@pytest.mark.parametrize('number', range(8))
async def test_all_references_grade_and_hidden_files_never_enter_agent_worktree(context, number):
    cases, settings = context
    case = cases[number]
    candidate = prepared(case, settings)
    subprocess.run(['git', 'apply', case['reference_patch']], cwd=candidate, check=True, capture_output=True)
    patch = workspace.capture(candidate)
    before = workspace.snapshot(candidate)
    result = await grading.grade(case, candidate, patch, settings)
    assert result['score'] == 1. and result['full_pass'], result
    assert not result['infrastructure_error']
    assert workspace.snapshot(candidate) == before
    assert not (candidate / '__hidden__').exists()
    assert not any('__hidden__' in path for path in patch['changed_paths'])
    assert not list(settings.workspaces.glob('.grading-*'))


async def test_candidate_import_errors_are_failed_tests_not_infrastructure(context):
    cases, settings = context
    candidate = prepared(cases[0], settings)
    (candidate / 'invoice/statistics.py').write_text('import package_that_does_not_exist_anywhere\n')
    result = await grading.grade(cases[0], candidate, workspace.capture(candidate), settings)
    assert result['score'] == .05  # Editing only permitted source still earns its independent constraint weight.
    assert all(grader['score'] == 0. for grader in result['graders'] if grader['component'] == 'functional')
    assert not result['full_pass'] and not result['infrastructure_error']
    assert all(not result['infrastructure_error'] for result in result['graders'])


async def test_configuration_and_conftest_cannot_disable_private_tests(context, tmp_path):
    cases, settings = context
    case = custom_case(cases[0], tmp_path, 'def test_private():\n    assert False, "PRIVATE_SOURCE_SENTINEL"\n')
    candidate = prepared(case, settings)
    (candidate / 'conftest.py').write_text('raise RuntimeError("CANDIDATE_CONFTEST_EXECUTED")\n')
    (candidate / 'pytest.py').write_text('raise RuntimeError("CANDIDATE_PYTEST_EXECUTED")\n')
    (candidate / 'pytest.ini').write_text('[pytest]\naddopts = --ignore=__hidden__\n')
    result = await grading.grade(case, candidate, workspace.capture(candidate), settings)
    assert result['score'] == 0. and not result['infrastructure_error']
    output = result['graders'][0]['output']
    assert '1 failed' in output
    assert 'CANDIDATE_CONFTEST_EXECUTED' not in output
    assert 'CANDIDATE_PYTEST_EXECUTED' not in output
    assert 'PRIVATE_SOURCE_SENTINEL' not in output
    assert 'assert False' not in output


async def test_missing_hidden_fixture_and_interpreter_are_infrastructure(context, tmp_path, monkeypatch):
    cases, settings = context
    candidate = prepared(cases[0], settings)
    patch = workspace.capture(candidate)
    result = await grading.grade({**cases[0], 'hidden_graders': str(tmp_path / 'missing')}, candidate, patch, settings)
    assert result['infrastructure_error'] and result['score'] is None
    case = custom_case(cases[0], tmp_path)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'missing-python'))
    result = await grading.grade(case, candidate, patch, settings)
    assert result['infrastructure_error'] and result['score'] is None
    assert not list(settings.workspaces.glob('.grading-*'))


async def test_junit_partial_credit_is_explicit(context, tmp_path):
    cases, settings = context
    case = custom_case(cases[0], tmp_path, 'def test_one():\n    assert True\ndef test_two():\n    assert False\n', partial=True)
    candidate = prepared(case, settings)
    patch = workspace.capture(candidate)
    result = await grading.grade(case, candidate, patch, settings)
    assert result['score'] == .5 and not result['full_pass'] and not result['infrastructure_error']
    case['scoring'][0]['partial_credit'] = False
    assert (await grading.grade(case, candidate, patch, settings))['score'] == 0.


async def test_symlink_and_reserved_overlay_are_candidate_failures(context, tmp_path):
    cases, settings = context
    candidate = prepared(cases[0], settings)
    patch = workspace.capture(candidate)
    (candidate / 'escape').symlink_to(tmp_path)
    result = await grading.grade(cases[0], candidate, patch, settings)
    assert result['score'] == 0 and not result['infrastructure_error']
    (candidate / 'escape').unlink()
    (candidate / '__hidden__').mkdir()
    result = await grading.grade(cases[0], candidate, patch, settings)
    assert result['score'] == 0 and not result['infrastructure_error']
    assert not list(settings.workspaces.glob('.grading-*'))


async def test_output_limits_and_redaction(context, tmp_path):
    cases, settings = context
    settings.execution['max_artifact_bytes'] = 300
    secret = 'sk-proj-' + 'a' * 40
    case = custom_case(cases[0], tmp_path, command=['{python}', '-c', f'print("{secret}"); print("x" * 10000)'])
    candidate = prepared(case, settings)
    result = await grading.grade(case, candidate, workspace.capture(candidate), settings)
    output = result['graders'][0]['output']
    assert result['full_pass']
    assert secret not in output and '[REDACTED' in output and 'truncated' in output
    assert len(output) < 500


def _child_running(pid):
    # ps treats an unreaped zombie as finished; it cannot run or retain descriptors.
    result = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() and not result.stdout.strip().startswith('Z')


@pytest.mark.parametrize('cancel', [False, True])
async def test_grader_timeout_and_cancellation_kill_descendants(context, tmp_path, cancel):
    cases, settings = context
    settings.execution['grader_timeout_seconds'] = 3 if cancel else .3
    pid_file = tmp_path / 'child.pid'
    script = ('import subprocess, sys, time; from pathlib import Path; '
              'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
              f'Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(30)')
    case = custom_case(cases[0], tmp_path, command=['{python}', '-c', script])
    candidate = prepared(case, settings)
    task = asyncio.create_task(grading.grade(case, candidate, workspace.capture(candidate), settings))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(.01)
    assert pid_file.exists()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result['score'] == 0 and not result['infrastructure_error']
        assert 'timed out' in result['graders'][0]['summary']
    assert not _child_running(int(pid_file.read_text()))
    assert not list(settings.workspaces.glob('.grading-*'))


def test_path_and_content_grader_boundaries(tmp_path):
    (tmp_path / 'source').mkdir()
    (tmp_path / 'source/app.py').write_text('VALUE = 42\n')
    patch = {'changed_paths': ['source/app.py', 'source-other/hidden.py'], 'lines_added': 2, 'lines_removed': 1}
    assert not grading._static({'grader': 'allowed_paths', 'paths': ['source/**']}, tmp_path, patch)
    assert not grading._static({'grader': 'forbidden_paths', 'paths': ['source-other/**']}, tmp_path, patch)
    assert grading._static({'grader': 'file_exists', 'paths': ['source/*.py']}, tmp_path, patch)
    assert grading._static({'grader': 'file_absent', 'paths': ['missing.py']}, tmp_path, patch)
    assert grading._static({'grader': 'contains', 'paths': ['source/app.py'], 'pattern': 'VALUE'}, tmp_path, patch)
    assert grading._static({'grader': 'contains', 'paths': ['source/app.py'], 'pattern': r'VALUE = \d+', 'regex': True}, tmp_path, patch)
    assert grading._static({'grader': 'excludes', 'paths': ['source/app.py'], 'pattern': 'SECRET'}, tmp_path, patch)
    assert grading._static({'grader': 'diff_size', 'max_lines': 3}, tmp_path, patch)
    assert not grading._static({'grader': 'diff_size', 'max_lines': 2}, tmp_path, patch)
    for path in ['../outside', '/tmp/outside', r'..\outside']:
        with pytest.raises(ValueError):
            grading._static({'grader': 'file_exists', 'paths': [path]}, tmp_path, patch)


def test_stale_grading_cleanup_removes_only_owned_overlays(tmp_path):
    root = tmp_path / 'workspaces'
    root.mkdir()
    settings = SimpleNamespace(workspaces=root)
    owned = root / '.grading-owned'
    owned.mkdir()
    (owned / workspace.MARKER).write_text('RouteBench grading scratch\n')
    (owned / 'private-grader.py').write_text('private evidence')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / workspace.MARKER).write_text('RouteBench grading scratch\n')
    (outside / 'keep').write_text('external')
    (owned / 'external-link').symlink_to(outside, target_is_directory=True)
    (root / '.grading-linked').symlink_to(outside, target_is_directory=True)
    (root / '.grading-unowned').mkdir()
    wrong = root / '.grading-wrong-marker'
    wrong.mkdir()
    (wrong / workspace.MARKER).write_text('Someone else owns this directory')
    linked_marker = root / '.grading-linked-marker'
    linked_marker.mkdir()
    (linked_marker / workspace.MARKER).symlink_to(outside / workspace.MARKER)
    ordinary = root / 'run-agent'
    ordinary.mkdir()
    (ordinary / workspace.MARKER).write_text('RouteBench grading scratch\n')
    assert grading.cleanup_stale(settings) == 1
    assert not owned.exists()
    assert (outside / 'keep').read_text() == 'external'
    assert all(path.exists() for path in (wrong, linked_marker, ordinary, root / '.grading-unowned', root / '.grading-linked'))
    assert grading.cleanup_stale(settings) == 0


async def test_grading_backend_death_stops_group_and_startup_cleans_overlay(context, tmp_path):
    cases, settings = context
    state = tmp_path / 'grading-state.json'
    source = f'''
def test_wait_for_parent_death():
    import json, os, subprocess, sys, time
    from pathlib import Path
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    state = Path({str(state)!r})
    pending = state.with_suffix('.tmp')
    pending.write_text(json.dumps({{"leader": os.getpid(), "child": child.pid, "group": os.getpgrp()}}))
    pending.replace(state)
    time.sleep(60)
'''
    case = custom_case(cases[0], tmp_path, source)
    candidate = prepared(case, settings)
    patch = workspace.capture(candidate)
    script = '\n'.join([
        'import asyncio',
        'from pathlib import Path',
        'from types import SimpleNamespace',
        'from routebench.grading import grade',
        f'settings = SimpleNamespace(workspaces=Path({str(settings.workspaces)!r}), execution={{"grader_timeout_seconds": 60}})',
        f'asyncio.run(grade({case!r}, Path({str(candidate)!r}), {patch!r}, settings))',
    ])
    backend = subprocess.Popen([sys.executable, '-I', '-c', script], start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not state.exists() and backend.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(.02)
        assert state.exists(), 'Real grader did not reach its ready marker'
        pids = json.loads(state.read_text())
        overlays = list(settings.workspaces.glob('.grading-*'))
        assert len(overlays) == 1
        assert (overlays[0] / 'candidate/__hidden__/test_private.py').is_file()
        assert not (candidate / '__hidden__').exists()
        assert _child_running(pids['leader']) and _child_running(pids['child'])
        backend.kill()
        assert await asyncio.to_thread(backend.wait, timeout=5) == -signal.SIGKILL
        deadline = time.monotonic() + 10
        while any(_child_running(pid) for pid in pids.values()) and time.monotonic() < deadline:
            await asyncio.sleep(.02)
        assert not any(_child_running(pid) for pid in pids.values())
        assert overlays[0].exists()  # SIGKILL cannot run grade()'s finally block.
        assert grading.cleanup_stale(settings) == 1
        assert not overlays[0].exists() and candidate.exists()
    finally:
        if backend.poll() is None:
            backend.kill()
            backend.wait(timeout=5)
        if state.exists():
            pids = json.loads(state.read_text())
            if any(_child_running(pid) for pid in pids.values()):
                from routebench.process import _signal_group
                await _signal_group(pids['group'], signal.SIGKILL)


async def test_grader_watchdog_cannot_be_shadowed_by_candidate_package(tmp_path):
    fake = tmp_path / 'routebench'
    fake.mkdir()
    (fake / '__init__.py').write_text('raise RuntimeError("CANDIDATE_PACKAGE_EXECUTED")\n')
    (fake / 'watchdog.py').write_text('raise RuntimeError("CANDIDATE_WATCHDOG_EXECUTED")\n')
    code, output, timed_out = await grading._run([sys.executable, '-c', 'print("trusted-wrapper")'],
                                                tmp_path, {}, 5, 1000)
    assert code == 0 and output.strip() == 'trusted-wrapper' and not timed_out
