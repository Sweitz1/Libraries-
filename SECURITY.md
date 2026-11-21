# Security

## Reporting a problem

Please report vulnerabilities privately: **[open a private advisory](https://github.com/Sweitz1/Libraries-/security/advisories/new)**. Don't open a public issue. Include the version or commit, what an attacker could do, and steps to reproduce. Never include real tokens or pairing links.

You should get a reply within a week. Fixes go into the latest release; older versions aren't patched.

## What counts

Libra runs on your own machine and network, so the boundaries that matter are:

- **The server's token check**, device tokens, pairing and lockout.
- **Web fetching** reaching private addresses (SSRF, DNS rebinding).
- **Trust rules**: a skill that changes something running without approval, or a deny rule being bypassed.
- **Outside content** (web pages, MCP/API output) driving a change without a person.
- **Secrets** appearing in logs or audit files.
- **The installer**, which runs as root.
- **The Android app**: its token storage and WebView.

Not in scope: plugins and MCP servers you install yourself (they run with your permissions by design), running with `--no-auth` or `--allow-private`, and anyone who already has your token.

## Automated checks

Every push runs [Stingward](https://github.com/HiveControlAgents/StingWard) in CI (`.github/workflows/test.yml`, job `stingward`). It checks the dependency against known vulnerabilities, and the code for secrets, injection, SSRF, and agent and MCP settings. It also audits every Claude Code skill in `.claude/skills/` for hidden instructions, credential theft and risky commands. The job fails on a high or critical finding (medium, for skills). Libra currently scans with no findings (grade A). A clean scan finds the common mistakes; it isn't a penetration test.

## Status

Libra has been through four external code reviews, answered point by point in [docs/REVIEW.md](docs/REVIEW.md) (sections 9–12). It has **not** had a professional security audit, and a real deployment on Debian with Tailscale, and the Android app on a device, are still unverified. Prefer the Tailscale (`https`) address over open ports.
