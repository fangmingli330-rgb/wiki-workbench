import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('release_tool', Path(__file__).resolve().parents[1] / 'tools' / 'build_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for rel in release.PUBLISHED:
            self.write(rel, 'Demo source fixture')
    def write(self, rel, value):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)
        return p
    def test_only_source_and_explicit_examples_are_exported(self):
        self.write('.wiki-workbench/private/model.json', '{"api_key":"never-export-me"}')
        self.write('wiki_workbench/.wiki-workbench/private/model.py', 'private data')
        self.write('.env', 'private data')
        self.write('真实资料/report.md', 'private report')
        self.write('examples/notes/私人笔记.md', 'my private thought')
        self.write('docs/个人记录.md', 'my private record')
        names = {p for p, _ in release.candidates(self.root)}
        self.assertEqual(names, set(release.PUBLISHED))
    def test_check_is_read_only_and_archive_has_no_history(self):
        before = sorted(str(p) for p in self.root.rglob('*'))
        release.build(root=self.root)
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob('*')))
        out = self.root / 'out.zip'
        manifest = release.build(out, root=self.root)
        with zipfile.ZipFile(out) as archive:
            self.assertEqual(len(archive.namelist()), len(manifest['files']) + 1)
            self.assertFalse(any('/.git/' in p for p in archive.namelist()))
        with self.assertRaises(ValueError):
            release.build(out, root=self.root)
    def test_credential_and_machine_paths_block_release(self):
        for text in ('sk-' + 'a' * 40, '/' + 'Users' + '/someone/private/config'):
            self.write('wiki_workbench/server.py', text)
            with self.assertRaises(ValueError):
                release.build(root=self.root)


if __name__ == '__main__':
    unittest.main()
