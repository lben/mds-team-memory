# Native SSH/SCP deployment verification

Update and TestData can use local OpenSSH binaries instead of Paramiko. Configure
`[local] transport = "openssh"`, `ssh` and `scp` in `tools/deploy.toml`; supplying
either binary path also selects native mode automatically. Executable paths are
passed as individual subprocess arguments, including paths with spaces.

Native authentication belongs to the installed client. Password, PAM/MFA and
host-key prompts remain on the console; no password is cached or written to
files, arguments or environment variables. Native mode uses separate SSH/SCP
connections and can prompt repeatedly without an agent or connection reuse.
Existing configurations continue to use Paramiko with one authenticated login.

Default `scp_protocol = "scp"` avoids SFTP. New clients receive `-O`; old clients
already default to SCP. `-S` makes SCP use the configured SSH binary. The server
must have `scp`. Optional `sftp` mode requires a modern local SCP client and a
working server subsystem. Uploads stage a private local copy and unique remote
temporary file; remote size and SHA256 checks precede chmod/atomic rename.
Failed transfers or checksum checks preserve the previous destination.

Managed host-key checking remains enabled. Native mode can reuse an existing
OpenSSH known_hosts file. A fingerprint pin requires a matching provisioned key;
only that key is trusted, and matching revoked entries are refused. Changed
server keys fail. The launchers choose native mode before resolving optional
dependencies, avoiding Paramiko downloads; local checks and dataset previews
also run with only the standard library.

## Focused checks

The focused suite covers native transport, existing Paramiko authentication,
Update, Build and TestData. Real native transport tests use an isolated OpenSSH
daemon on container loopback with SFTP unconfigured and MaxSessions=1. They
exercise repeated command/transfer cycles, binary data larger than an SSH window,
paths with spaces and shell punctuation, empty/failure cases, private file modes,
host pins and changed-key rejection. Protocol selection covers old and modern
client usage. Terminal tests use a real OpenSSH client, a private challenge
server and a PTY to check password and keyboard-interactive/MFA authentication,
including leading/trailing password spaces and hidden responses.

The focused Red Hat suite passed all 93 tests. The native module then passed
all 25 cases, including a final empty-file transfer check. Locally, its 19
portable cases passed; the four real-daemon and two terminal cases require Linux
and were exercised in the container. Existing focused tests also passed locally.

The Red Hat test image extends the existing UBI 8.10 test image with OpenSSH
8.0p1 clients/server. Tests use `--network none`, a read-only repository mount
and disposable containers. The workstation skips Linux daemon/terminal cases;
Windows path strings, executable arguments and launcher routing are checked
locally. No Windows/Cmder execution or company UAT/PROD access was performed.

## Full Update and TestData run

A separate disposable UBI 8.10 container ran a private OpenSSH daemon without
SFTP, allowing one channel per connection. The client ran from an isolated code
checkout, sharing deployment assets read-only. The SSH destination was the
ordinary `mds` user, with a unique root and app port. Key authentication kept the
full run unattended; password/MFA behavior was checked separately above.

The same uv invocation as the launchers passed `--check`, then completed a fresh
UAT installation in 218.5 seconds: bundled Python/uv, all 25 model/dependency
parts, verified joining/extraction, offline dependency setup, migration, web
health and active ML worker lease. TestData added a 1% expanded sample (3 scenarios,
15 posts), listed the batch and removed it successfully. A second Update passed
in 88.1 seconds, reused the verified model generation without transferring model
parts, and left both processes healthy. A final status check passed; both test
processes were stopped and the container removed. Total test time was 335.5
seconds. These loopback/container timings do not predict company network speed.

An earlier full run exhausted workstation disk during assembly and caused
Docker to stop itself. It was not counted as passing. Two temporary packaging
archives were removed only after verifying their exact SHA256/size matched
preserved transfer parts; source models and tracked deployment assets remained
intact. Docker was recovered and the original Postgres container was confirmed
healthy. The successful repeat shared source assets read-only and automatically
stopped its named test container if host free space fell below 2.5 GiB.
