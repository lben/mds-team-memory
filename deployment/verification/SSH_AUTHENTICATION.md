# Password and keyboard-interactive authentication

Update/TestData use an explicit Paramiko 4.0 authentication strategy instead of
the library's single-field automatic password fallback. That fallback can reject
a multi-field PAM query and report the original `BadAuthenticationType`, hiding
the actual challenge failure.

The client tries SSH password authentication without automatic fallback, then
uses keyboard-interactive when offered. It also handles a successful password
step followed by an interactive second factor. It reuses the initially entered
password once for a recognizable password challenge and asks for other server
responses in the console. Unknown, new-password and OTP/code prompts never
receive the cached password automatically. No agent/key discovery is attempted
by this strategy. An additional public-key requirement is still enforced.

Typing/pasting input is unchanged. Leading/trailing spaces and Unicode are
preserved. Passwords remain hidden; server non-echo challenges use hidden input.
Secrets are cleared from the authentication object when connection setup exits,
and are not stored in configuration, shell arguments or application logs.

Verification:

- 61 focused authentication/Update/Build/TestData tests passed locally and on
  Red Hat UBI 8.10.
- Real encrypted Paramiko SSH/SFTP connections over isolated socket pairs
  passed plain password, single password challenge, password + OTP in one query,
  informational/empty query followed by credentials, echoed username plus hidden
  credentials, and password authentication followed by a separate OTP step.
- Commands and atomic SFTP upload/rename worked after each authentication mode.
- Wrong passwords failed without extra retries/SFTP, and mismatched host keys
  failed before credentials were sent. Secrets were cleared on success/failure.
- Test passwords included Unicode and leading/trailing spaces. Prompt counts
  confirmed one initial password entry and a separate OTP only when required.

No actual UAT/PROD or company SSH server was contacted. These checks verify SSH
protocol behavior and shared client plumbing; the company's exact PAM/MFA and
public-key policies remain controlled by its server. Native Windows terminal
paste behavior was not available for testing; the standard hidden input function
is retained.
