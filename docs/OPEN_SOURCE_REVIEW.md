# 2026-10-11 开源选型记录

本轮先核对官方仓库、文档、许可证、发布和模块代码，随后再做通用版抽取。以下是范围适配判断，不是对整个项目质量排名。

| 项目 | 核对内容 | 本轮采用与差异 |
| --- | --- | --- |
| [SilverBullet](https://github.com/silverbulletmd/silverbullet) | [MIT](https://github.com/silverbulletmd/silverbullet/blob/main/LICENSE.md)、[2.12.0 发布](https://github.com/silverbulletmd/silverbullet/releases/tag/2.12.0)、[Plugs 文档](https://docs.silverbullet.md/Plugs)、[manifest 类型](https://github.com/silverbulletmd/silverbullet/blob/main/plug-api/types/manifest.ts) | 借鉴文件夹承载资料、模块声明入口与能力、宿主/模块分离。项目近期仍发布；其重点是 Markdown 编辑、Lua 和宿主内插件，直接整体替换会引入新的服务/编辑体系。独立阅读与已有 Python 保存逻辑仍需适配。 |
| [Logseq](https://github.com/logseq/logseq) | [AGPL-3.0](https://github.com/logseq/logseq/blob/master/LICENSE.md)、[2.0.2 Beta](https://github.com/logseq/logseq/releases/tag/2.0.2)、[DB 文档](https://github.com/logseq/docs/blob/master/db-version.md)、[插件样例](https://github.com/logseq/logseq-plugin-samples/tree/master/logseq-hello-world) | 借鉴书籍持续页面与日期记录关联。当前 DB 与文件图的插件兼容需区分；完整采用会要求用户进入 Logseq 的宿主/图模型，独立阅读仍需要额外包装。 |

决定：第一轮复用已有阅读保存和校验逻辑，移除机器路径、研究引擎和投资数据依赖；用小型标准库宿主验证“空文件夹即可使用、可独立打开、可嵌入其他工作台”。未复制上述项目源代码、未安装其依赖，也未实现与其插件 SDK 的直接兼容。

这种机制借鉴不等于已经拥有 SilverBullet 的插件隔离、Logseq 的完整图数据库或成熟编辑器。若后续需求进入复杂协作、富文本编辑、跨设备同步，应重新评估采用成熟宿主，避免在试用版中继续重复建设。

## 0.1.1 界面改进参考

本次视觉改进先查看 [shadcn/ui](https://github.com/shadcn-ui/ui) 的 [Sidebar 文档](https://ui.shadcn.com/docs/components/sidebar)，以及 [Primer CSS](https://github.com/primer/css) 的 [Dialog 规范](https://primer.style/product/components/dialog/)。两者官方仓库均采用 MIT 许可。借鉴导航分区、可见选中状态、表单分组、弹窗标题与操作区的设计惯例。

现有应用是无构建步骤的 HTML/JavaScript；本轮继续使用已有原生控件、事件处理及原文保护逻辑，不引入 React、Tailwind 或整套组件依赖。图标由本项目绘制，字体使用操作系统已有字体；未复制上述项目源代码或品牌资源。这样保留了 Python 标准库即可运行的安装方式，也避免页面依赖第三方网络资源。
