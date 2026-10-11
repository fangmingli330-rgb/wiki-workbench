"""Export reviewed source only. Never archive a workspace, Git history or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# Add a new file only after reviewing it. Never expand this from a folder glob.
PUBLISHED = (
    '.gitignore', 'LICENSE', 'README.md', 'pyproject.toml',
    'docs/MODULE_API.md', 'docs/OPEN_SOURCE_REVIEW.md',
    'examples/host.html', 'examples/notes/学习记录.txt', 'examples/notes/阅读方法.md',
    'tests/test_model.py', 'tests/test_modules.py', 'tests/test_reading.py',
    'tests/test_release.py', 'tests/test_server.py', 'tests/test_ui.js', 'tests/test_workspace.py',
    'tools/build_release.py',
    'wiki_workbench/__init__.py', 'wiki_workbench/__main__.py',
    'wiki_workbench/locking.py', 'wiki_workbench/model.py', 'wiki_workbench/modules.py',
    'wiki_workbench/reading.py', 'wiki_workbench/reading_ai.py', 'wiki_workbench/server.py',
    'wiki_workbench/workspace.py', 'wiki_workbench/static/app.js',
    'wiki_workbench/static/index.html', 'wiki_workbench/static/style.css',
)


def candidates(root=ROOT):
    files = []
    for rel in sorted(PUBLISHED):
        path = root / rel
        current = root
        for part in Path(rel).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('源码路径含符号链接，拒绝打包：' + rel)
        if not path.is_file():
            raise ValueError('发布源码缺失：' + rel)
        raw = path.read_bytes()
        text = raw.decode('utf-8')
        if re.search(r'/(?:Users|home)/[A-Za-z0-9_.-]+/', text):
            raise ValueError('源码含个人绝对路径：' + rel)
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', text):
            raise ValueError('源码含私钥内容：' + rel)
        if re.search(r'\bsk-(?:proj-|svcacct-|ant-api\d+-)?[A-Za-z0-9_-]{28,}\b', text):
            raise ValueError('源码疑似含真实API key：' + rel)
        files.append((rel, raw))
    return files


def build(output=None, root=ROOT):
    files = candidates(root)
    manifest = {'version': '0.1.1', 'files': [{'path': p, 'sha256': hashlib.sha256(raw).hexdigest()} for p, raw in files],
                'excluded': ['workspaces', 'credentials', 'model_responses', 'git_history', 'local_paths']}
    if output:
        output = Path(output)
        if output.exists():
            raise ValueError('发布包已存在，请使用新文件名，避免覆盖已审阅的包')
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
            for p, raw in files:
                archive.writestr('wiki-workbench/' + p, raw)
            archive.writestr('wiki-workbench/RELEASE_MANIFEST.json', json.dumps(manifest, indent=2))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='只导出已审核的程序源码和虚构示例')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.check and args.output:
        parser.error('--check 不写文件，请勿同时指定 --output')
    try:
        result = build(None if args.check else args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as exc:
        parser.exit(1, str(exc) + '\n')
