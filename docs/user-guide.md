# 使用指南

relay-audit 在你的电脑上运行，用相同任务比较两端 API，或重新分析已有的采集记录。第一次使用建议完成 README 中的安装，打开本机工作台，先跑离线演示。

## 选择工作方式

| 方式 | 需要准备 | 是否调用模型 API | 适合做什么 |
| --- | --- | --- | --- |
| 离线演示 | 无需 Key | 否 | 熟悉操作，查看同来源及已知变化的模拟结果 |
| 可控实验台 | 选择情境，无需 Key | 否 | 观察不同服务机制怎样影响回答、错误与指标 |
| 双端点检测 | 两端地址、Key、匹配模型版本 | 是，可能计费 | 在相同任务和参数下采集并比较服务表现 |
| 离线复算 | 参考端和待测端 JSONL | 否 | 复核已有证据，调整统计设置后重新生成报告 |

工作台默认地址为 `http://127.0.0.1:8765`。它是本机页面，界面文件随项目提供。使用期间保留启动它的终端；按 `Ctrl+C` 关闭服务。

## 先跑离线演示

选择“离线演示”并启动。工具会生成三组模拟回答：参考组、同来源组和刻意改变回答的组，再给出两份对照报告。它通过真正的 Inspect 执行和 MET 分析流程运行，但回答来自本机模拟程序。

默认每题重复 12 次。演示共有 3 道题、3 组来源，因此生成 108 条模拟响应。演示中的“已知变化”用于观察工具能否发现这个特定差异；它不提供真实模型的准确率。

结果页先展示总体判断，再展示请求表现和逐题结果。展开回答记录，可以看到统计比较对应的内容。下载 Markdown 阅读报告，或用 Excel 打开逐题 CSV。

## 用可控实验台理解原理

实验台提供六个机制已知的情境，全部在本机用人工数据模拟。它与离线演示的用途有所不同：演示帮助熟悉流程，实验台用来比较多个可能的解释。

| 情境 | 代码名称 | 适合讨论的问题 |
| --- | --- | --- |
| 同来源 | honest | 两组模拟数据来源相同时，报告怎样表达有限证据？ |
| 模型替换 | substitution | 明显不同的回答分布会留下什么信号？ |
| 混合路由 | mixed_route | 部分请求走不同路径时，少量抽样可能看到什么？ |
| 提示改写 | prompt_rewrite | 上游模型未换、提示被改动，也能产生类似差异吗？ |
| 输出截断 | output_cap | 过小的输出预算怎样影响答案和格式通过率？ |
| 请求不稳定 | unstable | 请求错误怎样影响成功率和比较完整性？ |

课堂演示可以先跑同来源，再分别跑“模型替换”和“提示改写”。这两个情境刻意支持产生相同的回答观察结果：知道背后机制的是实验设置，黑盒响应只展示它们共同造成的表现。让同学提出下一步要核对的提示、配置、日志或合同材料，再对照报告中的证据解释。

```bash
relay-audit lab --scenario honest --out runs/lab-honest
relay-audit lab --scenario substitution --out runs/lab-substitution
relay-audit lab --scenario prompt_rewrite --out runs/lab-rewrite
```

实验中的耗时和 token 也是设定值，不代表真实服务成本或速度。

## 将服务承诺带入研究报告

在工作台填写服务研究档案，把具体产品及套餐、来源链接、资料日期、研究问题与相关声明保存下来。可以只填本轮关心的字段，例如同时比较模型声明和数据政策。工具把这些内容作为研究者录入的声明，不会自动抓取来源或验证其真实性。

报告围绕模型能力、价格计量、上游路由、数据用途和持续服务五类承诺，分别列出声明、本次可观察内容以及下一步需要的证据。模型行为差异提示复核配置；token 自报量需结合账单核对；数据留存和训练用途则需要运营方材料。这样可以把技术测量接回市场与治理问题。

