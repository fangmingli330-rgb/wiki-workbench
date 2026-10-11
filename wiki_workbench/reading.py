"""Byte-preserved reading journals with independent, optional AI organization.

Book identity is independent of its title. No model call happens during saving
or reading. This module is independent of any domain workbench.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid
from .locking import exclusive, redirected

FOLDER = Path('.wiki-workbench/modules/reading')
STATE = '.阅读状态.json'
MAX_CONTENT_CHARS = 20_000
MAX_BYTES = 100_000
_STATUSES = {'reading', 'paused', 'finished'}


def now():
    return datetime.now().astimezone().isoformat()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError('记录编号必须是 UUID')
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise ValueError('记录编号必须是 UUID') from None
    if parsed != value.lower():
        raise ValueError('记录编号必须是完整 UUID')
    return parsed


def _string(value, name, maximum, required=True):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise ValueError(name + '格式或长度无效')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError(name + '必须是有效文字') from None
    if required and not value.strip():
        raise ValueError(name + '不能为空')
    return value


def _path(vault, relative='', create=False):
    vault = Path(vault).absolute()
    if redirected(vault) or not vault.is_dir():
        raise ValueError('工作区目录无效')
    base = vault / FOLDER
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('阅读资料路径无效')
    current = vault
    parts = (*FOLDER.parts, *relative.parts)
    for n, part in enumerate(parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if create and n < len(parts)-1:
                current.mkdir(mode=0o700, exist_ok=True)
                if redirected(current) or not current.is_dir():
                    raise ValueError('阅读目录无效')
            continue
        if redirected(current):
            raise ValueError('阅读目录或文件不能是符号链接')
        if n < len(parts)-1 and not stat.S_ISDIR(mode):
            raise ValueError('阅读目录无效')
    return base / relative


def _read(path, limit=4_000_000):
    if redirected(path):
        raise ValueError('阅读资料不能是符号链接')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('阅读资料格式或长度无效')
        return stream.read(limit+1)


def _state(vault):
    path = _path(vault, STATE)
    if not path.exists():
        return {'schema_version': 1, 'books': {}, 'notes': {}, 'ai_enabled': False}
    try:
        value = json.loads(_read(path))
        if value.get('schema_version') != 1 or not isinstance(value.get('books'), dict) or not isinstance(value.get('notes'), dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, OSError):
        raise ValueError('阅读台账异常，已停止，原文仍保留') from None


def _atomic(vault, relative, raw, *, immutable=False):
    path = _path(vault, relative, create=True)
    fd, pending = tempfile.mkstemp(prefix='.reading-pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            os.chmod(pending, 0o444)
            try:
                os.link(pending, path)
            except FileExistsError:
                if _read(path) != raw:
                    raise ValueError('原文或回执已存在且内容不同，未覆盖') from None
        else:
            _path(vault, relative)
            os.replace(pending, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(pending):
            if os.name == 'nt':
                os.chmod(pending, 0o600)
            os.unlink(pending)
            if immutable and os.name == 'nt' and path.exists():
                # NTFS hard links share readonly attributes. Restore the final
                # immutable file after removing its temporary hard link.
                os.chmod(path, 0o444)


@contextmanager
def transaction(vault):
    lockpath = _path(vault, '.reading.lock', create=True)
    with exclusive(lockpath):
        state = _state(vault)
        yield state
        _atomic(vault, STATE, _json(state))


def content(vault, note):
    raw = _read(_path(vault, note['original_path']), MAX_BYTES)
    if _sha(raw) != note['sha256']:
        raise ValueError('阅读原文校验失败，未处理，需人工核对')
    return raw.decode('utf-8')


def _public_note(vault, note, books):
    row = {k: note.get(k) for k in ('id', 'book_id', 'date', 'created_at', 'allow_ai', 'analysis_status', 'error')}
    row.update(book_title=books[note['book_id']]['title'], analysis=None,
               original_rel=(FOLDER / note['original_path']).as_posix())
    try:
        row['content'] = content(vault, note)
    except (ValueError, OSError, UnicodeError, KeyError):
        row.update(content='', analysis_status='failed', error='阅读原文校验失败，需人工核对')
        return row
    try:
        if note.get('analysis_path'):
            raw = _read(_path(vault, note['analysis_path']))
            if _sha(raw) != note.get('analysis_sha256'):
                raise ValueError('阅读整理结果校验失败，需人工核对')
            row['analysis'] = json.loads(raw)
    except (ValueError, OSError, UnicodeError, KeyError):
        row.update(analysis=None, analysis_status='failed', error='阅读整理结果校验失败，原文仍可阅读，需人工核对')
    return row


def _public_books(state):
    rows = []
    for book in state['books'].values():
        notes = [n for n in state['notes'].values() if n['book_id'] == book['id']]
        rows.append({**book, 'note_count': len(notes), 'last_read_at': max((n['created_at'] for n in notes), default='')})
    return sorted(rows, key=lambda b: b['last_read_at'], reverse=True)


def overview(vault, book_id='', query=''):
    state = _state(vault)
    books = _public_books(state)
    if book_id and book_id != 'all' and book_id not in state['books']:
        raise ValueError('这本书不存在')
    selected = '' if book_id == 'all' else book_id or (books[0]['id'] if books else '')
    query = _string(query, '搜索词', 200, required=False).strip().casefold()
    rows = []
    for note in sorted(state['notes'].values(), key=lambda n: (n['created_at'], n['id']), reverse=True):
        if selected and note['book_id'] != selected:
            continue
        public = _public_note(vault, note, state['books'])
        searchable = public['content'] + public['book_title'] + json.dumps(public['analysis'], ensure_ascii=False)
        if not query or query in searchable.casefold():
            rows.append(public)
    return {'ok': True, 'books': books, 'notes': rows, 'selected_book_id': selected, 'ai_enabled': bool(state.get('ai_enabled'))}


def submit(payload, vault):
    if not isinstance(payload, dict) or set(payload) - {'submission_id', 'book_id', 'book_title', 'content', 'allow_ai'}:
        raise ValueError('阅读字段无效，不能指定保存路径')
    ident = _uuid(payload.get('submission_id'))
    body = _string(payload.get('content'), '感悟', MAX_CONTENT_CHARS)
    raw = body.encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('感悟超过保存长度限制')
    if type(payload.get('allow_ai')) is not bool:
        raise ValueError('请明确选择是否允许 AI 整理')
    bid = payload.get('book_id') or ''
    if bid:
        bid = _uuid(bid)
    title = _string(payload.get('book_title', ''), '书名', 200, required=not bid).strip()
    fingerprint = _sha(_json({'book_id': bid, 'book_title': title, 'content': body, 'allow_ai': payload['allow_ai']}))
    with transaction(vault) as state:
        existing = state['notes'].get(ident)
        if existing:
            if existing['submission_sha256'] != fingerprint:
                raise ValueError('同一记录编号已保存其他内容，请勿覆盖原文')
            note = _public_note(vault, existing, state['books'])
            book = next(b for b in _public_books(state) if b['id'] == existing['book_id'])
            return {'ok': True, 'status': 'saved', 'note': note, 'book': book, 'duplicate': True}
        if bid:
            if bid not in state['books']:
                raise ValueError('所选书籍不存在')
        else:
            # Reuse the exact title while keeping file paths unrelated to it.
            matches = [b for b in state['books'].values() if b['title'].casefold() == title.casefold()]
            bid = matches[0]['id'] if matches else str(uuid.uuid5(uuid.UUID(ident), 'reading-book'))
            if not matches:
                state['books'][bid] = {'id': bid, 'title': title, 'status': 'reading'}
        stamp = now()
        day = datetime.fromisoformat(stamp).astimezone().date().isoformat()
        rel = f'原文/{ident}.md'
        _atomic(vault, rel, raw, immutable=True)
        note = {'id': ident, 'book_id': bid, 'created_at': stamp, 'date': day,
                'original_path': rel, 'sha256': _sha(raw), 'submission_sha256': fingerprint,
                'allow_ai': payload['allow_ai'], 'analysis_status': 'pending' if payload['allow_ai'] else 'local',
                'attempts': 0, 'error': '', 'analysis_path': '', 'analysis_sha256': ''}
        state['notes'][ident] = note
        state['ai_enabled'] = payload['allow_ai']
        book = next(b for b in _public_books(state) if b['id'] == bid)
        public = _public_note(vault, note, state['books'])
    return {'ok': True, 'status': 'saved', 'note': public, 'book': book, 'duplicate': False}


def update_book(payload, vault):
    if not isinstance(payload, dict) or set(payload) - {'book_id', 'status'}:
        raise ValueError('书籍字段无效')
    bid = _uuid(payload.get('book_id'))
    if payload.get('status') not in _STATUSES:
        raise ValueError('书籍状态无效')
    with transaction(vault) as state:
        if bid not in state['books']:
            raise ValueError('这本书不存在')
        state['books'][bid]['status'] = payload['status']
        book = next(b for b in _public_books(state) if b['id'] == bid)
    return {'ok': True, 'book': book}
