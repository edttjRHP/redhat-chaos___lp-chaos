from collections.abc import Iterator
from io import StringIO, TextIOBase
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from unittest import mock

import pytest

import libs.Utils.ShellSourceEnv as MUT
from libs.CustomTypes import TempEnv
import Utils


@pytest.mark.parametrize('pyBin', [None, sys.executable])
@pytest.mark.parametrize('usePath', [False, True])
def test_GetEnvDelta_SourcesQuotedScriptAndClosesNativeBash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    pyBin: str | None,
    usePath: bool,
) -> None:
    monkeypatch.setenv('UNIT_SHELL_DELETE', 'old')
    monkeypatch.setenv('UNIT_SHELL_KEEP', 'unchanged')
    monkeypatch.delenv('UNIT_SHELL_NEW', raising=False)
    beforeEnv = dict(os.environ)
    envFile = tmp_path / "quoted ' environment.sh"
    envFile.write_text(
        'unset UNIT_SHELL_DELETE\n'
        "export UNIT_SHELL_NEW='value with spaces and $literal'\n"
        "echo 'source output'\n"
    )
    shEnv = MUT.BashEnv(pyBin)

    try:
        updated = MUT.GetEnvDelta(
            lambda: shEnv, [(envFile if usePath else str(envFile))],
        )
        assert (updated['UNIT_SHELL_DELETE'] is None)
        assert (updated['UNIT_SHELL_NEW'] == 'value with spaces and $literal')
        assert ('UNIT_SHELL_KEEP' not in updated)
        assert (shEnv.proc.poll() == 0)
        assert ('source output' in capfd.readouterr().err)
        assert (dict(os.environ) == beforeEnv)
    finally:
        shEnv.Close()
        shEnv.proc.stdin.close()
        shEnv.proc.stdout.close()


@pytest.fixture(name='bashEnv')
def BashEnv(monkeypatch: pytest.MonkeyPatch) -> MUT.BashEnv:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'Popen')
    MUT.subprocess.Popen.return_value.stdin = mock.Mock(spec=TextIOBase)
    MUT.subprocess.Popen.return_value.stdout = mock.Mock(spec=TextIOBase)
    shEnv = MUT.BashEnv('/python with spaces')
    MUT.subprocess.Popen.assert_called_once_with(
        ['bash', '--noprofile', '--norc'], bufsize=1,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, text=True,
    )
    return shEnv


