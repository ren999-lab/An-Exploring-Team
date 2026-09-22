# Spec 智能体使用说明

本项目的第 1 问智能体把一句中文电路需求转换为结构化 JSON。它不是在电脑上运行大语言模型，而是在本机运行 Python 程序，并通过 API 调用赛题指定的 Qwen3.8-Max。

## 它在整个赛题中做什么

```text
中文需求
  -> Spec 智能体（本地 UI / Python）
  -> spec.json
  -> 拓扑识别与参数约减（Agent 2）
  -> variables.csv + reduced netlist
  -> 华大九天服务器：PyAether / MDE / ALPS 仿真与尺寸优化
```

UI 只做第一步。它的作用是让用户输入需求、查看 JSON、下载 JSON；它不会替代电路仿真或尺寸优化。

## 首次准备

本机 Python 环境位于：

```text
D:\Deepseek Program\ICT\.conda
```

打开 PowerShell，运行：

```powershell
conda activate "D:\Deepseek Program\ICT\.conda"
cd "D:\Deepseek Program\ICT\An-Exploring-Team"
python -m pip install -r requirements.txt
```

## 启动 UI

推荐直接双击项目根目录的 `启动UI.bat`。它会优先使用本项目上级目录的 `.conda` 环境，避免误用 Anaconda base 环境。

`启动UI.bat` 中的提示文字刻意只使用英文字符，这是为了兼容 Windows `cmd` 的本地编码；请不要用记事本在这个批处理文件中加入中文提示，否则可能再次出现“不是内部或外部命令”的乱码报错。

也可以在同一个 PowerShell 窗口运行：

```powershell
python -m streamlit run app.py
```

浏览器会自动打开；若没有自动打开，请访问：

```text
http://localhost:8501
```

项目已关闭 Streamlit 的匿名使用统计，因此首次启动不需要填写 Email，也不会因统计服务无法连接而影响启动。

页面中依次完成：

1. 在 `DashScope API Key` 输入框粘贴赛事方或阿里云百炼提供的 Key。
2. 在“电路性能需求”中填写中文需求，例如：

   ```text
   设计一个带有共模反馈的全差分运放，DC增益不低于95dB，单位增益带宽至少60MHz，
   相位裕度大于55度，所有PVT下工作电流不超过3mA，并尽量减小面积。
   ```

3. 点击“生成 Spec JSON”。
4. 查看校验后的 JSON；点击“下载 spec.json”保存后交给下一阶段。

## API Key 安全规则

- UI 输入框会隐藏 Key，Key 不会写入源码、配置文件、JSON 输出或 Git 提交。
- Key 仅留在当前 Streamlit 页面会话的内存中；每次模型调用结束后，程序会恢复原本的进程环境变量。
- 不要把 Key 发到聊天群、PPT、报告、截图或代码仓库。
- 已经在公开聊天、截图或仓库中出现过的 Key，应立即到百炼控制台轮换。

## 队友首次安装

队友无需复制你的 Conda 环境，只需各自在自己的电脑配置一次。先安装 Miniconda 或 Anaconda，然后在 PowerShell 中执行：

```powershell
cd "团队项目所在目录\An-Exploring-Team"
conda create --prefix "..\.conda" python=3.11 pip -y
conda run --prefix "..\.conda" python -m pip install -r requirements.txt
```

随后双击 `启动UI.bat` 即可。每个人在自己的浏览器页面临时输入 API Key；不要把 Key 写入共享文件。

## 输出 JSON 怎么看

生成的 `spec.json` 主要包括：

| 字段 | 含义 | 示例 |
|---|---|---|
| `hard_constraints` | 必须满足的硬约束 | PM >= 55 deg、I_OPA <= 3 mA |
| `optimization_targets` | 在满足硬约束后追求的目标 | UGB 最大化、Area 最小化 |
| `units` | 指标单位 | MHz、mA、dB、deg、um^2 |
| `priorities` | 优化目标优先级 | Area: 1 |
| `cross_check` | Qwen 与规则解析的差异记录 | 供报告审计与人工复核 |
| `assumptions` | 程序自动补充的赛事默认要求 | 需在报告中说明 |

程序会以赛题基线约束补全缺失项，例如 PM、GM、I_OPA 和 DC Gain。最终提交前，团队应复核 `assumptions`，避免把用户未提出的要求误写成原始需求。

## 不使用 UI 的离线备用方式

当 API 网络异常、Key 暂不可用或需要批量回归测试时，可以使用不联网的规则解析：

```powershell
conda activate "D:\Deepseek Program\ICT\.conda"
cd "D:\Deepseek Program\ICT\An-Exploring-Team"
python -m agent1_spec_parser.main --no-llm "设计全差分运放，相位裕度不低于60度，工作电流不超过3mA，面积尽可能小。"
```

输出位置：

```text
agent1_spec_parser\output\spec.json
```

离线规则是可靠的兜底路径，但 UI 的正式演示默认通过 Qwen3.8-Max API 完成解析。

## 常见问题

### 点击后提示“请先输入 DashScope API Key”

输入框为空。粘贴有效 Key 后重试。

### 点击后提示“解析失败”

依次检查网络、Key 是否有效、百炼账户是否有对应模型权限和余额。错误提示中不要复制或截取 Key。

若页面显示“已生成离线规则备份结果”，说明 Qwen 在线调用没有成功，页面展示的是本地规则解析的备份 JSON；该 JSON 可用于排查，但正式演示时应确保输出的 `source` 是 `llm+rule`。

### 浏览器没有自动打开

保持 PowerShell 中的 Streamlit 进程运行，并手动访问 `http://localhost:8501`。

### 如何停止页面

回到启动 Streamlit 的 PowerShell 窗口，按 `Ctrl+C`。

## 团队协作建议

- 成员 A：维护 UI、Qwen 提示词、Spec JSON 和调用日志。
- 成员 B：把赛方 Public 电路导出的网表接入 Agent 2，检查电流镜、差分对、CMFB、Dummy 和匹配关系。
- 成员 C：在华大服务器上实现 PVT 仿真结果解析与约束优先的尺寸优化。

提交材料中应保存：输入需求、生成的 `spec.json`、Qwen/规则交叉校验记录、拓扑识别结果、变量表、全 PVT 仿真报告和最终电路图。
