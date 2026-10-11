# WikiWorkbench · 本地知识工作台

选择一个自己的资料文件夹，建立可追溯的知识库；每日阅读可独立使用，也可接入其他个人工作台。

这是 **0.1.1 本地试用版**。仅依赖 Python 3.10 及以上的标准库，没有投资系统、数据库服务、Node 安装或模型订阅要求。模型 API 由使用者自行配置，默认关闭。新建空文件夹即可开始阅读记录。

0.1.1 统一了知识库、阅读、首次使用和设置界面：深色导航与暖白内容区、主题分类、阅读时间线和分组表单。使用系统字体与本地图标，页面不加载第三方字体或 CDN。

## 开始使用

解压源码，在此目录运行：

```sh
python3 -m wiki_workbench
```

Windows 可用 `py -m wiki_workbench`。浏览器打开 `http://127.0.0.1:8780/`，粘贴本地资料文件夹完整路径。也可以启动时指定：

```sh
python3 -m wiki_workbench --workspace "/path/to/my-notes" --module reading
```

端口固定为 8780，占用时明确报错。可显式传 `--port`；程序不会静默换端口。只监听本机地址，不是可直接公开部署的多用户服务。关闭终端或按 Ctrl+C 后，自动整理停止。

## 已实现的流程

1. **选择资料文件夹**：可以为空；仅检查你明确选择的目录。
2. **扫描和预览**：读取 UTF-8 Markdown/TXT，列出来源、跳过原因与本地分类建议。不会调用模型或移动原件。
3. **可选 AI 建议**：配置 API 并启用后，点击生成建议。最多发送 100 份资料，每份前 500 字的摘要内容；页面会说明范围。AI 建议只引用已有来源编号。
4. **确认建立**：核验资料没有变化后，在 `.wiki-workbench` 生成可重建索引和首页，原有文件夹保持原状。
5. **检索和问答**：关键词检索返回原文位置；“问 AI”根据检索片段回答并校验来源编号。无证据不会调用模型。此版本是关键词检索，不承诺任意自然语言都能找到资料。
6. **独立阅读**：书名加一个感悟框；日期自动记录，同书持续累积。勾选 AI 整理后，程序运行期间会处理待整理小记。标准表和原文折叠展示，草稿按文件夹与书籍隔离。

当前支持文件上限、单文件长度和总大小限制，扫描结果明确列出。PDF、Word、图片等会标为尚不支持，不会假装已经解析。目录新增资料后，本版需要再次点击扫描并确认更新。

## 模型与费用

设置里填自己的 OpenAI-compatible Chat Completions 服务地址、模型名和 API key；默认示例地址为 DeepSeek，默认示例模型 `deepseek-chat`，实际名称应以你的服务支持列表为准。也可填写兼容的 GLM 接口，但需要该接口支持 JSON 格式响应；本版没有复杂路由。

密钥只写入所选目录的 `.wiki-workbench/private/model.json`，权限尽可能限制为当前用户，界面状态不会返回密钥。私有目录自带 Git 忽略规则。不要手动将该文件或数据目录公开。更换服务地址需要重新提供适用于该服务的密钥。

费用依据设置中的 token 单价估算，**不是供应商实时价格或账单**。调用前预留单次与累计额度；未知结果仍计入预留额度。整理失败保留原文并停止自动重购。模型关闭、额度不足时保留待处理记录。电脑休眠或退出后，下次运行同一目录继续检查；没有安装开机任务。

## 数据和可插拔边界

- 你选中的原文件始终只读，整理不等于核实文件里的每项说法。
- 派生索引在 `.wiki-workbench/index.json`，可阅读首页在 `.wiki-workbench/Home.md`。
- 阅读记录在 `.wiki-workbench/modules/reading/`，原文与 AI 结果分开，后者不代表本人认可。
- 数据文件夹中的脚本、插件说明或提示词不会被执行。
- 初版提供内置模块清单及 HTTP 接口，支持独立链接与明确授权的 iframe 嵌入。没有第三方插件市场、任意代码热加载或宿主间自动共享资料。

接口与接入例子见 [MODULE_API.md](docs/MODULE_API.md)。阅读无需投资模块，也不自动访问任何投资资料。

## 开源参考与发布

选型记录见 [OPEN_SOURCE_REVIEW.md](docs/OPEN_SOURCE_REVIEW.md)。复用了本项目已有的阅读原文保存、幂等和引用校验逻辑，借鉴成熟项目的模块设计；没有复制 SilverBullet/Logseq 源码。

发布包使用显式文件清单，仅包含程序、测试、文档和虚构样例，不包含个人资料文件夹、模型配置、响应、Git 历史或本机路径。可先审查：

```sh
python3 tools/build_release.py --check
python3 tools/build_release.py --output /path/to/wiki-workbench-0.1.1.zip
```

测试：`python3 -m unittest discover -s tests`。前端离线测试：`node tests/test_ui.js`（开发检查可选，运行应用不需要 Node）。Mac 本地验收结果不代表已在 Windows/Linux 实机验收。

## English quick start

Python 3.10+ only. Run `python3 -m wiki_workbench`, open the loopback URL, then select a specific local folder. Original files stay untouched. The reading workbench works with an empty folder and no investment application. AI is optional, explicitly configured, and billed by your own provider. This alpha supports UTF-8 Markdown/TXT and keyword retrieval. See the API document for embedding; never publish a personal workspace or its `.wiki-workbench` directory.
