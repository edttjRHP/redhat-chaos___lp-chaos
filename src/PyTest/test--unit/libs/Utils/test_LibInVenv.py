from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Optional
from unittest import mock

import pytest

import libs.Utils.LibInVenv as MUT


class UnitPath(PurePosixPath):
    '''Use pure path operations without filesystem access.'''

    def expanduser(self) -> 'UnitPath':
        return self

    def resolve(self) -> 'UnitPath':
        return self


class FalseReceiveError(Exception):
    '''An exception whose truth value is false.'''

    def __bool__(self) -> bool:
        return False


@pytest.fixture(name='workerContext')
def WorkerContext(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MUT.ModInVenv, mock.MagicMock]:
    context = mock.MagicMock()
    context.Pipe.return_value = (mock.MagicMock(), mock.MagicMock())
    context.Process.return_value.exitcode = 0
    context.Process.return_value.is_alive.return_value = False

    mp = mock.Mock()
    mp.get_context.return_value = context
    spawn = mock.Mock()
    spawn.get_executable.return_value = b'/previous/python'
    tempEnv = mock.MagicMock()
    environment = {}

    monkeypatch.setattr(MUT, 'mp', mp)
    monkeypatch.setattr(MUT, 'spawn', spawn)
    monkeypatch.setattr(MUT, 'wait', mock.Mock())
    monkeypatch.setattr(MUT, 'os', SimpleNamespace(
        name='posix', environ=environment,
    ))
    monkeypatch.setattr(MUT, 'Path', UnitPath)
    monkeypatch.setattr(MUT, 'sys', SimpleNamespace(
        version_info=(3, 11, 8, 'final', 0),
    ))
    monkeypatch.setattr(MUT, 'TempEnv', mock.Mock(return_value=tempEnv))
    monkeypatch.setattr(MUT, 'UpdEnv', mock.Mock())
    monkeypatch.setattr(MUT, '_procLock', mock.MagicMock())
    monkeypatch.setattr(MUT, 'Event', mock.Mock())
    monkeypatch.setattr(MUT, 'Thread', mock.Mock())
    monkeypatch.setattr(MUT, 'time', SimpleNamespace(monotonic=mock.Mock()))

    return (
        MUT.ModInVenv('/unit/venv', importPaths=('/unit/import',)),
        context,
    )


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


@pytest.mark.parametrize('execTO', [None, 10])
def test_Call_UsesExpectedSpawnConfig(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
    execTO: Optional[int],
) -> None:
    (worker, context) = workerContext
    (rcvPipe, sndPipe) = context.Pipe.return_value
    proc = context.Process.return_value
    worker = MUT.ModInVenv(
        worker.venvRoot,
        importPaths=('/unit/first', '/unit/second'),
        execTO=execTO,
    )
    rcvPipe.recv.return_value = (MUT.ExitType.RETURN, 42)

    if (execTO is not None):
        MUT.time.monotonic.side_effect = [100, 101, 102]
        MUT.Event.return_value.wait.return_value = True
        MUT.Thread.return_value.start.side_effect = (
            lambda: MUT.Thread.call_args.kwargs['target']()
        )
        MUT.wait.return_value = [proc.sentinel]

    assert (worker.Call(
        'target', 'Callable', None, 'positional', option=True,
    ) == 42)

    MUT.mp.get_context.assert_called_once_with('spawn')
    context.Pipe.assert_called_once_with(duplex=False)
    assert (context.Process.call_args.kwargs == {
        'target': MUT._CallInVenv,
        'args': (
            sndPipe, 'target', 'Callable', ('positional',), {'option': True},
            ('/unit/first', '/unit/second'), '/unit/venv',
            (3, 11, 8, 'final', 0),
        ),
    })
    proc.start.assert_called_once_with()
    proc.close.assert_called_once_with()
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()
    rcvPipe.close.assert_called_once_with()
    assert sndPipe.close.called
    MUT.spawn.get_executable.assert_called_once_with()
    assert (MUT.mp.set_executable.call_args_list == [
        mock.call('/unit/venv/bin/python'), mock.call(b'/previous/python'),
    ])

    if (execTO is None):
        MUT.Thread.assert_not_called()
        MUT.Event.assert_called_once_with()
        MUT.Event.return_value.wait.assert_not_called()
        MUT.Event.return_value.set.assert_called_once_with()
        MUT.wait.assert_called_once_with([proc.sentinel])
        proc.join.assert_called_once_with()
    else:
        MUT.Thread.assert_called_once()
        assert (MUT.Thread.call_args.kwargs['daemon'] is True)
        MUT.Event.return_value.wait.assert_called_once_with(9)
        MUT.Event.return_value.set.assert_called_once_with()
        MUT.wait.assert_called_once_with([proc.sentinel], 8)
        proc.join.assert_called_once_with(0)


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
    assert (context.Process.call_args.kwargs['args'][-1] == tuple(
        MUT.sys.version_info
    ))
    proc.start.assert_called_once_with()
    MUT.wait.assert_called_once_with([proc.sentinel])
    proc.join.assert_called_once_with()
    proc.close.assert_called_once_with()
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()
    rcvPipe.close.assert_called_once_with()
    assert sndPipe.close.called
    assert (MUT.mp.set_executable.call_args_list == [
        mock.call('/unit/venv/bin/python'), mock.call(b'/previous/python'),
    ])


