'''KubeVirt VirtualMachine wrapper with lifecycle and SSH management.'''
import logging
import sys
from typing import Optional

from libs.CustomTypes import NamedDict
from libs.OpenShift.LP.Virt.CLI.VirtCtl import VirtCtlCLI
from libs.OpenShift.LP.Virt.VM.RHOVssh import RHOVsshCon
from libs.OpenShift.OCP.CLI.OC import OCcli


logs = logging.getLogger(__name__)


class VM:
    '''
    Wrapper around a KubeVirt `VirtualMachine` resource.

    Provides `Start`/`Stop` lifecycle via `virtCtl`, and lazy SSH
    access via `RHOVsshCon`.
    '''

    @staticmethod
    def GetVMuid(oc: OCcli, vmName: str) -> tuple[bool, str, str]:
        '''
        Look up a VM UID in the CLI client's configured namespace.

        :param oc:
            OpenShift CLI client configured for the target namespace.
        :param vmName:
            VirtualMachine resource name to look up.
        :return:
            A tuple where:
              - lookupOK:   Lookup command success, not a VM-existence flag.
              - vmUID:      Stripped VM UID output, empty when the resource is
                            absent.
              - lookupErr:  Stripped standard error output, which may be empty.
        '''
        result = oc.Run(
            'get',
            f'VirtualMachine/{vmName}',
            o='jsonpath={.metadata.uid}',
            ignore_not_found=True,
        )
        return (result.success, result.stdout.strip(), result.stderr.strip())

    @staticmethod
    def CleanUpVMs(
        vmList: list['VM'],
        ownVMs: list[tuple[OCcli, str, str]],
    ) -> None:
        '''
        Close SSH connections and delete VM resources recorded as owned.

        SSH connections are closed for every wrapper in `vmList`, including
        wrappers for reused VMs. Each ownership record is looked up through the
        same CLI context used to create the resource. A VM is deleted only when
        its current UID matches the recorded UID; lookup and deletion are not
        atomic.

        :param vmList:
            VM wrappers whose SSH connections are closed.
        :param ownVMs:
            Ownership records containing:
              - oc:     CLI client from the resource creation context.
              - vmName: VirtualMachine resource name.
              - ownUID: UID recorded after resource creation.
        :raises RuntimeError:
            Cleanup continues after individual failures. After all attempts,
            aggregated failures raise `RuntimeError` when no exception is
            active. Otherwise, failures are logged and the active exception is
            preserved.
        '''
        primExc = sys.exc_info()[1]
        clnFail = []

        for vm in vmList:
            try: vm.RmvSSH()
            except Exception as ex:
                clnFail.append(f'Close SSH for `{vm.name}`: {ex}')
                logs.exception(f'Could not close SSH for VM `{vm.name}`.')

        for (oc, vmName, ownUID) in ownVMs:
            try:
                (lookupOK, curUID, lookupErr) = VM.GetVMuid(oc, vmName)
                if not lookupOK:
                    clnFail.append(
                        f'Verify ownership of `{vmName}` before deletion: '
                        f'{lookupErr}'
                    )
                    logs.error(
                        f'Could not verify ownership of VM `{vmName}` before '
                        f'deletion: {lookupErr}'
                    )
                    continue
                if not curUID:
                    logs.info(f'VM `{vmName}` is already absent.')
                    continue
                if (curUID != ownUID):
                    logs.warning(
                        f'Not deleting VM `{vmName}` because its UID changed.'
                    )
                    continue

                phase, result = oc.DelRes(f'VirtualMachine/{vmName}')
                if not result.success:
                    clnFail.append(
                        f'Delete `{vmName}` at `{phase.name}`: {result.stderr}'
                    )
                    logs.error(
                        f'Could not clean up VM `{vmName}` at '
                        f'`{phase.name}`: {result.stderr}'
                    )
                    continue
                logs.info(f'Cleaned up VM `{vmName}`.')
            except Exception as ex:
                clnFail.append(f'Delete `{vmName}`: {ex}')
                logs.exception(f'Could not clean up VM `{vmName}`.')

        if clnFail:
            clnErr = 'VM cleanup failed: ' + '; '.join(clnFail)
            if (primExc is None): raise RuntimeError(clnErr)
            logs.error('%s; preserving primary exception: %s', clnErr, primExc)

    def __init__(
        self,
        name: str,
        ns: str,
        oc: Optional[OCcli] = None,
        virtCtl: Optional[VirtCtlCLI] = None,
        sshUsr: str = 'qa-usr',
        sshKey: Optional[str] = None,
    ) -> None:
        '''
        :param name:
            VM resource name.
        :param ns:
            Kubernetes Namespace.
        :param oc:
            Optional `OCcli` instance. If `None`, a default instance is created
            with `ns` pre-configured.
        :param virtCtl:
            Optional `VirtCtlCLI` instance. If `None`, a default instance is
            created with `ns` pre-configured.
        :param sshUsr:
            SSH UserName (default: `'qa-usr'`).
        :param sshKey:
            Path to the SSH private key. If `None`, depends on the `ssh` client
            used (for OpenSSH, it defaults to `~/.ssh/id_<xxx>`).
        '''
        self.name = name
        self.namespace = ns
        self.cli = NamedDict(
            oc=(oc or OCcli(n=ns)),
            virtCtl=(virtCtl or VirtCtlCLI(n=ns)),
        )
        self.sshUsr = sshUsr
        self.sshKey = sshKey
        self._ssh: Optional[RHOVsshCon] = None

    def _Status(self) -> str:
        '''Query the VM's current `printableStatus` via `oc get`.'''
        result = self.cli.oc.Run(
            'get', f'VirtualMachine/{self.name}',
            o="jsonpath='{.status.printableStatus}'",
        )
        return result.stdout.strip("'") if result.success else ''

    def Start(self) -> bool:
        '''
        Start the VM if not already running or transitioning.

        :return:    `True` if the VM is starting or was already active.
        '''
        if self._Status() in (
            'Running', 'Starting', 'Scheduling', 'Provisioning', 'Migrating'
        ): return True
        return self.cli.virtCtl.Run('start', self.name).success

    def Stop(self) -> bool:
        '''
        Stop the VM if not already stopped or transitioning.

        :return:    `True` if the VM is stopping or was already inactive.
        '''
        if self._Status() in ('Stopped', 'Stopping'): return True
        return self.cli.virtCtl.Run('stop', self.name).success

    def NewSSH(self, probeTO: int = 300, **kwargs) -> RHOVsshCon:
        '''
        Create a new `RHOVsshCon` instance for this VM.

        Probes SSH readiness before returning. Blocks up to `probeTO` seconds
        waiting for SSH Server in the VM to become reachable.

        :param probeTO:
            SSH probe timeout in seconds (default: `300`).
        :param kwargs:
            Forwarded to `RHOVsshCon.__init__` (override `user`, `idFile`,
            `kubeconfig`, etc.).
        :return:
            A new `RHOVsshCon` instance, probed for readiness.
        '''
        defArgs = {
            'ns': self.namespace,
            'host': self.name,
            'user': self.sshUsr,
            'idFile': self.sshKey or '~/.ssh/id_ed25519',
        }
        conn = RHOVsshCon(**{**defArgs, **kwargs})
        if not conn.Probe(timeout=probeTO):
            raise TimeoutError(
                f'SSH readiness probe timed out for VM `{self.name}` '
                f'after {probeTO} seconds.'
            )
        return conn

    def RmvSSH(self) -> None:
        '''Remove and close the SSH connection, if active.'''
        if self._ssh is not None:
            self._ssh.Close()
            self._ssh = None

    @property
    def ssh(self) -> RHOVsshCon:
        '''Lazy default SSH connection (created on first access).'''
        if self._ssh is None:
            self._ssh = self.NewSSH()
        return self._ssh
