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
- The template-based provisioning example requires deployment-specific VM
  template context before that path can run.
- The implemented krkn-lib scenarios cover launcher-pod recovery and a
  non-destructive host inspection. Fault injection that targets BSOD behavior
  remains future work.


### VM Template Context

`conf/config--vm--template.yaml` is rendered with caller-owned `ctxData`; the
framework does not provide a default context file. Template context keys use
camelCase and preserve the established `vmName` and `vmNS` names. Its complete
context interface is:

- Required: `vmDataVolumeSource`, a non-empty mapping that identifies a real
  caller-owned image, and `sshPublicKeyB64`, the base64 encoding of the complete
  raw OpenSSH public-key text as a non-empty string. Rendering reports either
  missing value and rejects empty or basic-type mismatches instead of
  substituting an empty image source or SSH key.
- Defaultable: `vmName` (`vm-img--windows`), `vmNS` (`default`),
  `vmDataVolumeName` (`vmName + '--vhd--00-system'`), `vmStorageSize` (`96Gi`),
  `vmStorageClass` (`gp3-csi`), `vmCpuCores` (`4`), and `vmMemory` (`8Gi`).

The `96Gi` and `gp3-csi` values reproduce the source example; they do not
assert that `gp3-csi` exists on every cluster. Callers must override capacity
or storage class when their target cluster requires different values.

Raw caller inputs are checked before defaulting or conversion. All defaults,
native preparation, and serialization then form one initialization block.
Caller-owned string fields are converted and JSON-serialized once during
template initialization. Manifest expressions then reuse the prepared value:
```yaml
{% set vmNS = vmNS | default('default') | string | tojson %}
...
namespace: {{ vmNS }}
```
This preserves YAML-looking values such as `123`, `true`, and `null` as strings
and escapes quotes, backslashes, newlines, and control characters once. The
conversion defines representation, not Kubernetes domain validity. The
DataVolume name is derived from the native VM name before either value is
serialized, then reused for both its metadata and the VM volume reference. VM
layout identifiers such as `vhd--00-fs-rootfs`,
`cloud-init-disk`, `nic--0`, and `tablet` are fixed structural values, not
context knobs.

Structured image-source data is serialized without string conversion, so
nested mappings, lists, Booleans, and null values retain their native types.
CPU cores must be a positive integer and are serialized as a native number.
The pre-encoded `sshPublicKeyB64` value is passed through unchanged because the
cloud-init `write_files` entry declares `encoding: b64` and performs the
decoding in the guest.

These template-local checks intentionally stop at basic shape and type. The
caller remains responsible for the CDI source schema and supported backend,
Kubernetes quantity grammar, storage-class existence, whether converted names
meet Kubernetes naming rules, and whether `sshPublicKeyB64` is valid base64
that decodes to an OpenSSH public key. No image URL, source credential, or SSH
key is fabricated by the template, so the defaultable fields alone are not
enough to provision a VM.


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