@pytest.mark.parametrize('failure', ['none', 'start', 'receive'])
def test_Call_ClosesTemporaryEnvironmentAndRestoresExecutable(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
    failure: str,
) -> None:
    (worker, context) = workerContext
    (rcvPipe, sndPipe) = context.Pipe.return_value
    proc = context.Process.return_value
    error = OSError('injected startup failure')
    envUpd = {
        'UNIT_VENV_REMOVE': None, 'UNIT_VENV_MISSING': None,
        'UNIT_VENV_VALUE': 42, 'UNIT_VENV_EMPTY': '',
    }

    if (failure == 'start'): proc.start.side_effect = error
    else:
        rcvPipe.recv.return_value = (MUT.ExitType.RETURN, None)
        if (failure == 'receive'): rcvPipe.recv.side_effect = error

    if (failure == 'none'):
        assert (worker.Call('target', 'Callable', envUpd) is None)
    else:
        errorType = OSError if (failure == 'start') else RuntimeError
        with pytest.raises(errorType) as raised:
            worker.Call('target', 'Callable', envUpd)
        if (failure == 'start'): assert (raised.value is error)
        else: assert (raised.value.__cause__ is error)

    MUT.TempEnv.assert_called_once_with()
    MUT.TempEnv.return_value.__enter__.assert_called_once_with()
    MUT.TempEnv.return_value.__exit__.assert_called_once()
    MUT.UpdEnv.assert_called_once_with(envUpd)
    assert (MUT.mp.set_executable.call_args_list == [
        mock.call('/unit/venv/bin/python'), mock.call(b'/previous/python'),
    ])
    proc.close.assert_called_once_with()
    rcvPipe.close.assert_called_once_with()
    assert sndPipe.close.called


def test_Call_RestoresExecutableAfterEnvironmentUpdateFailure(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
) -> None:
    (worker, context) = workerContext
    (rcvPipe, sndPipe) = context.Pipe.return_value
    proc = context.Process.return_value
    error = ValueError('synthetic environment update failure')
    envUpd = {'UNIT_VENV_FIRST': 'temporary', 'UNIT_VENV_BAD': 'invalid'}
    MUT.UpdEnv.side_effect = error

    with pytest.raises(ValueError) as raised:
        worker.Call('target', 'Callable', envUpd)

    assert (raised.value is error)
    MUT.TempEnv.assert_called_once_with()
    MUT.TempEnv.return_value.__enter__.assert_called_once_with()
    MUT.TempEnv.return_value.__exit__.assert_called_once()
    MUT.UpdEnv.assert_called_once_with(envUpd)
    proc.start.assert_not_called()
    assert (MUT.mp.set_executable.call_args_list == [
        mock.call('/unit/venv/bin/python'), mock.call(b'/previous/python'),
    ])
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

    assert (isinstance(raised.value.__cause__, EOFError))
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
        MUT.ExitType.EXCEPTION if (isinstance(payload, BaseException))
        else MUT.ExitType.RETURN
    )
    rcvPipe.recv.return_value = (exitType, payload)

    with pytest.raises(RuntimeError, match='exit status: 9') as raised:
        worker.Call('target', 'Callable')

    assert (str(payload) in str(raised.value))
    expectedCause = payload if (isinstance(payload, BaseException)) else None
    assert (raised.value.__cause__ is expectedCause)
    proc.join.assert_called_once_with()
    proc.close.assert_called_once_with()


@pytest.mark.parametrize('execTO', [
    0, -1, False, True, 0.2, 1.0, '10',
    float('nan'), float('inf'), float('-inf'),
])
def test_Init_RejectsInvalidExecutionBudget(execTO: object) -> None:
    with pytest.raises(
        ValueError, match='The `execTO` must be a positive integer',
    ):
        MUT.ModInVenv('/unit/venv', execTO=execTO)


@pytest.fixture(name='boundedWorker')
def BoundedWorker(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
) -> tuple[MUT.ModInVenv, mock.MagicMock]:
    '''Provide deterministic response and exit waits for a bounded worker.'''
    (worker, context) = workerContext
    proc = context.Process.return_value
    MUT.time.monotonic.side_effect = [100, 102, 106]
    MUT.Event.return_value.wait.return_value = True
    MUT.Thread.return_value.start.side_effect = (
        lambda: MUT.Thread.call_args.kwargs['target']()
    )
    MUT.wait.return_value = [proc.sentinel]
    return (MUT.ModInVenv(worker.venvRoot, execTO=10), context)


