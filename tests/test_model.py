import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error

from wiki_workbench.model import ModelClient, BudgetBlocked, _endpoint, _NoRedirect


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        self.client = ModelClient(self.root)

    def configure(self, **values):
        return self.client.configure({'api_key': 'test-only-placeholder', 'enabled': True, **values})

    def response(self, business=None):
        return io.BytesIO(json.dumps({'model': 'provider-actual', 'usage': {'prompt_tokens': 100, 'completion_tokens': 20},
                                     'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(business or {'summary': 'OK'})}}]}).encode())

    def test_status_does_not_create_files_or_leak_api_key(self):
        status = self.client.status()
        self.assertFalse(status['enabled'])
        self.assertFalse(status['configured'])
        self.assertEqual(list(self.root.iterdir()), [])
        status = self.configure()
        self.assertTrue(status['configured'])
        self.assertNotIn('api_key', status)
        self.assertNotIn('test-only-placeholder', json.dumps(status))
        config = self.root / '.wiki-workbench/private/model.json'
        if os.name != 'nt':
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual((config.parent / '.gitignore').read_text(), '*\n')

    def test_disabled_and_budget_block_before_network(self):
        with patch('wiki_workbench.model.urllib.request.build_opener') as network:
            with self.assertRaises(BudgetBlocked):
                self.client.complete('system', 'text', 'test')
            self.configure(budget_cny=0.001)
            with self.assertRaises(BudgetBlocked):
                self.client.complete('system', 'text', 'test')
            network.assert_not_called()
            self.assertEqual(self.client.status()['calls'], 0)

    def test_real_provider_contract_returns_business_json_and_logs_usage(self):
        self.configure()
        opener = Mock()
        opener.open.return_value = self.response({'summary': '整理成功'})
        with patch('wiki_workbench.model.urllib.request.build_opener', return_value=opener):
            result = self.client.complete('system', 'fictional note', 'reading', max_tokens=2400)
        self.assertEqual(result, {'summary': '整理成功'})
        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)['max_tokens'], 2400)
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-placeholder')
        ledger = json.loads((self.root / '.wiki-workbench/private/usage.json').read_text())
        self.assertEqual(ledger['calls'][0]['returned_model'], 'provider-actual')
        self.assertEqual(ledger['calls'][0]['usage']['prompt_tokens'], 100)
        self.assertNotIn('fictional note', json.dumps(ledger))
        self.assertNotIn('test-only-placeholder', json.dumps(ledger))
        self.assertAlmostEqual(self.client.status()['usage_cny'], 0.0008)

    def test_transport_failure_retains_reserved_budget_and_sanitizes_error(self):
        self.configure()
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError('test-only-placeholder secret provider body')
        with patch('wiki_workbench.model.urllib.request.build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, '不会自动重试') as raised:
                self.client.complete('system', 'fictional note', 'test')
        self.assertNotIn('test-only-placeholder', str(raised.exception))
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(self.client.status()['uncertain_calls'], 1)
        self.assertGreater(self.client.status()['usage_cny'], 0)

    def test_concurrent_calls_reserve_budget_before_network(self):
        self.configure(budget_cny=0.075)
        entered, release = threading.Event(), threading.Event()
        def opening(*args, **kwargs):
            entered.set()
            release.wait(3)
            return self.response()
        opener = Mock()
        opener.open.side_effect = opening
        failures = []
        def first():
            try:
                self.client.complete('system', 'text', 'first')
            except Exception as exc:
                failures.append(exc)
        with patch('wiki_workbench.model.urllib.request.build_opener', return_value=opener):
            thread = threading.Thread(target=first)
            thread.start()
            self.assertTrue(entered.wait(2))
            with self.assertRaises(BudgetBlocked):
                self.client.complete('system', 'text', 'second')
            release.set()
            thread.join(3)
        self.assertFalse(failures)
        self.assertEqual(opener.open.call_count, 1)

    def test_plaintext_external_and_redirect_forbidden(self):
        with self.assertRaises(ValueError):
            _endpoint('http://example.com/v1')
        with self.assertRaises(ValueError):
            _endpoint('https://user:password@example.com')
        self.assertEqual(_endpoint('http://127.0.0.1:1234/v1'), 'http://127.0.0.1:1234/v1/chat/completions')
        with self.assertRaisesRegex(ValueError, '重定向'):
            _NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid')

    def test_invalid_response_keeps_actual_usage_and_no_retries(self):
        self.configure()
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({'model': 'actual', 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}, 'choices': [{'finish_reason': 'stop', 'message': {'content': 'invalid JSON'}}]}).encode())
        with patch('wiki_workbench.model.urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ValueError):
                self.client.complete('system', 'text', 'test')
        self.assertEqual(opener.open.call_count, 1)
        self.assertAlmostEqual(self.client.status()['usage_cny'], 0.0008)
        ledger = json.loads((self.root / '.wiki-workbench/private/usage.json').read_text())
        self.assertEqual(ledger['calls'][0]['status'], 'invalid_response')

    def test_changing_provider_origin_requires_replacing_or_clearing_key(self):
        self.configure()
        with self.assertRaisesRegex(ValueError, '新服务密钥'):
            self.client.configure({'base_url': 'https://new.invalid/v1'})
        self.assertEqual(self.client.status()['base_url'], 'https://api.deepseek.com')
        cleared = self.client.configure({'base_url': 'https://new.invalid/v1', 'clear_api_key': True})
        self.assertFalse(cleared['configured'])

    def test_truncated_response_cannot_be_treated_as_success(self):
        self.configure()
        data = json.loads(self.response().getvalue())
        data['choices'][0]['finish_reason'] = 'length'
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(data).encode())
        with patch('wiki_workbench.model.urllib.request.build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, '不会自动重试'):
                self.client.complete('system', 'text', 'test')
        self.assertEqual(opener.open.call_count, 1)
        self.assertAlmostEqual(self.client.status()['usage_cny'], 0.0008)

    def test_missing_private_gitignore_rule_prevents_writing_key(self):
        private = self.root / '.wiki-workbench/private'
        private.mkdir(parents=True)
        (private / '.gitignore').write_text('')
        with self.assertRaisesRegex(ValueError, '忽略规则'):
            self.configure()
        self.assertFalse((private / 'model.json').exists())

    def test_malformed_transport_json_is_sanitized_and_keeps_reservation(self):
        self.configure()
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'[]')
        with patch('wiki_workbench.model.urllib.request.build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, '不会自动重试'):
                self.client.complete('system', 'text', 'test')
        self.assertEqual(self.client.status()['uncertain_calls'], 1)

    def test_sensitive_content_never_sent(self):
        self.configure()
        with patch('wiki_workbench.model.urllib.request.build_opener') as network:
            with self.assertRaisesRegex(ValueError, '敏感|凭据'):
                self.client.complete('system', 'api_key=sk-secretvalue123456789', 'test')
            network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