@pytest.fixture(name='nativeBashEnv')
def NativeBashEnv(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[MUT.BashEnv]:
    for key in tuple(os.environ):
        if key.startswith('UNIT_STREAM_'): monkeypatch.delenv(key)
    shEnv = MUT.BashEnv()
    try: yield shEnv
    finally:
        shEnv.Close()
        shEnv.proc.stdin.close()
        shEnv.proc.stdout.close()


@pytest.mark.parametrize('usePath', [False, True])
def test_Source_QuotesPathsAndProtectsCommandChannel(
    bashEnv: MUT.BashEnv,
    usePath: bool,
) -> None:
    path = Path("quoted ' environment.sh")

    bashEnv.Source((path if usePath else str(path)))

    bashEnv.proc.stdin.write.assert_called_once_with(
        f'source {shlex.quote(str(path))} 0< /dev/null 1>&2 || exit $?\n'
    )
    bashEnv.proc.stdin.flush.assert_called_once_with()


def test_Source_CopiesStreamContentWithoutQuotingOrClosing(
    bashEnv: MUT.BashEnv,
) -> None:
    content = 'export UNIT_STREAM_VALUE="literal \'quotes\' and $HOME"'
    stream = StringIO(content)

    bashEnv.Source(stream)

    assert (bashEnv.proc.stdin.write.call_args_list == [
        mock.call('{\n: # Protect from empty file.\n'), mock.call(content),
        mock.call('\n} 0< /dev/null 1>&2 || exit $?\n'),
    ])
    bashEnv.proc.stdin.flush.assert_called_once_with()
    assert not stream.closed


@pytest.mark.parametrize('script,expected', [
    ('', {}),
    ('# comment without newline', {}),
    (' \n\t', {}),
    ('export UNIT_STREAM_VALUE=plain', {'UNIT_STREAM_VALUE': 'plain'}),
    (
        'export UNIT_STREAM_VALUE=plain # trailing comment',
        {'UNIT_STREAM_VALUE': 'plain'}
    ),
    (
        (
            "export UNIT_STREAM_VALUE='\"; $literal `echo ignored` { }'\n"
            'export UNIT_STREAM_SECOND="second line"'
        ),
        {
            'UNIT_STREAM_VALUE': '"; $literal `echo ignored` { }',
            'UNIT_STREAM_SECOND': 'second line'
        }
    ),
])
def test_Source_ExecutesTextAndPreservesBoundaryNewline(
    nativeBashEnv: MUT.BashEnv,
    script: str,
    expected: dict[str, str],
) -> None:
    stream = StringIO(script)

    nativeBashEnv.Source(stream)
    snapshot = nativeBashEnv.Snapshot()

    assert ({
        key: value
        for (key, value) in snapshot.items()
        if key.startswith('UNIT_STREAM_')
    } == expected)
    assert not stream.closed
    assert (stream.tell() == len(script))
    nativeBashEnv.Source(StringIO('export UNIT_STREAM_NEXT=ready'))
    assert (nativeBashEnv.Snapshot()['UNIT_STREAM_NEXT'] == 'ready')


@pytest.mark.parametrize('realFile', [False, True])
def test_Source_ReadsCurrentPositionAndLeavesCallerStreamOpen(
    nativeBashEnv: MUT.BashEnv,
    tmp_path: Path,
    realFile: bool,
) -> None:
    prefix = 'ignored non-shell prefix\n'
    script = 'export UNIT_STREAM_VALUE=from-offset'
    if realFile:
        path = tmp_path / 'readable text.txt'
        path.write_text(prefix + script)
        stream = path.open()
    else:
        stream = StringIO(prefix + script)
    try:
        stream.seek(len(prefix))
        nativeBashEnv.Source(stream)
        assert (nativeBashEnv.Snapshot()['UNIT_STREAM_VALUE'] == (
            'from-offset'
        ))
        assert not stream.closed
        assert (stream.read() == '')
    finally:
        stream.close()


def test_Source_RetainsParentShellVariablesFunctionsAndUnsets(
    nativeBashEnv: MUT.BashEnv,
) -> None:
    nativeBashEnv.Source(StringIO(
        'localValue=retained\n'
        'SetStreamValue() { export UNIT_STREAM_VALUE="$localValue"; }\n'
        'export UNIT_STREAM_DELETE=old'
    ))
    assert (nativeBashEnv.Snapshot()['UNIT_STREAM_DELETE'] == 'old')
    nativeBashEnv.Source(StringIO(
        'unset UNIT_STREAM_DELETE\nSetStreamValue'
    ))

    snapshot = nativeBashEnv.Snapshot()
    assert (snapshot['UNIT_STREAM_VALUE'] == 'retained')
    assert ('UNIT_STREAM_DELETE' not in snapshot)
    assert ('localValue' not in snapshot)


def test_Source_IsolatesScriptStdinAndRedirectsOutputFromSnapshots(
    capfd: pytest.CaptureFixture[str],
) -> None:
    shEnv = MUT.BashEnv()
    try:
        shEnv.Source(StringIO(
            'printf "non-json source output\\n"\n'
            'if read -r input; then export UNIT_STREAM_READ=consumed; '
            'else export UNIT_STREAM_READ=eof; fi'
        ))

        assert (shEnv.Snapshot()['UNIT_STREAM_READ'] == 'eof')
        output = capfd.readouterr()
        assert ('non-json source output' in output.err)
        assert ('non-json source output' not in output.out)
        assert (shEnv.Snapshot()['UNIT_STREAM_READ'] == 'eof')
    finally:
        shEnv.Close()
        shEnv.proc.stdin.close()
        shEnv.proc.stdout.close()


@pytest.mark.parametrize('script', [
    'false', 'export UNIT_STREAM_VALUE=partial\nfalse # no final newline',
])
def test_Source_FinalFailureIsDetectedBySnapshot(
    nativeBashEnv: MUT.BashEnv,
    script: str,
) -> None:
    nativeBashEnv.Source(StringIO(script))

    with pytest.raises(RuntimeError, match='Bash exited unexpectedly'):
        nativeBashEnv.Snapshot()
    assert (nativeBashEnv.proc.wait(timeout=5) == 1)


def test_Source_TransfersLargeStreamWithOnlyBoundedReads(
    nativeBashEnv: MUT.BashEnv,
) -> None:
    readSizes = []

    class BoundedStream(StringIO):
        def read(self, size: int = -1) -> str:
            assert (0 < size <= 65536)
            readSizes.append(size)
            return super().read(size)

    stream = BoundedStream(
        ('# bounded chunk payload\n' * 50000) +
        'export UNIT_STREAM_VALUE=complete'
    )

    nativeBashEnv.Source(stream)

    assert (nativeBashEnv.Snapshot()['UNIT_STREAM_VALUE'] == 'complete')
    assert (len(readSizes) > 3)
    assert (stream.tell() == len(stream.getvalue()))
    assert not stream.closed


def test_GetEnvDelta_ExecutesStreamsInOrderWithoutMutatingParent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('UNIT_STREAM_DELETE', 'old')
    before = dict(os.environ)
    streams = [StringIO(
        'localValue=retained\n'
        'StreamValue() { printf "%s" "$localValue"; }'
    ), StringIO(
        'export UNIT_STREAM_VALUE="$(StreamValue)"\n'
        'unset UNIT_STREAM_DELETE'
    )]

    delta = MUT.GetEnvDelta(MUT.BashEnv, streams)

    assert (delta['UNIT_STREAM_VALUE'] == 'retained')
    assert (delta['UNIT_STREAM_DELETE'] is None)
    assert (dict(os.environ) == before)
    assert all(not stream.closed for stream in streams)


@pytest.mark.parametrize('sourceFails', [False, True])
def test_DoShellSource_ExecutesStreamsWithUnsetsAndRestoration(
    monkeypatch: pytest.MonkeyPatch,
    sourceFails: bool,
) -> None:
    monkeypatch.setenv('UNIT_STREAM_DELETE', 'old')
    monkeypatch.delenv('UNIT_STREAM_VALUE', raising=False)
    before = dict(os.environ)
    stream = StringIO(
        'export UNIT_STREAM_VALUE=updated\nunset UNIT_STREAM_DELETE\n' +
        ('false' if sourceFails else ':')
    )

    with TempEnv():
        if sourceFails:
            with pytest.raises(RuntimeError, match='Bash exited unexpectedly'):
                MUT.DoShellSource([stream])
            assert (dict(os.environ) == before)
        else:
            MUT.DoShellSource([stream])
            assert (os.environ['UNIT_STREAM_VALUE'] == 'updated')
            assert ('UNIT_STREAM_DELETE' not in os.environ)
    assert (dict(os.environ) == before)
    assert not stream.closed


def test_Snapshot_UsesQuotedInterpreterAndDecodesOneLine(
    bashEnv: MUT.BashEnv,
) -> None:
    expected = {'VALUE': 'spaces\nnewlines and $literal'}
    bashEnv.proc.stdout.readline.return_value = json.dumps(expected) + '\n'

    assert (bashEnv.Snapshot() == expected)

    command = bashEnv.proc.stdin.write.call_args.args[0]
    (pyBin, option, pyCode) = shlex.split(command)
    assert (pyBin == '/python with spaces')
    assert (option == '-c')
    assert ('json.dump(dict(os.environ), sys.stdout)' in pyCode)
    assert ('sys.stdout.write("\\n")' in pyCode)
    bashEnv.proc.stdin.flush.assert_called_once_with()


@pytest.mark.parametrize('response', ['', 'not-json\n'])
def test_Snapshot_RejectsMissingOrMalformedResponse(
    bashEnv: MUT.BashEnv,
    response: str,
) -> None:
    bashEnv.proc.stdout.readline.return_value = response
    bashEnv.proc.poll.return_value = 32

    if response:
        with pytest.raises(json.JSONDecodeError): bashEnv.Snapshot()
    else:
        with pytest.raises(RuntimeError, match='exit status: 32'):
            bashEnv.Snapshot()

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
        bashEnv.proc.wait.side_effect = [subprocess.TimeoutExpired('bash', 5),
                                         0]
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
        'before': [], 'firstSource': ['first.sh'],
        'secondSource': ['first.sh', 'second.sh'],
        'after': ['first.sh', 'second.sh', 'last.sh'],
    }[failure]
    assert (shEnv.Source.call_args_list == [
        mock.call(envFile) for envFile in expectedFiles
    ])


