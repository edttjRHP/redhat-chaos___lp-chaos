'''
Call an importable Module Function using the same Major/Minor Version CPython
VirtualEnv.
'''
import multiprocessing as mp
from multiprocessing import spawn
from multiprocessing.connection import wait
import os
from pathlib import Path
import sys
from threading import Lock
from typing import Any, Optional

from libs.CustomTypes import TempEnv
from libs.Utils.DynLoad import UpdEnv
from libs.Utils.LibInVenv_Worker import ExitType, _CallInVenv


_START_LOCK = Lock()


class ModInVenv:
    '''
    Run Module Callable without activating a vEnv in the caller.

    Arguments, return value and exceptions must be pickleable and their classes
    importable in the receiving process.
    '''
    def __init__(self, venvRoot: Path, importPaths: tuple = tuple()) -> None:
        '''
        Resolve the vEnv CPython interpreter.

        :param venvRoot:    Root of the target Python virtual environment.
        :param importPaths: Extra module lookup paths.
        '''
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
            A `dict` containing Env. Variables to bet set or removed (set the
            value to `None`) in the child process.
        :param args:        Positional arguments for the target Callable.
        :param kwArgs:      Keyword arguments for the target Callable.

        :return:    The target Callable return value.

        :raises BaseException:
            Target or result-serialization exception, with a traceback note.
        :raises RuntimeError:
            Exception transport/reconstruction failure, abnormal exit or
            missing response.
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
                tuple(sys.version_info[:2]),
            ),
        )
        try:
            with _START_LOCK:
                origPyBin = spawn.get_executable()
                try:
                    mp.set_executable(str(self.pyPath))
                    with TempEnv():
                        UpdEnv(envUpd or {})
                        proc.start()
                finally:
                    mp.set_executable(origPyBin)
            sndPipe.close()
            exitObj = rcvErr = None
            try: (exitType, exitObj) = rcvPipe.recv()
            except Exception as ex: rcvErr= ex
            if (rcvErr is not None):
                raise RuntimeError(
                    'Venv Worker tagged response is missing '
                    f'(exit status: {proc.exitcode}).'
                ) from rcvErr
            wait([proc.sentinel])
            with _START_LOCK:
                proc.join()
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
            with _START_LOCK:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(1)
                if proc.is_alive():
                    proc.kill()
                    proc.join(1)
                if not proc.is_alive(): proc.close()
            rcvPipe.close()
