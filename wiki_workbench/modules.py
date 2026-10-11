"""Versioned built-in module registry. No workspace-supplied code is executed."""
import copy

API_VERSION = 1
_MODULES = ({
    'id': 'reading', 'title': '每日阅读', 'version': '0.1.0',
    'entry': '/#reading', 'standalone': True, 'api_version': API_VERSION,
    'capabilities': ['reading.read', 'reading.write', 'model.optional'],
},)


def builtin_modules():
    return copy.deepcopy(list(_MODULES))


def module_by_id(module_id):
    for module in builtin_modules():
        if module['id'] == module_id:
            return module
    raise ValueError('模块未安装或不在内置白名单')
