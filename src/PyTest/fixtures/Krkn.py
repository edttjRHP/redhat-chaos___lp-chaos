'''
Krkn fixture for Chaos scenarios

Dependencies:
  - Stand-alone packages:
    - https://github.com/krkn-chaos/krkn
    - https://github.com/krkn-chaos/krkn-hub
  - Python modules:
    - https://github.com/krkn-chaos/krkn-lib
'''
from collections.abc import Callable, Generator
import dataclasses
import logging
import os
from pathlib import Path
from typing import Any, Optional, Union

import pytest

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from libs.CustomTypes import NamedDict, TempEnv
from libs.Utils.DynLoad import ImportFromPath, RunFromPath
from libs.Utils.ShellSourceEnv import BashEnv, GetEnvDelta


logs = logging.getLogger()
_DEF__FXT_CFG = NamedDict(
    kCfg=(
        os.environ.get('KUBECONFIG') or
        os.path.expanduser('~/.kube/config')
    ),
    cliCfg=NamedDict(
        ocCfg={},
        vcCfg={},
    ),
    ns=LPQE__DEF__NAMESPACE,
    krknPars=NamedDict(
        rootPath=NamedDict(
            krkn=Path(os.environ.get('KRKN_SRC') or ''),
            krknHub=Path(
                os.environ.get('KRKNHUB_SRC')
                or os.environ.get('KRKN_HUB_SRC')
                or ''
            ),
        ),
    ),
    testPars=NamedDict(),
    ctrImgURL_ExecNode='registry.access.redhat.com/ubi9/ubi'
)


@dataclasses.dataclass(frozen=True)
class FuncParsKrknHub_Run_krknPrepMod:
    modPath: Union[str, Path]
    modFuncName: str = 'BuildArgs'
    modFuncArgsPos: list[Any] = dataclasses.field(default_factory=list)
    modFuncArgsKW: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class FxtObjKrkn:
    '''
    Fixture Object.

    Can be sub-classed to add fields. Sub-classes must also be decorated with
    `@dataclass(frozen=True)`.
    '''
    krknHub: 'KrknHub'
    krknPars: NamedDict
    testPars: NamedDict
    cli: NamedDict
    ns: str


class KrknHub:
    def __init__(
        self,
        rootPath: Union[str, Path],
        krkn: Union[str, Path],
        kCfg: Union[str, Path],
    ) -> None:
        '''
        Store paths used to prepare and invoke Krkn.

        :param rootPath:
            Root directory for environment and preparation files.
        :param krkn:    Krkn script path.
        :param kCfg:    Kubernetes configuration path passed to the script.

        :return: `None`.
        '''
        self.rootPath = Path(rootPath)
        self.krkn = krkn
        self.kCfg = kCfg

    def Run(
        self,
        krknEnvFile: Union[str, Path],
        krknPrepMod: FuncParsKrknHub_Run_krknPrepMod,
        envVars: dict[str, Any] = None,
        initGlb: dict[str, Any] = None,
        runName: str = '__main__',
    ) -> None:
        '''
        Apply hub prep env, build Krkn argv, run ``run_kraken.py`` in-process.

        krkn-hub contract (keep shell and Python routes aligned):
          - ``krknEnvFile``: scenario ``prep_env.sh`` (shared Env. Var. prep /
            template render only; no argv construction).
          - ``krknPrepMod``: scenario ``prep_args_python.py`` with
            ``BuildArgs() -> list[str]`` matching ``prep_args_shell.sh``'s
            ``cliArgs[@]``.
          - For a package checkout (not the hub container), set
            ``KRKN_HUB__IN_CONTAINER=0`` in the process env (or ``envVars``)
            before calling. Example for ``vmi-outage``::

                krknHub.Run(
                    'vmi-outage/prep_env.sh',
                    FuncParsKrknHub_Run_krknPrepMod(
                        'vmi-outage/prep_args_python.py',
                    ),
                    envVars={'KRKN_HUB__IN_CONTAINER': '0'},
                )

        Env. Var. changes use delta apply/restore via ``TempEnv`` (no
        ``os.environ.clear()``).

        :param krknEnvFile: Shell environment file relative to the root.
        :param krknPrepMod: Preparation module, callable and its arguments.
        :param envVars:     Additional environment overrides for the script.
        :param initGlb:     Initial script globals.
        :param runName:     Script module name (default: '__main__').

        :return:    Globals returned by the executed script.

        :raises ValueError:     The preparation callable name is empty.
        :raises AttributeError: The preparation callable does not exist.
        :raises TypeError:
            Preparation is not callable or does not return `list[str]`.
        '''
        with TempEnv():
            # Sourcing Shell Env. file to update the Env. Variables.
            if envVars:
                for (key, val) in envVars.items():
                    if (val is None):
                        os.environ.pop(str(key), None)
                    else:
                        os.environ[str(key)] = str(val)
            (delEnv, updEnv) = GetEnvDelta(
                BashEnv,
                (self.rootPath / krknEnvFile),
            )
            for key in delEnv: os.environ.pop(key, None)
            for (key, val) in updEnv.items() : os.environ[key] = str(val)

            # Executing Python module for building `krkn` CLI Arguments.
            if not krknPrepMod.modFuncName.strip():
                raise ValueError(
                     'The `krknPrepMod.modFuncName` can not be empty'
                )
            prepMod = ImportFromPath(self.rootPath / krknPrepMod.modPath)
            try:
                prepFunc = getattr(prepMod, krknPrepMod.modFuncName)
            except AttributeError as err:
                raise AttributeError(
                    f'Module `{krknPrepMod.modPath}` has no function '
                    f'`{krknPrepMod.modFuncName}()`.'
                ) from err
            if not callable(prepFunc):
                raise TypeError(
                    f'The `{krknPrepMod.modFuncName}()` in '
                    f'`{krknPrepMod.modPath}` is not callable.'
                )
            krknArgs = prepFunc(
                *krknPrepMod.modFuncArgsPos,
                **krknPrepMod.modFuncArgsKW,
            )
            if (
                not isinstance(krknArgs, list) or
                not all(isinstance(arg, str) for arg in krknArgs)
            ):
                raise TypeError(
                    f'The `{krknPrepMod.modFuncName}()` in '
                    f'`{krknPrepMod.modPath}` must produce `list[str]`.'
                )

            return RunFromPath(
                self.krkn, krknArgs,
                {'KUBECONFIG': self.kCfg, **(envVars or {})},
                initGlb, runName,
            )


