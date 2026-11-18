from collections.abc import Iterator
from io import StringIO
from json import JSONDecodeError
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest

import libs.Utils.ShellSourceEnv as MUT


@pytest.fixture(autouse=True)
def PatchParsingAndQuoting(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = mock.Mock(name='json')
    quoting = mock.Mock(name='shlex')
    quoting.quote.return_value = 'quoted-token'
    monkeypatch.setattr(MUT, 'json', parser)
    monkeypatch.setattr(MUT, 'shlex', quoting)


def NewBashEnv(
    monkeypatch: pytest.MonkeyPatch,
    pyBin: str | None = None,
) -> tuple[MUT.BashEnv, mock.Mock]:
    process = mock.Mock()
    process.stdin = mock.Mock()
    process.stdout = mock.Mock()
    process.poll.return_value = 0
    popen = mock.Mock(return_value=process)
    monkeypatch.setattr(MUT.subprocess, 'Popen', popen)
    shEnv = MUT.BashEnv(pyBin)
    popen.assert_called_once_with(
        ['bash', '--noprofile', '--norc'],
        bufsize=1,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
    )
    return (shEnv, popen)


@pytest.fixture(name='bashEnv')
def BashEnv(monkeypatch: pytest.MonkeyPatch) -> MUT.BashEnv:
    (shEnv, _) = NewBashEnv(monkeypatch, '/python with spaces')
    return shEnv


@pytest.mark.parametrize('pyBin', [None, sys.executable])
def test_Init_UsesCurrentPythonAndMockedProcess(
    monkeypatch: pytest.MonkeyPatch,
    pyBin: str | None,
) -> None:
    (shEnv, popen) = NewBashEnv(monkeypatch, pyBin)

    assert (shEnv.pyBin == (pyBin or sys.executable))
    assert (shEnv.proc is popen.return_value)


@pytest.mark.parametrize('method', ['write', 'flush'])
def test_Snapshot_BrokenPipeIsRuntimeError(
    bashEnv: MUT.BashEnv,
    method: str,
) -> None:
    error = BrokenPipeError('closed')
    getattr(bashEnv.proc.stdin, method).side_effect = error

    with pytest.raises(RuntimeError) as caught:
        bashEnv.Snapshot()

    assert (caught.value.__cause__ is error)
    bashEnv.proc.stdout.readline.assert_not_called()


@pytest.mark.parametrize('usePath', [False, True])
def test_Source_QualifiesBasenameForShellSource(
    bashEnv: MUT.BashEnv,
    usePath: bool,
) -> None:
    sourceFile = Path('env.sh')
    envFile = sourceFile if usePath else str(sourceFile)

    bashEnv.Source(envFile)

    expectedPath = 'quoted-token'
    MUT.shlex.quote.assert_called_once_with(('./' + str(sourceFile)))
    bashEnv.proc.stdin.write.assert_called_once_with(
        f'source {expectedPath} 0< /dev/null 1>&2\n'
        'case $? in (0) ;; (*) exit $?;; esac\n'
    )
    bashEnv.proc.stdin.flush.assert_called_once_with()


@pytest.mark.parametrize('pyBin', [None, sys.executable])
@pytest.mark.parametrize('usePath', [False, True])
def test_GetEnvDelta_UsesMockedSnapshotsAndQuotedSourcePath(
    monkeypatch: pytest.MonkeyPatch,
    pyBin: str | None,
    usePath: bool,
) -> None:
    (shEnv, _) = NewBashEnv(monkeypatch, pyBin)
    envPath = Path("/unit/quoted ' environment.sh")
    envFile = envPath if usePath else str(envPath)
    before = {
        'UNIT_SHELL_DELETE': 'old',
        'UNIT_SHELL_KEEP': 'unchanged',
        'UNIT_SHELL_CHANGE': 'old',
    }
    after = {
        'UNIT_SHELL_KEEP': 'unchanged',
        'UNIT_SHELL_CHANGE': 'new',
        'UNIT_SHELL_NEW': 'value with spaces and $literal',
    }
    MUT.json.loads.side_effect = [before, after]
    shEnv.proc.stdout.readline.side_effect = [
        'before-snapshot\n',
        'after-snapshot\n',
    ]
    factory = mock.Mock(return_value=shEnv)

    updated = MUT.GetEnvDelta(factory, [envFile])

    assert (updated == {
        'UNIT_SHELL_DELETE': None,
        'UNIT_SHELL_CHANGE': 'new',
        'UNIT_SHELL_NEW': 'value with spaces and $literal',
    })
    assert (shEnv.pyBin == (pyBin or sys.executable))
    factory.assert_called_once_with()
    assert (shEnv.proc.stdout.readline.call_count == 2)
    assert (shEnv.proc.stdin.flush.call_count == 3)
    assert (MUT.json.loads.call_args_list == [
        mock.call('before-snapshot\n'), mock.call('after-snapshot\n'),
    ])
    assert (MUT.shlex.quote.call_args_list[2] == mock.call(str(envPath)))
    sourceCommand = shEnv.proc.stdin.write.call_args_list[1].args[0]
    assert (sourceCommand == (
        'source quoted-token 0< /dev/null 1>&2\n'
        'case $? in (0) ;; (*) exit $?;; esac\n'
    ))
    assert (shEnv.proc.poll.call_count == 1)


@pytest.mark.parametrize('script', [
    '',
    '# comment without newline',
    'export UNIT_STREAM_VALUE="literal \'quotes\' and $HOME"',
    ' \n\t',
    'export UNIT_STREAM_VALUE=plain',
    'export UNIT_STREAM_VALUE=plain # trailing comment',
    (
        "export UNIT_STREAM_VALUE='\"; $literal `echo ignored` { }'\n"
        'export UNIT_STREAM_SECOND="second line"'
    ),
    (
        'printf "non-json source output\\n"\n'
        'if read -r input; then export UNIT_STREAM_READ=consumed; '
        'else export UNIT_STREAM_READ=eof; fi'
    ),
    'false',
    'export UNIT_STREAM_VALUE=partial\nfalse # no final newline',
    (
        'export UNIT_STREAM_VALUE=partial\n'
        'set -e\n'
        '(exit 7)\n'
        'export UNIT_STREAM_VALUE=unreachable'
    ),
    'export UNIT_STREAM_VALUE=partial\n(exit 7)',
])
def test_Source_CopiesScriptTextWithoutRunningIt(
    bashEnv: MUT.BashEnv,
    script: str,
) -> None:
    stream = StringIO(script)

    bashEnv.Source(stream)

    writes = [call.args[0] for call in bashEnv.proc.stdin.write.call_args_list]
    assert (writes == [
        '{\n: # Protect from empty file.\n',
        *([script] if script else []),
        '\n} 0< /dev/null 1>&2\n'
        'case $? in (0) ;; (*) exit $?;; esac\n',
    ])
    assert (stream.tell() == len(script))
    assert not stream.closed


@pytest.mark.parametrize('useFileMock', [False, True])
def test_Source_ReadsCurrentPositionAndLeavesCallerStreamOpen(
    bashEnv: MUT.BashEnv,
    useFileMock: bool,
) -> None:
    prefix = 'ignored non-shell prefix\n'
    script = 'export UNIT_STREAM_VALUE=from-offset'
    if useFileMock:
        stream = mock.Mock()
        stream.read.side_effect = [script, '']
        stream.close = mock.Mock()
        stream.closed = False
    else:
        stream = StringIO(prefix + script)
        stream.seek(len(prefix))

    bashEnv.Source(stream)

    writes = [call.args[0] for call in bashEnv.proc.stdin.write.call_args_list]
    assert (writes == [
        '{\n: # Protect from empty file.\n',
        script,
        '\n} 0< /dev/null 1>&2\n'
        'case $? in (0) ;; (*) exit $?;; esac\n',
    ])
    if useFileMock:
        assert (stream.read.call_args_list == [
            mock.call(65536), mock.call(65536),
        ])
        stream.close.assert_not_called()
    else:
        assert (stream.tell() == len((prefix + script)))
    assert not stream.closed


def test_Source_SendsSequentialScriptsToOneProcess(
    bashEnv: MUT.BashEnv,
) -> None:
    first = StringIO(
        'localValue=retained\n'
        'SetStreamValue() { export UNIT_STREAM_VALUE="$localValue"; }\n'
        'export UNIT_STREAM_DELETE=old'
    )
    second = StringIO(
        'unset UNIT_STREAM_DELETE\n'
        'SetStreamValue'
    )
    process = bashEnv.proc

    bashEnv.Source(first)
    bashEnv.Source(second)

    assert (bashEnv.proc is process)
    MUT.subprocess.Popen.assert_called_once()
    assert (bashEnv.proc.stdin.write.call_args_list == [
        mock.call('{\n: # Protect from empty file.\n'),
        mock.call(first.getvalue()),
        mock.call(
            '\n} 0< /dev/null 1>&2\n'
            'case $? in (0) ;; (*) exit $?;; esac\n'
        ),
        mock.call('{\n: # Protect from empty file.\n'),
        mock.call(second.getvalue()),
        mock.call(
            '\n} 0< /dev/null 1>&2\n'
            'case $? in (0) ;; (*) exit $?;; esac\n'
        ),
    ])
    assert not first.closed
    assert not second.closed


def test_Source_TransfersLargeStreamWithOnlyBoundedReads(
    bashEnv: MUT.BashEnv,
) -> None:
    readSizes = []

    class BoundedStream(StringIO):
        def read(self, size: int = -1) -> str:
            assert (0 < size <= 65536)
            readSizes.append(size)
            return super().read(size)

    script = (
        ('# bounded chunk payload\n' * 50000) +
        'export UNIT_STREAM_VALUE=complete'
    )
    stream = BoundedStream(script)

    bashEnv.Source(stream)

    writes = [call.args[0] for call in bashEnv.proc.stdin.write.call_args_list]
    assert (''.join(writes[1:-1]) == script)
    assert (len(readSizes) > 3)
    assert (stream.tell() == len(script))
    assert not stream.closed


def test_GetEnvDelta_UsesOneMockShellForOrderedStreams() -> None:
    shEnv = mock.Mock(spec=MUT.BashEnv)
    before = {
        'UNIT_STREAM_DELETE': 'old',
        'UNIT_STREAM_KEEP': 'same',
    }
    after = {
        'UNIT_STREAM_KEEP': 'same',
        'UNIT_STREAM_VALUE': 'retained',
    }
    shEnv.Snapshot.side_effect = [before, after]
    factory = mock.Mock(return_value=shEnv)
    streams = [
        StringIO(
            'localValue=retained\n'
            'StreamValue() { printf "%s" "$localValue"; }'
        ),
        StringIO(
            'export UNIT_STREAM_VALUE="$(StreamValue)"\n'
            'unset UNIT_STREAM_DELETE'
        ),
    ]

    delta = MUT.GetEnvDelta(factory, streams)

    assert (delta == {
        'UNIT_STREAM_DELETE': None,
        'UNIT_STREAM_VALUE': 'retained',
    })
    factory.assert_called_once_with()
    assert (shEnv.mock_calls == [
        mock.call.Snapshot(),
        mock.call.Source(streams[0]),
        mock.call.Source(streams[1]),
        mock.call.Snapshot(),
        mock.call.Close(),
    ])


@pytest.mark.parametrize('failure', [
    'before', 'firstSource', 'secondSource', 'after',
])
def test_GetEnvDelta_ClosesShellAndPreservesFailure(failure: str) -> None:
    shEnv = mock.Mock(spec=MUT.BashEnv)
    error = OSError('snapshot or source failure')
    if (failure in ('firstSource', 'secondSource')):
        shEnv.Source.side_effect = (
            [error] if (failure == 'firstSource') else [None, error]
        )
        shEnv.Snapshot.return_value = {}
    else:
        shEnv.Snapshot.side_effect = (
            [error] if (failure == 'before') else [{}, error]
        )
    factory = mock.Mock(return_value=shEnv)

    with pytest.raises(OSError) as raised:
        MUT.GetEnvDelta(factory, ['first.sh', 'second.sh', 'last.sh'])

    assert (raised.value is error)
    factory.assert_called_once_with()
    shEnv.Close.assert_called_once_with()
    expectedFiles = {
        'before': [],
        'firstSource': ['first.sh'],
        'secondSource': ['first.sh', 'second.sh'],
        'after': ['first.sh', 'second.sh', 'last.sh'],
    }[failure]
    assert (shEnv.Source.call_args_list == [
        mock.call(envFile) for envFile in expectedFiles
    ])


@pytest.mark.parametrize('before,after,expected', [
    ({}, {}, {}),
    (
        {'KEEP': 'same', 'DROP': '', 'CHANGE': 'old', 'EMPTY': ''},
        {'KEEP': 'same', 'CHANGE': '', 'EMPTY': '', 'NEW': 'value'},
        {'DROP': None, 'CHANGE': '', 'NEW': 'value'},
    ),
    ({}, {'EMPTY': ''}, {'EMPTY': ''}),
    ({'DROP': 'value'}, {}, {'DROP': None}),
])
def test_GetEnvDelta_ReturnsOnlyChangesWithNoneForDeletedKeys(
    before: dict,
    after: dict,
    expected: dict,
) -> None:
    shEnv = mock.Mock(spec=MUT.BashEnv)
    shEnv.Snapshot.side_effect = [before, after]
    beforeCopy = dict(before)
    afterCopy = dict(after)

    assert (MUT.GetEnvDelta(lambda: shEnv, ['env.sh']) == expected)

    assert (before == beforeCopy)
    assert (after == afterCopy)
    shEnv.Source.assert_called_once_with('env.sh')
    shEnv.Close.assert_called_once_with()


def test_ShellKind_ContainsOnlyBash() -> None:
    assert (list(MUT.ShellKind) == [MUT.ShellKind.BASH])
    assert (MUT.ShellKind.BASH.value == 1)


@pytest.mark.parametrize('envFiles', [[], [Path('first.sh'), 'second.sh']])
def test_GetEnvDelta_CapturesOneSessionAroundOrderedIterable(
    envFiles: list[str | Path],
) -> None:
    shEnv = mock.Mock(spec=MUT.BashEnv)
    shEnv.Snapshot.side_effect = [{'VALUE': 'old'}, {'VALUE': 'new'}]
    factory = mock.Mock(return_value=shEnv)

    assert (MUT.GetEnvDelta(factory, iter(envFiles)) == {'VALUE': 'new'})

    factory.assert_called_once_with()
    assert (shEnv.mock_calls == [
        mock.call.Snapshot(),
        *[mock.call.Source(envFile) for envFile in envFiles],
        mock.call.Snapshot(),
        mock.call.Close(),
    ])


@pytest.mark.parametrize('explicitShell', [False, True])
def test_DoShellSource_PassesDeltaToEnvironmentUpdater(
    monkeypatch: pytest.MonkeyPatch,
    explicitShell: bool,
) -> None:
    envFiles = (Path('root.sh'), 'scenario.sh')
    envFilesIter = iter(envFiles)
    expectedDelta = {
        'UNIT_SHELL_DELETE': None,
        'UNIT_SHELL_ABSENT': None,
        'UNIT_SHELL_NEW': 7,
    }

    def Capture(
        shEnvClass: type[MUT.BashEnv],
        files: Iterator[str | Path],
    ) -> dict[str, str | int | None]:
        assert (shEnvClass is MUT.BashEnv)
        assert (files is envFilesIter)
        assert (tuple(files) == envFiles)
        return expectedDelta

    getDelta = mock.Mock(side_effect=Capture)
    updateEnv = mock.Mock()
    monkeypatch.setattr(MUT, 'GetEnvDelta', getDelta)
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)
    shellArgs = (MUT.ShellKind.BASH,) if explicitShell else ()

    assert (MUT.DoShellSource(envFilesIter, *shellArgs) is None)

    getDelta.assert_called_once_with(MUT.BashEnv, envFilesIter)
    updateEnv.assert_called_once_with(expectedDelta)


