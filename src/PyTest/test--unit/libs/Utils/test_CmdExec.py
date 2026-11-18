import inspect
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

import libs.Utils.CmdExec as MUT


def Completed(
    returnCode: int,
    stdout: str | bytes = '',
    stderr: str | bytes = '',
) -> SimpleNamespace:
    return SimpleNamespace(returncode=returnCode, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize('output', ['text', b'bytes', None])
def test_Run_UncapturedStderrRetries_ReviewRegression(
    monkeypatch: pytest.MonkeyPatch,
    output: str | bytes | None,
) -> None:
    results = [
        subprocess.CompletedProcess(['synthetic'], code, output, None)
        for code in (7, 0)
    ]
    run = mock.Mock(side_effect=results)
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))
    spPars = {'capture_output': False, 'text': not isinstance(output, bytes)}
    if (output is not None): spPars['stdout'] = subprocess.PIPE
    result = MUT.CmdExec().Run(['synthetic'], tryMax=2, spPars=spPars)
    assert result.success
    assert (result.stdout is output)
    assert (result.stderr is None)
    assert (run.call_count == 2)
    expectedCall = mock.call(['synthetic'], input=None, shell=False, **spPars)
    assert (run.call_args_list == ([expectedCall] * 2))
    sleep.assert_called_once_with(5)


@pytest.mark.parametrize('warnOnFail', [None, True, False])
@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('exitCodes', [(0,), (7,), (7, 0), (7, 9)])
def test_Run_WarnOnFailChangesOnlyNonzeroWarnings(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    warnOnFail: bool | None,
    isSecret: bool,
    exitCodes: tuple[int, ...],
) -> None:
    run = mock.Mock(side_effect=[
        Completed(code, 'synthetic-output', 'synthetic-error')
        for code in exitCodes
    ])
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))
    options = {} if (warnOnFail is None) else {'warnOnFail': warnOnFail}
    with caplog.at_level('INFO'):
        result = MUT.CmdExec(defTryMax=4, tryWait=3).Run(
            ['synthetic-command'], False, 'synthetic-input', isSecret,
            **options, tryMax=len(exitCodes), spPars={'timeout': 2},
        )

    assert (result == MUT.CmdRes(
        'synthetic-output', 'synthetic-error', exitCodes[-1],
        (exitCodes[-1] == 0),
    ))
    expectedCall = mock.call(
        ['synthetic-command'], input='synthetic-input', capture_output=True,
        shell=False, text=True, timeout=2,
    )
    assert (run.call_args_list == ([expectedCall] * len(exitCodes)))
    assert (sleep.call_args_list == ([mock.call(3)] * (len(exitCodes) - 1)))
    expected = []
    for (attempt, code) in enumerate(exitCodes, 1):
        if (code and (warnOnFail is not False)):
            detail = '[SNIPPED]' if isSecret else 'synthetic-error'
            expected.append((
                'WARNING', f'Command failed with exit code {code} '
                f'(Try {attempt}/{len(exitCodes)}): {detail}',
            ))
        if (attempt < len(exitCodes)):
            expected.append(('INFO', 'Retrying execution in 3 seconds...'))
    assert ([
        (entry.levelname, entry.message) for entry in caplog.records
    ] == expected)
    assert ('synthetic-input' not in caplog.text)
    assert ('synthetic-command' not in caplog.text)
    if isSecret: assert ('synthetic-error' not in caplog.text)