@pytest.fixture(name='fxtKrkn', scope='function')
def FxtKrkn(
    request: Any,
    fxtOCcli: Callable[..., Any],
    fxtVirtCtlCLI: Callable[..., Any],
) -> Generator[FxtObjKrkn, None, None]:
    '''
    Provide a `krkn-lib` client and scenario parameters to a test.

    Configure via indirect parametrize with `dict`:
        @pytest.mark.parametrize(
            'fxtKrkn,',
            [({
                'ns': 'bsod-test',
                'testPars': NamedDict(vmName='bsod-win2025'),
            },)],
            indirect=True,
        )

        Fixture Configuration:
          - kCfg:
            The `KUBECONFIG` file for Krkn Client.
          - cliCfg:
            Configuration options for CLIs:
              - ocCfg:
                  Configuration options for `OCcli`.
              - vcCfg:
                  Configuration options for `VirtCtlCLI`.
          - ns:
            Kubernetes Namespace (default: `LPQE__DEF__NAMESPACE`).
          - krknPars:
            Parameter for Krkn:
              - rootPath:
                Checkout paths (defaults: ``KRKN_SRC``, ``KRKNHUB_SRC``):
                  - krkn:       Path to the `krkn` checkout.
                  - krknHub:    Path to the `krkn-hub` checkout.
          - testPars:
            Parameter to be passed to test.
          - ctrImgURL_ExecNode:
            Container image URL for host-side execution
            (default: `'registry.access.redhat.com/ubi9/ubi'`).

    :yield:
        A `FxtObjKrkn` where:
          - krknHub:    A `KrknHub` instance.
          - krknPars:   A `NamedDict` of Krkn parameters.
          - testPars:   A `NamedDict` of test-specific parameters.
          - cli:
            A `NamedDict` with:
              - oc:         A `OCcli` instance.
              - virtCtl:    A `VirtCtlCLI` instance.
          - ns:         Kubernetes Namespace.
    '''
    fxtCfg = _DEF__FXT_CFG | NamedDict(**getattr(request, 'param', {}))

    logs.info(
        f'Initializing `krkn-lib` client with `KUBECONFIG`: {fxtCfg.kCfg}'
    )

    yield FxtObjKrkn(
        krknHub=KrknHub(
            fxtCfg.krknPars.rootPath.krknHub,
            (fxtCfg.krknPars.rootPath.krkn / 'run_krkn.py'),
            fxtCfg.kCfg,
        ),
        krknPars=fxtCfg.krknPars,
        testPars=fxtCfg.testPars,
        cli=NamedDict(
            oc=fxtOCcli(ns=fxtCfg.ns, **fxtCfg.cliCfg.ocCfg),
            virtCtl=fxtVirtCtlCLI(ns=fxtCfg.ns, **fxtCfg.cliCfg.vcCfg),
        ),
        ns=fxtCfg.ns,
    )

    logs.info('The `fxtKrkn` fixture teardown complete.')