@pytest.mark.parametrize('before,after,expected', [
    ({}, {}, {}),
    ({'KEEP': 'same', 'DROP': '', 'CHANGE': 'old', 'EMPTY': ''},
     {'KEEP': 'same', 'CHANGE': '', 'EMPTY': '', 'NEW': 'value'},
     {'DROP': None, 'CHANGE': '', 'NEW': 'value'}),
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
        mock.call.Snapshot(), mock.call.Close(),
    ])


@pytest.mark.parametrize('explicitShell', [False, True])
def test_DoShellSource_AppliesAggregateDeltaAndCoercesValues(
    monkeypatch: pytest.MonkeyPatch,
    explicitShell: bool,
) -> None:
    monkeypatch.setenv('UNIT_SHELL_DELETE', 'old')
    monkeypatch.setenv('UNIT_SHELL_KEEP', 'unchanged')
    monkeypatch.delenv('UNIT_SHELL_ABSENT', raising=False)
    monkeypatch.delenv('UNIT_SHELL_NEW', raising=False)
    envFiles = (Path('root.sh'), 'scenario.sh')
    envFilesIter = iter(envFiles)

    def Delta(shEnvClass: type, files: Iterator[str | Path]) -> dict:
        assert (shEnvClass is MUT.BashEnv)
        assert (files is envFilesIter)
        assert (tuple(files) == envFiles)
        assert (os.environ['UNIT_SHELL_DELETE'] == 'old')
        assert ('UNIT_SHELL_NEW' not in os.environ)
        return {
            'UNIT_SHELL_DELETE': None, 'UNIT_SHELL_ABSENT': None,
            'UNIT_SHELL_NEW': 7,
        }

    delta = mock.Mock(side_effect=Delta)
    monkeypatch.setattr(MUT, 'GetEnvDelta', delta)
    beforeEnv = dict(os.environ)
    with TempEnv():
        shellArgs = (MUT.ShellKind.BASH,) if explicitShell else ()
        result = MUT.DoShellSource(envFilesIter, *shellArgs)
        assert (result is None)
        assert (os.environ['UNIT_SHELL_NEW'] == '7')
        assert (os.environ['UNIT_SHELL_KEEP'] == 'unchanged')
        assert ('UNIT_SHELL_DELETE' not in os.environ)
        assert ('UNIT_SHELL_ABSENT' not in os.environ)
    assert (dict(os.environ) == beforeEnv)
    delta.assert_called_once_with(MUT.BashEnv, envFilesIter)


