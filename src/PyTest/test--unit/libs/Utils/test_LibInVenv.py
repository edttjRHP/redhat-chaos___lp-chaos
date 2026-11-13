import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest

import libs.Utils.LibInVenv as MUT
import Utils


def test_Call_ReceiveFailureBeforeUnboundedWait_ReviewRegression(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
) -> None:
    (worker, context) = workerContext
    proc = context.Process.return_value
    proc.is_alive.side_effect = [True, False, False]
    proc.exitcode = None
    error = FalseReceiveError('could not reconstruct')
    context.Pipe.return_value[0].recv.side_effect = error
    MUT.wait.side_effect = AssertionError('unbounded wait reached')
    with pytest.raises(RuntimeError) as caught:
        worker.Call('target', 'Callable')
    assert (caught.value.__cause__ is error)
    MUT.wait.assert_not_called()
    proc.terminate.assert_called_once_with()
    proc.join.assert_called_once_with(1)
    proc.close.assert_called_once_with()
    context.Pipe.return_value[0].close.assert_called_once_with()


def test_Call_ImportsSpawnInFreshInterpreter() -> None:
    sourceRoot = Path(MUT.__file__).resolve().parents[2]
    code = (
        'from pathlib import Path\n'
        'import sys\n'
        f'sys.path.insert(0, {str(sourceRoot)!r})\n'
        'import libs.Utils.LibInVenv as MUT\n'
        f'assert str(Path(MUT.__file__).resolve()) == {MUT.__file__!r}\n'
        'originalExecutable = MUT.spawn.get_executable()\n'
        'result = MUT.ModInVenv(Path(sys.prefix)).Call('
        '"builtins", "str", None, 42)\n'
        'assert MUT.spawn.get_executable() == originalExecutable\n'
        'print(result)\n'
    )
    result = subprocess.run(
        [sys.executable, '-B', '-c', code],
        capture_output=True, text=True, check=True,
    )
    assert (result.stdout == '42\n')
    assert (result.stderr == '')


class FalseReceiveError(Exception):
    '''A reconstruction error whose truth value is false.'''

    def __bool__(self) -> bool:
        return False


