'''
Krkn fixture for Chaos scenarios

Inject chaos into the Cluster Under Test (CUT) through KrknHub Scenarios model.

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
from io import StringIO
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional, Union

import pytest

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from libs.CustomTypes import NamedDict, TempEnv
from libs.Utils.CmdExec import CmdExec
from libs.Utils.DynLoad import RunFromPath, UpdEnv
from libs.Utils.ShellSourceEnv import BashEnv, DoShellSource, GetEnvDelta
from libs.Utils.YAML import YAMLcfg


logs = logging.getLogger()
_DEF__FXT_CFG = NamedDict(
    kCfg=(
        os.environ.get('KUBECONFIG') or
        os.path.expanduser('~/.kube/config')
    ),
    # The `--kubeconfig` option will be overridden by the above `.kCfg`.
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
        krknCfg=NamedDict(
            krknEnv={
                'ENABLE_ES':    'False',
                'ES_SERVER':    'http://0.0.0.0',
                'ES_PORT':      'ES_PORT:=443',
            },
        ),
    ),
    testPars=NamedDict(),
)


@dataclasses.dataclass(frozen=True)
class InstParsKrknHub:
    fxtCfgObj: NamedDict


@dataclasses.dataclass(frozen=True)
class FxtObjKrkn:
    '''
    Fixture Object.

    Can be sub-classed to add fields. Sub-classes must also be decorated with
    `@dataclass(frozen=True)`.
    '''
    krknHub: 'KrknHub'
    fxtInf: NamedDict
    testPars: NamedDict
    cli: NamedDict
    ns: str


class KrknHub:
    def __init__(
        self,
        request: Any,
        iPars: InstParsKrknHub,
    ) -> None:
        '''
        Store paths used to prepare and invoke Krkn.
        Store Fixture Configuration for subsequent Krkn Scenario runs.

        :param request:
            Pytest `request` built-in fixture.
            See `FxtKrkn()` for fixture parametrization details.
        :param iPars:
            Persistent instance parameters.
            - fxtCfgObj:
                    Base fixture configuration. See `FxtKrkn()` for details.
        '''
        self.request = request
        self.iPars = iPars

    def Run(
        self,
        scnName: Union[str, Path],
        envVars: dict[str, Any] = None,
        initGlb: dict[str, Any] = None,
        runName: str = '__main__',
        krknScr: Union[str, Path] = 'run_kraken.py',
        **fxtCfgArgs: Any,
    ) -> dict[str, Any]:
        '''
        Render a Hub scenario and run Krkn in a temporary environment.

        Mimic KrknHub Scenario runner (`run.sh`):
          - Apply configured `krknEnv`.
          - Source KrknHub main `env.sh` then Scenario `env.sh`.
          - Render Krkn Scenario and Conf. from their templates.
          - Execute the Scenario `prep.sh` if present.
          - Prepare CLI arguments and execute the Krkn runner.
        The `prep.sh` is executed directly, so it must be executable with a
        valid she-bang. A failed preparation stops the run.

        Inject chaos into the CUT using the selected KrknHub Scenario model.
        Supply scenario targeting, recovery assertions, and cleanup in the
        test. See `docs/guide--krkn.md` for prerequisites and artifact
        retention.

        Relative runtime directory roots resolve from the caller's working
        directory before dispatch changes it. Nested scenario names retain
        their directory structure. Absolute artifact/path-variable values
        remain absolute rather than being prefixed by a resolution base.

        :param scnName:     Scenario directory beneath the KrknHub root.
        :param envVars:     Additional environment overrides for the script.
        :param initGlb:     Initial script globals.
        :param runName:     Script module name (default: '__main__').
        :param krknScr:
            Full path (from `krkn` root) to the Krkn runner script
            (default: 'run_kraken.py').
        :param fxtCfgArgs:
            Fixture conf. overrides. See `FxtKrkn()` for details.

        :return:    Globals returned by the executed script.

        :raises AssertionError:
            The scenario fails the Dockerfile label/directory guard.
        :raises KeyError:
            A required template or output variable is unset.
        :raises OSError:
            A file is unavailable or run directories collide.
        :raises RuntimeError:
            Sourcing, GNU `envsubst`, or Scenario preparation fails.
        '''
        fxtCfg = self.iPars.fxtCfgObj | NamedDict(fxtCfgArgs)

        kHubRoot = Path(fxtCfg.fxtInf.rootPath.krknHub).resolve()
        krknRoot = Path(fxtCfg.fxtInf.rootPath.krkn).resolve()
        krknRun = (krknRoot / krknScr).resolve()
        assert (re.search(r'(^/|(^|/)\.\.(/|$))', str(scnName)) is None)
        scnDir = (kHubRoot / scnName).resolve()
        assert (
            (scnDir.name != 'dummy-scenario') and
            (
                scnDockerfile := scnDir / 'Dockerfile.template'
            ).is_file() and
            re.search(
                (
                    r'(?im)^\s*LABEL\s+'
                    r'krknctl\.is_a_scenario\s*=\s*[\'"]?true[\'"]?\s*$'
                ),
                scnDockerfile.read_text(encoding='utf-8'),
            )
        )

        with TempEnv():
            UpdEnv({
                **fxtCfg.fxtInf.krknCfg.krknEnv,
                'KRKN_KUBE_CONFIG': fxtCfg.kCfg,
            })
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

            if (scnDir / 'prep.sh').is_file():
                result = CmdExec().Run([str(scnDir / 'prep.sh')])
                if not result.success:
                    raise RuntimeError(
                        f'The `prep.sh` failed ({result.exitStat}): '
                        f'{result.stderr}'
                    )

            # Prepare Krkn CLI arguments.
            krknArgs = [f'--config={cfgFile}']
            if (os.environ.get('KRKN_DEBUG') == 'True'):
                krknArgs.extend(['--debug', 'True'])

            # Execute Krkn.
            with chdir(runDir):
                return RunFromPath(
                    krknRun,
                    krknArgs,
                    (envVars or {}),
                    initGlb,
                    runName,
                )


@pytest.fixture(name='fxtKrkn', scope='function')
def FxtKrkn(
    request: Any,
    fxtOCcli: Callable[..., Any],
    fxtVirtCtlCLI: Callable[..., Any],
) -> Generator[FxtObjKrkn, None, None]:
    '''
    Provide a `KrknHub` runner and scenario parameters to a test.

    Seed initial Env. Var. from Krkn Fixture Options file (YAML), selected by
    `IENG_PYTEST____FXT__KRKN____OPTS` (default:
    `<PyTestRoot>/conf/Krkn/opts--krkn.yaml`), key `sourceFile.envVar` (trusted
    Bash Shell Script block).

    This is the complete Fixture Configuration reference (see
    `docs/guide--krkn.md` for concise usage).
    Configure via indirect parametrize with `dict` and nested `NamedDict`:
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
            The `KUBECONFIG` file for the Krkn runner (default: `KUBECONFIG`
            Env. Var. value (default: `'~/.kube/config'`)).
          - cliCfg:
            Configuration options for CLIs:
              - ocCfg:  Configuration options for `OCcli` (default: `{}`).
              - vcCfg:  Configuration options for `VirtCtlCLI` (default: `{}`).
          - ns:
            Kubernetes Namespace (default:
            `.libs.CommonConstant.LPQE__DEF__NAMESPACE`).
            This does not constrain every vendor scenario automatically.
          - fxtInf:
            Fixture settings:
              - rootPath:
                Root dir. of vendor packages:
                  - krkn:
                    Location where `krkn` is installed (default:
                    `IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKN`
                    Env. Var. value (default: `'<PyTestRoot>/vendor/krkn'`)).
                  - krknHub:
                    Location where `krkn-hub` is installed (default:
                    `IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKNHUB`
                    Env. Var. value (default:
                    `'<PyTestRoot>/vendor/krknHub'`)).
              - krknCfg:
                Krkn configurations.
                  - krknEnv:    Initial seed of Env. Var.
          - testPars:
            Parameters to be passed to test (default: `NamedDict()`).

    Provide a KrknHub runner to inject chaos into the CUT for one test. See
    `docs/guide--krkn.md` for usage and safety.

    Call `krknHub.Run(scnName)` to render and execute the selected KrknHub
    Scenario model.
    Prepare its target, permissions, assertions, and rollback. The fixture
    restores local environment/CWD, not cluster state or retained artifacts.

    :yield:
        A `FxtObjKrkn` where:
          - krknHub:    A `KrknHub` instance.
          - fxtInf:     The resolved fixture settings `NamedDict` (see Fixture
                        Configuration for details).
          - testPars:   A `NamedDict` of test-specific parameters.
          - cli:
            A `NamedDict` with:
              - oc:         A `OCcli` instance.
              - virtCtl:    A `VirtCtlCLI` instance.
          - ns:         Kubernetes Namespace.
    '''
    fxtOpts = NamedDict.FromDict(YAMLcfg.ToDict(Path(os.getenv(
        'IENG_PYTEST____FXT__KRKN____OPTS',
        (request.config.rootpath / 'conf/Krkn/opts--krkn.yaml').resolve(),
    )).read_text()))
    # The `GetEnvDelta()` omits unchanged exports (the currently inherited Env.
    # Var. value is the same with the set value in `envFiles`). So if that Env.
    # Var. also exist in `_DEF__FXT_CFG.fxtInf.krknCfg.krknEnv`, its value will
    # become effective, instead of the one set in `envFiles`. So for each Env.
    # Var. exist in `_DEF__FXT_CFG.fxtInf.krknCfg.krknEnv`, get the current
    # inherited value first.
    fxtCfg = (
        _DEF__FXT_CFG |
        NamedDict(fxtInf=NamedDict(krknCfg=(NamedDict(krknEnv={
            **{
                key: os.environ[key]
                for key in _DEF__FXT_CFG.fxtInf.krknCfg.krknEnv
                if (key in os.environ)
            },
            **GetEnvDelta(BashEnv, [StringIO(fxtOpts.sourceFile.envVar)]),
        })))) |
        NamedDict(**getattr(request, 'param', {}))
    )

    yield FxtObjKrkn(
        krknHub=KrknHub(request, InstParsKrknHub(fxtCfgObj=fxtCfg)),
        fxtInf=fxtCfg.fxtInf,
        testPars=fxtCfg.testPars,
        cli=NamedDict(
            oc=fxtOCcli(
                ns=fxtCfg.ns,
                **{**fxtCfg.cliCfg.ocCfg, 'kubeconfig': fxtCfg.kCfg},
            ),
            virtCtl=fxtVirtCtlCLI(
                ns=fxtCfg.ns,
                **{**fxtCfg.cliCfg.vcCfg, 'kubeconfig': fxtCfg.kCfg},
            ),
        ),
        ns=fxtCfg.ns,
    )

    logs.info('The `fxtKrkn` fixture teardown complete.')
