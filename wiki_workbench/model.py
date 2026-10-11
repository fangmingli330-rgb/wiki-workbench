"""Explicitly configured OpenAI-compatible client with local estimated-cost reservations.

No ambient credentials are read. Prices are user-configurable estimates, not provider bills.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .locking import exclusive, LockBusy
from .workspace import atomic_json, secure_directory, sensitive, valid_root

DEFAULTS = {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-chat', 'enabled': False,
            'budget_cny': 10.0, 'max_call_cny': 0.5,
            'input_per_million_cny': 5.0, 'output_per_million_cny': 15.0}


class BudgetBlocked(ValueError):
    """Request was rejected before any network call; retry later is safe."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('模型服务重定向已拒绝，未向重定向地址发送密钥')


def _endpoint(base: str) -> str:
    parts = urllib.parse.urlsplit(base.strip())
    if parts.username or parts.password or parts.query or parts.fragment or not parts.hostname:
        raise ValueError('API 地址格式不正确')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and parts.hostname in {'127.0.0.1', 'localhost', '::1'}):
        raise ValueError('API 必须使用 HTTPS；只有本机服务允许 HTTP')
    path = parts.path.rstrip('/')
    if not path.endswith('/chat/completions'):
        path += '/chat/completions'
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, '', ''))


class ModelClient:
    def __init__(self, workspace_path: str | Path):
        self.root = valid_root(workspace_path)

    @property
    def private(self) -> Path:
        return secure_directory(self.root, '.wiki-workbench', 'private')

    def _read(self, name: str, default):
        path = self.private / name
        if path.is_symlink():
            raise ValueError('模型配置不能使用符号链接')
        if not path.exists():
            return default
        if path.stat().st_size > 5_000_000:
            raise ValueError('模型本地记录超出限制')
        return json.loads(path.read_text(encoding='utf-8'))

    def _config(self) -> dict:
        return {**DEFAULTS, **self._read('model.json', {})}

    def _init(self) -> Path:
        directory = secure_directory(self.root, '.wiki-workbench', 'private', create=True)
        os.chmod(directory, 0o700)
        ignore = directory / '.gitignore'
        if ignore.is_symlink():
            raise ValueError('模型私有目录的忽略文件不能是符号链接')
        if ignore.exists():
            if ignore.stat().st_size > 8192 or '*' not in ignore.read_text(encoding='utf-8').splitlines():
                raise ValueError('私密配置目录的 Git 忽略规则无效，请先恢复仅含星号的 .gitignore')
        else:
            fd = os.open(ignore, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                out.write('*\n')
        return directory

    def _ledger(self) -> dict:
        return self._read('usage.json', {'calls': []})

    def status(self) -> dict:
        cfg = self._config()
        ledger = self._ledger()
        used = sum(float(row.get('charged_estimate_cny', row.get('reserved_cny', 0))) for row in ledger['calls'])
        return {**{key: cfg[key] for key in DEFAULTS}, 'configured': bool(cfg.get('api_key')),
                'key_present': bool(cfg.get('api_key')), 'usage_cny': round(used, 6),
                'remaining_cny': round(max(0.0, float(cfg['budget_cny']) - used), 6),
                'calls': len(ledger['calls']),
                'uncertain_calls': sum(row.get('status') in {'reserved', 'uncertain'} for row in ledger['calls']),
                'cost_basis': '按你设置的每百万 token 价格估算；不是供应商账单。未知结果保留预留额度，不自动重试。'}

    def configure(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError('模型设置格式不正确')
        cfg = self._config()
        original_url = urllib.parse.urlsplit(cfg['base_url'])
        for key in DEFAULTS:
            if key in payload:
                cfg[key] = payload[key]
        if type(cfg['enabled']) is not bool:
            raise ValueError('模型启用开关必须为布尔值')
        cfg['base_url'] = str(cfg['base_url']).strip().rstrip('/')
        _endpoint(cfg['base_url'])
        updated_url = urllib.parse.urlsplit(cfg['base_url'])
        if (original_url.scheme, original_url.hostname, original_url.port) != (updated_url.scheme, updated_url.hostname, updated_url.port) and cfg.get('api_key') and not payload.get('api_key') and payload.get('clear_api_key') is not True:
            raise ValueError('切换模型服务地址时，请同时填入新服务密钥或清除旧密钥')
        cfg['model'] = str(cfg['model']).strip()
        if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}', cfg['model']):
            raise ValueError('模型名称格式不正确')
        for key in ('budget_cny', 'max_call_cny', 'input_per_million_cny', 'output_per_million_cny'):
            try:
                value = float(cfg[key])
            except (ValueError, TypeError) as exc:
                raise ValueError('预算和价格必须为有效数字') from exc
            if not math.isfinite(value) or value <= 0 or value > 10000:
                raise ValueError('预算和价格必须在 0 到 10000 之间')
            cfg[key] = value
        if payload.get('clear_api_key') is True:
            cfg.pop('api_key', None)
        elif payload.get('api_key'):
            key = str(payload['api_key']).strip()
            if len(key) > 1024 or any(c in key for c in '\r\n'):
                raise ValueError('API 密钥格式不正确')
            cfg['api_key'] = key
        private = self._init()
        with exclusive(private / 'model.lock', timeout=2):
            atomic_json(private / 'model.json', cfg)
        return self.status()

    def _finish(self, call_id: str, values: dict) -> None:
        private = self.private
        with exclusive(private / 'model.lock', timeout=10):
            ledger = self._ledger()
            row = next(row for row in ledger['calls'] if row['id'] == call_id)
            row.update(values)
            atomic_json(private / 'usage.json', ledger)

    def complete(self, system: str, user: str, purpose: str, max_tokens: int = 1800) -> dict:
        if not isinstance(system, str) or not isinstance(user, str) or len(system) + len(user) > 100_000:
            raise ValueError('模型输入超过限制')
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= 8000:
            raise ValueError('模型输出长度必须介于 1 到 8000')
        if sensitive(system) or sensitive(user):
            raise ValueError('待发送文本疑似含凭据或私人账户数据，已阻止模型调用')
        cfg = self._config()
        if not cfg['enabled'] or not cfg.get('api_key'):
            raise BudgetBlocked('请先在设置中配置并明确启用模型')
        endpoint = _endpoint(cfg['base_url'])
        private = self._init()
        # UTF-8 byte count plus generous chat framing allowance bounds ordinary tokenizer input.
        input_bound = len(system.encode('utf-8')) + len(user.encode('utf-8')) + 4096
        reserve = (input_bound * cfg['input_per_million_cny'] + max_tokens * cfg['output_per_million_cny']) / 1_000_000
        call_id = uuid.uuid4().hex
        try:
            with exclusive(private / 'model.lock', timeout=2):
                cfg_latest = self._config()
                if cfg_latest != cfg:
                    raise BudgetBlocked('模型设置刚刚改变，请重试')
                ledger = self._ledger()
                used = sum(float(row.get('charged_estimate_cny', row.get('reserved_cny', 0))) for row in ledger['calls'])
                if reserve > cfg['max_call_cny'] or used + reserve > cfg['budget_cny']:
                    raise BudgetBlocked('本次预估上限超出单次或累计预算，尚未调用模型')
                ledger['calls'].append({'id': call_id, 'created_at': time.time(), 'purpose': str(purpose)[:100],
                                        'requested_model': cfg['model'], 'status': 'reserved', 'reserved_cny': reserve,
                                        'cost_basis': 'user_configured_token_prices',
                                        'input_per_million_cny': cfg['input_per_million_cny'],
                                        'output_per_million_cny': cfg['output_per_million_cny']})
                atomic_json(private / 'usage.json', ledger)
        except LockBusy as exc:
            raise BudgetBlocked('预算记录正在更新，请稍后再试') from exc
        payload = {'model': cfg['model'], 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                   'max_tokens': max_tokens, 'stream': False, 'response_format': {'type': 'json_object'}}
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cfg['api_key']}, method='POST')
        response_body = None
        try:
            # Explicitly configured endpoint only. No redirects, no environment proxy credentials.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            with opener.open(request, timeout=90) as response:
                response_body = response.read(2_000_001)
                if len(response_body) > 2_000_000:
                    raise ValueError('模型响应超过限制')
            data = json.loads(response_body.decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('模型响应必须是对象')
            usage = data.get('usage') or {}
            if not isinstance(usage, dict):
                raise ValueError('模型 usage 格式不正确')
            prompt_tokens, completion_tokens = usage.get('prompt_tokens'), usage.get('completion_tokens')
            if (not isinstance(prompt_tokens, int) or isinstance(prompt_tokens, bool) or prompt_tokens < 0 or
                    not isinstance(completion_tokens, int) or isinstance(completion_tokens, bool) or completion_tokens < 0):
                cost = reserve
                usage = {}
            else:
                cost = (prompt_tokens * cfg['input_per_million_cny'] + completion_tokens * cfg['output_per_million_cny']) / 1_000_000
                usage = {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens}
            metadata = {'status': 'completed', 'returned_model': str(data.get('model', 'unknown'))[:160],
                        'usage': usage, 'charged_estimate_cny': cost, 'finished_at': time.time()}
            self._finish(call_id, metadata)
            if data['choices'][0].get('finish_reason') != 'stop':
                raise ValueError('模型输出未正常结束')
            content = data['choices'][0]['message']['content']
            if not isinstance(content, str):
                raise ValueError('模型业务响应格式不正确')
            content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content.strip()).strip()
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError('模型业务响应必须是 JSON 对象')
            return result
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            # Never persist raw exceptions, server error bodies, prompts, or credentials.
            # A response may already have consumed tokens even if validation or transport failed.
            current = next((x for x in self._ledger()['calls'] if x['id'] == call_id), {})
            if current.get('status') == 'completed':
                self._finish(call_id, {'status': 'invalid_response'})
            else:
                self._finish(call_id, {'status': 'uncertain', 'finished_at': time.time()})
            raise ValueError('模型调用未能生成有效结果；已保留费用记录，不会自动重试。请检查服务配置或稍后手动再试。') from None
