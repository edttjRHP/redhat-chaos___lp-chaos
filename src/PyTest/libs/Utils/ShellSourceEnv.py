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
        # Send STDOUT prints, produced by envFile, to STDERR, so it does not
        #   corrupt the one-JSON-document-per-line protocol on STDOUT.
        #
        # If sourcing fails, terminate Bash. The next Snapshot() then raises.
        self.proc.stdin.write(
            f'source {shlex.quote(os.fspath(envFile))} 0</dev/null 1>&2 || '
            'exit $?\n'
        )
        self.proc.stdin.flush()

    def Close(self) -> None:
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
    '''Return `(delEnv, updEnv)` after sourcing the `envFile`.'''
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
