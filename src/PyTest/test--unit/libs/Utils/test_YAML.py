import base64
from itertools import product
from pathlib import Path
from unittest import mock

from jinja2.exceptions import UndefinedError
from jinja2 import nodes
import pytest
import yaml

import libs.Utils.YAML as MUT
from fixtures.libs.VM import PresetCtx
import Utils


VM_TEMPLATE = Path(__file__).parents[3] / 'conf/VM/cfg-template--vm.yaml'
VM_SOURCE = {
    's3': {
        'secretRef': 'synthetic-image-source',
        'url': 'https://example.invalid/windows.qcow2',
    },
}
SSH_PUBLIC_KEY = (
    'ssh-ed25519 '
    'AAAAC3NzaC1lZDI1NTE5AAAAISyntheticUnitTestOnly000000000 '
    'unit@example.invalid'
)
SSH_PUBLIC_KEY_B64 = base64.b64encode(SSH_PUBLIC_KEY.encode()).decode()
REQUIRED_VM_CONTEXT = {
    'vmDataVolumeSource': VM_SOURCE,
    'sshPublicKeyB64': SSH_PUBLIC_KEY_B64,
}


def test_Render_VMtemplate_OrdersNativeDefaultsAndEmissionFilters() -> None:
    source = VM_TEMPLATE.read_text()
    (header, body) = source.split('apiVersion: kubevirt.io/v1', 1)
    environment = MUT.Environment()
    headerTree = environment.parse(header)
    bodyTree = environment.parse(body)
    assignments = [
        node.target.name for node in headerTree.find_all(nodes.Assign)
    ]
    firstUses = list(dict.fromkeys(
        node.name for node in bodyTree.find_all(nodes.Name)
        if ((node.ctx == 'load') and (node.name in assignments))
    ))
    assert (assignments == firstUses)
    assert (len(assignments) == len(set(assignments)))
    assert not any(
        (node.name == 'tojson') for node in headerTree.find_all(nodes.Filter)
    )
    assert not any(
        (node.name == 'string')
        for node in environment.parse(source).find_all(nodes.Filter)
    )
    emissions = [
        expression for output in bodyTree.find_all(nodes.Output)
        for expression in output.nodes
        if not isinstance(expression, nodes.TemplateData)
    ]
    assert (len(emissions) == 21)
    for expression in emissions:
        assert isinstance(expression, nodes.Filter)
        assert (expression.name == 'tojson')
        assert isinstance(expression.node, nodes.Name)
    lines = header.rstrip().splitlines()
    assert (len(lines[-1]) == 80)
    assert all((len(line) <= 79) for line in lines[:-1])


