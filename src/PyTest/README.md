# bsodpoc

`bsodpoc` is a PyTest framework for exercising Windows virtual-machine
resiliency on OpenShift Virtualization. It brings VM provisioning, guest access,
cluster operations, and chaos orchestration into one test lifecycle so a
scenario can prepare its resources, run an assertion, and clean up what it
created.

The repository is a proof of concept, not a complete BSOD validation system.
Its current examples cover VM creation and guest reachability, VM provisioning
through benchmark-runner, recovery after disruption of a VM launcher pod, and
read-only access to the VM's worker node. They do not yet inject a Windows
bugcheck, detect a crash, or collect and analyze crash evidence.


## Framework Capabilities

| Integration                   | Role                                                              |
|-------------------------------|-------------------------------------------------------------------|
| PyTest                        | Organizes selection, setup, teardown, assertions, and reporting.  |
| OpenShift Virtualization      | Supplies the Windows VMs, VM lifecycle, and guest connectivity.   |
| benchmark-runner              | Provides an alternative path for provisioning one or more VMs.    |
| krkn-lib                      | Provides Pod and Node operations for resiliency scenarios.        |

The framework can provision VMs from a repository template or delegate
provisioning to benchmark-runner. Tests interact with OpenShift through `oc`,
with virtual machines through `virtctl`, and with Windows guests over SSH.
krkn-lib adds cluster-level disruption and observation capabilities while `oc`
continues to provide the OpenShift Virtualization state used in assertions.


## Test Lifecycle

At a conceptual level, each test follows the same flow:

 1. PyTest selects a scenario and supplies its configuration.
 2. The framework prepares cluster clients and, when requested, creates VMs.
 3. The scenario checks its starting conditions, performs its action, and
    evaluates the resulting VM, Pod, Node, or guest state.
 4. Teardown closes guest connections and removes resources owned by the test.

Some chaos examples intentionally operate on an existing VM instead of one
created during the same test. Cluster selection and scenario configuration must
therefore be reviewed before running anything beyond collection.


## Current Limitations

- The example suite is environment-specific and should not be treated as a
  portable acceptance suite.
- benchmark-runner is used by the framework but is not pinned in
  `requirements.txt`.
- The template-based provisioning example expects a template name that is not
  present under `conf/` at this ref; the included VM template must be selected
  explicitly before that path can run.
- The implemented krkn-lib scenarios cover launcher-pod recovery and a
  non-destructive host inspection. Fault injection that targets BSOD behavior
  remains future work.


## Getting Started

The repository targets Python 3.11:
```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest --collect-only
```

Collection is the safe first check. Executing the examples requires authorized
access to a disposable OpenShift Virtualization environment plus the CLIs,
credentials, images, and guest access needed by the selected scenario. The
tests can create or delete cluster resources.


### Unit Tests

The isolated unit suite mocks process, SSH, and cluster boundaries and does not
collect the environment-specific scenarios under `test-suites/`:
```bash
.venv/bin/pip install -r requirements--unit.txt
.venv/bin/python -m pytest -c pytest--unit.ini test--unit/
```


## Documentations

- [PyTest lifecycle](docs/walkthrough--pytest.md)
- [benchmark-runner integration](docs/guide--benchmark-runner.md)
- [krkn-lib integration](docs/guide--krkn-lib.md)
- [BSOD scenario roadmap](docs/roadmap--krkn-lib-bsod.md)