def test_DoShellSource_EmptyFilesStillCaptureAggregateDelta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delta = mock.Mock(return_value={})
    monkeypatch.setattr(MUT, 'GetEnvDelta', delta)
    beforeEnv = dict(os.environ)
    assert (MUT.DoShellSource(envFiles=[]) is None)
    assert (dict(os.environ) == beforeEnv)
    delta.assert_called_once_with(MUT.BashEnv, [])


def test_DoShellSource_AcceptsShellKindKeyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delta = mock.Mock(return_value={})
    monkeypatch.setattr(MUT, 'GetEnvDelta', delta)
    MUT.DoShellSource(envFiles=['env.sh'], shellKind=MUT.ShellKind.BASH)
    delta.assert_called_once_with(MUT.BashEnv, ['env.sh'])


@pytest.mark.parametrize('shellKind', [None, 'BASH', 1])
def test_DoShellSource_RejectsUnsupportedShellBeforeSourcing(
    monkeypatch: pytest.MonkeyPatch,
    shellKind: object,
) -> None:
    delta = mock.Mock()
    monkeypatch.setattr(MUT, 'GetEnvDelta', delta)
    beforeEnv = dict(os.environ)
    with pytest.raises(ValueError) as raised:
        MUT.DoShellSource(['root.sh'], shellKind)
    assert (raised.value.args == (f'Unsupported Shell Kind: {shellKind}',))
    assert (dict(os.environ) == beforeEnv)
    delta.assert_not_called()