@pytest.mark.parametrize(
    ('annotations', 'eviction', 'selector', 'tolerations', 'tlbflush'),
    list(product((False, True), repeat=5)),
)
def test_Render_VMtemplate_OrdersOptionalMappingsAndRetainsNativeTypes(
    annotations: bool,
    eviction: bool,
    selector: bool,
    tolerations: bool,
    tlbflush: bool,
) -> None:
    context = REQUIRED_VM_CONTEXT | {
        'vmName': 'true', 'vmNS': 'false', 'vmCpuCores': 7,
        'vmAnnotations': {'z.review': 'quoted"\n', 'a.review': 'first'}
        if annotations else {},
        'vmEvictionStrategy': 'LiveMigrate' if eviction else None,
        'vmNodeSelector': {'z.review': 'last', 'a.review': 'first'}
        if selector else {},
        'vmTolerations': [
            {'key': 'second', 'operator': 'Exists'},
            {'key': 'first', 'operator': 'Exists'},
        ] if tolerations else [],
        'vmFeatures__Hyperv_Tlbflush__Ena': tlbflush,
        'sshPublicKeyB64': '1234',
    }
    vm = yaml.safe_load(MUT.YAMLcfg.Render(VM_TEMPLATE, context))

    def CheckKeys(value: object) -> None:
        if isinstance(value, dict):
            assert (list(value) == sorted(value))
            for child in value.values(): CheckKeys(child)
        elif isinstance(value, list):
            for child in value: CheckKeys(child)

    CheckKeys(vm)
    assert (vm['metadata']['name'] == 'true')
    assert (vm['metadata']['namespace'] == 'false')
    spec = vm['spec']['template']['spec']
    assert (type(spec['domain']['cpu']['cores']) is int)
    assert (spec['domain']['cpu']['cores'] == 7)
    dvName = vm['spec']['dataVolumeTemplates'][0]['metadata']['name']
    assert (dvName == 'true--vhd--00-system')
    assert (spec['volumes'][0]['dataVolume']['name'] == dvName)
    assert (('annotations' in vm['metadata']) is annotations)
    for (field, enabled) in (
        ('evictionStrategy', eviction), ('nodeSelector', selector),
        ('tolerations', tolerations),
    ):
        assert ((field in spec) is enabled)
    if annotations:
        assert (vm['metadata']['annotations'] == context['vmAnnotations'])
    if selector: assert (spec['nodeSelector'] == context['vmNodeSelector'])
    if tolerations: assert (spec['tolerations'] == context['vmTolerations'])
    assert (('tlbflush' in spec['domain']['features']['hyperv']) is tlbflush)
    cloud = yaml.safe_load(spec['volumes'][1]['cloudInitNoCloud']['userData'])
    assert (cloud['write_files'][0]['content'] == '1234')


@pytest.mark.parametrize('preset', [
    'modelGstOSwin', 'modelNodeHog', 'modelMigrationStorm',
])
def test_Render_VMtemplate_PreservesPresetValuesAndExplicitDVname(
    preset: str,
) -> None:
    settings = PresetCtx.modelGstOSwin | getattr(PresetCtx, preset)
    context = REQUIRED_VM_CONTEXT | settings | {
        'vmName': 'native-vm', 'vmDataVolumeName': 'explicit-system-disk',
    }
    vm = yaml.safe_load(MUT.YAMLcfg.Render(VM_TEMPLATE, context))
    volume = vm['spec']['dataVolumeTemplates'][0]
    spec = vm['spec']['template']['spec']
    assert (volume['metadata']['name'] == 'explicit-system-disk')
    assert (spec['volumes'][0]['dataVolume']['name'] == 'explicit-system-disk')
    assert (volume['spec']['storage']['accessModes'] ==
            settings['vmStorageAccessModes'])
    assert (volume['spec']['storage']['storageClassName'] ==
            settings['vmStorageClass'])
    assert (spec['domain']['cpu']['cores'] == settings['vmCpuCores'])
    assert (type(spec['domain']['cpu']['cores']) is int)
    assert (spec['domain']['memory']['guest'] == settings['vmMemory'])
    if settings['vmEvictionStrategy']:
        assert (spec['evictionStrategy'] == settings['vmEvictionStrategy'])
    else: assert ('evictionStrategy' not in spec)


def test_Render_UsesTemplateEnvironment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.Path, 'exists')
    Utils.AutoPatch(monkeypatch, MUT, 'FileSystemLoader')
    Utils.AutoPatch(monkeypatch, MUT, 'Environment')
    MUT.Path.exists.return_value = True
    template = mock.MagicMock(spec=['render'])
    template.render.return_value = 'rendered\n'
    MUT.Environment.return_value.get_template.return_value = template
    path = Path('/templates/vm.yaml')

    result = MUT.YAMLcfg.Render(tmplPath=path, ctxData={'name': 'vm-one'})

    assert (result == 'rendered\n')
    MUT.FileSystemLoader.assert_called_once_with('/templates')
    MUT.Environment.assert_called_once_with(
        loader=MUT.FileSystemLoader.return_value,
        keep_trailing_newline=True,
    )
    MUT.Environment.return_value.get_template.assert_called_once_with(
        'vm.yaml',
    )
    template.render.assert_called_once_with({'name': 'vm-one'})