@pytest.fixture(name='workerContext')
def WorkerContext(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[MUT.ModInVenv, mock.MagicMock]:
    context = mock.MagicMock()
    context.Pipe.return_value = (mock.MagicMock(), mock.MagicMock())
    context.Process.return_value.exitcode = 0
    context.Process.return_value.is_alive.return_value = False
    Utils.AutoPatch(monkeypatch, MUT.mp, 'get_context')
    Utils.AutoPatch(monkeypatch, MUT.mp, 'set_executable')
    Utils.AutoPatch(monkeypatch, MUT.spawn, 'get_executable')
    Utils.AutoPatch(monkeypatch, MUT, 'wait')
    MUT.mp.get_context.return_value = context
    MUT.spawn.get_executable.return_value = b'/previous/python'
    return (MUT.ModInVenv(tmp_path), context)


@pytest.mark.parametrize('payload', [None, 42, ('ordinary', 'tuple')])
def test_Call_ReturnsTaggedValueAndReapsChild(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
    payload: object,
) -> None:
    (worker, context) = workerContext
    (rcvPipe, sndPipe) = context.Pipe.return_value
    proc = context.Process.return_value
    rcvPipe.recv.return_value = (MUT.ExitType.RETURN, payload)

    assert (worker.Call('target', 'Callable') is payload)

    MUT.mp.get_context.assert_called_once_with('spawn')
    context.Pipe.assert_called_once_with(duplex=False)
    proc.start.assert_called_once_with()
    MUT.wait.assert_called_once_with([proc.sentinel])
    proc.join.assert_called_once_with()
    proc.close.assert_called_once_with()
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()
    rcvPipe.close.assert_called_once_with()
    assert sndPipe.close.called
    assert (MUT.mp.set_executable.call_args_list == [
        mock.call(str(worker.pyPath)), mock.call(b'/previous/python'),
    ])


@pytest.mark.parametrize('failure', ['none', 'start', 'receive'])
def test_Call_RestoresEnvironmentAndExecutableAfterStartup(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    (worker, context) = workerContext
    (rcvPipe, sndPipe) = context.Pipe.return_value
    proc = context.Process.return_value
    monkeypatch.setenv('UNIT_VENV_REMOVE', 'original')
    monkeypatch.delenv('UNIT_VENV_MISSING', raising=False)
    monkeypatch.setenv('UNIT_VENV_VALUE', 'original')
    monkeypatch.setenv('UNIT_VENV_EMPTY', 'original')
    beforeEnv = dict(os.environ)
    error = OSError('injected startup failure')

    def Start() -> None:
        assert ('UNIT_VENV_REMOVE' not in os.environ)
        assert ('UNIT_VENV_MISSING' not in os.environ)
        assert (os.environ['UNIT_VENV_VALUE'] == '42')
        assert (os.environ['UNIT_VENV_EMPTY'] == '')
        if (failure == 'start'): raise error

    proc.start.side_effect = Start
    rcvPipe.recv.return_value = (MUT.ExitType.RETURN, None)
    if (failure == 'receive'): rcvPipe.recv.side_effect = error
    envUpd = {
        'UNIT_VENV_REMOVE': None, 'UNIT_VENV_MISSING': None,
        'UNIT_VENV_VALUE': 42, 'UNIT_VENV_EMPTY': '',
    }
    if (failure == 'none'):
        assert (worker.Call('target', 'Callable', envUpd) is None)
    else:
        errorType = OSError if (failure == 'start') else RuntimeError
        with pytest.raises(errorType) as raised:
            worker.Call('target', 'Callable', envUpd)
        if (failure == 'start'): assert (raised.value is error)
        else: assert (raised.value.__cause__ is error)

    assert (dict(os.environ) == beforeEnv)
    assert (MUT.mp.set_executable.call_args_list == [
        mock.call(str(worker.pyPath)), mock.call(b'/previous/python'),
    ])
    proc.close.assert_called_once_with()
    rcvPipe.close.assert_called_once_with()
    assert sndPipe.close.called


def test_Call_RestoresEnvironmentAndExecutableAfterUpdateFailure(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
) -> None:
    (worker, context) = workerContext
    (rcvPipe, sndPipe) = context.Pipe.return_value
    proc = context.Process.return_value
    error = ValueError('synthetic environment conversion failure')
    value = mock.MagicMock()
    value.__str__.side_effect = error
    beforeEnv = dict(os.environ)

    with pytest.raises(ValueError) as raised:
        worker.Call('target', 'Callable', {
            'UNIT_VENV_FIRST': 'temporary', 'UNIT_VENV_BAD': value,
        })

    assert (raised.value is error)
    assert (dict(os.environ) == beforeEnv)
    assert (MUT.mp.set_executable.call_args_list == [
        mock.call(str(worker.pyPath)), mock.call(b'/previous/python'),
    ])
    proc.start.assert_not_called()
    proc.close.assert_called_once_with()
    rcvPipe.close.assert_called_once_with()
    sndPipe.close.assert_called_once_with()


@pytest.mark.parametrize('killRequired', [False, True])
def test_Call_CleansUpLiveChildAfterReceiveFailure(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
    killRequired: bool,
) -> None:
    (worker, context) = workerContext
    proc = context.Process.return_value
    proc.is_alive.side_effect = [True, killRequired, False]
    context.Pipe.return_value[0].recv.side_effect = EOFError('no response')

    with pytest.raises(RuntimeError) as raised:
        worker.Call('target', 'Callable')

    assert isinstance(raised.value.__cause__, EOFError)
    proc.terminate.assert_called_once_with()
    assert (proc.join.call_args_list == (
        [mock.call(1)] * (2 if killRequired else 1)
    ))
    if killRequired: proc.kill.assert_called_once_with()
    else: proc.kill.assert_not_called()
    proc.close.assert_called_once_with()

    MUT.wait.assert_not_called()


@pytest.mark.parametrize('exitType', [MUT.ExitType.EXCEPTION, 'unknown'])
def test_Call_RejectsInvalidTaggedResponse(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
    exitType: object,
) -> None:
    (worker, context) = workerContext
    context.Pipe.return_value[0].recv.return_value = (exitType, 42)

    with pytest.raises(RuntimeError, match='42'):
        worker.Call('target', 'Callable')

    context.Process.return_value.join.assert_called_once_with()
    context.Process.return_value.close.assert_called_once_with()


@pytest.mark.parametrize('error', [
    ValueError('original target error', 7), SystemExit(3),
])
def test_Call_RaisesTransportedExceptionAfterNormalExit(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
    error: BaseException,
) -> None:
    (worker, context) = workerContext
    rcvPipe = context.Pipe.return_value[0]
    error.add_note('original remote traceback')
    rcvPipe.recv.return_value = (MUT.ExitType.EXCEPTION, error)

    with pytest.raises(type(error)) as raised:
        worker.Call('target', 'Callable')

    assert (raised.value is error)
    assert (raised.value.__notes__ == ['original remote traceback'])
    context.Process.return_value.join.assert_called_once_with()
    context.Process.return_value.close.assert_called_once_with()


@pytest.mark.parametrize('error', [
    EOFError('missing response'), FalseReceiveError('reconstruction failed'),
    ModuleNotFoundError('child-only class'),
])
@pytest.mark.parametrize('exitStat', [0, 32])
def test_Call_ReceiveErrorRetainsCauseAndReapsChild(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
    error: Exception,
    exitStat: int,
) -> None:
    (worker, context) = workerContext
    rcvPipe = context.Pipe.return_value[0]
    proc = context.Process.return_value
    proc.exitcode = exitStat
    rcvPipe.recv.side_effect = error

    with pytest.raises(
        RuntimeError, match=f'exit status: {exitStat}'
    ) as raised:
        worker.Call('target', 'Callable')

    assert (raised.value.__cause__ is error)
    MUT.wait.assert_not_called()
    proc.join.assert_not_called()
    proc.close.assert_called_once_with()
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()
    rcvPipe.close.assert_called_once_with()


@pytest.mark.parametrize('payload', [42, ValueError('sent before crash')])
def test_Call_AbnormalExitRejectsResponseWithoutInvalidCause(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
    payload: object,
) -> None:
    (worker, context) = workerContext
    rcvPipe = context.Pipe.return_value[0]
    proc = context.Process.return_value
    proc.exitcode = 9
    exitType = (
        MUT.ExitType.EXCEPTION if isinstance(payload, BaseException)
        else MUT.ExitType.RETURN
    )
    rcvPipe.recv.return_value = (exitType, payload)

    with pytest.raises(RuntimeError, match='exit status: 9') as raised:
        worker.Call('target', 'Callable')

    assert (str(payload) in str(raised.value))
    expectedCause = payload if isinstance(payload, BaseException) else None
    assert (raised.value.__cause__ is expectedCause)
    proc.join.assert_called_once_with()
    proc.close.assert_called_once_with()
