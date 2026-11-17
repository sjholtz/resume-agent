"""Optional actual Deep Agents graph-construction smoke test.

Run ONLY in the project's uv environment; it does not invoke a model:
    RUN_RESUME_AGENT_INTEGRATION=1 uv run python -m unittest \
        discover -s tests -p test_graph_runtime_optional.py -v
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(
    os.getenv("RUN_RESUME_AGENT_INTEGRATION") == "1",
    "Set RUN_RESUME_AGENT_INTEGRATION=1 to test actual installed Deep Agents",
)
class ActualRuntimeSmokeTests(unittest.TestCase):
    def test_graph_compiles_with_real_middleware(self):
        from agent import make_graph

        with tempfile.TemporaryDirectory() as td:
            with patch("pathlib.Path.cwd", return_value=Path(td)):
                graph = make_graph()
        self.assertTrue(callable(getattr(graph, "invoke", None)))


if __name__ == "__main__":
    unittest.main()
