"""app.py 的冒烟测试：不开浏览器、不装 streamlit 也能验证页面脚本能跑完。

做法：用一个 stub 顶替 `streamlit` 模块，再 `runpy` 执行 app.py 全文。
这样能抓到真实运行时错误（拼写错的 st.xxx、参数用错、变量未定义、
按钮回调里的异常），而这些问题靠"语法编译通过"是发现不了的。

注意：stub 只保证脚本**跑得完**，不校验视觉效果——视觉要在浏览器里看。
"""

import runpy
import sys
import types
import unittest
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Ctx:
    """既可当上下文管理器、又可当返回值用的占位对象。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __bool__(self):
        return True


class FakeStreamlit:
    """记录调用、返回可控值的 streamlit 替身。"""

    def __init__(self):
        self.calls = []
        self.session_state = {}
        self.errors = []

    def _note(self, name, **kw):
        self.calls.append((name, kw))

    # ---- 需要返回具体值的控件（否则 int()/比较会炸）-----------------------
    def text_input(self, *a, **kw):
        self._note("text_input")
        return kw.get("value", "")

    def text_area(self, *a, **kw):
        self._note("text_area")
        return kw.get("value", "")

    def number_input(self, *a, **kw):
        self._note("number_input")
        return kw.get("value", 0)

    def toggle(self, *a, **kw):
        self._note("toggle")
        # 强制离线模式：冒烟测试不能真去调 Qwen（慢、要 Key、还会产生费用）
        return True

    def radio(self, *a, **kw):
        self._note("radio")
        options = a[1] if len(a) > 1 else kw.get("options", [])
        return options[0] if options else None

    def file_uploader(self, *a, **kw):
        self._note("file_uploader")
        return None

    def button(self, *a, **kw):
        self._note("button")
        return True                           # 触发各标签页的主流程

    def columns(self, n, *a, **kw):
        self._note("columns")
        return [self for _ in range(int(n))]

    def tabs(self, labels, *a, **kw):
        self._note("tabs")
        return [_Ctx() for _ in labels]

    def sidebar(self, *a, **kw):
        self._note("sidebar")
        return nullcontext()

    def expander(self, *a, **kw):
        self._note("expander")
        return nullcontext()

    def error(self, *a, **kw):
        self._note("error")
        self.errors.append(str(a[0]) if a else "")

    # ---- 其余一律当作可调用的渲染指令 ------------------------------------
    def __getattr__(self, name):
        def call(*a, **kw):
            self._note(name)
            return _Ctx()
        return call


def run_app_with_stub():
    fake = FakeStreamlit()
    module = types.ModuleType("streamlit")
    for attr in dir(fake):
        if attr.startswith("_"):
            continue
        setattr(module, attr, getattr(fake, attr))
    # __getattr__ 兜底：st 上任何未显式声明的 API 都返回一个可调用对象
    module.__getattr__ = lambda name: (lambda *a, **kw: (
        fake._note(name), _Ctx())[1])
    module.session_state = fake.session_state

    # st.sidebar 在 Streamlit 里是**上下文管理器对象**，不是函数（`with st.sidebar:`）
    module.sidebar = _Ctx()
    saved = sys.modules.get("streamlit")
    sys.modules["streamlit"] = module
    try:
        runpy.run_path(str(ROOT / "app.py"), run_name="__streamlit_stub__")
    finally:
        if saved is not None:
            sys.modules["streamlit"] = saved
        else:
            sys.modules.pop("streamlit", None)
    return fake


class TestAppRuns(unittest.TestCase):
    def test_script_executes_without_exception(self):
        fake = run_app_with_stub()
        names = [n for n, _ in fake.calls]
        self.assertIn("set_page_config", names)
        self.assertIn("tabs", names)

    def test_all_three_agents_render_something(self):
        """三个标签页的主流程都要真的执行到渲染，而不是静默跳过。"""
        fake = run_app_with_stub()
        names = [n for n, _ in fake.calls]
        self.assertIn("json", names)          # ① Spec JSON
        self.assertIn("dataframe", names)     # ② 模块表/变量表 ③ 指标表
        self.assertIn("download_button", names)

    def test_no_unexpected_error_banners(self):
        """除了"缺 Key/缺内容"这类预期提示，不应有其它报错横幅。"""
        fake = run_app_with_stub()
        unexpected = [e for e in fake.errors
                      if e and ("API Key" not in e) and ("网表内容为空" not in e)]
        self.assertEqual(unexpected, [], f"页面出现非预期报错: {unexpected}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
