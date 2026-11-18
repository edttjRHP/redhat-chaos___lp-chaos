'''
Optional VM template contexts.

One or more (merged) of these models can be used to pre-seed the
`fxtInf.vmCfg.ctxData` in Fixture Configuration.
'''
import os


# Guest OS Windows.
modelGstOSwin = dict(
    vmCpuCores=4,
    vmMemory='8Gi',
    vmStorageClass=os.getenv(
        'IENG_PYTEST____FXT__LIBS_VM__PRESETCTX____BASE__STORAGE_CLASS',
        'ocs-storagecluster-ceph-rbd-virtualization',
    ),
    vmStorageAccessModes=['ReadWriteOnce'],
    vmStorageSize='96Gi',
    vmNodeSelector=None,
    vmTolerations=None,
    vmAnnotations=None,
    vmEvictionStrategy=None,
    vmFeatures__Hyperv_Tlbflush__Ena=False,
)

# Node CPU/Memory/IO Hog scenario.
modelNodeHog = dict(
    vmCpuCores=8,
    vmMemory='16Gi',
    vmNodeSelector={
        'kubernetes.io/hostname': os.getenv(
            (
                'IENG_PYTEST____FXT__LIBS_VM__PRESETCTX____'
                'NODE_HOG__NODE_SELECTOR'
            ),
            'PLACEHOLDER_REPLACE_WITH_TARGET_NODE_NAME',
        ),
    },
)

# Migration scenario.
modelMigrationStorm = dict(
    vmStorageAccessModes=['ReadWriteMany'],
    vmEvictionStrategy='LiveMigrate',
)
