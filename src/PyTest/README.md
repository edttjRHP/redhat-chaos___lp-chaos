# PyTest Framework

Use these fixtures to provision VMs, communicate with their guests, and inject chaos into a Cluster Under Test (CUT).

## Choose a Fixture

| Fixture       | Purpose                                                                                       | Lifetime      |
|---------------|-----------------------------------------------------------------------------------------------|---------------|
| `fxtVM`       | Create VMs from a supplied or bundled YAML/Jinja template, with guest communication support.  | Per test      |
| `fxtVMbyBR`   | Deploy VM workloads at scale through benchmark-runner's separate worker process.              | Per module    |
| `fxtKrkn`     | Inject chaos into the CUT by running a Krkn Hub scenario.                                     | Per test      |

Both VM fixtures return `vmList` and `vmCreate(...)` for additional batches. Setup waits for provisioning and, by default, VM startup/Ready; it is not a
fire-and-forget background API. `vm.ssh` connects lazily when first used and supports guest commands, PowerShell, and file transfer. Ready does not prove guest
communication or authorization.

The `fxtVM` can create multiple VMs, but prefer benchmark-runner for scale-oriented workloads rather than the direct fixture's sequential VM operations. This
is usage guidance, not a measured performance guarantee.


## Prerequisites

- Use Python 3.11 and authenticated `oc`/`virtctl` access to a disposable CUT with OpenShift Virtualization.
- Supply usable guest images, compatible storage, and any image-source credentials; repository values are examples.
- For guest communication, provide OpenSSH tools and a guest SSH service/user authorized for the selected key. The bundled Windows template requires its
  Cloudbase-Init guest setup. Custom images/templates need equivalent setup.
- Install benchmark-runner in its configured vendor virtual environment, or Krkn and Krkn Hub at their configured paths, only when using those integrations.
  Krkn rendering also requires GNU `envsubst` and Bash.
- Align CLI context and runner kubeconfig with the same CUT. `kCfg` defaults to `KUBECONFIG` or `~/.kube/config`; shared CLI factories use their configured CLI
  context.


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

Configure image and credentials first. See [direct VMs and custom templates](docs/guide--vm.md), [benchmark-runner](docs/guide--benchmark-runner.md), and
[Krkn Hub](docs/guide--krkn.md).


## Best Practices

- **Namespace isolation is a recommendation, not automatic.** Prefer a dedicated namespace per test for `fxtVM` and standalone `fxtKrkn` scenarios. Prefer a
  dedicated namespace per module for module-scoped `fxtVMbyBR`, whose VMs remain available to that module's tests until teardown. Separate concurrent runs and
  parameter groups as needed.
- Share a module namespace only when tests intentionally share VMs and their mutations. Avoid having a function-scoped fixture create a namespace containing
  module-scoped or caller-owned resources: deleting that namespace removes its contents. Pre-create shared namespaces and manage their lifetime outside the
  shorter-lived fixture.
- The `fxtVM` reuses an existing generated name in the same namespace only with `reuseExisting=True`; reused VMs are not owned or deleted by that fixture.
  Supply valid existing credentials and restore any test changes yourself. The `fxtVMbyBR` does not offer this reuse option and rejects collisions with its
  generated VM names.
- VM teardown closes guest connections and attempts deletion only for VMs still matching their recorded ownership. The `fxtVM` also removes namespaces/Secrets
  it created; pre-existing ones remain. BR cleanup covers its tracked VMs, not every workload artifact or namespace. Krkn scenario cleanup is the scenario
  author's responsibility.
- Assert guest access explicitly when needed. Neither a Ready condition nor possession of an SSH key grants guest authorization. Review disruptive scenarios
  and retained artifacts before use; keep credentials out of source/logs.


## NamedDict Merge API Reference

The `NamedDict` deep merge (`|`) requires exact concrete types at existing keys, including nested keys, unless either value is `None`. Mismatches raise
`TypeError` with a backtick-delimited key path and expected/received types, not values. Key components use Python `repr` to represent quotes, backslashes and
control characters. Matching exact `dict` and `NamedDict` values recurse; matching exact lists concatenate into a new list without changing either input list.
Order, duplicates and shallow item identities are preserved; list items are not recursively merged. Reapplying an override appends its items again, so list
merges are intentionally not idempotent. Tuples, sets and custom sequences do not concatenate. Other matching values replace, and new keys are inserted
unchanged. Plain dictionaries cannot override existing non-null `NamedDict` sections. A `None` base accepts the override value; a `None` override replaces the
existing value with `None`.


## Test Locations and Examples

Put scenarios under `test--suites/` and isolated unit tests under `test--unit/`. The PyTest root's [`conftest.py`](conftest.py) registers the shared CLI, VM,
benchmark-runner, and Krkn fixtures for scenario tests. Use indirect parametrization for configuration and normal assertions for outcomes.

- `vm`: [direct VM example](test--suites/ts--example/test_VM.py) creates a VM and asserts a guest PowerShell response.
- `vm__br`: [benchmark-runner examples](test--suites/ts--example/test_BenchmarkRunner.py) include two VM Ready checks and a skipped legacy guest
  SSH/file-transfer test.
- `krkn`: [Krkn examples](test--suites/ts--example/test_Krkn.py) are skipped launcher-Pod/host-observation placeholders.

Adapt all environment-specific values. No current example implements a complete BSOD detection/analysis workflow. See the
[BSOD roadmap](docs/roadmap--krkn-lib-bsod.md) for planned work.


## Discovery and Offline Unit Tests

From `src/PyTest/`, use the existing test environment to inspect selection without executing fixture setup:
```bash
python -m pytest -c pytest.ini test--suites/ --collect-only
python -m pytest -c pytest.ini test--suites/ -m vm__br --collect-only
python -m pytest -c pytest.ini test--suites/ -m krkn --collect-only
python -m pytest -c pytest--unit.ini test--unit/
```

Only remove `--collect-only` on an authorized, disposable CUT after reviewing the selected tests' creation, disruption, guest-access, and cleanup requirements.
Unit tests use the separate `pytest--unit.ini` configuration.
