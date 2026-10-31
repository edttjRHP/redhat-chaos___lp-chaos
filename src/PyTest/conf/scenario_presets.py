'''Scenario presets for Layered Product Chaos testing.'''
import os

from libs.CustomTypes import NamedDict

# Base configuration applied to all VM scenarios.
presetBase = NamedDict(
    vmCpuCores=4,
    vmMemory='8Gi',
    vmStorageClass=os.getenv(
        'LPCHAOS__VM__DEFAULT_STORAGE_CLASS',
        'ocs-storagecluster-ceph-rbd-virtualization',
    ),
    vmAccessModes=['ReadWriteOnce'],
    vmStorageSize='96Gi',
    vmNodeSelector=None,
    vmTolerations=None,
    vmAnnotations=None,
    vmEvictionStrategy=None,
    vmHypervTlbflushEnabled=True,
)

# Node CPU/Memory/IO Hog scenario.
presetNodeHog = presetBase | NamedDict(
    vmCpuCores=8,
    vmMemory='16Gi',
    vmNodeSelector={
        'kubernetes.io/hostname': os.getenv(
            'LPCHAOS__VM__TARGET_WORKER_NODE',
            'PLACEHOLDER_REPLACE_WITH_TARGET_NODE_NAME',
        ),
    },
)

# Migration scenario.
# Uses RWX storage for volume migration, and LiveMigrate eviction strategy
# to ensure the VM survives node drains/cordons during chaos injection.
presetMigrationChaos = presetBase | NamedDict(
    vmAccessModes=['ReadWriteMany'],
    vmEvictionStrategy='LiveMigrate',
)

# Degraded VirtIO driver scenario.
# Requires a separate base image with older/buggy drivers pre-installed.
# TLB flush is disabled to simulate older hypervisor/guest interactions.
presetDegradedVirtio = presetBase | NamedDict(
    vmHypervTlbflushEnabled=False,
    
    # Deep-merge override: Keeps type, credType, and credInfo from _DEF__FXT_CFG,
    # but overrides ONLY the url.
    gstCfg=NamedDict(
        imgSrc=NamedDict(
            CDI=NamedDict(
                url=os.getenv(
                    'LPCHAOS__VM__DEGRADED_IMG_URL',
                    'PLACEHOLDER_MUST_PROVIDE_DEGRADED_IMAGE_URL',
                ),
            ),
        ),
    ),
)