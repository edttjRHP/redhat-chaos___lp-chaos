import os
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

import libs.Utils.CmdExec as MUT
import Utils


@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('text', [False, True])
@pytest.mark.parametrize('exitCode', [0, 3])
def test_Run_NativeTextBinaryStdinAndRetryPreserveRawResults(
    isSecret: bool, text: bool, exitCode: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = 'synthetic-stdin' if (text) else b'synthetic-stdin'
    stderr = 'synthetic-stderr' if (text) else b'synthetic-stderr'
    with caplog.at_level('DEBUG'):
        result = MUT.CmdExec(defTryMax=2, tryWait=0).Run(
            [sys.executable, '-c',
             'import sys; '
             'sys.stdout.buffer.write(sys.stdin.buffer.read()); '
             'sys.stderr.buffer.write(b"synthetic-stderr"); '
             f'sys.exit({exitCode})'],
            input=payload, isSecret=isSecret,
            spPars={'text': text, 'timeout': 5},
        )
    assert result == MUT.CmdRes(payload, stderr, exitCode, exitCode == 0)
    assert type(result.stdout) is type(payload)
    assert type(result.stderr) is type(stderr)
    if (isSecret):
        assert 'synthetic-stdin' not in caplog.text
        assert 'synthetic-stderr' not in caplog.text
        if (exitCode):
            assert caplog.text.count('Command failed with exit code 3') == 2
            assert caplog.text.count(': [SNIPPED]') == 2
            assert 'Try 1/2' in caplog.text and 'Try 2/2' in caplog.text
            assert 'Retrying execution in 0 seconds' in caplog.text
    elif (exitCode):
        assert caplog.text.count('Command failed with exit code') == 2
        assert 'Retrying execution in 0 seconds' in caplog.text


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_NativeSubprocessArgumentConflictIsNotIntercepted(
    isSecret: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(ValueError, match='stdout and stderr'):
        MUT.CmdExec().Run(
            [sys.executable, '-c', 'pass'], isSecret=isSecret,
            spPars={'stdout': subprocess.PIPE},
        )
    assert caplog.text == ''


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_NativeTimeoutPreservesFailureAndSuppressesOnlyLogs(
    isSecret: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    result = MUT.CmdExec().Run(
        [sys.executable, '-c', 'import time; time.sleep(10)'],
        isSecret=isSecret, spPars={'timeout': 0.02},
    )
    assert result.stdout == ''
    assert result.exitCode == -2 and result.success is False
    assert 'timed out after 0.02 seconds' in result.stderr
    if (isSecret):
        assert result.stderr not in caplog.text
        assert 'import time' not in caplog.text
        assert 'Try 1/1' in caplog.text and ': [SNIPPED]' in caplog.text
        assert 'exit code -2' not in caplog.text
    else: assert result.stderr in caplog.text


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


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_SuccessPassesSubprocessArguments(
    monkeypatch: pytest.MonkeyPatch,
    isSecret: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'run')
    MUT.subprocess.run.return_value = Completed(0, 'out', 'err')
    executor = MUT.CmdExec(defTryMax=3, tryWait=7)

    result = executor.Run(
        ['tool', 'arg'], False, 'payload', isSecret, 1, {'timeout': 9},
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

    assert result == MUT.CmdRes(
        '', 'Max. execution tries exceeded.', -3, False,
    )
    MUT.subprocess.run.assert_not_called()


@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('outcome', [
    'success', 'failure', 'missing', 'denied', 'timeout', 'subprocess',
])
def test_Run_SecretPolicyPreservesNativeResultsAndRetries(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    isSecret: bool, outcome: str,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'run')
    Utils.AutoPatch(monkeypatch, MUT.time, 'sleep')
    marker = 'synthetic-sensitive-value'
    errors = {
        'missing': FileNotFoundError(marker),
        'denied': PermissionError(marker),
        'timeout': subprocess.TimeoutExpired([marker], 1, stderr=marker),
        'subprocess': subprocess.SubprocessError(marker),
    }
    if outcome in errors:
        MUT.subprocess.run.side_effect = errors[outcome]
    else:
        MUT.subprocess.run.return_value = Completed(
            0 if outcome == 'success' else 7, marker, marker,
        )
    executor = Utils.RecordingExec()
    executor.defTryMax = 2
    executor.tryWait = 0
    with caplog.at_level('DEBUG'):
        result = executor.Run(
            [marker], input=b'synthetic-stdin', isSecret=isSecret,
            spPars={'text': False, 'timeout': 2, 'shell': True},
        )
    assert result.success == (outcome == 'success')
    assert result.stderr == (
        str(errors[outcome]) if outcome in errors else marker
    )
    if outcome not in errors: assert result.stdout == marker
    attempts = 1 if outcome in ('success', 'missing', 'denied') else 2
    assert MUT.subprocess.run.call_count == attempts
    for call in MUT.subprocess.run.call_args_list:
        assert call.args == ([marker],)
        assert call.kwargs == {
            'input': b'synthetic-stdin', 'capture_output': True,
            'shell': True, 'text': False, 'timeout': 2,
        }
    if isSecret:
        assert marker not in caplog.text
        assert 'synthetic-stdin' not in caplog.text
        if (outcome != 'success'):
            assert f'Try 1/{executor.defTryMax}' in caplog.text
            status = -2 if (outcome in errors) else 7
            assert result.exitCode == status
            assert ': [SNIPPED]' in caplog.text
            if (outcome in errors):
                assert 'exit code -2' not in caplog.text
                assert (
                    'Critical environment exception'
                    if (outcome in ('missing', 'denied'))
                    else 'Subprocess plumbing exception'
                ) in caplog.text
            else: assert f'exit code {status}' in caplog.text
        if (attempts == 2):
            assert 'Try 2/2' in caplog.text
            assert 'Retrying execution in 0 seconds' in caplog.text
        assert executor.inputs == []
    else:
        assert executor.inputs == [b'synthetic-stdin']
        if outcome != 'success': assert marker in caplog.text
    assert MUT.time.sleep.call_args_list == [mock.call(0)] * (attempts - 1)


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_SecretPolicyPreservesNativeUnexpectedException(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    isSecret: bool,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'run')
    error = ValueError('synthetic-native-conflict')
    MUT.subprocess.run.side_effect = error
    with pytest.raises(ValueError) as caught:
        MUT.CmdExec().Run(['native'], isSecret=isSecret)
    assert caught.value is error
    assert caplog.text == ''


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_SecretPolicyPreservesRealBinaryProcessContract(
    isSecret: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    result = MUT.CmdExec().Run(
        [sys.executable, '-c',
         'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); '
         'sys.stderr.buffer.write(b"synthetic-stderr"); sys.exit(3)'],
        input=b'synthetic-stdin', spPars={'text': False, 'timeout': 5},
        isSecret=isSecret,
    )
    assert result == MUT.CmdRes(
        b'synthetic-stdin', b'synthetic-stderr', 3, False,
    )
    if isSecret:
        assert 'synthetic-stdin' not in caplog.text
        assert 'synthetic-stderr' not in caplog.text
        assert 'Command failed with exit code 3' in caplog.text
        assert 'Try 1/1' in caplog.text
        assert ': [SNIPPED]' in caplog.text
    else: assert 'synthetic-stderr' in caplog.text


def test_Run_SecretNativeRetriesLogOnlySafeMetadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = 'synthetic-secret-argument'
    payload = b'synthetic-secret-input'
    executor = Utils.RecordingExec()
    executor.defTryMax = 2
    executor.tryWait = 0
    with caplog.at_level('DEBUG'):
        result = executor.Run(
            [sys.executable, '-c',
             'import os, sys; '
             'sys.stdout.buffer.write(sys.stdin.buffer.read()); '
             'sys.stderr.write(os.environ["SYNTHETIC_SECRET"]); '
             'sys.exit(9)', marker],
            input=payload, isSecret=True,
            spPars={
                'text': False, 'timeout': 5,
                'env': {
                    **os.environ,
                    'SYNTHETIC_SECRET': 'synthetic-secret-environment',
                },
            },
        )
    assert result == MUT.CmdRes(
        payload, b'synthetic-secret-environment', 9, False,
    )
    assert executor.inputs == []
    for value in (marker, payload.decode(), result.stderr.decode()):
        assert value not in caplog.text
    assert caplog.messages == [
        'Command failed with exit code 9 (Try 1/2): [SNIPPED]',
        'Retrying execution in 0 seconds...',
        'Command failed with exit code 9 (Try 2/2): [SNIPPED]',
    ]
    assert [record.levelname for record in caplog.records] == [
        'WARNING', 'INFO', 'WARNING',
    ]
    assert all(record.exc_info is None for record in caplog.records)
