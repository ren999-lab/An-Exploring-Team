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
│   ├── topology_recognizer.py   #   多角色标注：电流镜/差分对/尾源/有源负载/共源共栅/输出级…
│   └── param_reducer.py         #   参数约减 + 变量CSV + 变量化网表
├── optimization.py              # 第③问 提交入口（CLI 与赛题 run_opt_command.sh 对齐）
├── q3_optimizer/                # 第③问 优化器（可行性优先多目标 DE）
│   ├── paramfile.py             #   参数文件保真读写 + 单位换算
│   ├── simulator.py             #   仿真适配层 + 指标解析 + 本地测试替身
│   └── optimize.py              #   约束处理 / 预算规划 / DE / Pareto 存档 / 断点续跑
├── tools/pack_submission.py     # 第④问 提交包打包与结构校验
├── tests/                       # 回归测试集（84 个用例，无需 API Key / 无需服务器）
│   ├── netlists/*.sp            #   金标准网表（每个都对应一个历史缺陷）
│   ├── data/                    #   参数文件 / variables.csv 样例
│   ├── test_spec_rules.py       #   第①问：四要素、GM、中英文、单位换算
│   ├── test_topology.py         #   第②问：命名判型、作用域、镜像比、模块识别
│   └── test_optimizer.py        #   第③问：参数保真、指标解析、约束优先、端到端
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

# 第③问：尺寸优化（本地无服务器时用 --mock 验证算法链路）
python optimization.py --param_file tests/data/param_init.txt \
    --variables-csv tests/data/variables.csv --mock --output_path ./_local_run
# 真实运行（拿到官方脚本后，--sim-cmd 填实际命令模板）
python optimization.py --ae_lib <lib> --ae_cell <cell> --ae_view <view> \
    --mde_cell <tb> --mde_view <corner> --param_file extractcdfVal_0.txt \
    --variables-csv agent2_topology/output/variables.csv \
    --output_path ./results_case1 --output_file output.log \
    --sim-cmd "bash run_opt_command.sh {param_file} {out}"

# 打包提交（阶段 5）
python tools/pack_submission.py

# 回归测试（84 个用例，纯本地、不需要 Key、不需要服务器）
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

## 第②问：识别规则一览（多角色标注）

识别器采用**多角色（multi-label）**模型：一个器件可以同时承载多个功能角色。
这是关键架构决策——原实现是互斥划分，PMOS 镜像一旦被判成"电流镜"就不再可能是
"有源负载"，于是标准两级运放的模块列表里**根本没有"有源负载"**。

| 模块 | 判据 | 置信度 |
|---|---|---|
| 输入对管（差分对） | 两管极性相同、源极共连于非电源轨节点、栅极不同；**支持多对** | 0.95 |
| 尾电流源 | 漏极接差分对源节点，源极接电源轨 | 0.90 |
| 电流镜 | 同极性 + 同源极 + 栅极互联；要求组内有二极管连接参考臂，**或**栅节点是内部节点（非子电路端口），避免把"共享外部偏置的两管"误判为镜像 | 0.90 |
| 共源共栅（cascode） | 上管源极 == 下管漏极、同极性、上管非二极管连接；按共享栅偏置分组。**排除**差分对叠在尾电流源上的情况 | 0.85 |
| 有源负载 | 沿"差分对漏极 → 电源轨"的负载通路识别（含共源共栅堆叠） | 0.85 |
| 输出级 | 漏极直接驱动输出端口（非电源轨、且不是任何 MOS 栅极的 `.subckt` 端口） | 0.80 |
| 补偿网络（密勒补偿） | 两端均非电源轨的电容（三端电容取前两个节点为信号端）；同节点电阻认作调零电阻 | 0.75 |
| 去耦电容 | 一端接电源轨的电容 | 0.80 |
| MOS 电容 | D/S/B 同节点、栅为另一极板；**与 Dummy 分开**，且仍保留优化变量 | 0.80 |
| Dummy 器件 | 命名含 `dum`（DUMMY/MDUM/XDUM），或 D/S 短接但 B 不同节点；不设优化变量 | 0.85 |
| 共模反馈（CMFB） | 器件连接 `cmfb/vcm/cm_fb` 等网络 | 0.70 |
| 匹配器件 | 同族前缀 + 同模型 + 同极性 + 尺寸参数完全相同；`subsumed_by` 标注它同时归属的结构模块 | 0.60 |

顶层输出 `device_roles`（器件 → 角色表）与 `role_summary`，报告里可直接引用。

> 注：`tests/sample_netlist.sp` 里 M5/M6 的注释写的是"输出级共源放大"，
> 但它的栅极接固定偏置 `biasn`、源极接第一级输出 `drn1`，
> 结构上是共栅/共源共栅级。识别按**网表连接**判定，因此 M5/M6 同时带
> "共源共栅"与"输出级"两个角色。若这是笔误，改网表即可。

## 第②问：参数约减策略（报告需说明"为何约减/为何保留"）

| 模块 | 变量处理 | 理由 |
|---|---|---|
| Dummy 器件 | 不设变量 | 不参与信号路径，无性能影响 |
| MOS 电容 | 保留 W/L/m | 是晶体管，PyAether 会参数化；容值影响补偿/去耦效果 |
| 电流镜 | 参考臂 W/L/m 自由；从臂**按初始比例**随参考臂缩放 | 保证镜像比不变，只移除冗余自由度 |
| 差分对 / 匹配器件 | 合并为一份自由变量（W/L/m 强制同尺寸） | 版图匹配要求 |
| 尾电流源 / 共源共栅 / 有源负载 / 输出级 / 无源网络 | 各器件 W/L/m 自由 | 独立设计自由度 |
| 电阻/电容/电感 | 有 W/L/Seg 就用这些；只有裸值时才把值本身作为变量 | 按 PDK 参数化，不发明网表里不存在的参数 |

**约减顺序很关键**：因为识别是多角色标注，同一批器件会出现在多个模块里
（例如输出级 M5/M6 同时属于"共源共栅"和"匹配器件"）。约减器分两遍处理：
**第一遍**先让"电流镜 / 差分对 / 匹配器件"这些*依赖型*模块认领器件并建立联动关系，
**第二遍**才处理其余模块。否则匹配对会各自变成独立变量，白白丢掉一半约减收益
（实测样例自由变量会从 15 个涨到 18 个）。

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

## 第③问：优化器设计（`optimization.py` + `q3_optimizer/`）

### 为什么是"可行性优先"而不是加权惩罚

赛题 6.3(2) 是**全有或全无**：PM / GM / I_OPA 三项，任何一项在**任意 PVT** 下不满足，
该项直接扣满 10 分；而 6.3(3) 的 DCGain / UGB / Area 是按名次比例给分。所以最优策略是：

> **先保证 100% 可行，再在可行域内优化目标。**

选择算子用约束支配（Deb 思想）：只要有可行解就绝不接受不可行解；两个都不可行时比较
归一化违反度。实测在测试替身上，优化器把 Gain 81.8→93.2dB、UGB 0.84→3.0GHz、
Area 76.5→64.8µm²，**全程没有让任何约束失效**。

### 预算倒推（3 小时时限）

```
可用评估次数 = 总时限 × (1 - 预留比例) ÷ 单次仿真耗时
```
预留默认 20%，留给反标与全 PVT 验证。**先跑一次真实仿真测出单次耗时**，再决定种群
规模与代数——这样能保证不会跑到一半被时限杀掉。相关函数：`plan_budget()`。

### 断点续跑

每次迭代落盘 `checkpoint.json`（含种群、适应度、最优解、Pareto 存档），
VPN 掉线后加 `--resume` 继续，不浪费已花掉的时间。

### 过程留痕（评分项 6：运行次数/时间/内存）

`optimization_log.jsonl` 逐次记录每次评估的参数、指标与违反度；
`optimization_result.json` 汇总评估次数、代数、耗时、约束满足情况与 Pareto 解集。

### 接口契约（阶段 1 拿到真实资产后需要替换的部分）

| 项 | 当前状态 | 拿到真实资产后要做什么 |
|---|---|---|
| CLI 参数 | 与 PDF 第 13–14 页完全一致 | 无需改动 |
| `--param_file` 格式 | 保真读写（任意 `key=value`/`key : value` 格式，未改动行逐字节一致） | 补一组真实样例的回归用例 |
| 仿真命令 | `--sim-cmd` 模板（含 `{param_file}` / `{out}` 占位符） | 填入官方脚本实际命令 |
| 指标解析 | 宽松正则，多 corner 取**最坏值** | 用真实 `output.log` 校准 |
| 优化变量与边界 | `--variables-csv`（第②问产物）提供 min/max/type | 对齐 PyAether 变量命名 |
| `MockSimulator` | 本地测试替身 | **不要**用于真实结果，仅验证算法链路 |

> ⚠️ `MockSimulator` 是解析式测试替身，其数值**没有任何物理意义**，
> 只用于在无服务器条件下验证优化器、约束守卫、预算与续跑逻辑。

## 已知限制 / 待办

前两轮已修复：有源负载不触发、共源共栅误判、MOS 电容与 Dummy 混淆、
只支持一个差分对、补偿网络未成模块、CMFB 缺失、跨 subckt 缝合、器件命名依赖首字母。

仍存在的限制（诚实记录，避免报告过度声称）：

1. **CMFB 只按网络命名识别**，不具备结构性共模检测（共模采样网络/共模反馈放大器的
   拓扑分析）能力。网表里没有 `cmfb/vcm` 命名网络时不会触发。
2. **匹配器件依赖"同族前缀 + 参数完全相同"**。命名不成族（如 `MA`/`MB` 之外的
   自定义名）或参数有微小差异的匹配对不会被归类。
3. **有源负载的负载通路判据**基于"漏极接在差分对漏极节点上并向上堆叠"。
   折叠共源共栅里级联管（M4/M5）会被判为共源共栅、其下方电流源（M6/M7）判为有源负载，
   这是有意的划分，但与"把整个折叠支路都算负载"的直觉不同。
4. **未解析的层次实例**：`X` 实例仅在本文件内有 `.subckt` 定义时展开（带 `实例名.` 前缀）；
   展开出来的器件没有原始网表行，无法在约减网表中就地变量化，会在文件头列出提示。
5. **R/C 同时带裸值与 W/L/Seg** 时（如 `R2 d2 vss 3k seg=2 w=2u l=10u`），
   裸值原样保留、几何量变量化；真实 PDK 器件模型下是否应删除裸值需按器件模型确认。
6. **只做拓扑/连接级分析**，不含小信号或 AC 分析，因此无法验证识别结果的电学合理性。
7. **双极型（Q）与二极管（D）** 目前只记录、不做参数化。
