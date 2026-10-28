# PyTest Framework

This reusable PyTest framework supports Layered Product Chaos testing on OpenShift through shared fixtures,
configuration utilities, CLI wrappers, and scenario-specific assertions. Its current integrations focus on OpenShift
Virtualization: VM lifecycle management, guest access, `benchmark-runner` provisioning, and `krkn-lib` Chaos
orchestration.


## Framework Capabilities

| Integration                   | Role                                                              |
|-------------------------------|-------------------------------------------------------------------|
| PyTest                        | Organizes selection, fixtures, assertions, and reporting.         |
| Configuration utilities       | Merge fixture settings and render Jinja2/YAML configuration.      |
| OpenShift Virtualization      | Provides VM lifecycle and guest access through CLI/SSH wrappers.  |
| benchmark-runner              | Provides an alternative path for provisioning one or more VMs.    |
| krkn-lib                      | Provides Pod and Node operations for resiliency scenarios.        |

The `conftest.py` registers the `fxtOCcli`, `fxtVirtCtlCLI`, `fxtVM`,`fxtVMbyBR`, and `fxtKrkn` fixtures. Indirect
parametrization supplies fixture settings (use `NamedDict` for nested settings that need dot access). The VM fixtures
expose a shared `vmList` and a `vmCreate` factory for additional batches. VM wrappers provide start/stop operations and
lazy SSH access, including shell and PowerShell commands and SCP file transfer. `YAMLcfg` renders templates and
converts YAML, JSON, and dictionaries.

`NamedDict` deep merge (`|`) requires exact concrete types at existing keys, including nested keys; mismatches raise
`TypeError` with a backtick-delimited key path and expected/received types, not values. Key components use Python
`repr` to represent quotes, backslashes and control characters. Matching `dict` and `NamedDict` values recurse;
matching exact lists concatenate into a new list without changing either input list. Order, duplicates and shallow
item identities are preserved; list items are not recursively merged. Reapplying an override appends its items again,
so list merges are intentionally not idempotent. Tuples, sets and custom sequences do not concatenate. Other matching
values replace, and new keys are inserted unchanged. Plain dictionaries cannot override existing
`NamedDict` sections. `None` is not a wildcard for a different type.


## Test Lifecycle

Scenarios live under `test--suites/`; Framework Unit Test live under `test--unit/`. Markers in `pytest.ini`.

A scenario's lifecycle is:
 1. PyTest selects a scenario and supplies its configuration.
 2. The framework prepares cluster clients and, when requested, creates VMs.
 3. The scenario checks its starting conditions, performs its action, and evaluates the resulting VM, Pod, Node, or
    guest state.
 4. VM-fixture teardown attempts to close cached guest connections and delete VMs whose current UID matches the
    recorded ownership UID. Lookup and deletion are not atomic; cleanup can fail. Reused VMs are not deleted.

The `vm` examples request one Windows VM through local-template `fxtVM` or `fxtVMbyBR`. The local example starts the VM
and checks a fixed PowerShell response; the benchmark-runner example uses guest commands and file transfer. The
`vm__br` example requests two VMs through `fxtVMbyBR` and checks their SSH
service. The `krkn-lib` examples target an existing VM: one deletes its launcher Pod and checks recovery; the other
reads kernel information through a temporary privileged Pod. These examples do not implement Windows bugcheck
injection, Guest OS crash detection, or crash-dump collection and analysis.


## Current Limitations

Limitations:
  - The example suite is environment-specific and should not be treated as a portable acceptance suite.
  - The single-VM benchmark-runner example's local file paths require adaptation.
  - Both benchmark-runner examples remain blocked by upstream configuration capture and unresolved runtime dependency
    compatibility; see the [benchmark-runner guide](docs/guide--benchmark-runner.md).
  - See the [fxtVM configuration schema](fixtures/VM.py) for VM inputs.
  - Scaled benchmark-runner provisioning reads `WORKER_NODE`; workload/image preparation and matching Guest OS
    credentials remain caller responsibilities.
  - The launcher-pod recovery example expects a VM with `runStrategy: Always`; the repository template uses `Manual`.
    The node-inspection example reads data but still creates and deletes privileged cluster resources.


### VM Template Context

The `YAMLcfg.Render` accepts caller-owned `ctxData` for `conf/config--vm--template.yaml`:
  - Required:
      - vmDataVolumeSource: A non-empty mapping that identifies a real CDI image source.
      - sshPublicKeyB64: A non-empty string containing the base64 encoding of the complete raw OpenSSH public-key text.
  - Optional (default):
      - vmName (`'vm-img--windows'`)
      - vmNS (`'default'`)
      - vmDataVolumeName (`vmName + '--vhd--00-system'`)
      - vmStorageSize (`'96Gi'`)
      - vmStorageClass (`'gp3-csi'`)
      - vmCpuCores (`4`): Constraint: `> 0`.
      - vmMemory (`'8Gi'`)

String fields are converted and JSON-serialized once during initialization, then reused in manifest expressions:
```yaml
{% set vmNS = vmNS | default('default') | string | tojson %}
...
namespace: {{ vmNS }}
```
This preserves YAML-looking strings and escapes special characters.

Callers must validate the CDI source/backend, Kubernetes names and quantities, storage capacity/class, and
base64/OpenSSH key content. The `gp3-csi` is only an example default, not a cluster guarantee. The Windows image must
support the template's Cloudbase-Init script and SSH setup. Defaults do not supply an image, source credentials, or a
key.


## Getting Started

Run from the repository root with Python 3.11. For local discovery, the isolated-unit dependencies are sufficient
because `benchmark-runner` and `krkn-lib` are imported only when their fixtures execute:
```bash
cd src/PyTest
python3.11 -m venv .venv
.venv/bin/pip install -r requirements--unit.txt
.venv/bin/python -m pytest -c pytest.ini test--suites/ --collect-only
.venv/bin/python -m pytest -c pytest.ini test--suites/ts--example/test_VM.py --collect-only
.venv/bin/python -m pytest -c pytest.ini test--suites/ -m vm__br --collect-only
.venv/bin/python -m pytest -c pytest.ini test--suites/ -m krkn --collect-only
```

Before executing any scenario, verify compatible runtime dependencies and address applicable example limitations.
Review the Namespace, VM identity, CLI context, and Krkn's kubeconfig together. Run only on an authorized, disposable
OpenShift Virtualization environment with the required `oc`, `virtctl`, OpenSSH tools, credentials, images, and Guest
access.


### Unit Tests

The isolated unit suite mocks process, SSH, and cluster boundaries and does not collect the environment-specific
scenarios under `test--suites/`. From `src/PyTest/`, using the environment above:
```bash
.venv/bin/python -m pytest -c pytest--unit.ini test--unit/
```


## Documentation

- [PyTest lifecycle](docs/walkthrough--pytest.md)
- [benchmark-runner integration](docs/guide--benchmark-runner.md)
- [krkn-lib integration](docs/guide--krkn-lib.md)
- [Future BSOD scenario example roadmap](docs/roadmap--krkn-lib-bsod.md)