def test_DoShellSource_EmptyFilesStillCaptureAggregateDelta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    getDelta = mock.Mock(return_value={})
    updateEnv = mock.Mock()
    monkeypatch.setattr(MUT, 'GetEnvDelta', getDelta)
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)

    assert (MUT.DoShellSource(envFiles=[]) is None)

    getDelta.assert_called_once_with(MUT.BashEnv, [])
    updateEnv.assert_called_once_with({})


def test_DoShellSource_AcceptsShellKindKeyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    getDelta = mock.Mock(return_value={})
    updateEnv = mock.Mock()
    monkeypatch.setattr(MUT, 'GetEnvDelta', getDelta)
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)

    MUT.DoShellSource(envFiles=['env.sh'], shellKind=MUT.ShellKind.BASH)

    getDelta.assert_called_once_with(MUT.BashEnv, ['env.sh'])
    updateEnv.assert_called_once_with({})


@pytest.mark.parametrize('shellKind', [None, 'BASH', 1])
def test_DoShellSource_RejectsUnsupportedShellBeforeSourcing(
    monkeypatch: pytest.MonkeyPatch,
    shellKind: object,
) -> None:
    getDelta = mock.Mock()
    updateEnv = mock.Mock()
    monkeypatch.setattr(MUT, 'GetEnvDelta', getDelta)
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)

    with pytest.raises(ValueError) as raised:
        MUT.DoShellSource(['root.sh'], shellKind)

    assert (raised.value.args == (f'Unsupported Shell Kind: {shellKind}',))
    getDelta.assert_not_called()
    updateEnv.assert_not_called()


