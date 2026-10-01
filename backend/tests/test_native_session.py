"""Native CLI routing, host trust, real OpenSSH/SCP and atomic uploads."""

import base64
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import client_cli
import deploylib
import native_session
import update
import update_session


def target_for(tmp_path):
    target = deploylib.Target("uat", {"host": "deployer@server", "root": "/app"})
    target.transport = "openssh"
    target.local = {"ssh": shutil.which("ssh"), "scp": shutil.which("scp")}
    target.host_key_sha256 = ""
    return target


def test_native_paths_select_transport_and_keep_windows_paths_literal(tmp_path):
    config = tmp_path / "deploy.toml"
    config.write_text('[uat]\nhost="mds@server"\nroot="/app"\nssh_options=["-i", "C:/Keys/work key"]\n'
                      "[local]\nssh='C:\\Program Files\\Cmder\\ssh.exe'\nscp='C:\\Program Files\\Cmder\\scp.exe'\n")
    target, _ = update.configured_target("uat", config)
    assert target.transport == "openssh"
    assert target.local["ssh"] == r"C:\Program Files\Cmder\ssh.exe"
    assert target.ssh_options == ["-i", "C:/Keys/work key"]


def test_factory_does_not_load_paramiko_for_native(tmp_path, monkeypatch):
    target = target_for(tmp_path)
    sentinel = object()
    monkeypatch.setattr(native_session, "NativeSession", lambda *a: sentinel)
    monkeypatch.setattr(update_session, "Session", lambda *a: pytest.fail("Paramiko used"))
    assert update_session.open_session(target, tmp_path / "known_hosts") is sentinel


@pytest.mark.parametrize("local", [{"ssh": "/missing/ssh"}, {"scp": "relative/scp"},
                                   {"scp_protocol": "ftp"}, {"ssh_options": "-v"}])
def test_invalid_native_configuration_fails_before_network(tmp_path, local):
    target = target_for(tmp_path)
    target.local.update(local)
    with pytest.raises(ValueError):
        native_session.validate_local(target)


@pytest.mark.parametrize("transport", ["openssh", "paramiko"])
def test_check_does_not_resolve_paramiko_or_connect(tmp_path, monkeypatch, transport):
    config = tmp_path / "deploy.toml"
    config.write_text(f'[uat]\nhost="mds@server"\nroot="/app"\n[local]\ntransport="{transport}"\n')
    monkeypatch.setattr(update, "checked_package", lambda: (tmp_path, {}, {"parts": [], "archive": {"size": 0}}, tmp_path))
    monkeypatch.setattr(update, "Session", lambda *a: pytest.fail("Network opened"))
    monkeypatch.setattr(client_cli.subprocess, "run", lambda *a, **k: pytest.fail("Dependency command invoked"))
    assert client_cli.main(["update", "--config", str(config), "--check"]) == 0


