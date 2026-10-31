# API-MRI · DSH Better Sidebar

在 DSH 侧栏打开完整的 API-MRI 工作台。连接、吞吐、工具调用、模型指纹与示例画廊沿用 API-MRI 的界面和报告。

插件连接已经启动的 API-MRI。第一次打开页签时输入地址并点击「连接」；默认地址是 `http://127.0.0.1:8765`。

## 安装

需要 DSH **0.2.1-rc.1 及以上的 0.2.x**、Better Sidebar **0.28.2**。这版面向回环地址上的 DSH Web。

已在 Windows、Node.js 24.11.1、DSH 0.2.1-rc.3、Better Sidebar 0.28.2 上完成真实安装、配置装配、浏览器页签与跨来源连接验证。测试使用独立的 DSH_HOME，未配置模型或 API key；结果见 [host-qa.json](tests/host-qa.json)。

在已有 DSH Web profile 中安装：

```sh
dsh plugin --profile web add dsh-better-sidebar@0.28.2
dsh plugin --profile web add github:Lee-Eee-Eee/API-MRI#v0.8.0
dsh --profile web --dump-config
```

也可以下载 Release 中的 `dsh-api-mri-0.8.0.tgz`，将第二条命令改为：

```sh
dsh plugin --profile web add ./dsh-api-mri-0.8.0.tgz
```

重启 DSH，在右侧栏的「新建标签页」菜单选 **API-MRI**。`dsh plugin add` 会装配插件层，毋须再手写一份 Cordis 插件行。

## 启动并连接

打开页签后的「启动 API-MRI」会显示当前 DSH 地址对应的命令。例如 DSH 在 `http://127.0.0.1:3080`：

Windows：在解压后的 API-MRI 程序目录运行：

```powershell
.\API-MRI.exe --embed-origin http://127.0.0.1:3080 --no-browser
```

macOS / Linux：按[主项目的源码安装指南](https://github.com/Lee-Eee-Eee/API-MRI#readme)装好 Python 环境，在该环境运行：

```sh
python -m relay_audit.cli ui --embed-origin http://127.0.0.1:3080
```

然后在页签中点击「连接」。`--embed-origin` 必须与浏览器地址栏中的 DSH **协议、主机和端口**一致；`localhost` 与 `127.0.0.1` 是两个不同来源。更换 DSH 端口后，按新地址重启 API-MRI。

API-MRI 使用其他端口时，修改页签的服务地址。支持 `http`、`https` 和 `127.0.0.1`、`localhost`、`[::1]`；地址只包含来源，不附路径、查询参数或账号密码。DSH 与 API-MRI 使用不同端口。

「打开 ↗」在独立窗口继续使用同一服务。「更换连接」关闭当前嵌入并回到地址表单。首次打开、地址编辑和页签注册均不调用模型 API；具体检测仍由你在 API-MRI 中发起。

## 连接失败时

- 在浏览器单独打开 API-MRI 地址，确认程序已经启动。
- 用页签中生成的命令重启 **v0.8.0 或以上** API-MRI；旧版没有嵌入授权与握手。
- 确认 DSH 地址是回环来源；此版本接入回环地址上的 DSH Web。
- 在 HTTPS DSH 中连接 HTTP 服务若被浏览器拦截，使用同为 HTTP 的 DSH Web，或为 API-MRI 配置对应的 HTTPS 回环服务。

## 凭据与页面边界

API key 在 API-MRI 工作台内填写。插件的 Node 入口不注册 HTTP 代理、不启动进程，也不读取 DSH 的账号、会话或密钥。插件只在 DSH 的浏览器存储中保存一个经过校验的服务地址。

API-MRI 页面与 DSH 处于不同来源。双方握手只传 `type`、随机 `nonce` 和 API-MRI 的 `version`，父页面核对发送窗口、来源与 nonce 后显示连接状态。iframe 允许脚本、同源请求、表单与下载；不允许顶层跳转。API-MRI 默认拒绝被嵌入，只有启动时指定的 DSH 来源能打开它。

## 开发与打包

在 API-MRI 仓库根目录运行：

```sh
npm run build:dsh
npm run test:dsh
npm pack
```

构建与单元测试只需要 Node.js；没有安装脚本和运行时依赖。React 由 DSH 提供，Better Sidebar 通过 peer 依赖共享。预构建的 `lib/` 随 Git 与 tarball 分发。

开发时，可把安装命令换成绝对路径：

```sh
dsh plugin --profile web add link:/absolute/path/to/API-MRI
```

`src/index.js` 是空的宿主入口；`src/client.js` 使用 `ctx.betterSidebar.registerTab()`，由 Cordis effect 回收注册；`src/core.js` 管理地址、存储与握手。页签隐藏时停止父页面的握手计时与事件监听，卸载时清理。模型请求和页面自身的任务仍由 API-MRI 管理。

### 自有插件索引

DSH 的发现页接受 format-1 索引。发布者先提交发布源码，再用对应的 **40 位提交 SHA** 生成索引：

```sh
node dsh-plugin/market-index.mjs --commit <40位小写SHA> --published-at <Unix秒> --out dsh-plugin/market/index.json
```

本版本的[插件索引](https://github.com/Lee-Eee-Eee/API-MRI/releases/download/v0.8.0/api-mri-dsh-index.json)可直接添加到 DSH「插件 → 发现」。索引的版本来源固定到发布提交 SHA。自行维护索引时，将生成的 JSON 托管在可直接获取的 HTTPS 地址；该命令只生成索引，不向第三方市场提交内容。

## 接入依据

- [DSH 组合包打包与安装](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/publish.zh.md)
- [DSH 浏览器模块加载](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/client-modules.zh.md)
- [Better Sidebar 外部插件指南](https://github.com/omdsh-dev/DSH-better-sidebar/blob/2d736f5496fe97f4bba6af37d99861649b9ef3c0/docs/external-plugin-guide.md)
- [DSH 插件索引格式](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/util/plugin-market-index/src/types.ts)
