# Security

This community tool is not an official OpenAI product. Never report secrets through public issues. Use GitHub's private vulnerability reporting when enabled; otherwise contact the repository owner privately and ask for a secure reporting channel before sending sensitive detail. Do not post auth files, logs containing private source, cookies or tokens.

The queue uses each user's Codex CLI authentication. It does not scrape ChatGPT, buy credits, consume reset credits or store tokens in settings. Prompts, worktrees and logs are private user data. Defaults live outside source checkout; private permissions are requested on POSIX and Windows filesystem ACLs remain the user's responsibility.

Real execution requires a config gate and explicit foreground/CLI action or separate native scheduler opt-in. Optional native integration verifies owned definitions and loaded origins; never use elevated privileges to bypass ownership/permission errors. New jobs use isolated worktrees. Session continuation reuses existing files and requires the previous owner to stop and attest exclusive ownership. Queue file locks do not lock out unrelated editors; App Server status is not a global process-ownership oracle. Resolve ambiguity before execution. A real resumed-session sandbox has not been inference-tested; validate it on a disposable project. Never bypass sandbox protections to get a session running.

Review logs and partial files after failure/interruption before retry. Credentials available to the OS account may be readable by Codex; use a dedicated constrained account/environment for stronger isolation. No sandbox is a substitute for reviewing third-party project instructions.

Publication checks must include both current tracked files and history. Do not rewrite history or rotate credentials automatically. If a credential is found, stop publication, privately report the location, revoke/rotate it with the owner and agree on history cleanup. A regex audit is not proof that all secrets are absent.
