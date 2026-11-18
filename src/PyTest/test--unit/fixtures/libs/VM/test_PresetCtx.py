'''Unit tests for the optional VM preset contexts.'''

import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

import fixtures.libs.VM as parent


moduleName = 'fixtures.libs.VM.PresetCtx'


def LoadPresetCtx(getenv: mock.Mock) -> ModuleType:
    '''Import a fresh preset module with an isolated getenv boundary.'''
    with mock.patch.dict(sys.modules, {'os': SimpleNamespace(getenv=getenv)}):
        sys.modules.pop(moduleName, None)
        with mock.patch.object(parent, 'PresetCtx', create=True):
            return importlib.import_module(moduleName)


@pytest.mark.parametrize(
    'envValues',
    [
        pytest.param(
            (
                'ocs-storagecluster-ceph-rbd-virtualization',
                'PLACEHOLDER_REPLACE_WITH_TARGET_NODE_NAME',
            ),
            id='defaults',
        ),
        pytest.param(('custom-storage-class', 'node-17'), id='custom-values'),
    ],
)
def test_PresetsUseEnvironmentValuesAndPreserveScenarios(
    envValues: tuple[str, str],
) -> None:
    (storageClass, nodeName) = envValues
    getenv = mock.Mock(side_effect=envValues)
    presetCtx = LoadPresetCtx(getenv)
    expectedCalls = [
        mock.call(
            'IENG_PYTEST____FXT__LIBS_VM__PRESETCTX____BASE__STORAGE_CLASS',
            'ocs-storagecluster-ceph-rbd-virtualization',
        ),
        mock.call(
            (
                'IENG_PYTEST____FXT__LIBS_VM__PRESETCTX____'
                'NODE_HOG__NODE_SELECTOR'
            ),
            'PLACEHOLDER_REPLACE_WITH_TARGET_NODE_NAME',
        ),
    ]

    assert (getenv.call_args_list == expectedCalls)
    assert (presetCtx.modelGstOSwin == {
        'vmCpuCores': 4,
        'vmMemory': '8Gi',
        'vmStorageClass': storageClass,
        'vmStorageAccessModes': ['ReadWriteOnce'],
        'vmStorageSize': '96Gi',
        'vmNodeSelector': None,
        'vmTolerations': None,
        'vmAnnotations': None,
        'vmEvictionStrategy': None,
        'vmFeatures__Hyperv_Tlbflush__Ena': False,
    })
    assert (presetCtx.modelNodeHog == {
        'vmCpuCores': 8,
        'vmMemory': '16Gi',
        'vmNodeSelector': {'kubernetes.io/hostname': nodeName},
    })
    assert (presetCtx.modelMigrationStorm == {
        'vmStorageAccessModes': ['ReadWriteMany'],
        'vmEvictionStrategy': 'LiveMigrate',
    })
