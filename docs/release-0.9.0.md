# API-MRI 0.9.0

示例馆按模型发布日期排序，统一跨来源名称，模型与运行档位分开选择。默认展示42道至少有6个模型对照的题目；少量对照题可单独展开。公开HTML/SVG在隔离区按需加载，three.js r160和OrbitControls随包提供，WebGL作品也可页内查看。

新增「能力参考」：按模型检索公开benchmark成绩，保留出处、指标、版本、推理配置与Harness；另设BFCL、SWE-bench、Terminal-Bench、LiveBench、Artificial Analysis和Arena入口。元数据读取日期2026-11-02。

「工具调用」改为「工具协议与回路」，报告增加固定最小Harness的执行条件。

Windows x64的DSH插件首次打开时下载、校验、解压并启动对应版本后端，以后自动启动。独立Windows ZIP仍可直接解压使用。

下载`dsh-api-mri-0.9.0.tgz`安装插件，或下载`API-MRI-0.9.0-windows-x64.zip`使用独立工作台。`api-mri-runtime.json`给出插件运行包的版本、大小和SHA-256；`SHA256SUMS.txt`列出发布文件摘要。macOS/Linux继续使用源码和手动连接。