@pytest.mark.parametrize('error', [OSError('source failure'), SystemExit(7)])
def test_DoShellSource_PreservesCaptureFailureWithoutApplyingAnyDelta(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    delta = mock.Mock(side_effect=error)
    monkeypatch.setattr(MUT, 'GetEnvDelta', delta)
    beforeEnv = dict(os.environ)
    with TempEnv():
        with pytest.raises(type(error)) as raised:
            MUT.DoShellSource(envFiles=['root.sh', 'scenario.sh', 'last.sh'])
        assert (raised.value is error)
    assert (dict(os.environ) == beforeEnv)
    delta.assert_called_once_with(
        MUT.BashEnv, ['root.sh', 'scenario.sh', 'last.sh'],
    )


@pytest.mark.parametrize('sourceFails', [False, True])
def test_DoShellSource_NativeBashVisibilityAndTempEnvRestoration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sourceFails: bool,
) -> None:
    monkeypatch.setenv('UNIT_SHELL_DELETE', 'old')
    monkeypatch.setenv('UNIT_SHELL_ORDER', 'parent')
    monkeypatch.delenv('UNIT_SHELL_SEEN', raising=False)
    monkeypatch.delenv('UNIT_SHELL_LOCAL', raising=False)
    monkeypatch.delenv('UNIT_SHELL_TRANSIENT', raising=False)
    rootFile = tmp_path / 'root env.sh'
    scenFile = tmp_path / 'scenario env.sh'
    rootFile.write_text(
        'UNIT_SHELL_LOCAL=root\n'
        'ShellValue() { printf "%s" "$UNIT_SHELL_LOCAL"; }\n'
        'export UNIT_SHELL_ORDER=root\n'
        'export UNIT_SHELL_TRANSIENT=temporary\n'
    )
    scenFile.write_text(
        'export UNIT_SHELL_SEEN="$(ShellValue)"\n'
        'export UNIT_SHELL_ORDER="$UNIT_SHELL_ORDER-scenario"\n'
        'unset UNIT_SHELL_TRANSIENT\n'
        'unset UNIT_SHELL_DELETE\n' + ('return 7\n' if sourceFails else '')
    )
    beforeEnv = dict(os.environ)
    beforeCwd = Path.cwd()
    beforePython = sys.executable
    with TempEnv():
        if sourceFails:
            with pytest.raises(RuntimeError, match='exit status: 7'):
                MUT.DoShellSource(envFiles=[rootFile, scenFile])
            assert (os.environ['UNIT_SHELL_ORDER'] == 'parent')
            assert (os.environ['UNIT_SHELL_DELETE'] == 'old')
            assert ('UNIT_SHELL_SEEN' not in os.environ)
        else:
            MUT.DoShellSource(envFiles=[rootFile, scenFile])
            assert (os.environ['UNIT_SHELL_SEEN'] == 'root')
            assert (os.environ['UNIT_SHELL_ORDER'] == 'root-scenario')
            assert ('UNIT_SHELL_DELETE' not in os.environ)
        assert ('UNIT_SHELL_LOCAL' not in os.environ)
        assert ('UNIT_SHELL_TRANSIENT' not in os.environ)
    assert (dict(os.environ) == beforeEnv)
    assert (Path.cwd() == beforeCwd)
    assert (sys.executable == beforePython)
