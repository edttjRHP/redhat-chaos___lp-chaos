# PyTest Lifecycle

PyTest is the orchestration layer for `bsodpoc`. It discovers scenarios under `test--suites/`, registers the
framework's shared capabilities, supplies each test with its requested environment, and runs teardown after the test
finishes.


## How Scenarios Are Organized

The suite uses markers to group scenarios by capability:
  - The `vm` covers single-VM provisioning through benchmark-runner and guest interaction.
  - The `vm__br` covers two-VM provisioning through benchmark-runner.
  - The `krkn` covers krkn-lib pod and node scenarios.

Scenario configuration is passed through PyTest parametrization. This lets an example select such things as its
Namespace, VM count, VM identity, or recovery window while the framework owns common setup and cleanup. The reader does
not need to understand the fixture implementation to run or assess a scenario.


## Lifecycle

PyTest first loads shared cluster, VM, benchmark-runner, and krkn-lib capabilities. For a selected scenario, the
lifecycle is:
 1. Read the scenario configuration and establish the requested CLI or library access.
 2. Provision VMs when the scenario requests framework-managed VMs, or connect to the existing VM named by a chaos
    scenario.
 3. Wait for the required starting condition, such as VM readiness or guest SSH reachability.
 4. Run the scenario action and assertions.
 5. Close guest connections and remove resources owned by the setup phase.

Teardown is designed to run even after an assertion fails. Existing resources that the framework did not create are
outside that ownership boundary, although a chaos scenario may intentionally disrupt a selected existing resource.


## Current Examples

The example suite covers single-VM and two-VM provisioning through `fxtVMbyBR` (`count=1` and `count=2`), guest file or
command access, launcher-pod recovery, and read-only worker-node inspection. Both provisioning examples use the
`FxtObjVMbyBR` bundle; the first accesses `vmList[0]`. Benchmark-runner's upstream configuration-capture blocker and
runtime dependency compatibility remain unresolved. Some example values and local file operations are
environment-specific.

The separate local template-provisioning path still needs caller-owned image-source and pre-base64-encoded SSH
public-key context wired into `VMcreate` before execution. Identity, storage, memory, and CPU have documented
deployment defaults, but the example storage class is not guaranteed to exist on every cluster. Template validation
covers required presence, source shape, SSH-key type, and a positive native CPU integer; deployment schema and content
validity remain caller responsibilities. The exact nine-key context interface and defaults are documented in the
[framework README](../README.md#vm-template-context); the fixed VM layout is in the
[VM template](../conf/config--vm--template.yaml).

No current PyTest scenario completes an end-to-end BSOD workflow. In particular, crash injection, guest-side detection,
evidence collection, and dump analysis remain outside the implemented examples.


## Safe Discovery

Create the documented Python environment, then inspect what PyTest would select without running setup:
```bash
.venv/bin/pytest --collect-only
.venv/bin/pytest -m vm__br --collect-only
.venv/bin/pytest -m krkn --collect-only
```

Only remove `--collect-only` after reviewing the selected scenarios and target environment. The VM and chaos groups can
create, delete, or disrupt cluster resources.

See [`benchmark-runner` integration](guide--benchmark-runner.md) and [`krkn-lib integration`](guide--krkn-lib.md) for
the responsibilities of those optional paths.