@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('exception', [
    FileNotFoundError('synthetic-missing'),
    PermissionError('synthetic-denied'),
    subprocess.SubprocessError('synthetic-plumbing'),
])
def test_Run_WarnOnFailPreservesExceptionLoggingAndRetries(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    isSecret: bool,
    exception: Exception,
) -> None:
    run = mock.Mock(side_effect=exception)
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))
    with caplog.at_level('INFO'):
        result = MUT.CmdExec(defTryMax=2, tryWait=4).Run(
            ['synthetic-command'], isSecret=isSecret, warnOnFail=False,
        )
    plumbing = isinstance(exception, subprocess.SubprocessError)
    attempts = 2 if plumbing else 1
    assert (result == MUT.CmdRes('', str(exception), -2, False))
    assert (run.call_count == attempts)
    assert (sleep.call_args_list == ([mock.call(4)] if plumbing else []))
    kind = (
        'Subprocess plumbing exception' if plumbing
        else 'Critical environment exception'
    )
    level = 'WARNING' if plumbing else 'ERROR'
    detail = '[SNIPPED]' if isSecret else str(exception)
    expected = [(level, f'{kind} (Try 1/2): {detail}')]
    if plumbing:
        expected.extend([
            ('INFO', 'Retrying execution in 4 seconds...'),
            (level, f'{kind} (Try 2/2): {detail}'),
        ])
    assert ([
        (entry.levelname, entry.message) for entry in caplog.records
    ] == expected)
    if isSecret: assert (str(exception) not in caplog.text)


@pytest.mark.parametrize('warnOnFail', [True, False])
@pytest.mark.parametrize('isSecret', [True, False])
@pytest.mark.parametrize('finalCode', [0, 9])
def test_Run_PositionalWarnOnFailPreservesSubprocessOptions(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    warnOnFail: bool,
    isSecret: bool,
    finalCode: int,
) -> None:
    run = mock.Mock(side_effect=[
        Completed(7, 'first-output', 'first-error'),
        Completed(finalCode, 'last-output', 'last-error'),
    ])
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))
    with caplog.at_level('INFO'):
        result = MUT.CmdExec(defTryMax=4, tryWait=3).Run(
            ['synthetic'], True, 'payload', isSecret, warnOnFail, 2,
            {'timeout': 9, 'text': False},
        )

    assert (result == MUT.CmdRes(
        'last-output', 'last-error', finalCode, (finalCode == 0),
    ))
    expectedCall = mock.call(
        ['synthetic'], input='payload', capture_output=True, shell=True,
        text=False, timeout=9,
    )
    assert (run.call_args_list == ([expectedCall] * 2))
    sleep.assert_called_once_with(3)
    warnings = [
        entry.message for entry in caplog.records
        if (entry.levelname == 'WARNING')
    ]
    expected = []
    if warnOnFail:
        detail = '[SNIPPED]' if isSecret else 'first-error'
        expected.append(f'Command failed with exit code 7 (Try 1/2): {detail}')
        if finalCode:
            detail = '[SNIPPED]' if isSecret else 'last-error'
            expected.append(
                f'Command failed with exit code 9 (Try 2/2): {detail}',
            )
    assert (warnings == expected)
    assert ('Retrying execution in 3 seconds...' in caplog.messages)
    if isSecret:
        assert ('first-error' not in caplog.text)
        assert ('last-error' not in caplog.text)


