import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import BackendPaths, build_backend
from config import AgentConfig


class FakeFilesystemBackend:
    def __init__(self, root_dir, virtual_mode):
        self.root_dir = root_dir
        self.virtual_mode = virtual_mode


class FakeStateBackend:
    pass


class FakeCompositeBackend:
    def __init__(self, default, routes):
        self.default = default
        self.routes = routes


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.app = root / "app"
        self.workspace = root / "workspace"
        self.app.mkdir()
        self.workspace.mkdir()
        self.config = AgentConfig(
            app_root=self.app,
            workspace_root=self.workspace,
            config_path=root / "config.toml",
            workspace_source="argument",
        )

    def test_backend_constructs_disjoint_routes(self):
        module = ModuleType("deepagents")
        backends = ModuleType("deepagents.backends")
        backends.CompositeBackend = FakeCompositeBackend
        backends.FilesystemBackend = FakeFilesystemBackend
        backends.StateBackend = FakeStateBackend
        with patch.dict(
            sys.modules,
            {
                "deepagents": module,
                "deepagents.backends": backends,
            },
        ):
            result = build_backend(self.config)
        self.assertIsInstance(result, FakeCompositeBackend)
        self.assertIsInstance(result.default, FakeStateBackend)
        self.assertEqual(set(result.routes), {"/system/", "/workspace/"})
        self.assertEqual(result.routes["/system/"].root_dir, self.app)
        self.assertEqual(result.routes["/workspace/"].root_dir, self.workspace)
        self.assertTrue(all(route.virtual_mode for route in result.routes.values()))

    def test_overlapping_roots_denied(self):
        nested = self.app / "candidate"
        nested.mkdir()
        cfg = AgentConfig(
            app_root=self.app,
            workspace_root=nested,
            config_path=self.config.config_path,
            workspace_source="argument",
        )
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            build_backend(cfg)

    def test_virtual_memory_paths(self):
        self.assertEqual(
            BackendPaths.agent_memory("technical_review"),
            "/system/agents/technical_review/AGENTS.md",
        )
        self.assertEqual(BackendPaths.shared_memory, "/system/agents/shared/AGENTS.md")
        with self.assertRaises(ValueError):
            BackendPaths.agent_memory("../secrets")


if __name__ == "__main__":
    unittest.main()