def test_client_launcher_routes_native_without_optional_dependencies(tmp_path, monkeypatch):
    config = tmp_path / "deploy.toml"
    config.write_text('[local]\nssh="ssh"\nscp="scp"\n')
    calls = []
    monkeypatch.setattr(update, "main", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(client_cli.subprocess, "run", lambda *a, **k: pytest.fail("Native mode invoked uv dependencies"))
    assert client_cli.main(["update", "PROD", f"--config={config}"]) == 0
    assert calls == [["PROD", f"--config={config}"]]


def test_local_testdata_preview_ignores_broken_deployment_config(tmp_path, monkeypatch):
    import testdata
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/deploy.toml").write_text("not valid TOML")
    monkeypatch.setattr(client_cli, "ROOT", tmp_path)
    monkeypatch.setattr(testdata, "configured_target", lambda *a: pytest.fail("Local preview read deployment config"))
    monkeypatch.setattr(client_cli.subprocess, "run", lambda *a, **k: pytest.fail("Local preview resolved dependencies"))
    assert client_cli.main(["testdata", "preview", "--dataset", "expanded", "--percent", "1"]) == 0


def test_client_launcher_preserves_paramiko_with_exact_arguments(tmp_path, monkeypatch):
    config = tmp_path / "deploy.toml"
    config.write_text('[local]\ntransport="paramiko"\n')
    calls = []
    monkeypatch.setattr(client_cli.shutil, "which", lambda _: "/bin/uv")
    monkeypatch.setattr(client_cli.subprocess, "run", lambda argv: calls.append(argv) or subprocess.CompletedProcess(argv, 7))
    assert client_cli.main(["update", "UAT", "--config", str(config)]) == 7
    assert calls[0] == ["/bin/uv", "run", "--system-certs", "--python", "3.12",
                        str(client_cli.ROOT / "tools/update.py"), "UAT", "--config", str(config)]


@pytest.mark.parametrize("hashed", [False, True])
def test_fingerprint_pin_filters_out_other_server_keys(tmp_path, hashed):
    key = base64.b64encode(b"server public key").decode()
    hostname = "[server]:2222"
    host = hostname
    if hashed:
        salt = b"salt for this test"
        host = "|1|" + base64.b64encode(salt).decode() + "|" + base64.b64encode(hmac.new(salt, hostname.encode(), hashlib.sha1).digest()).decode()
    path = tmp_path / "known_hosts"
    path.write_text(f'{host} ssh-ed25519 {key}\n{hostname} ssh-rsa {base64.b64encode(b"other key").decode()}\n')
    expected = "SHA256:" + base64.b64encode(hashlib.sha256(b"server public key").digest()).decode().rstrip("=")
    assert native_session._pinned_keys(path, hostname, expected) == f"{hostname} ssh-ed25519 {key}\n"
    with pytest.raises(ValueError, match="no matching server key"):
        native_session._pinned_keys(path, "wrong-host", expected)


def test_native_prompt_streams_and_scp_use_configured_ssh(tmp_path, monkeypatch):
    target = target_for(tmp_path)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if len(argv) == 1:
            return subprocess.CompletedProcess(argv, 1, "", "usage: scp [-346ABCOpqRrsTv] source target")
        return subprocess.CompletedProcess(argv, 0, "output" if kwargs.get("stdout") == subprocess.PIPE else None)
    monkeypatch.setattr(native_session.subprocess, "run", run)
    session = native_session.NativeSession(target, tmp_path / "folder with spaces/known_hosts")
    session.run("echo output", capture=True)
    source = tmp_path / "payload with spaces"
    source.write_bytes(b"binary\0\xff\r\n")
    assert session.put(source, "/server/data").returncode == 0
    _, kwargs = calls[1]
    assert kwargs["stdout"] == subprocess.PIPE and "stderr" not in kwargs
    assert "stdin" not in kwargs and "-n" in calls[1][0]
    upload, kwargs = calls[2]
    assert upload[:4] == [session.scp, "-O", "-S", session.ssh]
    assert upload[-2] == "payload"
    assert "stderr" not in kwargs and "capture_output" not in kwargs
    assert "stdin" not in kwargs
    assert not Path(kwargs["cwd"]).exists()
    assert "sha256sum" in calls[3][0][-1] and "mv -f" in calls[3][0][-1]
    session.close()


def test_revoked_pinned_key_is_refused(tmp_path):
    key = base64.b64encode(b"revoked public key").decode()
    path = tmp_path / "known_hosts"
    path.write_text(f"server ssh-ed25519 {key}\n@revoked server ssh-ed25519 {key}\n")
    expected = "SHA256:" + base64.b64encode(hashlib.sha256(b"revoked public key").digest()).decode().rstrip("=")
    with pytest.raises(ValueError, match="@revoked"):
        native_session._pinned_keys(path, "server", expected)


@pytest.mark.parametrize("protocol,usage,flags", [
    ("scp", "usage: scp [-346BCpqrv] source target", []),
    ("scp", "usage: scp [-346ABCOpqRrsTv] source target", ["-O"]),
    ("sftp", "usage: scp [-346ABCOpqRrsTv] source target", ["-s"]),
])
def test_protocol_selection_with_old_and_modern_clients(tmp_path, monkeypatch, protocol, usage, flags):
    target = target_for(tmp_path)
    target.local["scp_protocol"] = protocol
    monkeypatch.setattr(native_session.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 1, "", usage))
    session = native_session.NativeSession(target, tmp_path / "known_hosts")
    assert session.scp_flags == flags
    session.close()


def test_native_local_binaries_with_spaces_are_one_executable_argument(tmp_path, monkeypatch):
    target = target_for(tmp_path)
    directory = tmp_path / "local tools with spaces"
    directory.mkdir()
    for name in ("ssh", "scp"):
        executable = directory / name
        executable.write_text("#!/bin/sh\nexit 1\n")
        executable.chmod(0o755)
        target.local[name] = str(executable)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "usage: scp [-346ABCOpqRrsTv] source target")
    monkeypatch.setattr(native_session.subprocess, "run", run)
    session = native_session.NativeSession(target, tmp_path / "known_hosts")
    session.run("true")
    assert calls[-1][0] == str(directory / "ssh")
    assert len(calls[-1]) > 1 and calls[-1][-1] == "true"
    session.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Native terminal authentication exercised in the disposable Linux container")
