# 模块接口 v1

模块可以作为独立页面打开，也可以嵌入明确授权的个人工作台。版本 1 使用同源 HTTP JSON；不要求宿主读取原文目录或得到模型密钥。

## 独立阅读

`python3 -m wiki_workbench --module reading --workspace /path/to/notes`

访问 `http://127.0.0.1:8780/#reading`。启动时不加载任何投资模块。

## 嵌入已有工作台

启动时指定宿主来源，例如：

```sh
python3 -m wiki_workbench --module reading --allow-embed-origin http://127.0.0.1:8790
```

宿主嵌入：

```html
<iframe title="每日阅读" src="http://127.0.0.1:8780/?embed=1#reading"
        style="width:100%;height:800px;border:0"></iframe>
```

该模式仍使用阅读程序自己的来源和会话，宿主不会自动获得跨域 API 权限。新增宿主需要显式加入 `--allow-embed-origin`；默认只允许同源嵌入。浏览器隐私/本地网络规则可能额外限制远程 HTTPS 宿主访问本机 HTTP，首版以同机宿主验收为准。

## 接口

`GET /api/status` 返回程序版本、已选目录（path/name/id/initialized）、模型状态、模块清单和 CSRF 会话。永不返回 API key。

除 status/modules 和选择目录以外的请求，带 `X-Workspace-ID`，值为当前 workspace.id。写请求额外需要正确的 `Origin`、`Content-Type: application/json`、`X-Workbench-CSRF`。用户切换目录后，旧页面请求收到 409，避免旧草稿写到新目录。

| 接口 | 用途 |
| --- | --- |
| GET /api/modules | 已安装的内置模块清单及 api_version |
| POST /api/workspace/select `{path}` | 选择一个明确的本地目录 |
| POST /api/scan `{}` | 只读预演，返回扫描 id 与建议 |
| POST /api/propose `{scan_id}` | 按预览范围调用模型生成分类建议 |
| POST /api/apply `{scan_id,suggestion?}` | 确认并生成派生索引；来源改变则拒绝 |
| GET /api/wiki | 已保存的知识库首页与资料清单 |
| GET /api/search?q= | 本地关键词检索 |
| GET /api/document?id= | 已登记、哈希一致的原文 |
| POST /api/ask `{query}` | 以检索片段问 AI；结果附 citations |
| GET /api/reading?book=&q= | 书籍及小记；book=all 读取全部 |
| POST /api/reading/notes | UUID submission_id、book_id 或 book_title、content、allow_ai |
| POST /api/reading/book | book_id、status（reading/paused/finished） |
| POST /api/model/config | 明确配置模型、密钥、预算和估算单价 |

`GET /api/modules` 中 reading 声明独立入口、版本、能力 `reading.read`、`reading.write`、`model.optional`。这是一份能力约定，**不是第三方代码沙箱**。本版只加载程序内置的模块；未来引入第三方模块前，需要另行完成权限隔离与安装审查。

数据隔离按所选文件夹；模块间没有默认互相授权。退出程序不会删除记录，移除宿主 iframe 也不会删除数据。
