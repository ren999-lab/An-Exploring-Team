# An-Exploring-Team

2026"中国电子杯"高校ICT产教融合创新大赛 · 赛题二：模拟电路 AI 智能化设计（北京华大九天）

本仓库实现第①②问（本地部署部分）：**自然语言 Spec 解析** 与 **拓扑模块识别 + 参数约减**。

---

## 项目结构

```
├── agent1_spec_parser/          # 第①问 自然语言 Spec 解析
│   ├── main.py                  #   入口 + 规则解析引擎（无 Key 也能完整跑）
│   ├── prompt.py                #   SystemPrompt + few-shot（技术报告需附）
│   ├── schema.py                #   指标词典 + Spec 结构 + 四要素完整性保障
│   └── llm_client.py            #   Qwen3.8-Max 调用 + 推理链(reasoning)日志
├── agent2_topology/             # 第②问 拓扑识别与参数约减（纯规则算法）
│   ├── main.py                  #   入口 + 交付物汇总
│   ├── netlist_parser.py        #   SPICE 网表解析（命名中立、保留 subckt 作用域）
│   ├── topology_recognizer.py   #   电流镜/差分对/尾电流源/有源负载/匹配/Dummy
│   └── param_reducer.py         #   参数约减 + 变量CSV + 变量化网表
├── tests/                       # 回归测试集（43 个用例，无需 API Key）
│   ├── netlists/*.sp            #   金标准网表（每个都对应一个历史缺陷）
│   ├── test_spec_rules.py       #   第①问：四要素、GM、中英文、单位换算
│   └── test_topology.py         #   第②问：命名判型、作用域、镜像比、无源器件
├── tests/sample_netlist.sp      # 示例网表（全差分两级运放）
├── config/config.example.yaml   # 配置模板（API Key 用环境变量 DASHSCOPE_API_KEY）
└── logs/                        # LLM 推理链日志（技术报告要求的 CoT 素材）
```

---

## 快速开始

```bash
pip install -r requirements.txt
setx DASHSCOPE_API_KEY "sk-你的Key"   # 阿里云百炼申请，重开终端生效

# 第①问：Spec 解析（带 Key 时 = LLM + 规则交叉校验；加 --no-llm 则纯规则）
python -m agent1_spec_parser.main "设计全差分运放，PM≥60°，工作电流3mA，增益尽可能大..."
python -m agent1_spec_parser.main --no-llm "..."      # 无 Key / 离线
python -m agent1_spec_parser.main --strict "..."      # 只输出原文明确写到的内容

# 第②问：拓扑识别与参数约减
python -m agent2_topology.main tests/sample_netlist.sp

# 回归测试（43 个用例，纯规则、不需要 Key）
python -m unittest discover -s tests -t . -v
SPEC_TEST_LLM=1 python -m unittest discover -s tests -t .   # 额外跑真实 LLM 冒烟
```

---

## 架构决策（技术报告可用）

### 1. 规则解析是主干，LLM 是增强层

原设计是"LLM 优先、规则兜底"，导致没有 API Key 时能力大幅缩水。现在反过来：

* **规则引擎**（`agent1/main.py` + `schema.py`）确定性地覆盖中英文指标词、比较词、
  单位换算，并**保证"硬约束 / 优化目标 / 指标单位 / 目标优先级"四要素永不为空**
  （赛题 6.3：每缺一项扣 1 分）；
* **LLM（Qwen3.8-Max）** 负责自由文本兜底；
* 两者结果做**交叉校验**，差异写进 Spec 的 `cross_check` 字段，直接作为报告素材。

实测交叉校验的价值（PDF 示例文本）：LLM 漏掉了 `GM` 和 DCGain/UGB 的"越大越好"目标，
`cross_check` 记为 `rule_only_constraints: ["GM"]`、`rule_only_targets: ["DCGain","UGB"]`，
由规则补齐。这也是"为什么不能只靠 LLM"的量化证据。

### 2. 赛题强制约束补齐（可关闭、可审计）

原文未写明的赛题硬性要求（PM≥50°、GM≤−10dB、I_OPA≤3mA 全 PVT、DCGain≥40dB）
会在缺失时补入 `hard_constraints`，并**逐条记进 `assumptions`**、带 `assumed: true` 标记。
用 `--strict` 可完全关闭。

### 3. LLM 端点选择（实测结论）

赛题指定的 `qwen3.8-max` **只在 OpenAI 兼容端点提供服务**：

| 端点 | 模型 | 结果 |
|---|---|---|
| `dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.8-max` | **200 ✓** |
| `dashscope.aliyuncs.com/api/v1`（SDK 默认） | `qwen3-max` 之外的模型 | — |
| `dashscope.aliyuncs.com/api/v1` | `qwen3.8-max` | **400 `InvalidParameter: url error`** |
| `dashscope.aliyuncs.com/api/v1` | `qwen-max`（对照） | 200 |

对照实验说明 Key 与网络正常，是**模型与端点不匹配**。因此 `llm_client.py` 默认用
**标准库 urllib** 直连兼容端点（不引入任何第三方 LLM 服务客户端库）；
若要改回官方 SDK，设 `DSH_LLM_TRANSPORT=dashscope`。

