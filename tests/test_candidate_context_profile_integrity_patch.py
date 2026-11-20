"""Offline regression checks for candidate context and profile integrity repair.

Run:
    python3 -m unittest discover -s tests -p 'test_candidate_context_profile_integrity_patch.py' -v
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from profile_content_quality import local_date_iso
from profile_receipts import ProfileReceiptRegistry
from profile_review import review_profile_action
import utils


ROOT = Path(__file__).resolve().parents[1]


class CandidateContextGuidanceTests(unittest.TestCase):
    def test_other_four_system_prompts_have_role_specific_context_guidance(self):
        for role in ('orchestrator', 'application', 'recruiting', 'technical'):
            with self.subTest(role=role):
                content = utils.load_system_prompt(role)
                for phrase in ('CTX-', 'disclos', 'do_not_use', 'Candidate Profile Development Agent'):
                    self.assertIn(phrase, content)

    def test_memory_files_explain_ctx_consent_and_read_only_boundaries(self):
        for role in ('shared', 'orchestrator', 'application-agent',
                     'recruiting-ats-review-agent', 'technical-review-agent'):
            with self.subTest(role=role):
                content = (ROOT / 'agents' / role / 'AGENTS.md').read_text(encoding='utf-8')
                self.assertIn('CTX-', content)
                self.assertIn('disclos', content.lower())
                self.assertIn('do_not_use', content)
                if role in ('application-agent', 'recruiting-ats-review-agent', 'technical-review-agent'):
                    self.assertIn('read-only', content.lower())

    def test_runtime_candidate_date_changes_without_rewriting_prompt(self):
        path = ROOT / 'prompts' / 'candidate_system_prompt.md'
        original = path.read_bytes()
        with patch('profile_content_quality.local_date_iso', return_value='2031-01-09'):
            one = utils.load_system_prompt('candidate')
        with patch('profile_content_quality.local_date_iso', return_value='2031-01-10'):
            two = utils.load_system_prompt('candidate')
        self.assertIn("Today's local date: 2031-01-09", one)
        self.assertIn("Today's local date: 2031-01-10", two)
        self.assertIn('Never write bracketed placeholders', two)
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn("Today's local date:", utils.load_system_prompt('application'))


class CandidateProfileGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.profile = Path(self.directory.name) / 'candidate' / 'profile'
        self.profile.mkdir(parents=True)
        self.path = self.profile / 'PROFILE.md'
        self.path.write_text('Header\nLast Updated: [Current Date]\n', encoding='utf-8')
        self.today = local_date_iso()

    def change(self, value):
        return {'name': 'edit_file', 'args': {
            'file_path': str(self.path),
            'old_string': 'Last Updated: [Current Date]',
            'new_string': value,
        }}

    def test_bad_date_blocked_before_candidate_content_approval(self):
        prompts = []
        result = review_profile_action(
            self.change('Last Updated: [YYYY-MM-DD]'),
            profile_prefix=str(self.profile),
            allowed_decisions=['approve', 'edit', 'reject'],
            input_fn=lambda question: prompts.append(question) or 'n',
            output=lambda _message: None,
        )
        self.assertEqual(result['type'], 'reject')
        self.assertFalse(any(question.startswith('Candidate content:') for question in prompts))
        self.assertFalse(any(question.startswith('Authorize this EXACT') for question in prompts))
        self.assertIn('[Current Date]', self.path.read_text())

    def test_candidate_revision_must_fix_placeholder_before_approval(self):
        responses = iter(['v', 'a', 'y'])
        result = review_profile_action(
            self.change('Last Updated: [YYYY-MM-DD]'),
            profile_prefix=str(self.profile),
            allowed_decisions=['approve', 'edit', 'reject'],
            revise_fn=lambda _old: 'Last Updated: ' + self.today,
            input_fn=lambda _question: next(responses),
            output=lambda _message: None,
        )
        self.assertEqual(result['type'], 'edit')
        self.assertEqual(result['edited_action']['args']['new_string'], 'Last Updated: ' + self.today)
        self.assertIn('[Current Date]', self.path.read_text())

    def test_context_json_validation_still_blocks_before_approval_and_receipt(self):
        # Preserve the CTX schema protections introduced after the older patch.
        from profile_review import ReviewError

        context_dir = self.profile.parent / "context"
        context_dir.mkdir()
        target = context_dir / "CTX-0001.json"
        action = {
            "name": "write_file",
            "args": {"file_path": str(target), "content": "{invalid-json"},
        }
        with self.assertRaisesRegex(ReviewError, "Invalid candidate-context"):
            review_profile_action(
                action,
                profile_prefix=str(self.profile.parent),
                allowed_decisions=["approve", "edit", "reject"],
                input_fn=lambda _question: self.fail("Approval must not be requested"),
                output=lambda _message: None,
            )
        receipts = ProfileReceiptRegistry()
        receipts.begin_turn("demo")
        with self.assertRaisesRegex(ValueError, "Invalid candidate-context write"):
            receipts.record_authorized(
                "demo", action, {"type": "approve"},
                profile_prefix=str(self.profile.parent),
            )
        self.assertEqual(receipts.unreported("demo"), 0)
        self.assertFalse(target.exists())

    def test_receipt_registry_blocks_bypassed_human_review(self):
        receipts = ProfileReceiptRegistry()
        receipts.begin_turn('demo')
        with self.assertRaisesRegex(ValueError, 'Authoritative profile content check failed'):
            receipts.record_authorized('demo', self.change('Last Updated: [YYYY-MM-DD]'),
                                      {'type': 'approve'}, profile_prefix=str(self.profile))
        self.assertEqual(receipts.unreported('demo'), 0)

    def test_receipt_rechecks_edited_action(self):
        receipts = ProfileReceiptRegistry()
        receipts.begin_turn('demo')
        original = self.change('Last Updated: ' + self.today)
        changed = self.change('Last Updated: [YYYY-MM-DD]')
        with self.assertRaisesRegex(ValueError, 'Authoritative profile content check failed'):
            receipts.record_authorized('demo', original, {'type': 'edit', 'edited_action': changed},
                                      profile_prefix=str(self.profile))
        self.assertEqual(receipts.unreported('demo'), 0)

    def test_existing_unrelated_placeholder_does_not_block_valid_insert(self):
        self.path.write_text('Last Updated: [Current Date]\nWork: Python\n', encoding='utf-8')
        action = {'name': 'edit_file', 'args': {
            'file_path': str(self.path),
            'old_string': 'Work: Python',
            'new_string': 'Work: Python\nTests: unittest',
        }}
        receipts = ProfileReceiptRegistry()
        receipts.begin_turn('demo')
        receipts.record_authorized('demo', action, {'type': 'approve'}, profile_prefix=str(self.profile))
        self.assertEqual(receipts.unreported('demo'), 1)


if __name__ == '__main__':
    unittest.main()
