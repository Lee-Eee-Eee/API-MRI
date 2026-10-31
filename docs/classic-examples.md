# 经典题与公开作品

打开示例馆就能看 24 幅公开 SVG，无需填写密钥。选一道题后，可以把同一提示发给正在使用的 API，保存它返回的代码、用量与耗时，再并排看两幅作品。浏览与试作分开：只有确认试作才会发出一次请求。

首批收录 6 道题、37 条作品记录。24 个 SVG 来自明确采用 MIT 许可的仓库，原文件合计 94,757 字节；另 13 条保留原作链接，包含近期模型和一次没有返回图形的运行。来源检查日期为 2026-10-31。

## 看什么

| 题目 | 可观察的细节 | 提示来源 |
| --- | --- | --- |
| 鹈鹕骑自行车 | 车架、脚与踏板、鸟与车座的关系，SVG 是否完整 | Simon Willison，2024-10-25 |
| 章鱼弹管风琴 | 触手是否连着身体、碰到琴键，乐器是否可辨认 | Tom Gally，2025-11 题集 |
| 海星开推土机 | 驾驶位置、履带、铲斗和机身的关系 | 同上 |
| 9:41 的钟 | 两根指针的角度、刻度、秒针动画与缩放 | 改写自 Brian Moore 的 AI World Clocks |
| 旋转六边形中的弹球 | 穿墙、卡角、重力、运动边界与刷新率 | 由公开 Python 题改写为 HTML |
| 会踩踏板的鹈鹕 | 脚是否持续跟随踏板，关节和车轮是否协调 | kings0527 的动画任务 |

静态截图常常看不出动画的错误。弹球可能几十秒后才穿墙，脚也可能只在第一帧贴住踏板。保存代码和请求记录，才能区分绘图缺陷、截断和运行环境问题。

## 为什么保留原作的运行条件

