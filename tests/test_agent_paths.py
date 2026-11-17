"""Ensure all configured memory, skills and prompts actually exist."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils


class AgentPathTests(unittest.TestCase):
    def test_memories_exist_under_composite_route(self):
        for role in (
            "orchestrator",
            "candidate",
            "application",
            "recruiting",
            "technical",
        ):
            sources = utils.get_memory_sources(role)
            self.assertEqual(len(sources), 2)
            for source in sources:
                self.assertTrue(source.startswith("/system/"))
                self.assertTrue(
                    (utils.AGENT_HOME / source.removeprefix("/system/")).is_file(),
                    source,
                )

    def test_skills_directories_exist(self):
        for role in (
            "orchestrator",
            "candidate",
            "application",
            "recruiting",
            "technical",
        ):
            sources = utils.get_skill_sources(role)
            for source in sources:
                path = utils.AGENT_HOME / source.removeprefix("/system/")
                self.assertTrue(path.is_dir(), str(path))
                self.assertTrue(list(path.glob("*/SKILL.md")), str(path))

    def test_prompts_exist_and_load(self):
        for role in (
            "orchestrator",
            "candidate",
            "application",
            "recruiting",
            "technical",
        ):
            self.assertTrue(utils.load_system_prompt(role).startswith("#"))

    def test_unknown_role_fails(self):
        for method in (
            utils.get_memory_sources,
            utils.get_skill_sources,
            utils.load_system_prompt,
        ):
            with self.assertRaises(ValueError):
                method("unknown")


if __name__ == "__main__":
    unittest.main()
