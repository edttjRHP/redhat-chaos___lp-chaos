'''
Call an importable Module Function using the same exact Version CPython
VirtualEnv.

This approach is meant to solve conflict of Python external packages (installed
via `pip`) between different external (vendor) library. It's NOT meant to allow
using different version of Python Interpreter.

This module relies on `multiprocessing.spawn.spawn_main()` behavior, as
essentially the child is invoked as below:
```python
python -c '
    from multiprocessing.spawn import spawn_main; spawn_main(...)
' --multiprocessing-fork
```

Use `python -m pytest ...`, not `pytest ...`, to avoid replaying the console
script before the worker resets its env. Exact interpreter versions are checked
via sister module `./LibInVenv_Worker.py`.

NOTE:
    The package isolation is NOT guaranteed 100%. Some packages may be imported
    from parent's env. during bootstrap phase.
'''
import multiprocessing as mp
from multiprocessing import spawn
from multiprocessing.connection import wait
import os
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time
from typing import Any, Optional

from libs.CustomTypes import TempEnv
from libs.Utils.DynLoad import UpdEnv
from libs.Utils.LibInVenv_Worker import ExitType, _CallInVenv


_procLock = Lock()


class ModInVenv:
    '''
    Run Module Callable without activating a vEnv in the caller.

    Arguments, return value and exceptions must be pickleable and their classes
    importable in the receiving process.
    '''
    def __init__(
        self,
        venvRoot: Path,
        importPaths: tuple = tuple(),
        execTO: Optional[int] = None,
    ) -> None:
        '''
        Resolve the vEnv CPython interpreter.

        :param venvRoot:    Root of the target Python virtual environment.
        :param importPaths: Extra module lookup paths.
        :param execTO:
            Module execution budget in sec. (default: `None`, no limit).

        :raises ValueError: If execTO is invalid.
        '''
        if (
            (execTO is not None) and (
                not isinstance(execTO, int) or
                isinstance(execTO, bool) or
                (execTO <= 0)
            )
        ): raise ValueError('The `execTO` must be a positive integer.')
        self.execTO = execTO
        self.venvRoot = Path(venvRoot).expanduser().resolve()
        self.pyPath = self.venvRoot / (
            'Scripts/python.exe' if (os.name == 'nt') else 'bin/python'
        )
        self.importPaths = tuple(
            str(Path(root).expanduser().resolve()) for root in importPaths
        )

    def Call(
        self,
        modName: str,
        funcName: str,
        envUpd: Optional[dict] = None,
        *args: Any,
        **kwArgs: Any,
    ) -> Any:
        '''
        Return the target Module Callable return value or raise its exception.

        Transportable exceptions retain their type and a remote traceback
        note. An exception that cannot be transported or reconstructed raises
        `RuntimeError` instead.

        :param modName:     Module to import in the selected vEnv.
        :param funcName:
            Callable attribute to invoke in the requested Module.
        :param envUpd:
            A `dict` containing Env. Variables to be set or removed (set the
            value to `None`) in the child process.
        :param args:        Positional arguments for the target Callable.
        :param kwArgs:      Keyword arguments for the target Callable.

        :return:    The target Callable return value.

        :raises BaseException:
            Target or result-serialization exception, with a traceback note.
        :raises RuntimeError:
            Exception transport/reconstruction failure, abnormal exit or
            missing response.
        :raises TimeoutError:   If the worker execution budget expires.
        '''
        mpCtx = mp.get_context('spawn')
        (rcvPipe, sndPipe) = mpCtx.Pipe(duplex=False)
        proc = mpCtx.Process(
            target=_CallInVenv,
            args=(
                sndPipe,
                modName, funcName, args, kwArgs,
                self.importPaths,
                str(self.venvRoot),
                tuple(sys.version_info),
            ),
        )
        deadline = None
        toMsg = f'Venv Worker execution timed out after {self.execTO}s.'
        try:
            with _procLock:
                origPyBin = spawn.get_executable()
                try:
                    mp.set_executable(str(self.pyPath))
                    with TempEnv():
                        UpdEnv(envUpd or {})
                        if (self.execTO is not None):
                            deadline = (time.monotonic() + self.execTO)
                        proc.start()
                finally:
                    mp.set_executable(origPyBin)
            sndPipe.close()
            exitType = exitObj = rcvErr = None
            received = Event()

            # The receiver writes first; Event signals completion.
            def Receive() -> None:
                nonlocal exitType, exitObj, rcvErr
                try: (exitType, exitObj) = rcvPipe.recv()
                except BaseException as error: rcvErr = error
                finally: received.set()

            if (deadline is None): Receive()
            else:
                Thread(target=Receive, daemon=True).start()
                remTO = (deadline - time.monotonic())
                if ((remTO <= 0) or not received.wait(remTO)):
                    raise TimeoutError(toMsg)
            if (rcvErr is not None):
                if not isinstance(rcvErr, Exception): raise rcvErr
                raise RuntimeError(
                    'Venv Worker tagged response is missing '
                    f'(exit status: {proc.exitcode}).'
                ) from rcvErr
            if (deadline is None): wait([proc.sentinel])
            else:
                remTO = (deadline - time.monotonic())
                if ((remTO <= 0) or not wait([proc.sentinel], remTO)):
                    raise TimeoutError(toMsg)
            with _procLock:
                if (deadline is None): proc.join()
                else: proc.join(0)
                exitStat = proc.exitcode
            if exitStat:
                raise RuntimeError(
                    f'Venv Worker exit status: {exitStat}\n'
                    f'{exitObj if (exitObj is not None) else ""}'
                ) from (
                    exitObj if isinstance(exitObj, BaseException) else None
                )
            if (exitType is ExitType.EXCEPTION):
                if (not isinstance(exitObj, BaseException)):
                    raise RuntimeError(
                        f'Venv Worker exit status: {exitStat}\n{exitObj}'
                    )
                raise exitObj
            if (exitType is not ExitType.RETURN):
                raise RuntimeError(
                    f'Venv Worker returned no result: {exitObj}'
                )
            return exitObj
        finally:
            sndPipe.close()
            with _procLock:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(1)
                if proc.is_alive():
                    proc.kill()
                    proc.join(1)
                if not proc.is_alive(): proc.close()
            rcvPipe.close()
