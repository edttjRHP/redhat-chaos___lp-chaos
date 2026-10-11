'''
Base thin shim for Cobra-based CLI tools with common-option auto-discovery.
'''
import logging
import re
import shlex
from typing import Optional

from libs.Utils.CmdExec import CmdExec, CmdRes


logs = logging.getLogger(__name__)
_SCALAR = (str, int, float)


class GoCobraCLI:
    '''
    Base thin shim for any Cobra-based CLI tool.

    At init, discovers common options via `'<binExec>' options` and stores
    user-provided defaults. On each call, common options go before the
    sub-command, sub-command specific options go after.
    never shell=True.
    '''

    def __init__(
        self,
        binExec: str,
        cmdExec: Optional[CmdExec] = None,
        **defOpts,
    ) -> None:
        '''
        Initialize and discover common options from `'<binExec>' options`.

        :param binExec:
            The CLI executable name (e.g. 'oc', 'virtctl').
        :param cmdExec:
            Optional CmdExec instance (default: `new CmdExec()`).
        :param defOpts:
            Default values for common options, e.g.
            `namespace='windows-bsod'`, `kubeconfig='/path/to/kcfg'`.
        '''
        self.binExec = binExec
        self.cmdExec = cmdExec or CmdExec()
        self.cmnOpts = {}   # 'namespace' -> {'short': '-n', 'long': '--namespace'}
        self.shortMap = {}  # '-n' -> 'namespace' (reverse lookup)
        self.defOpts = defOpts
        self._DiscoverOptions()

    def _DiscoverOptions(self) -> None:
        '''
        Parse `'<binExec>' options` output to populate self.cmnOpts.
        '''
        result = self.cmdExec.Run([self.binExec, 'options'])
        if not result.success:
            logs.warning(
                f'Failed to discover '
                f'`{shlex.quote(self.binExec)} options`: {result.stderr}'
            )
            return

        for line in f'{result.stdout}\n{result.stderr}'.splitlines():
            m = re.match(r'^\s+(?:(-\w),\s+)?--([\w-]+)(?:[=\s:]|$)', line)
            if m:
                short, name = m.group(1), m.group(2)
                self.cmnOpts[name] = {
                    'short': short,
                    'long': f'--{name}',
                }
                if short: self.shortMap[short] = name

    def _IsCommon(self, key: str) -> bool:
        '''Check if a key matches a discovered common option.'''
        if (len(key) == 1):
            return f'-{key}' in self.shortMap
        return key.replace('_', '-') in self.cmnOpts

    def Run(
            self,
            subCmd: str,
            *args,
            input=None,
            spPars=None,
            **opts,
        ) -> CmdRes:
        '''
        Execute: <binExec> [cmnOpts...] subCmd... [subCmdOpts...] [args...]

        :param subCmd:
            The sub-command(s). Space-separated for multi-level
            (e.g. 'vmexport create').
        :param args:
            Positional arguments (`str`/`int`/`float`; coerced to `str`).
        :param input:
            Optional string piped to the process's standard input.
        :param spPars:
            Optional `dict` of extra keyword arguments passed through
            to `subprocess.run()` (e.g. `{'timeout': 30, 'env': myEnv}`).
        :param opts:
            Keyword option's value:
            - `None`                 -> Flag omitted entirely (suppress option
                                        in `self.defOpts`).
            - `bool`                 -> `--flag=true` / `--flag=false`.
            - `str`/`int`/`float`    -> `--flag value`  (Coerced to `str`.)
            - single-char key        -> Short flag (`-o json`).
            - multi-char key         -> Long flag (`--no-headers=true`).
            - common option          -> Placed before sub-command.
            - otherwise              -> Placed after sub-command.
        :return:
            The `CmdRes` from `CmdExec.Run()`.
        :raises TypeError:
            If a positional argument or option's value is not a supported type.
        '''
        posArgs = []
        cmnArgs = []
        subArgs = []
        subCmds = subCmd.split()

        # Safeguarding positional arguments.
        for a in args:
            if (isinstance(a, bool) or not isinstance(a, _SCALAR)):
                raise TypeError(
                    f'positional arg must be `str`|`int`|`float`, '
                    f'got {type(a).__name__}: {a!r}'
                )
            posArgs.append(str(a))

        # Safeguarding keyword options.
        for (key, val) in {**self.defOpts, **opts}.items():
            if (val is None): continue

            flag = (
                f'-{key}' if (len(key) == 1) else f'--{key.replace("_", "-")}'
            )

            if isinstance(val, bool):
                token = [f"{flag}={'true' if val else 'false'}"]
            elif isinstance(val, _SCALAR):
                token = [flag, str(val)]
            else:
                raise TypeError(
                    f'Option {key!r} must be any of `None`, `bool`, `str`, '
                    f'`int`, or `float`. Got {type(val).__name__}: {val!r}'
                )

            if self._IsCommon(key): cmnArgs.extend(token)
            else: subArgs.extend(token)

        return self.cmdExec.Run(
            [self.binExec, *cmnArgs, *subCmds, *subArgs, *posArgs],
            input=input,
            spPars=spPars,
        )
