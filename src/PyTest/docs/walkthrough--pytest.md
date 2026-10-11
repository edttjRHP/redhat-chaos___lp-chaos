# PyTest Lifecycle

PyTest is the orchestration layer for `bsodpoc`. It discovers scenarios under
`test-suites/`, registers the framework's shared capabilities, supplies each
test with its requested environment, and runs teardown after the test finishes.


## How Scenarios Are Organized

The suite uses markers to group scenarios by capability:
  - `vm` covers repository-template VM provisioning and guest interaction.
  - `vm__br` covers VM provisioning through benchmark-runner.
  - `krkn` covers krkn-lib pod and node scenarios.

Scenario configuration is passed through PyTest parametrization. This lets an
example select such things as its Namespace, VM count, VM identity, or recovery
window while the framework owns common setup and cleanup. The reader does not
need to understand the fixture implementation to run or assess a scenario.


## Lifecycle

PyTest first loads shared cluster, VM, benchmark-runner, and krkn-lib
capabilities. For a selected scenario, the lifecycle is:

1. Read the scenario configuration and establish the requested CLI or library
   access.
2. Provision VMs when the scenario requests framework-managed VMs, or connect
   to the existing VM named by a chaos scenario.
3. Wait for the required starting condition, such as VM readiness or guest SSH
   reachability.
4. Run the scenario action and assertions.
5. Close guest connections and remove resources owned by the setup phase.

Teardown is designed to run even after an assertion fails. Existing resources
that the framework did not create are outside that ownership boundary, although
a chaos scenario may intentionally disrupt a selected existing resource.


## Current Examples

The example suite covers template-based VM creation and guest file or command
access, multi-VM provisioning through benchmark-runner, launcher-pod recovery,
and read-only worker-node inspection. Some example values and local file
operations are environment-specific. In addition, the default template name
used by the template-provisioning path is not present in the current `conf/`
directory, so that example needs explicit configuration before execution.

No current PyTest scenario completes an end-to-end BSOD workflow. In
particular, crash injection, guest-side detection, evidence collection, and
dump analysis remain outside the implemented examples.


## Safe Discovery

Create the documented Python environment, then inspect what PyTest would select
without running setup:
```bash
.venv/bin/pytest --collect-only
.venv/bin/pytest -m vm__br --collect-only
.venv/bin/pytest -m krkn --collect-only
```

Only remove `--collect-only` after reviewing the selected scenarios and target
environment. The VM and chaos groups can create, delete, or disrupt cluster
resources.

See [`benchmark-runner` integration](guide--benchmark-runner.md) and
[`krkn-lib integration`](guide--krkn-lib.md) for the responsibilities of those
optional paths.
