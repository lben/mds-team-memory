"""Exercise real encrypted SSH/SFTP with PAM challenges, without a network server."""

from contextlib import contextmanager
import os
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

paramiko = pytest.importorskip("paramiko")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import deploylib
import update_session

PASSWORD = "  pasted password 密码  "
OTP = "124578"


@pytest.fixture(scope="module")
def host_key():
    return paramiko.RSAKey.generate(2048)


@contextmanager
def ssh_server(tmp_path, host_key, mode):
    client_socket, server_socket = socket.socketpair()
    remote = tmp_path / "remote"
    remote.mkdir()
    class Files(paramiko.SFTPServerInterface):
        def path(self, path):
            result = remote / path.lstrip("/")
            assert result.resolve().is_relative_to(remote.resolve())
            return result
        def open(self, path, flags, attr):
            descriptor = os.open(self.path(path), flags, 0o600)
            handle = paramiko.SFTPHandle(flags)
            stream = os.fdopen(descriptor, "wb" if flags & os.O_WRONLY else "rb")
            if flags & os.O_WRONLY:
                handle.writefile = stream
            else:
                handle.readfile = stream
            return handle
        def stat(self, path):
            return paramiko.SFTPAttributes.from_stat(self.path(path).stat())
        def chattr(self, path, attributes):
            paramiko.SFTPServer.set_file_attr(str(self.path(path)), attributes)
            return paramiko.SFTP_OK
        def posix_rename(self, old, new):
            os.replace(self.path(old), self.path(new))
            return paramiko.SFTP_OK
    class Server(paramiko.ServerInterface):
        passwords = []
        responses = []
        commands = []
        rounds = 0
        interactive_calls = 0
        password_verified = False
        def get_allowed_auths(self, username):
            if mode == "partial" and self.password_verified:
                return "keyboard-interactive"
            return "password,keyboard-interactive" if mode in {"password", "wrong_password", "partial"} else "publickey,keyboard-interactive"
        def check_auth_password(self, username, password):
            self.passwords.append(password)
            if mode == "password" and password == PASSWORD:
                return paramiko.AUTH_SUCCESSFUL
            if mode == "partial" and password == PASSWORD:
                self.password_verified = True
                return paramiko.AUTH_PARTIALLY_SUCCESSFUL
            return paramiko.AUTH_FAILED
        def check_auth_interactive(self, username, submethods):
            self.interactive_calls += 1
            if mode == "informational":
                return paramiko.InteractiveQuery("Company login", "Proceeding with PAM")
            if mode == "partial":
                return paramiko.InteractiveQuery("Second factor", "", ("Verification code: ", False))
            prompts = [("Password: ", False)]
            if mode != "single":
                prompts.append(("Verification code: ", False))
            if mode == "echo":
                prompts.insert(0, ("Username: ", True))
            return paramiko.InteractiveQuery("Company login", "Enter required credentials", *prompts)
        def check_auth_interactive_response(self, responses):
            self.responses.append(responses)
            if mode == "informational" and self.rounds == 0:
                self.rounds += 1
                return paramiko.InteractiveQuery("", "", ("Password: ", False), ("Verification code: ", False))
            expected = [OTP] if mode == "partial" else ([PASSWORD] if mode == "single" else [PASSWORD, OTP])
            if mode == "echo":
                expected.insert(0, "mds")
            return paramiko.AUTH_SUCCESSFUL if responses == expected else paramiko.AUTH_FAILED
        def check_channel_request(self, kind, channel_id):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        def check_channel_exec_request(self, channel, command):
            self.commands.append(command)
            def reply():
                time.sleep(0.01)
                channel.send(b"authenticated SSH command\n")
                channel.send_stderr(b"fixture stderr\n")
                channel.send_exit_status(0)
            threading.Thread(target=reply, daemon=True).start()
            return True
    server = Server()
    transport = paramiko.Transport(server_socket)
    transport.add_server_key(host_key)
    transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Files)
    errors, channels = [], []
    def serve():
        try:
            transport.start_server(server=server)
            while transport.is_active():
                channel = transport.accept(0.1)
                if channel is not None:
                    channels.append(channel)
        except (EOFError, OSError, paramiko.SSHException) as error:
            if transport.is_active():
                errors.append(error)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    class Client(paramiko.SSHClient):
        def connect(self, **kwargs):
            Client.authentication = kwargs["auth_strategy"]
            return super().connect(sock=client_socket, **kwargs)
    try:
        yield server, remote, Client
    finally:
        transport.close()
        client_socket.close()
        server_socket.close()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert not errors


