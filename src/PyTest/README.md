# PyTest Framework

Reusable PyTest framework for Layered Product Chaos on OpenShift Virtualization: VM lifecycle, guest access,
benchmark-runner provisioning, and krkn / krkn-hub chaos — selected by markers, each vendor in its **own venv**
under `vendor/`, fixtures still in-process.


## Markers

| Marker   | Fixture / path              | Vendor checkout env / venv                          |
|----------|-----------------------------|-----------------------------------------------------|
| `vm`     | `fxtVM`                     | Framework `.venv` only                              |
| `vm__br` | `fxtVMbyBR`                 | `BR_SRC` → `vendor/benchmark-runner/.venv`          |
| `krkn`   | `fxtKrkn`                   | `KRKN_SRC` / `KRKNHUB_SRC` → `vendor/krkn/.venv`    |

`conftest.py` loads `fixtures.CLI`, `fixtures.VM`, `fixtures.Krkn`, and `fixtures.BenchmarkRunner`. For `-m krkn` or
`-m vm__br`, `VendorSetup` creates that vendor’s venv, installs the checkout’s `requirements.txt` (plus editable),
and re-execs pytest under `vendor/<name>/.venv/bin/python`. Do not combine both vendor markers in one pytest
invocation; use `run_parallel.py --br --chaos` to run them together (separate venvs).


## Framework Capabilities

| Integration              | Role                                                                 |
|--------------------------|----------------------------------------------------------------------|
| PyTest                   | Selection, fixtures, assertions, reporting.                          |
| Configuration utilities  | Merge fixture settings; render Jinja2/YAML.                          |
| OpenShift Virtualization | VM lifecycle and guest access (`oc` / `virtctl` / SSH).              |
| benchmark-runner         | Optional scaled VM provisioning (`-m vm__br`, requires `BR_SRC`).    |
| krkn / krkn-hub          | Chaos scenarios (`-m krkn`, requires `KRKN_SRC` / `KRKNHUB_SRC`).    |

Indirect parametrization supplies fixture settings (`NamedDict` for nested dot access). VM fixtures expose `vmList`
and `vmCreate`. VM wrappers provide start/stop and lazy SSH (shell, PowerShell, SCP). `YAMLcfg` renders templates.

`NamedDict` deep merge (`|`) requires exact concrete types at existing keys; mismatches raise `TypeError` with a
backtick-delimited key path. Matching `dict` / `NamedDict` recurse; matching lists concatenate. Plain `dict` cannot
override existing `NamedDict` sections. `None` is not a type wildcard.


## Test Lifecycle

Scenarios: `test--suites/`. Unit tests: `test--unit/`. Markers: `pytest.ini`.

 1. PyTest selects scenarios (and may ensure a vendor venv from `-m`).
 2. Fixtures prepare cluster clients and, when requested, create VMs.
 3. The scenario acts and asserts on VM / Pod / Node / guest state.
 4. VM teardown closes SSH and deletes owned VMs when UIDs still match (best-effort).

Examples under `test--suites/ts--example/`:
  - `vm` — one Windows VM from the local template; PowerShell check.
  - `vm__br` — VMs via benchmark-runner; SSH / guest checks.
  - `krkn` — chaos against an existing VM (placeholders / skipped until hub support is ready).

These examples do not implement Windows bugcheck injection or crash-dump analysis.


## Current Limitations

  - Examples are environment-specific, not a portable acceptance suite.
  - Benchmark-runner paths and upstream config capture may need local adaptation; see
    [benchmark-runner guide](docs/guide--benchmark-runner.md).
  - See [fxtVM schema](fixtures/VM.py) for VM inputs.
  - Scaled BR provisioning uses `WORKER_NODE`; images and guest credentials are caller-owned.
  - Launcher-pod recovery expects `runStrategy: Always`; the repo template uses `Manual`.


### VM Template Context

`YAMLcfg.Render` `ctxData` for `conf/config--vm--template.yaml`:
  - Required: `vmDataVolumeSource`, `sshPublicKeyB64`
  - Optional defaults: `vmName`, `vmNS`, `vmDataVolumeName`, `vmStorageSize` (`96Gi`), `vmStorageClass`
    (`gp3-csi`), `vmCpuCores` (`4`), `vmMemory` (`8Gi`)

Callers must validate CDI source, names, storage, and SSH key material.


## Getting Started

Python 3.11. Framework `requirements.txt` is lean (pytest / Jinja2 / PyYAML). Vendor pins install into
`vendor/krkn/.venv` or `vendor/benchmark-runner/.venv` from the checkout selected by `-m`. Vendor venvs are
always created with **python3.11** (override via `VENDOR_PYTHON`); do not use a 3.12+ framework interpreter
for those pins (e.g. `numpy==1.26.4` has no newer wheels).

```bash
cd src/PyTest
python3.11 -m venv .venv
.venv/bin/pip install -r requirements--unit.txt    # unit tests / collect-only

# Local template VM (framework .venv)
.venv/bin/python -m pytest -c pytest.ini test--suites/ -m vm

# Chaos → vendor/krkn/.venv (created/updated on first run)
export KRKN_SRC=/path/to/krkn
export KRKNHUB_SRC=/path/to/krkn-hub
.venv/bin/python -m pytest -c pytest.ini test--suites/ -m krkn

# benchmark-runner → vendor/benchmark-runner/.venv
export BR_SRC=/path/to/benchmark-runner
.venv/bin/python -m pytest -c pytest.ini test--suites/ -m vm__br

# Both vendors in parallel (separate venvs)
.venv/bin/python run_parallel.py --br --chaos
```

Use an authorized, disposable OpenShift Virtualization cluster with `oc`, `virtctl`, OpenSSH, credentials, and images.


### Unit Tests

```bash
.venv/bin/python -m pytest -c pytest--unit.ini test--unit/
```


## Documentation

- [PyTest lifecycle](docs/walkthrough--pytest.md)
- [benchmark-runner integration](docs/guide--benchmark-runner.md)
- [krkn-lib integration](docs/guide--krkn-lib.md)
- [Future BSOD scenario example roadmap](docs/roadmap--krkn-lib-bsod.md)
