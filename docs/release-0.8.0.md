# API-MRI 0.8.0

新增 DSH Better Sidebar 插件和经典题示例馆。Windows 下载包继续提供完整工作台。

- **示例馆：** 6 道题、37 条公开作品记录，包括鹈鹕骑自行车、章鱼弹管风琴、钟表和弹球。24 个 MIT 许可 SVG 随包提供，另 13 条链接到原作。支持并排看图、查看源码、暂停动画；作品保留出处和作者公开的运行参数。
- **单次试作：** 选题后用自己的 API 生成一份作品。默认 1 请求、2,048 输出上限，不自动重试；代码、用量、耗时和原始记录一起保存。导出的 JSONL 可离线复算，HTML 在隔离沙箱中运行。
- **DSH：** `dsh-api-mri-0.8.0.tgz` 可装到 Better Sidebar 0.28.2，在侧栏连接已启动的 API-MRI；同时提供 format-1 插件索引。插件只保存服务地址，握手只交换版本和随机 nonce。

Windows：完整解压 `API-MRI-0.8.0-windows-x64.zip`，双击 `API-MRI.exe`。[下载与升级指南](https://github.com/Lee-Eee-Eee/API-MRI/blob/v0.8.0/docs/portable.md)

DSH：先安装 Better Sidebar，再安装本版本：

```sh
dsh plugin --profile web add dsh-better-sidebar@0.28.2
dsh plugin --profile web add github:Lee-Eee-Eee/API-MRI#v0.8.0
```

API-MRI 启动时用 `--embed-origin` 指定 DSH 的完整来源，再到侧栏连接。[插件使用指南](https://github.com/Lee-Eee-Eee/API-MRI/blob/v0.8.0/dsh-plugin/README.md) · [作品来源](https://github.com/Lee-Eee-Eee/API-MRI/blob/v0.8.0/docs/classic-examples.md)

公开作品用于观察代码与画面表现。作品上的模型名来自原作者记录；身份评估仍使用指纹方法。Windows 程序未签名，下载可与 `SHA256SUMS.txt` 核对。

验证：378项源码测试、11项插件测试、12组成品验收通过。[构建与验收记录](https://github.com/Lee-Eee-Eee/API-MRI/actions/runs/41179368921)。测试使用公开作品、合成响应及回环模拟服务，未调用付费模型。