@pytest.mark.parametrize('error', [OSError('source failure'), SystemExit(7)])
def test_DoShellSource_PreservesCaptureFailureWithoutApplyingDelta(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    getDelta = mock.Mock(side_effect=error)
    updateEnv = mock.Mock()
    monkeypatch.setattr(MUT, 'GetEnvDelta', getDelta)
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)

    with pytest.raises(type(error)) as raised:
        MUT.DoShellSource(envFiles=['root.sh', 'scenario.sh', 'last.sh'])

    assert (raised.value is error)
    getDelta.assert_called_once_with(
        MUT.BashEnv, ['root.sh', 'scenario.sh', 'last.sh'],
    )
    updateEnv.assert_not_called()


@pytest.mark.parametrize('sourceFails', [False, True])
@pytest.mark.parametrize('usePath', [False, True])
def test_DoShellSource_UpdatesOnlyAfterSuccessfulCapture(
    monkeypatch: pytest.MonkeyPatch,
    sourceFails: bool,
    usePath: bool,
) -> None:
    if usePath:
        envFiles = [
            Path('/unit/root env.sh'),
            Path('/unit/scenario env.sh'),
        ]
    else:
        envFiles = [
            StringIO('first synthetic stream'),
            StringIO('second synthetic stream'),
        ]
    expectedDelta = {
        'CAPTURED_VALUE': 'synthetic',
        'UNIT_SHELL_DELETE': None,
    }
    error = RuntimeError('source failure')
    getDelta = mock.Mock(return_value=expectedDelta)
    if sourceFails: getDelta.side_effect = error
    updateEnv = mock.Mock()
    monkeypatch.setattr(MUT, 'GetEnvDelta', getDelta)
    monkeypatch.setattr(MUT, 'UpdEnv', updateEnv)

    if sourceFails:
        with pytest.raises(RuntimeError) as raised:
            MUT.DoShellSource(envFiles)
        assert (raised.value is error)
        updateEnv.assert_not_called()
    else:
        assert (MUT.DoShellSource(envFiles) is None)
        updateEnv.assert_called_once_with(expectedDelta)

    getDelta.assert_called_once_with(MUT.BashEnv, envFiles)