def test_Render_RejectsMissingTemplate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Utils.AutoPatch(monkeypatch, MUT.Path, 'exists')
    MUT.Path.exists.return_value = False
    path = Path('/missing/template.yaml')

    with pytest.raises(FileNotFoundError, match=str(path)):
        MUT.YAMLcfg.Render(path, {})

    MUT.Path.exists.assert_called_once_with(path)


def test_Render_VMtemplate_ComposesMeaningfulDefaultsAndReferences() -> None:
    rendered = MUT.YAMLcfg.Render(VM_TEMPLATE, REQUIRED_VM_CONTEXT)

    vm = yaml.safe_load(rendered)

    assert (vm['metadata'] == {
        'name': 'vm-img--windows',
        'namespace': 'default',
        'labels': {'usrMeta.vmGroup': 'vm-img--windows'},
    })
    dataVolume = vm['spec']['dataVolumeTemplates'][0]
    assert (dataVolume['apiVersion'] == 'cdi.kubevirt.io/v1beta1')
    assert (dataVolume['kind'] == 'DataVolume')
    assert (dataVolume['metadata']['name'] == (
        'vm-img--windows--vhd--00-system'
    ))
    assert (dataVolume['metadata']['labels']['usrMeta.volGroup'] == (
        'vm-img--windows'
    ))
    assert (dataVolume['spec']['source'] == VM_SOURCE)
    assert (dataVolume['spec']['storage']['accessModes'] == ['ReadWriteOnce'])
    assert (dataVolume['spec']['storage']['resources']['requests'] == {
        'storage': '96Gi',
    })
    assert (dataVolume['spec']['storage']['storageClassName'] == 'gp3-csi')
    assert (vm['spec']['runStrategy'] == 'Manual')
    assert (vm['spec']['template']['metadata']['labels'] == {
        'kubevirt.io/domain': 'vm-img--windows',
        'kubevirt.io/vm': 'vm-img--windows',
    })
    templateSpec = vm['spec']['template']['spec']
    assert (templateSpec['architecture'] == 'amd64')
    domain = templateSpec['domain']
    assert domain['clock']['timer']['hyperv']['present']
    assert (domain['cpu'] == {
        'cores': 4,
        'sockets': 1,
        'threads': 1,
    })
    assert isinstance(domain['cpu']['cores'], int)
    devices = domain['devices']
    assert not devices['autoattachPodInterface']
    assert (devices['disks'] == [
        {
            'bootOrder': 1,
            'disk': {'bus': 'virtio'},
            'name': 'vhd--00-fs-rootfs',
        },
        {
            'cdrom': {'bus': 'sata'},
            'name': 'cloud-init-disk',
        },
    ])
    assert (devices['inputs'] == [
        {'bus': 'usb', 'name': 'tablet', 'type': 'tablet'},
    ])
    assert (devices['interfaces'] == [
        {'masquerade': {}, 'model': 'virtio', 'name': 'nic--0'},
    ])
    assert devices['networkInterfaceMultiqueue']
    assert (devices['tpm'] == {})
    spinlocks = domain['features']['hyperv']['spinlocks']
    assert (spinlocks == {'enabled': True, 'spinlocks': 8191})
    assert domain['firmware']['bootloader']['efi']['secureBoot']
    assert (domain['machine']['type'] == 'q35')
    assert (domain['memory']['guest'] == '8Gi')
    assert (domain['resources'] == {
        'limits': {'memory': '8Gi'},
        'requests': {'memory': '8Gi'},
    })
    assert (templateSpec['networks'] == [{'name': 'nic--0', 'pod': {}}])
    volumes = templateSpec['volumes']
    assert (volumes[0] == {
        'dataVolume': {'name': 'vm-img--windows--vhd--00-system'},
        'name': 'vhd--00-fs-rootfs',
    })
    assert (volumes[1]['name'] == 'cloud-init-disk')
    cloudInit = yaml.safe_load(volumes[1]['cloudInitNoCloud']['userData'])
    assert (cloudInit['write_files'] == [{
        'path': r'C:\ProgramData\Scripts\Data--App--CloudBaseInit--SSH.txt',
        'encoding': 'b64',
        'content': SSH_PUBLIC_KEY_B64,
    }])
    assert isinstance(cloudInit['write_files'][0]['content'], str)
    assert (base64.b64decode(
        cloudInit['write_files'][0]['content'], validate=True
    ).decode() == SSH_PUBLIC_KEY)

    assert (dataVolume['metadata']['name'] == volumes[0]['dataVolume']['name'])
    assert (devices['disks'][0]['name'] == volumes[0]['name'])
    assert (devices['disks'][1]['name'] == volumes[1]['name'])
    assert (devices['interfaces'][0]['name'] == templateSpec['networks'][0][
        'name'
    ])


