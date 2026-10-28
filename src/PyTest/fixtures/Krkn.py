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
            krkn=Path(os.getenv(
                (
                    'IENG_PYTEST____FXT__KRKN____'
                    'DEF_FXT_CFG__ROOTPATH_KRKN'
                ),
                (
                    f'{Path(__file__).resolve().parent.parent}/'
                    'vendor/krkn'
                ),
            )),
            krknHub=Path(os.getenv(
                (
                    'IENG_PYTEST____FXT__KRKN____'
                    'DEF_FXT_CFG__ROOTPATH_KRKNHUB'
                ),
                (
                    f'{Path(__file__).resolve().parent.parent}/'
                    'vendor/krknHub'
                ),
            )),
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
        with TempEnv():
            # Sourcing Shell Env. file to update the Env. Variables.
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
                Root dir. of vendor packages:
                  - krkn:       Location where `krkn` is installed.
                  - krknHub:    Location where `krkn-hub` is installed.
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
