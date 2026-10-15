# API-MRI 参考包：来源、原理与使用条件

参考包是提前采集的一组模型行为记录。API-MRI 随工具提供参考，因此使用者只需准备待测 API。参考包解决的是重复采集的成本，来源审核与版本维护仍然重要。

## 内置参考包

v0.4 收录 [KBF](https://github.com/Ooo0ption/KBF) 固定提交 `68aefbbab879469152aaac9fd988a86c30ee4739` 中 **26 份兼容参考包**，原始 JSON 保持不变。本次检查的九月目录共有 28 份候选，另 2 份自测汇总与逐题复算不一致，暂不收录。以下字段核查于 **2026年10月13日**；日期来自文件时间戳，原文件没有注明时区。

新增覆盖包含 GPT-6 Astra、Claude Opus 5、Claude Fable 5.1、DeepSeek V4 Pro 0813、Kimi K3、MiniMax M3 等作者记录的版本。目录表示已有参考覆盖；模型发布日期与参考生成日期分别理解。精确型号和原始数据见下表。

| 参考包 ID / 模型 | 生成 / 自测日期（2026年） | 题数 | 文件记录的参考端点 | 文件记录的提供方 |
| --- | --- | ---: | --- | --- |
| [`gpt-4.1-mini_20260915`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gpt-4.1-mini_20260915.json) · GPT-4.1 mini | 09-15 / 09-15 | 265 | OpenRouter | OpenAI |
| [`gpt-5.6-sol_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gpt-5.6-sol_20260912.json) · GPT-5.6 sol | 09-12 / 09-12 | 426 | 未记录 | OpenAI |
| [`claude-opus-4.6_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/claude-opus-4.6_20260912.json) · Claude Opus 4.6 | 09-12 / 09-12 | 445 | 未记录 | Amazon Bedrock |
| [`qwen3.5-9b_20260913`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/qwen3.5-9b_20260913.json) · Qwen3.5 9B | 09-13 / 09-16 | 240 | 未记录 | Together |
| [`claude-fable-5.1_20260911`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/claude-fable-5.1_20260911.json) · Claude Fable 5.1 | 09-11 / 09-11 | 453 | 未记录 | Anthropic |
| [`claude-opus-5_20260911`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/claude-opus-5_20260911.json) · Claude Opus 5 | 09-11 / 09-19 | 414 | 未记录 | Claude Platform on AWS |
| [`claude-sonnet-4.6_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/claude-sonnet-4.6_20260912.json) · Claude Sonnet 4.6 | 09-12 / 09-12 | 436 | 未记录 | Google |
| [`deepseek-v3.2_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/deepseek-v3.2_20260912.json) · DeepSeek V3.2 | 09-12 / 09-12 | 380 | 未记录 | Google |
| [`deepseek-v4-pro-0813_20260911`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/deepseek-v4-pro-0813_20260911.json) · DeepSeek V4 Pro 0813 | 09-11 / 09-11 | 557 | 未记录 | Novita |
| [`gemini-2.5-flash-lite_20260918`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gemini-2.5-flash-lite_20260918.json) · Gemini 2.5 Flash Lite | 09-18 / 09-18 | 319 | OpenRouter | Google |
| [`gemini-3-flash-preview_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gemini-3-flash-preview_20260912.json) · Gemini 3 Flash Preview | 09-12 / 09-12 | 409 | 未记录 | Google |
| [`glm-4.7_20260913`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/glm-4.7_20260913.json) · GLM-4.7 | 09-13 / 09-13 | 430 | 未记录 | Z.AI |
| [`glm-5_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/glm-5_20260912.json) · GLM-5 | 09-12 / 09-16 | 332 | 未记录 | Z.AI |
| [`gpt-4.1-nano_20260918`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gpt-4.1-nano_20260918.json) · GPT-4.1 nano | 09-18 / 09-18 | 248 | OpenRouter | OpenAI |
| [`gpt-5.4-mini_20260916`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gpt-5.4-mini_20260916.json) · GPT-5.4 mini | 09-16 / 09-16 | 194 | OpenRouter | OpenAI |
| [`gpt-5.4_20260913`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gpt-5.4_20260913.json) · GPT-5.4 | 09-13 / 09-19 | 531 | OpenRouter | OpenAI |
| [`gpt-5.6-luna_20260915`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gpt-5.6-luna_20260915.json) · GPT-5.6 Luna | 09-15 / 09-15 | 305 | OpenRouter | OpenAI |
| [`gpt-6-astra_20260910`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gpt-6-astra_20260910.json) · GPT-6 Astra | 09-10 / 09-11 | 538 | 未记录 | OpenAI |
| [`hy4-preview_20260911`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/hy4-preview_20260911.json) · Hunyuan HY4 Preview | 09-11 / 09-11 | 538 | 未记录 | Tencent |
| [`kimi-k2-0905_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/kimi-k2-0905_20260912.json) · Kimi K2 0905 | 09-12 / 09-13 | 363 | 未记录 | Novita |
| [`kimi-k3_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/kimi-k3_20260912.json) · Kimi K3 | 09-12 / 09-12 | 424 | 未记录 | Moonshot AI |
| [`llama-4-scout_20260918`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/llama-4-scout_20260918.json) · Llama 4 Scout | 09-18 / 09-18 | 231 | OpenRouter | Novita |
| [`minimax-m3_20260911`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/minimax-m3_20260911.json) · MiniMax M3 | 09-11 / 09-16 | 311 | 未记录 | Minimax |
| [`qwen3.5-27b_20260913`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/qwen3.5-27b_20260913.json) · Qwen3.5 27B | 09-13 / 09-16 | 149 | 未记录 | Alibaba |
| [`qwen3.5-397b-a17b_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/qwen3.5-397b-a17b_20260912.json) · Qwen3.5 397B A17B | 09-12 / 09-16 | 254 | 未记录 | Alibaba |
| [`seed-2-1-turbo_20260911`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/seed-2-1-turbo_20260911.json) · Seed 2.1 Turbo | 09-11 / 09-11 | 400 | 未记录 | Seed |

其中 7 份明确记录 OpenRouter 参考端点，19 份未记录完整端点。提供方来自作者保存的 `provider_map`，与模型名称、API 端点是不同字段；未记录端点时保持未知，不能只靠提供方名称补写成厂商直连。本组尚未独立认证这些采集来源。

本地 [catalog.json](../src/relay_audit/data/reference_packs/catalog.json) 保存每包的完整模型名、日期、原始链接、提交和 SHA-256。运行时检查文件哈希，报告和原始响应记录携带参考包身份；哈希确认文件版本一致，不认证作者的来源声明。

### 两份未收录的候选

| 作者文件 | 原文件自测不匹配数 | 本地逐题复算 | 处理 |
| --- | ---: | ---: | --- |
| [deepseek-v4.1-flash_20260911.json](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/deepseek-v4.1-flash_20260911.json) | 29 | 28 | 保留来源与原因，等待上游修正或进一步核查 |
| [glm-5.3_20260911.json](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/glm-5.3_20260911.json) | 27 | 26 | 保留来源与原因，等待上游修正或进一步核查 |

这两份分别为 DeepSeek V4.1 Flash 和 GLM-5.3。自测字段不一致影响基线复算，因此不进入可用列表；这项检查不判定作者的模型来源。完整检查摘要见 [update-audit.json](../src/relay_audit/data/reference_packs/update-audit.json)。

## 它具体比较什么

KBF 全称 Knowledge Boundary Fingerprinting，即“知识边界指纹”。它挑选一组数值填空探针，例如某个事实对应的年份或数值，观察模型如何回答。不同模型可能在不同问题上表现出稳定的回答模式；这些模式可用于比较。探针中的参考值来自模型响应，不等同于人工核验的事实答案。[上游原理与流程](https://github.com/Ooo0ption/KBF/tree/68aefbbab879469152aaac9fd988a86c30ee4739)

一份包包含探针、参考值和模型再次回答自身探针的自测结果。待测端按相同提示与参数回答后，程序解析数值，按各领域容差与参考值比较。报告中的“不匹配率”表示偏离参考的比例，不是知识错误率。

KBF 的判据先根据参考自测的不匹配数计算保守基线，再对待测端做单侧二项检验：若偏离显著高于基线，返回 `DIFF`；否则返回 `SAME`。参考或候选可解析覆盖不足时返回 `UNDETERMINED`。当前沿用上游显著性阈值 0.05、Clopper–Pearson 99% 上界和最低覆盖率 0.5。[判据源码](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/scripts/kbf_test.py)

`SAME` 表示本轮未拒绝相容性；不是“同一模型的概率”，也不认证权重。API-MRI 使用“未检出明显差异”表达这个状态。v0.5 在完整模式中增加完整性要求：计划内任一题目未取得可解析回答，均保留“证据不足”。上游的 50% 最低覆盖率不能单独满足本工具的报告要求。

v0.5 默认采用低成本初筛。它确定性地跨领域抽取最多三个原始批次，保留批次提示、温度和输出配额；报告匹配、差异与缺失数量，不计算 KBF 的 p 值，也不返回 SAME / DIFF。这个抽取方案用于控制成本，尚未通过真实模型实验校准。完整参考比较需要主动选择；名义显著性阈值并不等于已测得的实际误报率。

## 从参考包到一次请求

本版直接复用 KBF 的提示构造、数值解析、领域容差和统计评分。采集层增加请求预算、超时、取消、落盘前密钥脱敏以及可复算记录。上游代码的来源和保留说明见 [vendor/kbf/NOTICE](../src/relay_audit/vendor/kbf/NOTICE)。

每批最多 10 道探针。为适配接口，流程最多尝试 6 种推理设置，每种至多一次；初筛每个题组只尝试一次，完整模式每组最多三次。所有试探、失败和格式尝试均计入用户预算，没有网络自动重试，也不自动扩大输出配额。下一笔完整请求超过剩余额度时停止，报告保留已采集结果和预算停止原因。

初筛默认上限为 4 次请求、4,096 token 累计请求输出配额。输出配额是请求的 `max_tokens` 之和，不是服务实际计量或账单上限；实际 usage 另列，缺失值保留未知。完整模式省略自定预算时使用整份原始计划上限，运行前可调整。

参数与提示需保持匹配。模型版本更新、别名重新指向、提供方变更、系统提示改写或输出限制都可能影响结果。遇到这些变化时，先记录条件，再决定重新采集参考还是用双端点模式复核。

## 离线复算保留了什么

`fingerprint_records.jsonl` 按运行说明（`manifest`）、请求（`request`）和结束状态（`finish`）保存证据。内容包括批次原始响应、尝试记录、参考包及哈希、运行标识和完整设置，各行通过哈希链连接。v0.5 另保存模式、固定选题和预算。复算从原始响应重新解析，不直接信任已经填好的分数；历史记录省略模式时按原完整流程复算。程序拒绝跨参考包、跨运行或设置不一致的拼接记录。

离线复算提高了分析过程的可检查性，但不能证明上传响应确由某服务器生成。研究者仍需保留采集背景、时间与渠道材料。文件字段由工具管理，推荐原样导入导出，操作见[使用指南](user-guide.md#保存分享和离线复算)。

## 参考库如何更新

v0.4 提供“抓取参考库更新”按钮和 `api-mri references-update` 命令。更新从固定的 KBF 作者仓库获取公开数据：先将主分支冻结为一个 Git 提交，再扫描 `probes/reference_YYYYMM/` 按月目录中的 JSON。来源提交、原始路径、访问时间和 SHA-256 随每份数据保存；同名文件内容发生变化时，使用带哈希后缀的新 ID，旧版本继续保留。

数据进入可用列表前，需要通过以下检查：

- 文件大小、结构和必需字段符合本地要求，数值与日期可解析。
- 题组、固定提示、领域定义和自测记录能与当前 KBF 实现对应，逐题自测及汇总可以复算。
- 下载内容与冻结提交的 Git 文件哈希一致，许可文本与随工具保留的 Apache-2.0 原文一致。

不兼容的数据会跳过并说明原因。更新详情区分新增、已有、跳过和失败；没有新包时保持现有列表，网络失败或限流时也可继续使用旧参考。抓取设置限定来源、文件类型、下载量及等待时间，只获取 JSON 与许可材料，不下载或执行远程代码，也不自动调用任何模型。

新模型需要有人先采集参考并记录配置与自测。这个按钮可以取得已有的数据，不能按一个名称自动产生可靠基线。若上游评分实现变化，本机代码仍保持发布版本，需要另行核查和适配。操作和缓存位置见[使用指南](user-guide.md#抓取参考库更新)。

后续值得验证的是参考随时间和配置变化的稳定性，以及面对参考库未收录模型时的表现。当前尚未完成真实模型的误报和检出能力校准；相关实验需另行确定账户、预算和设计。

## 许可

上述内置 KBF 代码与数据按固定提交的 Apache-2.0 许可保留，原许可见 [LICENSE](../src/relay_audit/vendor/kbf/LICENSE)，本组适配代码仍采用 MIT。重新分发时保留相应许可和来源说明。其他候选方法如 LLMmap、MET 历史响应集和 IRIS 尚未作为单 API 参考库打包进本项目，不混用其数据许可。

更新时，文件 Git blob 哈希未变的已安装参考不重复下载。发行版已核查并排除的两个文件也按原始哈希复用排除记录；上游内容变化后重新下载和检查，不把旧排除结果沿用到新内容。
