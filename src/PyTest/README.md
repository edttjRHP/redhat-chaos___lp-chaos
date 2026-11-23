# PyTest Test Suite Framework
## Prerequisites

  - Use Python 3.11 and authenticated `oc` access to a disposable Cluster Under Test (CUT).
  - Align CLI context and runner kubeconfig with the same CUT. `kCfg` defaults to `KUBECONFIG` or `~/.kube/config`; shared CLI factories use their configured
    CLI context.
  - Read the `## Prerequisites` sections of all fixture guides (`docs/guide--*.md`):
      - [`guide--VM.md`](docs/guide--VM.md#prerequisites)
      - [`guide--Krkn.md`](docs/guide--Krkn.md#prerequisites)
      - [`guide--BenchmarkRunner.md`](docs/guide--BenchmarkRunner.md#prerequisites)


## Setup

Run setup from `src/PyTest/` in the Python environment you intend to use:
```bash
./setup.sh    # Production Install (excludes `requirements--unit.txt`).
./setup.sh 1  # Development Install (includes `requirements--unit.txt`).
```

Production Install:
  - Developing Test Suites for product test.
  - Running the Test Suites in CI for product test.
Development Install:
  - Developing this PyTest Test Suite Framework itself.


## Configure and Write a Test

Use indirect parametrization and `NamedDict` for schema sections. Keep payloads plain dictionaries: `cliCfg.ocCfg`, `cliCfg.vcCfg`, `fxtInf.vmCfg.ctxData`,
`fxtInf.brCfg.brEnv`, `fxtInf.krknCfg.krknEnv`, and `fxtInf.gstCfg.imgSrc.CDI.credInfo.vaultInfo`. Common settings are `ns` (default: `default`), `kCfg`,
`cliCfg.ocCfg`, `cliCfg.vcCfg`, and `testPars`. Dictionary sections merge; matching lists append. Existing value types must match unless either value is
`None`. See each fixture guide for options and prerequisites.

```python
import pytest

from fixtures.VM import FxtObjVM
from libs.CustomTypes import NamedDict


@pytest.mark.parametrize(
    'fxtVM,',
    [({
        'ns': 'my-test',
        'fxtInf': NamedDict(vmCfg=NamedDict(namePfx='my-vm')),
    },)],
    indirect=True,
)
def test_GuestCommand(fxtVM: FxtObjVM) -> None:
    result = fxtVM.vmList[0].ssh.RunPowerShell('Write-Output ready')
    assert result.success
```

Configure image and credentials first. See [direct VMs and custom templates](docs/guide--VM.md), [benchmark-runner](docs/guide--BenchmarkRunner.md), and
[Krkn Hub](docs/guide--Krkn.md).


## Best Practices

  - **Namespace isolation.** A fixture should fully owns the namespace it creates, so it can tear it down at the end of its lifetime (hence safely and
    completely cleanup all created test resources). NOTE: Do not place resources need to be retained, after fixture lifetime, inside a fixture-owned namespace.
  - Review the logs to ensure no credentials leaked.
  - Read the `## Best Practices` sections of all fixture guides (`docs/guide--*.md`):
      - [`guide--VM.md`](docs/guide--VM.md#best-practices)
      - [`guide--Krkn.md`](docs/guide--Krkn.md#best-practices)
      - [`guide--BenchmarkRunner.md`](docs/guide--BenchmarkRunner.md#best-practices)


## NamedDict Merge API Reference

The `NamedDict` deep merge (`|`) requires exact concrete types at existing keys, including nested keys, unless either value is `None`. Mismatches raise
`TypeError` with a backtick-delimited key path and expected/received types, not values. Key components use Python `repr` to represent quotes, backslashes and
control characters. Matching exact `dict` and `NamedDict` values recurse; matching exact lists concatenate into a new list without changing either input list.
Order, duplicates and shallow item identities are preserved; list items are not recursively merged. Reapplying an override appends its items again, so list
merges are intentionally not idempotent. Tuples, sets and custom sequences do not concatenate. Other matching values replace, and new keys are inserted
unchanged. Plain dictionaries cannot override existing non-null `NamedDict` sections. A `None` base accepts the override value; a `None` override replaces the
existing value with `None`.


## Test Locations

Use `test--unit/` for isolated Python-module tests, `test--contract/` for offline template contracts, and `test--suites/` for live CUT scenarios.

Tests MUST be invoked via `python -m pytest ...`. Direct invocation via `pytest ...` MUST NOT be used. See
[LibInVenv module documentation](libs/Utils/LibInVenv.py) for details.

Run the commands below from `src/PyTest/` using the existing test environment.


### Unit Tests

Each unit test MUST test only its corresponding module. All external calls MUST be mocked, including built-ins such as `open()`.

Normally leave standard binding or decorator machinery, such as `functools.partial`, real. When the target it wraps or binds is external to the unit being
tested, whether another function in the same file or an imported one, mock it at the module lookup before the binding is made, then assert the delegation and
results. If the target is a nested or local function that belongs to the unit, it MUST not be mocked as it is a part of the unit being tested.

Use the separate `pytest--unit.ini` configuration:
```bash
python -m pytest -c pytest--unit.ini test--unit/
```


### Offline Template Contract Tests

The VM-template contracts are in [`test--contract/test_YAML_confTemplate.py`](test--contract/test_YAML_confTemplate.py). They read the template under
`conf/VM/` and use real Jinja rendering and YAML parsing, rather than mocking those collaborators as isolated unit tests do.

Use `pytest--contract.ini` to select them explicitly.
```bash
python -m pytest -c pytest--contract.ini
```


### Live Test Suites

Put live CUT scenarios under `test--suites/` (examples are in `test--suites/ts--example/`). The root [`conftest.py`](conftest.py) registers the
[fixtures](#choose-a-fixture). Use indirect parametrization for configuration and normal assertions for outcomes.

Markers in the `ts--example/`:
  - `vm`: A [Direct VM example](test--suites/ts--example/test_VM.py) creates a VM and asserts a guest PowerShell response.
  - `vm__br`: The [benchmark-runner examples](test--suites/ts--example/test_BenchmarkRunner.py) include two VM Ready checks and a skipped legacy guest SSH /
    file-transfer test.
  - `krkn`: A [KrknHub `pod-scenario` example](test--suites/ts--example/test_Krkn.py) kills a Pod in a Deployment and asserts a new one is respawned
    automatically.

Inspect live-suite selection without executing fixture setup:
```bash
python -m pytest -c pytest.ini test--suites/ --collect-only
python -m pytest -c pytest.ini test--suites/ -m vm --collect-only
python -m pytest -c pytest.ini test--suites/ -m vm__br --collect-only
python -m pytest -c pytest.ini test--suites/ -m krkn --collect-only
```

Only remove `--collect-only` on an authorized, disposable CUT after reviewing the selected tests' creation, disruption, guest-access, and cleanup requirements.


#### Choose a Fixture

Use these fixtures to provision VMs, communicate with their guests, and inject chaos into a Cluster Under Test (CUT).

| Fixture       | Purpose                                                                                       | Lifetime      |
|---------------|-----------------------------------------------------------------------------------------------|---------------|
| `fxtVM`       | Create VMs from a supplied or bundled YAML/Jinja template, with guest communication support.  | Per module    |
| `fxtVMbyBR`   | Deploy VM workloads at scale through benchmark-runner's separate worker process.              | Per module    |
| `fxtKrkn`     | Inject chaos into the CUT by running a Krkn Hub scenario.                                     | Per test      |

Both VM fixtures return `vmList` and `vmCreate(...)` for additional batches. Setup waits for provisioning and, by default, VM startup/Ready; it is not a
fire-and-forget background API. `vm.ssh` connects lazily when first used and supports guest commands, PowerShell, and file transfer. Ready does not prove guest
communication or authorization.

The `fxtVM` can create multiple VMs, but prefer benchmark-runner for scale-oriented workloads rather than the direct fixture's sequential VM operations. This
is usage guidance, not a measured performance guarantee.
