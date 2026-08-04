# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities or paste API keys, `.env` contents, or database dumps into issues.

Instead:

1. Open a [private security advisory](https://github.com/zabrodschiipavel-sketch/notebookbuy/security/advisories/new) on GitHub, or
2. Contact the maintainer through GitHub with minimal details and request a secure channel.

Include steps to reproduce, impact, and affected version when possible.

## Secrets

- Never commit `.env` or `GEMINI_API_KEY` values.
- Rotate any API key that was accidentally exposed in git history or logs.
