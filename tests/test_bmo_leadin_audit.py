import unittest

import numpy as np

from tools.audit_bmo_leadins import MATCH_THRESHOLD, correlation


class LeadInFingerprintTests(unittest.TestCase):
    def test_shifted_artifact_is_located_with_normalized_correlation(self):
        rng = np.random.default_rng(7)
        template = rng.normal(0, 1000, 8000)
        audio = np.concatenate((np.zeros(4410), template, np.zeros(12000)))
        score, start = correlation(audio, template.copy())
        self.assertGreater(score, 0.99)
        self.assertAlmostEqual(start, 0.2, places=2)

    def test_unrelated_audio_stays_below_apply_threshold(self):
        rng = np.random.default_rng(11)
        template = rng.normal(0, 1000, 8000)
        audio = rng.normal(0, 1000, 30000)
        score, _ = correlation(audio, template.copy())
        self.assertLess(score, MATCH_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