def test_Render_VMtemplate_AppliesAllMeaningfulOverrides() -> None:
    source = {
        'http': {
            'url': 'https://example.invalid/image?channel="stable"',
            'extraHeaders': ['X-Synthetic: false', 'X-Null: null'],
            'secretRef': None,
        },
    }
    rendered = MUT.YAMLcfg.Render(VM_TEMPLATE, {
        'vmName': 'synthetic-vm',
        'vmNS': 'synthetic-vm-ns',
        'vmDataVolumeName': 'synthetic-dv',
        'vmDataVolumeSource': source,
        'vmStorageSize': '128Gi',
        'vmStorageClass': 'synthetic-storage',
        'vmCpuCores': 6,
        'vmMemory': '12Gi',
        'sshPublicKeyB64': SSH_PUBLIC_KEY_B64,
    })

    vm = yaml.safe_load(rendered)

    assert (vm['metadata']['name'] == 'synthetic-vm')
    assert (vm['metadata']['namespace'] == 'synthetic-vm-ns')
    assert (vm['metadata']['labels']['usrMeta.vmGroup'] == 'synthetic-vm')
    dataVolume = vm['spec']['dataVolumeTemplates'][0]
    assert (dataVolume['metadata']['name'] == 'synthetic-dv')
    assert (dataVolume['metadata']['labels']['usrMeta.volGroup'] == (
        'synthetic-vm'
    ))
    assert (dataVolume['spec']['source'] == source)
    assert (dataVolume['spec']['storage']['resources']['requests'] == {
        'storage': '128Gi',
    })
    assert (
        dataVolume['spec']['storage']['storageClassName'] ==
        'synthetic-storage'
    )
    templateSpec = vm['spec']['template']['spec']
    assert (vm['spec']['template']['metadata']['labels'] == {
        'kubevirt.io/domain': 'synthetic-vm',
        'kubevirt.io/vm': 'synthetic-vm',
    })
    assert (templateSpec['volumes'][0]['dataVolume']['name'] == 'synthetic-dv')
    domain = templateSpec['domain']
    assert (domain['cpu']['cores'] == 6)
    assert isinstance(domain['cpu']['cores'], int)
    assert (domain['memory']['guest'] == '12Gi')
    assert (domain['resources']['limits']['memory'] == '12Gi')
    assert (domain['resources']['requests']['memory'] == '12Gi')


def test_Render_VMtemplate_DerivesDataVolumeNameFromCustomVMname() -> None:
    context = REQUIRED_VM_CONTEXT | {'vmName': 'custom-windows'}

    vm = yaml.safe_load(MUT.YAMLcfg.Render(VM_TEMPLATE, context))

    expectedName = 'custom-windows--vhd--00-system'
    assert (vm['spec']['dataVolumeTemplates'][0]['metadata']['name'] == (
        expectedName
    ))
    assert (
        vm['spec']['template']['spec']['volumes'][0]['dataVolume']['name'] ==
        expectedName
    )


