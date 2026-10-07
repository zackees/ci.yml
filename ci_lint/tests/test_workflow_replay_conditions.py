"""Profile exclusions must follow source conditions, never guessed runner state."""

import unittest

from ci_lint.workflow_replay_conditions import condition_excludes
from ci_lint.workflow_replay_inputs import BoundInput
from ci_lint.workflow_replay_identity import JobIdentity
from ci_lint.workflow_replay_outputs import ReplayOutput


class ConditionProofTest(unittest.TestCase):
    def setUp(self):
        self.inputs = (BoundInput("compile", False), BoundInput("profile", "dev"),
                       BoundInput("artifact", ""), BoundInput("target", "linux-x64"))

    def excluded(self, guard, *, successful=False):
        return condition_excludes(guard, self.inputs, {"download"}, successful=successful)

    def test_boolean_and_string_profile_conditions(self):
        for guard in ("inputs.compile", "inputs.compile == true", "inputs.profile == 'release'",
                      "inputs.artifact != ''", "contains(inputs.target, 'windows')"):
            with self.subTest(guard=guard):
                self.assertTrue(self.excluded(guard))
        for guard in ("inputs.profile == 'DEV'", "inputs.compile == false", "inputs.artifact == ''"):
            self.assertFalse(self.excluded(guard))

    def test_compound_profile_short_circuits_unknown_runner_context(self):
        self.assertTrue(self.excluded("${{ inputs.profile == 'release' && github.workflow_ref != format('{0}@{1}', github.repository, github.ref) }}"))
        self.assertTrue(self.excluded("inputs.artifact != '' && (inputs.profile != 'dev' || inputs.compile)"))
        self.assertFalse(self.excluded("inputs.compile || github.ref == 'main'"))
        self.assertFalse(self.excluded("inputs.compile || true"))
        self.assertTrue(self.excluded("(inputs.compile || inputs.artifact != '') && runner.os == 'Linux'"))

    def test_diagnostics_excluded_only_when_every_required_check_must_pass(self):
        for guard in ("failure()", "failure() && runner.os == 'Linux'", "always() && (failure() || cancelled())"):
            self.assertFalse(self.excluded(guard))
            self.assertTrue(self.excluded(guard, successful=True))
        self.assertFalse(self.excluded("success()", successful=True))

    def test_event_guard_uses_declared_receipt_event_only(self):
        guard = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        self.assertFalse(self.excluded(guard))
        self.assertTrue(condition_excludes(guard, (), set(), event="pull_request"))
        self.assertFalse(condition_excludes(guard, (), set(), event="push"))

    def test_negation_uses_bound_truth_and_preserves_unknowns(self):
        for guard in ("!inputs.profile", "!!inputs.compile", "!(!inputs.compile)",
                      "!inputs.profile == true", "!inputs.profile != false",
                      "!(inputs.profile == 'dev')"):
            with self.subTest(guard=guard):
                self.assertTrue(self.excluded(guard))
        for guard in ("!inputs.compile", "!inputs.missing", "!!inputs.missing",
                      "!false", "!true", "!inputs.profile == ''",
                      "!inputs.profile == 'dev'"):
            with self.subTest(guard=guard):
                self.assertFalse(self.excluded(guard))

    def test_negated_status_guards_require_successful_execution_proof(self):
        guard = "!cancelled() && !failure() && inputs.profile == 'release'"
        self.assertTrue(self.excluded(guard, successful=True))
        self.assertFalse(self.excluded("!success()"))
        self.assertTrue(self.excluded("!success()", successful=True))
        self.assertFalse(self.excluded("!cancelled() && !failure()", successful=True))
        self.assertFalse(self.excluded("!cancelled()"))

    def test_skipped_producer_and_operand_valued_boolean_operators(self):
        self.assertTrue(self.excluded("steps.download.outcome == 'success'"))
        self.assertFalse(self.excluded("steps.missing.outcome == 'success'"))
        self.assertFalse(self.excluded("(github.ref || 'fallback') == 'fallback'"))
        self.assertTrue(self.excluded("(inputs.profile || 'fallback') == 'fallback'"))

    def test_unknown_types_syntax_and_constants_cannot_waive_checks(self):
        for guard in (False, "false", "false && inputs.profile", "inputs.compile == ''",
                      "inputs.missing", "!inputs.compile", "inputs.compile + true", "inputs.compile ||",
                      "inputs.compile && __import__('os').system('true')", "x" * 4097):
            with self.subTest(guard=guard):
                self.assertFalse(self.excluded(guard))

    def test_quoted_operators_and_github_quote_escaping(self):
        inputs = (BoundInput("text", "it's && fine"),)
        self.assertFalse(condition_excludes("inputs.text == 'it''s && fine'", inputs, set()))
        self.assertTrue(condition_excludes("inputs.text != 'it''s && fine'", inputs, set()))

    def test_proved_dependency_output_guards(self):
        outputs = (ReplayOutput((JobIdentity("planner"),), "suites", '["integration"]', 21),)
        guard = "contains(fromJSON(needs.planner.outputs.suites), 'unit')"
        self.assertTrue(condition_excludes(guard, (), set(), outputs=outputs, dependencies=("planner",)))
        for absent in ((), ("foreign",)):
            self.assertFalse(condition_excludes(guard, (), set(), outputs=outputs, dependencies=absent))
        self.assertFalse(condition_excludes(guard, (), set(), dependencies=("planner",)))

    def test_output_membership_is_exact_and_ascii_case_insensitive(self):
        guard = "contains(fromJSON(needs.planner.outputs.suites), 'unit')"
        for value, excluded in (('["unittest"]', True), ('["UNIT"]', False), ('[]', True),
                                ('[1]', False), ('{}', False), ('null', False),
                                ('["λ"]', False), ('["${{ inputs.x }}"]', False)):
            with self.subTest(value=value):
                outputs = (ReplayOutput((JobIdentity("planner"),), "suites", value, 21),)
                self.assertEqual(condition_excludes(guard, (), set(), outputs=outputs,
                                                   dependencies=("planner",)), excluded)

    def test_output_guard_rejects_foreign_matrix_and_duplicate_producers(self):
        guard = "needs.planner.outputs.test != 'true'"
        good = ReplayOutput((JobIdentity("planner"),), "test", "true", 21)
        for outputs in ((good, good),
                        (ReplayOutput((JobIdentity("caller"), JobIdentity("planner")), "test", "true", 21),),
                        (ReplayOutput((JobIdentity("planner", '{"lane":"a"}'),), "test", "true", 21),)):
            self.assertFalse(condition_excludes(guard, (), set(), outputs=outputs, dependencies=("planner",)))
        self.assertTrue(condition_excludes(guard, (), set(), outputs=(good,), dependencies=("planner",)))

    def test_array_identity_is_not_structural_equality(self):
        guard = "fromJSON(needs.planner.outputs.suites) != fromJSON(needs.planner.outputs.suites)"
        output = ReplayOutput((JobIdentity("planner"),), "suites", '["unit"]', 21)
        self.assertFalse(condition_excludes(guard, (), set(), outputs=(output,), dependencies=("planner",)))

    def test_output_guard_binds_to_nested_consumer_scope(self):
        caller = (JobIdentity("linux"),)
        output = ReplayOutput(caller + (JobIdentity("planner"),), "test", "true", 21)
        guard = "needs.planner.outputs.test != 'true'"
        self.assertTrue(condition_excludes(guard, (), set(), outputs=(output,),
                                           dependencies=("planner",), scope=caller))
        self.assertFalse(condition_excludes(guard, (), set(), outputs=(output,),
                                            dependencies=("planner",), scope=(JobIdentity("windows"),)))

    def test_output_array_limits_never_waive_a_check(self):
        import json
        guard = "contains(fromJSON(needs.planner.outputs.suites), 'unit')"
        for value in (json.dumps(["integration"] * 257), json.dumps(["x" * 65536]),
                      "[" * 1000 + "]" * 1000):
            output = ReplayOutput((JobIdentity("planner"),), "suites", value, 21)
            self.assertFalse(condition_excludes(guard, (), set(), outputs=(output,), dependencies=("planner",)))
