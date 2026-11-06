'''Child-side module invocation using only standard-library imports.'''
from enum import auto, Enum
import importlib
from multiprocessing.connection import Connection
import os
from pathlib import Path
import site
import sys
import sysconfig
import traceback


class ExitType(Enum):
    '''Distinguish a returned value from a child exception.'''
    RETURN = auto()
    EXCEPTION = auto()


def _CallInVenv(
    sndPipe: Connection,
    modName: str,
    funcName: str,
    funcArgs: tuple,
    funcKwArgs: dict,
    importPaths: tuple,
    venvRoot: str,
    pyVer: tuple,
) -> None:
    '''
    Execute a Module Callable in separate `vEnv` context (child process).

    :param sndPipe:
        Pipe EndPoint to send the tagged response to the caller (parent).
        A `tuple` of (exitType, exitObj) will be sent back to parent upon
        completion Module Callable execution.
        When `exitType=ExitType.RETURN`, the `exitObj` is set to the return
        value of the Module Callable.
        When `exitType=ExitType.EXCEPTION`, the `exitObj` is set to the occured
        Exception.
    :param modName:     Module to import in the selected vEnv.
    :param funcName:    Callable attribute to invoke in the requested Module.
    :param funcArgs:    Positional arguments for the target Callable.
    :param funcKwArgs:  Keyword arguments for the target Callable.
    :param importPaths:
        Absolute extra module lookup paths. To be inserted in front of
        `sys.path`, before `PYTHONPATH`.
    :param venvRoot:    Expected child `sys.prefix`.
    :param pyVer:       Expected CPython Major/Minor Version pair.

    :raises SystemExit:
        The final response cannot be delivered. The child process Exit Status
        is set to 32.
    '''
    try:
        if (
            (sys.implementation.name != 'cpython') or
            (tuple(sys.version_info[:2]) != pyVer) or
            (Path(sys.prefix).resolve() != Path(venvRoot)) or
            (sys.prefix == sys.base_prefix)
        ):
            raise RuntimeError(
                'A same Major/Minor Version CPython VirtualEnv is required.'
            )
        paths = sysconfig.get_paths()
        stdlib = Path(paths['stdlib'])
        zipName = 'python{}{}.zip'.format(*sys.version_info[:2])
        # Python `multiprocessing` bootstrap the child process with parent's
        #   `sys.path` for marshalling the Funcion Arguments and Return values.
        #   Need to restore the original to maintain the vEnv isolation.
        sys.path = list(dict.fromkeys([
            *importPaths,
            *(
                os.environ['PYTHONPATH'].split(
                    os.pathsep
                ) if os.environ.get('PYTHONPATH') else []
            ),
            str(stdlib.parent / zipName),
            paths['stdlib'], paths['platstdlib'],
            (sysconfig.get_config_var('DESTSHARED') or str(stdlib / 'DLLs')),
        ]))
        for path in dict.fromkeys([paths['purelib'], paths['platlib']]):
            site.addsitedir(path)
        importlib.invalidate_caches()
        module = importlib.import_module(modName)
        result = getattr(module, funcName)(*funcArgs, **funcKwArgs)
        sndPipe.send((ExitType.RETURN, result))
    except BaseException as ex:
        rmtTrace = traceback.format_exc()
        try:
            ex.add_note(rmtTrace)
            sndPipe.send((ExitType.EXCEPTION, ex))
        except BaseException:
            try:
                sndPipe.send((ExitType.EXCEPTION, RuntimeError(
                    'Venv Worker could not transport the occured Exception:\n'
                    f'{rmtTrace}\n{traceback.format_exc()}'
                )))
            except BaseException: raise SystemExit(32)
    finally:
        sndPipe.close()