def test_Run_ParameterOrderSupportsNormalPositionalBinding() -> None:
    parameters = inspect.signature(MUT.CmdExec.Run).parameters
    assert (list(parameters) == [
        'self', 'command', 'shell', 'input', 'isSecret', 'warnOnFail',
        'tryMax', 'spPars',
    ])
    assert all(
        (parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for parameter in parameters.values()
    )
    assert (parameters['warnOnFail'].default is True)


@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('text', [False, True])
@pytest.mark.parametrize('exitCode', [0, 3])
def test_Run_TextBinaryInputAndRetryPreserveRawResults(
    monkeypatch: pytest.MonkeyPatch,
    isSecret: bool,
    text: bool,
    exitCode: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = 'synthetic-stdin' if text else b'synthetic-stdin'
    stderr = 'synthetic-stderr' if text else b'synthetic-stderr'
    run = mock.Mock(side_effect=[
        Completed(exitCode, payload, stderr) for _ in range(2)
    ])
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))
    with caplog.at_level('DEBUG'):
        result = MUT.CmdExec(defTryMax=2, tryWait=0).Run(
            ['synthetic-command'], input=payload, isSecret=isSecret,
            spPars={'text': text, 'timeout': 5},
        )
    assert (result == MUT.CmdRes(payload, stderr, exitCode, (exitCode == 0)))
    assert (type(result.stdout) is type(payload))
    assert (type(result.stderr) is type(stderr))
    attempts = 1 if (exitCode == 0) else 2
    expectedCall = mock.call(
        ['synthetic-command'], input=payload, capture_output=True,
        shell=False, text=text, timeout=5,
    )
    assert (run.call_args_list == ([expectedCall] * attempts))
    assert (sleep.call_args_list == ([mock.call(0)] * (attempts - 1)))
    if isSecret:
        assert ('synthetic-stdin' not in caplog.text)
        assert ('synthetic-stderr' not in caplog.text)
    if exitCode:
        assert (caplog.text.count('Command failed with exit code 3') == 2)
        assert (('Try 1/2' in caplog.text) and ('Try 2/2' in caplog.text))
        assert ('Retrying execution in 0 seconds...' in caplog.text)
        if isSecret: assert (caplog.text.count(': [SNIPPED]') == 2)
        else: assert ('synthetic-stderr' in caplog.text)


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_SubprocessArgumentConflictIsNotIntercepted(
    monkeypatch: pytest.MonkeyPatch,
    isSecret: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run = mock.Mock(side_effect=ValueError('stdout and stderr'))
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    with pytest.raises(ValueError, match='stdout and stderr'):
        MUT.CmdExec().Run(
            ['synthetic-command'], isSecret=isSecret,
            spPars={'stdout': subprocess.PIPE},
        )
    run.assert_called_once_with(
        ['synthetic-command'], input=None, capture_output=True,
        shell=False, text=True, stdout=subprocess.PIPE,
    )
    assert (caplog.text == '')


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_TimeoutReturnsStructuredFailureAndSuppressesOnlyLogs(
    monkeypatch: pytest.MonkeyPatch,
    isSecret: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    timeout = 0.02
    error = subprocess.TimeoutExpired(['synthetic-command'], timeout)
    run = mock.Mock(side_effect=error)
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    with caplog.at_level('DEBUG'):
        result = MUT.CmdExec().Run(
            ['synthetic-command'], isSecret=isSecret,
            spPars={'timeout': timeout},
        )
    assert (result.stdout == '')
    assert ((result.exitStat == -2) and (result.success is False))
    assert (result.stderr == str(error))
    run.assert_called_once_with(
        ['synthetic-command'], input=None, capture_output=True,
        shell=False, text=True, timeout=timeout,
    )
    if isSecret:
        assert (result.stderr not in caplog.text)
        assert ('synthetic-command' not in caplog.text)
        assert ('Try 1/1' in caplog.text)
        assert (': [SNIPPED]' in caplog.text)
        assert ('exit code -2' not in caplog.text)
    else: assert (result.stderr in caplog.text)


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_SuccessPassesSubprocessArguments(
    monkeypatch: pytest.MonkeyPatch,
    isSecret: bool,
) -> None:
    run = mock.Mock(return_value=Completed(0, 'out', 'err'))
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    executor = MUT.CmdExec(defTryMax=3, tryWait=7)

    result = executor.Run(
        ['tool', 'arg'], False, 'payload', isSecret, True, 1, {'timeout': 9},
    )

    assert (result == MUT.CmdRes('out', 'err', 0, True))
    run.assert_called_once_with(
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
    run = mock.Mock(side_effect=[
        Completed(1, stderr='first failure'),
        Completed(0, stdout='recovered'),
    ])
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))

    result = MUT.CmdExec(defTryMax=2, tryWait=4).Run('command', shell=True)

    assert result.success
    assert (result.stdout == 'recovered')
    sleep.assert_called_once_with(4)
    assert (run.call_count == 2)
    expectedCall = mock.call(
        'command', input=None, capture_output=True, shell=True, text=True,
    )
    assert (run.call_args_list == ([expectedCall] * 2))


def test_Run_FinalNonzeroResultIsReturned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = mock.Mock(return_value=Completed(5, stderr='failed'))
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))

    result = MUT.CmdExec(defTryMax=1).Run(['bad'])

    assert (result == MUT.CmdRes('', 'failed', 5, False))
    sleep.assert_not_called()
    run.assert_called_once_with(
        ['bad'], input=None, capture_output=True, shell=False, text=True,
    )


