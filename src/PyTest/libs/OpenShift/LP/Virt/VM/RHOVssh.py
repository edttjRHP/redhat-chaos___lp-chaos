'''Establish a persistent SSH multiplexed connection to VM.'''
import logging
import os
from pathlib import Path
import time
from typing import Optional

from libs.OpenShift.LP.Virt.CLI.VirtCtl import VirtCtlCLI
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
        cmdExec: Optional[CmdExec] = None,
    ) -> None:
        '''
        Initialize the SSH ControlMaster connection settings.

        :param ns:          Kubernetes Namespace where the target VM resides.
        :param host:        Name of the target VM.
        :param user:        SSH username to connect as.
        :param idFile:
            Path to the SSH private key used for authentication.
        :param ctrlPersist: ControlMaster idle timeout (default: '4h').
        :param kubeconfig:
            Optional path to `KUBECONFIG` file
            (default: read from Env. Var. `KUBECONFIG` or `None`).
            Passed to `VirtCtlCLI` if no `virtCtl` is provided.
        :param virtCtl:
            Optional `VirtCtlCLI` instance (default: auto-created
            with `n=ns` and `kubeconfig` if provided).
        :param cmdExec:     Optional command executor instance.
        '''
        self.ns = ns
        self.host = host
        self.user = user
        self.idFile = str(Path(os.path.expandvars(idFile)).expanduser())
        self.ctrlPersist = ctrlPersist
        kcfg = kubeconfig or os.environ.get('KUBECONFIG') or None
        if virtCtl:
            self.virtCtl = virtCtl
        else:
            kcOpts = {'kubeconfig': kcfg} if kcfg else {}
            self.virtCtl = VirtCtlCLI(n=ns, **kcOpts)
        self.cmdExec = cmdExec or CmdExec()
        self.target = f'{user}@{host}'
        self.ctrlPath = f'/tmp/ssh..{self.target}..sock'

    def IsAlive(self) -> bool:
        '''
        Check if the ControlMaster socket is active and responding.

        :return:    True if the master connection is alive, False otherwise.
        '''
        result = self.cmdExec.Run(
            ['ssh', '-O', 'check', '-o', f'ControlPath={self.ctrlPath}', 'muxSock'],
        )
        return result.success

    def Connect(self) -> None:
        '''
        Establish the persistent ControlMaster connection if not already active.

        :raises RuntimeError:   If tunnel establishment fails.
        '''
        if self.IsAlive(): return

        self.Close()    # Tear down any stale socket before re-establishing.
        logs.info(f'Establishing SSH ControlMaster tunnel to `{self.target}`.')
        result = self.virtCtl.Run(
            'ssh',
            '-t', '-o UserKnownHostsFile=/dev/null',
            '-t', '-o StrictHostKeyChecking=no',
            '-t', '-o ControlMaster=yes',
            '-t', f'-o ControlPath={self.ctrlPath}',
            '-t', f'-o ControlPersist={self.ctrlPersist}',
            '-t', '-N',
            '-i', self.idFile,
            f'{self.user}@vm/{self.host}',
        )
        if not result.success:
            raise RuntimeError(
                f'Failed to establish SSH tunnel to `{self.target}`: '
                f'{result.stderr}'
            )

    def Close(self) -> None:
        '''Tear down the ControlMaster connection, if active.'''
        self.cmdExec.Run(
            ['ssh', '-O', 'exit', '-o', f'ControlPath={self.ctrlPath}', 'muxSock'],
        )

    def Probe(self, timeout: int = 300, interval: int = 10) -> bool:
        '''
        Block until SSH is reachable or timeout expires.

        Establishes and tears down a test connection each attempt. Does not
        leave an open ControlMaster, caller must call `Connect()` again before
        actually able to use it. This way, this can also serve as a heartbeat
        check.

        :param timeout:
            Maximum wait in seconds (default: `300`).
        :param interval:
            Wait time, in seconds, between attempts (default: `10`).
        :return:
          - True     -> VM is reachable.
          - False    -> Timeout occurs.
        '''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.Connect()
                self.Close()
                return True
            except RuntimeError:
                logs.info(
                    f'SSH not ready on {self.target}, '
                    f'retrying in {interval}s...'
                )
                time.sleep(interval)
        logs.warning(f'SSH probe timeout on {self.target} after {timeout}s')
        return False

    def RunShell(self, command: str, stdin: Optional[str] = None) -> CmdRes:
        '''
        Run a command in the VM's default shell.

        :param command: Shell command to execute on the remote guest.
        :param stdin:
            Optional raw input piped verbatim to standard input.
            Caller must include trailing line terminator if required by the
            last input line.
        :return:        A `CmdRes` containing execution result.
        '''
        self.Connect()
        cmd = [
            'ssh',
            '-o', 'ControlMaster=no',
            '-o', f'ControlPath={self.ctrlPath}',
            'muxSock',
            command,
        ]
        logs.info(f'Running remote command on {self.target}: {command}')
        return self.cmdExec.Run(cmd, input=stdin)

    def RunPowerShell(self, script: str) -> CmdRes:
        '''
        Run a PowerShell script on the VM.

        :param script:
            PowerShell script text (piped verbatim to standard input).
            Caller must include trailing line terminator if required by the
            last input line.
        :return:    A `CmdRes` containing execution result.
        '''
        return self.RunShell('powershell.exe -NonInteractive -File -', script)

    def Send(self, localPath: Path, remotePath: str) -> CmdRes:
        '''
        Copy local files to the VM via SCP.

        :param localPath: Local file path to send.
        :param remotePath: Destination file path on the remote guest.
        :return:l   A `CmdRes` with the SCP result.
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
        return self.cmdExec.Run(cmd)

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
        return self.cmdExec.Run(cmd)
