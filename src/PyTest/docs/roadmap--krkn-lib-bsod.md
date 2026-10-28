# BSOD Scenario Roadmap

This roadmap separates what the PyTest framework demonstrates today from the broader BSOD validation workflow it may
support in the future. The framework is currently a resiliency proof of concept; a successful example run does not
establish that a Windows bugcheck was triggered, detected, or diagnosed.


## Current Example Scaffolding

The current suite includes a local-template VM/SSH example and four other examples. Both benchmark-runner examples
remain blocked by benchmark-runner's known upstream configuration-capture issue and unresolved runtime dependency
compatibility. The local `VMcreate` derives template context from `gstCfg.imgSrc.CDI` and `gstCfg.usrCrd.sshKey`;
callers must supply usable local raw credential files and an unencrypted private key. See
the [README](../README.md#current-limitations) and [benchmark-runner guide](guide--benchmark-runner.md).

The examples cover:
- creation and start of one VM through `fxtVM` and a fixed PowerShell response check (`vm`);
- creation of one Windows VM through benchmark-runner (`vm`, `count=1`) and guest access through the `FxtObjVMbyBR`
  bundle's `vmList[0]`;
- creation of two VMs through benchmark-runner (`vm__br`, `count=2`) and confirmation of guest reachability;
- disruption of an existing VM's launcher pod followed by an assertion that a replacement pod and the VM return to a
  running state; and
- read-only execution on the worker node hosting a VM, including collection of selected host-kernel log lines as
  diagnostic context.

The launcher-pod scenario is a recovery test, not a BSOD test. The host-side scenario proves access for later
experiments but does not inject a fault.


## Planned Capability Areas

Future BSOD-focused scenarios may build on those foundations in stages:
 1. Add controlled host or workload pressure with explicit safety boundaries and cleanup behavior.
 2. Correlate cluster, VM, guest, and host observations over the same scenario timeline.
 3. Integrate an external Windows crash detector and evidence collector.
 4. Define assertions that distinguish a recovered VM, an expected bugcheck, an unrelated infrastructure failure, and
    an inconclusive run.
 5. Make scenarios portable through documented environment profiles instead of repository-specific example values.

These items are aspirations only. This ref contains no implemented vCPU-stall, time-skew, resource-contention,
network-fault, or Windows crash-detection scenario, and it does not map particular fault types to guaranteed Windows
stop codes.


## Scope Boundary

The `krkn-lib` supplies cluster-level operations; OpenShift Virtualization supplies VM state and placement; guest
tooling supplies Windows observations. Crash detection, dump collection, and dump analysis require a separate
capability and must not be inferred from cluster recovery alone.

See [`krkn-lib integration`](guide--krkn-lib.md) for the implemented chaos boundary,
[`benchmark-runner integration`](guide--benchmark-runner.md) for scaled VM provisioning, and
[PyTest lifecycle](walkthrough--pytest.md) for scenario orchestration.