@pytest.mark.parametrize('exception', [
    FileNotFoundError('missing'), PermissionError('denied'),
])
def test_Run_EnvironmentExceptionsReturnStructuredFailure(
    monkeypatch: pytest.MonkeyPatch,
    exception: OSError,
) -> None:
    run = mock.Mock(side_effect=exception)
    monkeypatch.setattr(MUT.subprocess, 'run', run)

    result = MUT.CmdExec().Run(['tool'])

    assert (result.exitStat == -2)
    assert not result.success
    assert (result.stderr == str(exception))
    run.assert_called_once_with(
        ['tool'], input=None, capture_output=True, shell=False, text=True,
    )


def test_Run_SubprocessExceptionRetriesThenReturnsFailure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = mock.Mock(side_effect=subprocess.SubprocessError('broken pipe'))
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))

    result = MUT.CmdExec(defTryMax=2, tryWait=3).Run(['tool'])

    assert (result == MUT.CmdRes('', 'broken pipe', -2, False))
    assert (run.call_count == 2)
    assert (sleep.call_args_list == [mock.call(3)])


def test_Run_ZeroAttemptsReturnsMaxTriesFailure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)

    result = MUT.CmdExec().Run(['tool'], tryMax=0)

    assert (result == MUT.CmdRes(
        '', 'Max. execution tries exceeded.', -3, False,
    ))
    run.assert_not_called()


@pytest.mark.parametrize('isSecret', [False, True])
@pytest.mark.parametrize('outcome', [
    'success', 'failure', 'missing', 'denied', 'timeout', 'subprocess',
])
def test_Run_SecretPolicyPreservesResultsAndRetries(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    isSecret: bool,
    outcome: str,
) -> None:
    marker = 'synthetic-sensitive-value'
    payload = b'synthetic-stdin'
    output = marker.encode()
    errors = {
        'missing': FileNotFoundError(marker),
        'denied': PermissionError(marker),
        'timeout': subprocess.TimeoutExpired([marker], 1, stderr=marker),
        'subprocess': subprocess.SubprocessError(marker),
    }
    run = mock.Mock()
    if (outcome in errors): run.side_effect = errors[outcome]
    else:
        run.return_value = Completed(
            0 if (outcome == 'success') else 7, output, output,
        )
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))
    executor = MUT.CmdExec(defTryMax=2, tryWait=0)
    with caplog.at_level('DEBUG'):
        result = executor.Run(
            [marker], input=payload, isSecret=isSecret,
            spPars={'text': False, 'timeout': 2, 'shell': True},
        )
    assert (result.success == (outcome == 'success'))
    expectedError = str(errors[outcome]) if (outcome in errors) else output
    assert (result.stderr == expectedError)
    if (outcome not in errors): assert (result.stdout == output)
    attempts = 1 if (outcome in ('success', 'missing', 'denied')) else 2
    expectedCall = mock.call(
        [marker], input=payload, capture_output=True, shell=True,
        text=False, timeout=2,
    )
    assert (run.call_args_list == ([expectedCall] * attempts))
    assert (sleep.call_args_list == ([mock.call(0)] * (attempts - 1)))
    assert ('synthetic-stdin' not in caplog.text)
    if isSecret:
        assert (marker not in caplog.text)
        if (outcome != 'success'):
            assert (f'Try 1/{executor.defTryMax}' in caplog.text)
            status = -2 if (outcome in errors) else 7
            assert (result.exitStat == status)
            assert (': [SNIPPED]' in caplog.text)
            if (outcome in errors):
                assert ('exit code -2' not in caplog.text)
                expectedKind = (
                    'Critical environment exception'
                    if (outcome in ('missing', 'denied'))
                    else 'Subprocess plumbing exception'
                )
                assert (expectedKind in caplog.text)
            else: assert (f'exit code {status}' in caplog.text)
        if (attempts == 2):
            assert ('Try 2/2' in caplog.text)
            assert ('Retrying execution in 0 seconds' in caplog.text)
    elif (outcome != 'success'): assert (marker in caplog.text)


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_SecretPolicyPreservesUnexpectedException(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    isSecret: bool,
) -> None:
    run = mock.Mock()
    error = ValueError('synthetic-native-conflict')
    run.side_effect = error
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    with pytest.raises(ValueError) as caught:
        MUT.CmdExec().Run(['synthetic-command'], isSecret=isSecret)
    assert (caught.value is error)
    run.assert_called_once_with(
        ['synthetic-command'], input=None, capture_output=True,
        shell=False, text=True,
    )
    assert (caplog.text == '')


