import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

import libs.Utils.CmdExec as MUT
import Utils


def Completed(
    returnCode: int,
    stdout: str = '',
    stderr: str = '',
) -> SimpleNamespace:
    return SimpleNamespace(
        returncode=returnCode,
        stdout=stdout,
        stderr=stderr,
    )


def test_Run_SuccessPassesSubprocessArguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'run')
    MUT.subprocess.run.return_value = Completed(0, 'out', 'err')
    executor = MUT.CmdExec(defTryMax=3, tryWait=7)

    result = executor.Run(
        ['tool', 'arg'],
        shell=False,
        input='payload',
        spPars={'timeout': 9},
    )

    assert result == MUT.CmdRes('out', 'err', 0, True)
    MUT.subprocess.run.assert_called_once_with(
        ['tool', 'arg'],
        input='payload',
        capture_output=True,
        shell=False,
        text=True,
        timeout=9,
    )


def test_Run_NonzeroResultRetriesThenSucceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'run')
    Utils.AutoPatch(monkeypatch, MUT.time, 'sleep')
    MUT.subprocess.run.side_effect = [
        Completed(1, stderr='first failure'),
        Completed(0, stdout='recovered'),
    ]

    result = MUT.CmdExec(defTryMax=2, tryWait=4).Run('command', shell=True)

    assert result.success
    assert result.stdout == 'recovered'
    MUT.time.sleep.assert_called_once_with(4)
    assert MUT.subprocess.run.call_count == 2


def test_Run_FinalNonzeroResultIsReturned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'run')
    Utils.AutoPatch(monkeypatch, MUT.time, 'sleep')
    MUT.subprocess.run.return_value = Completed(5, stderr='failed')

    result = MUT.CmdExec(defTryMax=1).Run(['bad'])

    assert result == MUT.CmdRes('', 'failed', 5, False)
    MUT.time.sleep.assert_not_called()


@pytest.mark.parametrize(
    'exception',
    [FileNotFoundError('missing'), PermissionError('denied')],
)
def test_Run_EnvironmentExceptionsReturnStructuredFailure(
    monkeypatch: pytest.MonkeyPatch,
    exception: OSError,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'run')
    MUT.subprocess.run.side_effect = exception

    result = MUT.CmdExec().Run(['tool'])

    assert result.exitCode == -2
    assert not result.success
    assert result.stderr == str(exception)


def test_Run_SubprocessExceptionRetriesThenReturnsFailure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'run')
    Utils.AutoPatch(monkeypatch, MUT.time, 'sleep')
    MUT.subprocess.run.side_effect = subprocess.SubprocessError('broken pipe')

    result = MUT.CmdExec(defTryMax=2, tryWait=3).Run(['tool'])

    assert result == MUT.CmdRes('', 'broken pipe', -2, False)
    assert MUT.subprocess.run.call_count == 2
    assert MUT.time.sleep.call_args_list == [mock.call(3)]


def test_Run_ZeroAttemptsReturnsMaxTriesFailure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'run')

    result = MUT.CmdExec().Run(['tool'], tryMax=0)

    assert result == MUT.CmdRes('', 'Max. execution tries exceeded.', -3, False)
    MUT.subprocess.run.assert_not_called()
