'''Sourcing Shell Script and get the modified Env. Variables.'''
from collections.abc import Iterable
from enum import Enum, auto
import json
import os
import shlex
import subprocess
import sys
from typing import Optional, TextIO, Union
from pathlib import Path

from libs.Utils.DynLoad import UpdEnv


class ShellKind(Enum):
    BASH = auto()


class BashEnv:
    '''Shell Env. Handler for Bash.'''
    def __init__(self, pyBin: Optional[str] = None) -> None:
        '''
        Start a persistent Bash process for Env. Variables snapshots.

        :param pyBin:       Python executable (default: current interpreter) to
                            execute snapshot script.

        :raises OSError:    Bash could not be started.
        '''
        self.pyBin = pyBin or sys.executable
        self.proc = subprocess.Popen(
            ['bash', '--noprofile', '--norc'],
            bufsize=1,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
        )

    def Snapshot(self) -> dict[str, str]:
        '''
        Read the current Shell process Env. Variables via a JSON snapshot.

        :return:                Env. Variable names and values.

        :raises RuntimeError:   Bash exited before returning a snapshot.
        :raises ValueError:     The snapshot is not valid JSON.
        '''
        pyCode = (
            'import json, os, sys; '
            'json.dump(dict(os.environ), sys.stdout); '
            'sys.stdout.write("\\n")'
        )

        self.proc.stdin.write(
            f'{shlex.quote(self.pyBin)} -c {shlex.quote(pyCode)}\n'
        )
        self.proc.stdin.flush()

        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(
                'Bash exited unexpectedly with exit status: '
                f'{self.proc.poll()}'
            )

        return json.loads(line)

    def Source(self, envFile: Union[str, Path, TextIO]) -> None:
        '''
        Source a file or execute a readable text stream in this Shell.

        Send STDOUT prints, produced by `envFile`, to STDERR, so it does not
        corrupt the one-JSON-document-per-line protocol on STDOUT.

        If sourcing fails, terminate Bash. The next `BashEnv.Snapshot()` then
        raises.

        :param envFile:
            Shell script path or readable text stream to set, update, and/or
            delete Env. Variables.
            Streams is wrapped into a brace group, no temporary script file is
            created.
            It may also do some File System modification, if needed.
            Any change in Shell Variables will not be effective towards this
            Python Process.

        :raises OSError:    Writing to the Bash process fails.
        '''
        if hasattr(envFile, 'read'):
            self.proc.stdin.write('{\n: # Protect from empty file.\n')
            while (chunk := envFile.read(65536)): self.proc.stdin.write(chunk)
            self.proc.stdin.write('\n} 0< /dev/null 1>&2 || exit $?\n')
        else:
            self.proc.stdin.write(
                f'source {shlex.quote(os.fspath(envFile))} 0< /dev/null 1>&2 '
                '|| exit $?\n'
            )
        self.proc.stdin.flush()

    def Close(self) -> None:
        '''
        Close Bash process, terminating it if graceful shutdown fails.

        :raises subprocess.TimeoutExpired:
            Bash is still running after retry.
        '''
        if (self.proc.poll() is not None): return
        try:
            self.proc.stdin.write('exit\n')
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.proc.terminate()
            self.proc.wait(timeout=5)


def GetEnvDelta(
    shEnvClass: Union[BashEnv],
    envFiles: Iterable[Union[str, Path, TextIO]],
) -> dict[str, Optional[str]]:
    '''
    Get the Env. Variables difference between after and before sourcing
    `envFiles`.

    :param shEnvClass:
        Shell Env. Handler class that contains `Snapshot()`, `Source()`, and
        `Close()` Methods.
    :param envFiles:
        Shell script paths or readable text streams to execute in order.

    :return:
        A `dict` containing Env. Variables are modified or unset after
        sourcing `envFiles`. Value `None` marks unset items.
    '''
    shEnv = shEnvClass()
    try:
        beforeEnv = shEnv.Snapshot()
        for envFile in envFiles: shEnv.Source(envFile)
        afterEnv = shEnv.Snapshot()
    finally:
        shEnv.Close()

    return {
        **dict.fromkeys((beforeEnv.keys() - afterEnv.keys())),
        **{
            key: value
            for (key, value) in afterEnv.items()
            if (beforeEnv.get(key) != value)
        },
    }


def DoShellSource(
    envFiles: Iterable[Union[str, Path, TextIO]],
    shellKind: ShellKind = ShellKind.BASH,
) -> None:
    '''
    Source Shell files in order, applying each delta to this process.

    Later files inherit earlier changes. Use TempEnv around this call when
    changes must be restored, including when sourcing a file raises.

    :param envFiles:
        Shell script paths or readable text streams to execute in order.
    :param shellKind:
        Shell Interpreter Kind to be used to source the `envFiles`
        (default: ShellKind.BASH).

    :raises ValueError:
        The Shell Kind is not supported.
    '''
    if (shellKind == ShellKind.BASH): shEnvClass = BashEnv
    else: raise ValueError(f'Unsupported Shell Kind: {shellKind}')

    UpdEnv(GetEnvDelta(shEnvClass, envFiles))
