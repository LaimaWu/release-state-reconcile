# Contributing

Contributions that improve correctness, evidence calibration, portability, documentation, or tests are welcome.

## Development setup

Use Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

## Change guidelines

- Preserve the GET-only GitHub client and explicit `UNKNOWN` states.
- Add generic tests for reconciliation behavior; do not hard-code one candidate's expected answer into product logic.
- Keep repository-specific vocabulary in configuration.
- Do not interpret missing public evidence as proof of an absent change unless the report clearly states the bounded query that was completed.
- Keep pull requests focused and document user-visible behavior in the release notes when appropriate.

## Reporting bugs

Use the bug report template and include a minimal public example when possible. Remove tokens, private URLs, and confidential logs before posting.
