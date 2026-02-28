# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest (`main`) | ✅ |
| older branches | ❌ |

We recommend always running the latest version from `main`.

## Reporting a Vulnerability

**Please do not report security issues via public GitHub Issues.**

If you discover a vulnerability, please disclose it responsibly:

1. **Open a private [GitHub Security Advisory](../../security/advisories/new)** — this is the preferred channel.
2. Alternatively, describe the issue in a direct message to the maintainer.

### What to include

- Description of the issue and its potential impact
- Steps to reproduce or a proof-of-concept (if available)
- Any suggested mitigations

### What to expect

- **Acknowledgement** within 48 hours
- **Assessment and timeline** communicated within 7 days
- Credit in the release notes (unless you prefer to remain anonymous)

## Scope

This project is a **self-hosted, single-user application** intended to run on a private home network. The following are considered in-scope:

- Authentication bypass
- Privilege escalation
- Data exfiltration via API
- Remote code execution through document ingestion
- Credential or secret leakage

Out-of-scope: issues that require physical access to the host machine, or are inherent limitations of the user's own network configuration.

## Security Design Notes

- All passwords are hashed with **bcrypt**
- Sessions use **JWT stored in HttpOnly, SameSite=Lax cookies**
- No cloud APIs are called — all inference runs locally via Ollama
- Gmail OAuth credentials are stored outside the container in a `secrets/` directory (never committed to git)
- The database is SQLite, accessible only from within the Docker network

For deployment hardening recommendations, see the **Security** section of the [README](README.md).
