from __future__ import annotations

import unittest

import numpy as np

from app.geometry.esdf import compute_esdf


class BevEsdfTest(unittest.TestCase):
    def test_free_positive_obstacle_negative(self) -> None:
        occupancy = np.zeros((5, 5), dtype=np.uint8)
        occupancy[2, 2] = 255

        esdf = compute_esdf(occupancy, 0.1)

        self.assertLess(esdf[2, 2], 0)
        self.assertGreater(esdf[0, 0], 0)
        self.assertGreater(esdf[2, 1], 0)


if __name__ == "__main__":
    unittest.main()
