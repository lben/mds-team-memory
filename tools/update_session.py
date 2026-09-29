"""One authenticated SSH/SFTP connection for an entire cross-platform update."""

import base64
import getpass
import hashlib
from pathlib import Path
import subprocess
import sys
import time


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


class Session:
    def __init__(self, target, known_hosts, *, client_factory=None):
        import paramiko

        self.client = (client_factory or paramiko.SSHClient)()
        self.sftp = None
        self.target = target
        known_hosts = Path(known_hosts).expanduser()
        known_hosts.parent.mkdir(parents=True, exist_ok=True)
        if not known_hosts.exists():
            known_hosts.touch(mode=0o600)
        self.client.load_system_host_keys()
        self.client.load_host_keys(str(known_hosts))
        expected = getattr(target, "host_key_sha256", "")

        class TrustHost(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                actual = fingerprint(key)
                if expected:
                    if actual != expected:
                        raise paramiko.SSHException("Server host key does not match host_key_sha256")
                else:
                    print(f"First connection to {hostname}: {actual}")
                    if not sys.stdin.isatty() or input("Trust this server key? Type yes: ").strip().lower() != "yes":
                        raise paramiko.SSHException("Server key was not accepted")
                client.get_host_keys().add(hostname, key.get_name(), key)
                client.save_host_keys(str(known_hosts))

        self.client.set_missing_host_key_policy(TrustHost())
        username, hostname = target.host.rsplit("@", 1)
        password = getpass.getpass(f"Server password for {target.host}: ")
        try:
            self.client.connect(hostname=hostname, username=username, port=target.ssh_port,
                                password=password, allow_agent=False, look_for_keys=False,
                                timeout=20, banner_timeout=30, auth_timeout=60)
            if expected and fingerprint(self.client.get_transport().get_remote_server_key()) != expected:
                raise paramiko.SSHException("Server host key does not match host_key_sha256")
            self.client.get_transport().set_keepalive(30)
            self.sftp = self.client.open_sftp()
        except BaseException:
            self.close()
            raise
        finally:
            password = None

    def run(self, command, capture=False):
        _, stdout, _ = self.client.exec_command(command)
        channel = stdout.channel
        channel.shutdown_write()
        out, err = bytearray(), bytearray()
        # Drain both streams concurrently: a pip/install log can otherwise fill
        # stderr's SSH window while a blocking stdout reader waits forever.
        try:
            while True:
                for ready, receive, buffer, sink in (
                    (channel.recv_ready, channel.recv, out, sys.stdout),
                    (channel.recv_stderr_ready, channel.recv_stderr, err, sys.stderr),
                ):
                    if ready():
                        block = receive(65536)
                        if capture:
                            buffer.extend(block)
                        else:
                            sink.write(block.decode("utf-8", errors="replace"))
                            sink.flush()
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                time.sleep(0.01)
            return subprocess.CompletedProcess(command, channel.recv_exit_status(),
                                               out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace"))
        finally:
            channel.close()

    def put(self, local, remote_path):
        temporary = remote_path + ".uploading"
        try:
            self.sftp.put(str(local), temporary)
            self.sftp.chmod(temporary, 0o600)
            # POSIX rename atomically replaces a completed upload. RHEL OpenSSH
            # supports this extension; a failed upload never changes the target.
            self.sftp.posix_rename(temporary, remote_path)
            return subprocess.CompletedProcess(["sftp", str(local), remote_path], 0, "", "")
        except OSError as error:
            return subprocess.CompletedProcess(["sftp", str(local), remote_path], 1, "", str(error))

    def close(self):
        if self.sftp is not None:
            self.sftp.close()
        self.client.close()
