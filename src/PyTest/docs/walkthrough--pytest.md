# PyTest Lifecycle

PyTest is the orchestration layer for this framework. It discovers scenarios under `test--suites/`, registers the
framework's shared capabilities, supplies each test with its requested environment, and runs teardown after the test
finishes.


## How Scenarios Are Organized

The suite uses markers to group scenarios by capability:
  - The `vm` covers single-VM provisioning through the local template or benchmark-runner and guest interaction.
  - The `vm__br` covers two-VM provisioning through benchmark-runner.
  - The `krkn` covers krkn / krkn-hub Chaos scenarios.

Scenario configuration is passed through PyTest parametrization. This lets an example select such things as its
Namespace, VM count, VM identity, or recovery window while the framework owns common setup and cleanup. The reader does
not need to understand the fixture implementation to run or assess a scenario.


## Lifecycle

PyTest first loads shared cluster, VM, benchmark-runner, and krkn/krkn-hub capabilities. Tool backends run in isolated
venvs via subprocess. For a selected scenario, the lifecycle is:
 1. Read the scenario configuration and establish the requested CLI or library access.
 2. Provision VMs when the scenario requests framework-managed VMs, or connect to the existing VM named by a chaos
    scenario.
 3. Wait for the required starting condition, such as VM readiness or guest SSH reachability.
 4. Run the scenario action and assertions.
 5. Close guest connections and remove resources owned by the setup phase.

Teardown is designed to run even after an assertion fails. Existing resources that the framework did not create are
outside that ownership boundary, although a chaos scenario may intentionally disrupt a selected existing resource.


## Current Examples

The local-template example uses `fxtVM` to create and start one VM and check a fixed PowerShell response via SSH.
The other examples cover single-VM and two-VM provisioning through `fxtVMbyBR` (`count=1` and `count=2`), guest file or
command access, launcher-pod recovery, and read-only worker-node inspection. Both benchmark-runner examples use the
`FxtObjVMbyBR` bundle; the first accesses `vmList[0]`. Benchmark-runner's upstream configuration-capture blocker may
still apply inside its tool venv; shared-site-packages conflicts are avoided by process isolation. Some example values
and local file operations are environment-specific.

See the [fxtVM configuration schema](../fixtures/VM.py) for VM inputs.
Use `cfgFile` (formerly `configFile`) for the manifest template.
Creation and import waits are `vmCfg.timeout.vmCreate` (`15m`) and
`vmCfg.timeout.dvImport` (`30m`), not a top-level `timeout`.
All newly created VM wrappers and ownership records are registered before
waiting for each actually referenced DataVolume to reach `Succeeded`.
Reused VMs are not import-waited. A VM without DataVolumes needs no import
wait. Import failure uses the existing VM, Secret, Namespace cleanup order.

`vmCfg.ctxData=NamedDict()` (default) or `None` generates image-source
credentials, namespace and SSH public-key context. Nonempty `NamedDict`
context bypasses generation. Fixture and per-call contexts use the normal
configuration merge, preserving inherited keys unless overridden. The
default context rejects plain `dict` overrides rather than converting them.
For the default template supply `vmDataVolumeSource`
and `sshPublicKeyB64`, plus `vmNS` when selecting a namespace. The template
uses its own defaults for omitted optional fields. The fixture only injects
the per-VM `vmName`; it does not add a generated namespace or key to custom
context. Guest connection credentials remain configured under `gstCfg`.

Identity, storage, memory, and CPU have documented
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

See [`benchmark-runner` integration](guide--benchmark-runner.md) and [krkn / krkn-hub integration](guide--krkn-lib.md)
for the responsibilities of those optional paths.
