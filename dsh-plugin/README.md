# API-MRI · DSH 原生侧栏

从DSH左侧栏打开API-MRI，在中央主区域使用完整工作台：连接与速度、工具协议、模型指纹、示例馆和公开能力参考。

## 安装与打开

0.9.3使用DSH原生入口，无需Better Sidebar。Windows x64首次打开时自动安装运行程序。

下载[插件包](https://github.com/Lee-Eee-Eee/API-MRI/releases/download/v0.9.3/dsh-api-mri-0.9.3.tgz)，在PowerShell安装到桌面版：

```powershell
& "$env:LOCALAPPDATA\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd" plugin --profile desktop add "C:\下载目录\dsh-api-mri-0.9.3.tgz"
```

在DSH左侧栏点击 **API-MRI**。第一次打开自动下载Windows运行包、核对SHA-256、解压并启动；页面显示下载进度。以后复用已安装版本，自动启动并连接。运行包包含Python与CPU分析依赖，大小以进度条为准。

工作台默认选择「DSH 已配置模型」，选型号后核对计划，再点击开始检测。地址和Key由DSH处理。「刷新」重新读取模型目录，不调用模型。连接与速度、工具协议、模型指纹及示例试作共用此入口；「手动接口」可另测其他服务。

检测走DSH模型适配器，报告保留这条调用路径。不支持的指定工具或推理参数会单列显示；指纹需要该型号明确提供关闭思考的档位。

0.9.3修复了短指纹请求被422拒绝、工具结果无法回填和流末尾字段遗漏。DSH服务测量默认每请求256 tokens，型号支持时关闭推理；强制调用不受支持时继续检查自动调用和结果回填。[排障与实测](../docs/dsh-diagnostics-2026-11-09.md)

0.9.0旧插件包使用Better Sidebar右栏；升级到0.9.3后从原生左栏进入。

无需另下便携版。已有API-MRI服务时，也可以填写地址手动连接。macOS/Linux暂用[源码启动](../README.md#开始使用)，然后手动连接。

DSH Web使用`web` profile：

```sh
dsh plugin --profile web add ./dsh-api-mri-0.9.3.tgz
```

Web地址须为回环地址，可附端口。桌面来源固定为`dsh-app://app`。HTTPS Web页面连接HTTP后端会受到浏览器混合内容限制；这种部署连接自行配置的HTTPS后端。

## 下载、进程和记录

运行包固定到插件版本，从本项目GitHub Release下载。tarball内置运行包摘要；Git源码安装读取同版本Release的运行清单。大小和SHA-256均匹配后才解压和启动。

Windows保存位置：`%LOCALAPPDATA%\API-MRI\dsh-runtime`。版本号子目录保存程序，`data`保存检测记录与参考缓存。

插件分配空闲端口并授权当前DSH来源嵌入。DSH卸载插件或退出时停止自己启动的进程；手动连接的独立服务由用户管理。升级保留`data`。

进入插件就会开始检查和启动，无需先运行命令或填写端口。已有健康实例时直接复用；旧进程已退出时重新启动。本地已校验安装优先于下载，重新构建源码也能在离线时打开已安装版本。

下载失败可点「重试自动启动」，或手动连接。尚未发布运行包的开发版本会显示错误，源码后端仍可独立启动。

## 密钥与预览

DSH供应商Key留在DSH的模型服务中。检测器只收到限定型号、请求数和输出配额的临时授权，关闭插件后失效；授权不写入检测记录。手动接口的Key在工作台内填写。启动后端时保留Windows运行、代理与CA设置，不继承API Key环境变量。读取目录、看作品和核对计划不调用模型。自动下载使用Node 22.21及以上的代理支持；当前DSH桌面版自带兼容Node。

宿主路由复用DSH的`connection.requestRejection()`。父页面与工作台按来源、窗口和随机nonce确认连接，再交换公开目录及本轮临时授权。本地调用桥只接受授权内的型号和预算。作品置于没有同源、网络和弹窗权限的沙箱；three.js r160与OrbitControls随包提供。

## 构建

```sh
npm run build:dsh
npm run test:dsh
npm pack --ignore-scripts
```

构建仅需Node.js，没有安装钩子。React与原生侧栏扩展接口由DSH提供。Release CI在便携包验收后运行`scripts/prepare_dsh_runtime.py`，写入该ZIP的摘要和大小，再打插件tarball。源码占位清单通过对应版本Release解析。

开发时可在源码目录运行`npm run build:dsh`，再用`dsh plugin --profile desktop add <源码目录>`链接。安装已发布插件包时直接使用上面的`.tgz`命令。

自己的发现页索引由`dsh-plugin/market-index.mjs`生成，固定到发布提交，随Release提供；未提交第三方插件市场。

## 接入依据

- [DSH打包安装](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/publish.zh.md)
- [DSH WebServer与Connection](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/web-server.zh.md)
- [DSH原生左栏入口](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/client/ui-sidebar/README.md)
- [DSH主区域与面板选择](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/client/ui-layout/README.md)
- [DSH模型调用服务](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/README.md)
- [0.8.0的Web安装验收](tests/host-qa.json)记录旧版Better Sidebar接入；0.9.0自动安装见Release验收记录。
