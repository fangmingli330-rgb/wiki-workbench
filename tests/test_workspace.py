import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from wiki_workbench.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(self.root)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_scan_is_read_only_and_filters_credentials_hidden_and_unsupported(self):
        self.write('书籍/阅读.md', '# 阅读\n这是关于学习的感悟 #学习')
        self.write('api_key.md', 'api_key=sk-secretvalue1234567890')
        self.write('.private/hidden.md', '不应读取')
        self.write('node_modules/foo.md', '不应读取')
        self.write('报表.pdf', '非支持格式')
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        report = self.workspace.scan()
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
        self.assertEqual([f['path'] for f in report['files']], ['书籍/阅读.md'])
        self.assertEqual(report['suggestion']['origin'], 'local')
        self.assertEqual(report['suggestion']['categories'][0]['title'], '书籍')
        self.assertIn('sensitive_content', [x['reason'] for x in report['skipped']])
        self.assertIn('unsupported_format', [x['reason'] for x in report['skipped']])

    def test_confirm_build_search_and_sources_preserve_original(self):
        p = self.write('研究.md', '# 原件\n第一行\n现金流比表面利润更重要。')
        original = p.read_bytes()
        report = self.workspace.scan()
        result = self.workspace.apply(report['id'])
        self.assertEqual(result['count'], 1)
        self.assertEqual(p.read_bytes(), original)
        found = self.workspace.search('现金流')['results'][0]
        self.assertEqual(found['line'], 3)
        self.assertEqual(self.workspace.document(found['source_id'])['content'], original.decode())
        self.assertEqual(self.workspace.overview()['count'], 1)
        self.assertTrue((self.root / '.wiki-workbench/Home.md').exists())

    def test_changed_and_new_sources_require_rescan(self):
        p = self.write('note.md', 'before')
        report = self.workspace.scan()
        p.write_text('after')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.workspace.apply(report['id'])
        self.assertFalse((self.root / '.wiki-workbench').exists())
        report = self.workspace.scan()
        self.write('new.md', 'new')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.workspace.apply(report['id'])

    def test_registered_source_change_never_returns_stale_evidence(self):
        p = self.write('note.md', '看好学习')
        report = self.workspace.scan()
        self.workspace.apply(report['id'])
        p.write_text('不同内容', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.workspace.document(report['files'][0]['id'])
        found = self.workspace.search('看好')
        self.assertEqual(found['results'], [])
        self.assertEqual(found['stale'], [report['files'][0]['id']])

    def test_proposal_cannot_create_paths_or_reference_unknown_sources(self):
        self.write('note.md', '普通资料')
        report = self.workspace.scan()
        with self.assertRaisesRegex(ValueError, '未登记'):
            self.workspace.apply(report['id'], {'categories': [{'title': 'x', 'file_ids': ['../../x']}]})
        proposal = {'title': 'Test', 'description': '<script>alert(1)</script>', 'categories': [{'title': '../../escape', 'file_ids': [report['files'][0]['id']]}], 'path': '../attack.md'}
        self.workspace.apply(report['id'], proposal)
        self.assertIn('\\<script\\>', (self.root / '.wiki-workbench/Home.md').read_text())
        self.assertFalse((self.root / 'attack.md').exists())

    def test_overlimits_are_explained(self):
        self.write('long.md', '文' * 51)
        self.write('good.md', 'ok')
        with patch('wiki_workbench.workspace.MAX_DOCUMENT_CHARS', 50):
            report = self.workspace.scan()
        self.assertEqual(len(report['files']), 1)
        self.assertEqual(next(x['reason'] for x in report['skipped'] if x['path'] == 'long.md'), 'document_length_limit')

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlink unavailable')
    def test_symlink_and_derived_destination_are_rejected(self):
        self.write('note.md', 'safe')
        try:
            (self.root / 'alias.md').symlink_to(self.root / 'note.md')
        except OSError:
            self.skipTest('symlink permission unavailable')
        report = self.workspace.scan()
        self.assertEqual(len(report['files']), 1)
        with tempfile.TemporaryDirectory() as external:
            (self.root / '.wiki-workbench').symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, '符号链接'):
                self.workspace.apply(report['id'])
            self.assertEqual(list(Path(external).iterdir()), [])

    def test_invalid_path_and_tampered_hidden_source_are_rejected(self):
        with self.assertRaises(ValueError):
            Workspace(None)
        self.write('note.md', 'safe')
        report = self.workspace.scan()
        self.workspace.apply(report['id'])
        secret = self.write('.env', 'secret content')
        index_path = self.root / '.wiki-workbench/index.json'
        index = json.loads(index_path.read_text())
        index['files'][0]['path'] = '.env'
        index['files'][0]['sha256'] = hashlib.sha256(secret.read_bytes()).hexdigest()
        index_path.write_text(json.dumps(index))
        with self.assertRaisesRegex(ValueError, '不合法'):
            self.workspace.document(index['files'][0]['id'])

    def test_symlink_ancestor_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder).resolve()
            real = parent / 'real'
            real.mkdir()
            (real / 'notes').mkdir()
            try:
                (parent / 'link').symlink_to(real, target_is_directory=True)
            except OSError:
                self.skipTest('symlink not available')
            with self.assertRaisesRegex(ValueError, '符号链接'):
                Workspace(parent / 'link' / 'notes')

    def test_broad_root_and_file_are_rejected(self):
        with self.assertRaises(ValueError):
            Workspace(Path.home())
        with self.assertRaises(ValueError):
            Workspace(Path(self.root.anchor))
        with self.assertRaises(ValueError):
            Workspace(self.write('file.md', 'x'))

    def test_replaced_scan_cannot_send_unconfirmed_summary(self):
        self.write('old.md', '已确认内容')
        first = self.workspace.scan()
        self.write('new.md', '尚未确认的新内容')
        second = self.workspace.scan()
        self.assertNotEqual(first['id'], second['id'])
        client = Mock()
        with self.assertRaisesRegex(ValueError, '扫描清单已变化'):
            self.workspace.propose(client, scan_id=first['id'])
        client.complete.assert_not_called()

    def test_concurrent_scan_does_not_change_proposal_transmission(self):
        self.write('old.md', '已确认内容')
        first = self.workspace.scan()
        entered, proceed = threading.Event(), threading.Event()
        original_read = self.workspace._read_source
        def paused_read(rel):
            if threading.current_thread().name == 'proposer':
                entered.set()
                self.assertTrue(proceed.wait(3))
            return original_read(rel)
        client = Mock()
        client.complete.return_value = {'categories': [{'title': '旧资料', 'file_ids': [first['files'][0]['id']]}]}
        results, errors = [], []
        def generate():
            try:
                results.append(self.workspace.propose(client, scan_id=first['id']))
            except Exception as exc:
                errors.append(exc)
        with patch.object(self.workspace, '_read_source', side_effect=paused_read):
            thread = threading.Thread(target=generate, name='proposer')
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.write('new.md', '未确认内容不能随旧请求发出')
                second = self.workspace.scan()
            finally:
                proceed.set()
                thread.join(3)
        self.assertFalse(errors)
        self.assertEqual(results[0]['scan_id'], first['id'])
        sent = json.loads(client.complete.call_args.args[1])
        self.assertEqual([row['source_id'] for row in sent], [first['files'][0]['id']])
        self.assertNotIn('未确认内容', json.dumps(sent, ensure_ascii=False))
        self.assertEqual(self.workspace._scan['id'], second['id'])
        with self.assertRaisesRegex(ValueError, '另一版本'):
            self.workspace.apply(second['id'], results[0])
        self.assertFalse((self.root / '.wiki-workbench').exists())

    def test_ai_proposal_without_scan_id_or_wrong_scan_is_rejected(self):
        self.write('note.md', 'safe')
        report = self.workspace.scan()
        proposal = {'origin': 'ai', 'categories': []}
        with self.assertRaisesRegex(ValueError, '缺少'):
            self.workspace.apply(report['id'], proposal)
        proposal = {'origin': 'local', 'categories': [], 'scan_id': 'wrong'}
        with self.assertRaisesRegex(ValueError, '另一版本'):
            self.workspace.apply(report['id'], proposal)
        self.assertFalse((self.root / '.wiki-workbench').exists())

    def test_failed_apply_does_not_promote_unseen_scan(self):
        self.write('old.md', 'reviewed')
        first = self.workspace.scan()
        self.write('new.md', 'not yet previewed')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.workspace.apply(first['id'])
        self.assertIsNone(self.workspace._scan)
        client = Mock()
        with self.assertRaisesRegex(ValueError, '先扫描'):
            self.workspace.propose(client)
        client.complete.assert_not_called()
        self.assertFalse((self.root / '.wiki-workbench').exists())

    def test_ai_proposal_has_only_explicit_filtered_summaries_and_known_ids(self):
        self.write('材料.md', '这是一个资料摘要。' * 200)
        self.write('secret.txt', 'password=abcdef123456789')
        report = self.workspace.scan()
        sid = report['files'][0]['id']
        client = Mock()
        client.complete.return_value = {'title': '学习', 'description': 'AI建议', 'categories': [{'title': '主题', 'file_ids': [sid]}]}
        proposal = self.workspace.propose(client)
        self.assertEqual(proposal['origin'], 'ai')
        sent = json.loads(client.complete.call_args.args[1])
        self.assertEqual(len(sent), 1)
        self.assertLessEqual(len(sent[0]['excerpt']), 500)
        self.assertNotIn('path', sent[0])
        self.assertFalse((self.root / '.wiki-workbench').exists())


if __name__ == '__main__':
    unittest.main()
