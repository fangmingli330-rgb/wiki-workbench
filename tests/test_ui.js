'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const ui = require('../wiki_workbench/static/app.js');
test('source and AI text are escaped, including quote delimiters', () => {
  assert.equal(ui.esc('<script a="x">&\'</script>'), '&lt;script a=&quot;x&quot;&gt;&amp;&#39;&lt;/script&gt;');
  const html = ui.noteHTML({id:'1',book_title:'<img src=x onerror=alert(1)>',content:'<script>alert(1)</script>',analysis_status:'ready',analysis:{summary:'<b>AI</b>',reflections:[{text:'<svg/onload=1>',quote:'<script>quote</script>'}]}});
  assert.ok(!html.includes('<script>')); assert.ok(!html.includes('<svg')); assert.ok(html.includes('&lt;b&gt;AI&lt;/b&gt;'));
});
test('drafts are isolated by both workspace and book', () => {
  assert.notEqual(ui.draftKey('/a','book'),ui.draftKey('/b','book'));
  assert.notEqual(ui.draftKey('/a','book1'),ui.draftKey('/a','book2'));
  assert.notEqual(ui.draftKey('/a:b','c'),ui.draftKey('/a','b:c'));
});
test('ready AI result is separate from original', () => {
  const html = ui.noteHTML({id:'1',book_title:'书',content:'我自己的原话',analysis_status:'ready',analysis:{summary:'整理摘要',questions:[{text:'问题',quote:'我自己的原话'}]}});
  assert.ok(html.includes('AI 辅助整理，以原文为准')); assert.ok(html.includes('我的原文')); assert.ok(html.includes('我自己的原话'));
});
test('blocked and local notes retain a visible original and honest state', () => {
  assert.equal(ui.noteState({analysis_status:'blocked'}),'整理已暂停');
  const html = ui.noteHTML({id:'1',content:'保留我',analysis_status:'failed',error:'预算不足'});
  assert.ok(html.includes('预算不足')); assert.ok(html.includes('保留我'));
  assert.ok(ui.analysisHTML({analysis_status:'local'}).includes('未送模型'));
});
test('both backend citation identifiers are supported', () => {
  assert.equal(ui.sourceId({source_id:'s',id:'i'}),'s'); assert.equal(ui.sourceId({id:'i'}),'i');
});
test('category schema handles local and AI suggestions without code execution', () => {
  assert.deepEqual(ui.categories({categories:['阅读',{id:'book',name:'图书',file_ids:['x']}]}).map(x => x.title),['阅读','图书']);
  assert.deepEqual(ui.categories(null),[]);
});

test('citation labels preserve the answer source numbering', () => {
  const html = ui.citationHTML({source_id:'original-id',label:'3',title:'Third source'},0);
  assert.ok(html.includes('[3]')); assert.ok(html.includes('data-document="original-id"')); assert.ok(!html.includes('[1]'));
});
test('Wiki operations reject overlapping scan, proposal and apply without blocking independent reading work', async () => {
  const transitions = [], gate = ui.createWikiOperations(value => transitions.push(value));
  let release;
  const pending = gate.run(() => new Promise(resolve => { release = resolve; }));
  assert.equal(gate.busy,true);
  let invoked = false;
  await assert.rejects(gate.run(() => { invoked = true; }),/等待当前操作完成/);
  assert.equal(invoked,false);
  // Reading uses its own save state, not the Wiki operation gate.
  let readingSaved = false;
  await Promise.resolve().then(() => { readingSaved = true; });
  assert.equal(readingSaved,true);
  release('scan done'); assert.equal(await pending,'scan done');
  assert.equal(gate.busy,false); assert.deepEqual(transitions,[true,false]);
  assert.equal(await gate.run(async () => 'next apply'),'next apply');
});
test('a failed Wiki operation releases the operation gate', async () => {
  const gate = ui.createWikiOperations();
  await assert.rejects(gate.run(async () => { throw new Error('network unavailable'); }),/network unavailable/);
  assert.equal(gate.busy,false);
  assert.equal(await gate.run(async () => 'retry'),'retry');
});
test('an AI response cannot overwrite a newer scan or a different workspace', async () => {
  for (const changed of ['scan','workspace']) {
    const current = {status:{workspace:{id:'workspace-a'}},scan:{id:'scan-a',suggestion:{title:'original'}}};
    const expected = ui.scanContext(current);
    let reply;
    const pending = new Promise(resolve => { reply = resolve; }).then(value => ui.acceptSuggestion(current,expected,value));
    if (changed === 'scan') current.scan = {id:'scan-b',suggestion:{title:'keep new scan'}};
    else current.status.workspace.id = 'workspace-b';
    const retained = current.scan.suggestion;
    reply({scan_id:'scan-a',title:'stale AI suggestion'});
    assert.equal(await pending,false);
    assert.equal(current.scan.suggestion,retained);
  }
});
test('AI suggestions must echo the captured scan id before becoming selectable', () => {
  const current = {status:{workspace:{id:'workspace-a'}},scan:{id:'scan-a',suggestion:{title:'local'}}};
  const expected = ui.scanContext(current), original = current.scan.suggestion;
  assert.equal(ui.acceptSuggestion(current,expected,{title:'missing scan id'}),false);
  assert.equal(ui.acceptSuggestion(current,expected,{scan_id:'scan-b',title:'wrong scan id'}),false);
  assert.equal(current.scan.suggestion,original);
  const valid = {scan_id:'scan-a',title:'AI structure'};
  assert.equal(ui.acceptSuggestion(current,expected,valid),true);
  assert.equal(current.scan.suggestion,valid);
});