@pytest.mark.parametrize("method", ["password", "keyboard-interactive"])
def test_real_native_console_password_and_mfa(tmp_path, method):
    import pty
    import select
    import signal
    paramiko = pytest.importorskip("paramiko")
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(15)
    port = listener.getsockname()[1]
    known = tmp_path / "known_hosts"
    known.write_text(f"[127.0.0.1]:{port} {key.get_name()} {key.get_base64()}\n")
    password, otp = "  pasted test password  ", "124578"
    responses, errors = [], []
    class Server(paramiko.ServerInterface):
        def get_allowed_auths(self, username): return method
        def check_auth_password(self, username, answer):
            responses.append(answer)
            return paramiko.AUTH_SUCCESSFUL if answer == password else paramiko.AUTH_FAILED
        def check_auth_interactive(self, username, methods):
            return paramiko.InteractiveQuery("Company login", "", ("Company password: ", False), ("Verification code: ", False))
        def check_auth_interactive_response(self, answers):
            responses.extend(answers)
            return paramiko.AUTH_SUCCESSFUL if answers == [password, otp] else paramiko.AUTH_FAILED
        def check_channel_request(self, kind, channel_id):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        def check_channel_exec_request(self, channel, command):
            def answer():
                time.sleep(0.05)  # let the exec-request acknowledgement go first
                channel.sendall(b"native authentication worked")
                channel.send_exit_status(0)
                channel.shutdown_write()
                channel.close()
            threading.Thread(target=answer, daemon=True).start()
            return True
    def serve():
        transport = None
        try:
            connection, _ = listener.accept()
            transport = paramiko.Transport(connection)
            transport.add_server_key(key)
            transport.start_server(server=Server())
            channel = transport.accept(15)
            if channel is None: raise RuntimeError("Native client did not open a command channel")
            while transport.is_active(): time.sleep(0.01)
        except Exception as error:
            errors.append(error)
        finally:
            if transport is not None: transport.close()
    program = tmp_path / "native_console.py"
    program.write_text(f'''import sys
sys.path.insert(0, {str(client_cli.ROOT / 'tools')!r})
from deploylib import Target
from native_session import NativeSession
t=Target("uat", {{"host":"tester@127.0.0.1", "root":"/app", "ssh_port":{port}}})
t.local={{"ssh":{shutil.which('ssh')!r}, "scp":{shutil.which('scp')!r}}}
t.host_key_sha256=""
s=NativeSession(t, {str(known)!r})
r=s.run("test command", capture=True)
assert r.returncode == 0 and r.stdout == "native authentication worked"
s.close()
print("NATIVE_CONSOLE_SUCCESS", flush=True)
''')
    environment = {k: v for k, v in os.environ.items() if k not in {"DISPLAY", "WAYLAND_DISPLAY", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE"}}
    pid, terminal = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, str(program)], environment)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    output = bytearray()
    sent = set()
    ended = False
    prompts = [(b"password: ", password)] if method == "password" else [(b"Company password: ", password), (b"Verification code: ", otp)]
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if select.select([terminal], [], [], 0.1)[0]:
                try: block = os.read(terminal, 65536)
                except OSError: break
                if not block: break
                output.extend(block)
                for label, answer in prompts:
                    if label in output and label not in sent:
                        os.write(terminal, answer.encode() + b"\n")
                        sent.add(label)
            result, status = os.waitpid(pid, os.WNOHANG)
            if result:
                ended = True
                assert os.waitstatus_to_exitcode(status) == 0, output.decode(errors="replace")
                break
        if not ended:
            result, status = os.waitpid(pid, os.WNOHANG)
            if result:
                ended = True
                assert os.waitstatus_to_exitcode(status) == 0, output.decode(errors="replace")
        assert ended and b"NATIVE_CONSOLE_SUCCESS" in output, output.decode(errors="replace")
        assert responses == ([password] if method == "password" else [password, otp])
        assert password.encode() not in output and otp.encode() not in output
    finally:
        if not ended:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        os.close(terminal)
        listener.close()
        thread.join(timeout=2)
    assert not errors


