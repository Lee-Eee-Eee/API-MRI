# 参考包：来源、原理与使用条件

参考包是提前采集的一组模型行为记录。Relay Audit 随工具提供参考，因此使用者只需准备待测 API。参考包解决的是重复采集的成本，来源审核与版本维护仍然重要。

## 内置参考包

本版收录 [KBF](https://github.com/Ooo0ption/KBF) 固定提交 `68aefbbab879469152aaac9fd988a86c30ee4739` 的 4 份原始 JSON，未改写其内容。以下字段核查于 **2026年10月13日**；日期来自文件时间戳，原文件没有注明时区。

| 参考包 ID / 模型 | 生成 / 自测日期 | 题数 | 文件记录的参考端点 | 文件记录的提供方 |
| --- | --- | ---: | --- | --- |
| [`gpt-4.1-mini_20260915`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gpt-4.1-mini_20260915.json) · GPT-4.1 mini | 09-15 / 09-15 | 265 | OpenRouter | OpenAI |
| [`gpt-5.6-sol_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/gpt-5.6-sol_20260912.json) · GPT-5.6 sol | 09-12 / 09-12 | 426 | 未记录 | OpenAI |
| [`claude-opus-4.6_20260912`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/claude-opus-4.6_20260912.json) · Claude Opus 4.6 | 09-12 / 09-12 | 445 | 未记录 | Amazon Bedrock |
| [`qwen3.5-9b_20260913`](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/probes/reference_202609/qwen3.5-9b_20260913.json) · Qwen3.5 9B | 09-13 / 09-16 | 240 | 未记录 | Together |

这些提供方来自作者保存的 `provider_map`；它与参考模型名称、API 端点是不同字段。GPT-4.1 mini 的 `reference_endpoint` 记录为 `https://openrouter.ai/api/v1/chat/completions`。另外 3 份没有该字段，不能只靠提供方名称补写成厂商直连。当前本组尚未独立认证这些采集来源。

本地 [catalog.json](../src/relay_audit/data/reference_packs/catalog.json) 保存每包的完整模型名、日期、原始链接和 SHA-256。运行时检查文件哈希，报告和原始响应记录携带参考包身份；哈希确认文件版本一致，不认证作者的来源声明。

## 它具体比较什么

KBF 全称 Knowledge Boundary Fingerprinting，即“知识边界指纹”。它挑选一组数值填空探针，例如某个事实对应的年份或数值，观察模型如何回答。不同模型可能在不同问题上表现出稳定的回答模式；这些模式可用于比较。探针中的参考值来自模型响应，不等同于人工核验的事实答案。[上游原理与流程](https://github.com/Ooo0ption/KBF/tree/68aefbbab879469152aaac9fd988a86c30ee4739)

一份包包含探针、参考值和模型再次回答自身探针的自测结果。待测端按相同提示与参数回答后，程序解析数值，按各领域容差与参考值比较。报告中的“不匹配率”表示偏离参考的比例，不是知识错误率。

KBF 的判据先根据参考自测的不匹配数计算保守基线，再对待测端做单侧二项检验：若偏离显著高于基线，返回 `DIFF`；否则返回 `SAME`。参考或候选可解析覆盖不足时返回 `UNDETERMINED`。当前沿用上游显著性阈值 0.05、Clopper–Pearson 99% 上界和最低覆盖率 0.5。[判据源码](https://github.com/Ooo0ption/KBF/blob/68aefbbab879469152aaac9fd988a86c30ee4739/scripts/kbf_test.py)

`SAME` 表示本轮未拒绝相容性；不是“同一模型的概率”，也不认证权重。Relay Audit 使用“未检出明显差异”表达这个状态，并在采集被取消、失败或截断时保留“证据不足”。

## 从参考包到一次请求

本版直接复用 KBF 的提示构造、数值解析、领域容差和统计评分。采集层增加请求预算、超时、取消、落盘前密钥脱敏以及可复算记录。上游代码的来源和保留说明见 [vendor/kbf/NOTICE](../src/relay_audit/vendor/kbf/NOTICE)。

每批最多 10 道探针。为适配接口，流程最多尝试 6 种推理设置，每种至多一次；每个探针批次最多进行 3 次格式尝试，没有网络自动重试。拒绝上游扩大输出预算的自动回退。运行前的计划按最坏情况列出请求与输出上限，运行结果另记录实际发送量。

参数与提示需保持匹配。模型版本更新、别名重新指向、提供方变更、系统提示改写或输出限制都可能影响结果。遇到这些变化时，先记录条件，再决定重新采集参考还是用双端点模式复核。

## 离线复算保留了什么

`fingerprint_records.jsonl` 按运行说明（`manifest`）、请求（`request`）和结束状态（`finish`）保存证据。内容包括批次原始响应、尝试记录、参考包及哈希、运行标识和完整设置，各行通过哈希链连接。复算从原始响应重新解析，不直接信任已经填好的分数。程序拒绝跨参考包、跨运行或设置不一致的拼接记录。

离线复算提高了分析过程的可检查性，但不能证明上传响应确由某服务器生成。研究者仍需保留采集背景、时间与渠道材料。文件字段由工具管理，推荐原样导入导出，操作见[使用指南](user-guide.md#保存分享和离线复算)。

## 参考库如何更新

本版使用固定包，不在启动时自动下载或执行远程代码。新增参考包需要核查原始许可与出处、模型版本、采集渠道、生成设置、自测覆盖及文件哈希，并通过本地格式与复算测试。现有包的变化应使用新版本，保留旧文件对应的历史报告。

后续值得验证的是参考随时间和配置变化的稳定性，以及面对参考库未收录模型时的表现。当前尚未完成真实模型的误报和检出能力校准；相关实验需另行确定账户、预算和设计。

## 许可

上述 KBF 代码与数据按其仓库的 Apache-2.0 许可保留，原许可见 [LICENSE](../src/relay_audit/vendor/kbf/LICENSE)，本组适配代码仍采用 MIT。重新分发时保留相应许可和来源说明。其他候选方法如 LLMmap、MET 历史响应集和 IRIS 尚未作为单 API 参考库打包进本项目，不混用其数据许可。