@pytest.mark.parametrize('namespace', ['123', 'true', 'null'])
def test_Render_VMtemplate_PreservesYAMLlookingNamespaceStrings(
    namespace: str,
) -> None:
    context = REQUIRED_VM_CONTEXT | {'vmNS': namespace}

    vm = yaml.safe_load(MUT.YAMLcfg.Render(VM_TEMPLATE, context))

    assert (vm['metadata']['namespace'] == namespace)
    assert isinstance(vm['metadata']['namespace'], str)


def test_Render_VMtemplate_SerializesNamespaceBreakoutAsData() -> None:
    namespace = 'safe"\n  annotations:\n    injected: true\n  #'
    context = REQUIRED_VM_CONTEXT | {'vmNS': namespace}

    vm = yaml.safe_load(MUT.YAMLcfg.Render(VM_TEMPLATE, context))

    assert (vm['metadata']['namespace'] == namespace)
    assert (set(vm['metadata']) == {'name', 'namespace', 'labels'})


@pytest.mark.parametrize('value', [
    'quoted"value',
    r'C:\Synthetic\Path',
    'line one\nline two',
    'control\x01value',
])
def test_Render_VMtemplate_StringFieldsRoundTripExactlyOnce(
    value: str,
) -> None:
    context = REQUIRED_VM_CONTEXT | {
        'vmName': value,
        'vmNS': value,
        'vmDataVolumeName': value,
        'vmStorageSize': value,
        'vmStorageClass': value,
        'vmMemory': value,
    }

    vm = yaml.safe_load(MUT.YAMLcfg.Render(VM_TEMPLATE, context))

    assert (vm['metadata'] == {
        'name': value,
        'namespace': value,
        'labels': {'usrMeta.vmGroup': value},
    })
    dataVolume = vm['spec']['dataVolumeTemplates'][0]
    assert (dataVolume['metadata']['name'] == value)
    assert (dataVolume['metadata']['labels']['usrMeta.volGroup'] == value)
    assert (dataVolume['spec']['storage']['resources']['requests'] == {
        'storage': value,
    })
    assert (dataVolume['spec']['storage']['storageClassName'] == value)
    template = vm['spec']['template']
    assert (template['metadata']['labels'] == {
        'kubevirt.io/domain': value,
        'kubevirt.io/vm': value,
    })
    domain = template['spec']['domain']
    assert (domain['memory']['guest'] == value)
    assert (domain['resources'] == {
        'limits': {'memory': value},
        'requests': {'memory': value},
    })
    assert (template['spec']['volumes'][0]['dataVolume']['name'] == value)
    for emitted in (
        vm['metadata']['namespace'], dataVolume['metadata']['name'],
        dataVolume['spec']['storage']['resources']['requests']['storage'],
        dataVolume['spec']['storage']['storageClassName'],
        domain['memory']['guest'],
        template['spec']['volumes'][0]['dataVolume']['name'],
    ):
        assert (type(emitted) is type(value))


@pytest.mark.parametrize('value', [123, 1.25, True, None])
def test_Render_VMtemplate_RejectsNonStringVMnameWithoutCoercion(
    value: object,
) -> None:
    with pytest.raises(TypeError):
        MUT.YAMLcfg.Render(
            VM_TEMPLATE, REQUIRED_VM_CONTEXT | {'vmName': value},
        )


@pytest.mark.parametrize('value', [123, 1.25, True, None])
def test_Render_VMtemplate_PreservesNativeScalarEmissions(
    value: object,
) -> None:
    fields = (
        'vmNS', 'vmDataVolumeName', 'vmStorageSize', 'vmStorageClass',
        'vmMemory',
    )
    context = REQUIRED_VM_CONTEXT | dict.fromkeys(fields, value)

    vm = yaml.safe_load(MUT.YAMLcfg.Render(VM_TEMPLATE, context))
    dataVolume = vm['spec']['dataVolumeTemplates'][0]
    template = vm['spec']['template']
    domain = template['spec']['domain']

    assert (vm['metadata']['namespace'] == value)
    assert (type(vm['metadata']['namespace']) is type(value))
    assert (dataVolume['metadata']['name'] == value)
    assert (dataVolume['spec']['storage']['resources']['requests'] == {
        'storage': value,
    })
    assert (dataVolume['spec']['storage']['storageClassName'] == value)
    assert (domain['memory']['guest'] == value)
    assert (template['spec']['volumes'][0]['dataVolume']['name'] == value)


