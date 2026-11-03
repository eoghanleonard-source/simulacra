# Contributing

Use a branch named `codex/<short-description>` for Codex changes, or another descriptive branch for human contributions. Open a pull request into `main` with the concrete problem, resulting behaviour and checks performed. Run the offline CI before merging and wait for the GitHub check to pass. Branch protection is an account setting and is not configured by this document.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install pytest==8.1.1
make check PYTHON=python
```

Install `.[dev]` when working on live CLI dependencies. Use `make demo` to exercise the fictional offline lifecycle. Add tests for a demonstrated failure or uncovered requirement, and keep unrelated refactoring out of the change.

Never commit credentials, private keys, real transcripts, client identifiers, private evaluation references or generated delivery archives. Check the staged diff before committing; ignore rules do not protect already tracked files. Use fictional fixtures for public reproductions. Keep credentials in local environment configuration.

The engine source ZIP is allowlisted in `scripts/engine/package_release.py`. Update that list when a change needs new files at runtime or in the delivered documentation. Check that the packaged source remains reproducible.

A software change and an improved extraction result are different claims. For extraction changes, record the source sample, denominator, exposed versus untouched status, model/configuration, cost where available, regressions and rollback. Preserve rejected results. Do not infer content accuracy from valid JSON, successful export or a green CI run.

Write documentation for an informed reader who has not read the code: explain the action and consequence first, preserve important numbers, and distinguish confirmed findings from suspected causes. Explain new technical terms and prefer a short example over compressed shorthand.