同一个“鹈鹕骑车”，可能是一次短请求，也可能是一整轮编程 Agent。kings0527 的 DeepSeek V4 Flash 动画由 pi 驱动，作者记录了 115 次 API 回合，包含复核子任务。示例馆的一键试作只发送一次请求，原作的回合数和工具使用会随链接一起显示。[原作与运行记录](https://github.com/kings0527/pelican-bicycle)（2026-10-31 读取）。

bbinwang 的两份 Qwen3.6 PRISM 输出分别标为 `agent`、`code`，这里指采样档名：温度、top-p 和 presence penalty 不同。它们都是作者记录的本地量化模型输出。把这些标签读成两个不同模型，或读成多轮 Agent，会误解作品之间的差异。[配置与说明](https://github.com/bbinwang/pelican-bicycle/tree/b14acbb2ebe9d9be5f0dec3d35d8f4ae6fcaa432)。

失败也留下来。Simon 的 Claude Opus 5.5 对照中，`max` 档耗尽 128,000 个输出 token，没有返回 SVG；`high` 档则返回了图形。它说明推理预算会改变一次交付的时间、费用和结果。catalog 沿用原作者记录，未重跑这些模型。[原始对照](https://static.simonwillison.net/static/2026/claude-pelicans-grid.html)（运行日期 2026-09-22）。

作品好看与否只能回答这道题。Simon 在 2026-07-16 回顾中指出，鹈鹕表现与整体能力的早期相关性已减弱，这道题也没有覆盖 Agent 工具调用。API-MRI 将作品用于观察几何表达、代码完整度和调用代价；模型身份仍由独立的指纹方法与校准数据评估。[作者的方法反思](https://simonwillison.net/2026/Jul/16/kimi-k3/)。

## 来源集合

| 来源 | 日期及版本 | 收录方式与许可 |
| --- | --- | --- |
| [Simon Willison：Pelicans on a bicycle](https://simonwillison.net/2024/Oct/25/pelicans-on-a-bicycle/) | 2024-10-25 | 经典短提示与 GPT-4o 原作链接；未定位到覆盖作品的再分发许可 |
| [Nebius cookbook](https://github.com/nebius/token-factory-cookbook/tree/45408f6508d2e9ff6f8c1fe9e9b8254d2a6879e5/fun/pelican-riding-bicycle) | 固定提交 `45408f6`，2026-10-30 | 19 个原 SVG，MIT；实际生成时间未知 |
| [bbinwang 本地 Qwen 图集](https://github.com/bbinwang/pelican-bicycle/tree/b14acbb2ebe9d9be5f0dec3d35d8f4ae6fcaa432) | 固定提交 `b14acbb`，2026-08-16 | 5 个原 SVG，MIT；带采样和量化说明 |
| [GPT 系列对照网格](https://static.simonwillison.net/static/2026/openai-pelicans-october-2026.html) · [引用该网格的文章](https://simonwillison.net/2026/Oct/26/gpt-61-terra/) | 文章 2026-10-26 | GPT-6 Astra、GPT-6.1 Sol / Terra、GPT-6 Luna；仅链接 |
| [Claude 系列对照网格](https://static.simonwillison.net/static/2026/claude-pelicans-grid.html) | Fable 5.1：2026-09-01；Opus 5.5：2026-09-22 | 含不同推理档和未返回 SVG 的运行；仅链接 |
| [Kimi K3.1 Pro](https://simonwillison.net/2026/Oct/23/kimi-k31-pro/) | 2026-10-23 | 作者经 Moonshot API 运行，附 low / medium / max 原始记录；仅链接 |
| [Gemini 4 Argon](https://simonwillison.net/2026/oct/13/) | 2026-10-13 | 作者通过 llm-gemini 运行，附 low / medium / high 作品；仅链接 |
| [Tom Gally：30 道 SVG 题](https://gally.net/temp/20251107pelican-alternatives/index.html) | 2025-11，补入 GPT-5.2 Pro：2025-12-12 | 收录两个短提示和对应原作链接；未定位到输出的再分发许可 |
| [Brian Moore：AI World Clocks](https://clocks.brianmoore.com/) | 发布日期未注明，2026-10-31 读取 | 原站按分钟显示钟表，规定 2,000 token 和最低推理档；这里改写提示，未复制作品 |
| [Bouncing Ball in a Hexagon](https://github.com/lucrbvi/bouncing-ball-hexagon/tree/4532ab20e16b4320466cc75497c25affc8e54ea2) | 固定提交 `4532ab2`，2025-05-18 | MIT；仓库自述并非题目最初创作者。这里改写为 HTML，未复制其 Python 输出 |
| [kings0527：动画鹈鹕](https://github.com/kings0527/pelican-bicycle) | 发布日期未确认，2026-10-31 读取 | 作者明确未授予许可，只保留链接和运行条件 |
| [Rapidata：Text-to-SVG human feedback](https://huggingface.co/datasets/Rapidata/text-to-svg-human-feedback-benchmark) | 2026-10-31 检查 | CC BY 4.0，含模型标签、作品对与偏好/一致性/题意对齐反馈；适合后续分层抽样，本批未下载 |

近期作品的模型名称来自原作者实测记录，未由 API-MRI 独立认证。Nebius 和 bbinwang 的文件逐一匹配了固定提交中的 Git blob SHA-1，并另存 SHA-256；这能核对下载的作品与原仓库一致。24 个文件均通过 XML 解析、根节点与脚本/链接检查，其中一个含 SMIL 动画。预览时仍按模型代码处理，由受限展示层加载。

## 数据格式

数据位于 `src/relay_audit/data/showcase/catalog.json`，分为任务和作品。任务保存提示、格式、出处、是否改写；作品保存模型标签、来源、运行条件、日期、许可和呈现方式。

- `display: bundled_asset`：含 `asset`、`sha256` 和第三方许可，文件随项目提供。
- `display: external_link`：没有 `asset`，只打开原作。
- `model_label_provenance.kind: author_reported`：模型名称由来源作者标注。
- `reported_settings`：只填写来源公开的设置；未知值为 `null`。
- `generated_at`、`source.published_at`、`source.snapshot_at`、`source.retrieved_at`：分别记录生成、发表、代码快照和收集日期。
- `source_based_validity`：记录原文件的语法检查。它不包含模型身份评分。

完整第三方许可和逐文件校验记录见 [showcase/LICENSES](../src/relay_audit/data/showcase/LICENSES/NOTICE.md)。扩充图集时，优先选带明确许可、原始代码和运行设置的作品；下载失败、拒答和截断同样入档，保留原作者所用的提示与回合数。
