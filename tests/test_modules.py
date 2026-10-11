import tempfile
from pathlib import Path
import unittest
from wiki_workbench.modules import builtin_modules, module_by_id


class ModuleTests(unittest.TestCase):
    def test_reading_is_standalone_versioned_and_has_no_investment_dependency(self):
        module = module_by_id('reading')
        self.assertTrue(module['standalone'])
        self.assertEqual(module['api_version'], 1)
        self.assertEqual(module['capabilities'], ['reading.read', 'reading.write', 'model.optional'])
        self.assertEqual(module['entry'], '/#reading')

    def test_registry_is_copy_and_unknown_module_rejected(self):
        rows = builtin_modules()
        rows[0]['capabilities'].append('unsafe.execute')
        self.assertNotIn('unsafe.execute', builtin_modules()[0]['capabilities'])
        with self.assertRaises(ValueError):
            module_by_id('../module.py')

    def test_files_in_workspace_never_register_or_execute_plugins(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / 'reading_plugin.py'
            plugin.write_text('raise RuntimeError("must never run")')
            self.assertEqual([item['id'] for item in builtin_modules()], ['reading'])
