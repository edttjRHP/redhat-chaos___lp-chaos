'''Dynamic Loading functions.'''
from pathlib import Path
import importlib.util
import runpy
import os
import re
import sys
from types import ModuleType
from typing import Any, Optional, Union
from unittest.mock import patch

from libs.CustomTypes import TempEnv


def ImportFromPath(modPath: Union[str, Path]) -> ModuleType:
    '''
    Dynamically loads a Python module from an absolute or relative file path
    without modifying `sys.path`.

    The module name encodes its resolved absolute path without the `.py`
    suffix. Runs of `-`, `.`, `_` become underscored runs of `h`, `d`, `u`,
    respectively. For ex., `--` ->  `_hh_, `_._` -> `_udu_`.

    No parent packages are created. Each call loads a fresh module, retained
    in `sys.modules` on success. A failed load restores the previous entry.

    :param modPath: Path to the `.py` module (`Path` object or `str`).

    :return:
        The loaded Python module object.

    :raises FileNotFoundError:
        If the specified `modPath` does not exist.
    :raises ImportError:
        If the module spec cannot be generated or loaded.
    '''
    path = Path(modPath).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'Target module does not exist: {path}')

    namePath = path.with_suffix('') if (path.suffix == '.py') else path
    escapes = str.maketrans('-._', 'hdu')
    modName = '.'.join(
        re.sub(
            r'[-._]+',
            lambda match: f'_{match.group().translate(escapes)}_',
            component,
        )
        for component in namePath.parts[1:]
    )
    spec = importlib.util.spec_from_file_location(modName, path)
    if ((spec is None) or (spec.loader is None)):
        raise ImportError(f'Could not build module spec. for: {path}')

    module = importlib.util.module_from_spec(spec)
    missing = object()
    prev = sys.modules.get(modName, missing)
    sys.modules[modName] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if (prev is missing): sys.modules.pop(modName, None)
        else: sys.modules[modName] = prev
        raise

    return module


def RunFromPath(
    scrPath: Union[str, Path],
    scrArgs: Optional[list[str]] = None,
    envVars: dict[str, Any] = None,
    initGlb: dict[str, Any] = None,
    runName: str = '__main__',
) -> dict[str, Any]:
    '''
    Executes a standalone `.py` script in-process without modifying `sys.path`.

    :param scrPath: Path to the `.py` script (`Path` object or `str`).
    :param scrArgs: A `list` containing script's arguments.
    :param envVars:
        A `dict` containing Env. Variables to bet set or removed (set the value
        to `None`) in the execution context.
    :param initGlb:
        Optional dictionary to inject into the script's `globals`.
    :param runName:
        The `__name__` assigned to the script during execution
        (default: '__main__').

    :return:
        The globals dictionary of the executed script after completion.

    :raises FileNotFoundError:
        If the specified `scrPath` does not exist.
    '''
    path = Path(scrPath).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'Target script does not exist: {path}')

    # Executes the file as a script and returns its resulting global namespace.
    with (
        TempEnv(),
        patch.object(
            sys,
            'argv',
            [str(path), *[str(arg) for arg in (scrArgs or [])]],
        ),
    ):
        for (key, val) in (envVars or {}).items():
            if (val is None): os.environ.pop(key, None)
            else: os.environ[key] = str(val)
        return runpy.run_path(
            str(path),
            init_globals=initGlb,
            run_name=runName,
        )
