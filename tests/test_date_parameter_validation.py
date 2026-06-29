import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import dfZSXQ
import goWTA
import goWXGZH


class DateParameterValidationTests(unittest.TestCase):
    def test_invalid_date_ranges_are_rejected_in_all_scripts(self):
        modules = [dfZSXQ, goWTA, goWXGZH]
        invalid_ranges = [
            ('2026-06-01', '20260602'),
            ('20260230', '20260301'),
            ('20260602', '20260601'),
        ]
        for module in modules:
            for start, end in invalid_ranges:
                with self.subTest(
                    module=module.__name__, start=start, end=end
                ):
                    with self.assertRaises(ValueError):
                        module.validate_date_range(start, end)

    def test_wta_keeps_supported_six_digit_date_shorthand(self):
        start, end = goWTA.validate_date_range('260601', '260602')

        self.assertEqual(('20260601', '20260602'), (start, end))

    def test_valid_date_range_is_returned_normalized(self):
        for module in (dfZSXQ, goWXGZH):
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    ('20260601', '20260602'),
                    module.validate_date_range('20260601', '20260602'),
                )


if __name__ == '__main__':
    unittest.main()
