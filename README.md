# relay-audit · API 服务一致性检测工作台

**选一份公开参考包，填入你正在用的 API，看看它在相同探针上的回答是否与参考相符。** 你只需提供待测端的地址、模型名和 Key，参考数据随工具提供，无需再购买一个官方 API 账户。

v0.4 扩充了内置参考库，并加入“抓取参考库更新”按钮。可以搜索模型、按参考提供方筛选，再为精确版本选择参考。双端点比较和六情境实验台继续保留。单 API 表示只配置一个服务，一次检测仍会发送多次请求；页面在开始前显示请求计划。

![Relay Audit 本机工作台](docs/images/workbench.jpg)

这是清华大学《人工智能发展与治理》第 11 小组研究的附加开源工具。我们关注模型服务的商业承诺怎样被验证，以及用户取得证据需要付出什么成本。

## 开始使用

先安装 [Git](https://git-scm.com/downloads) 和 [Python](https://www.python.org/downloads/)，推荐 Python **3.11 或 3.12**。工具可在 CPU 上运行。安装依赖需要联网，无密钥示例与记录复算不调用模型 API。

### Windows · PowerShell

```powershell
git clone https://github.com/Lee-Eee-Eee/relay-audit.git
cd relay-audit
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -e ".[api,ui,dev]"
.\.venv\Scripts\relay-audit.exe ui
```

若已安装 Python 3.11，把 `py -3.12` 改为 `py -3.11`。以上命令直接使用虚拟环境里的程序，无需修改 PowerShell 执行策略。

### Linux / macOS · 终端

```bash
git clone https://github.com/Lee-Eee-Eee/relay-audit.git
cd relay-audit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Linux 安装 CPU 版 Torch：

```bash
python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
```

macOS 使用对应平台的 PyPI 版本：

```bash
python -m pip install torch==2.8.0
```

接着安装并启动：

```bash
python -m pip install -e '.[api,ui,dev]'
relay-audit ui
```

在浏览器打开 **http://127.0.0.1:8765**，保留启动终端，按 `Ctrl+C` 停止。工作台只接受本机访问；端口冲突时加 `--port 8766`。已进行的平台测试见[验证记录](docs/validation.md)，Linux/macOS 的安装路径不等于已完成对应平台验证。

## 第一次检测

1. **先看示例。** 选择“离线体验”，挑选一个参考包，熟悉计划、报告和逐题记录。示例用于展示流程，回答由本机生成。
2. **选匹配的参考包。** 转到“单 API 检测”，搜索模型并查看版本、参考来源和日期。点击“抓取参考库更新”可检查 KBF 作者公开目录中的新增数据；列表没有匹配版本时，使用进阶双端点比较。
3. **填一个 API。** 输入候选服务的 Base URL、实际请求模型名和 Key。例如 `https://api.example.com/v1` 是接口根地址，通常不要带 `/chat/completions`。
4. **查看计划。** 点击“核对采样计划”，检查将发送的请求数和输出上限。题目、温度和系统提示随参考包固定，便于和已有数据比较。
5. **开始并阅读结果。** 点击“开始检测”，查看进度、取消或打开历史记录。结果页按顺序展示结论、主要原因、参考来源和逐题记录，可下载报告。

输出 token 上限不包含全部输入与其他计费项目，实际费用仍按服务账单计算。先确认套餐允许自动 API 调用；仅限特定交互工具的订阅权益需核查使用范围。

运行结果保存在启动目录下的 `runs/ui/`。界面 Key 用于当前运行，不保存为配置或浏览器持久化设置；CLI 从你指定的环境变量读 Key。报告与原始回答保存到本机。详细操作见[使用指南](docs/user-guide.md)。

## 结果应该怎样读

| 结果 | 含义和下一步 |
| --- | --- |
| 发现显著差异（DIFF） | 在这份参考包及其设置下，待测回答的偏离达到判据。先核对版本、提示处理和输出限制，再考虑复采 |
| 未检出明显差异（SAME） | 本次探针没有检出超过参考基线的偏离；保留这次记录，按实际服务变化安排复核 |
| 证据不足（UNDETERMINED） | 覆盖不足、解析失败、条件不符或其他问题妨碍比较，按报告原因修正 |

**SAME 是方法返回的标签，不是模型真伪认证。** 模型版本、参数或中转处理都可能改变回答。报告保留参考来源及日期，让结果对应到具体条件。[参考包说明](docs/reference-packs.md)解释数据从哪里来、怎样比较，以及哪些变化需要更新参考。

内置参考从 4 份扩展到 **26 份**，包含 GPT-6 Astra、Claude Opus 5 / Fable 5.1、DeepSeek V4 Pro 0813、Kimi K3 等作者记录的版本。数据来自 KBF 的 2026 年 9 月目录，逐包核对探针格式、提示与领域配置、自测记录和文件哈希。模型名、提供方和采集渠道来自作者记录，尚未由本组独立认证。完整数量和版本见[来源清单](docs/reference-packs.md#内置参考包)。采集日期与模型发布日期是两回事；参考列表表示当前有数据可比对的版本。

## 更新参考库

在参考选择区点击 **“抓取参考库更新”**。工具从固定的 [KBF 作者仓库](https://github.com/Ooo0ption/KBF)读取公开 JSON，先冻结本次 Git 提交，再检查兼容性和哈希，合格的数据保存在本机 `runs/reference-library/`。它不需要 API Key，也不调用模型。

更新详情会区分新增、已有和跳过的数据。提示、领域或自测不兼容的包会说明原因；网络失败或 GitHub 限流也会明确显示。旧版本保留供历史记录复算，操作不会下载或执行远程代码。上游没有新增参考时，更新不会产生新模型数据；刚发布的型号仍可能没有可用参考。详细流程见[更新指南](docs/user-guide.md#抓取参考库更新)。

## 命令行

以下命令在虚拟环境激活后使用；Windows 也可将 `relay-audit` 换成 `.\.venv\Scripts\relay-audit.exe`。

```bash
relay-audit references
relay-audit references-update --library-dir runs/reference-library
```

需要单独管理缓存时，`references`、`references-update`、`fingerprint-*` 和 `ui` 均可加 `--library-dir path/to/library`；一起使用的命令指定同一目录。默认是启动目录下的 `runs/reference-library/`。

复制 [fingerprint.example.json](examples/fingerprint.example.json) 为 `fingerprint.json`，填写参考包 ID 和候选端配置，保留 `api_key_env` 为环境变量名称。随后执行：

```bash
relay-audit fingerprint-plan --config fingerprint.json
relay-audit fingerprint --config fingerprint.json --out runs/fingerprint-01
```

`fingerprint-plan` 只检查计划。真实运行前，按照[密钥输入指南](docs/user-guide.md#在命令行中提供密钥)设置候选端的环境变量。

无需 Key 的示例和离线复算：

```bash
relay-audit fingerprint-demo --reference-pack gpt-4.1-mini_20260915 --variant different --out runs/fingerprint-demo-01
relay-audit fingerprint-replay --reference-pack gpt-4.1-mini_20260915 --records runs/fingerprint-demo-01/fingerprint_records.jsonl --out runs/fingerprint-replay-01
```

示例支持 `match`、`different` 和 `incomplete`，分别展示匹配、偏离和记录不足。复算时使用原来那份参考包和候选记录，不会再次请求模型。每次使用新的输出目录，保留已有证据。

## 进阶：双端点比较与课堂实验

当你需要自己的任务或现有参考库没有覆盖目标版本时，可以提供两端参考，使用 Inspect 采集和 MET 逐题分布比较：

```bash
relay-audit plan --config pair.json
relay-audit run --config pair.json --out runs/pair-01
relay-audit compare --reference runs/pair-01/reference.jsonl --candidate runs/pair-01/candidate.jsonl --out runs/replay-01
```

先将[双端点模板](examples/pair.template.json)复制为 `pair.json`，修改地址和模型。这里需要两端各自的 Key；采样参数和任务两端共享，MET 结论与单 API 指纹标签分别解释。

“对照与实验”中的六个可控情境展示同来源、模型替换、混合路由、提示改写、输出截断和请求不稳定。特别可以对照“替换”与“提示改写”：程序让它们产生相同的回答观察，帮助讨论还需要哪些证据才能判断原因。

```bash
relay-audit lab --scenario prompt_rewrite --out runs/lab-01
```

服务研究档案把模型、价格、路由、数据用途和补救声明与测量结果并列，进一步连接课程的市场与治理问题。详见[研究背景](docs/research-context.md)。

## 复用与验证

单 API 路线复用 [KBF](https://github.com/Ooo0ption/KBF) 的公开参考及评分方法；高级双端点路线继续使用 [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) **0.3.278** 和 [Model Equality Testing](https://github.com/irenasaracay/model-equality-testing) **0.0.2**。本项目负责参考来源展示、采集与记录、本地工作台和报告解释。

新增代码采用 MIT 许可；KBF 及其他第三方材料保留原许可、来源和固定版本，详见 [THIRD_PARTY.md](THIRD_PARTY.md)。[验证记录](docs/validation.md)区分程序测试、合成示例和真实模型实验，不用流程通过率代替检测准确率。

开发时运行：

```bash
python -m pytest -q
```

反馈问题时提供复现步骤、参考包 ID、组件版本和合成样例即可。参考库覆盖、更新和实测校准仍是研究的一部分，更多说明见[参考包指南](docs/reference-packs.md)。
