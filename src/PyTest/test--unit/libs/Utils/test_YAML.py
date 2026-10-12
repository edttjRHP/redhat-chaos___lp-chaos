import base64
from pathlib import Path
from unittest import mock

import pytest
import yaml

import libs.Utils.YAML as MUT
import Utils


VM_TEMPLATE = (
    Path(__file__).parents[3] / 'conf' / 'config--vm--template.yaml'
)


def test_Render_UsesTemplateEnvironment(monkeypatch: pytest.MonkeyPatch) -> None:
    Utils.AutoPatch(monkeypatch, MUT.Path, 'exists')
    Utils.AutoPatch(monkeypatch, MUT, 'FileSystemLoader')
    Utils.AutoPatch(monkeypatch, MUT, 'Environment')
    MUT.Path.exists.return_value = True
    template = mock.MagicMock(spec=['render'])
    template.render.return_value = 'rendered\n'
    MUT.Environment.return_value.get_template.return_value = template
    path = Path('/templates/vm.yaml')

    result = MUT.YAMLcfg.Render(path, {'name': 'vm-one'})

    assert result == 'rendered\n'
    MUT.FileSystemLoader.assert_called_once_with('/templates')
    MUT.Environment.assert_called_once_with(
        loader=MUT.FileSystemLoader.return_value,
        keep_trailing_newline=True,
    )
    MUT.Environment.return_value.get_template.assert_called_once_with('vm.yaml')
    template.render.assert_called_once_with({'name': 'vm-one'})


def test_Render_RejectsMissingTemplate(monkeypatch: pytest.MonkeyPatch) -> None:
    Utils.AutoPatch(monkeypatch, MUT.Path, 'exists')
    MUT.Path.exists.return_value = False
    path = Path('/missing/template.yaml')

    with pytest.raises(FileNotFoundError, match=str(path)):
        MUT.YAMLcfg.Render(path, {})

    MUT.Path.exists.assert_called_once_with(path)


def test_Render_VMtemplate_UsesEverySourceDefaultAndReferences() -> None:
    rendered = MUT.YAMLcfg.Render(VM_TEMPLATE, {})

    vm = yaml.safe_load(rendered)

    assert vm['metadata'] == {
        'name': '',
        'namespace': '',
        'labels': {'usrMeta.vmGroup': ''},
    }
    dataVolume = vm['spec']['dataVolumeTemplates'][0]
    assert dataVolume['apiVersion'] == 'cdi.kubevirt.io/v1beta1'
    assert dataVolume['kind'] == 'DataVolume'
    assert dataVolume['metadata']['name'] == ''
    assert dataVolume['metadata']['labels']['usrMeta.volGroup'] == ''
    assert dataVolume['spec']['source'] == {}
    assert dataVolume['spec']['storage']['accessModes'] == ['ReadWriteOnce']
    assert dataVolume['spec']['storage']['resources']['requests'] == {
        'storage': '',
    }
    assert dataVolume['spec']['storage']['storageClassName'] == ''
    assert vm['spec']['runStrategy'] == 'Manual'
    assert vm['spec']['template']['metadata']['labels'] == {
        'kubevirt.io/domain': '',
        'kubevirt.io/vm': '',
    }
    templateSpec = vm['spec']['template']['spec']
    assert templateSpec['architecture'] == 'amd64'
    domain = templateSpec['domain']
    assert domain['clock']['timer']['hyperv']['present']
    assert domain['cpu'] == {
        'cores': 4,
        'sockets': 1,
        'threads': 1,
    }
    devices = domain['devices']
    assert not devices['autoattachPodInterface']
    assert devices['disks'] == [
        {
            'bootOrder': 1,
            'disk': {'bus': 'virtio'},
            'name': 'vhd--00-fs-rootfs',
        },
        {
            'cdrom': {'bus': 'sata'},
            'name': 'cloud-init-disk',
        },
    ]
    assert devices['inputs'] == [
        {'bus': 'usb', 'name': 'tablet', 'type': 'tablet'},
    ]
    assert devices['interfaces'] == [
        {'masquerade': {}, 'model': 'virtio', 'name': 'nic--0'},
    ]
    assert devices['networkInterfaceMultiqueue']
    spinlocks = domain['features']['hyperv']['spinlocks']
    assert spinlocks == {'enabled': True, 'spinlocks': 8191}
    assert domain['firmware']['bootloader']['efi']['secureBoot']
    assert domain['machine']['type'] == 'q35'
    assert domain['memory']['guest'] == '8Gi'
    assert domain['resources'] == {
        'limits': {'memory': '8Gi'},
        'requests': {'memory': '8Gi'},
    }
    assert templateSpec['networks'] == [{'name': 'nic--0', 'pod': {}}]
    volumes = templateSpec['volumes']
    assert volumes[0] == {
        'dataVolume': {'name': ''},
        'name': 'vhd--00-fs-rootfs',
    }
    assert volumes[1]['name'] == 'cloud-init-disk'
    cloudInit = yaml.safe_load(volumes[1]['cloudInitNoCloud']['userData'])
    assert cloudInit['write_files'] == [{
        'path': r'C:\ProgramData\Scripts\Data--App--CloudBaseInit--SSH.txt',
        'encoding': 'b64',
        'content': '',
    }]

    assert dataVolume['metadata']['name'] == (
        volumes[0]['dataVolume']['name']
    )
    assert devices['disks'][0]['name'] == volumes[0]['name']
    assert devices['disks'][1]['name'] == volumes[1]['name']
    assert devices['interfaces'][0]['name'] == templateSpec['networks'][0][
        'name'
    ]


