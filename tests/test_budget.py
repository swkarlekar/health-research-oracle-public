"""Budget invariants with concurrent callers; no AWS clients or network."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from oracle.budget import SpendLedger, microdollars
from oracle.corpus import HarnessError


class BudgetTests(unittest.TestCase):
    def test_settlement_cannot_relabel_an_approved_stage(self):
        self.ledger.use_stage('approved', '.50', isolated=True)
        identity = self.ledger.reserve('call', '.49')
        self.ledger.settle(identity, '.49', details={'approval_stage': 'different'})
        with self.assertRaisesRegex(HarnessError, 'BUDGET STOP'):
            self.ledger.reserve('call', '.49')
        self.assertEqual(self.ledger.summary()['charges'][0]['details']['approval_stage'], 'approved')

    def test_independent_approved_allowances_can_interleave_without_resetting_either(self):
        self.ledger.reserve('earlier', 4)
        embeddings = SpendLedger(self.path).use_stage('embeddings', '.01', isolated=True)
        generation = SpendLedger(self.path).use_stage('generation', '.50', isolated=True)
        embeddings.reserve('embedding', '.004')
        generation.reserve('model', '.49')
        restarted = SpendLedger(self.path).use_stage('embeddings', '.01', isolated=True)
        restarted.reserve('embedding', '.006')
        with self.assertRaises(HarnessError):
            restarted.reserve('embedding', '.000001')
        with self.assertRaises(HarnessError):
            generation.reserve('model', '.010001')
        with self.assertRaises(HarnessError):
            restarted.use_stage('embeddings', '.02', isolated=True)
        self.assertAlmostEqual(restarted.summary()['committed_upper_usd'], 4.5)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'budget.sqlite'
        self.ledger = SpendLedger(self.path)

    def test_concurrent_reservations_cannot_cross_cap(self):
        def attempt(_):
            try:
                return self.ledger.reserve('call', '0.6')
            except HarnessError:
                return None
        with ThreadPoolExecutor(max_workers=12) as executor:
            accepted = list(executor.map(attempt, range(40)))
        self.assertEqual(sum(item is not None for item in accepted), 16)
        self.assertEqual(self.ledger.summary()['committed_upper_usd'], 9.6)

    def test_failed_call_and_restart_keep_the_reservation(self):
        self.ledger.reserve('failed-or-unknown', '9.5')
        restarted = SpendLedger(self.path)
        with self.assertRaises(HarnessError):
            restarted.reserve('next', '0.500001')
        self.assertEqual(restarted.summary()['remaining_usd'], 0.5)

    def test_settlement_releases_only_the_unused_reservation(self):
        identity = self.ledger.reserve('model', '1')
        self.ledger.settle(identity, '0.1234561', details={'input_tokens': 100})
        summary = self.ledger.summary()
        self.assertEqual(summary['committed_upper_usd'], 0.123457)
        self.assertEqual(summary['charges'][0]['details']['input_tokens'], 100)
        with self.assertRaises(HarnessError):
            self.ledger.settle(identity, 0)

    def test_any_overrun_persists_and_stops_even_if_total_is_below_cap(self):
        identity = self.ledger.reserve('call', '0.01')
        with self.assertRaises(HarnessError):
            self.ledger.settle(identity, '0.02')
        self.assertEqual(self.ledger.summary()['committed_upper_usd'], 0.02)
        with self.assertRaises(HarnessError):
            SpendLedger(self.path).reserve('next', 0)

    def test_cap_cannot_be_raised_or_reset(self):
        for cap in ('11', '0', '-1', 'NaN'):
            with self.subTest(cap=cap), self.assertRaises(HarnessError):
                SpendLedger(self.path, cap)
        with self.assertRaises(HarnessError):
            SpendLedger(self.path, '9')

    def test_round_up_and_invalid_amounts(self):
        self.assertEqual(microdollars('0.00000001'), 1)
        for amount in ('-1', 'NaN', 'Infinity'):
            with self.assertRaises(HarnessError):
                self.ledger.reserve('bad', amount)

    def test_stage_ceiling_is_atomic_and_cannot_reset_on_resume(self):
        self.ledger.reserve('earlier', 4)
        self.ledger.use_stage('approved-addition', '.60')
        self.ledger.reserve('setup', '.25')
        resumed = SpendLedger(self.path).use_stage('approved-addition', '.60')
        self.assertEqual(resumed.summary()['stage_limits'][0]['ceiling_usd'], 4.6)
        with self.assertRaises(HarnessError):
            resumed.reserve('too-much', '.350001')
        with self.assertRaises(HarnessError):
            resumed.use_stage('approved-addition', '.70')

    def test_historical_stage_baseline_does_not_include_its_own_setup(self):
        self.ledger.reserve('earlier', 4)
        marker = self.ledger.reserve('stage-setup', '.25')
        timestamp = next(r['created_at'] for r in self.ledger.summary()['charges'] if r['id'] == marker)
        self.ledger.use_stage('approved-addition', '.60', baseline_before=timestamp)
        self.assertEqual(self.ledger.summary()['stage_limits'][0]['ceiling_usd'], 4.6)


if __name__ == '__main__':
    unittest.main()
