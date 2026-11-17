import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import (
    ConfigurationError,
    default_config_path,
    load_config,
    save_initial_config,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.app = self.base / "app"
        self.work = self.base / "candidate"
        self.other = self.base / "other"
        for path in (self.app, self.work, self.other):
            path.mkdir()
        self.file = self.base / "settings" / "config.toml"
        self.cwd = self.base / "other"
        self.args = {
            "app_root": self.app,
            "config_path": self.file,
            "cwd": self.cwd,
            "environ": {},
            "interactive": False,
        }

    def test_explicit_override_then_env_then_config(self):
        self.file.parent.mkdir()
        self.file.write_text(
            f'schema_version = 1\n[storage]\ncandidate_root = "{self.work}"\n'
        )
        supplied = self.base / "cli"
        supplied.mkdir()
        cfg = load_config(**self.args, workspace=supplied)
        self.assertEqual(
            (cfg.workspace_root, cfg.workspace_source), (supplied, "argument")
        )
        cfg = load_config(
            **(self.args | {"environ": {"RESUME_AGENT_WORKSPACE": str(supplied)}})
        )
        self.assertEqual(
            (cfg.workspace_root, cfg.workspace_source), (supplied, "environment")
        )
        cfg = load_config(**self.args)
        self.assertEqual(
            (cfg.workspace_root, cfg.workspace_source), (self.work, "config")
        )

    def test_config_requires_absolute_root(self):
        self.file.parent.mkdir()
        self.file.write_text('schema_version=1\n[storage]\ncandidate_root="relative"\n')
        with self.assertRaisesRegex(ConfigurationError, "absolute"):
            load_config(**self.args)

    def test_recognized_cwd_only(self):
        with self.assertRaisesRegex(ConfigurationError, "No candidate workspace"):
            load_config(**self.args)
        (self.cwd / ".resume-agent-workspace").touch()
        cfg = load_config(**self.args)
        self.assertEqual((cfg.workspace_root, cfg.workspace_source), (self.cwd, "cwd"))

    def test_prompt_does_not_automatically_write(self):
        cfg = load_config(
            **(self.args | {"interactive": True}), input_fn=lambda _: str(self.work)
        )
        self.assertEqual(cfg.workspace_source, "prompt")
        self.assertFalse(self.file.exists())
        saved = save_initial_config(cfg)
        self.assertEqual(saved, self.file)
        self.assertIn(str(self.work), self.file.read_text())
        with self.assertRaisesRegex(ConfigurationError, "already exists"):
            save_initial_config(cfg)
        self.assertEqual(load_config(**self.args).workspace_root, self.work)

    def test_bad_config_never_silent_fallback(self):
        (self.cwd / ".resume-agent-workspace").touch()
        self.file.parent.mkdir()
        self.file.write_text(
            'schema_version = 1\n[storage]\ncandidate_root = "/invalid-no-such-workspace"\n'
        )
        with self.assertRaises(ConfigurationError):
            load_config(**self.args)

    def test_relative_override_is_relative_to_launch_dir(self):
        cfg = load_config(**self.args, workspace=".")
        self.assertEqual(cfg.workspace_root, self.cwd)

    def test_no_prompt_when_behavior_disables(self):
        self.file.parent.mkdir()
        self.file.write_text(
            "schema_version=1\n[behavior]\nprompt_if_missing = false\n"
        )
        with self.assertRaisesRegex(ConfigurationError, "No candidate workspace"):
            load_config(
                **(self.args | {"interactive": True}), input_fn=lambda _: str(self.work)
            )

    def test_xdg_config_path(self):
        value = default_config_path(environ={"XDG_CONFIG_HOME": str(self.base)})
        self.assertEqual(value, self.base / "resume-agent/config.toml")
        with self.assertRaisesRegex(ConfigurationError, "absolute"):
            default_config_path(environ={"XDG_CONFIG_HOME": "relative/path"})


if __name__ == "__main__":
    unittest.main()
