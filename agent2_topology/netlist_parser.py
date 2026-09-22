"""SPICE 网表解析器：提取器件、连接关系与尺寸参数。

支持：M(晶体管) R(电阻) C(电容) 器件、'+' 续行、'*' 注释、'.SUBCKT/.ENDS'、
.include/.lib 忽略。输出统一器件字典列表 + 节点连接表。
"""

import re
from dataclasses import dataclass, field


@dataclass
class Device:
    name: str
    dtype: str            # 'M' / 'R' / 'C'
    nodes: list           # 连接节点列表（M: d g s b; R/C: p n）
    model: str = ""       # 模型名（MOS: NM/PM 型）
    params: dict = field(default_factory=dict)  # w/l/m/seg/c 等小写键

    @property
    def is_nmos(self):
        return self.dtype == "M" and self.model.upper().startswith("N")

    @property
    def is_pmos(self):
        return self.dtype == "M" and self.model.upper().startswith("P")

    def __str__(self):
        return f"{self.name}({' '.join(self.nodes)}) {self.model} {self.params}"


class Netlist:
    def __init__(self, devices, subckts, source_lines):
        self.devices = devices              # list[Device]
        self.subckts = subckts              # {name: (ports, devices)}
        self.source_lines = source_lines    # 原始行（生成约减网表用）

    def mos_list(self):
        return [d for d in self.devices if d.dtype == "M"]

    def by_node(self, node):
        return [d for d in self.devices if node in d.nodes]


def _parse_params(tokens):
    """解析 'w=2u l=0.5u m=2' 形式参数。"""
    params = {}
    for t in tokens:
        if "=" in t:
            k, v = t.split("=", 1)
            params[k.lower()] = v
    return params


def _scale(v):
    """SPICE 单位后缀 → 数值（um 等）。'2u'→2e-6, '0.5'→0.5。"""
    mult = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
            "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
    if not v:
        return None
    s = str(v).strip().lower()
    m = re.match(r"^([\-0-9.]+e?[\-0-9]*)([a-z]*)$", s)
    if not m:
        return None
    num, suf = m.groups()
    try:
        val = float(num)
    except ValueError:
        return None
    return val * mult.get(suf, 1.0)


def parse_netlist(path_or_text) -> Netlist:
    text = path_or_text
    if "\n" not in str(path_or_text):
        text = open(path_or_text, encoding="utf-8", errors="ignore").read()

    # 合并续行
    merged = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("*") or line.startswith("$"):
            continue
        if line.startswith("+") and merged:
            merged[-1] += " " + line[1:].strip()
        else:
            merged.append(line)

    devices, subckts, cur_sub = [], {}, None
    for line in merged:
        low = line.lower()
        if low.startswith(".subckt"):
            toks = line.split()
            cur_sub = toks[1]
            subckts[cur_sub] = {"ports": toks[2:], "devices": []}
            continue
        if low.startswith(".ends"):
            cur_sub = None
            continue
        if low.startswith("."):  # .include/.lib/.param 等忽略
            continue

        toks = line.split()
        name, dtype = toks[0], toks[0][0].upper()
        if dtype == "M":
            if len(toks) < 6:
                continue
            dev = Device(name, "M", toks[1:5], toks[5],
                         _parse_params(toks[6:]))
        elif dtype == "R":
            if len(toks) < 4:
                continue
            dev = Device(name, "R", toks[1:3], "", _parse_params(toks[3:]))
        elif dtype == "C":
            if len(toks) < 4:
                continue
            dev = Device(name, "C", toks[1:3], "", _parse_params(toks[3:]))
        else:
            continue  # 电源/V/I 暂不作为优化对象，保留在 source_lines

        if cur_sub:
            subckts[cur_sub]["devices"].append(dev)
        devices.append(dev)

    return Netlist(devices, subckts, merged)
