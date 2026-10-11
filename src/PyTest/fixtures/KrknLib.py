'''
KrknLib fixture for Chaos scenarios

https://github.com/krkn-chaos/krkn-lib
'''
from functools import partial
import logging
import os
from typing import Any, Callable, Generator, NamedTuple

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


class KrknContext(NamedTuple):
    '''
    Bundle handed to a test: `krkn-lib` client, parameters, and CLI helpers.

    Attributes:
      - krknClient:
        Initialized `KrknOpenshift` client instance.
      - krknPars:
        A `NamedDict` containing Krkn parameters.
      - testPars:
        A `NamedDict` containing test-specific parameters.
      - cli:
        A `NamedDict` containing pre-configured CLI wrappers:
          - oc: A `OCcli` instance.
          - virtCtl: A `VirtCtlCLI` instance.
      - ExecOnNode:
        Helper function `ExecOnNode(node, podName, command, ...)`, bound to
        `krknClient`.
    '''
    krknClient: Any
    krknPars: NamedDict
    testPars: NamedDict
    cli: NamedDict
    ns: str
    ExecOnNode: Callable[..., Any]


def ExecOnNode(
    krknClient,
    node,
    podName,
    command,
    ns=_DEF__FXT_CFG.ns,
    ctrImg=_DEF__FXT_CFG.ctrImgURL_ExecNode,
    timeout=120
) -> str:
    '''
    Run a shell command on a Node via a transient privileged pod, using
    `krkn-lib` primitives directly.

    This replaces `krkn-lib`'s `exec_command_on_node` wrapper, which
        (a) hardcodes a dead image,
        (b) waits 500s before failing, and
        (c) never deletes the pod.
    Here we build the same privileged/hostNetwork/dbus pod but with a pullable
    image, a short timeout, and guaranteed cleanup.

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


@pytest.fixture(name='krknChaos', scope='function')
def KrnChaos(
    request: Any,
    fxtOCcli: Callable[..., Any],
    fxtVirtCtlCLI: Callable[..., Any],
) -> Generator[Any, Any, Any]:
    '''
    Provide a `krkn-lib` client and scenario parameters to a test.

    Configure via indirect parametrize with `dict`:
        @pytest.mark.parametrize(
            'krknChaos,',
            [({
                'ns': 'bsod-test',
                'testPars': {
                    'vmName': 'bsod-win2025',
                },
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
        A `KrknContext` namedtuple containing:
          - krknClient: A `KrknOpenshift` instance.
          - krknPars: A `NamedDict` of Krkn parameters.
          - testPars: A `NamedDict` of test-specific parameters.
          - cli: A `NamedDict` with:
              - oc: A `OCcli` instance.
              - virtCtl: A `VirtCtlCLI` instance.
          - ns: Kubernetes Namespace.
          - ExecOnNode: Node execution helper bound to `krknClient`.
    '''
    # Import lazily so the TCs are still collected if these modules are absent.
    from krkn_lib.ocp import KrknOpenshift

    fxtCfg = _DEF__FXT_CFG | NamedDict(getattr(request, 'param', {}))

    logs.info(
        f'Initializing `krkn-lib` client with `KUBECONFIG`: {fxtCfg.kCfg}'
    )
    krknClient = KrknOpenshift(kubeconfig_path=fxtCfg.kCfg)

    yield KrknContext(
        krknClient=krknClient,
        krknPars=fxtCfg.krknPars,
        testPars=fxtCfg.testPars,
        cli=NamedDict(
            oc=fxtOCcli(ns=fxtCfg.ns, **fxtCfg.cliCfg.ocCfg),
            virtCtl=fxtVirtCtlCLI(ns=fxtCfg.ns, **fxtCfg.cliCfg.vcCfg),
        ),
        ns=fxtCfg.ns,
        ExecOnNode=partial(ExecOnNode, krknClient),
    )

    logs.info('The `krknChaos` fixture teardown complete.')
