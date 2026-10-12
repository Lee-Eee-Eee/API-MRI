# 复用与许可

本项目的新增适配、证据记录和报告代码采用MIT许可。依赖通过包管理器安装，不将其源代码冒充本组实现。

| 项目 | 本版使用 | 原始来源及许可 |
| --- | --- | --- |
| Inspect AI 0.3.278 | Task、Sample、模型适配、执行、评分与评测日志 | [官方仓库](https://github.com/UKGovernmentBEIS/inspect_ai)，MIT；以实际安装版本记录为准 |
| Model Equality Testing 0.0.2 | CompletionSample、Unicode编码和原始两样本检验 | [官方仓库](https://github.com/irenasaracay/model-equality-testing)，[包内MIT许可](https://github.com/irenasaracay/model-equality-testing/blob/main/model_equality_testing/LICENSE)；原版权行有占位符，保留发布包原文，不补写作者身份 |
| ShadowAPI | 仅参考公开的对照实验和复用路线，本版没有复制其代码或数据 | [官方仓库](https://github.com/TrustAIRLab/ShadowAPI)；其自有代码与第三方题目分别适用许可 |

本版未集成LLMmap、KBF或IRIS。它们的参考库、数据和许可需分别核查；现成指纹不视为当前模型真值。

MET 0.0.2在包初始化时导入数据集模块，后者需要发布包元数据未声明的Transformers；本项目显式补充该运行依赖，以及MET使用的tqdm。这里只满足导入，不下载模型权重、分词器或原论文响应数据。

本版demo使用本组构造的文本fixture，没有下载第三方模型响应数据。MET接口返回的原始p值另保留，本组添加逐题组织、有限次Monte Carlo修正和Holm校正；不重新实现其MMD或置换检验。
