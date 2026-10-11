"""Offline reading journal tests, with no model calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from wiki_workbench import reading as notes
from wiki_workbench import reading_ai as processor
from wiki_workbench.model import BudgetBlocked
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import threading


class FakeClient:
    def __init__(self, callback=None, enabled=True):
        self.callback = callback
        self.enabled = enabled
        self.calls = []

    def status(self):
        return {'enabled': self.enabled, 'configured': True}

    def complete(self, system, user, **kwargs):
        context = json.loads(user)
        self.calls.append((context, kwargs))
        if self.callback:
            return self.callback(context)
        return output(context)


def output(context, **changes):
    return {'summary': '这次小记关注阅读节奏。',
            'reflections': [{'text': '关注自己的理解。', 'quote': context['current']['content']}],
            'questions': [], 'topics': ['阅读'], 'reading_location': None,
            'excerpts': [], 'changes': [], **changes}

class ReadingNotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
    def payload(self, **changes):
        return {'submission_id': str(uuid.uuid4()), 'book_title': '虚构读书样本', 'content': '今天想到：阅读需要考虑节奏。\n  保留空白\n', 'allow_ai': True, **changes}
    def test_empty_read_is_side_effect_free(self):
        self.assertEqual(notes.overview(self.vault)['notes'], [])
        self.assertEqual(list(self.vault.iterdir()), [])
    def test_save_exact_bytes_and_idempotency(self):
        payload = self.payload()
        result = notes.submit(payload, self.vault)
        original = self.vault / result['note']['original_rel']
        self.assertEqual(original.read_bytes(), payload['content'].encode())
        self.assertEqual(result['note']['analysis_status'], 'pending')
        self.assertIsNone(result['note']['analysis'])
        self.assertTrue(notes.submit(payload, self.vault)['duplicate'])
        self.assertEqual(len(notes.overview(self.vault)['notes']), 1)
        with self.assertRaisesRegex(ValueError, '其他内容'):
            notes.submit({**payload, 'content': '不同内容'}, self.vault)
        self.assertEqual(original.read_bytes(), payload['content'].encode())
    def test_multiple_days_and_same_day_same_book(self):
        with patch.object(notes, 'now', return_value='2026-10-08T16:01:00+00:00'):
            first = notes.submit(self.payload(), self.vault)
        with patch.object(notes, 'now', return_value='2026-10-09T16:01:00+00:00'):
            notes.submit(self.payload(book_id=first['book']['id'], book_title='', content='第二天'), self.vault)
            notes.submit(self.payload(book_id=first['book']['id'], book_title='', content='同日第二条'), self.vault)
        view = notes.overview(self.vault)
        self.assertEqual(len(view['books']), 1)
        self.assertEqual(view['books'][0]['note_count'], 3)
        from datetime import datetime
        expected = {datetime.fromisoformat(stamp).astimezone().date().isoformat() for stamp in ('2026-10-08T16:01:00+00:00', '2026-10-09T16:01:00+00:00')}
        self.assertEqual({n['date'] for n in view['notes']}, expected)
    def test_local_only_and_book_status(self):
        saved = notes.submit(self.payload(allow_ai=False), self.vault)
        self.assertEqual(saved['note']['analysis_status'], 'local')
        self.assertFalse(notes.overview(self.vault)['ai_enabled'])
        notes.update_book({'book_id': saved['book']['id'], 'status': 'finished'}, self.vault)
        self.assertEqual(notes.overview(self.vault)['books'][0]['status'], 'finished')
        with self.assertRaises(ValueError):
            notes.update_book({'book_id': saved['book']['id'], 'status': 'approved'}, self.vault)
    def test_search_all_books_without_ai(self):
        notes.submit(self.payload(allow_ai=False), self.vault)
        notes.submit(self.payload(book_title='另一本书', content='理解与时间'), self.vault)
        self.assertEqual(len(notes.overview(self.vault, 'all')['notes']), 2)
        self.assertEqual(len(notes.overview(self.vault, 'all', '节奏')['notes']), 1)
    def test_title_cannot_become_path_and_unsupported_paths_rejected(self):
        saved = notes.submit(self.payload(book_title='../../outside'), self.vault)
        self.assertIn('原文/', saved['note']['original_rel'])
        with self.assertRaises(ValueError):
            notes.submit(self.payload(path='/tmp/secret'), self.vault)
        with self.assertRaises(ValueError):
            notes._path(self.vault, '../outside')
    def test_symlink_protection(self):
        outside = self.vault / 'outside'
        outside.mkdir()
        base = self.vault / notes.FOLDER
        base.parent.mkdir(parents=True)
        base.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            notes.submit(self.payload(), self.vault)
        self.assertEqual(list(outside.iterdir()), [])
        base.unlink()
        notes.submit(self.payload(), self.vault)
        state_path = base / notes.STATE
        data = state_path.read_bytes()
        state_path.unlink()
        elsewhere = outside / 'state.json'
        elsewhere.write_bytes(data)
        state_path.symlink_to(elsewhere)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            notes.overview(self.vault)
    def test_tampered_original_is_visible(self):
        saved = notes.submit(self.payload(), self.vault)
        path = self.vault / saved['note']['original_rel']
        path.chmod(0o600)
        path.write_text('changed')
        found = notes.overview(self.vault)['notes'][0]
        self.assertEqual(found['analysis_status'], 'failed')
        self.assertEqual(found['content'], '')
        self.assertIn('校验失败', found['error'])
    def test_bad_ai_result_does_not_hide_original(self):
        saved = notes.submit(self.payload(), self.vault)
        with notes.transaction(self.vault) as state:
            note = state['notes'][saved['note']['id']]
            note.update(analysis_status='ready', analysis_path='AI整理/missing.json', analysis_sha256='bad')
        found = notes.overview(self.vault)['notes'][0]
        self.assertEqual(found['content'], saved['note']['content'])
        self.assertEqual(found['analysis_status'], 'failed')
        self.assertIsNone(found['analysis'])

    def test_validation_does_not_create_anything(self):
        for payload in (self.payload(content=' '), self.payload(allow_ai='yes'), self.payload(content='x'*20001)):
            with self.assertRaises(ValueError):
                notes.submit(payload, self.vault)
        self.assertEqual(list(self.vault.iterdir()), [])

    def test_concurrent_saves_preserve_all_originals(self):
        payloads = [self.payload(content=f'虚构感悟第{i}条') for i in range(12)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda data: notes.submit(data, self.vault), payloads))
        self.assertEqual(len(notes.overview(self.vault)['notes']), 12)
        self.assertEqual(len(notes.overview(self.vault)['books']), 1)
        for result, payload in zip(results, payloads):
            self.assertEqual((self.vault / result['note']['original_rel']).read_bytes(), payload['content'].encode())


class ReadingAITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def save(self, content='今天反思：慢一点更有助于理解。', allow_ai=True,
             title='虚构的阅读验收书', stamp='2026-10-08T10:00:00+00:00'):
        with patch.object(notes, 'now', return_value=stamp):
            return notes.submit({'submission_id': str(uuid.uuid4()), 'book_title': title,
                                 'content': content, 'allow_ai': allow_ai}, self.root)['note']

    def test_empty_read_and_disabled_model_are_side_effect_free(self):
        client = FakeClient()
        self.assertEqual(processor.process_pending(self.root, client)['processed'], 0)
        self.assertEqual(list(self.root.iterdir()), [])
        self.save()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        client.enabled = False
        self.assertEqual(processor.process_pending(self.root, client)['processed'], 0)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(client.calls, [])

    def test_local_only_never_sent(self):
        self.save(allow_ai=False)
        client = FakeClient()
        self.assertEqual(processor.process_pending(self.root, client)['processed'], 0)
        self.assertEqual(client.calls, [])

    def test_success_original_retained_ai_separate_and_one_call(self):
        note = self.save()
        client = FakeClient()
        result = processor.process_pending(self.root, client)
        self.assertEqual(result['ready'], 1)
        view = notes.overview(self.root)['notes'][0]
        self.assertEqual(view['content'], note['content'])
        self.assertIn('AI 整理', view['analysis']['attribution'])
        self.assertEqual(client.calls[0][1]['purpose'], 'reading:' + note['id'])
        self.assertEqual(client.calls[0][1]['max_tokens'], 2400)
        self.assertEqual(processor.process_pending(self.root, client)['processed'], 0)
        self.assertEqual(len(client.calls), 1)

    def test_scoped_context_excludes_other_book_local_only_and_future(self):
        for day in range(1, 6):
            self.save(content=f'过去感悟{day}', stamp=f'2026-10-0{day}T10:00:00+00:00')
        self.save(content='跨书记录', title='另一本虚构书', stamp='2026-10-06T10:00:00+00:00')
        self.save(content='只在本地保存', allow_ai=False, stamp='2026-10-07T10:00:00+00:00')
        self.save(content='未来记录', stamp='2026-10-09T10:00:00+00:00')
        note = self.save()
        state = notes._state(self.root)
        context = processor._context(self.root, state['notes'][note['id']], state)
        self.assertEqual([row['content'] for row in context['prior']], ['过去感悟5', '过去感悟4', '过去感悟3'])

    def test_sensitive_input_saved_but_not_sent(self):
        # Synthetic credential-shaped fixture, assembled only inside this test.
        synthetic_key = 'sk-' + 'abcdefghijklmnopqrstuvwxyz123456'
        for content in ('API_KEY=' + synthetic_key, '我的持仓数量：123股', '我持有股票 123股'):
            self.save(content=content)
            client = FakeClient()
            self.assertEqual(processor.process_pending(self.root, client)['blocked'], 1)
            self.assertEqual(client.calls, [])
        self.assertTrue(all(n['analysis_status'] == 'blocked' for n in notes.overview(self.root)['notes']))

    def test_invalid_quote_no_automatic_retry(self):
        self.save()
        client = FakeClient(lambda context: output(context, reflections=[{'text': '假的反思', 'quote': '捏造的引用'}]))
        self.assertEqual(processor.process_pending(self.root, client)['failed'], 1)
        self.assertEqual(processor.process_pending(self.root, client)['processed'], 0)
        self.assertEqual(len(client.calls), 1)
        self.assertIn('慢一点', notes.overview(self.root)['notes'][0]['content'])

    def test_network_uncertain_no_automatic_retry(self):
        self.save()
        def fail(context):
            raise TimeoutError('模拟网络中断，不应出现在界面原始错误里')
        client = FakeClient(fail)
        self.assertEqual(processor.process_pending(self.root, client)['failed'], 1)
        self.assertEqual(processor.process_pending(self.root, client)['processed'], 0)
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn('模拟网络', notes.overview(self.root)['notes'][0]['error'])

    def test_budget_rejection_can_resume_without_losing_original(self):
        self.save()
        def budget(context):
            raise BudgetBlocked('budget')
        client = FakeClient(budget)
        self.assertEqual(processor.process_pending(self.root, client)['blocked'], 1)
        state = notes._state(self.root)
        self.assertEqual(next(iter(state['notes'].values()))['attempts'], 0)
        client.callback = None
        self.assertEqual(processor.process_pending(self.root, client)['ready'], 1)

    def test_interrupted_attempt_is_not_reissued(self):
        note = self.save()
        with notes.transaction(self.root) as state:
            state['notes'][note['id']].update(attempts=1, analysis_status='processing')
        client = FakeClient()
        self.assertEqual(processor.process_pending(self.root, client)['failed'], 1)
        self.assertEqual(client.calls, [])

    def test_tampered_original_never_sent(self):
        note = self.save()
        path = self.root / note['original_rel']
        path.chmod(0o600)
        path.write_text('被修改的原文')
        client = FakeClient()
        self.assertEqual(processor.process_pending(self.root, client)['failed'], 1)
        self.assertEqual(client.calls, [])

    def test_model_does_not_block_saving_or_book_update(self):
        note = self.save()
        def during_model(context):
            with ThreadPoolExecutor(max_workers=1) as pool:
                saved = pool.submit(self.save, content='整理期间的新记录', allow_ai=False).result(timeout=2)
                self.assertTrue(saved['id'])
            notes.update_book({'book_id': note['book_id'], 'status': 'finished'}, self.root)
            return output(context)
        client = FakeClient(during_model)
        self.assertEqual(processor.process_pending(self.root, client)['ready'], 1)
        view = notes.overview(self.root)
        self.assertEqual(len(view['notes']), 2)
        self.assertEqual(view['books'][0]['status'], 'finished')

    def test_two_workers_never_buy_same_note_twice(self):
        self.save()
        arrived = threading.Event()
        release = threading.Event()
        def wait(context):
            arrived.set()
            self.assertTrue(release.wait(3))
            return output(context)
        first = FakeClient(wait)
        second = FakeClient()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(processor.process_pending, self.root, first)
            self.assertTrue(arrived.wait(2))
            result = processor.process_pending(self.root, second)
            self.assertTrue(result['already_running'])
            release.set()
            self.assertEqual(future.result(timeout=3)['ready'], 1)
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(second.calls, [])

    def test_explicit_excerpt_location_and_old_note_quotes(self):
        old = self.save(content='以前以为读快就足够。', stamp='2026-10-07T10:00:00+00:00')
        current = self.save(content='读到第2章。摘录：“这是虚构测试原句。”\n现在觉得需要慢下来。')
        state = notes._state(self.root)
        context = processor._context(self.root, state['notes'][current['id']], state)
        value = output(context, reading_location={'text': '第2章', 'quote': '读到第2章。'},
                       excerpts=[{'quote': '这是虚构测试原句。'}],
                       changes=[{'text': '阅读节奏变化', 'current_quote': '现在觉得需要慢下来。',
                                 'prior_note_id': old['id'], 'prior_quote': '以前以为读快就足够。'}])
        self.assertEqual(processor.validate(value, context)['changes'][0]['prior_note_id'], old['id'])
        for change in ({'prior_note_id': '不存在'}, {'current_quote': '杜撰'}, {'prior_quote': '杜撰'}):
            bad = copy.deepcopy(value)
            bad['changes'][0].update(change)
            with self.assertRaises(ValueError):
                processor.validate(bad, context)
        bad = copy.deepcopy(value)
        bad['excerpts'] = [{'quote': '现在觉得需要慢下来。'}]
        with self.assertRaises(ValueError):
            processor.validate(bad, context)


if __name__ == '__main__':
    unittest.main()


