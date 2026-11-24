# Krkn Chaos Fixture

Use `fxtKrkn` to inject chaos into the Cluster Under Test (CUT) through KrknHub scenarios. The fixture exposes a KrknHub runner, not a krkn-lib client. It does
not provision VMs or automatically undo scenario effects. The test must prepare and consequently clean up all required test resources.


## Prerequisites

  - The fixture uses Krkn and KrknHub at `fxtInf.rootPath.krkn` and `fxtInf.rootPath.krknHub` (defaults: `vendor/krkn` and `vendor/krknHub` under the PyTest
    root). `setup.sh` installs them: it runs `setup--fxt--Krkn.sh`, which extracts the public `main` branch archives into
    `IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKN` and `IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKNHUB` (relative paths resolve from the
    PyTest root) and keeps existing directories.
  - GNU `envsubst` must be on `PATH`: the runner renders the scenario and config templates with it. It is a host binary, not a Python package, and `setup.sh`
    does not install it: `setup--fxt--Krkn.sh` exits early, before any download, whenever the command is missing (it checks presence only, not the GNU
    implementation). Install it with, e.g., `dnf install /usr/bin/envsubst` (DNF installs the package that provides that file) or
    `apt-get install gettext-base` (Debian / Ubuntu). Environment loading uses Bash.


## Minimal Usage

After configuring an authorized scenario and its target/environment:
```python
from fixtures.Krkn import FxtObjKrkn


def test_Chaos(fxtKrkn: FxtObjKrkn) -> None:
    fxtKrkn.krknHub.Run('pod-scenarios')
```

Select and configure the scenario for your test, assert the expected CUT recovery, and provide appropriate cleanup. This is a usage example, not a portable
acceptance test. Select the repository example with the `krkn` marker: the [pod-scenario example](../test--suites/ts--example/test_Krkn.py) creates its own
Namespace and Deployment (KrknHub Pod Scenarios kill existing Pods), kills that Pod, and checks its replacement. See the complete
[Fixture Configuration and Parametrization](../fixtures/Krkn.py#L401) and [runner API reference](../fixtures/Krkn.py#L121) for all parameters and returned
values.


## Configuration

  - Select a scenario directory other than `dummy-scenario`. Before loading environment scripts, the runner requires its `Dockerfile.template` to contain a
    standalone `LABEL krknctl.is_a_scenario=true` line (case-insensitive; whitespace and quotes around `true` are accepted).
  - Select fixture options with `IENG_PYTEST____FXT__KRKN____OPTS` (default: `conf/Krkn/opts--krkn.yaml` under the PyTest root). `sourceFile.envVar` is trusted
    Bash Shell Script, not a script path. The supplied options enable ElasticSearch by default and require `ES_USERNAME`/`ES_PASSWORD` unless
    `ENABLE_ES=false`. Adapt the endpoint or disable collection.
  - Configure via indirect parametrization with `ns`, `kCfg`, `cliCfg`, `testPars`, and nested `NamedDict` settings. The `kCfg` is passed as
    `KRKN_KUBE_CONFIG`. Use an absolute path and align CLI context with the same CUT.
  - The `fxtInf.krknCfg.krknEnv` applies before KrknHub main `env.sh`, `common_run.sh`, and then Scenario `env.sh` are loaded, together with
    `KRAKEN_FOLDER` (Krkn root) and `ROOT_FOLDER` (scenario directory). These scripts can change settings. The `Run(..., envVars=...)` overrides only the
    final runner invocation, not loading, preparation, or rendering.
  - The scenario template is `SCENARIO_TEMPLATE_FILE` (set by the Scenario `env.sh`), relative to the scenario directory, and the config template is
    `CONFIG_FILE_TEMPLATE` (set by the KrknHub main `env.sh`), relative to the KrknHub root, absolute paths also work.
  - If present, scenario `prep.sh` runs after loading and before rendering and dispatch; it must be executable with a valid shebang. A failed preparation stops
    the run.
  - The `SCENARIO_FILE` names the rendered scenario. An existing file (e.g. written by `prep.sh`) is used as is, without rendering. An empty template renders
    nothing.

The bundle exposes `cli.oc`, `cli.virtCtl`, `ns`, resolved `fxtInf`, caller `testPars`, and `krknHub`. Setting `ns` does not limit every vendor scenario to
that namespace: verify its actual selectors and disruption scope.


## Artifacts and Cleanup

Each `Run` retains a UTC `YYYYMMDDTHHmmssZ--<scnName>` directory beneath `IENG_PYTEST____FXT__KRKN____KRKN__RUN_DIR__ROOT` (default: `/tmp/run-dir--krkn`).
A same-name, same-second collision fails rather than overwriting artifacts. See `config/chaos.yaml` for config.
Relative `ROLLBACK_VERSIONS_DIRECTORY` and `TELEMETRY_ARCHIVE_PATH` use the run directory, and relative `SCENARIO_FILE` uses the run directory's
`scenarios/` sub-directory; relative `ALERTS_PATH`, `METRICS_PATH`, and `RESILIENCY_FILE` use the Krkn root. `KRKN_DEBUG=True` enables runner debug mode.

The fixture is function-scoped and restores the caller's environment and working directory, not cluster state or files modified by scenario scripts. Test
authors own scenario rollback and artifact retention. Follow the [namespace recommendations](../README.md#best-practices); cluster-wide scenarios need a wider
isolation plan. Windows bugcheck injection, guest crash detection, and dump analysis remain [roadmap work](roadmap--krkn--bsod.md).


## Best Practices

  - The framework [Best Practices](../README.md#best-practices) apply.
  - Review disruptive scenarios before use.