@pytest.mark.parametrize(
    ('missingKey', 'context'),
    [
        ('vmDataVolumeSource', {'sshPublicKeyB64': SSH_PUBLIC_KEY_B64}),
        ('sshPublicKeyB64', {'vmDataVolumeSource': VM_SOURCE}),
    ],
)
def test_Render_VMtemplate_ReportsMissingRequiredContext(
    missingKey: str,
    context: dict,
) -> None:
    vmName = mock.MagicMock()
    vmName.__str__.side_effect = AssertionError(
        'VM name coerced before checks',
    )
    context = context | {'vmName': vmName}

    with pytest.raises(UndefinedError, match=missingKey):
        MUT.YAMLcfg.Render(VM_TEMPLATE, context)

    assert (missingKey not in context)
    vmName.__str__.assert_not_called()


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('vmDataVolumeSource', {}),
        ('vmDataVolumeSource', []),
        ('vmDataVolumeSource', 's3'),
        ('vmDataVolumeSource', None),
        ('vmDataVolumeSource', True),
        ('vmDataVolumeSource', 123),
        ('vmDataVolumeSource', 1.5),
        ('sshPublicKeyB64', ''),
        ('sshPublicKeyB64', '  '),
        ('sshPublicKeyB64', 123),
        ('sshPublicKeyB64', None),
        ('sshPublicKeyB64', {}),
        ('sshPublicKeyB64', []),
        ('sshPublicKeyB64', True),
        ('sshPublicKeyB64', 1.5),
        ('vmCpuCores', 0),
        ('vmCpuCores', -1),
        ('vmCpuCores', True),
        ('vmCpuCores', 4.0),
        ('vmCpuCores', '4'),
        ('vmCpuCores', None),
        ('vmCpuCores', False),
        ('vmCpuCores', {}),
        ('vmCpuCores', []),
        ('vmCpuCores', ''),
    ],
)
def test_Render_VMtemplate_RejectsInvalidContractValues(
    field: str,
    value: object,
) -> None:
    vmName = mock.MagicMock()
    vmName.__str__.side_effect = AssertionError(
        'VM name coerced before checks',
    )
    context = REQUIRED_VM_CONTEXT | {field: value, 'vmName': vmName}

    with pytest.raises(UndefinedError, match=field):
        MUT.YAMLcfg.Render(VM_TEMPLATE, context)

    vmName.__str__.assert_not_called()


def test_Render_VMtemplate_RejectsNonSerializableNameWithoutCoercion() -> None:
    vmName = mock.MagicMock()
    vmName.__str__.side_effect = AssertionError('VM name conversion reached')

    with pytest.raises(TypeError, match='not JSON serializable'):
        MUT.YAMLcfg.Render(
            VM_TEMPLATE, REQUIRED_VM_CONTEXT | {'vmName': vmName},
        )

    vmName.__str__.assert_not_called()


def test_Render_VMtemplate_PreservesStructuredSourceTypes() -> None:
    source = {
        'http': {
            'url': 'https://example.invalid/image?value="quoted"\nnext',
            'extraHeaders': ['X-False: false', 'X-Null: null'],
            'secretRef': None,
            'insecureTLS': False,
        },
    }
    context = REQUIRED_VM_CONTEXT | {'vmDataVolumeSource': source}

    vm = yaml.safe_load(MUT.YAMLcfg.Render(VM_TEMPLATE, context))

    actual = vm['spec']['dataVolumeTemplates'][0]['spec']['source']
    assert (actual == source)
    assert (actual['http']['secretRef'] is None)
    assert (actual['http']['insecureTLS'] is False)
    assert isinstance(actual['http']['extraHeaders'], list)