def test_Render_VMtemplate_AcceptsDeploymentContextAndEncodedSSHkey() -> None:
    source = {
        'http': {
            'url': 'https://example.invalid/synthetic-image',
            'extraHeaders': ['X-Synthetic: true', 'X-Number: 0'],
            'secretRef': None,
        },
    }
    publicKey = (
        'ssh-ed25519 '
        'AAAAC3NzaC1lZDI1NTE5AAAAISyntheticUnitTestOnly000000000 '
        'unit@example.invalid'
    )
    publicKeyB64 = base64.b64encode(publicKey.encode()).decode()
    rendered = MUT.YAMLcfg.Render(VM_TEMPLATE, {
        'vmName': 'synthetic-vm',
        'vmNS': 'synthetic-vm-ns',
        'vmDataVolumeName': 'synthetic-dv',
        'vmDataVolumeSource': source,
        'vmStorageSize': '64Gi',
        'vmStorageClass': 'synthetic-storage',
        'sshPublicKeyB64': publicKeyB64,
    })

    vm = yaml.safe_load(rendered)

    assert vm['metadata']['name'] == 'synthetic-vm'
    assert vm['metadata']['namespace'] == 'synthetic-vm-ns'
    dataVolume = vm['spec']['dataVolumeTemplates'][0]
    assert dataVolume['metadata']['name'] == 'synthetic-dv'
    assert dataVolume['spec']['source'] == source
    assert dataVolume['spec']['storage']['resources']['requests'] == {
        'storage': '64Gi',
    }
    assert (
        dataVolume['spec']['storage']['storageClassName'] ==
        'synthetic-storage'
    )
    templateSpec = vm['spec']['template']['spec']
    assert templateSpec['volumes'][0]['dataVolume']['name'] == 'synthetic-dv'
    cloudInit = yaml.safe_load(
        templateSpec['volumes'][1]['cloudInitNoCloud']['userData']
    )
    keyFile = cloudInit['write_files'][0]
    assert keyFile['encoding'] == 'b64'
    assert base64.b64decode(
        keyFile['content'], validate=True
    ).decode() == publicKey


def test_Render_VMtemplate_PreservesExplicitFalseAndZero() -> None:
    rendered = MUT.YAMLcfg.Render(VM_TEMPLATE, {
        'vmHypervTimerPresent': False,
        'vmCpuCores': 0,
        'vmCpuSockets': 0,
        'vmCpuThreads': 0,
        'vmAutoattachPodInterface': False,
        'vmDiskBootOrder': 0,
        'vmNetworkInterfaceMultiqueue': False,
        'vmSpinlocksEnabled': False,
        'vmSpinlocks': 0,
        'vmSecureBoot': False,
    })

    domain = yaml.safe_load(rendered)['spec']['template']['spec']['domain']

    assert not domain['clock']['timer']['hyperv']['present']
    assert domain['cpu'] == {'cores': 0, 'sockets': 0, 'threads': 0}
    assert not domain['devices']['autoattachPodInterface']
    assert domain['devices']['disks'][0]['bootOrder'] == 0
    assert not domain['devices']['networkInterfaceMultiqueue']
    assert domain['features']['hyperv']['spinlocks'] == {
        'enabled': False,
        'spinlocks': 0,
    }
    assert not domain['firmware']['bootloader']['efi']['secureBoot']


