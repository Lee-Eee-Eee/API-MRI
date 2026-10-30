# Windows 便携版

从 [Releases](https://github.com/Lee-Eee-Eee/API-MRI/releases/latest) 下载 `API-MRI-0.7.0-windows-x64.zip`，完整解压到一个可以写入的文件夹，双击 `API-MRI.exe`。程序自带 Python 和 CPU 分析依赖，不需要管理员权限。

浏览器会打开工作台。先点击“试用示例”即可查看合成结果，不需要 Key。真实检测时填写 API 地址、模型名和 Key，核对计划后开始。请求从这台电脑发往填写的服务地址；密钥只用于本次运行，不写入浏览器持久存储或配置文件。

启动后的小窗口提供“打开工作台”和“退出”。关闭浏览器标签页后，程序仍运行；在小窗口退出才能停止服务。服务只监听 `127.0.0.1`。默认端口被占用时，会另选空闲端口。

## 文件放在哪里

| 文件或目录 | 用途 |
| --- | --- |
| `API-MRI.exe` | 双击启动工作台 |
| `api-mri-cli.exe` | 命令行入口；支持 README 中的全部子命令 |
| `_internal/` | 随包运行库，须与程序一起保留 |
| `data/runs/` | 便携工作台生成的检测报告与证据 |
| `data/reference-library/` | 手动更新的公开参考数据 |
| `licenses/` | 随包组件的许可原文和来源 |

可以移动整个文件夹，运行记录随 `data/` 一起移动。分享程序时使用原始下载包；已有的 `data/` 包含你的检测记录，分享报告前请检查正文和服务信息。

升级时先退出旧版，把新包解压到新文件夹，再将旧版的 `data/` 复制过去。不要只替换一个 exe，也不要在压缩包预览窗口内运行程序。

## 命令行使用

在解压目录打开 PowerShell：

```powershell
.\api-mri-cli.exe service-demo --out data/cli-service-example
.\api-mri-cli.exe identify-demo --out data/cli-identify-example
.\api-mri-cli.exe references
```

命令行的相对输出路径以当前工作目录为准。输出目录须为新目录；已有证据不会被覆盖。

启动器也接受参数：

```powershell
.\API-MRI.exe --no-browser
.\API-MRI.exe --port 8766 --data-dir "D:\API-MRI records"
```

启动器的小窗口显示当前地址。重复启动同一份程序会打开已有会话。

## 常见问题

**页面没有自动打开：** 点击启动窗口中的“打开工作台”，或复制窗口里的地址。

**文件夹不能写入：** 把完整文件夹放到用户可写的位置，例如下载或文档目录；也可用 `--data-dir` 指定数据目录。

**Windows 提示未识别的发布者：** 当前 exe 尚未签名。核对下载来自本仓库的 Release，并按发布页提供的 SHA-256 验证文件；也可按 README 从源码安装。

**Key 填错或接口不支持：** 查看逐请求错误。当前协议为 OpenAI 兼容 Chat Completions；服务商须支持所选功能和参数。检测不会自动重试失败请求。

便携包面向 Windows x64。Linux / macOS 使用[源码安装](../README.md#开始使用)。模型指纹的历史验证、拒判与参考数据日期见[方法说明](model-identification.md)。
