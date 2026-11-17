import unittest

from write_decision_audit import WriteDecisionAudit


class WriteAuditTests(unittest.TestCase):
    def test_rejected_write_produces_canonical_failure_of_authorization(self):
        audit = WriteDecisionAudit()
        audit.record(
            {
                "name": "write_file",
                "args": {
                    "file_path": "/workspace/applications/smoke-test/SMOKE_RESULT.md",
                    "content": "APPROVED_WRITE_SMOKE_V1\n",
                },
            },
            authorized=False,
        )
        report = audit.rejection_report()
        self.assertIn("REJECTED: write_file", report)
        self.assertIn("No non-profile file modification was authorized", report)

    def test_multiple_denials(self):
        audit = WriteDecisionAudit()
        audit.record(
            {"name": "write_file", "args": {"file_path": "/a"}}, authorized=False
        )
        audit.record(
            {"name": "edit_file", "args": {"file_path": "/b"}}, authorized=False
        )
        self.assertEqual(len(audit.denied), 2)

    def test_mixed_authorizations_does_not_claim_no_writes(self):
        audit = WriteDecisionAudit()
        audit.record(
            {"name": "write_file", "args": {"file_path": "/a"}}, authorized=False
        )
        audit.record(
            {"name": "write_file", "args": {"file_path": "/a"}}, authorized=True
        )
        report = audit.rejection_report()
        self.assertIn("AUTHORIZED (execution not verified)", report)
        self.assertNotIn("No non-profile file modification was authorized", report)

    def test_approved_only_does_not_override_model(self):
        audit = WriteDecisionAudit()
        audit.record(
            {"name": "write_file", "args": {"file_path": "/a"}}, authorized=True
        )
        self.assertIsNone(audit.rejection_report())

    def test_non_write_tool_ignored(self):
        audit = WriteDecisionAudit()
        audit.record(
            {"name": "read_file", "args": {"file_path": "/a"}}, authorized=False
        )
        self.assertFalse(audit.decisions)
        self.assertIsNone(audit.rejection_report())

    def test_does_not_leak_proposed_content(self):
        audit = WriteDecisionAudit()
        audit.record(
            {
                "name": "write_file",
                "args": {"file_path": "/a", "content": "PRIVATE CANDIDATE CONTENT"},
            },
            authorized=False,
        )
        self.assertNotIn("PRIVATE CANDIDATE CONTENT", audit.rejection_report())


if __name__ == "__main__":
    unittest.main()
