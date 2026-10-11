import logging
import time

import pytest

from libs.CustomTypes import NamedDict


logs = logging.getLogger()


class TestChaos():
    '''Krkn chaos scenarios for BSOD/VM resiliency.'''

    @pytest.mark.krkn
    @pytest.mark.parametrize(
        'krknChaos,',
        [({
            'ns': 'bsod-test',
            'testPars': {
                'vmName': 'bsod-win2022',
                'recoveryTO': 300,
            },
        },)],
        indirect=True,
    )
    def test_VMsurvivesVirtLauncherKill(self, krknChaos):
        '''
        Chaos: kill the VM's `virt-launcher` Pod and assert KubeVirt recovers
        it.

        Deleting the `virt-launcher` Pod of a running VMI simulates a Node /
        Pod failure. With `runStrategy: Always` KubeVirt must reschedule a new
        launcher pod and bring the VMI back to `Running`.
        '''
        client = krknChaos.krknClient
        oc = krknChaos.cli.oc
        ns = krknChaos.ns
        (vmName, recoveryTO) = krknChaos.testPars.values()
        labelSel = f'vm.kubevirt.io/name={vmName}'

        #1. Find the live `virt-launcher` Pod managing the VM.
        pods = client.list_pods(namespace=ns, label_selector=labelSel)
        assert pods, (
            f'No `virt-launcher` Pod found for `{vmName}`. Is the VM running?'
        )
        originalPod = pods[0]
        logs.info(f'Target `virt-launcher` Pod: {originalPod}')

        #2. Ensure the VM is already running before introducing chaos.
        before = oc.Run(
            'get', f'VirtualMachineInstance/{vmName}',
            o="jsonpath='{.status.phase}'",
        )
        assert (before.stdout.strip("'") == 'Running'), (
            f'VM not Running before chaos: {before.stdout!r}'
        )

        #3. CHAOS: Kill the `virt-launcher` Pod (simulates Node / Pod failure).
        logs.info(f'CHAOS: Deleting `virt-launcher` Pod `{originalPod}`.')
        client.delete_pod(originalPod, ns)

        #3. Assert recovery: A new launcher Pod reaches Running AND the VMI
        #   returns to `Running`, within `recoveryTO`.
        deadline = time.time() + recoveryTO
        recovered = False
        while (time.time() < deadline):
            current = client.list_pods(namespace=ns, label_selector=labelSel)
            newPods = [p for p in current if (p != originalPod)]
            vmiPhase = oc.Run(
                'get', f'VirtualMachineInstance/{vmName}',
                o="jsonpath='{.status.phase}'",
            ).stdout.strip("'")
            if (
                newPods and
                client.is_pod_running(newPods[0], ns) and
                (vmiPhase == 'Running')
            ):
                logs.info(
                    f'Recovered: New Pod `{newPods[0]}` Running, '
                    f'VMI phase `{vmiPhase}`.'
                )
                recovered = True
                break
            logs.info(f'Waiting for recovery (VMI phase={vmiPhase!r})...')
            time.sleep(10)

        assert recovered, (
            f'VM `{vmName}` did not recover within {recoveryTO}s after'
            '`virt-launcher` Pod is killed.'
        )

    @pytest.mark.krkn
    @pytest.mark.parametrize(
        'krknChaos,',
        [({
            'ns': 'bsod-test',
            'testPars': {
                'vmName': 'bsod-win2022',
            },
        },)],
        indirect=True,
    )
    def test_HostSideKernelScanOnVMnode(self, krknChaos):
        '''
        Host-side reach: run a command on the worker Node that runs the VM.

        Spins up a transient privileged pod on the target Node and runs a shell
        command with host visibility. That host level is exactly where the
        TLB-flush / `HYPERVISOR_ERROR` split-lock (#AC) signatures appear in
        the kernel log; those never reach the Windows guest dump. This test is
        the foundation lever for host-side fault injection, but is written
        non-destructively (it only reads).

        Steps:
         1. Resolve which Node the VMI runs on (KubeVirt-specific -> `oc`).
         2. Sanity-check that Node is `Ready`.
         3. Run a host-side command (`uname -r`) on it and assert the output,
            proving host-side execution works.
         4. As evidence, scan `dmesg` for split-lock/#AC/hypervisor lines and
            log them (not asserted: a clean host is healthy).
        '''
        client = krknChaos.krknClient
        oc = krknChaos.cli.oc
        ns = krknChaos.ns
        (vmName,) = krknChaos.testPars.values()

        #1. Which node does the VM run on? (KubeVirt VMI -> node name.)
        nodeRes = oc.Run(
            'get', f'VirtualMachineInstance/{vmName}',
            o="jsonpath='{.status.nodeName}'",
        )
        assert (nodeRes.success and nodeRes.stdout), (
            f'Could not resolve Node for `{vmName}`: {nodeRes.stderr}'
        )
        node = nodeRes.stdout.strip().strip("'")
        logs.info(f'VM `{vmName}` runs on node `{node}`.')

        #2. That node must be Ready (krkn-lib node inventory).
        readyNodes = client.list_ready_nodes()
        assert (node in readyNodes), (
            f'Node `{node}` is not Ready (ready: {readyNodes}).'
        )

        # 3. Host-side command: prove we can execute on the node.
        kernel = krknChaos.ExecOnNode(
            node, 'krkn-hostscan', 'uname -r',
            ns=ns,
        )
        logs.info(f'Node `{node}` kernel release: {kernel!r}')
        assert (kernel and kernel.strip()), (
            f'No output from host-side execution on `{node}` '
            '(helper Pod may be failed).'
        )

        # 4. Scan the host kernel log for BSOD-relevant signatures.
        scan = krknChaos.ExecOnNode(
            node,
            'krkn-hostscan',
            (
                'dmesg 2> /dev/null | '
                "grep -iE 'split.?lock|#AC|hypervisor' || true"
            ),
            ns=ns,
        )
        logs.info(f'Host kernel log scan on `{node}`:\n{scan}')
