'''Establish a persistent SSH multiplexed connection to VM.'''
import hashlib
import logging
import os
from pathlib import Path
import time
from typing import Optional

from libs.CustomTypes import NamedDict
from libs.OpenShift.LP.Virt.CLI.VirtCtl import VirtCtlCLI
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.Utils.CmdExec import CmdExec, CmdRes


logs = logging.getLogger(__name__)


class RHOVsshCon:
    '''
    Persistent SSH ControlMaster connection to a RHOV/KubeVirt VM, established
    via `virtctl ssh`.
    '''

    def __init__(
        self,
        ns: str,
        host: str,
        user: str,
        idFile: str,
        ctrlPersist: str = '4h',
        kubeconfig: Optional[str] = None,
        virtCtl: Optional['VirtCtlCLI'] = None,
        oc: Optional[OCcli] = None,
        cmdExec: Optional[CmdExec] = None,
    ) -> None:
        '''
        Initialize the SSH ControlMaster connection settings.

        Supplied clients and executor are reused. The Cluster UID is looked up
        once during intantiation.

        :param ns:          Kubernetes Namespace where the target VM resides.
        :param host:        Name of the target VM.
        :param user:        SSH username to connect as.
        :param idFile:
            Path to the SSH private key used for authentication.
        :param ctrlPersist: ControlMaster idle timeout (default: '4h').
        :param kubeconfig:
            Optional path to `KUBECONFIG` file
            (default: read from Env. Var. `KUBECONFIG` or `None`).
            Passed to auto-created `OCcli` and `VirtCtlCLI` clients.
        :param virtCtl:
            Optional `VirtCtlCLI` instance (default: auto-created with `n=ns`
            and `kubeconfig` if provided). Must address the same cluster as
            `oc`.
        :param oc:
            Optional `OCcli` instance (default: auto-created with `n=ns` and
            `kubeconfig` if provided). Must address the same cluster as
            `virtCtl`.
        :param cmdExec:     Optional command executor instance.

        :raises RuntimeError:
            The cluster UID lookup fails or returns an empty UID.
        '''
        self.ns = ns
        self.host = host
        self.user = user
        self.idFile = str(Path(os.path.expandvars(idFile)).expanduser())
        self.ctrlPersist = ctrlPersist
        kCfg = kubeconfig or os.environ.get('KUBECONFIG') or None
        if (cmdExec is None): cmdExec = CmdExec()
        kcOpts = {'kubeconfig': kCfg} if kCfg else {}
        if (oc is None): oc = OCcli(n=ns, **kcOpts)
        if (virtCtl is None): virtCtl = VirtCtlCLI(n=ns, **kcOpts)
        self.cli = NamedDict(cmdExec=cmdExec, oc=oc, virtCtl=virtCtl)
        (lookupOK, clsID, lookupErr) = self.cli.oc.GetResUID(
            'namespace/kube-system',
        )
        if (not lookupOK or not clsID):
            raise RuntimeError(
                f'Failed to obtain UID of namespace/kube-system: {lookupErr}'
            )
        self.target = f'{user}@{host}.{ns}.{clsID}'
        # In Linux, the UDF Socket full path is limited to 108 Bytes (with
        #   terminating `\0`.). To mitigate potential long names, bound to a
        #   fixed 80 Bytes path.
        self.ctrlPath = (
            '/tmp/ssh..'
            f'{hashlib.sha256(self.target.encode("utf-8")).hexdigest()}..sock'
        )

    def IsAlive(self, cmdTO: int = 5) -> bool:
        '''
        Check if the ControlMaster socket is active and responding.

        :param cmdTO:
            Timeout (in sec.) for each command execution (default: 5).

        :return:    Return `True` if the master connection is alive.

        :raises ValueError: The `cmdTO` is invalid.
        '''
        if (
            not isinstance(cmdTO, int) or
            isinstance(cmdTO, bool) or
            (cmdTO <= 0)
        ): raise ValueError('The `cmdTO` must be a positive integer.')
        return self.cli.cmdExec.Run(
            [
                'ssh', '-O', 'check', '-o',
                f'ControlPath={self.ctrlPath}', 'muxSock',
            ],
            warnOnFail=False,
            tryMax=1,
            spPars={'timeout': cmdTO},
        ).success

    def Connect(self, cmdTO: int = 5) -> None:
        '''
        Establish the persistent ControlMaster connection if not already
        active.

        :param cmdTO:
            Timeout (in sec.) for each command execution (default: 5).

        :raises ValueError: The `cmdTO` is invalid.
        :raises RuntimeError: If tunnel establishment fails.
        '''
        if (
            not isinstance(cmdTO, int) or
            isinstance(cmdTO, bool) or
            (cmdTO <= 0)
        ): raise ValueError('The `cmdTO` must be a positive integer.')
        if self.IsAlive(cmdTO): return

        # Tear down any stale socket before re-establishing.
        self.Close(cmdTO)
        logs.info(f'Establishing SSH ControlMaster tunnel to `{self.target}`.')
        if not (result := self.cli.virtCtl.Run(
            'ssh',
            '-t', '-o UserKnownHostsFile=/dev/null',
            '-t', '-o StrictHostKeyChecking=no',
            '-t', '-o ControlMaster=yes',
            '-t', f'-o ControlPath={self.ctrlPath}',
            '-t', f'-o ControlPersist={self.ctrlPersist}',
            '-t', '-N',
            '-i', self.idFile,
            f'{self.user}@vm/{self.host}',
            tryMax=1,
            spPars={'timeout': cmdTO},
        )).success:
            raise RuntimeError(
                f'Failed to establish SSH tunnel to `{self.target}`: '
                f'{result.stderr}'
            )

    def Close(self, cmdTO: int = 5) -> None:
        '''
        Tear down the ControlMaster connection, if active (best effort).

        :param cmdTO:
            Timeout (in sec.) for each command execution (default: 5).

        :raises ValueError: The `cmdTO` is invalid.
        '''
        if (
            not isinstance(cmdTO, int) or
            isinstance(cmdTO, bool) or
            (cmdTO <= 0)
        ): raise ValueError('The `cmdTO` must be a positive integer.')
        self.cli.cmdExec.Run(
            [
                'ssh', '-O', 'exit', '-o',
                f'ControlPath={self.ctrlPath}', 'muxSock',
            ],
            warnOnFail=False,
            tryMax=1,
            spPars={'timeout': cmdTO},
        )

    def Probe(
        self,
        probeTO: int = 300,
        interval: int = 10,
        cmdTO: int = 5,
    ) -> bool:
        '''
        Probe SSH readiness within the specified time budget.

        Establishes and tears down a test connection each attempt. Does not
        leave an open ControlMaster, caller must call `Connect()` again before
        actually able to use it. This way, this can also serve as a heartbeat
        check.

        Cleanup is attempted (at best effort) even when connection
        establishment fails. Each attempt requires the full `5 * cmdTO`
        allowance remaining.

        :param probeTO:
            Total budget (in sec.) for probing activity (default: `300`). Must
            at least cover `5 * cmdTO` (min. budget).
        :param interval:
            Wait (in sec.) between probing attempts (default: `10`).
        :param cmdTO:
            Timeout (in sec.) for each command execution (default: 5).

        :return:
            True:   VM is reachable.
            False:  Timeout occurs.

        :raises ValueError:
            The timeout value is invalid, or `probeTO` cannot even cover the
            min. budget.
        '''
        for (k, v) in locals().items():
            if (k == 'self'): continue
            if (
                not isinstance(v, int) or
                isinstance(v, bool) or
                (v <= 0)
            ): raise ValueError(f'The `{k}` must be a positive integer.')
        if ((tryMaxTime := 5 * cmdTO) > probeTO):
            raise ValueError(
                'The `probeTO` must cover `5 * cmdTO`.'
            )

        deadline = time.monotonic() + (remTO := probeTO)
        while (remTO >= tryMaxTime):
            try:
                try:
                    self.Connect(cmdTO)
                finally:
                    self.Close(cmdTO)
                return True
            except RuntimeError:
                if ((
                    remTO := deadline - time.monotonic()
                ) < (interval + tryMaxTime)): break
                logs.info(
                    f'SSH not ready on {self.target}, retrying in '
                    f'{interval}s...'
                )
                time.sleep(interval)
                remTO -= interval

        logs.warning(f'SSH probe failed on {self.target} (budget: {probeTO}s)')
        return False

    def RunShell(
        self,
        command: str,
        stdin: Optional[str] = None,
        tty: bool = False,
    ) -> CmdRes:
        '''
        Run a command in the VM's default shell.

        :param command: Shell command to execute on the remote guest.
        :param stdin:
            Optional raw input piped verbatim to standard input.
            Caller must include trailing line terminator if required by the
            last input line.
        :param tty:
            Allocate Remote TTY or not (default: False).

        :return:        A `CmdRes` containing execution result.
        '''
        self.Connect()
        cmd = [
            'ssh',
            ('-t' if tty else '-T'),
            '-o', 'ControlMaster=no',
            '-o', f'ControlPath={self.ctrlPath}',
            'muxSock',
            command,
        ]
        logs.info(f'Running remote command on {self.target}: {command}')
        return self.cli.cmdExec.Run(cmd, input=stdin)

    def RunPowerShell(self, script: str, interactive: bool = False) -> CmdRes:
        '''
        Run a PowerShell script on the VM.

        :param script:
            PowerShell script text (piped verbatim to standard input).
            Caller must include trailing line terminator if required by the
            last input line.
        :param interactive:
            Allow interactive prompting to user (default: False).

        :return:    A `CmdRes` containing execution result.
        '''
        return self.RunShell((
            f"powershell.exe {'' if interactive else '-NonInteractive '}"
            '-Command -'
        ), script)

    def Send(self, localPath: Path, remotePath: str) -> CmdRes:
        '''
        Copy local files to the VM via SCP.

        :param localPath: Local file path to send.
        :param remotePath: Destination file path on the remote guest.

        :return:    A `CmdRes` with the SCP result.
        '''
        self.Connect()
        cmd = [
            'scp',
            '-o', 'ControlMaster=no',
            '-o', f'ControlPath={self.ctrlPath}',
            str(localPath),
            f'muxSock:{remotePath}',
        ]
        logs.info(f'Copying {localPath} to {self.target}:{remotePath}')
        return self.cli.cmdExec.Run(cmd)

    def Recv(self, remotePath: Path, localPath: str) -> CmdRes:
        '''
        Copy remote files from the VM to local filesystem via SCP.

        :param remotePath:  Source file path on the remote guest.
        :param localPath:   Destination file path on the local machine.

        :return:            A `CmdRes` with the SCP result.
        '''
        self.Connect()
        cmd = [
            'scp',
            '-o', 'ControlMaster=no',
            '-o', f'ControlPath={self.ctrlPath}',
            f'muxSock:{remotePath}',
            str(localPath),
        ]
        logs.info(f'Downloading {self.target}:{remotePath} to {localPath}')
        return self.cli.cmdExec.Run(cmd)