@pytest.mark.parametrize('isSecret', [False, True])
def test_Run_SecretPolicyPreservesBinarySubprocessResults(
    monkeypatch: pytest.MonkeyPatch,
    isSecret: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = b'synthetic-stdin'
    stderr = b'synthetic-stderr'
    run = mock.Mock(return_value=Completed(3, payload, stderr))
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    with caplog.at_level('DEBUG'):
        result = MUT.CmdExec().Run(
            ['synthetic-command'], input=payload, isSecret=isSecret,
            spPars={'text': False, 'timeout': 5},
        )
    assert (result == MUT.CmdRes(payload, stderr, 3, False))
    run.assert_called_once_with(
        ['synthetic-command'], input=payload, capture_output=True,
        shell=False, text=False, timeout=5,
    )
    if isSecret:
        assert ('synthetic-stdin' not in caplog.text)
        assert ('synthetic-stderr' not in caplog.text)
        assert ('Command failed with exit code 3' in caplog.text)
        assert ('Try 1/1' in caplog.text)
        assert (': [SNIPPED]' in caplog.text)
    else: assert ('synthetic-stderr' in caplog.text)


def test_Run_SecretRetriesLogOnlySafeMetadata(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = 'synthetic-secret-argument'
    payload = b'synthetic-secret-input'
    stderr = b'synthetic-secret-environment'
    env = {'SYNTHETIC_SECRET': 'synthetic-secret-environment'}
    run = mock.Mock(side_effect=[
        Completed(9, payload, stderr), Completed(9, payload, stderr),
    ])
    sleep = mock.Mock()
    monkeypatch.setattr(MUT.subprocess, 'run', run)
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(sleep=sleep))
    with caplog.at_level('DEBUG'):
        result = MUT.CmdExec(defTryMax=2, tryWait=0).Run(
            ['synthetic-command', marker], input=payload, isSecret=True,
            spPars={'text': False, 'timeout': 5, 'env': env},
        )
    assert (result == MUT.CmdRes(payload, stderr, 9, False))
    expectedCall = mock.call(
        ['synthetic-command', marker], input=payload, capture_output=True,
        shell=False, text=False, timeout=5, env=env,
    )
    assert (run.call_args_list == ([expectedCall] * 2))
    assert (sleep.call_args_list == [mock.call(0)])
    for value in (
        marker, payload.decode(), stderr.decode(), env['SYNTHETIC_SECRET'],
    ):
        assert (value not in caplog.text)
    assert (caplog.messages == [
        'Command failed with exit code 9 (Try 1/2): [SNIPPED]',
        'Retrying execution in 0 seconds...',
        'Command failed with exit code 9 (Try 2/2): [SNIPPED]',
    ])
    assert ([record.levelname for record in caplog.records] == [
        'WARNING', 'INFO', 'WARNING',
    ])
    assert all((record.exc_info is None) for record in caplog.records)
