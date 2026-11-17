# Running these Tests

The second command breakdown:
1. Must run under `uv` so that the `trafilatura` package is available.
2. `-m` locate and import argument `unittest` and `discover` says the
   automatically discover the tests in the directory provided by `-s
   tests` (the `tests` directory—the `-s` is the option that specifies
   which directory to “s“tart in (defaults to `.`))
3. `-v` says “verbose output”

From the project root:

```sh
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q agent.py subagents.py permissions.py cli.py main.py models.py utils.py

# Optional live Deep Agents/Middleware graph construction (NO model call):
RUN_RESUME_AGENT_INTEGRATION=1 uv run python -m unittest discover -s tests -p test_graph_runtime_optional.py -v
```

The second command breakdown:
1. Must run under `uv` so that the `trafilatura` package is available.
2. `-m` locate and import argument `unittest` and `discover` says the
   automatically discover the tests in the directory provided by `-s
   tests` (the `tests` directory—the `-s` is the option that specifies
   which directory to “s“tart in (defaults to `.`))
3. `-v` says “verbose output”

## Live smoke-test checklist

1. Launch the Guix container in a **disposable** `/workspace` with
   `/app` read-only.
2. Set the actual profile prefix (e.g. `/workspace/candidate`) and
   confirm profile/FUP locations.
3. Run `main.py --message 'Describe your roles without modifying
   files.'` and confirm the model responds.
4. Test file reads from `/workspace` and `/system` **without** HITL
   prompts.
5. Request an application draft write, verify HITL pause, **reject**
   it, and verify no persistent file changed. Repeat and approve it.
6. Ask a reviewer to edit a file and verify the operation is
   **denied**, not offered for approval.
7. Ask the application agent or orchestrator to modify a profile
   record and verify it is **denied**.
8. Ask the profile agent to propose an authorized profile
   modification; verify both candidate-content consent and tool HITL
   review in CLI. Reject one stage to ensure nothing is written.
9. Test an external research tool and verify HITL approval; check
   proxy and TLS behavior against your deployed `web_fetch.py`.
10. Repeat with `--trace` and verify traces appear only when
    enabled. Protect sensitive data.

`InMemorySaver` is intentionally retained for the initial CLI;
interrupted runs **cannot** resume after the process exits. A durable
checkpointer and a fully developed browser UI remain future work.
