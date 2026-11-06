'''
Krkn fixture for Chaos scenarios

Dependencies:
  - Python modules:
    - https://github.com/krkn-chaos/krkn-lib
  - Stand-alone packages:
    - https://github.com/krkn-chaos/krkn
    - https://github.com/krkn-chaos/krkn-hub
'''
from collections.abc import Callable, Generator
from contextlib import chdir
import dataclasses
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Optional, Union

import pytest

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from libs.CustomTypes import NamedDict, TempEnv
from libs.Utils.CmdExec import CmdExec
from libs.Utils.DynLoad import RunFromPath, UpdEnv
from libs.Utils.ShellSourceEnv import DoShellSource


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
    fxtInf=NamedDict(
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
        krknCfg=NamedDict(),
    ),
    testPars=NamedDict(),
)


@dataclasses.dataclass(frozen=True)
class FxtObjKrkn:
    '''
    Fixture Object.

    Can be sub-classed to add fields. Sub-classes must also be decorated with
    `@dataclass(frozen=True)`.
    '''
    krknHub: 'KrknHub'
    krknInf: NamedDict
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
            Krkn Hub root directory containing env.sh and scenario directories.
        :param krkn:    Full path to the Krkn runner script (`run_kraken.py`).
        :param kCfg:    Path to Kubernetes Configuration file.
        '''
        self.rootPath = Path(rootPath)
        self.krkn = Path(krkn).resolve()
        self.kCfg = kCfg

    def Run(
        self,
        scnName: Union[str, Path],
        envVars: dict[str, Any] = None,
        initGlb: dict[str, Any] = None,
        runName: str = '__main__',
    ) -> dict[str, Any]:
        '''
        Render a Hub scenario and run Krkn in a temporary environment.

        Mimic KrknHub Scenario runner (`run.sh`):
          - Source KrknHub main `env.sh` then Scenario `env.sh`.
          - Render Krkn Scenario and Config from their templates.

        :param scnName: Scenario directory beneath the Krkn Hub root.
        :param envVars: Additional environment overrides for the script.
        :param initGlb: Initial script globals.
        :param runName: Script module name (default: '__main__').

        :return:    Globals returned by the executed script.

        :raises KeyError:
            A required template or output variable is unset.
        :raises OSError:
            A file is unavailable or run directories collide.
        :raises RuntimeError:
            Sourcing or GNU `envsubst` fails.
        '''
        with TempEnv():
            kHubRoot = self.rootPath.resolve()
            scnDir = (kHubRoot / scnName).resolve()
            DoShellSource(envFiles=[kHubRoot / 'env.sh', scnDir / 'env.sh'])

#           Krkn configuration items that referencing file or dir. have
#           different path resolution algorithm (in `krkn-hub`, each item's
#           value is controlled by an Env. Var.). The configuration paths
#           listed below accept abs. paths and will be honored as is. But
#           when supplied with rel. path, the following rules applied:
#            1. Rel. path resolved from CWD:
#                 - R/O.
#                   ```yaml
#                   kraken:
#                     chaos_scenarios:
#                       - <scenarioType>:       # This refer to Scenario Plugin Name.
#                           - <scenarioFile>    # SCENARIO_FILE
#                     kubeconfig_path:          # KRKN_KUBE_CONFIG
#                   performance_monitoring:
#                     alert_profile:            # ALERTS_PATH
#                     metrics_profile:          # METRICS_PATH
#                   resiliency:
#                     resiliency_file:          # RESILIENCY_FILE (def: ALERTS_PATH)
#                   ```
#                 - R/W, W/O.
#                   ```yaml
#                   kraken.rollback_versions_directory: # ROLLBACK_VERSIONS_DIRECTORY
#                   telemetry.archive_path:             # TELEMETRY_ARCHIVE_PATH
#                   ```
#            2. Rel. path resolved from parent dir. of `run_kraken.py`.
#                 - R/O.
#                   ```yaml
#                   kraken.image_signature_public_key   # Set to a fixed value.
#                   ```
            krknRoot = Path(self.krkn).resolve().parent
            (runDir := Path(os.getenv(
                'IENG_PYTEST____FXT__KRKN____KRKN__RUN_DIR__ROOT',
                '/tmp/run-dir--krkn',
            )).resolve() / (
                f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}--{scnName}'
            )).mkdir(parents=True, exist_ok=False)
            UpdEnv({
                envVar: (root / os.environ[envVar]).resolve()
                for (root, envVar) in (
                    (krknRoot, (
                        'ALERTS_PATH', 'METRICS_PATH', 'RESILIENCY_FILE',
                    )),
                    (runDir, (
                        'SCENARIO_FILE', 'ROLLBACK_VERSIONS_DIRECTORY',
                        'TELEMETRY_ARCHIVE_PATH',
                    )),
                )
                for envVar in envVar if os.environ.get(envVar)
            })
            cfgTmpl = (
                kHubRoot / os.environ['KRKN_HUB__TEMPLATE__CFG__FN']
            ).resolve()
            scnTmpl = (
                scnDir / os.environ['KRKN_HUB__TEMPL__SCN__FN']
            ).resolve()
            cfgFile = (runDir / 'config/chaos.yaml').resolve()
            scnFile = Path(os.environ['SCENARIO_FILE'])
            for dir in (cfgFile.parent, scnFile.parent, *(
                Path(os.environ[envVar])
                for envVar in (
                    'ROLLBACK_VERSIONS_DIRECTORY', 'TELEMETRY_ARCHIVE_PATH',
                ) if os.environ.get(envVar)
            )):
                dir.mkdir(parents=True, exist_ok=True)
            for (tmpl, output) in (
                (scnTmpl.read_bytes(), scnFile),
                (cfgTmpl.read_bytes(), cfgFile),
            ):
                result = CmdExec().Run(
                    ['envsubst'],
                    input=tmpl,
                    spPars={'text': False},
                )
                if not result.success:
                    raise RuntimeError(
                        f'The `envsubst` failed ({result.exitStat}): '
                        f'{result.stderr}'
                    )
                output.write_bytes(result.stdout)

            # Prepare Krkn CLI arguments.
            krknArgs = [f'--config={cfgFile}']
            if (os.environ.get('KRKN_DEBUG') == 'True'):
                krknArgs.extend(['--debug', 'True'])

            # Execute Krkn.
            with chdir(runDir):
                return RunFromPath(
                    self.krkn,
                    krknArgs,
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
              - ocCfg:  Configuration options for `OCcli`.
              - vcCfg:  Configuration options for `VirtCtlCLI`.
          - ns:
            Kubernetes Namespace (default: `LPQE__DEF__NAMESPACE`).
          - fxtInf:
            Fixture settings:
              - rootPath:
                Root dir. of vendor packages:
                  - krkn:       Location where `krkn` is installed.
                  - krknHub:    Location where `krkn-hub` is installed.
              - krknCfg:
                Krkn configurations.
          - testPars:
            Parameters to be passed to test.

    :yield:
        A `FxtObjKrkn` where:
          - krknHub:    A `KrknHub` instance.
          - krknInf:    The `fxtInf.krknCfg` mapping of Krkn parameters.
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
            fxtCfg.fxtInf.rootPath.krknHub,
            (fxtCfg.fxtInf.rootPath.krkn / 'run_kraken.py'),
            fxtCfg.kCfg,
        ),
        krknInf=fxtCfg.fxtInf.krknCfg,
        testPars=fxtCfg.testPars,
        cli=NamedDict(
            oc=fxtOCcli(ns=fxtCfg.ns, **fxtCfg.cliCfg.ocCfg),
            virtCtl=fxtVirtCtlCLI(ns=fxtCfg.ns, **fxtCfg.cliCfg.vcCfg),
        ),
        ns=fxtCfg.ns,
    )

    logs.info('The `fxtKrkn` fixture teardown complete.')
