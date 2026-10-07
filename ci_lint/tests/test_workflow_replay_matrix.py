"""Literal matrix conformance and bounds; never simulated execution proof."""

import json
import unittest

from ci_lint.workflow_replay_matrix import job_matrices


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
