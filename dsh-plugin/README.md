# API-MRI · DSH Better Sidebar

在侧栏使用完整工作台：连接与速度、工具协议、模型指纹、示例馆和公开能力参考。

## 安装与打开

需要DSH 0.2.1-rc.1及以上的0.2.x、Better Sidebar 0.28.2。Windows x64从0.9.0开始自动安装运行程序。

下载[Release](https://github.com/Lee-Eee-Eee/API-MRI/releases/latest)中的`dsh-api-mri-0.9.0.tgz`，在PowerShell安装到桌面版：

```powershell
& "$env:LOCALAPPDATA\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd" plugin --profile desktop add "C:\下载目录\dsh-api-mri-0.9.0.tgz"
```

重开DSH，在Better Sidebar的「新建标签页」中选 **API-MRI**。第一次打开自动下载Windows运行包、核对SHA-256、解压并启动；页面显示下载进度。以后复用已安装版本，自动启动并连接。运行包包含Python与CPU分析依赖，大小以进度条为准。

无需另下便携版。已有API-MRI服务时，也可以填写地址手动连接。macOS/Linux暂用[源码启动](../README.md#开始使用)，然后手动连接。

DSH Web使用`web` profile：

```sh
dsh plugin --profile web add dsh-better-sidebar@0.28.2
dsh plugin --profile web add ./dsh-api-mri-0.9.0.tgz
```

Web地址须为回环地址，可附端口。桌面来源固定为`dsh-app://app`。HTTPS Web页面连接HTTP后端会受到浏览器混合内容限制；这种部署连接自行配置的HTTPS后端。

## 下载、进程和记录

运行包固定到插件版本，从本项目GitHub Release下载。tarball内置运行包摘要；Git源码安装读取同版本Release的运行清单。大小和SHA-256均匹配后才解压和启动。

Windows保存位置：`%LOCALAPPDATA%\API-MRI\dsh-runtime`。版本号子目录保存程序，`data`保存检测记录与参考缓存。

插件分配空闲端口并授权当前DSH来源嵌入。DSH卸载插件或退出时停止自己启动的进程；手动连接的独立服务由用户管理。升级保留`data`。

下载失败可点「重试自动启动」，或手动连接。尚未发布运行包的开发版本会显示错误，源码后端仍可独立启动。

## 密钥与预览

Key在工作台内填写。安装器不读取DSH账号、会话和模型配置。启动后端时仅传Windows运行所需环境变量，不继承API Key环境变量。安装与看作品不调用模型。

宿主路由复用DSH的`connection.requestRejection()`。父页面与工作台握手仅传随机nonce和版本。作品置于没有同源、网络和弹窗权限的沙箱；three.js r160与OrbitControls随包提供。

## 构建

```sh
npm run build:dsh
npm run test:dsh
npm pack --ignore-scripts
```

构建仅需Node.js，没有安装钩子。React与Better Sidebar由DSH提供。Release CI在便携包验收后运行`scripts/prepare_dsh_runtime.py`，写入该ZIP的摘要和大小，再打插件tarball。源码占位清单通过对应版本Release解析。

自己的发现页索引由`dsh-plugin/market-index.mjs`生成，固定到发布提交，随Release提供；未提交第三方插件市场。

## 接入依据

- [DSH打包安装](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/publish.zh.md)
- [DSH WebServer与Connection](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/web-server.zh.md)
- [Better Sidebar插件接口](https://github.com/omdsh-dev/DSH-better-sidebar/blob/2d736f5496fe97f4bba6af37d99861649b9ef3c0/docs/external-plugin-guide.md)
- [0.8.0的Web安装验收](tests/host-qa.json)保留历史验证范围；0.9.0自动安装由新版测试和Release验收记录。
