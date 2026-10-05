"""Native OpenSSH/SCP with normal terminal authentication and verified uploads."""

import base64
import hashlib
import hmac
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid


def _binary(value, name):
    value = str(value).strip()
    path = Path(value).expanduser()
    if path.is_absolute():
        found = str(path) if path.is_file() else None
    elif "/" in value or "\\" in value:
        raise ValueError(f"[local] {name} must be an absolute executable path or a name on PATH")
    else:
        found = shutil.which(value)
    if not found or not os.access(found, os.X_OK):
        raise ValueError(f"[local] {name} executable not found or not executable: {value}")
    return str(Path(found).resolve())


def validate_local(target):
    local = target.local
    protocol = local.get("scp_protocol", "scp")
    if protocol not in {"scp", "sftp"}:
        raise ValueError("[local] scp_protocol must be scp or sftp")
    options = local.get("ssh_options", [])
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise ValueError("[local] ssh_options must be an array of strings")
    return (_binary(local.get("ssh", "ssh"), "ssh"),
            _binary(local.get("scp", "scp"), "scp"), protocol)


def _host_matches(pattern, hostname):
    if pattern.startswith("|1|"):
        try:
            _, version, salt, digest = pattern.split("|")
            actual = hmac.new(base64.b64decode(salt, validate=True), hostname.encode(), hashlib.sha1).digest()
            return hmac.compare_digest(actual, base64.b64decode(digest, validate=True))
        except ValueError:
            return False
    return pattern == hostname


def _pinned_keys(known_hosts, hostname, expected):
    accepted = []
    for line in known_hosts.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        revoked = bool(fields and fields[0] == "@revoked")
        if revoked:
            fields = fields[1:]
        if len(fields) < 3 or fields[0].startswith(("#", "@")):
            continue
        if not any(_host_matches(host, hostname) for host in fields[0].split(",")):
            continue
        try:
            digest = hashlib.sha256(base64.b64decode(fields[2], validate=True)).digest()
        except ValueError:
            continue
        actual = "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
        if hmac.compare_digest(actual, expected):
            if revoked:
                raise ValueError("The pinned server key is marked @revoked in known_hosts")
            accepted.append(f"{hostname} {fields[1]} {fields[2]}\n")
    if not accepted:
        raise ValueError("host_key_sha256 has no matching server key in [local] known_hosts. "
                         "Have IT provision the matching OpenSSH known_hosts entry before deploying, "
                         "or remove the fingerprint and verify OpenSSH's first-connection prompt.")
    return "".join(accepted)


class NativeSession:
    def __init__(self, target, known_hosts):
        self.target = target
        self.ssh, self.scp, self.protocol = validate_local(target)
        self.temporary = None
        known_hosts = Path(known_hosts).expanduser().resolve()
        known_hosts.parent.mkdir(parents=True, exist_ok=True)
        if not known_hosts.exists():
            known_hosts.touch(mode=0o600)
        expected = target.host_key_sha256
        if expected:
            hostname = target.host.rsplit("@", 1)[1]
            if target.ssh_port != 22:
                hostname = f"[{hostname}]:{target.ssh_port}"
            keys = _pinned_keys(known_hosts, hostname, expected)
            self.temporary = tempfile.TemporaryDirectory(prefix="mds-ssh-trust-")
            known_hosts = Path(self.temporary.name) / "known_hosts"
            known_hosts.write_text(keys, encoding="utf-8")
            known_hosts.chmod(0o600)
        # OpenSSH parses spaces inside -o values too; quote the file path inside
        # the option. The space separator makes Windows quote the whole argument:
        # MSYS clients (Git for Windows, Cmder) misread an escaped quote in an
        # unquoted argument and swallow the following arguments. Managed options
        # come first (OpenSSH uses the first value).
        self.options = ["-o", f"Port={target.ssh_port}",
                        "-o", f'UserKnownHostsFile "{known_hosts.as_posix()}"',
                        "-o", "GlobalKnownHostsFile=none",
                        "-o", "StrictHostKeyChecking=" + ("yes" if expected else "ask"),
                        "-o", "ConnectTimeout=20",
                        "-o", "ServerAliveInterval=30",
                        *target.local.get("ssh_options", []), *target.ssh_options]
        self.scp_flags = []
        # Old OpenSSH already uses SCP and has no -O flag. New OpenSSH uses
        # SFTP by default and advertises -O in its usage. No server contact.
        usage = subprocess.run([self.scp], capture_output=True, text=True)
        groups = re.findall(r"\[-([A-Za-z0-9]+)\]", usage.stderr or "")
        if not groups:
            self.close()
            raise ValueError("Could not read OpenSSH scp usage; configure an OpenSSH-compatible scp binary")
        modern = any("O" in group for group in groups)
        if self.protocol == "scp" and modern:
            self.scp_flags = ["-O"]
        elif self.protocol == "sftp" and modern:
            self.scp_flags = ["-s"]
        elif self.protocol == "sftp" and not modern:
            self.close()
            raise ValueError("scp_protocol = 'sftp' requires a modern OpenSSH scp client; use 'scp' with this version")
        print("Using native SSH/SCP. Answer your SSH client's console prompts; "
              "separate connections may prompt again unless your SSH agent/config handles login.", flush=True)

    def run(self, command, capture=False):
        # Never capture stderr: native password/MFA/host-trust prompts must stay
        # visible. SSH reads credentials from the terminal, not command stdin.
        return subprocess.run([self.ssh, *self.options, "-n", "-T", self.target.host, command],
                              stdout=subprocess.PIPE if capture else None,
                              text=True, encoding="utf-8", errors="replace")

    def put(self, local, remote_path):
        local = Path(local).resolve()
        if not remote_path.startswith("/") or any(c in remote_path for c in "\n\r\0"):
            raise ValueError("SCP destination must be an absolute server path without control characters")
        temporary = remote_path + ".uploading-" + uuid.uuid4().hex
        with local.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        size = local.stat().st_size
        # A private local copy supplies mode 0600 in the SCP protocol. Copying
        # also avoids drive-letter host parsing and uploads one immutable file.
        with tempfile.TemporaryDirectory(prefix="mds-scp-") as directory:
            staged = Path(directory) / "payload"
            with staged.open("xb") as outgoing, local.open("rb") as incoming:
                staged.chmod(0o600)
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            destination = shlex.quote(temporary) if self.protocol == "scp" else temporary
            result = subprocess.run([self.scp, *self.scp_flags, "-S", self.ssh, *self.options,
                                     "payload", f"{self.target.host}:{destination}"],
                                    cwd=directory, text=True)
        if result.returncode == 0:
            script = ('set -eu; '
                      'test "$(wc -c < "$1")" -eq "$3"; '
                      'test "$(sha256sum "$1" | cut -d\' \' -f1)" = "$4"; '
                      'chmod 600 "$1"; mv -f -- "$1" "$2"')
            result = self.run(" ".join(shlex.quote(a) for a in
                                     [self.target.bash, "-c", script, "upload", temporary, remote_path, str(size), digest]))
        if result.returncode:
            # Do not turn failure into success or replace the old destination.
            # A refused cleanup leaves only a unique staging file.
            self.run("rm -f -- " + shlex.quote(temporary))
        return result

    def close(self):
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
