# 指纹参考怎样更新

默认比较库新增了2026年11月9日采集的两个服务快照：DSH原厂适配器中的 `deepseek-flash` 和 `deepseek-v4-pro`。它们与原132个历史标签共用8组短探针，组成134候选的滚动库。七月历史库单独保留。

采样前，“参考覆盖”显示这个调用名有没有同协议样本、实际采集日期和有效重复数。`deepseek-flash`这类别名可以指向更新后的版本，界面将它标为通道快照；版本对应关系随来源记录查看。

## 日常使用

进入“模型指纹”，选择比较库和待测型号，查看计划后开始。近期库按距离列候选；七月库继续使用其历史校准。切换参考后，计划会重新生成。

已有报告可以点“用当前库重排”。程序复用原回答与新库共同的完整单元，列出新候选；本次模型调用为零。导出文件带上原始记录链与目标参考快照，原报告保留。

“同步公开指纹”只下载本项目公布的JSON数据。工作台每7天检查一次，已有SHA-256的快照跳过下载；手动同步可立即检查。已手选的参考版本继续保留，新版本在列表中可选。程序代码、模型权重和API凭据不进入这条更新链。

## 给新型号补参考

1. 切到“补采参考”，选择DSH已配置模型，填写型号与通道声明。
2. 查看计划：8组探针，每组8次建参考、4次独立留出，共96次短请求，最多申请1536输出tokens。
3. 点击开始。采完核对来源、有效回答与留出情况，再点“发布并使用”。

采样始终按计划尝试，语义无效、拒答和空回答单列，不为凑足有效数重复请求。网络错误、返回推理、整段缓存或截断会停止采样。停止后符合续采条件的记录可继续剩余请求；界面分别列出保留记录和新增费用配额。

新参考至少有6/8组各得到5条有效参考，整个候选集合共同可比单元也须至少6组；另须至少3组取得4条有效留出。留出回答不进入参考计数。其他型号沿用已有数据，每次只采所选型号。

每轮硬上限为256请求、4096输出配额，仅手动启动。输出配额限制请求里的`max_tokens`之和，输入和服务商计费另记。来源标为操作者或原厂通道声明，模型回答本身不会认证来源身份。

## 本轮采集与检查

| 2026年11月9日的通道 | 有效参考 | 有效留出 | 在134候选中的留出排名 |
| --- | ---: | ---: | ---: |
| DeepSeek-V4.1-Flash | 63 | 31 | 1 |
| DeepSeek-V4-Pro | 64 | 32 | 1 |

两次采集合计192请求，累计申请3072输出配额；供应商返回8112输入、326输出tokens，账单金额未查询。Flash中断前的62次记录已复用，续采只新增34次。其两条格式外回答仍保留在证据中。

这次检查在同通道、同一采集时段进行。滚动库尚未完成跨日、跨渠道和未知模型校准，报告保留身份未定，历史库的接受门槛不套到新库。[逐项结果](current-reference-pilot-2026-11-09.json)

## 命令行维护

配置用`candidate.api_key_env`引用环境变量。采集配置另含`source`，例如：

```json
{
  "candidate": {"api_base": "https://api.example.com/v1", "model": "model-id", "api_key_env": "REFERENCE_KEY"},
  "source": {"kind": "operator_declared", "label": "服务型号名称", "provider": "服务名称", "model_alias": "model-id"},
  "budget": {"max_requests": 96, "max_output_tokens": 1536}
}
```

```bash
api-mri reference-plan --config reference.json
api-mri reference-collect --config reference.json --out runs/reference-01
api-mri reference-publish --records runs/reference-01/reference_records.jsonl --activate
api-mri identification-sync --force
api-mri identify-recompare --records runs/identify-01/identification_records.jsonl --bank-id rolling-20261109-core8-v1 --out runs/reordered-01
```

CLI识别配置可用`bank_id`选库；随包示例已经选用11月滚动库。旧配置省略该字段时继续使用七月库。

维护者可在公开来源和许可核对后，将数据快照与摘要加入`data/identification/public-index.json`。客户端只从固定项目地址同步，逐份核对摘要和协议；本地采集不会自动上传GitHub。不同提示的KBF、ModelTrace和社区单整数数据分别管理。[本轮来源核查](fingerprint-sources-2026-11-09.md)
