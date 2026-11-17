# Updated test_cli.py for CLI v3 / v4b

The reported four errors come from old unit-test fixtures, not evidence that
production approval handling is failing. The current CLI expects the
`review_configs` array matching `action_requests` and the profile review
requires a valid on-disk UTF-8 snapshot for edit/write reviews.

The replacement tests:
- Include `review_configs` with matching `action_name` and decisions.
- Use temporary files for genuine profile-review tests (without executing writes).
- Use `r` to reject content. `n` now asks for a new proposal; the test also
  checks that distinct behavior.
- Account for two profile approval stages: `a` content approval, then `y`
  or `n` for execution.
- Stub AIMessage and ToolMessage in the fake LangChain module used by the
  `main()` tracing test. Production code imports the real classes.
- Test that missing/mismatched review_configs fails closed.

## Install

Run these steps from the agent source tree. This only replaces test code.

```sh
cd ~/bin/resume-agent
cp tests/test_cli.py tests/test_cli.py.bak
# Copy tests/test_cli.py from the extracted ZIP over the existing tests/test_cli.py.
python3 -m unittest tests.test_cli -v
# Or run your full project test command.
```

The ZIP also contains `test_cli.patch`, a unified patch against the original
`resume-agent-tracing-permissions-update.zip` test_cli.py. Because your local
tests may differ, inspect the diff and prefer the replacement test file if
`patch --dry-run -p1` reports conflicts.

## Verification

Nine tests passed in an isolated Python 3.13 run using the available v4b
source and earlier review/registry modules. This has not been tested against
your full 77-test local suite. No production permission validation or HITL
checks were relaxed.
