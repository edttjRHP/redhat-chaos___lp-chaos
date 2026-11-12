# BSOD Scenario Roadmap

Current examples demonstrate direct VM guest communication and benchmark-runner VM readiness. The legacy BR guest example and both KrknHub examples remain
skipped. Provisioning, Ready, and guest login do not establish that a Windows bugcheck was injected, detected, or diagnosed. See
[current examples](../README.md#test-locations-and-examples).


## Planned Work

1. Implement controlled chaos scenarios with explicit CUT targeting, safety boundaries, and rollback.
2. Correlate cluster, VM, guest, and host observations on the same timeline.
3. Integrate Windows crash detection, evidence collection, and dump analysis.
4. Distinguish an expected bugcheck from recovery, unrelated infrastructure failure, or an inconclusive result.
5. Replace environment-specific example values with documented environment profiles.

These are proposals, not supported guarantees. No current example implements vCPU stalls, time skew, resource contention, network faults, or Windows crash
detection, or maps faults to guaranteed stop codes.

Use the [Krkn guide](guide--krkn.md) for current chaos configuration and the [VM](guide--vm.md) or [benchmark-runner](guide--benchmark-runner.md) guide for
provisioning and guest requirements.
