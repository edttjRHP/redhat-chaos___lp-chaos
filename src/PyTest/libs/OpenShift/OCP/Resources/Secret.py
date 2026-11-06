'''OpenShift Resource Secret handler.'''
import base64
import csv
import os
import re
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional

import yaml

from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.OCP.Resources import (
    OwnInf, ResCfg,
    SpecMismatch,
    _ValResCfg, _ResCreate,
)


def Create(
    oc: OCcli,
    resCfg: ResCfg,
    ownInf: Optional[OwnInf] = None,
) -> dict[str, Any]:
    '''
    Create or reuse a Secret.

    :param oc:
        CLI Client for target OpenShift Cluster.
    :param resCfg:
        See `ResCfg` for details.
        Native mode input:
            resCfg=ResCfg(
                meta=NamedDict(namespace='<ns>', name='<name>'),
                spec=NamedDict(
                    type='Opaque',
                    data={},        # Base64 encoded.
                    stringData={}), # Plain Text UTF-8. Duplicated keys in
                                    #   `data` is overridden.
            )
    :param ownInf:
        A `OwnInf` instance, containing list to track newly created Resource.
        Set to `None` (default) if no tracking is desired.

    :return:
        Existing or newly (re-)created resource Live Object State in a `dict`.

    :raises ValueError:
        Invalid `resCfg` input.
    :raises RuntimeError:
        Secret preparation or creation failed.
    :raises SpecMismatch:
        Resolved Secret type or decoded data differs from the desired values.
    '''
    _ValResCfg(
        resCfg,
        {'namespace', 'name'},
        {'type', 'data', 'stringData'},
        'Secret'
    )
    try:
        if ((resYAML := resCfg.yaml) is None):
            ns = resCfg.meta.get('namespace')
            spec = resCfg.spec
        else:
            spec = yaml.safe_load(resYAML)
        secretType = spec.get('type', 'Opaque')
        expData = {
            key: base64.b64decode(val)
            for (key, val) in spec.get('data', {}).items()
        }
        expData.update({
            key: val.encode('utf-8')
            for (key, val) in spec.get('stringData', {}).items()
        })
        if not all(
            (isinstance(key, str) and re.match(r'[A-Za-z0-9._-]+$', key))
            for key in expData
        ): raise ValueError()
    except Exception:
        raise RuntimeError(
            'Could not prepare Secret (private details are suppressed).'
        ) from None

    try:
        if (resYAML is None):
            with (
                TemporaryDirectory(prefix='oc--secret--') as dir,
                StringIO() as fromFiles,
            ):
                os.chmod(dir, 0o0700)
                args = [resCfg.meta.name]
                fileSources = []
                for (idx, (key, val)) in enumerate(expData.items()):
                    path = Path(dir, str(idx))
                    with os.fdopen(
                        os.open(
                            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o0600,
                        ),
                        'wb',
                    ) as stream: stream.write(val)
                    fileSources.append(f'{key}={path}')
                csv.writer(fromFiles, lineterminator='\n').writerow(
                    fileSources
                )
                resource = _ResCreate(
                    oc,
                    {
                        'subCmd': 'secret generic', 'args': args,
                        'opts': {
                            'from-file': fromFiles.getvalue()[:-1] or None,
                            'type': secretType,
                            **({'n': ns} if ns else {})
                        },
                    },
                    ownInf,
                    True,
                )
        else:
            resource = _ResCreate(oc, resYAML, ownInf, True)
        actData = {
            key: base64.b64decode(val)
            for (key, val) in resource.get('data', {}).items()
        }
    except Exception:
        raise RuntimeError(
            'Could not create Secret (private details are suppressed).'
        ) from None

    if (
        (resource.get('type', 'Opaque') != secretType) or
        (actData != expData)
    ): raise SpecMismatch('Resolved Secret has different type or data.')

    return resource
