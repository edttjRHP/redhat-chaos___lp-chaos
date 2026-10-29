from pathlib import Path
from unittest import mock

import pytest

import libs.Utils.VenvTool as MUT
from libs.Utils.CmdExec import CmdRes
import Utils


def test_ResolvePython_UsesExplicitInterpreter(tmp_path: Path) -> None:
    python = tmp_path / 'custom-python'
    python.write_text('#!/bin/sh\n')
    python.chmod(0o755)

    assert (MUT.ResolvePython(tmp_path / 'tool', python) == python.resolve())


def test_ResolvePython_RejectsMissingExplicitInterpreter(
    tmp_path: Path,
) -> None:
    missing = tmp_path / 'missing-python'
    with pytest.raises(FileNotFoundError, match='does not exist'):
        MUT.ResolvePython(tmp_path, missing)


def test_ResolvePython_FindsUnixVenvInterpreter(tmp_path: Path) -> None:
    python = tmp_path / '.venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\n')
    python.chmod(0o755)

    assert (MUT.ResolvePython(tmp_path) == python.resolve())


def test_ResolvePython_FindsWindowsVenvInterpreter(tmp_path: Path) -> None:
    python = tmp_path / '.venv' / 'Scripts' / 'python.exe'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\n')
    python.chmod(0o755)

    assert (MUT.ResolvePython(tmp_path) == python.resolve())


def test_ResolvePython_RejectsMissingVenv(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match='No tool venv interpreter'):
        MUT.ResolvePython(tmp_path)


def test_RunInVenv_MergesEnvAndRaisesOnFailure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    python = tmp_path / '.venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\n')
    python.chmod(0o755)
    executor = mock.MagicMock()
    executor.Run.return_value = CmdRes('out', 'err', 7, False)
    monkeypatch.setenv('KEEP', '1')
    monkeypatch.setenv('DROP', 'x')

    with pytest.raises(RuntimeError, match='exit 7'):
        MUT.RunInVenv(
            tmp_path,
            ['-c', 'pass'],
            envVars={'NEW': '2', 'DROP': None},
            cwd=tmp_path,
            cmdExec=executor,
            warnOnFail=False,
        )

    command = executor.Run.call_args.args[0]
    assert (command == [str(python.resolve()), '-c', 'pass'])
    assert (executor.Run.call_args.kwargs['warnOnFail'] is False)
    env = executor.Run.call_args.kwargs['spPars']['env']
    assert (env['KEEP'] == '1')
    assert (env['NEW'] == '2')
    assert ('DROP' not in env)
    assert (executor.Run.call_args.kwargs['spPars']['cwd'] == str(tmp_path))


def test_RunInVenv_ReturnsSuccessfulResult(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    python = tmp_path / '.venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\n')
    python.chmod(0o755)
    expected = CmdRes('ok', '', 0, True)
    executor = mock.MagicMock()
    executor.Run.return_value = expected
    Utils.AutoPatch(monkeypatch, MUT, 'ResolvePython')
    MUT.ResolvePython.return_value = python.resolve()

    result = MUT.RunInVenv(
        tmp_path, ['script.py', '--flag'], cmdExec=executor,
    )

    assert (result is expected)
    assert (executor.Run.call_args.args[0] == [
        str(python.resolve()), 'script.py', '--flag',
    ])
    MUT.ResolvePython.assert_called_once_with(tmp_path, None)
