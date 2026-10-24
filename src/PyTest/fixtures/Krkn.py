'''
Krkn fixture for Chaos scenarios

https://github.com/krkn-chaos/krkn-lib
'''
from collections.abc import Callable, Generator
import dataclasses
import functools
import logging
import os
from typing import Any

import pytest

from fixtures.libs.CommonConstant import LPQE__DEF__NAMESPACE
from libs.CustomTypes import NamedDict


logs = logging.getLogger()
_DEF__FXT_CFG = NamedDict(
    kCfg=(
        os.environ.get('KUBECONFIG') or
        os.path.expanduser('~/.kube/config')
    ),
    cliCfg=NamedDict(
        ocCfg={},
        vcCfg={},
    ),
    ns=LPQE__DEF__NAMESPACE,
    krknPars=NamedDict(),
    testPars=NamedDict(),
    ctrImgURL_ExecNode='registry.access.redhat.com/ubi9/ubi'
)


@dataclasses.dataclass(frozen=True)
class FxtObjKrkn:
    '''
    Fixture Object.

    Can be sub-classed to add fields. Sub-classes must also be decorated with
    `@dataclass(frozen=True)`.
    '''
    krknClient: Any
    krknPars: NamedDict
    testPars: NamedDict
    cli: NamedDict
    ns: str
    ExecOnNode: Callable[..., str]


def ExecOnNode(
    krknClient: Any,
    node: str,
    podName: str,
    command: str,
    ns: str = _DEF__FXT_CFG.ns,
    ctrImg: str = _DEF__FXT_CFG.ctrImgURL_ExecNode,
    timeout: Any = 120
) -> str:
    '''
    Run a shell command on a Node via a transient privileged pod, using
    `krkn-lib` primitives directly.

    This replaces `krkn-lib`'s `exec_command_on_node` wrapper, which
        (a) hardcodes a dead image,
        (b) waits 500s before failing, and
        (c) never deletes the pod.
    Here we build the same privileged/hostNetwork/dbus pod but with a pullable
    image, a short timeout, and best-effort cleanup in `finally`. Deletion
    failures are logged and suppressed, so the helper Pod may remain.

    :param command:
        The command as a single shell string (e.g. 'uname -r').
        It is handed to `krkn-lib` as `[command]` so it runs under `bash -c
        '<command>'`; passing a token list instead (['uname', '-r']) makes
        `krkn-lib` build `bash -c uname -r`, where `-r` becomes a positional
        param and is silently dropped.
    :return: the command's stdout as a string.
    '''
    podCfg = {
        'apiVersion': 'v1',
        'kind': 'Pod',
        'metadata': {'name': podName},
        'spec': {
            'hostNetwork': True,
            'nodeName': node,
            'restartPolicy': 'Never',
            'containers': [{
                'name': 'host-tools',
                'image': ctrImg,
                'command': ['/bin/sh', '-c', 'sleep infinity'],
                'securityContext': {'privileged': True},
                'volumeMounts': [{
                    'mountPath': '/run/dbus/system_bus_socket',
                    'name': 'dbus',
                    'readOnly': True,
                }],
            }],
            'volumes': [{
                'name': 'dbus',
                'hostPath': {'path': '/run/dbus/system_bus_socket'},
            }],
        },
    }

    # Best-effort pre-clean in case a previous run left the pod behind.
    try: krknClient.delete_pod(podName, ns)
    except Exception: pass

    try:
        krknClient.create_pod(podCfg, ns, timeout)
        # Pass as a single-element list so `krkn-lib` runs
        #   `bash -c '<command>'`.
        return krknClient.exec_cmd_in_pod([command], podName, ns)
    finally:
        try: krknClient.delete_pod(podName, ns)
        except Exception:
            logs.warning(f'Could not delete helper Pod {podName!r} in {ns!r}.')


@pytest.fixture(name='fxtKrkn', scope='function')
def FxtKrkn(
    request: Any,
    fxtOCcli: Callable[..., Any],
    fxtVirtCtlCLI: Callable[..., Any],
) -> Generator[FxtObjKrkn, None, None]:
    '''
    Provide a `krkn-lib` client and scenario parameters to a test.

    Configure via indirect parametrize with `dict`:
        @pytest.mark.parametrize(
            'fxtKrkn,',
            [({
                'ns': 'bsod-test',
                'testPars': NamedDict(vmName='bsod-win2025'),
            },)],
            indirect=True,
        )

        Fixture Configuration:
          - kCfg:
            The `KUBECONFIG` file for Krkn Client.
          - cliCfg:
            Configuration options for CLIs:
              - ocCfg:
                  Configuration options for `OCcli`.
              - vcCfg:
                  Configuration options for `VirtCtlCLI`.
          - ns:
            Kubernetes Namespace (default: `LPQE__DEF__NAMESPACE`).
          - krknPars:
            Parameter for Krkn.
          - testPars:
            Parameter to be passed to test.
          - ctrImgURL_ExecNode:
            Container image URL for host-side execution
            (default: `'registry.access.redhat.com/ubi9/ubi'`).

    :return:
        A `FxtObjKrkn` where:
          - krknClient: A `KrknOpenshift` instance.
          - krknPars:   A `NamedDict` of Krkn parameters.
          - testPars:   A `NamedDict` of test-specific parameters.
          - cli:
            A `NamedDict` with:
              - oc: A `OCcli` instance.
              - virtCtl: A `VirtCtlCLI` instance.
          - ns:         Kubernetes Namespace.
          - ExecOnNode:
            Node execution helper bound to `krknClient`, configured `ns` and
            `ctrImgURL_ExecNode`; per-call `ns` and `ctrImg` keyword arguments
            override these defaults.
    '''
    # Import lazily so the TCs are still collected if these modules are absent.
    from krkn_lib.ocp import KrknOpenshift

    fxtCfg = _DEF__FXT_CFG | NamedDict(**getattr(request, 'param', {}))

    logs.info(
        f'Initializing `krkn-lib` client with `KUBECONFIG`: {fxtCfg.kCfg}'
    )
    krknClient = KrknOpenshift(kubeconfig_path=fxtCfg.kCfg)

    yield FxtObjKrkn(
        krknClient=krknClient,
        krknPars=fxtCfg.krknPars,
        testPars=fxtCfg.testPars,
        cli=NamedDict(
            oc=fxtOCcli(ns=fxtCfg.ns, **fxtCfg.cliCfg.ocCfg),
            virtCtl=fxtVirtCtlCLI(ns=fxtCfg.ns, **fxtCfg.cliCfg.vcCfg),
        ),
        ns=fxtCfg.ns,
        ExecOnNode=functools.partial(
            ExecOnNode, krknClient,
            ns=fxtCfg.ns, ctrImg=fxtCfg.ctrImgURL_ExecNode,
        ),
    )

    logs.info('The `fxtKrkn` fixture teardown complete.')
