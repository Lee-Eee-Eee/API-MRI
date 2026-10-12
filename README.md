# relay-audit API服务一致性研究工具

这是清华大学《人工智能发展与治理》第11小组研究的附加原型，版本0.1.0。它保存可追溯采集记录，比较匹配任务、配置与参考下的响应行为，并生成中文证据报告。

当前验证包括合成fixture与本地HTTP回环。真实模型误报率、检出能力和商业端点行为尚未校准；结果不认证模型身份，也不判定欺诈、数据泄露、蒸馏或法律责任。

## 实际复用

- [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) 0.3.278：Task、模型适配、执行及原生日志。
- [Model Equality Testing](https://github.com/irenasaracay/model-equality-testing) 0.0.2：逐题两样本检验，使用mmd_hamming字符核。
- 本项目新增：记录适配、匹配与覆盖检查、Monte Carlo加一修正、Holm校正及有限解释。

MET包初始化还需要其元数据未声明的Transformers，本项目已显式补齐。没有复制第三方响应数据、下载模型权重或分词器。[许可及复用说明](THIRD_PARTY.md)

## 安装

Python要求≥3.10；当前验证环境为Windows/Python3.12.14。建议先安装CPU版Torch，保持环境隔离：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -X utf8 -m pip install -e ".[api,dev]"
```

Linux/macOS将Python路径换成`.venv/bin/python`；尚未宣称在这些平台完成测试。[requirements-lock.txt](requirements-lock.txt)为本轮Windows环境快照，非跨平台锁定保证。

## 本地演示与离线复算

```powershell
.\.venv\Scripts\python.exe -X utf8 -m relay_audit.cli demo --out runs/demo-local
.\.venv\Scripts\python.exe -X utf8 -m relay_audit.cli compare --reference runs/demo-local/reference.jsonl --candidate runs/demo-local/substituted.jsonl --out runs/replay-local
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
```

demo使用Inspect mock后端，没有真实模型或付费调用。compare只读本地JSONL。目录须是新的，避免覆盖已有证据。报告包含输入哈希、版本、参数、逐题检验与覆盖；哈希只核对本组文件，不认证供应商后台。

## 显式端点采集

填写[配置模板](examples/endpoint.template.json)，API Key仅通过api_key_env指定的环境变量提供。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m relay_audit.cli collect --config path/to/config.json --out runs/new-endpoint-run
```

这个命令会实际请求端点，可能计费。本轮未调用外部模型。先核查套餐允许用途：仅限交互编程的Coding Plan不能默认用于自动脚本或批量测评。使用无个人信息的合成任务，勿提交密钥或私人请求。

首版只实现非流式、单轮OpenAI兼容Chat Completions，关闭客户端缓存和SDK/Inspect重试，单连接采样。服务端自报模型、ID与usage单独保存，不作为认证。配置的official标签由研究者提供，账户与来源须另核验。

成功、失败及中断时脱敏本次日志中的已知凭据与认证字段；残缺且无法确认脱敏的日志拒绝导出。提示词和回答仍属于敏感性取决于内容的研究资料，原生日志默认留在本地。

## 判断边界

每题单独调用MET，避免不同题目混池；保留原始p值，添加有限次Monte Carlo修正和Holm校正。字符核不是语义评分，工程最低样本数不保证统计功效。

模型/配置/提示不匹配、计划缺失、重复或重试、失败和未知参考会导致拒判。当前未完成真实功效校准，统计非拒绝时总体输出“证据不足”，不会给出“模型保真”结论。

客户端harness的提示、工具、上下文与预算也能改变输出，不能直接用完整Agent体验反推模型替换。

## 验证与贡献

[验证摘要](docs/validation.md)说明21项测试与108条合成响应演示的范围。课程报告同时研究市场、服务承诺与治理，工具只提供一部分行为证据。[研究定位](docs/research-context.md)

新增代码采用MIT许可。代码由AI辅助起草、经运行测试与AI交叉审查，最终学术解释仍需成员复核。欢迎可复现的错误报告；提供合成样例和组件版本，勿附API密钥、私人日志或未经同意的数据。

## 方法引用

Irena Gao, Percy Liang, Carlos Guestrin. *Model Equality Testing: Which Model is this API Serving?* ICLR 2025. [正式论文](https://proceedings.iclr.cc/paper_files/paper/2025/hash/d73234a13815fc1f9779dd17d89be9b4-Abstract-Conference.html)
