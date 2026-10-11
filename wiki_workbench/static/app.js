/* A small host for independent, local workspaces. No third-party runtime or CDN. */
(function () {
  'use strict';
  const arr = value => Array.isArray(value) ? value : [];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const short = (value, limit = 140) => { const text = String(value || '').trim().replace(/\s+/g, ' '); return text.length > limit ? text.slice(0, limit) + '…' : text; };
  const draftKey = (workspace, book) => 'wiki-workbench.reading.v1:' + encodeURIComponent(workspace) + ':' + encodeURIComponent(book || 'new');
  const sourceId = source => String(source.source_id || source.id || '');
  const citationHTML = (item, index) => `<button class="citation" data-document="${esc(sourceId(item))}">[${esc(item.label || index + 1)}] ${esc(item.title || item.path || '查看出处')}</button>`;
  const categories = suggestion => arr(suggestion?.categories).map((item, index) => typeof item === 'string' ? {id:String(index),title:item,file_ids:[]} : {...item,id:String(item.id || index),title:String(item.title || item.name || '未分类'),file_ids:arr(item.file_ids)});
  const noteState = note => ({local:'仅本地保存',pending:'等待整理',processing:'正在整理',ready:'已整理',blocked:'整理已暂停',failed:'整理失败'}[note.analysis_status] || '状态待核对');
  const quoted = item => `<p>${esc(item.text || '')}</p>${item.quote ? `<blockquote>${esc(item.quote)}</blockquote>` : ''}`;
  function createWikiOperations(onChange = () => {}) {
    let busy = false;
    return {
      get busy() { return busy; },
      async run(work) {
        if (busy) throw new Error('正在处理知识库结构，请等待当前操作完成。');
        busy = true;
        try { onChange(true); return await work(); }
        finally { busy = false; onChange(false); }
      }
    };
  }
  function scanContext(current) {
    return {workspace_id:String(current.status?.workspace?.id || ''),scan_id:String(current.scan?.id || '')};
  }
  function sameScan(current, expected) {
    const active = scanContext(current);
    return !!expected?.workspace_id && !!expected?.scan_id && active.workspace_id === expected.workspace_id && active.scan_id === expected.scan_id;
  }
  function acceptSuggestion(current, expected, suggestion) {
    if (!sameScan(current,expected) || suggestion?.scan_id !== expected.scan_id) return false;
    current.scan.suggestion = suggestion;
    return true;
  }
  function analysisHTML(note) {
    const value = note.analysis;
    if (note.analysis_status !== 'ready' || !value) return `<p class="muted small">${note.analysis_status === 'local' ? '原文只保存在本地，未送模型。' : '原文已保存，整理结果会显示在这里。'}</p>${note.error ? `<p class="error">${esc(note.error)}</p>` : ''}`;
    const rows = [];
    if (value.summary) rows.push(['核心感悟', `<p>${esc(value.summary)}</p>`]);
    if (arr(value.reflections).length) rows.push(['反思', value.reflections.map(quoted).join('')]);
    if (arr(value.questions).length) rows.push(['留下的问题', value.questions.map(quoted).join('')]);
    if (arr(value.topics).length) rows.push(['相关主题', `<p>${value.topics.map(esc).join(' · ')}</p>`]);
    if (value.reading_location?.text) rows.push(['阅读位置', quoted(value.reading_location)]);
    if (arr(value.excerpts).length) rows.push(['明确摘录', value.excerpts.map(item => `<blockquote>${esc(item.quote || '')}</blockquote>`).join('')]);
    if (arr(value.changes).length) rows.push(['理解的变化', value.changes.map(item => `<p>${esc(item.text || '')}</p>${item.current_quote ? `<blockquote>这次：${esc(item.current_quote)}</blockquote>` : ''}${item.prior_quote ? `<blockquote>此前：${esc(item.prior_quote)}</blockquote>` : ''}`).join('')]);
    return `<p class="muted small">AI 辅助整理，以原文为准。</p><table class="analysis-table"><tbody>${rows.map(([label, body]) => `<tr><th scope="row">${label}</th><td>${body}</td></tr>`).join('')}</tbody></table>`;
  }
  function noteHTML(note) {
    return `<article class="reading-note"><div class="note-top"><strong>${esc(note.book_title)}</strong><time>${esc(note.date || String(note.created_at || '').slice(0,10))}</time></div><p class="note-preview">${esc(short(note.analysis?.summary || note.content))}</p><div class="note-bottom"><span>${esc(noteState(note))}</span></div><details class="note-detail" data-note="${esc(note.id)}"><summary>展开小记 ↓</summary><h3>AI 整理</h3>${analysisHTML(note)}<h3>我的原文</h3><div class="original">${esc(note.content)}</div></details></article>`;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {esc,short,draftKey,sourceId,citationHTML,categories,createWikiOperations,scanContext,sameScan,acceptSuggestion,analysisHTML,noteHTML,noteState};
  if (typeof document === 'undefined') return;

  const $ = selector => document.querySelector(selector);
  const embedded = new URLSearchParams(location.search).get('embed') === '1';
  const state = {status:null, scan:null, wiki:null, files:[], query:'', category:'', answer:null, reading:null, book:'', all:false, readingLoaded:false, saving:false, proposalIntent:null, generation:0, timer:null};
  const wikiOperations = createWikiOperations(updateWikiButtons);
  let toastTimer;
  if (embedded) document.body.classList.add('embedded');
  function page() { return embedded || location.hash === '#reading' || (!location.hash && state.status?.default_module === 'reading') ? 'reading' : 'wiki'; }
  function workspaceID() { return String(state.status?.workspace?.id || state.status?.workspace?.path || ''); }
  function canAI() { return !!(state.status?.model?.configured && state.status?.model?.enabled); }
  function notice(text, error = false) {
    $('#notice').textContent = text; $('#notice').className = 'visible' + (error ? ' error' : '');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#notice').className = ''; }, error ? 10000 : 5000);
  }
  async function api(path, data, options = {}) {
    const selected = workspaceID(), headers = {'Accept':'application/json'};
    if (selected && path !== '/api/workspace/select') headers['X-Workspace-ID'] = selected;
    if (data !== undefined) { headers['Content-Type'] = 'application/json'; headers['X-Workbench-CSRF'] = state.status?.csrf || ''; }
    const response = await fetch(path, {method:data === undefined ? 'GET' : 'POST',headers,body:data === undefined ? undefined : JSON.stringify(data)});
    let value;
    try { value = await response.json(); } catch (_) { throw new Error('工作台暂时没有响应，请确认程序仍在运行。'); }
    if (response.status === 409) throw new Error('资料目录已经变化。草稿仍在原目录下，请刷新页面后再操作。');
    if (!response.ok || value.ok === false) throw new Error(value.error || '操作没有完成，请稍后重试。');
    if (!options.allowChange && selected && workspaceID() !== selected) throw new Error('目录已切换，已忽略旧目录的响应。');
    return value;
  }
  function readStored(key, fallback) { try { const value = JSON.parse(localStorage.getItem(key) || 'null'); return value && typeof value === 'object' ? value : fallback; } catch (_) { return fallback; } }
  function readingDraft() { return readStored(draftKey(workspaceID(), state.book), {content:'',book_title:'',allow_ai:canAI()}); }
  function storeDraft(draft) { try { localStorage.setItem(draftKey(workspaceID(), state.book), JSON.stringify(draft)); return true; } catch (_) { return false; } }
  function captureDraft() {
    if (!$('#readingContent')) return;
    const draft = readingDraft(); draft.content = $('#readingContent').value; draft.book_title = $('#readingTitle')?.value || ''; draft.allow_ai = $('#readingAI').checked;
    if (!storeDraft(draft)) $('#readingMessage').textContent = '浏览器无法保留草稿，请在离开前保存。';
  }
  function updateWikiButtons(busy = wikiOperations.busy) {
    const actions = new Set(['scan','propose','confirm-propose','apply','cancel-scan','workspace']);
    document.querySelectorAll('button[data-action], #workspaceForm button[type=submit]').forEach(button => {
      if (!actions.has(button.dataset.action) && button.closest('form')?.id !== 'workspaceForm') return;
      if (busy) {
        if (button.dataset.wikiDisabled === undefined) button.dataset.wikiDisabled = button.disabled ? 'true' : 'false';
        button.disabled = true;
      } else if (button.dataset.wikiDisabled !== undefined) {
        button.disabled = button.dataset.wikiDisabled === 'true'; delete button.dataset.wikiDisabled;
      }
    });
  }
  function shell(body) {
    const w = state.status?.workspace, current = page();
    $('#app').innerHTML = `<div class="app-shell"><aside class="sidebar"><a class="brand" href="#wiki"><span class="brand-mark">w</span>Wiki Workbench</a><nav class="nav" aria-label="工作台"><a href="#wiki" ${current === 'wiki' ? 'class="active" aria-current="page"' : ''}><span class="nav-icon" aria-hidden="true">▧</span>知识库</a><a href="#reading" ${current === 'reading' ? 'class="active" aria-current="page"' : ''}><span class="nav-icon" aria-hidden="true">▥</span>每日阅读</a></nav><div class="sidebar-bottom"><div class="workspace-label"><strong>${esc(w?.name || '尚未选择资料目录')}</strong><span class="small muted">资料留在自己的电脑</span></div><details class="settings-menu"><summary>设置与工具 ⌄</summary><button class="text-button" data-action="model">模型设置</button><button class="text-button" data-action="workspace">切换资料目录</button><button class="text-button" data-action="embed">嵌入阅读工作台</button></details></div></aside><main class="main"><div class="content"><div class="embed-settings"><button class="text-button" data-action="model">模型设置</button> · <button class="text-button" data-action="workspace">资料目录</button></div>${body}</div></main></div>`;
    updateWikiButtons();
  }
  function welcomeHTML() {
    return `<section class="welcome"><div class="eyebrow">Your knowledge, your space</div><h1>让已有资料，成为可积累的知识。</h1><p class="intro">选择一个本地文件夹，先看看里面有什么。<br>工作台会建议一种组织方式，由你决定是否采用。</p><form id="workspaceForm" class="panel"><label class="field" for="workspacePath">资料文件夹</label><div class="inline top-gap-small"><input id="workspacePath" name="path" placeholder="粘贴文件夹的完整路径" autocomplete="off" required><button class="primary" type="submit">选择并扫描 ↗</button></div><p class="helper">可以从一个空文件夹开始。原始文件会保留在原位置，不会移动或覆盖。</p></form><p class="privacy-line">首次扫描在本机完成。只有你启用 AI 时，选定内容才会发送给模型。</p></section>`;
  }
  function header(title, description, action = '') { return `<header class="page-head"><div><div class="eyebrow">Personal workspace</div><h1>${title}</h1><p>${description}</p></div>${action ? `<div class="head-action">${action}</div>` : ''}</header>`; }
  function documentRows(files) {
    return files.length ? `<div class="document-list">${files.map(file => `<button class="document-row" data-document="${esc(sourceId(file))}"><span class="file-icon" aria-hidden="true">▤</span><span class="file-text"><span class="file-title">${esc(file.title || file.path)}</span><span class="file-path">${esc(file.path)}</span></span><span class="file-arrow" aria-hidden="true">↗</span></button>`).join('')}</div>` : '<div class="empty">这里还没有资料。放入文件后，点击「扫描更新」。</div>';
  }
  function suggestionHTML(suggestion) {
    const items = categories(suggestion);
    return `<div class="scan-preview"><h3>${esc(suggestion?.title || '建议的知识组织方式')}</h3>${suggestion?.description ? `<p class="muted small">${esc(suggestion.description)}</p>` : ''}${items.length ? `<ul>${items.map(item => `<li><strong>${esc(item.title)}</strong>${item.description ? ' · ' + esc(item.description) : ''}${item.file_ids.length ? `（${item.file_ids.length} 份）` : ''}</li>`).join('')}</ul>` : '<p class="muted small">先建立一个简单资料库，之后可以继续补充。</p>'}</div>`;
  }
  function scanHTML() {
    const scan = state.scan;
    if (!scan) return '';
    return `<section class="panel scan-panel" aria-label="扫描结果"><div class="scan-header"><span class="scan-check">✓</span><div><h2>已经读到 ${arr(scan.files).length} 份资料</h2><p>原件保留在原位置，下面是本次组织建议。</p></div></div>${suggestionHTML(scan.suggestion)}${arr(scan.skipped).length ? `<details class="skip-details"><summary>${scan.skipped.length} 项未纳入本次整理 · 查看原因</summary><div class="skip-list">${scan.skipped.map(item => esc(typeof item === 'string' ? item : `${item.path || item.name || '文件'}：${item.reason || item.error || '已跳过'}`)).join('\n')}</div></details>` : ''}<div class="scan-actions"><button class="primary" data-action="apply">采用这个结构</button><button data-action="propose" ${canAI() ? '' : 'disabled'}>用 AI 生成建议</button><button class="text-button" data-action="cancel-scan">稍后再定</button></div><p class="helper">${canAI() ? 'AI 建议是可选步骤，运行前会展示发送范围。' : '当前为本地建议；配置并启用模型后，可让 AI 帮你整理结构。'}</p></section>`;
  }
  function wikiHTML() {
    const w = state.status.workspace, suggestion = state.wiki?.proposal || state.status.wiki?.suggestion || state.status.suggestion || w.suggestion || {}, groups = categories(suggestion);
    let files = state.files;
    if (state.category) { const group = groups.find(item => item.id === state.category); if (group) files = files.filter(item => group.file_ids.includes(item.id) || group.file_ids.includes(item.path) || item.category_id === group.id); }
    return header('知识库', '把散落的资料，连成自己的理解。', '<button class="text-button" data-action="scan">扫描更新 ↗</button>') +
      `<form id="searchForm" class="search-box"><input id="searchQuery" aria-label="查找资料或提出问题" placeholder="查找资料，或问一个问题…" value="${esc(state.query)}"><button type="submit">查找</button><button type="button" data-action="ask" ${canAI() ? '' : 'disabled'}>问 AI ↗</button></form>` +
      (state.answer ? `<section class="answer" aria-label="AI 回答"><h3>基于已有资料的回答</h3><div class="answer-body">${esc(state.answer.answer)}</div><div class="citations">${arr(state.answer.citations).map(citationHTML).join('')}</div></section>` : '') +
      scanHTML() + (!state.scan && !(state.status.initialized ?? w.initialized) ? `<div class="panel"><h2>从第一次扫描开始</h2><p class="helper">${esc(w.path)}<br>先查看目录内容，再决定知识库的组织方式。每日阅读可以直接使用。</p><div class="form-footer"><span></span><button class="primary" data-action="scan">扫描这个目录</button></div></div>` : '') +
      (groups.length ? `<div class="section-heading"><h2>知识分类</h2>${state.category ? '<button class="text-button" data-action="clear-category">查看全部</button>' : ''}</div><div class="categories">${groups.map((item,index) => `<button class="category ${state.category === item.id ? 'active' : ''}" data-category="${esc(item.id)}"><div class="category-number">${String(index+1).padStart(2,'0')}</div><h3>${esc(item.title)}</h3><p>${esc(item.description || (item.file_ids.length ? item.file_ids.length + ' 份关联资料' : '持续积累的一个主题'))}</p></button>`).join('')}</div>` : '') +
      `<div class="section-heading"><h2>${state.query ? '查找结果' : '资料'}</h2><span class="small muted">${files.length} 份</span></div>${documentRows(files)}<p class="helper">查找在本机完成。问 AI 时，会将问题和检索命中的片段发送给已配置模型。</p>`;
  }
  function readingHTML() {
    const draft = readingDraft(), books = arr(state.reading?.books), book = books.find(item => item.id === state.book);
    return `<div class="reading-shell">${header('每日阅读', '把今天读到的，变成自己的理解。', `<span class="small muted">${new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'long',day:'numeric'}).format(new Date())}</span>`)}<form id="readingForm" class="panel reading-card"><div class="book-row"><label for="readingBook">正在读</label><select id="readingBook" ${state.saving ? 'disabled' : ''}>${books.map(item => `<option value="${esc(item.id)}" ${item.id === state.book ? 'selected' : ''}>${esc(item.title)}</option>`).join('')}<option value="" ${state.book ? '' : 'selected'}>＋ 添加一本书</option></select>${book ? `<details><summary aria-label="书籍状态">···</summary><div class="book-menu"><select id="bookStatus" aria-label="阅读状态">${Object.entries({reading:'在读',paused:'暂搁',finished:'已读完'}).map(([value,label]) => `<option value="${value}" ${book.status === value ? 'selected' : ''}>${label}</option>`).join('')}</select></div></details>` : ''}</div>${state.book ? '' : `<input class="new-book" id="readingTitle" aria-label="新书书名" placeholder="输入书名" maxlength="200" value="${esc(draft.book_title)}" required>`}<textarea id="readingContent" aria-label="今天的感悟与反思" placeholder="今天有什么新的感悟或反思？\n随意写几句也很好。" maxlength="20000" required ${state.saving ? 'disabled' : ''}>${esc(draft.content)}</textarea><div class="form-footer"><label class="check-label"><input id="readingAI" type="checkbox" ${canAI() && draft.allow_ai ? 'checked' : ''} ${canAI() ? '' : 'disabled'}>AI 自动整理</label><button class="primary" type="submit" ${state.saving ? 'disabled' : ''}>${state.saving ? '正在保存…' : '保存小记 ↗'}</button></div><p class="helper" id="readingConsent">${canAI() ? '启用 AI 时，会发送书名、本次小记及同书已授权的历史摘句。原文始终单独保留。' : '当前仅本地保存。配置并启用模型后，可以自动整理小记。'}</p></form><p class="status-message" id="readingMessage"></p><div class="section-heading"><h2>${state.all ? '全部阅读小记' : book ? '这本书的阅读历程' : '阅读历程'}</h2><div class="inline"><button class="text-button" data-action="reading-all">${state.all ? '当前书籍' : '全部记录'}</button><button class="text-button" data-action="reading-refresh">刷新</button></div></div><div id="readingHistory">${readingHistoryHTML()}</div></div>`;
  }
  function readingHistoryHTML() { const notes = arr(state.reading?.notes); return notes.length ? notes.map(noteHTML).join('') : `<div class="empty">${state.readingLoaded ? '选一本书，记下今天的一点想法。' : '正在读取阅读小记…'}</div>`; }
  function render() {
    if (!state.status) return;
    shell(!state.status.workspace ? welcomeHTML() : page() === 'reading' ? readingHTML() : wikiHTML());
  }
  function modalHTML(title, content, css = '') {
    const node = $('#modal'); node.className = css; node.innerHTML = `<div class="modal-heading"><h2 id="modal-title">${esc(title)}</h2><button class="close-button" aria-label="关闭" data-action="close">×</button></div>${content}`;
    if (!node.open) node.showModal();
    updateWikiButtons();
  }
  async function loadStatus() { state.status = await api('/api/status'); }
  async function loadFiles() { state.wiki = await api('/api/wiki'); if (state.query) { const result = await api('/api/search?q=' + encodeURIComponent(state.query)); state.files = arr(result.results); if (arr(result.stale).length) notice('部分原件已变化，请扫描更新后再查询。',true); } else state.files = arr(state.wiki.files); }
  async function loadReading({quiet = false} = {}) {
    const generation = state.generation, selected = state.book;
    const result = await api('/api/reading?book=' + encodeURIComponent(state.all ? 'all' : selected));
    if (generation !== state.generation || selected !== state.book || page() !== 'reading') return;
    state.reading = result;
    if (!state.readingLoaded && !state.book) {
      const saved = readStored('wiki-workbench.selection:' + workspaceID(),{}), newDraft = readingDraft();
      if (!newDraft.content && !newDraft.book_title) state.book = arr(result.books).find(item => item.id === saved.book)?.id || result.selected_book_id || '';
    }
    const reloadBook = !state.readingLoaded && !state.all && state.book && state.book !== result.selected_book_id;
    state.readingLoaded = true;
    if (reloadBook) { await loadReading({quiet}); return; }
    if (!state.all) state.reading.notes = arr(result.notes).filter(note => note.book_id === state.book);
    if (!quiet) render(); else if ($('#readingHistory')) {
      const open = Array.from(document.querySelectorAll('[data-note][open]')).map(node => node.dataset.note);
      $('#readingHistory').innerHTML = readingHistoryHTML();
      document.querySelectorAll('[data-note]').forEach(node => { if (open.includes(node.dataset.note)) node.open = true; });
    }
    clearTimeout(state.timer);
    if (arr(result.notes).some(item => ['pending','processing'].includes(item.analysis_status))) state.timer = setTimeout(() => loadReading({quiet:true}).catch(error => notice(error.message,true)),3000);
  }
  async function loadPage() {
    clearTimeout(state.timer); state.generation++; render();
    if (!state.status.workspace) return;
    if (page() === 'reading') await loadReading(); else { await loadFiles(); render(); }
  }
  async function selectWorkspace(path) {
    return wikiOperations.run(async () => {
      const result = await api('/api/workspace/select',{path}, {allowChange:true});
      state.status = result.status || result; if (!state.status.workspace) await loadStatus();
      state.scan = null; state.proposalIntent = null; state.wiki = null; state.files = []; state.reading = null; state.readingLoaded = false; state.book = ''; state.answer = null; state.category = ''; state.query = ''; state.generation++;
      $('#modal').close(); render(); await performScan();
    });
  }
  async function performScan() {
    notice('正在本机扫描目录…');
    state.scan = await api('/api/scan',{}); state.proposalIntent = null;
    if (page() !== 'wiki' && !embedded) location.hash = 'wiki';
    render();
    if (embedded) { await loadReading(); notice(`已扫描 ${arr(state.scan.files).length} 份资料，阅读工作台已就绪。`); }
    else notice('扫描完成，请确认组织建议。');
  }
  async function scanWorkspace() { return wikiOperations.run(performScan); }
  function settings() {
    const model = state.status.model || {};
    modalHTML('模型设置', `<form id="modelForm"><p class="model-note">使用你自己的 OpenAI 兼容 API。密钥只交给本机程序保存，不写进浏览器草稿，也不会进入知识库。</p><label class="field" for="modelURL">API 地址<input id="modelURL" type="url" value="${esc(model.base_url || '')}" placeholder="https://api.deepseek.com/v1" required></label><label class="field" for="modelName">模型名称<input id="modelName" value="${esc(model.model || '')}" placeholder="填写供应商提供的模型 ID" required></label><label class="field" for="modelKey">API 密钥<input id="modelKey" type="password" autocomplete="new-password" placeholder="${model.configured ? '已保存；留空则不更换' : '仅在本机保存'}"></label><label class="field" for="modelBudget">预算上限（元）<input id="modelBudget" type="number" min="0.01" max="10000" step="0.01" value="${esc(model.budget_cny ?? 5)}" required></label><label class="check-label top-gap"><input id="modelEnabled" type="checkbox" ${model.enabled ? 'checked' : ''}>启用模型调用</label><p class="helper">预算按下方单价估算，不是供应商账单。请按所用模型价格填写；未知调用结果会保留预留额度。</p><details class="skip-details"><summary>费用估算与单次限额</summary><label class="field" for="modelInputPrice">输入价格（元 / 百万 token）<input id="modelInputPrice" type="number" min="0.01" max="10000" step="0.01" value="${esc(model.input_per_million_cny ?? 2)}" required></label><label class="field" for="modelOutputPrice">输出价格（元 / 百万 token）<input id="modelOutputPrice" type="number" min="0.01" max="10000" step="0.01" value="${esc(model.output_per_million_cny ?? 3)}" required></label><label class="field" for="modelCallBudget">单次调用上限（元）<input id="modelCallBudget" type="number" min="0.01" max="10000" step="0.01" value="${esc(model.max_call_cny ?? 0.5)}" required></label></details><div id="modelMessage" class="error" role="alert"></div><div class="modal-footer"><button type="button" data-action="close">取消</button><button class="primary" type="submit">保存设置</button></div></form>`);
  }
  async function saveReading() {
    if (state.saving) return;
    captureDraft(); const draft = readingDraft(), workspace = workspaceID(), book = state.book;
    const base = {book_id:book,book_title:book ? '' : String(draft.book_title || '').trim(),content:draft.content || '',allow_ai:canAI() && !!draft.allow_ai};
    if ((!book && !base.book_title) || !base.content.trim()) { notice('写下书名和一点感悟，再保存小记。',true); return; }
    const fingerprint = JSON.stringify(base);
    if (!draft.submission || draft.submission.fingerprint !== fingerprint) draft.submission = {fingerprint,payload:{submission_id:crypto.randomUUID(),...base}};
    storeDraft(draft); state.saving = true; render();
    try {
      const result = await api('/api/reading/notes', draft.submission.payload);
      if (!result.note?.id) throw new Error('没有收到保存回执，原文仍保留在草稿中。');
      if (workspaceID() !== workspace) throw new Error('目录已切换，请回原目录查看保存结果。');
      try { localStorage.removeItem(draftKey(workspace,book)); } catch (_) {}
      state.book = result.book?.id || result.note.book_id || book; state.all = false;
      try { localStorage.setItem('wiki-workbench.selection:' + workspace, JSON.stringify({book:state.book})); } catch (_) {}
      notice(base.allow_ai ? '原文已保存，AI 整理完成后会显示在阅读历程中。' : '小记已保存在本地。');
      await loadReading();
    } finally { state.saving = false; render(); }
  }
  async function action(name, node) {
    if (name === 'close') { $('#modal').close(); $('#modal').innerHTML = ''; return; }
    if (name === 'model') { if (!state.status.workspace) { notice('先选择一个资料目录，每个目录使用独立的模型设置。'); return; } settings(); return; }
    if (name === 'workspace') { modalHTML('选择资料目录', `<form id="workspaceForm"><label class="field" for="workspacePath">本地文件夹完整路径<input id="workspacePath" value="${esc(state.status.workspace?.path || '')}" required autocomplete="off"></label><p class="helper">每个目录的数据相互独立，原始文件不会移动。未保存的阅读草稿保留在原目录下。</p><div class="modal-footer"><button type="button" data-action="close">取消</button><button type="submit" class="primary">选择并扫描</button></div></form>`); return; }
    if (name === 'embed') { modalHTML('独立使用每日阅读', `<p class="model-note">下面的地址只展示阅读工作台，可收藏为独立页面。要嵌入其他网站，还需在启动配置中明确允许对应站点。</p><label class="field top-gap" for="embedURL">阅读工作台地址<input id="embedURL" readonly value="${esc(location.origin + '/?embed=1#reading')}"></label><p class="helper">嵌入只改变界面，不会授予宿主读取资料或调用模型的权限。</p>`); return; }
    if (!state.status.workspace) return;
    if (name === 'scan') { await scanWorkspace(); return; }
    if (name === 'cancel-scan') { await wikiOperations.run(async () => { state.scan = null; state.proposalIntent = null; await loadStatus(); await loadFiles(); render(); }); return; }
    if (name === 'propose') { if (wikiOperations.busy) throw new Error('正在处理知识库结构，请等待当前操作完成。'); state.proposalIntent = scanContext(state); if (!state.proposalIntent.scan_id) throw new Error('请先重新扫描目录。'); modalHTML('用 AI 建议知识结构', `<p class="model-note">将发送本次扫描的文件路径、标题和每份文件前 500 字符，最多 100 份，模型根据这些内容建议分类和首页结构。原始文件不会改变。</p><p class="helper">本次扫描：${arr(state.scan.files).length} 份资料；模型：${esc(state.status.model.model)}。调用受本机预算门槛限制。</p><div class="modal-footer"><button data-action="close">取消</button><button class="primary" data-action="confirm-propose">同意发送并生成</button></div>`); return; }
    if (name === 'confirm-propose') {
      const expected = state.proposalIntent;
      await wikiOperations.run(async () => {
        if (!sameScan(state,expected)) throw new Error('扫描或资料目录已变化，请重新查看发送范围。');
        const result = await api('/api/propose',{scan_id:expected.scan_id});
        if (!acceptSuggestion(state,expected,result.suggestion)) throw new Error('AI 建议对应的扫描已经变化，已丢弃旧建议；请重新扫描后再生成。');
        state.proposalIntent = null;
        if ($('#modal').querySelector('[data-action="confirm-propose"]')) $('#modal').close();
        if (page() === 'wiki') render(); notice('AI 建议已生成，请确认后采用。');
      });
      return;
    }
    if (name === 'apply') {
      await wikiOperations.run(async () => {
        const expected = scanContext(state), suggestion = state.scan?.suggestion;
        if (!sameScan(state,expected)) throw new Error('请先重新扫描目录。');
        await api('/api/apply',{scan_id:expected.scan_id,suggestion});
        if (!sameScan(state,expected)) throw new Error('扫描或资料目录已变化，请刷新后核对保存结果。');
        state.scan = null; state.proposalIntent = null; await loadStatus(); await loadFiles();
        if (page() === 'wiki') render(); notice('知识库已经建立，原始文件保持原样。');
      });
      return;
    }
    if (name === 'ask') {
      const query = $('#searchQuery').value.trim(); if (!query) { notice('先写下想问的问题。'); $('#searchQuery').focus(); return; }
      state.query = query; node.disabled = true; node.textContent = '正在查阅…';
      try { state.answer = await api('/api/ask',{query}); await loadFiles(); render(); } finally { node.disabled = false; node.textContent = '问 AI ↗'; } return;
    }
    if (name === 'clear-category') { state.category = ''; render(); return; }
    if (name === 'reading-all') { captureDraft(); state.all = !state.all; await loadReading(); return; }
    if (name === 'reading-refresh') { captureDraft(); await loadReading(); return; }
  }
  document.addEventListener('click', async event => {
    try {
      const actionable = event.target.closest('[data-action]'); if (actionable) { await action(actionable.dataset.action,actionable); return; }
      const file = event.target.closest('[data-document]'); if (file) {
        const value = await api('/api/document?id=' + encodeURIComponent(file.dataset.document));
        modalHTML(value.title || '原始资料', `<p class="document-path">${esc(value.path)}</p><pre class="document-content"></pre>`,'document-modal');
        $('#modal .document-content').textContent = value.content || value.text || '暂时无法展示正文。'; return;
      }
      const category = event.target.closest('[data-category]'); if (category) { state.category = state.category === category.dataset.category ? '' : category.dataset.category; render(); }
    } catch (error) { notice(error.message,true); }
  });
  document.addEventListener('submit', async event => {
    const form = event.target; if (!['workspaceForm','searchForm','readingForm','modelForm'].includes(form.id)) return;
    event.preventDefault();
    const submit = form.querySelector('[type=submit]'); if (submit) submit.disabled = true;
    try {
      if (form.id === 'workspaceForm') await selectWorkspace($('#workspacePath').value.trim());
      if (form.id === 'searchForm') { state.query = $('#searchQuery').value.trim(); state.answer = null; state.category = ''; await loadFiles(); render(); }
      if (form.id === 'readingForm') await saveReading();
      if (form.id === 'modelForm') {
        const payload = {base_url:$('#modelURL').value.trim(),model:$('#modelName').value.trim(),api_key:$('#modelKey').value,enabled:$('#modelEnabled').checked,budget_cny:Number($('#modelBudget').value),input_per_million_cny:Number($('#modelInputPrice').value),output_per_million_cny:Number($('#modelOutputPrice').value),max_call_cny:Number($('#modelCallBudget').value)};
        await api('/api/model/config',payload); $('#modelKey').value = ''; $('#modal').close(); $('#modal').innerHTML = ''; await loadStatus(); render(); notice('模型设置已保存在本机。');
      }
    } catch (error) { if (form.id === 'modelForm' && $('#modelMessage')) $('#modelMessage').textContent = error.message; else notice(error.message,true); }
    finally { if (submit) submit.disabled = false; }
  });
  document.addEventListener('input', event => {
    if (['readingContent','readingTitle'].includes(event.target.id)) { captureDraft(); if ($('#readingMessage')) $('#readingMessage').textContent = '草稿已保留'; }
  });
  document.addEventListener('change', async event => {
    try {
      if (event.target.id === 'readingBook') {
        captureDraft(); state.book = event.target.value; state.all = false; state.generation++;
        try { localStorage.setItem('wiki-workbench.selection:' + workspaceID(),JSON.stringify({book:state.book})); } catch (_) {}
        state.reading = {...state.reading,notes:[]}; render(); if (state.book) await loadReading();
      }
      if (event.target.id === 'readingAI') captureDraft();
      if (event.target.id === 'bookStatus') { await api('/api/reading/book',{book_id:state.book,status:event.target.value}); await loadReading(); }
    } catch (error) { notice(error.message,true); }
  });
  window.addEventListener('hashchange', () => { captureDraft(); loadPage().catch(error => notice(error.message,true)); });
  window.addEventListener('beforeunload', captureDraft);
  $('#modal').addEventListener('cancel', () => { $('#modal').innerHTML = ''; });
  loadStatus().then(loadPage).catch(error => { shell(`<div class="welcome"><h1>工作台暂时无法打开</h1><p class="error top-gap">${esc(error.message)}</p><p class="helper">确认本机程序仍在运行后，刷新页面。</p></div>`); });
})();