@pytest.mark.parametrize('value', [
    '',
    'bad"name',
    r'C:\Synthetic\Path',
    'line one\nline two',
    'false',
    'null',
    '123',
    'key: injected\nnested: true',
])
def test_Render_VMtemplate_MarshalsEveryStringScalar(value: str) -> None:
    stringKeys = (
        'vmName',
        'vmNS',
        'vmDataVolumeName',
        'vmStorageSize',
        'vmStorageClass',
        'vmDataVolumeAccessMode',
        'vmRunStrategy',
        'vmArchitecture',
        'vmRootDiskBus',
        'vmRootDiskName',
        'vmCloudInitDiskBus',
        'vmCloudInitDiskName',
        'vmInputBus',
        'vmInputName',
        'vmInputType',
        'vmInterfaceModel',
        'vmNetworkName',
        'vmMachineType',
        'vmMemory',
    )
    rendered = MUT.YAMLcfg.Render(
        VM_TEMPLATE,
        {key: value for key in stringKeys},
    )

    vm = yaml.safe_load(rendered)
    dataVolume = vm['spec']['dataVolumeTemplates'][0]
    template = vm['spec']['template']
    templateSpec = template['spec']
    domain = templateSpec['domain']
    devices = domain['devices']
    values = [
        vm['metadata']['name'],
        vm['metadata']['namespace'],
        vm['metadata']['labels']['usrMeta.vmGroup'],
        dataVolume['metadata']['name'],
        dataVolume['metadata']['labels']['usrMeta.volGroup'],
        dataVolume['spec']['storage']['accessModes'][0],
        dataVolume['spec']['storage']['resources']['requests']['storage'],
        dataVolume['spec']['storage']['storageClassName'],
        vm['spec']['runStrategy'],
        template['metadata']['labels']['kubevirt.io/domain'],
        template['metadata']['labels']['kubevirt.io/vm'],
        templateSpec['architecture'],
        devices['disks'][0]['disk']['bus'],
        devices['disks'][0]['name'],
        devices['disks'][1]['cdrom']['bus'],
        devices['disks'][1]['name'],
        devices['inputs'][0]['bus'],
        devices['inputs'][0]['name'],
        devices['inputs'][0]['type'],
        devices['interfaces'][0]['model'],
        devices['interfaces'][0]['name'],
        domain['machine']['type'],
        domain['memory']['guest'],
        domain['resources']['limits']['memory'],
        domain['resources']['requests']['memory'],
        templateSpec['networks'][0]['name'],
        templateSpec['volumes'][0]['dataVolume']['name'],
        templateSpec['volumes'][0]['name'],
        templateSpec['volumes'][1]['name'],
    ]

    assert values == [value] * len(values)


def test_VMtemplate_JinjaDelimitersHaveNoInnerPadding() -> None:
    template = VM_TEMPLATE.read_text()

    assert '{{ ' not in template
    assert ' }}' not in template


def test_ToDict_HandlesDictYAMLandBlankInput() -> None:
    original = {'answer': 42}

    assert MUT.YAMLcfg.ToDict(original) is original
    assert MUT.YAMLcfg.ToDict('answer: 42') == original
    assert MUT.YAMLcfg.ToDict('') == {}


def test_ToJSON_HandlesYAMLandDict() -> None:
    assert MUT.YAMLcfg.ToJSON('answer: 42', indent=0) == '{\n"answer": 42\n}'
    assert MUT.YAMLcfg.ToJSON({'answer': 42}) == '{\n  "answer": 42\n}'
    assert MUT.YAMLcfg.ToJSON('') == '{}'


def test_ToYAML_HandlesJSONandDict() -> None:
    expected = 'answer: 42\n'

    assert MUT.YAMLcfg.ToYAML('{"answer": 42}') == expected
    assert MUT.YAMLcfg.ToYAML({'answer': 42}) == expected
    assert yaml.safe_load(expected) == {'answer': 42}
