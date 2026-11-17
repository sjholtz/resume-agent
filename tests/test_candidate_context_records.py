"""Candidate-context schema and disclosure-boundary regressions."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from candidate_context_records import (
    ContextRecordError,
    load_context_directory,
    main,
    validate_connection_record,
    validate_connections_file,
    validate_context_record,
)

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / "examples/candidate-context/CTX-0001.json").read_text())
CONNECTION = json.loads(
    (ROOT / "examples/candidate-context/CANDIDATE_CONNECTIONS.json").read_text()
)["connections"][0]


class ContextSchemaTests(unittest.TestCase):
    def test_synthetic_context_fixture_valid(self):
        validate_context_record(BASE, filename="CTX-0001.json")

    def test_bad_context_id_denied(self):
        ctx = {**BASE, "id": "CTX-pending"}
        with self.assertRaisesRegex(ContextRecordError, "id"):
            validate_context_record(ctx)

    def test_filename_disagreement_denied(self):
        with self.assertRaisesRegex(ContextRecordError, "filename"):
            validate_context_record(BASE, filename="CTX-0002.json")

    def test_missing_candidate_relationship_denied(self):
        ctx = {**BASE, "candidate_relationship": ""}
        with self.assertRaisesRegex(ContextRecordError, "candidate_relationship"):
            validate_context_record(ctx)

    def test_missing_disclosure_preference_denied(self):
        ctx = dict(BASE)
        del ctx["disclosure_preference"]
        with self.assertRaisesRegex(ContextRecordError, "disclosure_preference"):
            validate_context_record(ctx)

    def test_unknown_disclosure_state_denied(self):
        ctx = {**BASE, "disclosure_preference": "public"}
        with self.assertRaisesRegex(ContextRecordError, "disclosure_preference"):
            validate_context_record(ctx)

    def test_placeholder_in_candidate_data_denied(self):
        ctx = {**BASE, "description": "[Insert candidate hobby]"}
        with self.assertRaisesRegex(ContextRecordError, "placeholder"):
            validate_context_record(ctx)

    def test_related_profile_ids_validated(self):
        ctx = {**BASE, "related_records": ["EXP-0007", "FUP-SMOKE-001"]}
        validate_context_record(ctx)
        ctx["related_records"] = ["a-bad-id"]
        with self.assertRaisesRegex(ContextRecordError, "related_records"):
            validate_context_record(ctx)

    def test_context_directory_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "context"
            path.mkdir()
            source = root / "real.json"
            source.write_text(json.dumps(BASE))
            (path / "CTX-0001.json").symlink_to(source)
            with self.assertRaisesRegex(ContextRecordError, "regular context file"):
                load_context_directory(path)

    def test_context_directory_reads_valid_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CTX-0001.json").write_text(json.dumps(BASE))
            result = load_context_directory(Path(tmp))
            self.assertEqual(list(result), ["CTX-0001"])

    def test_invalid_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CTX-0001.json").write_text("{not-json")
            with self.assertRaisesRegex(ContextRecordError, "cannot parse"):
                load_context_directory(Path(tmp))


class ConnectionSchemaTests(unittest.TestCase):
    def setUp(self):
        self.contexts = {BASE["id"]: copy.deepcopy(BASE)}
        self.connection = copy.deepcopy(CONNECTION)

    def test_pending_is_valid_and_not_included(self):
        validate_connection_record(self.connection, self.contexts)
        self.assertEqual(self.connection["use_decision"], "consider")

    def test_pending_cannot_be_included(self):
        self.connection["use_decision"] = "include"
        with self.assertRaisesRegex(ContextRecordError, "include requires"):
            validate_connection_record(self.connection, self.contexts)

    def test_candidate_declines_personal_fact(self):
        self.connection.update(
            candidate_confirmation="candidate_declined", use_decision="consider"
        )
        with self.assertRaisesRegex(ContextRecordError, "declined candidate"):
            validate_connection_record(self.connection, self.contexts)

    def test_candidate_declines_disclosure(self):
        self.connection.update(
            disclosure_authorization="declined", use_decision="include"
        )
        with self.assertRaises(ContextRecordError):
            validate_connection_record(self.connection, self.contexts)

    def test_approved_connection_with_evidence_is_valid(self):
        self.connection.update(
            candidate_confirmation="candidate_confirmed",
            disclosure_authorization="approved",
            approval_reference="Candidate explicitly approved this job's cover-letter use in current session",
            use_decision="include",
        )
        validate_connection_record(self.connection, self.contexts)

    def test_approved_without_approval_reference_is_rejected(self):
        self.connection.update(
            candidate_confirmation="candidate_confirmed",
            disclosure_authorization="approved",
            use_decision="include",
        )
        with self.assertRaisesRegex(ContextRecordError, "approval_reference"):
            validate_connection_record(self.connection, self.contexts)

    def test_unverified_candidate_fact_is_not_suitable(self):
        self.connection.update(
            candidate_confirmation="candidate_confirmed",
            disclosure_authorization="approved",
            approval_reference="Direct candidate approval",
            use_decision="include",
        )
        self.contexts[BASE["id"]]["evidence_status"] = "unverified"
        with self.assertRaisesRegex(ContextRecordError, "unverified candidate fact"):
            validate_connection_record(self.connection, self.contexts)

    def test_do_not_use_blocks_even_with_claimed_approval(self):
        self.connection.update(
            candidate_confirmation="candidate_confirmed",
            disclosure_authorization="approved",
            approval_reference="Direct candidate approval",
            use_decision="include",
        )
        self.contexts[BASE["id"]]["disclosure_preference"] = "do_not_use"
        with self.assertRaisesRegex(ContextRecordError, "do_not_use"):
            validate_connection_record(self.connection, self.contexts)

    def test_do_not_use_cannot_even_be_considered(self):
        self.contexts[BASE["id"]]["disclosure_preference"] = "do_not_use"
        with self.assertRaisesRegex(ContextRecordError, "do_not_use"):
            validate_connection_record(self.connection, self.contexts)

    def test_consent_requires_confirmed_fact(self):
        self.connection.update(
            disclosure_authorization="approved", approval_reference="candidate said yes"
        )
        with self.assertRaisesRegex(ContextRecordError, "candidate-confirmed"):
            validate_connection_record(self.connection, self.contexts)

    def test_ghost_context_rejected(self):
        self.connection["context_id"] = "CTX-0999"
        with self.assertRaisesRegex(ContextRecordError, "no matching profile"):
            validate_connection_record(self.connection, self.contexts)

    def test_unattributed_company_claim_rejected(self):
        self.connection["company_source_urls"] = []
        with self.assertRaisesRegex(ContextRecordError, "company_source_urls"):
            validate_connection_record(self.connection, self.contexts)

    def test_bad_company_url_rejected(self):
        self.connection["company_source_urls"] = ["file:///etc/passwd"]
        with self.assertRaisesRegex(ContextRecordError, "company_source_urls"):
            validate_connection_record(self.connection, self.contexts)

    def test_approved_reference_not_allowed_when_denied(self):
        self.connection["approval_reference"] = "candidate said yes"
        with self.assertRaisesRegex(ContextRecordError, "cannot exist"):
            validate_connection_record(self.connection, self.contexts)

    def test_duplicate_connection_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "CANDIDATE_CONNECTIONS.json"
            file.write_text(
                json.dumps({"connections": [self.connection, self.connection]})
            )
            with self.assertRaisesRegex(ContextRecordError, "duplicate connection"):
                validate_connections_file(file, self.contexts)

    def test_validator_cli_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "candidate" / "context"
            directory.mkdir(parents=True)
            (directory / "CTX-0001.json").write_text(json.dumps(BASE))
            file = Path(tmp) / "applications" / "test" / "CANDIDATE_CONNECTIONS.json"
            file.parent.mkdir(parents=True)
            file.write_text(json.dumps({"connections": [self.connection]}))
            before_ctx, before_conn = (
                (directory / "CTX-0001.json").read_bytes(),
                file.read_bytes(),
            )
            self.assertEqual(
                main(["--context-dir", str(directory), "--connections", str(file)]), 0
            )
            self.assertEqual((directory / "CTX-0001.json").read_bytes(), before_ctx)
            self.assertEqual(file.read_bytes(), before_conn)


class IntegrationGuidanceTests(unittest.TestCase):
    def test_all_agents_have_context_guidance(self):
        for filename in (
            "orchestrator",
            "application",
            "candidate",
            "recruiting",
            "technical",
        ):
            with self.subTest(filename=filename):
                p = ROOT / "prompts" / f"{filename}_system_prompt.md"
                text = p.read_text()
                self.assertIn("CTX-", text)
        for name in (
            "shared",
            "orchestrator",
            "application-agent",
            "candidate-profile-agent",
            "recruiting-ats-review-agent",
            "technical-review-agent",
        ):
            with self.subTest(name=name):
                text = (ROOT / "agents" / name / "AGENTS.md").read_text()
                self.assertIn("CTX-", text)

    def test_no_new_unreviewed_candidate_write_permissions(self):
        import inspect

        from permissions import make_permissions

        self.assertIn('mode="interrupt"', inspect.getsource(make_permissions))
        # The runtime permission model remains the unchanged production one;
        # no generic candidate-profile edit capability is handed to application.
        self.assertIn('mode="deny"', inspect.getsource(make_permissions))


if __name__ == "__main__":
    unittest.main()


class ProfileWriteBoundaryTests(unittest.TestCase):
    def test_context_write_guard_rejects_malformed_json(self):
        from candidate_context_records import validate_context_profile_write

        with self.assertRaisesRegex(ContextRecordError, "invalid JSON"):
            validate_context_profile_write(
                "/workspace/candidate/context/CTX-0001.json",
                "{bad",
                candidate_prefix="/workspace/candidate",
            )

    def test_context_write_guard_rejects_fake_filename(self):
        from candidate_context_records import validate_context_profile_write

        with self.assertRaisesRegex(ContextRecordError, "filename"):
            validate_context_profile_write(
                "/workspace/candidate/context/CTX-0002.json",
                json.dumps(BASE),
                candidate_prefix="/workspace/candidate",
            )

    def test_context_write_guard_rejects_unsupported_extension(self):
        from candidate_context_records import validate_context_profile_write

        with self.assertRaisesRegex(ContextRecordError, "must be .json"):
            validate_context_profile_write(
                "/workspace/candidate/context/CTX-0001.yaml",
                "id: CTX-0001",
                candidate_prefix="/workspace/candidate",
            )

    def test_context_write_guard_unrelated_profile_file_unchanged(self):
        from candidate_context_records import validate_context_profile_write

        validate_context_profile_write(
            "/workspace/candidate/profile/SMOKE_PROFILE.md",
            "# Text profile",
            candidate_prefix="/workspace/candidate",
        )

    def test_context_write_guard_custom_candidate_prefix(self):
        from candidate_context_records import validate_context_profile_write

        validate_context_profile_write(
            "/workspace/career/candidate/context/CTX-0001.json",
            json.dumps(BASE),
            candidate_prefix="/workspace/career/candidate",
        )

    def test_profile_review_rejects_invalid_context_before_human_approval(self):
        from profile_review import ReviewError, review_profile_action

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "context"
            path.mkdir()
            proposed = path / "CTX-0001.json"
            action = {
                "name": "write_file",
                "args": {"file_path": str(proposed), "content": "{bad"},
            }
            prompts = []

            def no_approval(prompt):
                prompts.append(prompt)
                self.fail("invalid CTX must fail before HITL")

            with self.assertRaisesRegex(
                ReviewError, "Invalid candidate-context proposal"
            ):
                review_profile_action(
                    action,
                    profile_prefix=temp,
                    allowed_decisions=["approve", "reject", "edit"],
                    input_fn=no_approval,
                )
            self.assertEqual(prompts, [])
            self.assertFalse(proposed.exists())

    def test_profile_review_valid_context_needs_two_approvals(self):
        from profile_review import review_profile_action

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "context"
            path.mkdir()
            proposed = path / "CTX-0001.json"
            action = {
                "name": "write_file",
                "args": {"file_path": str(proposed), "content": json.dumps(BASE)},
            }
            prompts, answers = [], iter(["a", "y"])

            def approve(prompt):
                prompts.append(prompt)
                return next(answers)

            decision = review_profile_action(
                action,
                profile_prefix=temp,
                allowed_decisions=["approve", "reject", "edit"],
                input_fn=approve,
                output=lambda _: None,
            )
            self.assertEqual(decision["type"], "approve")
            self.assertEqual(len(prompts), 2)
            self.assertFalse(proposed.exists())  # middleware never directly writes

    def test_receipt_registration_rejects_malformed_context_without_human_review(self):
        from profile_receipts import ProfileReceiptRegistry

        with tempfile.TemporaryDirectory() as temp:
            context = Path(temp) / "context"
            context.mkdir()
            action = {
                "name": "write_file",
                "args": {
                    "file_path": str(context / "CTX-0001.json"),
                    "content": "{bad",
                },
            }
            registry = ProfileReceiptRegistry()
            registry.begin_turn("smoke-thread")
            with self.assertRaisesRegex(ValueError, "Invalid candidate-context write"):
                registry.record_authorized(
                    "smoke-thread", action, {"type": "approve"}, profile_prefix=temp
                )
            self.assertEqual(registry.unreported("smoke-thread"), 0)
