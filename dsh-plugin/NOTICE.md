# API-MRI DSH companion

本插件使用 MIT 许可证，许可证全文见同目录 `LICENSE`。

接入契约参考以下 MIT 开源项目的公开文档与源码，核对日期为 2026-10-31：

- DeepSeek AI, **DeepSeek Harness**，https://github.com/deepseek-ai/deepseek-harness 。使用其 `package.json#dsh.bundle`、`package.json#dsh.client`、浏览器模块 factory 与 format-1 插件索引契约。
- omdsh-dev contributors, **DSH Better Sidebar**，v0.28.2，提交 `2d736f5496fe97f4bba6af37d99861649b9ef3c0`，https://github.com/omdsh-dev/DSH-better-sidebar 。使用其 `ctx.betterSidebar.registerTab()` 服务接口。

插件源码为 API-MRI 的独立实现，未复制或捆绑上述项目的运行时代码。React 及 Better Sidebar 由 DSH 环境提供，遵循各自许可证。API-MRI 的三柱标志、配色与嵌入工作台沿用主项目。