@pytest.mark.parametrize('response', ['', 'not-json\n'])
def test_Snapshot_RejectsMissingOrMalformedResponse(
    bashEnv: MUT.BashEnv,
    response: str,
) -> None:
    bashEnv.proc.stdout.readline.return_value = response
    bashEnv.proc.poll.return_value = 32
    error = JSONDecodeError('malformed snapshot', response, 0)
    MUT.json.loads.side_effect = error

    if response:
        with pytest.raises(JSONDecodeError) as raised:
            bashEnv.Snapshot()
        assert (raised.value is error)
        MUT.json.loads.assert_called_once_with(response)
    else:
        with pytest.raises(RuntimeError, match='exit status: 32'):
            bashEnv.Snapshot()
        MUT.json.loads.assert_not_called()

    bashEnv.proc.stdin.flush.assert_called_once_with()


def test_Snapshot_UsesQuotedInterpreterAndDecodedResult(
    bashEnv: MUT.BashEnv,
) -> None:
    expected = {'VALUE': 'spaces\nnewlines and $literal'}
    bashEnv.proc.stdout.readline.return_value = 'raw-snapshot\n'
    MUT.json.loads.return_value = expected
    pyCode = (
        'import json, os, sys; '
        'json.dump(dict(os.environ), sys.stdout); '
        'sys.stdout.write("\\n")'
    )

    assert (bashEnv.Snapshot() is expected)

    assert (MUT.shlex.quote.call_args_list == [
        mock.call('/python with spaces'), mock.call(pyCode),
    ])
    bashEnv.proc.stdin.write.assert_called_once_with(
        'quoted-token -c quoted-token || exit $?\n',
    )
    MUT.json.loads.assert_called_once_with('raw-snapshot\n')
    bashEnv.proc.stdin.flush.assert_called_once_with()