@pytest.mark.parametrize('payload', [None, 42, ValueError('target error')])
def test_Call_SharesBudgetAcrossResponseAndExit(
    boundedWorker: tuple[MUT.ModInVenv, mock.MagicMock],
    payload: object,
) -> None:
    (worker, context) = boundedWorker
    proc = context.Process.return_value
    exitType = (
        MUT.ExitType.EXCEPTION if (isinstance(payload, BaseException))
        else MUT.ExitType.RETURN
    )
    context.Pipe.return_value[0].recv.return_value = (exitType, payload)

    if (isinstance(payload, BaseException)):
        with pytest.raises(type(payload)) as raised:
            worker.Call('target', 'Callable')
        assert (raised.value is payload)
    else:
        assert (worker.Call('target', 'Callable') is payload)

    MUT.Event.return_value.wait.assert_called_once_with(8)
    MUT.Event.return_value.set.assert_called_once_with()
    MUT.wait.assert_called_once_with([proc.sentinel], 4)
    proc.join.assert_called_once_with(0)
    proc.close.assert_called_once_with()
    proc.terminate.assert_not_called()
    assert (MUT.Thread.call_args.kwargs['daemon'] is True)


@pytest.mark.parametrize('phase', ['response', 'exit', 'startup'])
@pytest.mark.parametrize('killRequired', [False, True])
def test_Call_TimeoutTerminatesAndReapsWorker(
    boundedWorker: tuple[MUT.ModInVenv, mock.MagicMock],
    phase: str,
    killRequired: bool,
) -> None:
    (worker, context) = boundedWorker
    (rcvPipe, sndPipe) = context.Pipe.return_value
    proc = context.Process.return_value
    proc.is_alive.side_effect = [True, killRequired, False]
    rcvPipe.recv.return_value = (MUT.ExitType.RETURN, None)
    if (phase == 'startup'): MUT.time.monotonic.side_effect = [100, 111]
    elif (phase == 'response'):
        MUT.Thread.return_value.start.side_effect = None
        MUT.Event.return_value.wait.return_value = False
    else: MUT.wait.return_value = []

    with pytest.raises(TimeoutError, match='timed out'):
        worker.Call('target', 'Callable')

    proc.terminate.assert_called_once_with()
    assert (proc.join.call_args_list == (
        [mock.call(1)] * (2 if killRequired else 1)
    ))
    if killRequired: proc.kill.assert_called_once_with()
    else: proc.kill.assert_not_called()
    proc.close.assert_called_once_with()
    rcvPipe.close.assert_called_once_with()
    assert sndPipe.close.called
    if (phase != 'exit'): MUT.wait.assert_not_called()
    if (phase == 'startup'): MUT.Event.return_value.wait.assert_not_called()
    if (phase == 'response'): rcvPipe.recv.assert_not_called()
    assert (MUT.mp.set_executable.call_args_list == [
        mock.call('/unit/venv/bin/python'), mock.call(b'/previous/python'),
    ])


@pytest.mark.parametrize('error', [
    FalseReceiveError('reconstruction failed'), KeyboardInterrupt(),
])
def test_Call_BoundedReceivePreservesExceptions(
    boundedWorker: tuple[MUT.ModInVenv, mock.MagicMock],
    error: BaseException,
) -> None:
    (worker, context) = boundedWorker
    context.Pipe.return_value[0].recv.side_effect = error
    errorType = RuntimeError if (isinstance(error, Exception)) else type(error)

    with pytest.raises(errorType) as raised:
        worker.Call('target', 'Callable')

    if (isinstance(error, Exception)): assert (raised.value.__cause__ is error)
    else: assert (raised.value is error)
    MUT.wait.assert_not_called()
    context.Process.return_value.close.assert_called_once_with()


def test_Call_TargetExecutionTimeoutKeywordIsNotIntercepted(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
) -> None:
    (worker, context) = workerContext
    context.Pipe.return_value[0].recv.return_value = (MUT.ExitType.RETURN, 42)

    assert (worker.Call('target', 'Callable', execTO=123) == 42)
    assert (context.Process.call_args.kwargs['args'][4] == {'execTO': 123})


def test_Call_BoundedReceiveTimeoutWhenReceiverDoesNotSignal(
    workerContext: tuple[MUT.ModInVenv, mock.MagicMock],
) -> None:
    (worker, context) = workerContext
    (rcvPipe, sndPipe) = context.Pipe.return_value
    proc = context.Process.return_value
    MUT.time.monotonic.side_effect = [100, 102]
    MUT.Event.return_value.wait.return_value = False
    MUT.Thread.return_value.start.side_effect = None
    proc.is_alive.side_effect = [True, False, False]

    with pytest.raises(TimeoutError):
        MUT.ModInVenv(worker.venvRoot, execTO=10).Call(
            'target', 'Callable',
        )

    MUT.Thread.assert_called_once()
    assert (MUT.Thread.call_args.kwargs['daemon'] is True)
    MUT.Thread.return_value.start.assert_called_once_with()
    MUT.Event.return_value.wait.assert_called_once_with(8)
    rcvPipe.recv.assert_not_called()
    MUT.wait.assert_not_called()
    proc.terminate.assert_called_once_with()
    proc.close.assert_called_once_with()
    rcvPipe.close.assert_called_once_with()
    assert sndPipe.close.called
