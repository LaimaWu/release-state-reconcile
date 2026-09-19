# Security Policy

## Supported versions

Security fixes are provided for the latest released version.

| Version | Supported |
|---|---|
| 0.1.x | Yes |
| Earlier versions | No |

## Reporting a vulnerability

Please do not disclose a suspected vulnerability in a public issue. After the repository is published, use GitHub's private vulnerability reporting or security-advisory feature. Include the affected version, impact, reproduction steps, and any suggested mitigation.

Never include live GitHub tokens or other secrets in a report. The CLI is designed to use GitHub `GET` requests only; any path that could mutate remote state should be treated as a security-sensitive regression.
