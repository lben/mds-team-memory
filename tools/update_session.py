"""One authenticated SSH connection, with optional SFTP and verified upload fallback."""

import base64
import getpass
import hashlib
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def open_session(target, known_hosts):
    if getattr(target, "transport", "paramiko") == "openssh":
        from native_session import NativeSession
        return NativeSession(target, known_hosts)
    return Session(target, known_hosts)


def console_authentication(username, password):
    """Use explicit SSH methods so PAM challenges are not flattened to a password."""
    import paramiko

    class ConsoleAuthentication(paramiko.AuthStrategy):
        def __init__(self, username, password):
            super().__init__(ssh_config=None)
            self.username = username
            self.password = password
            self.reuse_password = False

        def clear(self):
            self.password = None

        def challenge(self, title, instructions, prompts):
            for message in (title, instructions):
                if message:
                    print(message, flush=True)
            answers = []
            for prompt, echo in prompts:
                # Reuse the initial password once, only for an identifiable
                # password prompt. Never send it as an OTP or a new password.
                is_password = bool(re.search(r"\bpassword\b", prompt, re.I))
                different_secret = bool(re.search(r"\b(new|confirm|repeat|otp|token|code|verification)\b|one[- ]time", prompt, re.I))
                if self.reuse_password and is_password and not different_secret:
                    answers.append(self.password)
                    self.reuse_password = False
                else:
                    label = prompt or "SSH challenge response: "
                    answers.append(input(label) if echo and not is_password else getpass.getpass(label))
            return answers

        def authenticate(self, transport):
            try:
                remaining = transport.auth_password(self.username, self.password, fallback=False)
            except paramiko.BadAuthenticationType as error:
                if "keyboard-interactive" not in error.allowed_types:
                    raise
                remaining = error.allowed_types
                self.reuse_password = True
            if not transport.is_authenticated() and "keyboard-interactive" in remaining:
                transport.auth_interactive(self.username, self.challenge)
            if not transport.is_authenticated():
                raise paramiko.AuthenticationException(
                    "SSH authentication is incomplete; the server requires another method "
                    "(for example a public key) in addition to the password/challenge")

    return ConsoleAuthentication(username, password)


class Session:
    def __init__(self, target, known_hosts, *, client_factory=None):
        import paramiko

        self.client = (client_factory or paramiko.SSHClient)()
        self.sftp = None
        self.sftp_unavailable = False
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
        authentication = console_authentication(username, password)
        try:
            self.client.connect(hostname=hostname, username=username, port=target.ssh_port,
                                auth_strategy=authentication,
                                timeout=20, banner_timeout=30, auth_timeout=60, channel_timeout=30)
            if expected and fingerprint(self.client.get_transport().get_remote_server_key()) != expected:
                raise paramiko.SSHException("Server host key does not match host_key_sha256")
            self.client.get_transport().set_keepalive(30)
            print("SSH authentication succeeded.", flush=True)
        except BaseException:
            self.close()
            raise
        finally:
            authentication.clear()
            password = None

    def run(self, command, capture=False):
        # Some company servers/gateways allow only one session channel at a
        # time. Release the cached SFTP channel before executing a command.
        if self.sftp is not None:
            # sshd frees the session only once sftp-server exits, so send EOF
            # and wait for that exit (as commands do) before the next channel.
            channel = self.sftp.get_channel()
            channel.shutdown_write()
            channel.recv_exit_status()
            self.sftp.close()
            self.sftp = None
        return self._execute(command, capture=capture)

    def _execute(self, command, capture=False, source=None):
        import paramiko

        try:
            # Keep stdin alive while uploading: ChannelStdinFile destruction
            # sends EOF, which would truncate the binary stream prematurely.
            stdin, stdout, stderr = self.client.exec_command(command)
        except (paramiko.SSHException, OSError, EOFError) as error:
            raise RuntimeError(
                f"SSH login succeeded, but opening a command channel failed: {error}. "
                "Check that host/ssh_port identify the deployment server and that the server or "
                "SSH gateway permits non-interactive command sessions. This is a server/channel "
                "access issue; changing the password or rebuilding the app will not repair it.") from error
        channel = stdout.channel
        sender = None
        send_errors = []
        if source is None:
            channel.shutdown_write()
        else:
            channel.settimeout(60)
            def send():
                try:
                    with Path(source).open("rb") as incoming:
                        while block := incoming.read(1024 * 1024):
                            channel.sendall(block)
                except Exception as error:
                    send_errors.append(error)
                finally:
                    try:
                        channel.shutdown_write()
                    except (OSError, EOFError):
                        pass
            sender = threading.Thread(target=send, daemon=True)
            sender.start()
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
            code = channel.recv_exit_status()
            if sender is not None:
                sender.join(timeout=1)
                if sender.is_alive() or send_errors:
                    code = code or 1
                    err.extend(b"\nSSH upload did not complete.\n")
            return subprocess.CompletedProcess(command, code, out.decode("utf-8", errors="replace"),
                                               err.decode("utf-8", errors="replace"))
        finally:
            channel.close()
            if sender is not None:
                sender.join(timeout=1)
            if stdin is not None:
                stdin.close()

    def put(self, local, remote_path):
        import paramiko

        if self.sftp is None and not self.sftp_unavailable:
            try:
                self.sftp = self.client.open_sftp()
            except (paramiko.SSHException, OSError, EOFError) as error:
                self.sftp_unavailable = True
                print(f"SFTP unavailable ({error}); using verified uploads over SSH command channels.", flush=True)
        if self.sftp_unavailable:
            return self._put_over_ssh(local, remote_path)
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

    def _put_over_ssh(self, local, remote_path):
        local = Path(local)
        try:
            size = local.stat().st_size
            with local.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            temporary = remote_path + ".uploading-" + uuid.uuid4().hex
            script = ('set -eu; umask 077; trap \'rm -f -- "$1"\' EXIT; '
                      'cat > "$1"; '
                      'if test "$(wc -c < "$1")" -ne "$3"; then echo "SSH upload incomplete" >&2; exit 1; fi; '
                      'if test "$(sha256sum "$1" | cut -d\' \' -f1)" != "$4"; then '
                      'echo "SSH upload checksum mismatch" >&2; exit 1; fi; '
                      'chmod 600 "$1"; mv -f -- "$1" "$2"; trap - EXIT')
            command = " ".join(shlex.quote(arg) for arg in
                               [self.target.bash, "-c", script, "upload", temporary, remote_path, str(size), digest])
            result = self._execute(command, capture=True, source=local)
            return subprocess.CompletedProcess(["ssh-upload", str(local), remote_path], result.returncode,
                                               result.stdout, result.stderr)
        except OSError as error:
            return subprocess.CompletedProcess(["ssh-upload", str(local), remote_path], 1, "", str(error))

    def close(self):
        if self.sftp is not None:
            self.sftp.close()
        self.client.close()
