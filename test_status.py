import unittest

import quotes
from status import ACTION_STATUSES, CLOSED_STATUSES, QuoteStatus, sql_in


class QuoteStatusTests(unittest.TestCase):
    def test_values_match_persisted_strings(self):
        # Las filas ya guardadas en SQLite usan estos textos; no pueden cambiar.
        self.assertEqual(QuoteStatus.REVIEW_PENDING, "review_pending")
        self.assertEqual(str(QuoteStatus.READY), "ready")
        self.assertEqual(QuoteStatus("response_sent"), QuoteStatus.RESPONSE_SENT)

    def test_every_status_has_a_dashboard_label(self):
        self.assertEqual(set(quotes.STATUS_LABELS), set(QuoteStatus))

    def test_groups_only_contain_known_statuses(self):
        self.assertLessEqual(CLOSED_STATUSES | ACTION_STATUSES, set(QuoteStatus))

    def test_sql_in_rejects_unknown_status(self):
        self.assertEqual(sql_in(QuoteStatus.READY, QuoteStatus.IGNORED), "('ready', 'ignored')")
        with self.assertRaises(ValueError):
            sql_in("ready'); DROP TABLE quotes;--")


if __name__ == "__main__":
    unittest.main()