@pytest.fixture
def real_sshd(tmp_path):
    """A private, loopback-only OpenSSH daemon inside the disposable Linux test container."""
    if sys.platform != "linux" or os.geteuid() != 0 or not shutil.which("sshd"):
        pytest.skip("Real sshd fixture requires the disposable Linux container running as root")
    keygen = shutil.which("ssh-keygen")
    host, identity = tmp_path / "host", tmp_path / "identity"
    for path in (host, identity):
        subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    authorized = tmp_path / "authorized_keys"
    authorized.write_bytes(Path(str(identity) + ".pub").read_bytes())
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    user = getpass.getuser()
    config = tmp_path / "sshd_config"
    config.write_text(f"Port {port}\nListenAddress 127.0.0.1\nHostKey {host}\nPidFile {tmp_path}/pid\n"
                      f"AuthorizedKeysFile {authorized}\nStrictModes no\nPermitRootLogin yes\n"
                      f"AllowUsers {user}\nPasswordAuthentication no\nChallengeResponseAuthentication no\n"
                      "UsePAM no\nMaxSessions 1\nLogLevel ERROR\n")
    Path("/run/sshd").mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen([shutil.which("sshd"), "-D", "-e", "-f", str(config)], stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while True:
            if server.poll() is not None:
                pytest.fail(server.stderr.read())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    pytest.fail("Private sshd did not start")
                time.sleep(0.05)
        target = target_for(tmp_path)
        target.host = f"{user}@127.0.0.1"
        target.ssh_port = port
        target.local["ssh_options"] = ["-i", str(identity), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes"]
        known_hosts = tmp_path / "known_hosts"
        public = Path(str(host) + ".pub").read_text().split()
        known_hosts.write_text(f"[127.0.0.1]:{port} {public[0]} {public[1]}\n")
        yield target, known_hosts
    finally:
        server.terminate()
        server.wait(timeout=5)
        server.stderr.close()


def test_real_native_scp_without_sftp_and_repeated_commands(real_sshd, tmp_path):
    target, known_hosts = real_sshd
    session = native_session.NativeSession(target, known_hosts)
    # SCP 8.0 in RHEL uses legacy SCP already, without accepting -O.
    assert session.scp_flags == []
    source = tmp_path / "source with spaces"
    payload = bytes(range(256)) * 12000
    source.write_bytes(payload)
    destination = tmp_path / "destination space'quote;dollar$"
    for _ in range(3):
        assert session.run("printf 'command works'", capture=True).stdout == "command works"
        assert session.put(source, str(destination)).returncode == 0
        assert destination.read_bytes() == payload
        assert destination.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.uploading-*"))
    source.write_bytes(b"")
    assert session.put(source, str(destination)).returncode == 0
    assert destination.read_bytes() == b""
    assert session.run("exit 23", capture=True).returncode == 23
    session.close()


@pytest.mark.parametrize("failure", ["scp", "checksum"])
def test_real_upload_failure_preserves_existing_destination(real_sshd, tmp_path, failure):
    target, known_hosts = real_sshd
    source = tmp_path / "source"
    source.write_bytes(b"new content")
    destination = tmp_path / "destination"
    destination.write_bytes(b"old content")
    wrapper = tmp_path / "scp wrapper"
    scp = target.local["scp"]
    # This executable is test-only: corrupt or fail the transfer, then run the
    # real SSH verification against the real private daemon.
    script = f"#!/bin/sh\nif test \"$#\" -eq 0; then exec {shlex.quote(scp)}; fi\n"
    script += "exit 7\n" if failure == "scp" else f"printf 'corrupt' > payload\nexec {shlex.quote(scp)} \"$@\"\n"
    wrapper.write_text(script)
    wrapper.chmod(0o755)
    target.local["scp"] = str(wrapper)
    session = native_session.NativeSession(target, known_hosts)
    assert session.put(source, str(destination)).returncode != 0
    assert destination.read_bytes() == b"old content"
    assert not list(tmp_path.glob("*.uploading-*"))
    session.close()


def test_real_native_pinned_host_key_and_changed_key_refusal(real_sshd, tmp_path):
    target, known_hosts = real_sshd
    key = known_hosts.read_text().split()[2]
    target.host_key_sha256 = "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(key)).digest()).decode().rstrip("=")
    session = native_session.NativeSession(target, known_hosts)
    assert session.run("true").returncode == 0
    temporary = Path(session.temporary.name)
    session.close()
    assert not temporary.exists()
    # Replace the trusted entry while the actual server retains its old key.
    keygen = shutil.which("ssh-keygen")
    other = tmp_path / "other"
    subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
    fields = Path(str(other) + ".pub").read_text().split()
    known_hosts.write_text(f"[127.0.0.1]:{target.ssh_port} {fields[0]} {fields[1]}\n")
    target.host_key_sha256 = ""
    session = native_session.NativeSession(target, known_hosts)
    assert session.run("true", capture=True).returncode == 255
    session.close()
