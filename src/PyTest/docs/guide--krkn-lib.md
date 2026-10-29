# The Krkn / krkn-hub Integration

This project uses [`krkn`](https://github.com/krkn-chaos/krkn) with scenario wrappers from
[`krkn-hub`](https://github.com/krkn-chaos/krkn-hub) for cluster-level Chaos in OpenShift Virtualization resiliency
scenarios. PyTest supplies scenario configuration and lifecycle management; krkn runs inside its **own virtualenv** via
subprocess so its dependency pins never share the PyTest `.venv`.


## What the integration provides

The `fxtKrkn` fixture yields a frozen `FxtObjKrkn` with a `KrknHub` runner, scenario `krknPars` / `testPars`, and
OpenShift / virtualization CLI access. `KrknHub.Run` sources a hub `env.sh`, loads a small prep module that returns
`list[str]` CLI arguments, then executes `run_kraken.py` with the krkn tool venv interpreter and `KUBECONFIG`.

Example scenarios under `test--suites/ts--example/test_Krkn.py` are currently placeholders while hub wiring for the
new design lands. Collect them with:
```bash
.venv/bin/pytest -m krkn --collect-only
```


## Tool layout

Defaults under `src/PyTest/vendor/`:
  - `vendor/krkn` — krkn checkout + `.venv` (created by `scripts/setup-tool-venvs.sh`)
  - `vendor/krknHub` — krkn-hub checkout (no dedicated venv)

Overrides:
  - `IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKN`
  - `IENG_PYTEST____FXT__KRKN____DEF_FXT_CFG__ROOTPATH_KRKNHUB`
  - fixture `krknPars.python` for an explicit interpreter

```bash
cd src/PyTest
# Optional: export KRKN_SRC=/path/to/krkn
./scripts/setup-tool-venvs.sh
# Clone https://github.com/krkn-chaos/krkn-hub into vendor/krknHub when needed.
```


## Operational boundary

Chaos scenarios can delete pods or create privileged temporary pods on worker nodes. Run them only with an appropriate
kubeconfig on an authorized, disposable test environment.

The integration does not currently provide implemented BSOD fault injection, guest crash detection, evidence
collection, or dump analysis. Proposed pressure and fault scenarios remain roadmap items and must not be presented as
supported or safe until they exist with explicit assertions and cleanup behavior.

See the [BSOD scenario roadmap](roadmap--krkn-lib-bsod.md) for that distinction and
[PyTest lifecycle](walkthrough--pytest.md) for the surrounding test flow.
