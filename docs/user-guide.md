# 使用指南

Relay Audit 用预先采集的参考回答检查一个 API。你选择参考包、填写待测服务，工具发送相同探针，保存回答并生成报告。安装步骤见 [README](../README.md)，参考数据的原理与来源见[参考包说明](reference-packs.md)。

## 先选择一种方式

| 方式 | 需要准备 | 是否调用模型 API | 用途 |
| --- | --- | --- | --- |
| 单 API 检测 | 一份参考包、待测地址、模型名和 Key | 是，可能计费 | 检查待测服务与已有参考的行为差异 |
| 离线体验 | 选择参考包与示例情境 | 否 | 用人工回答熟悉三个结果状态 |
| 指纹记录复算 | 原来使用的参考包及一份 JSONL | 否 | 从保存的原始响应重新解析和判断 |
| 双端点比较 | 两端地址、Key 和匹配的模型版本 | 是，可能计费 | 自己采集参考，比较两端逐题回答分布 |
| 机制实验台 | 选择一个情境 | 否 | 观察替换、路由、提示与故障造成的现象 |

工作台默认地址为 `http://127.0.0.1:8765`。页面和分析进程运行在你的电脑上。保留启动它的终端，按 `Ctrl+C` 关闭；端口冲突可用 `relay-audit ui --port 8766`。

## 完成第一次单 API 检测

### 1. 先看一个无密钥示例

打开“离线体验”，选择参考包，再选择匹配、偏离或记录不足的示例。程序在本机生成候选回答，通过同一条解析和报告流程运行。这样可以先看懂报告、逐题表和下载内容，之后再使用自己的账户。

三个示例对应 `match`、`different` 和 `incomplete`。这些响应由程序构造，用来展示结果状态；示例通过不等于真实模型的准确率已得到验证。

### 2. 选择参考包

以服务承诺提供的模型和版本为准。包详情包含参考模型、采集及自测日期、提供方、题数和来源链接。模型别名可以不同，但应先核对它们指向哪个版本。

