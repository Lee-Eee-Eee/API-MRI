# API-MRI 使用指南

API-MRI 用预先采集的参考回答检查一个 API。默认的“模型候选识别”给多个候选排序；“指定模型对照”检查与一份 KBF 参考的差异。安装步骤见 [README](../README.md)。

## 从模型候选识别开始

v0.6 默认页只需一个端点。可以先点击“生成合成演示”，再填写 Base URL、接口模型别名和 Key。点击“查看采样计划”不会请求模型；核对后开始，默认最多 32 请求、累计申请 512 输出 tokens。相同回答同时比较 132 个参考标签，后续题目按候选分歧选择。

结果包含候选表、家族表、停止原因和用量；可下载报告或识别 JSONL，在本页“已有记录复算”中重算。合成演示用于操作体验，独立保留测试和模型覆盖时间见[模型识别说明](model-identification.md)。本模式与下文 KBF 参考库分开，更新按钮目前只更新 KBF。

命令行入口为 `api-mri`，原来的 `relay-audit` 保留为兼容别名。发行包名称已改为 `api-mri`，Python 模块名称仍是 `relay_audit`。旧版用户按[升级步骤](../README.md#已有版本的升级)先卸载旧发行包再安装，已有运行记录和缓存可以继续使用。

## 先选择一种方式

| 方式 | 需要准备 | 是否调用模型 API | 用途 |
| --- | --- | --- | --- |
| 模型候选识别 | 待测地址、模型别名和 Key | 是，可能计费 | 与共同探针库的 132 标签排序，保留未知与证据不足 |
| 指定模型对照 | 一份 KBF 参考包、待测地址、模型名和 Key | 是，可能计费 | 检查待测服务与指定版本的行为差异 |
| 离线体验 | 选择参考包与示例情境 | 否 | 用人工回答熟悉三个结果状态 |
| 指纹记录复算 | 原来使用的参考包及一份 JSONL | 否 | 从保存的原始响应重新解析和判断 |
| 抓取参考库更新 | 能访问 GitHub 的网络 | 否；只读取公开参考文件 | 补充作者已经发布且兼容的参考数据 |
| 双端点比较 | 两端地址、Key 和匹配的模型版本 | 是，可能计费 | 自己采集参考，比较两端逐题回答分布 |
| 机制实验台 | 选择一个情境 | 否 | 观察替换、路由、提示与故障造成的现象 |

工作台默认地址为 `http://127.0.0.1:8765`。页面和分析进程运行在你的电脑上。保留启动它的终端，按 `Ctrl+C` 关闭；端口冲突可用 `api-mri ui --port 8766`。

## 指定模型对照（KBF）

### 1. 先看一个无密钥示例

打开“离线体验”，选择参考包，再选择匹配、偏离或记录不足的示例。程序在本机生成候选回答，通过同一条解析和报告流程运行。这样可以先看懂报告、逐题表和下载内容，之后再使用自己的账户。

三个示例对应 `match`、`different` 和 `incomplete`。可以分别选择低成本初筛和完整参考比较，查看同样的回答差异如何形成不同强度的结论。这些响应由程序构造，不代表真实模型准确率。

### 2. 选择参考包

以服务承诺提供的模型和版本为准。包详情包含参考模型、采集及自测日期、提供方、题数和来源链接。模型别名可以不同，但应先核对它们指向哪个版本。

用搜索框查找模型，也可按参考提供方筛选。内置包的版本和数量见[来源清单](reference-packs.md#内置参考包)。点击“抓取参考库更新”可以补充上游已有的数据；列表仍没有匹配版本时，使用双端点比较建立自己的对照。名称接近不等于版本匹配。

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

默认选择“低成本初筛”，跨领域固定抽取最多 3 个原始批次、最多 30 道题。默认预算为 4 次请求、4,096 token 累计输出配额；通常可容纳一次参数试探和三个题组。若接口需要多次参数试探，留给题组的预算会减少，报告会显示尚未完成的部分。

“完整参考比较”按所选参考包的全部题目运行，使用 KBF 的完整判据。两种模式都保留原批次的提示和输出设置；初筛每批只尝试一次，完整比较每批最多三次格式尝试。初筛不根据答案好坏追加问题，也不对短样本套用完整模式的显著性阈值。

点击“核对采样计划”，确认模式、选题数、地址、请求模型与预算，再点击“开始检测”。计划显示完成最低流程所需的请求与输出配额；低于该额度可能无法完成。修改任何设置后需要重新预检。

请求预算包含参数试探、失败请求与格式尝试。输出配额按每次请求的 `max_tokens` 累加；剩余额度不够时停止，不把下一批截短后继续。报告同时显示已发送次数、已申请配额和服务自报实际用量。未报告 token 的请求标为未知；只返回部分用量时注明覆盖条数。这些上限不包括全部输入与额外计费项目，实际费用以账单为准。

采集中可查看进度并取消。取消后，正在发送的请求可能仍需完成或超时，已发请求仍可能计费。完成的响应会保留；被取消、失败或截断的采集按“证据不足”报告。当前版本不自动续跑中断任务，修正后使用新目录重新运行。

### 5. 阅读结果和逐题记录

| 页面结果 | 如何理解 |
| --- | --- |
| 初筛完成，尚不能判断模型身份 | 查看选中题目中的差异和缺失；即使全部匹配也不认证模型，初筛不报告 p 值 |
| 发现显著差异 | 候选响应对参考的偏离超过本次判据；先查差异题目，再核对模型版本、配置和请求处理 |
| 未检出明显差异 | 本次探针没有发现超过参考自测波动的偏离；统计标签为 `SAME`，含义是未拒绝相容性 |
| 证据不足 | 已有记录不足以完成本次判断；根据报告检查覆盖、解析、截断、取消或连接问题 |

报告里的“匹配”指回答与参考包中的数值是否相符，不是知识题的客观正确率。参考包记录的是模型行为，也可能包含模型稳定给出的错误答案。结果支持的是这组探针上的一致性判断，模型权重来源仍需其他证据。

重点看三个量：**覆盖率**表示多少题取得可解析回答，**不匹配率**表示可比较回答中有多少偏离参考，**参考自测偏离**表示参考模型再次回答自身探针时原有的波动。逐题表可展开查看题目、参考值、候选值和原始响应，避免只看一个标签。

初筛覆盖率以本次选中的题目为分母，页面同时列出完整参考题数。完整模式发现任一计划题目缺失时保留“证据不足”，避免遗漏的问题改变结论。自测记录不是独立误报评估，当前版本尚无本组真实模型的鉴别准确率。

## 抓取参考库更新

参考选择区的 **“抓取参考库更新”** 会联网检查 KBF 作者仓库，无需模型账户。启动工作台本身不会自动抓取。按以下顺序操作：

1. 点击按钮，查看抓取与检查进度；需要时点击“取消更新”。
2. 打开更新详情，核对新增、已有和跳过的记录。新增包会加入选择列表；已存在的包继续使用。
3. 搜索目标模型，检查精确版本、采集和自测日期、渠道说明及来源链接，再核对采样计划。

每次更新先确定一个上游 Git 提交，检查其许可，再读取按月参考目录中的 JSON。包须通过当前探针格式、固定提示、领域定义和自测检查；不兼容的数据不会进入可用列表。检查结果与来源保存在本机，历史版本不覆盖，旧报告仍可按原包复算。若上游修改同名文件，新版本会使用带哈希后缀的 ID，与旧包分别列出。文件哈希用于确认数据版本，来源可信度仍取决于采集材料。

默认缓存目录为 **`runs/reference-library/`**，相对于启动程序时的工作目录。可用以下命令指定位置：

```bash
api-mri references-update --library-dir runs/reference-library
api-mri references --library-dir runs/reference-library
api-mri ui --library-dir runs/reference-library
```

使用自定义目录时，`fingerprint-plan`、`fingerprint`、`fingerprint-demo` 和 `fingerprint-replay` 也加相同的 `--library-dir`。例如：

```bash
api-mri fingerprint-plan --config fingerprint.json --library-dir path/to/library
api-mri fingerprint --config fingerprint.json --out runs/fingerprint-01 --library-dir path/to/library
```

这个按钮只获取作者已经发布的参考，不会自动向新模型采样，也不更新本机评分代码。网络失败、GitHub 限流、无新增包和不兼容会分别显示；可以继续使用原有参考，稍后再检查。模型刚发布时，公开目录可能尚未覆盖，仍需自行建立对照。

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
  "timeout": 30,
  "profile": "screening",
  "budget": {"max_requests": 4, "max_output_tokens": 4096}
}
```

PowerShell 中可交互输入，避免把值写进命令历史：

```powershell
.\.venv\Scripts\api-mri.exe fingerprint-plan --config fingerprint.json
$candidateCredential = Get-Credential -UserName 'candidate' -Message '在密码框中粘贴待测端 API Key'
$env:CANDIDATE_API_KEY = $candidateCredential.GetNetworkCredential().Password
.\.venv\Scripts\api-mri.exe fingerprint --config fingerprint.json --out runs/fingerprint-01
Remove-Item Env:CANDIDATE_API_KEY
```

Linux/macOS 在已激活虚拟环境的终端进入 Bash 后输入：

```bash
api-mri fingerprint-plan --config fingerprint.json
bash
read -r -s -p 'Candidate API key: ' CANDIDATE_API_KEY
echo
export CANDIDATE_API_KEY
api-mri fingerprint --config fingerprint.json --out runs/fingerprint-01
unset CANDIDATE_API_KEY
exit
```

`fingerprint-plan` 不读取 Key、不发送请求。示例和复算也不需要 Key：

```bash
api-mri references
api-mri fingerprint-demo --reference-pack gpt-4.1-mini_20260915 --variant different --out runs/example-01
api-mri fingerprint-replay --reference-pack gpt-4.1-mini_20260915 --records runs/example-01/fingerprint_records.jsonl --out runs/replay-01
```

每次 `--out` 使用一个新目录，已有目录会被拒绝覆盖。

完整模式将 `profile` 改为 `full`，并按预检结果设置预算。省略 `budget` 时使用完整计划的默认上限；保留初筛预算会限制完整模式可完成的题目。旧配置省略 `profile` 现在默认初筛。演示可加 `--profile full` 查看完整模式，历史 JSONL 的复算会沿用记录内的模式，不受当前默认值影响。

### 离线盘点参考是否足够开展研究

```bash
api-mri reference-audit --out runs/reference-audit-01
```

输出 `reference-audit.md` 与 `reference-audit.json`，逐包列出来源记录、留存自测数量、其他模型测试记录和调用计划。它不联网。若参考只保留一次自测，就还需要跨时段重复与其他模型对照，才能测量误报、漏检与稳定性。

## 将服务承诺放进报告

工作台提供可选的服务研究档案：填写产品、套餐、来源链接、观察日期，以及本轮关注的模型、价格、路由、数据用途和补救声明。字段由研究者填写，保留其原始来源；工具不会自动认定声明属实。

命令行可以使用 [study.template.json](../examples/study.template.json)，在真实指纹采集或离线复算时附加 `--study study.json`：

```bash
api-mri fingerprint --config fingerprint.json --study study.json --out runs/case-01
api-mri fingerprint-replay --reference-pack gpt-4.1-mini_20260915 --records runs/case-01/fingerprint_records.jsonl --study study.json --out runs/case-replay-01
```

研究档案保存到本机并加入报告，不进入发给模型的请求。它帮助组员从“出现了差异”继续讨论“商家具体承诺了什么、还需取得什么材料”，参见[研究背景](research-context.md)。

## 进阶：自己采集两端参考

双端点模式使用 Inspect AI 采集两端回答，再用 Model Equality Testing（MET）比较每道题的响应分布。它适合自定义研究条件和参考库未覆盖的版本，与 KBF 的指纹判据分别解释。

将 [pair.template.json](../examples/pair.template.json) 复制为 `pair.json`，填写两端 Base URL、同一模型版本和环境变量名称。配置模板使用 `RELAY_REFERENCE_KEY`、`RELAY_CANDIDATE_KEY`，可按前面的隐藏输入方式分别设置。

```bash
api-mri plan --config pair.json
api-mri run --config pair.json --out runs/pair-01
api-mri compare --reference runs/pair-01/reference.jsonl --candidate runs/pair-01/candidate.jsonl --out runs/pair-replay-01
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
api-mri lab --scenario substitution --out runs/lab-substitution
api-mri lab --scenario prompt_rewrite --out runs/lab-rewrite
```

用相同种子运行“模型替换”和“提示改写”，可以取得相同的回答、耗时和计量记录。课堂上让组员先判断，再揭示设定，讨论还需要哪些配置、日志或条款证据。人工情境的耗时与 token 是设定值。

旧版 Inspect/MET 合成流程仍可用 `api-mri demo --out runs/legacy-demo` 运行。

## 常见问题

**找不到 `api-mri` 命令。** Windows 使用 `.\.venv\Scripts\api-mri.exe`；Linux/macOS 先执行 `source .venv/bin/activate`。也可用虚拟环境中的 Python 执行 `python -m relay_audit.cli ui`。

**网页打不开或提示会话更新。** 确认启动终端仍在运行，打开它打印的本机地址；会话更新后刷新页面。端口被占用时换用 `--port 8766`。

**模型不在参考列表里。** 先点击“抓取参考库更新”，再查看跳过原因和版本。若上游尚未发布兼容参考，可使用双端点比较，或在 GitHub 提出需求并附上精确版本与可核查来源。

**为什么更新后没有更多模型？** 更新详情会说明数据已经存在、目录没有新包，还是文件不兼容。它补充已有参考，不能按模型名称自动生成一份真实基线。

**更新提示限流或连接失败。** 现有内置包和已保存的缓存仍可使用。检查 GitHub 网络连接后再试；这个操作不需要填写模型 API Key。

**模型能在聊天工具里使用，这里却失败。** 核对 Chat Completions 支持、Base URL、模型名、权限、额度和超时。页面显示的探针批次数不是成功请求数；查看报告中记录的实际失败原因。

**怎样节约 token？** 候选识别一次采样比较全库，每次输出上限 16，默认 32 请求、512 输出配额；停止条件和失败请求都会记录。KBF 可另选 4 请求初筛。两者探针和任务不同，不按请求数直接比较准确率；已有记录优先离线复算。输入和额外推理费用查看服务账单。

**为什么初筛回答都匹配，还显示不能判断身份？** 多个模型可能回答相同，初筛尚未在真实同模型、相近模型及未知模型上校准。报告保留匹配结果，判断身份所需的证据仍待补充。

**安装 Torch 找不到对应版本。** 推荐 Python 3.11/3.12。Linux/Windows 使用 README 的 CPU 索引，macOS 使用 PyPI 对应版本。实际测试平台见[验证记录](validation.md)。

**CSV 的题号前有单引号。** 导出会保护可能被表格软件识别为公式的文本，数值列仍按数值输出。

**复算时提示记录不匹配。** 使用运行时同一参考包和完整导出的 JSONL；不要拼接多次运行或修改其中的设置、批次和来源字段。保留原文件，再检查参考包 ID 与哈希。

公开参考下载支持系统环境中的 `HTTPS_PROXY` / `HTTP_PROXY` 配置。遇到 GitHub 连接超时，可先检查本机代理及网络；更新失败会保留已经可用的参考。
