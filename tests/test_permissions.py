"""Permission policy regression tests; no model or Deep Agents install needed."""

import re
import sys
from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import make_permissions, profile_prefix


class FakePermission:
    def __init__(self, *, operations, paths, mode="allow"):
        self.operations = operations
        self.paths = paths
        self.mode = mode


def glob_matches(pattern, path):
    """Limited wcmatch/GLOBSTAR matching for permission regression cases."""
    pieces = []
    i = 0
    while i < len(pattern):
        if pattern[i : i + 2] == "**":
            pieces.append(".*")
            i += 2
        elif pattern[i] == "*":
            pieces.append("[^/]*")
            i += 1
        elif pattern[i : i + 5] == "[0-9]":
            pieces.append("[0-9]")
            i += 5
        else:
            pieces.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(pieces), path) is not None


def evaluate(rules, path, operation="write"):
    for rule in rules:
        if operation in rule.operations and any(glob_matches(glob, path) for glob in rule.paths):
            return rule.mode
    return "allow"


class PermissionsTests(unittest.TestCase):
    def setUp(self):
        module = ModuleType("deepagents")
        module.FilesystemPermission = FakePermission
        self.stub = patch.dict(sys.modules, {"deepagents": module})
        self.stub.start()
        self.addCleanup(self.stub.stop)

    def test_no_read_requires_hitl(self):
        for role in ["orchestrator", "candidate-profile-agent", "application-agent",
                     "technical-review-agent", "recruiting-ats-review-agent"]:
            self.assertEqual(evaluate(make_permissions(role), "/workspace/candidate/a.md", "read"), "allow")
            self.assertTrue(all("read" not in p.operations for p in make_permissions(role)))

    def test_orchestrator_and_application_profile_read_only(self):
        for role in ("orchestrator", "application-agent"):
            rules = make_permissions(role)
            self.assertEqual(evaluate(rules, "/workspace/candidate/fups.md"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/resume.tex"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/resume.tex"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/templates/resume_template.tex"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/candidate/base-documents/backend.tex"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/resume.tex"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/resume.tex"), "interrupt")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/.latex_source_manifest.json"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/claim_audit.md"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/build/claim_audit.json"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/build/validation.json"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/build/cover_letter.pdf"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/job_description.md"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/additional_inf.md"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/company-a/2026-11-17-ai_engineer/qualification_matrix.md"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/qualification_matrix.md"), "interrupt")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/2026-11-17-ai_engineer/reviews/technical.md"), "interrupt")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/company_inf.md"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/additional_inf.md"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/custom.md"), "deny")
            self.assertEqual(evaluate(rules, "/workspace/applications/acme/invalid_date/resume.tex"), "deny")
            self.assertEqual(evaluate(rules, "/system/agents/shared/AGENTS.md"), "deny")
            self.assertEqual(evaluate(rules, "/scratch/notes.md"), "allow")

    def test_candidate_developer_can_write_profile_with_approval_only(self):
        rules = make_permissions("candidate-profile-agent")
        self.assertEqual(evaluate(rules, "/workspace/candidate/profile.md"), "interrupt")
        self.assertEqual(evaluate(rules, "/workspace/candidate/FUP-001.md"), "interrupt")
        self.assertEqual(evaluate(rules, "/workspace/candidate/base-documents/resume.tex"), "deny")
        self.assertEqual(evaluate(rules, "/workspace/research/sources.md"), "interrupt")
        self.assertEqual(evaluate(rules, "/workspace/applications/draft.md"), "deny")
        self.assertEqual(evaluate(rules, "/workspace/other.md"), "deny")

    def test_reviewers_cannot_write_workspace(self):
        for role in ("technical-review-agent", "recruiting-ats-review-agent"):
            rules = make_permissions(role)
            for path in ("/workspace", "/workspace/candidate/profile.md",
                         "/workspace/applications/resume.md", "/system/skills/any.md"):
                self.assertEqual(evaluate(rules, path), "deny")

    def test_custom_profile_prefix_changes_protection(self):
        rules = make_permissions("application-agent", candidate_profile="/workspace/profiles/primary")
        self.assertEqual(evaluate(rules, "/workspace/profiles/primary/history.md"), "deny")
        self.assertEqual(evaluate(rules, "/workspace/applications/draft.md"), "deny")
        owner = make_permissions("candidate-profile-agent", candidate_profile="/workspace/profiles/primary")
        self.assertEqual(evaluate(owner, "/workspace/profiles/primary/history.md"), "interrupt")
        self.assertEqual(evaluate(owner, "/workspace/candidate/other.md"), "deny")
        self.assertEqual(evaluate(owner, "/workspace/candidate/base-documents/resume.tex"), "deny")

    def test_invalid_prefixes_fail_closed(self):
        for value in ("/", "/system/agents", "/workspace", "/workspace/../system",
                      "relative", "/workspace/applications", "/workspace/research/notes",
                      "/workspace//candidate", "\\workspace\\candidate"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                profile_prefix(value)
        self.assertEqual(profile_prefix(), "/workspace/candidate")

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValueError):
            make_permissions("general-purpose")


if __name__ == "__main__":
    unittest.main()