def test_Close_LeavesAlreadyExitedProcessAlone(bashEnv: MUT.BashEnv) -> None:
    bashEnv.proc.poll.return_value = 0

    bashEnv.Close()

    bashEnv.proc.stdin.write.assert_not_called()
    bashEnv.proc.wait.assert_not_called()
    bashEnv.proc.terminate.assert_not_called()


@pytest.mark.parametrize('failure', ['write', 'flush', 'wait'])
def test_Close_TerminatesWhenGracefulShutdownFails(
    bashEnv: MUT.BashEnv,
    failure: str,
) -> None:
    bashEnv.proc.poll.return_value = None
    if (failure == 'wait'):
        bashEnv.proc.wait.side_effect = [
            subprocess.TimeoutExpired('bash', 5),
            0,
        ]
    else:
        getattr(bashEnv.proc.stdin, failure).side_effect = BrokenPipeError()

    bashEnv.Close()

    bashEnv.proc.stdin.write.assert_called_once_with('exit\n')
    bashEnv.proc.terminate.assert_called_once_with()
    assert (bashEnv.proc.wait.call_args_list == (
        [mock.call(timeout=5)] * (2 if (failure == 'wait') else 1)
    ))


def test_Close_PropagatesTimeoutAfterTermination(bashEnv: MUT.BashEnv) -> None:
    bashEnv.proc.poll.return_value = None
    error = subprocess.TimeoutExpired('bash', 5)
    bashEnv.proc.wait.side_effect = error

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        bashEnv.Close()

    assert (raised.value is error)
    bashEnv.proc.terminate.assert_called_once_with()
    assert (bashEnv.proc.wait.call_count == 2)
