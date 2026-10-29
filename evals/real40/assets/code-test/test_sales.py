import unittest
import sales
class SalesTest(unittest.TestCase):
    def test_discount(self):
        self.assertEqual(sales.discount(15), 15)