def test_Render_VMtemplate_FixesStructuralWikiIdentifiers() -> None:
    context = REQUIRED_VM_CONTEXT | {
        'vmRootDiskName': 'ignored-root',
        'vmCloudInitDiskName': 'ignored-cloud-init',
        'vmNetworkName': 'ignored-network',
        'vmInputName': 'ignored-input',
        'vmRunStrategy': 'Always',
        'vmArchitecture': 'arm64',
        'vmMachineType': 'ignored-machine',
    }

    vm = yaml.safe_load(MUT.YAMLcfg.Render(VM_TEMPLATE, context))
    templateSpec = vm['spec']['template']['spec']
    devices = templateSpec['domain']['devices']

    assert (vm['spec']['runStrategy'] == 'Manual')
    assert (templateSpec['architecture'] == 'amd64')
    assert (devices['disks'][0] == {
        'bootOrder': 1,
        'disk': {'bus': 'virtio'},
        'name': 'vhd--00-fs-rootfs',
    })
    assert (devices['disks'][1] == {
        'cdrom': {'bus': 'sata'},
        'name': 'cloud-init-disk',
    })
    assert (devices['inputs'] == [
        {'bus': 'usb', 'name': 'tablet', 'type': 'tablet'},
    ])
    assert (devices['interfaces'] == [
        {'masquerade': {}, 'model': 'virtio', 'name': 'nic--0'},
    ])
    assert (templateSpec['networks'] == [{'name': 'nic--0', 'pod': {}}])
    assert (templateSpec['volumes'][0]['name'] == 'vhd--00-fs-rootfs')
    assert (templateSpec['volumes'][1]['name'] == 'cloud-init-disk')


def test_ToDict_HandlesDictYAMLandRejectsBlankInput() -> None:
    original = {'answer': 42}

    assert (MUT.YAMLcfg.ToDict(original) is original)
    assert (MUT.YAMLcfg.ToDict('answer: 42') == original)
    with pytest.raises(TypeError): MUT.YAMLcfg.ToDict('')


def test_ToJSON_HandlesYAMLandDict() -> None:
    assert (MUT.YAMLcfg.ToJSON('answer: 42', indent=0) == '{\n"answer": 42\n}')
    assert (MUT.YAMLcfg.ToJSON({'answer': 42}) == '{\n  "answer": 42\n}')
    with pytest.raises(TypeError): MUT.YAMLcfg.ToJSON('')


@pytest.mark.parametrize('data', [
    'false', '0', '0.0', '[]', '""', '', ' \n', '# comment\n', 'null', '~',
])
def test_ToJSON_RejectsFalsyAndNullRoots(data: str) -> None:
    with pytest.raises(TypeError): MUT.YAMLcfg.ToJSON(data)


def test_ToYAML_HandlesYAMLJSONandDict() -> None:
    expected = 'answer: 42\n'

    assert (MUT.YAMLcfg.ToYAML('answer: 42') == expected)
    assert (MUT.YAMLcfg.ToYAML('{"answer": 42}') == expected)
    assert (MUT.YAMLcfg.ToYAML({'answer': 42}) == expected)
    assert (yaml.safe_load(expected) == {'answer': 42})


