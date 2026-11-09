# 指纹来源核查：新模型在哪里补

2026年11月9日核查。PAMELA的公开采样仍停留在7月，Zenodo的最新版本接口指向原归档。API-MRI保留其中132个历史标签，另加入11月9日通过DSH采集的Flash、Pro两条通道快照，组成134候选的滚动库；两批样本分别保存采集日期与来源。[数据归档](https://zenodo.org/records/21278557) · [最新版本接口](https://zenodo.org/api/records/21278557/versions/latest) · [本轮采集结果](current-reference-pilot-2026-11-09.json)

## 已找到的数据

| 来源 | 核查到的实际内容 | 在API-MRI中的用法 |
| --- | --- | --- |
| PAMELA | 2026年7月6—8日采集，165个作者模型标签；10类任务、4种语言；数据CC BY 4.0，配置代码MIT | 继续保留历史库与原阈值。固定共同提示可供新参考采样复用。[来源](https://zenodo.org/records/21278557) |
| KBF | 9月目录中有28份指定型号参考，Apache-2.0；当前提交只比原固定提交多一项README修改，原始参考未更新 | 本轮补回两份可复现的旧规则参考，合计28份。用于指定型号对照，不拼入PAMELA共同库。[固定提交](https://github.com/Ooo0ption/KBF/tree/6b4c403def2498cb92b7bd51ad958ddee5b3d06f/probes/reference_202609) |
| ModelTrace | 18个型号、648条回答，每型号36条，覆盖12种提示环境；原始时间从8月27日到10月9日；MIT | 每次生成数百个整数，协议与短词探针不同，保留为独立方法参照。[固定提交](https://github.com/xqy2006/ModelTrace/tree/6cd4a8ff0f3f4e49d6d6969dd2fbadfa1569fde2/data) |
| hlwy-ai-checker | 8个社区标签的1—355单整数分布；Sonnet 5.5的文件日期为11月9日，Flash-1017为10月18日，各200个整数；LGPL-2.1 | 数据缺端点、逐请求参数和提示版本，先保留来源线索，不纳入默认识别。[固定提交](https://github.com/hanlinwenyuan/hlwy-ai-checker/tree/a5e123547d830bf6361926cfc658ee81b45542e0/baselines) |

ModelTrace原始记录将8个GPT标签的渠道写为`codex`、10个Claude标签写为`oaipro`；这些字段是采集者记录。WhatsMyLLM又基于ModelTrace扩充了24型号的长整数库，但其提示和判定程序仍与本项目的短探针不同。[原始GPT记录](https://github.com/xqy2006/ModelTrace/blob/6cd4a8ff0f3f4e49d6d6969dd2fbadfa1569fde2/data/gpt_reference.jsonl) · [原始Claude记录](https://github.com/xqy2006/ModelTrace/blob/6cd4a8ff0f3f4e49d6d6969dd2fbadfa1569fde2/data/claude_reference.jsonl) · [WhatsMyLLM方法](https://whatsmyllm.com/methodology/)

## 两份KBF参考为何曾被排除

Flash参考的自测记录为29处不匹配，按当前程序复算只有28处；GLM 5.3为27处与26处。原因已经定位到KBF的计分规则变更：9月29日的代码把绝对误差领域改为先进行半向上整数舍入，5月版直接比较原数值。Flash的一题比较1.0与1.1，GLM的一题比较10与9.95，两组数在舍入后相同。[旧规则](https://github.com/Ooo0ption/KBF/blob/b789b4b7abe119e28ec6260142564b2189ff5449/scripts/kbf_common.py) · [9月规则](https://github.com/Ooo0ption/KBF/blob/df361a373d35120d64b28d9d9974bc7c6709f19f/scripts/kbf_common.py)

独立复算旧公式后，两份文件的完整自测向量和全部计数逐项相符。本轮以`legacy_absolute_raw`标记它们，待测回答和离线复算也采用同一规则。原始JSON、自测计数和采集日期保留；原26份参考继续使用`absolute_half_up`。不符合任一完整校准向量的文件仍被拒绝。[校验台账](../src/relay_audit/data/reference_packs/update-audit.json)

## 同一个调用名会换版本

DeepSeek官方记录：9月10日发布V4.1 Flash，10月17日将`deepseek-flash`升级为V4.1-Flash-1017，调用名保持不变。因此9月11日的KBF Flash样本不能作为1017版本的参考。新参考需要同时保存调用名、渠道、采集日期和当时供应商公布的版本对应关系。[官方更新日志](https://api-docs.deepseek.com/zh-cn/updates/) · [1017发布页](https://api-docs.deepseek.com/zh-cn/news/news261017/)

## 本轮加入的两条通道快照

两条通道均来自DSH中标为`deepseek-official`的配置。每个型号固定尝试8题，每题8次参考、4次留出，共96次请求、1536累计申请输出配额。语义无效、拒答和空回答按实记录，不为凑足有效数补请求。Flash得到63条有效参考、31条有效留出，Pro为64条与32条；合计192次请求、3072输出配额，供应商报告8112输入、326输出tokens。Flash原先中断的62次记录已复用，续采只新增34次。[采集与维护流程](reference-maintenance.md)

用各自完整的四回答留出单元排序时，Flash使用7个单元，Pro使用8个单元，两者在134候选中均排第一。Pro只用前3个单元时排第12，用前4个单元时才排第一。这是同通道、同一采集时段的留出检查，尚未测得跨日、跨渠道或未知模型的识别表现。[逐项结果](current-reference-pilot-2026-11-09.json)

滚动快照为`rolling-20261109-core8-v1`，SHA-256为`dfb9bc9f1f11c81aca036c710ad5e9312152cc03088f8bc2e43b930f4b319740`；文件摘要与公开索引一致。八组提示保持PAMELA原文，历史计数保留原作者归属；新增聚合计数由API-MRI贡献者以CC BY 4.0发布。通道名称是来源声明，快照保留`identity_authenticated: false`和`open_set_calibrated: false`。[公开索引](../src/relay_audit/data/identification/public-index.json) · [归属与许可](../src/relay_audit/data/identification/NOTICE)

后续新型号沿用固定共同题，按用户预算手动采样。工作台每7天检查本项目公布的JSON，已有摘要的快照跳过下载；已有回答可以换库重排，无需再调用模型。跨日快照的漂移检测仍是后续工作，当前没有专门的漂移检查功能。七月全库阈值继续对应原库。

公开来源的链接、固定提交、原始文件摘要及逐项复算结果见[来源台账](fingerprint-update-sources-2026-11-09.json)。该来源核查没有执行下载的程序或调用模型；另行完成的两条通道采集记录在上述试采结果中。
