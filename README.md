# An-Exploring-Team
Our group is preparing for the competition

# 我们需要决定一个大概的框架，关于比赛的作品

# 梳理
1. 我们要做的是一个可以自己画板子，进行仿真的app
2. 我们要思考：UI、端口调用
---

# 赛题二：模拟电路 AI 智能化设计（2026"中国电子杯"）

## 项目结构

```
├── agent1_spec_parser/      # 第①问 自然语言 Spec 解析（本地，Qwen3.8-Max API）
│   ├── main.py              #   入口: python -m agent1_spec_parser.main "自然语言描述"
│   ├── prompt.py            #   SystemPrompt + few-shot（技术报告需附）
│   ├── schema.py            #   Spec JSON 结构定义与校验（四大评分要素）
│   └── llm_client.py        #   Qwen API 封装 + 推理链日志
├── agent2_topology/         # 第②问 拓扑模块识别与参数约减（本地，纯规则算法）
│   ├── main.py              #   入口: python -m agent2_topology.main <netlist.sp>
│   ├── netlist_parser.py    #   SPICE 网表解析
│   ├── topology_recognizer.py  # 电流镜/差分对/尾电流源/负载/Dummy 识别
│   └── param_reducer.py     #   参数约减 + 变量CSV + 约减网表输出
├── tests/sample_netlist.sp  # 本地测试网表（全差分两级运放）
├── config/config.example.yaml  # 配置模板（API Key 用环境变量 DASHSCOPE_API_KEY）
└── logs/                    # LLM 推理链日志（报告素材）
```

## 快速开始

```bash
pip install -r requirements.txt
setx DASHSCOPE_API_KEY "sk-你的Key"   # 阿里云百炼申请，重开终端生效

# 第①问：Spec 解析（不加 --no-llm 则调 Qwen3.8-Max）
python -m agent1_spec_parser.main --no-llm "设计全差分运放，增益不低于95dB..." 

# 第②问：拓扑识别与参数约减
python -m agent2_topology.main tests/sample_netlist.sp
```

## 交付物对应（评分点）

| Agent | 输出 | 评分要求 |
|---|---|---|
| Agent1 | `output/spec.json` | 硬约束/优化目标/单位/优先级四要素齐全 |
| Agent2 | `output/topology_result.json` | 模块识别结果 + 识别依据 |
| Agent2 | `output/variables.csv` | 约减后变量列表 |
| Agent2 | `output/netlist_reduced.sp` | 约减后网表 |
