'''Run standalone tools inside their own Python virtual environments.'''
from pathlib import Path
import os
from typing import Any, Mapping, Optional, Sequence, Union

from libs.Utils.CmdExec import CmdExec, CmdRes


def ResolvePython(
    root: Union[str, Path],
    python: Optional[Union[str, Path]] = None,
) -> Path:
    '''
    Resolve the interpreter for a tool rooted at `root`.

    Prefer an explicit `python` path. Otherwise look for
    `<root>/.venv/bin/python` and `<root>/.venv/Scripts/python.exe`.

    :param root:    Tool installation / checkout directory.
    :param python:  Optional explicit interpreter path.
    :return:        Absolute path to the tool's Python interpreter.
    :raises FileNotFoundError:
        If neither `python` nor a `.venv` interpreter under `root` exists.
    '''
    if (python is not None):
        path = Path(python).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(
                f'Tool Python interpreter does not exist: {path}'
            )
        return path

    rootPath = Path(root).expanduser().resolve()
    candidates = (
        rootPath / '.venv' / 'bin' / 'python',
        rootPath / '.venv' / 'Scripts' / 'python.exe',
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        f'No tool venv interpreter found under `{rootPath}/.venv`. '
        'Run `scripts/setup-tool-venvs.sh` or set an explicit python path.'
    )


def RunInVenv(
    root: Union[str, Path],
    argv: Sequence[str],
    *,
    python: Optional[Union[str, Path]] = None,
    envVars: Optional[Mapping[str, Any]] = None,
    cwd: Optional[Union[str, Path]] = None,
    cmdExec: Optional[CmdExec] = None,
    warnOnFail: bool = True,
) -> CmdRes:
    '''
    Execute `argv` with the tool venv interpreter under `root`.

    The command becomes `[python, *argv]`. Environment overrides in `envVars`
    are merged into a copy of the current process environment (`None` values
    remove keys). Non-zero exits raise `RuntimeError` after returning the
    captured result metadata in the exception message.

    :param root:        Tool installation / checkout directory.
    :param argv:        Arguments after the interpreter (script, `-c`, …).
    :param python:      Optional explicit interpreter path.
    :param envVars:     Extra Env. Variables (`None` value unsets a key).
    :param cwd:         Optional working directory for the subprocess.
    :param cmdExec:     Optional `CmdExec` instance (default: new instance).
    :param warnOnFail:  Forwarded to `CmdExec.Run`.
    :return:            Successful `CmdRes` from the subprocess.
    :raises RuntimeError:
        If the subprocess exits non-zero or cannot be started.
    '''
    interpreter = ResolvePython(root, python)
    command = [str(interpreter), *[str(arg) for arg in argv]]
    env = dict(os.environ)
    for (key, val) in (envVars or {}).items():
        if (val is None): env.pop(str(key), None)
        else: env[str(key)] = str(val)

    spPars: dict[str, Any] = {'env': env}
    if (cwd is not None): spPars['cwd'] = str(Path(cwd))

    executor = cmdExec if (cmdExec is not None) else CmdExec()
    result = executor.Run(command, warnOnFail=warnOnFail, spPars=spPars)
    if not result.success:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(
            f'Tool command failed (exit {result.exitCode}) in venv '
            f'`{interpreter}`: {command!r}'
            + (f': {detail}' if detail else '')
        )
    return result