@pytest.mark.parametrize(('data', 'expected'), [
    ({}, {}),
    ('{}', {}),
    ('answer: 42', {'answer': 42}),
    ('{answer: 42}', {'answer': 42}),
    ('{"answer": 42}', {'answer': 42}),
    ({'value': None, 'items': [None, False, 0, '', [], {}]}, {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
    ('value: null\nitems: [null, false, 0, "", [], {}]', {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
    ('{"value": null, "items": [null, false, 0, "", [], {}]}', {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
])
def test_ToDict_PreservesMappingsAndNestedValues(
    data: object,
    expected: dict,
) -> None:
    actual = MUT.YAMLcfg.ToDict(data)

    assert isinstance(actual, dict)
    assert (actual == expected)
    if isinstance(data, dict): assert (actual is data)


@pytest.mark.parametrize('data', [
    None, False, True, 0, 1, 0.0, [], [1], (), set(), b'{}',
    '', ' \n', '# comment\n', 'null', 'Null', 'NULL', '~',
    'false', 'true', '0', '1', '0.0', '[]', '[1]', '- item',
    '""', '"null"', 'text',
])
def test_ToDict_RejectsNonMappingInputs(data: object) -> None:
    with pytest.raises(TypeError): MUT.YAMLcfg.ToDict(data)


@pytest.mark.parametrize('data', ['{broken', 'value: [1, 2'])
def test_ToDict_PropagatesMalformedInput(data: str) -> None:
    with pytest.raises(yaml.YAMLError): MUT.YAMLcfg.ToDict(data)


@pytest.mark.parametrize(('data', 'expected'), [
    ({}, {}),
    ('{}', {}),
    ('answer: 42', {'answer': 42}),
    ('{answer: 42}', {'answer': 42}),
    ('{"answer": 42}', {'answer': 42}),
    ({'value': None, 'items': [None, False, 0, '', [], {}]}, {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
    ('value: null\nitems: [null, false, 0, "", [], {}]', {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
    ('{"value": null, "items": [null, false, 0, "", [], {}]}', {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
])
def test_ToJSON_PreservesMappingsAndNestedValues(
    data: object,
    expected: dict,
) -> None:
    actual = MUT.json.loads(MUT.YAMLcfg.ToJSON(data))

    assert isinstance(actual, dict)
    assert (actual == expected)


@pytest.mark.parametrize('data', [
    None, False, True, 0, 1, 0.0, [], [1], (), set(), b'{}',
    '', ' \n', '# comment\n', 'null', 'Null', 'NULL', '~',
    'false', 'true', '0', '1', '0.0', '[]', '[1]', '- item',
    '""', '"null"', 'text',
])
def test_ToJSON_RejectsNonMappingInputs(data: object) -> None:
    with pytest.raises(TypeError): MUT.YAMLcfg.ToJSON(data)


@pytest.mark.parametrize('data', ['{broken', 'value: [1, 2'])
def test_ToJSON_PropagatesMalformedInput(data: str) -> None:
    with pytest.raises(yaml.YAMLError): MUT.YAMLcfg.ToJSON(data)


@pytest.mark.parametrize(('data', 'expected'), [
    ({}, {}),
    ('{}', {}),
    ('answer: 42', {'answer': 42}),
    ('{answer: 42}', {'answer': 42}),
    ('{"answer": 42}', {'answer': 42}),
    ({'value': None, 'items': [None, False, 0, '', [], {}]}, {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
    ('value: null\nitems: [null, false, 0, "", [], {}]', {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
    ('{"value": null, "items": [null, false, 0, "", [], {}]}', {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
])
def test_ToYAML_PreservesMappingsAndNestedValues(
    data: object,
    expected: dict,
) -> None:
    actual = yaml.safe_load(MUT.YAMLcfg.ToYAML(data))

    assert isinstance(actual, dict)
    assert (actual == expected)


@pytest.mark.parametrize('data', [
    None, False, True, 0, 1, 0.0, [], [1], (), set(), b'{}',
    '', ' \n', '# comment\n', 'null', 'Null', 'NULL', '~',
    'false', 'true', '0', '1', '0.0', '[]', '[1]', '- item',
    '""', '"null"', 'text',
])
def test_ToYAML_RejectsNonMappingInputs(data: object) -> None:
    with pytest.raises(TypeError): MUT.YAMLcfg.ToYAML(data)


@pytest.mark.parametrize('data', ['{broken', 'value: [1, 2'])
def test_ToYAML_PropagatesMalformedInput(data: str) -> None:
    with pytest.raises(yaml.YAMLError): MUT.YAMLcfg.ToYAML(data)
