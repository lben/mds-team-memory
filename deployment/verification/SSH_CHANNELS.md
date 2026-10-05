# SSH channel and optional-SFTP handling

The former client opened and retained SFTP immediately after login. This made
command-only operations depend on SFTP and consumed a session channel while
Update tried to open another for its first command. Either a disabled SFTP
subsystem or a gateway's one-channel limit could stop an authenticated login.

SFTP now opens only for an upload and closes before executing a command. If
SFTP cannot open, uploads use a binary SSH command stream without a PTY. The
remote shell writes a unique temporary file, verifies length and SHA256, sets
mode 0600 and atomically renames it. Corrupt/incomplete uploads leave the prior
destination intact. Shell arguments are quoted. One login is reused throughout.
The upload sender retains stdin until completion and drains output concurrently
to avoid premature EOF and SSH-window backpressure.

The client prints successful authentication separately. Command-channel refusal
now identifies that stage and directs the operator to the deployment host/port
and gateway/command permissions. A server refusing all command sessions still
needs the appropriate route/access; this change cannot repair its policy.

Verification:

- 68 focused SSH/Update/Build/TestData tests passed on Red Hat UBI 8.10.
- 19 actual encrypted SSH/SFTP transport tests passed locally, including all
  existing password/PAM/MFA modes and the new channel cases.
- A server enforcing one session channel accepted alternating SFTP uploads and
  commands. Command-only operations made no SFTP request.
- A server rejecting SFTP accepted binary SSH uploads, including a file larger
  than the SSH window, empty files and all byte values. Repeated uploads reused
  one password entry and attempted SFTP only once.
- Corrupted and truncated streams failed verification, cleaned temporary files
  and preserved the previous destination. Filenames containing spaces, quotes,
  semicolons and shell metacharacters uploaded literally with mode 0600.
- A server refusing all channels produced the explicit command-stage error.

The full local packaging suite encountered the workstation's existing free-space
reserve limit (less than 2 GiB free); its complete focused suite passed on the
Red Hat test filesystem. No reserve guard was disabled. Actual UAT/PROD and the
company gateway were not contacted. The user's exact failure cause remains to be
confirmed from the next console output; the tests establish support for these
channel policies, not access to that server.

## Releasing SFTP before the next command

A real OpenSSH server with `MaxSessions 1` refused the first command after an
SFTP upload: `ChannelException(2, 'Connect failed')`, logged as `open failed`.
sshd frees an SFTP session only after its sftp-server process exits, and the
client closed SFTP and opened the next channel before that happened. The
in-process test server released the slot at channel close, so it missed this.

The client now sends EOF to SFTP and waits for sftp-server's exit status before
closing it, as it already does for commands. The test server now holds an SFTP
session until its handler has ended, like sshd.

Verification:

- The existing one-channel test failed with the same refusal before the client
  change and passed after it; the focused SSH/Update/native suites passed.
- Against a real loopback OpenSSH 10.3 server (`MaxSessions 1`, SFTP enabled),
  rounds of command, command, SFTP upload, command failed 20 of 20 before the
  change and passed 100 of 100 after it.
