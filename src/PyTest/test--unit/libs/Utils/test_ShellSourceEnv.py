import json
from pathlib import Path
from unittest import mock

import pytest

import libs.Utils.ShellSourceEnv as MUT
import Utils


class FakeProc:
    def __init__(
        self,
        snapshots: list[dict[str, str]],
        *,
        pollResults: list[int | None] | None = None,
        raiseOnExit: BaseException | None = None,
        emptyFirstRead: bool = False,
    ) -> None:
        self.snapshots = list(snapshots)
        self.pollResults = list(pollResults or [None, None, 0])
        self.raiseOnExit = raiseOnExit
        self.emptyFirstRead = emptyFirstRead
        self.stdin = mock.Mock()
        self.stdout = mock.Mock()
        self.written: list[str] = []
        self.waitCalls = 0
        self.terminated = False
        self._readCount = 0

        def Write(text: str) -> None:
            self.written.append(text)

        def Readline() -> str:
            self._readCount += 1
            if (self.emptyFirstRead and (self._readCount == 1)):
                return ''
            if not self.snapshots:
                return ''
            return json.dumps(self.snapshots.pop(0)) + '\n'

        self.stdin.write.side_effect = Write
        self.stdin.flush = mock.Mock()
        self.stdout.readline.side_effect = Readline

    def poll(self) -> int | None:
        if not self.pollResults:
            return 0
        return self.pollResults.pop(0)

    def wait(self, timeout: int | None = None) -> int:
        self.waitCalls += 1
        if (self.raiseOnExit is not None):
            error = self.raiseOnExit
            self.raiseOnExit = None
            raise error
        return 0

    def terminate(self) -> None:
        self.terminated = True


def test_BashEnv_SnapshotAndSourceUseQuotedProtocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeProc([{'A': '1'}, {'A': '2', 'B': '3'}])
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'Popen')
    MUT.subprocess.Popen.return_value = fake

    env = MUT.BashEnv(pyBin='/usr/bin/python3')
    assert (env.Snapshot() == {'A': '1'})
    env.Source('/tmp/demo.env')
    assert (env.Snapshot() == {'A': '2', 'B': '3'})
    assert any('/usr/bin/python3' in chunk for chunk in fake.written)
    assert any(('source' in chunk) and ('demo.env' in chunk)
               for chunk in fake.written)
    MUT.subprocess.Popen.assert_called_once_with(
        ['bash', '--noprofile', '--norc'],
        bufsize=1,
        stdin=MUT.subprocess.PIPE,
        stdout=MUT.subprocess.PIPE,
        stderr=None,
        text=True,
    )


def test_BashEnv_SnapshotRaisesWhenBashExits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeProc([], pollResults=[7], emptyFirstRead=True)
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'Popen')
    MUT.subprocess.Popen.return_value = fake

    with pytest.raises(RuntimeError, match='exit status: 7'):
        MUT.BashEnv().Snapshot()


def test_BashEnv_CloseIsIdempotentWhenAlreadyExited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeProc([], pollResults=[0])
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'Popen')
    MUT.subprocess.Popen.return_value = fake

    MUT.BashEnv().Close()
    assert (fake.waitCalls == 0)
    assert not fake.terminated


def test_BashEnv_CloseHandlesBrokenPipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeProc(
        [],
        pollResults=[None],
        raiseOnExit=BrokenPipeError(),
    )
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'Popen')
    MUT.subprocess.Popen.return_value = fake

    MUT.BashEnv().Close()
    assert fake.terminated
    assert (fake.waitCalls == 2)


def test_BashEnv_CloseHandlesTimeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeProc(
        [],
        pollResults=[None],
        raiseOnExit=MUT.subprocess.TimeoutExpired(cmd='bash', timeout=5),
    )
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'Popen')
    MUT.subprocess.Popen.return_value = fake

    MUT.BashEnv().Close()
    assert fake.terminated
    assert (fake.waitCalls == 2)


def test_GetEnvDelta_ReturnsDeletedAndUpdatedKeys(
    tmp_path: Path,
) -> None:
    envFile = tmp_path / 'env.sh'
    envFile.write_text('true\n')
    fake = FakeProc([
        {'KEEP': '1', 'DROP': 'x', 'CHANGE': 'old'},
        {'KEEP': '1', 'CHANGE': 'new', 'ADD': 'y'},
    ])

    class EnvStub(MUT.BashEnv):
        def __init__(self, pyBin: str | None = None) -> None:
            self.pyBin = pyBin or 'python'
            self.proc = fake

    (deleted, updated) = MUT.GetEnvDelta(EnvStub, envFile)
    assert (deleted == {'DROP'})
    assert (updated == {'CHANGE': 'new', 'ADD': 'y'})
    assert any('source' in chunk for chunk in fake.written)
    assert any(chunk.startswith('exit') for chunk in fake.written)


def test_GetEnvDelta_ClosesEvenWhenSnapshotFails(
    tmp_path: Path,
) -> None:
    envFile = tmp_path / 'env.sh'
    envFile.write_text('true\n')
    closed = {'done': False}

    class EnvStub:
        def Snapshot(self) -> dict[str, str]:
            raise RuntimeError('boom')

        def Source(self, envFile: object) -> None:
            raise AssertionError('should not source')

        def Close(self) -> None:
            closed['done'] = True

    with pytest.raises(RuntimeError, match='boom'):
        MUT.GetEnvDelta(EnvStub, envFile)
    assert closed['done']


def test_BashEnv_DefaultsPythonToSysExecutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.subprocess, 'Popen')
    MUT.subprocess.Popen.return_value = FakeProc([{}])
    env = MUT.BashEnv()
    assert (env.pyBin == MUT.sys.executable)
