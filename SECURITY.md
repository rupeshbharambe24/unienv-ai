# Security policy

UniEnv AI installs software and edits shell startup files and AI-tool instruction files, so security reports matter.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting: **Security → Report a vulnerability** on [rupeshbharambe24/unienv-ai](https://github.com/rupeshbharambe24/unienv-ai/security). We aim to acknowledge reports within 7 days, and to disclose within 90 days or once a fix ships.

## Design principles

- Recipes are **declarative** (no install scripts), so a recipe PR can only name packages, not run code.
- `unienv need` flags packages that are not in a curated recipe and logs every install to `~/.unienv/log/unienv.log`.
- Generated files never contain credentials. `unienv doctor` reports the **names** of exported secrets, never their values.
- No telemetry.
