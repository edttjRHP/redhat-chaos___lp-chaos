'''Execute local system executable via external process.'''
import logging
import subprocess
import time
from typing import Dict, List, NamedTuple, Optional, Union

logs = logging.getLogger(__name__)

class CmdRes(NamedTuple):
    '''Structured immutable output for executed commands.'''
    stdout: Optional[Union[str, bytes]]
    stderr: Optional[Union[str, bytes]]
    exitCode: int
    success: bool


class CmdExec:
    '''Execute commands locally.'''

    def __init__(self, defTryMax: int = 1, tryWait: int = 5) -> None:
        self.defTryMax = defTryMax
        self.tryWait = tryWait

    def Run(
        self,
        command: Union[str, List[str]],
        shell: bool = False,
        input: Optional[Union[str, bytes]] = None,
        isSecret: bool = False,
        tryMax: Optional[int] = None,
        spPars: Optional[Dict] = None,
    ) -> CmdRes:
        '''
        Run a command locally with optional retry and stdin piping.

        :param command: Command to execute (see `subprocess` for details).
        :param shell:   True to run via shell wrapper (use with caution).
        :param input:
            Optional string, piped to the process's standard input, passed
            through `to subprocess.run()`.
        :param isSecret:
            Omit sensitive log values and recordings; keep safe metadata.
            Raw results are unchanged and it is caller responsibility to ensure
            it is NOT leaked.
        :param tryMax:  Override the default maximum number of attempts.
        :param spPars:
            Optional `dict` of extra keyword arguments passed through
            to `subprocess.run()` (e.g. `{'timeout': 30, 'env': myEnv}`).
        '''
        tryMax = self.defTryMax if (tryMax is None) else tryMax
        spArgs = {
            'input': input,
            'capture_output': True,
            'shell': shell,
            'text': True,
        }
        if spPars: spArgs.update(spPars)

        for tryCnt in range(1, (tryMax + 1)):
            try:
                result = subprocess.run(command, **spArgs)
                if result.returncode:
                    logs.warning(
                        f'Command failed with exit code {result.returncode} '
                        f'(Try {tryCnt}/{tryMax}): '
                        f'{"[SNIPPED]" if isSecret else result.stderr.strip()}'
                    )
                    if (tryCnt < tryMax):
                        logs.info(
                            f'Retrying execution in {self.tryWait} seconds...'
                        )
                        time.sleep(self.tryWait)
                        continue
                return CmdRes(
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exitCode=result.returncode,
                    success=(result.returncode == 0),
                )
            except (FileNotFoundError, PermissionError) as ex:
                logs.error(
                    'Critical environment exception '
                    f'(Try {tryCnt}/{tryMax}): '
                    f'{"[SNIPPED]" if isSecret else ex}'
                )
                return CmdRes(
                    stdout='', stderr=str(ex), exitCode=-2, success=False,
                )
            except subprocess.SubprocessError as ex:
                logs.warning(
                    'Subprocess plumbing exception '
                    f'(Try {tryCnt}/{tryMax}): '
                    f'{"[SNIPPED]" if isSecret else ex}'
                )
                if (tryCnt < tryMax):
                    logs.info(
                        f'Retrying execution in {self.tryWait} seconds...'
                    )
                    time.sleep(self.tryWait)
                else:
                    return CmdRes(
                        stdout='', stderr=str(ex), exitCode=-2, success=False,
                    )

        return CmdRes(
            stdout='',
            stderr='Max. execution tries exceeded.',
            exitCode=-3,
            success=False,
        )
