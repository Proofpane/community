# Proofpane Community

An agentic development environment, and the open packages it runs on, both open source under the Apache License 2.0
(`LICENSE`; `NOTICE` and each part's own `NOTICE` say what they carry from others).

- `open_edition/`: Proofpane Community. Its `README.md` says what it does, client by client, and how to run it.
- `oss/`: Proofpane's open packages, `proofpane-core`, `proofpane-verify`, `proofpane-sign` and Verify for pull
  requests (`oss/README.md`).

The checks `.github/workflows/ci.yml` runs, on Python 3.12 and Node 22:

    cd open_edition && pip install -r requirements-dev.txt && pytest tests -q
    python oss/run_tests.py
    cd open_edition/ui && npm ci && npm run typecheck && npm test
