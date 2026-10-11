# krkn-lib integration

This project uses [`krkn-lib`](https://github.com/krkn-chaos/krkn-lib) for
cluster-level operations in OpenShift Virtualization resiliency scenarios.
PyTest supplies scenario configuration and lifecycle management, krkn-lib works
with pods and nodes, and `oc` supplies the VM and VMI state needed for
OpenShift Virtualization assertions.


## What the integration provides

For each selected chaos scenario, the framework creates a krkn-lib client from
the active kubeconfig and combines it with OpenShift and virtualization command
access. A test can then observe or disrupt pods, inspect ready nodes, run a
controlled command on a VM's worker node, and compare those results with VM or
VMI state.

The current suite contains two examples:

- A disruptive recovery scenario deletes the launcher pod for an existing VM
  and waits for a different launcher pod and the VMI to return to a running
  state.
- A non-destructive host-access scenario identifies the VM's worker node,
  confirms that the node is ready, reads its kernel version, and logs selected
  host-kernel messages.

The first example evaluates virtualization recovery after pod disruption. The
second establishes observation access for later work. Neither example injects a
Windows bugcheck or validates a crash dump.


## Operational boundary

The krkn-lib examples expect a pre-existing VM selected by the scenario; they do
not provision that VM as part of the same lifecycle. They also create temporary
cluster resources when performing node-level access. Run them only with an
appropriate kubeconfig on an authorized, disposable test environment.

To verify selection without touching a cluster:
```bash
.venv/bin/pytest -m krkn --collect-only
```

Running without `--collect-only` can delete a launcher pod and create a
privileged temporary pod on a worker node.


## Current boundary

The integration does not currently provide implemented BSOD fault injection,
guest crash detection, evidence collection, or dump analysis. Proposed pressure
and fault scenarios remain roadmap items and must not be presented as supported
or safe until they exist with explicit assertions and cleanup behavior.

See the [BSOD scenario roadmap](roadmap--krkn-lib-bsod.md) for that distinction
and [PyTest lifecycle](walkthrough--pytest.md) for the surrounding test flow.
