# Krkn Chaos Fixture

Use `fxtKrkn` to inject chaos into the Cluster Under Test (CUT) through KrknHub scenarios. The fixture exposes a KrknHub runner, not a krkn-lib client. It does
not provision VMs or automatically undo scenario effects.


## Minimal Usage

After configuring an authorized scenario and its target/environment:
```python
from fixtures.Krkn import FxtObjKrkn


def test_Chaos(fxtKrkn: FxtObjKrkn) -> None:
    fxtKrkn.krknHub.Run('pod-scenarios')
```

Select and configure the scenario for your test, assert the expected CUT recovery, and provide appropriate cleanup. This is a usage example, not a portable
acceptance test. Both repository `krkn` example tests are skipped placeholders. See the complete
[fixture configuration and parametrization](../fixtures/Krkn.py#L309) and [runner API reference](../fixtures/Krkn.py#L121) for all parameters and returned
values.


## Configuration

- Install Krkn and KrknHub at `fxtInf.rootPath.krkn` and `fxtInf.rootPath.krknHub` (defaults: `vendor/krkn` and `vendor/krknHub` under the PyTest root). GNU
  `envsubst` must be on `PATH`; environment loading uses Bash.
- Select a scenario directory other than `dummy-scenario`. Before loading environment scripts, the runner requires its `Dockerfile.template` to contain a
  standalone `LABEL krknctl.is_a_scenario=true` line (case-insensitive; whitespace and quotes around `true` are accepted).
- Select fixture options with `IENG_PYTEST____FXT__KRKN____OPTS` (default: `conf/Krkn/opts--krkn.yaml` under the PyTest root). `sourceFile.envVar` is trusted
  Bash Shell Script, not a script path. The supplied options enable Elasticsearch by default and require `ES_USERNAME`/`ES_PASSWORD` unless `ENABLE_ES=false`;
  adapt the endpoint or disable collection.
- Configure via indirect parametrization with `ns`, `kCfg`, `cliCfg`, `testPars`, and nested `NamedDict` settings. `kCfg` is passed as `KRKN_KUBE_CONFIG`; use
  an absolute path and align CLI context with the same CUT.
- `fxtInf.krknCfg.krknEnv` applies before KrknHub main `env.sh` and then Scenario `env.sh` are loaded. These scripts can change settings;
  `Run(..., envVars=...)` overrides only the final runner invocation, not loading, rendering, or preparation.
- Set `KRKN_HUB__TEMPLATE__CFG__FN` relative to the KrknHub root and `KRKN_HUB__TEMPL__SCN__FN` relative to the scenario directory; absolute paths also work.
  `SCENARIO_FILE` names the rendered scenario. If present, scenario `prep.sh` runs directly after rendering and before dispatch; it must be executable with a
  valid shebang. Failure stops dispatch.

The bundle exposes `krknHub`, resolved `fxtInf`, caller `testPars`, `cli.oc`, `cli.virtCtl`, and `ns`. Setting `ns` does not limit every vendor scenario to
that namespace: verify its actual selectors and disruption scope.


## Artifacts and Cleanup

Each `Run` retains a UTC `YYYYMMDDTHHmmssZ--<scnName>` directory beneath `IENG_PYTEST____FXT__KRKN____KRKN__RUN_DIR__ROOT` (default: `/tmp/run-dir--krkn`).
Choose separate roots for concurrent runs; a same-name, same-second collision fails rather than overwriting artifacts. See `config/chaos.yaml` for config.
Relative `SCENARIO_FILE`, `ROLLBACK_VERSIONS_DIRECTORY`, and `TELEMETRY_ARCHIVE_PATH` use the run directory; relative `ALERTS_PATH`, `METRICS_PATH`, and
`RESILIENCY_FILE` use the Krkn root. `KRKN_DEBUG=True` enables runner debug mode.

The fixture is function-scoped and restores the caller's environment and working directory, not cluster state or files modified by scenario scripts. Test
authors own scenario rollback and artifact retention. Follow the [namespace recommendations](../README.md#best-practices); cluster-wide scenarios need a wider
isolation plan. Windows bugcheck injection, guest crash detection, and dump analysis remain [roadmap work](roadmap--krkn-lib-bsod.md).
