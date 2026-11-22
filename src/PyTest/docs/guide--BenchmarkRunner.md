# Benchmark-Runner VM Fixture

Use `fxtVMbyBR` for scale-oriented VM provisioning through benchmark-runner in a separate worker process. Fixture setup still waits for the runner and, by
default, each VM's startup/Ready condition before returning; it does not return a background job handle. VMs and guest connections remain available until
module teardown.


## Prerequisites

  - The [Direct VM Fixture prerequisites](guide--VM.md#prerequisites) apply.
  - Tests using this fixture MUST be invoked via `python -m pytest ...`. Direct invocation via `pytest ...` MUST NOT be used. See
    [LibInVenv module documentation](../libs/Utils/LibInVenv.py) for details.
  - Supply a compatible workload/image and cluster access. For multiple VMs, set `SCALE_NODES` in the process environment, e.g. `"['worker-a']"`.
  - `setup.sh` installs benchmark-runner in its virtual environment at `IENG_PYTEST____FXT__BENCHMARKRUNNER____DEF_FXT_CFG__ROOTPATH_BR` (default:
    `vendor/BenchmarkRunner` under the PyTest root), the default `fxtInf.rootPath.br`.


## Minimal Usage

After preparing the workload/image and the namespace on the CUT:
```python
import pytest

from fixtures.BenchmarkRunner import FxtObjVMbyBR
from libs.CustomTypes import NamedDict


@pytest.mark.parametrize(
    'fxtVMbyBR,',
    [({'fxtInf': NamedDict(brCfg=NamedDict(count=2))},)],
    indirect=True,
)
def test_example(fxtVMbyBR):
    allVMs = fxtVMbyBR.vmList
    additionalVMs = fxtVMbyBR.vmCreate(
        fxtInf=NamedDict(brCfg=NamedDict(count=1)),
    )
```

The `vmCreate(...)` accepts the same settings, returns an additional batch, and appends it to `vmList`. Its VMs also remain until module teardown. See the
complete [fixture configuration](../fixtures/BenchmarkRunner.py#L290) and [factory API reference](../fixtures/BenchmarkRunner.py#L103), including parameters
and return values, and [namespace](../README.md#best-practices) and [cleanup](#guest-access-and-ownership) recommendations.


## Options

  - Under `fxtInf.brCfg`, use `count=1`, `wlName='windows'`, `wlKind='vm'`, and `brEnv={}` defaults. Workload selection uses `<wlName>_<wlKind>`; pass
    vendor-supported workload settings through the plain `brEnv` dictionary.
  - The runner budget is `timeout.brExecOH + (count * timeout.brExecVM)` minutes, with defaults `30` and `5`. The overhead mainly covers external VM Image
    Template import into an internal PVC; the per-VM allowance covers internal import from that PVC. Expiry stops waiting for the runner and attempts worker
    termination before reconciling created VMs for cleanup.
  - The `startVM=True` starts each VM and waits up to `timeout.vmStart='15m'`; `False` leaves startup to the caller. `timeout.vmRemove='5m'` controls the VM
    deletion wait at teardown. Nest these options under `fxtInf.brCfg`.
  - The `kCfg` selects the runner kubeconfig. Local artifacts are retained with `SAVE_ARTIFACTS_LOCAL=true`; set `RUN_ARTIFACTS_PATH` to choose their location
    and manage retention yourself.


## Guest Access and Ownership

Ready does not establish guest login. Access `vm.ssh` only if needed; the guest must run SSH and authorize the matching public key for
`fxtInf.gstCfg.usrCrd.sshUsr` (default: `qa-usr`). The wrapper uses the runner's `RUN_ARTIFACTS_PATH/ssh/vm_key` if that file exists, otherwise
`fxtInf.gstCfg.usrCrd.sshKey` (default: `~/.ssh/openshift-qe.pem`). A prepared SSH pair works only when its public key is authorized in the guest. Authorize it
through image preparation or a workload-supported guest setup mechanism; the fixture does not itself install a key in Windows, and a newly generated runner key
alone is not guest authorization.

VM names are generated per batch. Unlike `fxtVM`, this fixture has no reuse switch: a pre-existing VM with an expected generated name in the target namespace
causes setup to fail before invoking the runner. Teardown closes guest connections and attempts to delete tracked VMs whose identities still match. It does not
perform namespace-wide or complete vendor-artifact cleanup; provision and clean up other resources according to the workload's requirements.

The [enabled example](../test--suites/ts--example/test_BenchmarkRunner.py) checks two distinct VM Ready conditions. The legacy single-VM guest
SSH/file-transfer example remains skipped; adapt its local paths and guest authorization before using it. Both examples use `vm__br`; neither implements BSOD
injection or performance-result analysis.


## Best Practices

  - The framework [Best Practices](../README.md#best-practices) apply.
  - Since it take time to create a VM, each test should re-use the prev. test VM, if possible. Check the `vmList` before creation.
  - Assert guest access explicitly when needed, see [Guest Access and Ownership](#guest-access-and-ownership).
