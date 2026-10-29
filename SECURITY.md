# Security and privacy

Presence Guard runs on one trusted user's desktop. The local browser interface controls opt-in camera observation and a small fixed action set. It is not a hardened multi-user service or a replacement for Windows security policy.

## Boundaries

The default demo opens no camera and invokes no real desktop actions. `--camera` permits camera access only while active. Real desktop actions also require a corresponding launch flag and a local mode selection followed by Start. Stop disarms immediately. A capture that has not actually released prevents another camera session from starting.

The HTTP server binds only to `127.0.0.1`. It checks the exact Host and optional GET Origin; mutation requests require the exact Origin. Duplicate security and framing headers are rejected. Fetch Metadata rejects cross-site requests. JSON mutations require a random session token in the body, an exact field set, a fixed endpoint, and bounded input. No permissive CORS is enabled. A token is not an authentication boundary against other software running as the same desktop user.

Request lines, total headers, JSON bodies, connection count, and header/body receive time are bounded. Connections close after one request. Errors are fixed public messages; request content and tracebacks are not logged or reflected. Static assets are preloaded from an exact allowlist after checking for symbolic links and Windows reparse points. There is no general file-serving endpoint.

The application has no language model, prompt evaluation, shell executor, plug-in loader, user-supplied URL, face recognition, or remote camera mode. People counts are observations, not identities. Frames remain in memory. Events are a short, bounded in-memory list. Tokens and choices are not saved in browser storage.

Boss Alert uses the fixed synthetic workbook generator shared with Jarvis. A fresh per-process directory is created under `runtime/`, and each alert writes into a newly created subfolder. Linked paths and pre-existing alert folders are rejected. A spreadsheet is opened by its Windows file association, without a shell command. Other local software with the same user's permissions can still alter project files, race filesystem operations, inspect process memory, send requests, or interfere with the camera. Those capabilities are outside this application's security boundary.

The optional installer runs only from a local terminal after confirmation. It creates a project virtual environment, accepts one fixed OpenCV package pin, requests wheels from the official PyPI index, and checks dependency consistency. It does not provide a fully hashed dependency lock or a sandbox for third-party native code. Keep Python, the browser, Windows, and camera drivers updated. Do not run an untrusted checkout or a privileged instance.

## Failure behavior

After fresh frames stop for over 5 seconds, an active Privacy lock session requests a Windows lock even if no presence was established. Observe and Boss Alert modes do not lock. A stale camera is an unknown state and never proof of absence. Sustained observed absence only triggers after presence was established. Extra-person triggers are debounced by the shared policy.

Windows may reject a lock or workbook-open request. Failures are shown in the dashboard; a successful request does not verify the workstation is locked. The shared action latch prevents repeated attempts for the same condition, including after failure. Use the operating system's own auto-lock and inspect failures locally. A blocked native camera driver can delay cooperative shutdown; close the process if release does not finish. Closing the browser tab alone does not disarm the guard.

One separate worker handles real desktop requests without holding the policy lock. Stop and status remain responsive. Stop can cancel a request before the worker commits to it; after commitment, the request may still take effect. No new session can start until it finishes. Extra triggers are skipped visibly rather than queued, including if the outstanding request later fails. Shutdown disarms and uses one shared bounded wait for camera and action workers. It cannot terminate a blocked native call. Closed controllers reject restart.

## Reporting a vulnerability

Use [Security > Report a vulnerability](https://github.com/Jgolding1998/presence-guard/security/advisories/new) to send a private report. Do not submit vulnerability details through the public feedback form.

Include the affected version, platform, expected behavior and a synthetic reproduction. Leave out credentials, personal information and private files.
