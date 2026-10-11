# benchmark-runner integration

This project uses
[`benchmark-runner`](https://github.com/redhat-performance/benchmark-runner) as
an optional VM-provisioning backend for OpenShift Virtualization. It complements
the repository's template-based path by supporting both a single Windows VM and
a scaled group of VMs while presenting the same VM-oriented experience to a
PyTest scenario.


## What the integration provides

During PyTest setup, the framework translates scenario configuration into a
benchmark-runner workload, asks benchmark-runner to create the requested VMs,
and waits until those VMs are available to the test. The scenario can then use
the framework's normal VM and guest-connectivity capabilities. During teardown,
the framework removes only the VMs it identified as belonging to that test run.

The current example requests two VMs, confirms that both were returned, and
checks guest SSH service reachability. This demonstrates provisioning and basic
connectivity; it is not a benchmark, a BSOD injection, or a broad scale test.


## Fixture API

The module-scoped `fxtVMbyBR` fixture yields a frozen `FxtObjVMbyBR` bundle.
Its `vmList` field holds every VM wrapper managed by the fixture. Its `vmCreate`
field is a callable that accepts the same configuration as the fixture, creates
another batch through benchmark-runner, appends those wrappers to `vmList`, and
returns only the wrappers created by that call.

Configure the initial batch with indirect parametrization:
```python
@pytest.mark.parametrize('fxtVMbyBR,', [({'count': 2},)], indirect=True)
def test_example(fxtVMbyBR):
    allVMs = fxtVMbyBR.vmList
    additionalVMs = fxtVMbyBR.vmCreate(count=1)
```

The fixture uses the same ownership-safe teardown for the initial and later
batches. Cleanup deletes only resources whose current UID still matches the UID
recorded after benchmark-runner created them.


## Prerequisites and selection

benchmark-runner must be installed in the same Python environment as the test
suite. It is not included in `requirements.txt` at this ref. The selected
cluster must already provide OpenShift Virtualization and a usable Windows image,
and the local environment must provide authenticated `oc` and `virtctl` access.
Scaled provisioning also needs an eligible worker-node selection understood by
benchmark-runner.

After confirming the target environment and workload configuration, collect the
benchmark-runner examples with:
```bash
.venv/bin/pytest -m vm__br --collect-only
```

Remove `--collect-only` only on an authorized test cluster. The scenario creates
and later deletes VM resources.


## Current boundary

The repository currently carries one example of the scaled path. The single-VM
path exists as a framework capability but has no example scenario in the current
suite. Workload installation, image preparation, benchmark definition, and
performance-result analysis remain responsibilities of benchmark-runner and the
test environment rather than this repository.

For the surrounding PyTest lifecycle, see
[PyTest lifecycle](walkthrough--pytest.md). For the overall project status, see
the [README](../README.md).
