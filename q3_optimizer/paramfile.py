"""参数文件读写：与 MDE/PyAether 的 `param_file`（如 `extractcdfVal_0.txt`）对接。

设计原则：**保真优先**。我们目前还没有真实的 `extractcdfVal_0.txt` 样例，
格式未知，因此这一层的首要目标不是"解析出漂亮的结构"，而是：

1. **原样回写**：读入再写出，未改动的行必须逐字节一致（含注释、空行、缩进、
   分隔符风格、行尾注释）。
2. **只改值**：`set()` 只替换值部分，保留 `M1_w = 36u   # comment` 里的
   前缀、分隔符和行尾注释。

这样即使真实格式是 `key=value`、`key : value`、`key value` 还是分节格式，
我们的优化器都能安全地改写它，而不会因为"重新格式化"把官方脚本喂坏。

拿到真实样例后，只需在 `docs/server_assets.md` 里登记格式，并补一组回归用例。
"""

import re
from dataclasses import dataclass
from pathlib import Path

# 注释前缀：整行注释
_COMMENT_PREFIXES = ("#", "//", "*", ";", "!")
# key = value / key : value / key := value；值里允许出现除行尾注释外的任意字符
_KV_RE = re.compile(
    r"^(?P<prefix>\s*)"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_\.\[\]<>/\-]*)"
    r"(?P<sep>\s*(?:=|:|:=)\s*)"
    r"(?P<value>[^#;]*?)"
    r"(?P<tail>\s*(?:[#;].*)?)$"
)


def _is_comment(line: str) -> bool:
    s = line.lstrip()
    return (not s) or s.startswith(_COMMENT_PREFIXES)


@dataclass
class Entry:
    key: str
    value: str
    line_index: int
    prefix: str
    sep: str
    tail: str

    def render(self, value=None) -> str:
        v = self.value if value is None else str(value)
        return f"{self.prefix}{self.key}{self.sep}{v}{self.tail}"


class ParamFile:
    """保真读写 key=value 形式的参数文件。"""

    def __init__(self, lines):
        self.lines = list(lines)
        self.entries = {}          # key -> Entry（首次出现为准）
        self._index()

    # ---- 构建 --------------------------------------------------------------
    def _index(self):
        for i, line in enumerate(self.lines):
            if _is_comment(line):
                continue
            m = _KV_RE.match(line)
            if not m:
                continue
            key = m.group("key")
            if key in self.entries:
                continue           # 重复键：保留首次出现，避免误改
            self.entries[key] = Entry(
                key=key, value=m.group("value").strip(),
                line_index=i, prefix=m.group("prefix"),
                sep=m.group("sep"), tail=m.group("tail"))

    @classmethod
    def read(cls, path, encodings=("utf-8-sig", "utf-8", "gbk", "latin-1")):
        p = Path(path)
        text = None
        for enc in encodings:
            try:
                text = p.read_text(encoding=enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            text = p.read_text(encoding="utf-8", errors="ignore")
        return cls(text.splitlines())

    @classmethod
    def from_text(cls, text):
        return cls(text.splitlines())

    # ---- 读写 --------------------------------------------------------------
    def keys(self):
        return list(self.entries.keys())

    def has(self, key):
        return key in self.entries

    def get(self, key, default=None):
        e = self.entries.get(key)
        return e.value if e else default

    def set(self, key, value):
        """只改值，保留前缀/分隔符/行尾注释。键不存在时追加一行。"""
        e = self.entries.get(key)
        if e is None:
            self.lines.append(f"{key}={value}")
            self.entries[key] = Entry(key=key, value=str(value),
                                      line_index=len(self.lines) - 1,
                                      prefix="", sep="=", tail="")
            return
        e.value = str(value)
        self.lines[e.line_index] = e.render()

    def as_dict(self):
        return {k: e.value for k, e in self.entries.items()}

    def text(self):
        return "\n".join(self.lines) + "\n"

    def write(self, path):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.text(), encoding="utf-8")
        return p

    def copy(self):
        return ParamFile(list(self.lines))


# ---------------------------------------------------------------------------
# 变量表（variables.csv）<-> param_file
# ---------------------------------------------------------------------------
def load_variables_csv(path):
    """读取第②问输出的 variables.csv，返回 [{name, value, min, max, unit, type, ...}]。

    CSV 首列是 `variable`，这里统一补一个 `name` 别名，方便下游按统一字段名取用。
    """
    import csv
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("name"):
                row["name"] = row.get("variable", "")
            rows.append(row)
    return rows


def free_variables(variables):
    return [v for v in variables if (v.get("constraint") or "free") == "free"]


def build_bounds(variables, default_min=None, default_max=None):
    """从 variables.csv 的 min/max/type 列生成优化边界 [(name, lo, hi, is_int)]。"""
    bounds = []
    for v in free_variables(variables):
        lo = _to_float(v.get("min"), default_min)
        hi = _to_float(v.get("max"), default_max)
        is_int = (v.get("type") or "").strip().lower() == "int"
        bounds.append((v["name"], lo, hi, is_int))
    return bounds


def _to_float(s, fallback=None):
    if s is None or str(s).strip() == "":
        return fallback
    m = re.match(r"^\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", str(s))
    return float(m.group(1)) if m else fallback


# ---------------------------------------------------------------------------
# 带 SPICE 后缀的数值编解码
# ---------------------------------------------------------------------------
SPICE_SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
                "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
_VALUE_RE = re.compile(
    r"^\s*(?P<num>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(?P<suf>[a-zA-Z]*)\s*$")


def parse_value(text):
    """'36u' -> (3.6e-05, 'u')；纯数字 -> (值, '')；非数值 -> (None, '')."""
    m = _VALUE_RE.match(str(text or ""))
    if not m:
        return None, ""
    try:
        num = float(m.group("num"))
    except ValueError:
        return None, ""
    suf = m.group("suf")
    if suf.lower() == "meg":
        return num * 1e6, "meg"
    if suf and suf.lower() in SPICE_SUFFIX:
        return num * SPICE_SUFFIX[suf.lower()], suf
    if suf:
        return None, ""        # 带未知后缀（如表达式）-> 不参与数值优化
    return num, ""


def format_value(si_value, suffix="", sig=6):
    """SI 值 -> 保持原有后缀风格的字符串，例如 (3.6e-05,'u') -> '36u'。"""
    if si_value is None:
        return ""
    suf = suffix or ""
    scale = 1.0
    if suf:
        if suf.lower() == "meg":
            scale = 1e6
        else:
            scale = SPICE_SUFFIX.get(suf.lower(), 1.0)
    v = si_value / scale if scale else si_value
    if abs(v - round(v)) < 10 ** (-sig + 1) * max(abs(v), 1e-12):
        v = round(v)
    else:
        v = float(f"%.{sig}g" % v)
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return f"{v}{suf}"


class ParamCodec:
    """在 ParamFile 与"数值向量"之间转换，保留每个键原有的单位后缀风格。"""

    def __init__(self, param_file: ParamFile, keys):
        self.pf = param_file
        self.keys = list(keys)
        self.suffix = {}
        self.initial = []
        for k in self.keys:
            si, suf = parse_value(param_file.get(k))
            self.suffix[k] = suf
            self.initial.append(si)

    def numeric_keys(self):
        return [k for k, v in zip(self.keys, self.initial) if v is not None]

    def to_vector(self):
        return [v for v in self.initial if v is not None]

    def apply(self, keys, values):
        for k, v in zip(keys, values):
            self.pf.set(k, format_value(v, self.suffix.get(k, "")))
        return self.pf.text()