@pytest.mark.parametrize("mode", ["password", "single", "multiple", "informational", "echo", "partial"])
def test_password_paste_and_keyboard_interactive_allow_commands_and_sftp(tmp_path, monkeypatch, host_key, mode, capsys):
    prompts = []
    def hidden(prompt):
        prompts.append(prompt)
        return PASSWORD if prompt.startswith("Server password") else OTP
    monkeypatch.setattr(update_session.getpass, "getpass", hidden)
    monkeypatch.setattr("builtins.input", lambda prompt: "mds")
    target = deploylib.Target("uat", {"host": "mds@localhost", "root": "/fixture"})
    target.host_key_sha256 = update_session.fingerprint(host_key)
    with ssh_server(tmp_path, host_key, mode) as (server, remote, client_factory):
        session = update_session.Session(target, tmp_path / "known_hosts", client_factory=client_factory)
        assert client_factory.authentication.password is None
        try:
            result = session.run("probe", capture=True)
            assert result.returncode == 0
            assert result.stdout == "authenticated SSH command\n"
            assert result.stderr == "fixture stderr\n"
            source = tmp_path / "source.bin"
            source.write_bytes(b"app/model upload fixture\x00")
            assert session.put(source, "/uploaded.bin").returncode == 0
            assert (remote / "uploaded.bin").read_bytes() == source.read_bytes()
            assert not (remote / "uploaded.bin.uploading").exists()
            assert server.commands == [b"probe"]
            assert len([p for p in prompts if p.startswith("Server password")]) == 1
            if mode in {"password", "single"}:
                assert len(prompts) == 1
            else:
                assert prompts[1:] == ["Verification code: "]
        finally:
            session.close()
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err
    assert OTP not in output.out + output.err


def test_wrong_password_does_not_retry_or_open_sftp(tmp_path, monkeypatch, host_key):
    monkeypatch.setattr(update_session.getpass, "getpass", lambda prompt: "wrong password")
    target = deploylib.Target("uat", {"host": "mds@localhost", "root": "/fixture"})
    target.host_key_sha256 = update_session.fingerprint(host_key)
    with ssh_server(tmp_path, host_key, "wrong_password") as (server, remote, client_factory):
        with pytest.raises(paramiko.AuthenticationException):
            update_session.Session(target, tmp_path / "known_hosts", client_factory=client_factory)
        assert server.passwords == ["wrong password"]
        assert server.interactive_calls == 0
        assert not list(remote.iterdir())
        assert client_factory.authentication.password is None


def test_wrong_host_key_refused_before_credentials_are_sent(tmp_path, monkeypatch, host_key):
    monkeypatch.setattr(update_session.getpass, "getpass", lambda prompt: PASSWORD)
    target = deploylib.Target("uat", {"host": "mds@localhost", "root": "/fixture"})
    target.host_key_sha256 = "SHA256:deliberately-wrong-key"
    with ssh_server(tmp_path, host_key, "multiple") as (server, remote, client_factory):
        with pytest.raises(paramiko.SSHException, match="host key"):
            update_session.Session(target, tmp_path / "known_hosts", client_factory=client_factory)
        assert server.passwords == []
        assert server.interactive_calls == 0
        assert client_factory.authentication.password is None


@pytest.mark.parametrize("prompt", ["Enter response: ", "New password: ", "Confirm password: ", "Password or one-time code: "])
def test_unknown_hidden_challenge_does_not_receive_cached_password(monkeypatch, prompt):
    prompts = []
    monkeypatch.setattr(update_session.getpass, "getpass", lambda prompt: prompts.append(prompt) or OTP)
    authentication = update_session.console_authentication("mds", PASSWORD)
    authentication.reuse_password = True
    assert authentication.challenge("", "", [(prompt, False)]) == [OTP]
    assert prompts == [prompt]
    authentication.clear()
    assert authentication.password is None
