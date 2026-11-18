# Direct VM Fixture

Use `fxtVM` to create VMs you can communicate with through `vm.ssh`, provided the image/template configures guest SSH. It supports a user VM YAML/Jinja
template and multiple VMs; prefer [benchmark-runner](guide--benchmark-runner.md) for scale-oriented workloads. Setup creates the batch, then waits for each
VM's referenced DataVolumes and starts that VM by default. A successful startup wait establishes VM Ready, not a working guest login.

See the [minimal test and namespace recommendations](../README.md). The fixture is function-scoped. `vmList` contains all wrappers returned during that test;
`vmCreate(...)` returns only the additional batch and appends it to `vmList`:
```python
extraVMs = fxtVM.vmCreate(
    fxtInf=NamedDict(vmCfg=NamedDict(namePfx='extra-vm', count=2)),
)
```

See the complete [fixture configuration and parametrization](../fixtures/VM.py#L474) and [factory API reference](../fixtures/VM.py#L191) for all parameters and
returned values.


## Essential Options

Pass these under `fxtInf.vmCfg` using `NamedDict`:

| Option                | Default                   | Use                                                                           |
|-----------------------|---------------------------|-------------------------------------------------------------------------------|
| `count`               | `1`                       | Number of VMs; `0` skips initial VM creation.                                 |
| `namePfx`             | `lpqe-test`               | Name for one VM; indexed names for a larger batch.                            |
| `cfgTmpl`             | `cfg-template--vm.yaml`   | Absolute path or path relative to `conf/VM/` under the PyTest root.           |
| `ctxData`             | `{}`                      | Plain dictionary of template variables.                                       |
| `startVM`             | `True`                    | Start/wait for Ready; set `False` to manage startup yourself.                 |
| `reuseExisting`       | `False`                   | Reuse an existing generated VM name in the same namespace instead of failing. |
| `timeout.vmCreate`    | `15m`                     | VM creation wait.                                                             |
| `timeout.dvImport`    | `30m`                     | Wait for each referenced DataVolume import.                                   |
| `timeout.vmStart`     | `15m`                     | VM startup/Ready wait.                                                        |
| `timeout.vmRemove`    | `3m`                      | VM deletion wait during teardown.                                             |

Reused VMs are not checked for matching specifications, image, or credentials and are not import-waited or deleted by the fixture. They may still be started
when `startVM=True`. Supply an existing unencrypted private SSH key; reuse does not install guest authorization. Owned VMs, Secrets, and namespaces are cleaned
up at test teardown; pre-existing resources remain unowned. Do not place resources you want retained inside a namespace the fixture owns.


## Image and Guest Access

Configure `fxtInf.gstCfg.imgSrc.CDI` for the image import:
- `type='AWS-S3'` and an HTTP(S) `url` select the supported source.
- `credType='None'` uses an unauthenticated HTTP source; this is the string `'None'`, not Python `None`.
- `credType='K8s'` (default) uses `credInfo.name` (`aws-s3--cred`) with `vaultType='LocalFile'` and a plain `vaultInfo` mapping of `file--AccKeyId` and
  `file--AccKeySecret` to local raw credential files. Default paths are placeholders.
- Override the image URL through fixture settings or `IENG_PYTEST____FXT__VM____DEF_FXT_CFG__GSTCFG_IMGSRC_CDI_URL`.

Supported `imgSrc` shapes are shown below; replace the example URL and credential paths with your own values:
```yaml
CDI:
  type: AWS-S3
  credType: K8s
  url: https://images.example.test/windows.qcow2
  credInfo:
    name: aws-s3--cred
    vaultType: LocalFile
    vaultInfo:
      file--AccKeyId: /secretMount/secretFileForAccKeyId
      file--AccKeySecret: /secretMount/secretFileFoAccKeySecret
---
CDI:
  type: AWS-S3
  credType: 'None'
  url: https://images.example.test/windows.qcow2
```

Guest settings live under `fxtInf.gstCfg.usrCrd`: `sshUsr='qa-usr'` and `sshKey=NamedDict(prv=None, pub=None)`. For new VMs, a missing private key generates an
ephemeral ED25519 pair; a supplied private key with `pub=None` derives the public key using `ssh-keygen`. Supplied paths must be readable and private keys
unencrypted. Generated key files are removed at teardown. The bundled template passes the public key to its Windows guest setup; the image must support that
setup and an SSH server. Access `vm.ssh` only when guest communication is needed; setup itself does not probe SSH.

The [direct VM example](../test--suites/ts--example/test_VM.py) shows an unauthenticated image-source override and a PowerShell assertion. Adapt image,
storage, and guest setup to your CUT.


## Custom VM YAML Templates

Set `fxtInf.vmCfg.cfgTmpl` to your own YAML/Jinja file and pass extra variables in `ctxData`:
```python
fxtInf = NamedDict(vmCfg=NamedDict(
    cfgTmpl='/path/to/my-vm.yaml',
    ctxData={'vmMemory': '16Gi', 'vmStorageClass': 'my-storage'},
))
```

Render a `VirtualMachine` using the fixture-supplied `vmName` and `vmNS` so resource tracking matches the created VM. The fixture also supplies
`vmDataVolumeSource` from image settings and `sshPublicKeyB64` from guest key settings; it still resolves those settings when using a custom template. Provide
guest authorization in your template/image if you use `vm.ssh`. Other VM layouts and guest operating systems require a suitable custom template.

For the [bundled Windows template](../conf/VM/cfg-template--vm.yaml), required rendering inputs are a nonempty `vmDataVolumeSource` mapping and base64-encoded
raw OpenSSH public-key text in `sshPublicKeyB64`. The fixture fills these; direct `YAMLcfg.Render` callers must provide them. Optional context defaults are:

| Context                                                   | Default                                                           |
|-----------------------------------------------------------|-------------------------------------------------------------------|
| `vmName`, `vmNS`                                          | `vm-img--windows`, `default` when rendered outside the fixture    |
| `vmDataVolumeName`                                        | `vmName + '--vhd--00-system'`                                     |
| `vmStorageSize`, `vmStorageClass`, `vmStorageAccessModes` | `96Gi`, `gp3-csi`, `['ReadWriteOnce']`                            |
| `vmCpuCores`, `vmMemory`                                  | `4` (positive native integer), `8Gi`                              |
| `vmAnnotations`, `vmNodeSelector`                         | `{}`, `{}`                                                        |
| `vmTolerations`, `vmEvictionStrategy`                     | `[]`, `None`                                                      |
| `vmFeatures__Hyperv_Tlbflush__Ena`                        | `False`                                                           |

Keep context values native; the template serializes them at YAML insertion. Validate storage availability, names, quantities, source credentials, and guest
compatibility for your environment. Optional [context presets](../fixtures/libs/VM/PresetCtx.py) include recommended VM settings to be used for specific use
case. One or more (merged) of these settings can be used to pre-seed the `fxtInf.vmCfg.ctxData` in Fixture Configuration.
