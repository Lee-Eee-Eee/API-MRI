# 复用与许可

本项目的新增适配、证据记录和报告代码采用MIT许可。依赖通过包管理器安装，不将其源代码冒充本组实现。

| 项目 | 本版使用 | 原始来源及许可 |
| --- | --- | --- |
| Inspect AI 0.3.278 | Task、Sample、模型适配、执行、评分与评测日志 | [官方仓库](https://github.com/UKGovernmentBEIS/inspect_ai)，MIT；以实际安装版本记录为准 |
| Model Equality Testing 0.0.2 | CompletionSample、Unicode编码和原始两样本检验 | [官方仓库](https://github.com/irenasaracay/model-equality-testing)，[包内MIT许可](https://github.com/irenasaracay/model-equality-testing/blob/main/model_equality_testing/LICENSE)；原版权行有占位符，保留发布包原文，不补写作者身份 |
| KBF，本机评分代码固定于提交 68aefbbab879469152aaac9fd988a86c30ee4739 | 26 个经检查的完整参考包、原始提示与解析器、领域容差、格式校验和单侧二项统计评分 | [原始仓库](https://github.com/Ooo0ption/KBF/tree/68aefbbab879469152aaac9fd988a86c30ee4739)，Apache-2.0；许可及修改说明随 `src/relay_audit/vendor/kbf/` 分发 |
| ShadowAPI | 仅参考公开的对照实验和复用路线，本版没有复制其代码或数据 | [官方仓库](https://github.com/TrustAIRLab/ShadowAPI)；其自有代码与第三方题目分别适用许可 |

本版检查 KBF 九月目录的 28 份候选，收录其中与固定实现兼容且自测能复算的 26 份参考包，原始文件保持不变。完整数量与排除原因见[参考包清单](docs/reference-packs.md#内置参考包)，来源、采集及自测日期、许可链接和 SHA-256 保存在 `data/reference_packs/catalog.json`。模型、provider 与端点为作者记录，参考渠道未经本组独立认证；没有端点字段时保持未知。核查日期：2026年10月13日。

适配器直接调用 KBF 的 `extract_nums`、`validate_batch`、`check_match`、`probe_consensus` 和 `compute_verdict`，不把数值差异自行包装成模型身份概率。上游 `SAME` 被译为“未检出明显差异”，并不是模型身份证明。代码保留上游 Apache-2.0 许可与来源哈希；本项目 MIT 许可不替代这些许可。

有界传输替换上游自动网络重试：六种参数策略各最多一次，每批最多三次格式重试，禁用 50 倍输出预算回退。提示、共识值、解析和统计阈值保持上游定义。每次 HTTP 尝试先脱敏再存盘，离线复算从原始响应重建解析值；哈希仅用于一致性检查。

“抓取参考库更新”只读取固定 KBF 仓库的公开 JSON 和许可材料，每次记录冻结提交。检查该提交的许可与保留原文一致后，才安装兼容数据；缓存随数据保存 LICENSE、NOTICE 与来源记录。既有评分代码不会从网络更新。不同日期或内容的新参考分别保留，更新不构成对作者模型来源的独立认证。

本版未集成 LLMmap 或 IRIS。现成参考指纹不视为当前模型真值。

MET 0.0.2在包初始化时导入数据集模块，后者需要发布包元数据未声明的Transformers；本项目显式补充该运行依赖，以及MET使用的tqdm。这里只满足导入，不下载模型权重、分词器或原论文响应数据。

旧版 Inspect/MET demo 使用本组构造的文本 fixture，没有下载 MET 论文响应数据。指纹离线体验则按所选 KBF 参考构造候选回答，两者均不调用模型 API。MET 接口返回的原始 p 值另保留，本组添加逐题组织、有限次 Monte Carlo 修正和 Holm 校正；不重新实现其 MMD 或置换检验。
