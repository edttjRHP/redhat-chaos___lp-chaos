'''Sourcing Shell Script and get the modified Env. Variables.'''
import json
import os
import shlex
import subprocess
import sys
from typing import Optional, Union
from pathlib import Path


class BashEnv:
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
        Read the current Bash Env. Variables via a JSON snapshot.

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

    def Source(self, envFile: Union[str, Path]) -> None:
        '''
        Execute sourcing of a Bash Shell file.

        Send STDOUT prints, produced by `envFile`, to STDERR, so it does not
        corrupt the one-JSON-document-per-line protocol on STDOUT.

        If sourcing fails, terminate Bash. The next `BashEnv.Snapshot()` then
        raises.

        :param envFile:
            Bash Shell script file to set, update, and/or delete Env.
            Variables.
            It may also do some File System modification, if needed.
            Any change in Bash Shell Variables will not be effective towards
            this Python Process.

        :raises OSError:    Writing to the Bash process fails.
        '''
        self.proc.stdin.write(
            f'source {shlex.quote(os.fspath(envFile))} 0</dev/null 1>&2 || '
            'exit $?\n'
        )
        self.proc.stdin.flush()

    def Close(self) -> None:
        '''
        Close Bash, terminating it if graceful shutdown fails.

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
    envFile: Union[str, Path],
) -> tuple[set[str], dict[str, str]]:
    '''
    Get the Env. Variables difference between after and before sourcing an
    `envFile`.

    :param shEnvClass:
        Shell Env. class that contains `Snapshot()`, `Source()`, and `Close()`
        Methods.
    :param envFile:
        The Shell script file to be sourced.

    :return:    Tuple `(delEnv, updEnv)` after sourcing the `envFile`.
    '''
    shEnv = shEnvClass()
    try:
        beforeEnv = shEnv.Snapshot()
        shEnv.Source(envFile)
        afterEnv = shEnv.Snapshot()
    finally:
        shEnv.Close()

    return ((beforeEnv.keys() - afterEnv.keys()), {
        key: value
        for (key, value) in afterEnv.items()
        if (beforeEnv.get(key) != value)
    })