命令行使用普通 UTF-8 JSON 文件，例如保存为 `study.json`：

```json
{
  "case_id": "case-01",
  "service_name": "待研究服务",
  "product": "具体套餐或渠道",
  "observed_on": "2026-10-13",
  "source_url": "https://example.com/terms",
  "model_claim": "填写来源中可定位的模型及版本声明",
  "price_claim": "填写报价单位、币种和适用条件",
  "route_claim": "填写上游及备用路由声明",
  "data_policy_claim": "填写该产品的数据留存和使用声明",
  "remedy_claim": "填写故障、投诉或退款约定",
  "research_question": "服务承诺中哪些可通过外部观察复核？",
  "notes": "记录未知项和需要进一步取得的材料"
}
```

示例中的网址和内容是占位符，需替换为真实来源及记录日期。

```bash
relay-audit run --config pair.json --study study.json --out runs/study-01
relay-audit compare --reference runs/study-01/reference.jsonl --candidate runs/study-01/candidate.jsonl --study study.json --out runs/study-replay
```

研究档案另存为 `study.json` 并加入 JSON/Markdown 报告，不进入发给端点的请求。逐题 CSV 保持为测量汇总。

## 双端点检测的字段

| 字段 | 如何填写 |
| --- | --- |
| 服务标识 / endpoint_id | 便于自己识别的名字，例如 `official-reference`；不参与模型身份认证 |
| Base URL | OpenAI 兼容接口根地址，例如 `https://api.example.com/v1`；不要填聊天网页地址或 Key |
| 模型名称 / model_declared | 两端宣称提供的同一模型及版本；核对不同渠道别名对应的具体版本 |
| API Key | 分别填写两端账户的调用凭据；只用于当前运行内存 |
| 来源类型 / reference_kind | 据已核实的渠道填写。`official` 是研究者的来源标注，工具不会自动验证账户归属 |
| 任务 | 从五个合成任务中挑选；两端使用相同提示与检查规则 |
| 每题重复次数 / repeats | 每个端点对每道题独立调用多少次，默认 12 |
| 最大输出 token / max_tokens | 每次请求的输出上限，默认 128；过小可能让回答被截断 |
| temperature | 两端共享的采样参数，默认 0；即使为 0，服务也可能产生不同输出 |
| 请求超时 / timeout、attempt_timeout | 限制等待时间，单次尝试超时不能大于总超时 |
| 置换次数 / permutations | 用于近似计算统计结果的次数；工作台及双端点默认 199，更多次数通常需要更多分析时间 |
| 最低样本数 / min_samples | 每题每端成功样本的最低运行要求，默认 8；它不是足够检出真实差异的样本量保证 |
| 随机种子 / seed | 使相同数据、配置和环境下的离线计算可复现，默认 11 |
| 显著性阈值 / alpha | 默认 0.05，配合多题校正使用；不表示模型有 95% 概率为真 |

远程地址使用 HTTPS；HTTP 只接受本机回环地址，便于测试本地服务。URL 不接受嵌入用户名、密码、查询串或片段。

内置任务分别检查简单算术、指定 JSON 对象、信息提取、排序格式和合成上下文查找。它们让结果容易核对；若研究实际业务能力，需另设计有代表性的任务及评分方案。

### 启动前检查计划

先核对页面显示的总请求数：`题目数 × 每题重复次数 × 2`。例如选择 5 道题，每题 12 次，两端合计 120 次请求。输出 token 上限为请求数乘以 `max_tokens`；它不包括输入和服务可能另外计费的推理、缓存或工具项目。

双端点模式限制单次计划至多 1,000 次请求、请求的最大输出 token 合计至多 250,000。实际账单仍按服务规则计算。你的账户和套餐需要允许这种自动调用；仅限特定交互工具的订阅权益应先核查用途。

工具逐轮交换参考端与待测端的先后顺序，减少长期先调用一端造成的时间偏差。两端共享任务与生成参数，关闭客户端缓存和 SDK/Inspect 重试，并在每次完成后把记录写到本地。