另外 `qwen3.8-max` 是**思考型模型**，响应里 `content` 是答案、`reasoning_content`
是思维链。赛题技术报告明确要求"推理链日志（Chain-of-Thought）"，
所以两者都会落盘到 `logs/llm_*.json`。

---

## 第②问：参数约减策略（报告需说明"为何约减/为何保留"）

| 模块 | 变量处理 | 理由 |
|---|---|---|
| Dummy 器件 | 不设变量 | 不参与信号路径 |
| 电流镜 | 参考臂 W/L/m 自由；从臂**按初始比例**随参考臂缩放 | 保证镜像比不变，只移除冗余自由度 |
| 差分对 / 匹配器件 | 合并为一份自由变量（W/L/m 强制同尺寸） | 版图匹配要求 |
| 尾电流源 / 其他 | 各器件 W/L/m 自由 | 独立设计自由度 |
| 电阻/电容/电感 | 有 W/L/Seg 就用这些；只有裸值时才把值本身作为变量 | 按 PDK 参数化，不发明网表里不存在的参数 |

**为什么 `m` 必须是变量**：赛题面积公式是 `f*w*L*m*1.5`，`m` 是最省面积的旋钮；
原实现把 `m` 固化在网表里，等于丢掉了这个自由度。

**镜像比按初始比例保留**：镜像比可能用 W 表达（`w=4u` / `w=12u`）也可能用 `m`
表达（`m=1` / `m=3`）。按"W 与 m 分别保比例"缩放，既不改变原始设计点，又完成了约减。
从臂若按 >1 的比例放大，参考臂会自动标注 `derived_max`（例：`M7_m=M6_m*4` 且
PDK `m≤20` → `M6_m` 实际只能取到 5），供第③问设置优化上界。

## PDK 取值约束（PDF 第 8 页，已编码进 `netlist_parser.PDK_LIMITS`）

| 器件 | 参数 | 取值范围 |
|---|---|---|
| 晶体管 | W / L / m | 0.13u–20u / 0.13u–10u / 1–20（整数） |
| 电阻 | Seg | 1–20（整数） |
| 电容 | L | 0.13u–20u |

面积：晶体管 `f*w*L*m*1.5`，电容 `W*L`，电阻 `W*L*2`。

## 变量命名与第③问的衔接

变量名遵循 `<器件名>_<参数>`（如 `NM1_w`、`M7_m`、`R1_seg`），
与 PyAether 用 `instance 名字 + _cdf 参数`（PDF 例：`NM1_m`）生成的变量一致。

`variables.csv` 列：`variable, value, min, max, unit, type, devices, param, constraint, reason`
—— 其中 `value` 就是初值（对应题面 `M1_w=36u`），`min/max/type` 可直接作为第③问的边界。

`netlist_reduced.sp` 带 `.param` 声明块：自由变量给初值，联动变量以表达式引用参考变量
（如 `.param M5_w='M4_w*3'`），所有变量都被声明，**网表可自洽仿真**，且与 CSV 一一对应。

---

## 交付物对应（评分点）

| Agent | 输出 | 评分要求 |
|---|---|---|
| Agent1 | `output/spec.json` | 硬约束/优化目标/单位/优先级四要素齐全 + `cross_check`/`assumptions` |
| Agent2 | `output/topology_result.json` | 模块识别结果 + 识别依据 + 变量个数统计 + PDK 约束 |
| Agent2 | `output/variables.csv` | 约减后变量列表（含初值/上下界/类型） |
| Agent2 | `output/netlist_reduced.sp` | 约减后网表（含 `.param` 块） |

> ⚠️ `.gitignore` 目前忽略了 `agent1_spec_parser/output/` 与 `agent2_topology/output/`，
> 即上述四个**被评分的交付物不会进仓库**。若要以仓库形式提交输出文件，
> 需要调整 `.gitignore` 或在提交前重新运行两个 Agent 生成。

---

## 已知限制 / 待办（第②问规则仍有提升空间）

以下问题**不在本轮修复范围**，已在测试集中固化为"应识别但暂未识别"的行为：

1. **有源负载规则在标准两级运放上不触发**：接在差分对漏极的 PMOS 镜像本质就是
   有源负载，但会先被"电流镜"认领，导致模块列表里没有"有源负载"。
2. **共源共栅（cascode）器件会被误判为有源负载**：折叠共源共栅里栅接固定偏置、
   源接内部节点的管子，需要专门的 cascode 规则。
3. **MOS 电容与 Dummy 未区分**：`d==s==b` 短接的 MOS 去耦电容目前也归入 Dummy。
4. **只支持一个差分对**：双差分对电路里第二对不会被识别。
5. **补偿网络未单独成模块**：密勒补偿 RC/CC 与去耦电容目前统一归入
   "无源器件（RC/去耦，用途待确认）"。
6. **无 CMFB 识别规则**。
7. **未解析的层次实例 / `.param` 表达式**：`X` 实例在本文件内有 `.subckt` 定义时会
   展开（带 `实例名.` 前缀），否则按模型名兜底或原样保留待人工复核。
8. R/C 同时带裸值与 W/L/Seg 时（如 `R2 d2 vss 3k seg=2 w=2u l=10u`），
   裸值会原样保留、几何量变量化；真实 PDK 器件模型下是否应删除裸值需按器件模型确认。
