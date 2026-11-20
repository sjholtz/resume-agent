"""Graph-construction wiring test with stub frameworks (no model service needed)."""

from pathlib import Path
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class Perm:
    def __init__(self, *, operations, paths, mode="allow"):
        self.operations, self.paths, self.mode = operations, paths, mode


class FakeGraph:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.config = None

    def with_config(self, config):
        self.config = config
        return self


def module(name, **attrs):
    result = ModuleType(name)
    for key, value in attrs.items():
        setattr(result, key, value)
    return result


class GraphWiringTests(unittest.TestCase):
    def test_build_graph_propagates_backend_permissions_and_tools(self):
        graphs = []
        profile_registrations = []
        def fake_create(**kwargs):
            g = FakeGraph(kwargs)
            graphs.append(g)
            return g
        def memory(**kwargs):
            return SimpleNamespace(**kwargs)
        def make_profile(**kwargs):
            return SimpleNamespace(**kwargs)
        fake_deepagents = module("deepagents", FilesystemPermission=Perm, MemoryMiddleware=memory,
            SubAgent=dict, create_deep_agent=fake_create, HarnessProfile=make_profile,
            GeneralPurposeSubagentProfile=make_profile,
            register_harness_profile=lambda key, profile: profile_registrations.append((key, profile)))
        fake_protocol = module("deepagents.backends.protocol", BackendProtocol=object)
        fake_backends = module("deepagents.backends")
        fake_quickjs = module("langchain_quickjs", CodeInterpreterMiddleware=lambda: "quickjs")
        fake_ckpt = module("langgraph.checkpoint.memory", InMemorySaver=lambda: "memory-saver")
        fake_langgraph = module("langgraph")
        fake_langgraph_checkpoint = module("langgraph.checkpoint")
        fake_receipt_middleware = module(
            "profile_receipt_middleware",
            profile_task_receipt_middleware=lambda request, handler: handler(request),
        )
        fake_models = module("models", model="weak", strong_model="strong")
        fake_pdf_guard = module("pdf_read_guard", PdfReadGuardMiddleware=lambda: "pdf_guard")
        fake_tools = {name: module(name, **{name.rsplit(".", 1)[-1]: object()}) for name in
                      ("tools.search", "tools.read_web_page", "tools.read_web_pdf")}
        fake_tool_refs = {
            "tools.search": "web_search",
            "tools.read_web_page": "read_web_page",
            "tools.read_web_pdf": "read_web_pdf",
        }
        for mod_name, tool_name in fake_tool_refs.items():
            setattr(fake_tools[mod_name], tool_name, tool_name)
        fake_local_pdf = module(
            "tools.read_local_pdf",
            make_read_local_pdf_tool=lambda **kw: "read_local_pdf",
        )
        fake_application_initializer = module(
            "tools.initialize_application",
            make_initialize_application_tool=lambda **kwargs: "initialize_application",
        )
        stager_calls = []
        def make_stager(**kwargs):
            stager_calls.append(kwargs)
            return "prepare_latex_documents"
        fake_latex_stager = module(
            "tools.prepare_latex_documents",
            make_prepare_latex_documents_tool=make_stager,
        )
        fake_latex_validator = module(
            "tools.validate_latex_documents",
            make_validate_latex_documents_tool=lambda **kwargs: "validate_latex_documents",
        )
        fake_claim_auditor = module(
            "tools.audit_application_claims",
            make_audit_application_claims_tool=lambda **kwargs: "audit_application_claims",
        )
        fake_claim_checker = module(
            "tools.check_claim_audit",
            make_check_claim_audit_tool=lambda **kwargs: "check_claim_audit",
        )
        fake_application_state = module(
            "tools.check_application_status",
            make_check_application_status_tool=lambda **kwargs: "check_application_status",
        )
        fake_langchain_core = module("langchain_core")
        fake_langchain_api = module("langchain_core._api")
        fake_beta = module(
            "langchain_core._api.beta_decorator",
            LangChainBetaWarning=type("LangChainBetaWarning", (Warning,), {}),
        )
        stubs = {
            "langchain_core": fake_langchain_core,
            "langchain_core._api": fake_langchain_api,
            "langchain_core._api.beta_decorator": fake_beta,
            "deepagents": fake_deepagents,
            "deepagents.backends": fake_backends,
            "deepagents.backends.protocol": fake_protocol,
            "langchain_quickjs": fake_quickjs,
            "langgraph": fake_langgraph,
            "langgraph.checkpoint": fake_langgraph_checkpoint,
            "langgraph.checkpoint.memory": fake_ckpt,
            "profile_receipt_middleware": fake_receipt_middleware,
            "models": fake_models,
            "pdf_read_guard": fake_pdf_guard,
            **fake_tools,
            "tools.read_local_pdf": fake_local_pdf,
            "tools.initialize_application": fake_application_initializer,
            "tools.prepare_latex_documents": fake_latex_stager,
            "tools.validate_latex_documents": fake_latex_validator,
            "tools.audit_application_claims": fake_claim_auditor,
            "tools.check_claim_audit": fake_claim_checker,
            "tools.check_application_status": fake_application_state,
        }
        with patch.dict(sys.modules, stubs):
            for name in ("agent", "subagents"):
                sys.modules.pop(name, None)
            try:
                agent = importlib.import_module("agent")
                cfg = SimpleNamespace(workspace_root=Path("/workspace"), app_root=Path("/app"))
                fake_backend = object()
                with patch.object(agent, "load_config", return_value=cfg) as load, \
                     patch.object(agent, "build_backend", return_value=fake_backend):
                    graph = agent.make_graph(base_resume="backend.tex", base_cover="cover.tex")
                self.assertEqual(graph.config, {"recursion_limit": 50})
                self.assertEqual(stager_calls[0]["base_resume"], "backend.tex")
                self.assertEqual(stager_calls[0]["base_cover"], "cover.tex")
                self.assertEqual(load.call_args.kwargs["interactive"], False)
                self.assertEqual(load.call_args.kwargs["workspace"], Path.cwd())
                kw = graph.kwargs
                self.assertIs(kw["backend"], fake_backend)
                self.assertEqual(len(kw["subagents"]), 4)
                self.assertEqual(kw["skills"], ["/system/skills/orchestrator/"])
                self.assertEqual(kw["memory"], ["/system/agents/shared/AGENTS.md",
                                                  "/system/agents/orchestrator/AGENTS.md"])
                self.assertIn("web_search", kw["interrupt_on"])
                self.assertIn("read_local_pdf", kw["tools"])
                application = next(s for s in kw["subagents"] if s["name"] == "application-agent")
                self.assertIn("initialize_application", application["tools"])
                self.assertIn("prepare_latex_documents", application["tools"])
                self.assertIn("validate_latex_documents", application["tools"])
                self.assertIn("audit_application_claims", application["tools"])
                self.assertIn("check_claim_audit", application["tools"])
                self.assertIn("check_claim_audit", kw["tools"])
                self.assertNotIn("check_claim_audit", kw["interrupt_on"])
                self.assertIn("check_application_status", kw["tools"])
                self.assertIn("check_application_status", application["tools"])
                self.assertNotIn("check_application_status", kw["interrupt_on"])
                self.assertEqual(application["interrupt_on"]["audit_application_claims"]["allowed_decisions"], ["approve", "reject"])
                self.assertNotIn("check_claim_audit", application["interrupt_on"])
                self.assertEqual(application["interrupt_on"]["prepare_latex_documents"]["allowed_decisions"], ["approve", "reject"])
                self.assertEqual(application["interrupt_on"]["validate_latex_documents"]["allowed_decisions"], ["approve", "reject"])
                for other in (s for s in kw["subagents"] if s["name"] != "application-agent"):
                    self.assertNotIn("prepare_latex_documents", other["tools"])
                    self.assertNotIn("validate_latex_documents", other["tools"])
                    self.assertNotIn("audit_application_claims", other["tools"])
                for reviewer in (s for s in kw["subagents"] if "review" in s["name"]):
                    self.assertIn("check_claim_audit", reviewer["tools"])
                    self.assertNotIn("check_claim_audit", reviewer["interrupt_on"])
                self.assertEqual(application["interrupt_on"]["initialize_application"]["allowed_decisions"], ["approve", "reject"])
                self.assertTrue(all("initialize_application" not in s["tools"] for s in kw["subagents"] if s["name"] != "application-agent"))
                self.assertTrue(all("read_local_pdf" in sub["tools"] for sub in kw["subagents"]))
                self.assertNotIn("read_local_pdf", kw["interrupt_on"])
                self.assertNotIn("read_file", kw["interrupt_on"])
                self.assertEqual([s["name"] for s in kw["subagents"]], [
                    "candidate-profile-agent", "application-agent", "recruiting-ats-review-agent", "technical-review-agent"])
                self.assertTrue(all(s["middleware"][1].backend is fake_backend for s in kw["subagents"]))
                self.assertTrue(all(s["middleware"][0] == "pdf_guard" for s in kw["subagents"]))
                self.assertEqual([s["permissions"][1].mode for s in kw["subagents"]],
                                 ["deny", "deny", "deny", "deny"])
                self.assertEqual(kw["permissions"][1].mode, "deny")
                self.assertEqual(profile_registrations[0][0], "ollama")
                self.assertFalse(profile_registrations[0][1].general_purpose_subagent.enabled)
                self.assertEqual(
                    kw["middleware"],
                    [fake_receipt_middleware.profile_task_receipt_middleware, "pdf_guard"],
                )
            finally:
                for name in ("agent", "subagents"):
                    sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