### 取消与失败

点击“取消”后，正在进行的请求会完成或超时，随后停止新的请求。已完成的记录保留，并尽可能生成部分报告；采集未完成时总体结果保留为“证据不足”。关闭浏览器页面不会结束正在运行的本机服务，停止服务则会中断运行。

若程序退出，重新打开后可以查看历史记录和已保存文件；当前版本不自动续跑被中断的计划。修正条件后创建新的运行，便于保留原记录。

## 离线导入已有记录

选择“离线复算”，分别上传参考端和待测端的 `.jsonl`。每行应是本项目生成的一条 `AuditRecord` 记录，包含题号、重复编号、请求配置哈希、状态、回答、计量和来源信息。它不是普通聊天导出文件，也不是 `report.json`。

每个文件最多 2 MB、每端最多 3,000 条记录。网页把记录交给你电脑上的工作台进程分析，并保存该次复算的本地副本；不会发送到模型服务。

你可以直接从演示导入 `reference.jsonl` 和 `substituted.jsonl`。使用与原报告相同的统计设置和组件版本，得到相同的分析结果；输入文件内容变化会反映在报告的 SHA-256 哈希中。

## 阅读报告

先看结果和主要原因。如果显示“模型名称或版本不一致”，先核对来源；如果有失败请求，先排查连接、权限或额度；如果样本不足，按原计划安排新的完整采样。

| 指标 | 含义 |
| --- | --- |
| 请求成功率 | 接口成功返回的记录数除以全部已记录请求数；不代表回答正确 |
| 限定任务通过率 | 满足预设答案或格式的请求数，除以配置了有效检查的请求数；请求失败计为未通过 |
| 耗时中位数 | 有耗时记录的典型请求耗时；包含网络、排队和处理时间 |
| P95 耗时 | 把有耗时记录排序后取最近秩的第 95 百分位；样本少时接近最大值 |
| 来源自报 token | 服务返回的输入、输出、总量字段各自累加；缺失保留为缺失，不推算或补零 |
| 校正后 p 值 | 逐题分布检验经 Monte Carlo 与 Holm 校正后的结果，需结合预设阈值解释 |

报告会同时列出有耗时的请求数，以及每个 token 字段有报告的请求数。例如“输入合计 200（8/12）”表示只有 12 条中的 8 条报告了输入计量，不能把 200 当成全部请求的完整用量。

三个任务检查规则为：`exact` 比较去掉首尾空白后的完整文本；`contains` 检查是否含指定文本，区分大小写；`json_object` 要求回答为完整 JSON 对象且字段、值和类型与预设相符，键顺序可不同，额外说明或 Markdown 代码围栏不通过。

分布差异与任务通过率是两种观察。两端都答对时，措辞分布仍可能不同；两端都没答对，也可能暂时没有检出分布差异。当前方法比较字符分布，真实模型的误报与漏检情况还需要专门校准。

## 输出在哪里

工作台默认在启动目录下建立 `runs/ui/`。每次运行有独立目录，保留历史索引和 `evidence/` 证据。可用 `relay-audit ui --runs-dir path/to/runs` 指定其他本地目录。

| 文件 | 用途 |
| --- | --- |
| report.md | 给人阅读的结论、原因和逐题表 |
| report.json | 完整分析结果、参数、描述性指标、组件版本和输入哈希 |
| prompts.csv | 每题每端一行汇总，UTF-8 BOM 编码，可用 Excel 打开；不含原回答或认证字段 |
| reference.jsonl / candidate.jsonl | 原始派生记录，用于离线复算；含任务对应的回答 |
| sampling_plan.json | 双端点采样计划，含任务和参数，不含 Key 值 |
| study.json | 可选的研究档案；保存声明、来源和问题，不发送给模型服务 |
| inspect/ | Inspect 原生日志，包含测试任务、回答及请求记录 |

