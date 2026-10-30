'''KubeVirt VirtualMachine wrapper with lifecycle and SSH management.'''
import logging
import sys
import time
from typing import Any, Optional

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
        atomic. Cleanup delegates to `OCcli.CleanUpRes()`.

        :param vmList:
            VM wrappers whose SSH connections are closed.
        :param ownVMs:
            Ownership records containing:
              - oc:     CLI client from the resource creation context.
              - resRef: VM Resource Reference (`VirtualMachine/<name>`).
              - ownUID: VM UID recorded after resource creation.

        :return:
            `None` after all cleanup attempts.

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
            except Exception as error:
                clnFail.append(f'Close SSH for `{vm.name}`: {error}')
                logs.exception('Could not close SSH for VM `%s`.', vm.name)
        try: OCcli.CleanUpRes(ownVMs)
        except Exception as error: clnFail.append(str(error))
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
            Path to the SSH private key. If `None` or empty, `NewSSH` defaults
            to `~/.ssh/id_ed25519`; its `idFile` keyword argument can override
            this default.
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

    def Start(
        self,
        timeout: str = '5m',
        provTO: float = 1800.0,
    ) -> bool:
        '''
        Wait for provisioning to finish, then start the VM.

        :param timeout: Maximum time to wait for VM readiness (default: `5m`).
        :param provTO:  Provisioning wait budget in sec. (default: `1800.0`).

        :return:        `True` if the VM reaches ready state.
        '''
        provDeadline = time.monotonic() + provTO
        while True:
            status = self._Status()
            if (status != 'Provisioning'): break
            remTO = provDeadline - time.monotonic()
            if (remTO <= 0): return False
            time.sleep(min(5.0, remTO))

        if (status not in (
            'Running', 'Starting', 'Scheduling', 'Migrating'
        )):
            if not self.cli.virtCtl.Run('start', self.name).success:
                return False
        # KubeVirt mirrors VMI Ready onto VM Ready, covering the VMI readiness.
        return self.cli.oc.Run(
            'wait',
            f'VirtualMachine/{self.name}',
            **{'for': 'condition=Ready'}, timeout=timeout,
        ).success

    def Stop(self, timeout: str = '5m') -> bool:
        '''
        Stop the VM.

        :param timeout: Maximum time to wait for VM stop (default: `5m`).

        :return:        `True` if the VM is stopped within the given time.
        '''
        if (self._Status() not in ('Stopped', 'Stopping')):
            if not self.cli.virtCtl.Run('stop', self.name).success:
                return False
        return self.cli.oc.Run(
            'wait',
            f'VirtualMachine/{self.name}',
            **{'for': 'jsonpath={.status.printableStatus}=Stopped'},
            timeout=timeout,
        ).success

    def NewSSH(self, probeTO: int = 300, **kwargs: Any) -> RHOVsshCon:
        '''
        Create a new `RHOVsshCon` instance for this VM.

        Probes SSH readiness before returning, with a retry timeout and waits
        capped to its remaining duration.

        :param probeTO:
            Positive numeric SSH retry timeout in seconds (default: `300`).
        :param kwargs:
            Forwarded to `RHOVsshCon.__init__` (override `user`, `idFile`,
            `virtCtl`, etc.). Defaults to this VM's `virtCtl` client; pass
            `virtCtl=None` to create a client using `ns` and `kubeconfig`.

        :return:
            A new `RHOVsshCon` instance, probed for readiness.
        '''
        defArgs = {
            'ns': self.namespace,
            'host': self.name,
            'user': self.sshUsr,
            'idFile': (self.sshKey or '~/.ssh/id_ed25519'),
            'virtCtl': self.cli.virtCtl,
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
        if (self._ssh is not None):
            self._ssh.Close()
            self._ssh = None

    @property
    def ssh(self) -> RHOVsshCon:
        '''Lazy default SSH connection (created on first access).'''
        if (self._ssh is None):
            self._ssh = self.NewSSH()
        return self._ssh
