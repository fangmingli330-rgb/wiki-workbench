import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from wiki_workbench.server import Application, make_handler, ask


class FakeModel:
    def __init__(self, path):
        self.path = path
        self.calls = 0
    def status(self):
        return {'configured': True, 'enabled': True}
    def complete(self, system, user, **kwargs):
        self.calls += 1
        return {'paragraphs': [{'text': '原文提到了回看。', 'source_ids': ['1']}]}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'notes').mkdir()
        (self.root / 'notes' / 'example.md').write_text('# 阅读方法\n理解与回看也重要。\n', encoding='utf-8')
        self.app = Application(model_factory=FakeModel)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.port = self.server.server_port
        self.origin = f'http://127.0.0.1:{self.port}'
        self.ident = ''
    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
    def req(self, method, path, payload=None, **extra):
        conn = http.client.HTTPConnection('127.0.0.1', self.port)
        headers = {'Origin': self.origin, 'X-Workbench-CSRF': self.app.csrf, 'X-Workspace-ID': self.ident, 'Content-Type': 'application/json'}
        headers.update(extra)
        body = None if payload is None else json.dumps(payload).encode()
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        raw = response.read()
        parsed = json.loads(raw) if 'application/json' in response.getheader('Content-Type', '') else raw
        result = response.status, parsed, dict(response.getheaders())
        conn.close()
        return result
    def select(self, folder='notes'):
        code, value, _ = self.req('POST', '/api/workspace/select', {'path': str(self.root / folder)})
        self.assertEqual(code, 200)
        self.ident = value['workspace']['id']
        return value
    def test_first_open_is_read_only_and_directory_is_explicit(self):
        before = sorted(str(p) for p in self.root.rglob('*'))
        code, value, _ = self.req('GET', '/api/status')
        self.assertEqual(code, 200)
        self.assertIsNone(value['workspace'])
        self.select()
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob('*')))
    def test_scan_confirm_search_document_and_ai_citations(self):
        self.select()
        original = (self.root / 'notes' / 'example.md').read_bytes()
        code, scan, _ = self.req('POST', '/api/scan', {})
        self.assertEqual(code, 200)
        self.assertFalse((self.root / 'notes' / '.wiki-workbench').exists())
        self.assertEqual(self.req('POST', '/api/apply', {'scan_id': 'wrong'})[0], 409)
        self.assertEqual(self.req('POST', '/api/apply', {'scan_id': scan['id']})[0], 200)
        value = self.req('GET', '/api/search?q=%E5%9B%9E%E7%9C%8B')[1]
        self.assertEqual(value['results'][0]['line'], 2)
        sid = value['results'][0]['source_id']
        self.assertEqual(self.req('GET', '/api/document?id=' + sid)[1]['content'].encode(), original)
        answer = self.req('POST', '/api/ask', {'query': '回看'})[1]
        self.assertTrue(answer['model_called'])
        self.assertEqual(answer['citations'][0]['source_id'], sid)
        self.assertEqual(self.app.client.calls, 1)
        self.assertFalse(self.req('POST', '/api/ask', {'query': '完全没有'})[1]['model_called'])
        self.assertEqual(self.app.client.calls, 1)
        self.assertEqual((self.root / 'notes' / 'example.md').read_bytes(), original)
    def test_original_changed_after_preview_is_not_applied(self):
        self.select()
        scan = self.req('POST', '/api/scan', {})[1]
        (self.root / 'notes' / 'example.md').write_text('changed')
        self.assertEqual(self.req('POST', '/api/apply', {'scan_id': scan['id']})[0], 400)
    def test_proposal_is_bound_to_preview_and_rejected_if_scan_changes(self):
        self.select()
        scan = self.req('POST', '/api/scan', {})[1]
        ws = self.app.workspace
        captured = []
        def delayed_proposal(client, scan_id=None):
            captured.append(scan_id)
            (self.root / 'notes' / 'example.md').write_text('new revision')
            self.app.scan_current(ws)
            return {'origin': 'ai', 'scan_id': scan_id, 'categories': []}
        with patch.object(ws, 'propose', side_effect=delayed_proposal):
            code, result, _ = self.req('POST', '/api/propose', {'scan_id': scan['id']})
        self.assertEqual(captured, [scan['id']])
        self.assertEqual(code, 409)
        self.assertNotIn('suggestion', result)
        self.assertFalse((self.root / 'notes' / '.wiki-workbench').exists())
    def test_cross_origin_and_missing_csrf_cannot_select_or_write(self):
        for headers in ({'Origin': 'https://untrusted.example'}, {'X-Workbench-CSRF': ''}):
            self.assertEqual(self.req('POST', '/api/workspace/select', {'path': str(self.root / 'notes')}, **headers)[0], 403)
        self.assertIsNone(self.app.workspace)
    def test_reading_independent_and_stale_tab_cannot_write_other_folder(self):
        self.select()
        payload = {'submission_id': 'f60ea9db-9d27-4a06-9c09-3570113aa5ac', 'book_title': '虚构测试书', 'content': '今天读了几页。', 'allow_ai': False}
        self.assertEqual(self.req('POST', '/api/reading/notes', payload)[0], 200)
        self.assertEqual(self.req('POST', '/api/reading/notes', payload)[1]['duplicate'], True)
        self.assertEqual(len(self.req('GET', '/api/reading')[1]['notes']), 1)
        old_id = self.ident
        (self.root / 'empty').mkdir()
        self.select('empty')
        self.ident = old_id
        self.assertEqual(self.req('POST', '/api/reading/notes', payload)[0], 409)
        self.assertEqual(list((self.root / 'empty').iterdir()), [])
    def test_modules_and_embedding_are_explicit(self):
        code, manifest, headers = self.req('GET', '/api/modules')
        self.assertEqual(code, 200)
        self.assertTrue(manifest['modules'][0]['standalone'])
        self.assertIn("frame-ancestors 'self'", headers['Content-Security-Policy'])
        self.assertNotIn('*', headers['Content-Security-Policy'])
        with self.assertRaises(ValueError):
            Application(embed_origins=['https://example.com/unsafe/path'])
    def test_fake_source_reference_is_rejected(self):
        self.select()
        scan = self.app.scan_current(self.app.workspace)
        self.app.workspace.apply(scan['id'])
        class Bad(FakeModel):
            def complete(self, *args, **kwargs):
                return {'paragraphs': [{'text': '无依据', 'source_ids': ['999']}]}
        with self.assertRaises(ValueError):
            ask(self.app.workspace, Bad(self.root), '回看')


if __name__ == '__main__':
    unittest.main()