演示的两份报告分别保存在 `honest/` 和 `substituted/`。下载报告时默认提供 JSON、Markdown 和 CSV；原始 JSONL 与日志留在本地目录。提交复现问题时优先使用合成样例，避免分享私人任务或回答。

已知密钥与认证字段在每次请求结束后脱敏。若异常上游把密钥回显到响应中，脱敏前的本机原生日志可能短暂含有该值；因此原生日志仍按研究原始资料管理。

## 通过命令行提供密钥

从仓库根目录复制 `examples/pair.template.json` 为 `pair.json`，先修改两端地址和共同模型名称。保留 `api_key_env` 指向的环境变量名称；不要把 Key 写进 JSON。

PowerShell 可交互输入密码，避免值出现在命令历史中：

```powershell
$referenceCredential = Get-Credential -UserName 'reference' -Message '在密码框中粘贴参考端 API Key'
$env:RELAY_REFERENCE_KEY = $referenceCredential.GetNetworkCredential().Password
$candidateCredential = Get-Credential -UserName 'candidate' -Message '在密码框中粘贴待测端 API Key'
$env:RELAY_CANDIDATE_KEY = $candidateCredential.GetNetworkCredential().Password
.\.venv\Scripts\relay-audit.exe plan --config pair.json
.\.venv\Scripts\relay-audit.exe run --config pair.json --out runs/pair-01
Remove-Item Env:RELAY_REFERENCE_KEY, Env:RELAY_CANDIDATE_KEY
```

Linux/macOS 可以在已激活虚拟环境的终端中进入 Bash，再隐藏输入：

```bash
bash
read -r -s -p 'Reference API key: ' RELAY_REFERENCE_KEY
echo
export RELAY_REFERENCE_KEY
read -r -s -p 'Candidate API key: ' RELAY_CANDIDATE_KEY
echo
export RELAY_CANDIDATE_KEY
relay-audit plan --config pair.json
relay-audit run --config pair.json --out runs/pair-01
unset RELAY_REFERENCE_KEY RELAY_CANDIDATE_KEY
exit
```

`plan` 可以在设置密钥前单独运行；它只校验计划。CLI 不接受配置里的 `api_key` 明文项，界面则使用仅存于运行内存的 Key。

## 常见问题

**找不到 `relay-audit` 命令。** Windows 使用 `.\.venv\Scripts\relay-audit.exe`；Linux/macOS 执行 `source .venv/bin/activate`。也可以用虚拟环境中的 Python 执行 `python -m relay_audit.cli ui`。

**网页打不开。** 确认启动终端仍在运行，复制它打印的 `127.0.0.1` 地址。端口冲突时指定 `--port 8766`。若提示会话更新，刷新页面后重新操作。

**安装 Torch 时找不到对应版本。** 先核对 Python 版本与系统架构，建议新建 Python 3.11/3.12 环境。Linux/Windows 使用 CPU 索引，macOS 使用 PyPI 默认索引。完整平台验证范围见 [validation.md](validation.md)。

**接口能在聊天工具里使用，这里却失败。** 本版使用非流式、单轮 OpenAI 兼容 Chat Completions。核对 Base URL、模型名、Key 权限、额度和超时；只支持其他协议或必须依赖特定客户端的服务可能无法直接使用。

**为什么显示“证据不足”？** 逐条查看中文原因：配置或版本不匹配、未知参考、错误或缺失样本会阻止整体比较；完成检验但未发现差异时，也需要真实模型校准才能评价检出能力。

**CSV 里题号前面多了单引号。** 对以 `=`、`+`、`-`、`@` 等开头的文本，导出时添加了保护字符，避免表格软件把题号当公式。数值列仍是正常数值。

**能认证某个 API 用的是什么模型吗？** 当前工具提供有限的行为一致性证据。验证权重来源、内部路由和数据留存，需要结合合同、运行配置或上游可核验信息。原型不会生成供应商排行榜。
