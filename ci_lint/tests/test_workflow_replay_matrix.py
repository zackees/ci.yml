"""Literal matrix conformance and bounds; never simulated execution proof."""

import json
import unittest

from ci_lint.workflow_replay_matrix import job_matrices
from ci_lint.workflow_replay_outputs import ReplayOutput
from ci_lint.workflow_replay_identity import identity_part


class MatrixTest(unittest.TestCase):
    def test_product_exclusion_include_merge_and_extra_leg(self) -> None:
        actual = job_matrices({"strategy": {"matrix": {
            "os": ["linux", "windows"], "arch": ["x64", "arm"],
            "exclude": [{"os": "windows", "arch": "arm"}],
            "include": [{"os": "linux", "feature": True}, {"os": "other", "arch": "mips"}],
        }}})
        self.assertEqual(set(actual), {
            '{"arch":"x64","feature":true,"os":"linux"}',
            '{"arch":"arm","feature":true,"os":"linux"}',
            '{"arch":"x64","os":"windows"}',
            '{"arch":"mips","os":"other"}',
        })

    def test_include_only_and_object_axis_keep_full_values(self) -> None:
        self.assertEqual(job_matrices({"strategy": {"matrix": {"include": [
            {"target": "linux", "runner": "ubuntu"}, {"target": "windows", "runner": "windows"},
        ]}}}), ('{"runner":"ubuntu","target":"linux"}', '{"runner":"windows","target":"windows"}'))
        actual = job_matrices({"strategy": {"matrix": {"lane": [{"target": "linux", "native": True}]}}})
        self.assertEqual(actual, ('{"lane":{"native":true,"target":"linux"}}',))

    def test_unknown_duplicate_and_unbounded_matrices_reject(self) -> None:
        documents = (
            '{"matrix":"${{ needs.plan.outputs.matrix }}"}',
            '{"matrix":{"os":["${{ inputs.os }}"]}}',
            '{"matrix":{"os":["linux","linux"]}}',
            '{"matrix":{"os":["linux"],"exclude":[{"other":"x"}]}}',
            '{"matrix":{"os":[]}}',
            '{"matrix":{"os":["linux"],"include":"unknown"}}',
        )
        for document in documents:
            with self.subTest(document=document), self.assertRaises(ValueError):
                job_matrices({"strategy": json.loads(document)})
        with self.assertRaises(ValueError):
            job_matrices({"strategy": {"matrix": {"a": list(range(17)), "b": list(range(17))}}})

    def test_no_strategy_and_non_matrix_strategy_are_single_jobs(self) -> None:
        self.assertEqual(job_matrices({}), ("null",))
        self.assertEqual(job_matrices({"strategy": {"fail-fast": False}}), ("null",))


class ProvedMatrixTest(unittest.TestCase):
    def setUp(self):
        self.job = {"needs": "plan", "strategy": {
            "matrix": "${{ fromJSON(needs.plan.outputs.matrix) }}"}}
        self.output = ReplayOutput((identity_part("plan"),), "matrix", '{"lane":["left","right"]}', 21)

    def test_proved_matrix_uses_existing_product_and_include_rules(self):
        actual = job_matrices(self.job, outputs=(self.output,))
        self.assertEqual(actual, ('{"lane":"left"}', '{"lane":"right"}'))
        self.job["strategy"]["matrix"] = {"lane": "${{ fromJSON(needs.plan.outputs.matrix) }}"}
        output = ReplayOutput(self.output.identity, "matrix", '["left","right"]', 21)
        self.assertEqual(job_matrices(self.job, outputs=(output,)), actual)

    def test_missing_unrelated_ambiguous_and_foreign_scope_outputs_refuse(self):
        for outputs in (
            (), (ReplayOutput((identity_part("other"),), "matrix", self.output.value, 21),),
            (self.output, self.output),
            (ReplayOutput((identity_part("caller"), identity_part("plan")), "matrix", self.output.value, 21),),
            (ReplayOutput((identity_part("plan", {"lane": "one"}),), "matrix", self.output.value, 21),),
        ):
            with self.subTest(outputs=outputs), self.assertRaises(ValueError):
                job_matrices(self.job, outputs=outputs)
        self.job["needs"] = "other"
        with self.assertRaises(ValueError):
            job_matrices(self.job, outputs=(self.output,))

    def test_nested_caller_scope_is_preserved(self):
        scope = (identity_part("caller", {"os": "linux"}),)
        output = ReplayOutput(scope + self.output.identity, "matrix", self.output.value, 21)
        self.assertEqual(job_matrices(self.job, outputs=(output,), scope=scope),
                         ('{"lane":"left"}', '{"lane":"right"}'))

    def test_dynamic_payloads_do_not_drop_coverage_or_accept_invalid_json(self):
        for payload in ('{}', '{"include":[]}', '{"lane":[]}',
                        '{"lane":["left"],"lane":["right"]}',
                        '{"lane":[NaN]}', '{"lane":["${{ inputs.os }}"]}',
                        '{"lane":'+str(list(range(257)))+'}'):
            output = ReplayOutput(self.output.identity, "matrix", payload, 21)
            with self.subTest(payload=payload[:80]), self.assertRaises(ValueError):
                job_matrices(self.job, outputs=(output,))