目前内置 4 份包，见[来源清单](reference-packs.md#内置参考包)。列表里没有匹配版本时，使用双端点比较建立自己的对照；不要挑一个名称相近的包当作同版本基线。

### 3. 填写待测服务

| 字段 | 填写方式 |
| --- | --- |
| API 地址 / Base URL | 如 `https://api.example.com/v1`；也接受完整的 `/chat/completions` 地址。不要填聊天网页地址 |
| 模型名称 / model | 待测服务实际接受的模型名或别名，如 `gpt-4.1-mini` |
| API Key | 该服务的调用凭据；界面按密码输入，不写入浏览器持久化存储 |
| 请求超时 / timeout | 单次请求等待上限，默认 30 秒 |

当前单 API 模式使用非流式 OpenAI 兼容 Chat Completions。远程地址使用 HTTPS，本机回环地址可以使用 HTTP。URL 不接受用户名、密码、查询串或片段。

题目、系统提示和采样参数由参考检测流程固定，减少设置不同造成的偏差。接口必须接受这些请求；若服务只支持其他协议，或依赖特定客户端处理，需要先解决兼容问题。

### 4. 核对计划再运行

点击“核对采样计划”，确认参考包、地址、请求模型和请求上限，再点击“开始检测”。探针按批发送，每批最多 10 道题，因此题数与 HTTP 请求数不同。工具还会尝试兼容的推理设置，并在格式无法解析时有限重试；计划展示的是包含这些尝试的最大请求量。

输出 token 上限只计算本工具允许请求的输出额度，输入及服务另行计费的项目仍需查看套餐。先确认账户允许自动 API 调用。

采集中可查看进度并取消。取消后，正在发送的请求可能仍需完成或超时，已发请求仍可能计费。完成的响应会保留；被取消、失败或截断的采集按“证据不足”报告。当前版本不自动续跑中断任务，修正后使用新目录重新运行。

### 5. 阅读结果和逐题记录

| 页面结果 | 如何理解 |
| --- | --- |
| 发现显著差异 | 候选响应对参考的偏离超过本次判据；先查差异题目，再核对模型版本、配置和请求处理 |
| 未检出明显差异 | 本次探针没有发现超过参考自测波动的偏离；统计标签为 `SAME`，含义是未拒绝相容性 |
| 证据不足 | 已有记录不足以完成本次判断；根据报告检查覆盖、解析、截断、取消或连接问题 |

报告里的“匹配”指回答与参考包中的数值是否相符，不是知识题的客观正确率。参考包记录的是模型行为，也可能包含模型稳定给出的错误答案。结果支持的是这组探针上的一致性判断，模型权重来源仍需其他证据。

重点看三个量：**覆盖率**表示多少题取得可解析回答，**不匹配率**表示可比较回答中有多少偏离参考，**参考自测偏离**表示参考模型再次回答自身探针时原有的波动。逐题表可展开查看题目、参考值、候选值和原始响应，避免只看一个标签。

## 保存、分享和离线复算

工作台默认将每次运行保存到 `runs/ui/` 下的独立目录，可在历史结果中重新打开。启动时可用 `--runs-dir path/to/runs` 更换位置。

单 API 模式提供以下文件：

| 文件 | 用途 |
| --- | --- |
| `report.md` | 便于阅读和分享的结论、来源与逐题结果 |
| `report.json` | 结构化报告、参数及参考包信息 |
| `prompts.csv` | 逐题汇总，适合表格分析 |
| `fingerprint_records.jsonl` | 每批原始响应与尝试记录，用于离线重新解析 |
| `study.json` | 可选的案例与商业承诺档案 |

在“指纹记录复算”中选原来的参考包，上传 `fingerprint_records.jsonl`，界面每份最大 **4 MB**；CLI 导入上限为 **20 MiB**。复算不会再次调用模型。程序检查参考包、运行标识、设置与记录哈希链，拒绝将不同运行或参考包的记录混在一起。普通聊天导出、旧版双端点 JSONL 和 `report.json` 不能代替这份文件。

分享课堂结果时，通常提供 Markdown 报告和必要的逐题数据。工具会在持久化前清除已知 Key；响应中其他私人内容仍需分享者检查。不要把 API Key 放进案例备注、截图或问题反馈。

## 在命令行中提供密钥

将 [examples/fingerprint.example.json](../examples/fingerprint.example.json) 复制为 `fingerprint.json`，修改地址和模型。配置里的 `api_key_env` 填环境变量名称，CLI 会拒绝明文 `api_key`。

```json
{
  "reference_pack": "gpt-4.1-mini_20260915",
  "candidate": {
    "api_base": "https://api.example.com/v1",
    "model": "gpt-4.1-mini",
    "api_key_env": "CANDIDATE_API_KEY"
  },
  "timeout": 30
}
```

PowerShell 中可交互输入，避免把值写进命令历史：

```powershell
.\.venv\Scripts\relay-audit.exe fingerprint-plan --config fingerprint.json
$candidateCredential = Get-Credential -UserName 'candidate' -Message '在密码框中粘贴待测端 API Key'
$env:CANDIDATE_API_KEY = $candidateCredential.GetNetworkCredential().Password
.\.venv\Scripts\relay-audit.exe fingerprint --config fingerprint.json --out runs/fingerprint-01
Remove-Item Env:CANDIDATE_API_KEY
```

Linux/macOS 在已激活虚拟环境的终端进入 Bash 后输入：

```bash
relay-audit fingerprint-plan --config fingerprint.json
bash
read -r -s -p 'Candidate API key: ' CANDIDATE_API_KEY
echo
export CANDIDATE_API_KEY
relay-audit fingerprint --config fingerprint.json --out runs/fingerprint-01
unset CANDIDATE_API_KEY
exit
```

`fingerprint-plan` 不读取 Key、不发送请求。示例和复算也不需要 Key：

```bash
relay-audit references
relay-audit fingerprint-demo --reference-pack gpt-4.1-mini_20260915 --variant different --out runs/example-01
relay-audit fingerprint-replay --reference-pack gpt-4.1-mini_20260915 --records runs/example-01/fingerprint_records.jsonl --out runs/replay-01
```

每次 `--out` 使用一个新目录，已有目录会被拒绝覆盖。

## 将服务承诺放进报告

工作台提供可选的服务研究档案：填写产品、套餐、来源链接、观察日期，以及本轮关注的模型、价格、路由、数据用途和补救声明。字段由研究者填写，保留其原始来源；工具不会自动认定声明属实。

命令行可以使用 [study.template.json](../examples/study.template.json)，在真实指纹采集或离线复算时附加 `--study study.json`：

```bash
relay-audit fingerprint --config fingerprint.json --study study.json --out runs/case-01
relay-audit fingerprint-replay --reference-pack gpt-4.1-mini_20260915 --records runs/case-01/fingerprint_records.jsonl --study study.json --out runs/case-replay-01
```

研究档案保存到本机并加入报告，不进入发给模型的请求。它帮助组员从“出现了差异”继续讨论“商家具体承诺了什么、还需取得什么材料”，参见[研究背景](research-context.md)。

## 进阶：自己采集两端参考

双端点模式使用 Inspect AI 采集两端回答，再用 Model Equality Testing（MET）比较每道题的响应分布。它适合自定义研究条件和参考库未覆盖的版本，与 KBF 的指纹判据分别解释。

将 [pair.template.json](../examples/pair.template.json) 复制为 `pair.json`，填写两端 Base URL、同一模型版本和环境变量名称。配置模板使用 `RELAY_REFERENCE_KEY`、`RELAY_CANDIDATE_KEY`，可按前面的隐藏输入方式分别设置。

```bash
relay-audit plan --config pair.json
relay-audit run --config pair.json --out runs/pair-01
relay-audit compare --reference runs/pair-01/reference.jsonl --candidate runs/pair-01/candidate.jsonl --out runs/pair-replay-01
```

两端共享任务、温度与输出限制，逐轮交换调用顺序。客户端缓存和自动重试关闭，逐次保存结果。总请求数为“题数 × 每题重复数 × 2”；单次计划至多 1,000 次请求，请求输出上限合计至多 250,000 token。最低样本数是运行下限，真实差异的检出能力需通过校准确定。

五个内置任务检查算术、JSON、提取、排序和合成上下文查找。它们适合检查流程和有限能力；正式研究应按研究问题选取任务。报告同时给出请求成功率、任务通过率、耗时中位数及 P95、服务自报用量，以及逐题经校正的分布检验。未报告的计量保留为空。

双端点离线复算需要 `reference.jsonl` 和 `candidate.jsonl`，每份最大 2 MB、每端最多 3,000 条。文件采用旧版 `AuditRecord` 格式，与指纹记录分开导入。双端点另保存 `sampling_plan.json` 和 `inspect/` 原生日志，按研究原始资料管理。

## 进阶：机制实验台

六个情境全部使用人工数据，可在本机探索不同原因留下的观察：

| 情境 | CLI 名称 | 可以讨论什么 |
| --- | --- | --- |
| 同来源 | `honest` | 相同来源下怎样表达有限证据 |
| 模型替换 | `substitution` | 不同回答分布留下的信号 |
| 混合路由 | `mixed_route` | 少量请求换路由时是否容易漏检 |
| 提示改写 | `prompt_rewrite` | 请求变化也能造成与替换相似的结果 |
| 输出截断 | `output_cap` | 输出预算对答案与格式的影响 |
| 请求不稳定 | `unstable` | 错误和缺失样本怎样影响判断 |

```bash
relay-audit lab --scenario substitution --out runs/lab-substitution
relay-audit lab --scenario prompt_rewrite --out runs/lab-rewrite
```

用相同种子运行“模型替换”和“提示改写”，可以取得相同的回答、耗时和计量记录。课堂上让组员先判断，再揭示设定，讨论还需要哪些配置、日志或条款证据。人工情境的耗时与 token 是设定值。

旧版 Inspect/MET 合成流程仍可用 `relay-audit demo --out runs/legacy-demo` 运行。

## 常见问题

**找不到 `relay-audit` 命令。** Windows 使用 `.\.venv\Scripts\relay-audit.exe`；Linux/macOS 先执行 `source .venv/bin/activate`。也可用虚拟环境中的 Python 执行 `python -m relay_audit.cli ui`。

**网页打不开或提示会话更新。** 确认启动终端仍在运行，打开它打印的本机地址；会话更新后刷新页面。端口被占用时换用 `--port 8766`。

**模型不在参考列表里。** 当前不会从相近名称推测身份。可使用双端点比较，或在 GitHub 提出参考包需求并附上精确版本和可核查来源。

**模型能在聊天工具里使用，这里却失败。** 核对 Chat Completions 支持、Base URL、模型名、权限、额度和超时。页面显示的探针批次数不是成功请求数；查看报告中记录的实际失败原因。

**运行很慢，可以随意缩减题目吗？** 现有参考包和自测基线作为整体使用。运行前按请求计划评估成本；临时删题会改变比较条件，当前界面不提供这种操作。

**安装 Torch 找不到对应版本。** 推荐 Python 3.11/3.12。Linux/Windows 使用 README 的 CPU 索引，macOS 使用 PyPI 对应版本。实际测试平台见[验证记录](validation.md)。

**CSV 的题号前有单引号。** 导出会保护可能被表格软件识别为公式的文本，数值列仍按数值输出。

**复算时提示记录不匹配。** 使用运行时同一参考包和完整导出的 JSONL；不要拼接多次运行或修改其中的设置、批次和来源字段。保留原文件，再检查参考包 ID 与哈希。
