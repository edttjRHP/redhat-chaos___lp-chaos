# The `benchmark-runner` Integration

This project uses [`benchmark-runner`](https://github.com/redhat-performance/benchmark-runner) as an optional
VM-provisioning backend for OpenShift Virtualization. It complements the repository's template-based path by supporting
both a single Windows VM and a scaled group of VMs while presenting the same VM-oriented experience to a PyTest
scenario.


## What The Integration Provides

The intended setup path translates scenario configuration into a benchmark-runner workload and asks it to create the
requested VMs. The fixture calls `brMain` and reconciles VM UIDs; it does not independently wait for VM availability.
In the reviewed benchmark-runner sources, configuration and dispatch are cached before the fixture's temporary context
swap (known F2). This integration remains blocked pending upstream resolution, with runtime dependency compatibility
also unresolved. After successful provisioning, the scenario can use the framework's normal VM and guest-connectivity
capabilities. During teardown, the framework removes only the VMs it identified as belonging to that test run.

The `vm` example requests one VM and uses `fxtVMbyBR.vmList[0]` for guest commands and file transfer. The `vm__br`
example requests two VMs, confirms that both were returned, and checks guest SSH service reachability. These are
provisioning and connectivity examples, not benchmarks, BSOD injection, or broad scale tests; execution remains subject
to the blockers above.


## Fixture API

The module-scoped `fxtVMbyBR` fixture yields a frozen `FxtObjVMbyBR` bundle. Its `vmList` field holds every VM wrapper
managed by the fixture. Its `vmCreate` field is a callable that accepts the same configuration as the fixture, creates
another batch through benchmark-runner, appends those wrappers to `vmList`, and returns only the wrappers created by
that call.

Configure the initial batch with indirect parametrization:
```python
@pytest.mark.parametrize('fxtVMbyBR,', [({'count': 2},)], indirect=True)
def test_example(fxtVMbyBR):
    allVMs = fxtVMbyBR.vmList
    additionalVMs = fxtVMbyBR.vmCreate(count=1)
```

The fixture uses the same ownership-safe teardown for the initial and later batches. Cleanup deletes only resources
whose current UID still matches the UID recorded after benchmark-runner created them.


## Prerequisites and Selection

The `benchmark-runner` must be installed in the same Python environment as the test suite. The selected cluster must
already provide OpenShift Virtualization and a usable Windows image, and the local environment must provide
authenticated `oc` and `virtctl` access. Scaled provisioning also needs an eligible worker-node selection understood by
benchmark-runner.

After confirming the target environment and workload configuration, collect the benchmark-runner examples with:
```bash
.venv/bin/pytest -m 'vm or vm__br' --collect-only
```

Remove `--collect-only` only on an authorized test cluster. The scenarios create and later delete VM resources.


## Current boundary

The suite contains a single-VM example (`vm`, `count=1`) and a two-VM example (`vm__br`, `count=2`), both using the
registered `fxtVMbyBR` fixture and its `FxtObjVMbyBR` bundle. Workload installation, image preparation, benchmark
definition, and performance-result analysis remain responsibilities of benchmark-runner and the test environment rather
than this repository.

For the surrounding PyTest lifecycle, see [PyTest lifecycle](walkthrough--pytest.md). For the overall project status,
see the [README](../README.md).
