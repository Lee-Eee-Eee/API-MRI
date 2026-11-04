# FlashProve 能怎样用于 API-MRI

FlashProve值得加入研究，但现阶段适合做**有供应商配合的证明验证扩展**。普通中转API只返回文本时，继续使用现有行为测量；供应商额外提供权重承诺和逐请求证明后，才具备运行FlashProve验证器的输入。

核查日期：2026-11-04。Anthropic的[研究文章](https://www.anthropic.com/research/provable-inference)发布于11月2日；本次代码固定到[`anthropics/FlashProve@9e2bdd8`](https://github.com/anthropics/FlashProve/tree/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c)，README标注版本0.16.0、研究原型、停止维护和Apache-2.0许可。读取了源码、验证接口和限制文档，没有编译或执行验证器。[源码状态](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/README.md)

## 它验证什么

供应商先把一组权重和计算配置固定为一个承诺ID，再为每次请求生成密码学证明。用户持有自己的请求、回答token编号、承诺ID及公开参数，用CPU验证回答与已承诺计算的关系。生成证明需要供应商掌握权重；验证方不需要权重。这里的“指纹”是密码学承诺，与API-MRI从短回答分布估计候选型号的统计指纹含义不同。[承诺、证明与验证](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/guide/what-is-proved.md)

一个有效证明只约束指定token位置和接受策略。`exact-match`要求这些token符合已承诺整数模型的贪心选择；`clipped-gap`允许分值落在事先接受的容差内，验证器输出`WITHIN LIMITS`。后者可以接纳其他模型产生的回答，不能在界面中显示成“模型身份已证实”。[两种策略](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/guide/known-limits.md)

承诺ID本身也没有“Claude”“GPT”等商业名称的含义。被测中转商可以给便宜模型生成自己的承诺。用户必须在调用前，从中转商控制之外的可信渠道取得承诺ID和配置，并核对型号、量化与策略；否则验证通过只表明它兑现了自己的那份承诺。[验证前的输入](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/guide/verify-a-proof.md)

## 当前实现与成本

| 项目原文事实 | 对本项目的影响 |
| --- | --- |
| 全尺寸演示为Llama 4 Maverick形状，公开4-bit转换、整数定点计算，窗口1,024 tokens；仓库另有合成权重例子。 | 尚无可直接用于Claude、GPT、DeepSeek等日常API的通用证明配置。[模型与平台](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/README.md) |
| 约110 tokens/s是24请求一批、按1,024个位置计算的稳态prefill证明吞吐。每批从开始到交出证明约221—226秒，未含队列、承诺初始化和中间件验证。 | 这个数字不代表聊天输出速度。原文另报生成证明的GPU时间约为对照vLLM的70—95倍，比较配置和算术格式也有差异。[计量条件](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/guide/performance.md) |
| 单份证明平均18.78 MB；客户端一次下载key与leaf约6.24 MB，配置文件另计。文章报告完整检查约17秒/24线程、21秒/单线程。 | 客户端验证可独立于推理GPU，但传输与CPU耗时仍应记账。这些是作者测量，API-MRI本轮未复测。[性能表](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/guide/performance.md)、[研究文章](https://www.anthropic.com/research/provable-inference) |
| 验证器的公开构建路径为Linux、g++ 13+；证明方的支持平台包括H100 80 GB及约330 GB主机内存。实际权重路线需下载约232 GB转换文件。 | 保持API-MRI主包轻量：不加入证明方、CUDA或模型权重；验证器若试用，作为用户另装的Linux/WSL外部程序。[运行路线](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/README.md) |
| Lean中证明的是相应协议和Lean验证器的性质；C++验证器、CUDA证明器及Python封装通过测试对照，未获得同一形式化正确性保证。整数模型与原浮点模型的接近程度另靠测量。 | 报告需要记录所用实现、提交和接受策略，不能把Lean定理当作整个程序和原厂模型的认证。[证明范围](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/guide/what-is-proved.md) |

## 最值得复用的部分

| 上游模块 | 已核读的功能 | API-MRI的可行接法 |
| --- | --- | --- |
| [`integrations/check_proof.py`](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/integrations/check_proof.py) | 用标准库核对承诺ID、token范围、末尾token、策略和验证器结果，再运行`cp_verify`。 | 优先包装这个完整流程。自行搜索stdout中的`YES`会漏掉无效调用、范围不符和较弱策略。 |
| [`verifier/src/cp_verify.cpp`](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/verifier/src/cp_verify.cpp) | 验证公开文件、leaf及请求证明，输出退出码、`result-v1`和最终结论。 | 外部进程调用，固定版本并限制时长、内存和输入体积；保存完整验证输出供复核。 |
| [`integrations/ask_and_check.py`](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/integrations/ask_and_check.py) | 请求`return_token_ids`，取得证明ID，轮询`/plugins/proof/`下的任务，再下载和验证。 | 仅对明确采用该协议的供应商启用。普通Chat Completions响应没有这些材料；DSH适配器也不会替上游补出证明。 |

上游封装使用`posix`和Linux文件权限机制，直接拷进Windows主进程不可用。Apache-2.0允许复用，实际分发时保留许可证、NOTICE及改动标识。更适合先固定外部工具版本，按需运行；源码链接和流程理解本身不增加主包依赖。[集成说明](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/integrations/README.md)、[许可](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/LICENSE)

## 建议怎样推进

第一步，在研究方法中加入“供应商附带计算证明”这条路线，与现有统计指纹分列。证明验证按次覆盖已经发生的回答，可以减少额外探针；代价转为供应商生成证明和客户端传输、验证，是否更省总成本要按两端分别计算。这是由协议结构得到的研究推断。

第二步，做独立的“导入证明”实验入口，输入可信承诺来源、公开配置、本人留存的请求与回答token、证明文件和外部验证器路径。状态至少区分“材料缺失”“本地验证未完成”“拒绝”“exact-match通过”“clipped-gap满足容差”。超时、程序错误和供应商不提供证明分别保留，不能直接统计为冒充型号。当前API-MRI尚未实现这一入口。

第三步，只有取得真实提供证明的端点后，再比较诚实响应、修改token、替换承诺、缩短覆盖范围、错误结束符、超时和无证明等情境。仓库合成样例通过，只能确认工具链会验证该样例，不能作为某家中转站的检测结果。[样例与结果读取规则](https://github.com/anthropics/FlashProve/blob/9e2bdd8ff5bf9c62f18263d7dac189b0400b260c/integrations/README.md)

对课程研究，新增的问题是：谁来把商业型号、权重版本、量化和承诺ID对应起来；更新后如何公告；证明覆盖哪些请求；平台转发过程中能否保留token和证明材料。计算证明可以补充一次交付的证据，数据留存、计费和投诉责任仍靠相应合同与记录说明。承诺注册与证据保全规则可作为本组的治理设计问题，不写成现行制度或已有部署。

逐条核查记录与固定文件摘要另存课程工作区的`research/sources/flashprove_sources_2026-11-04.json`。本轮没有下载模型权重、生成证明、运行`cp_verify`或测试真实服务。
