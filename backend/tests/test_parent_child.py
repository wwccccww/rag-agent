"""
测试 chunk_text_hierarchical 的父子分块行为。
"""
import pytest
from app.services.text_extract import chunk_text_hierarchical


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _all_children(result):
    return [child for _, _, children in result for child in children]


def _parent_contents(result):
    return [pc for pc, _, _ in result]


# ── 测试用例 ──────────────────────────────────────────────────────────────────

MD_SMALL_SECTIONS = """\
# 需求文档

## 1. 登录功能

用户可以通过用户名和密码登录系统。

### 1.1 参数

- username: 字符串
- password: 字符串

### 1.2 响应

登录成功后返回 JWT Token。

## 2. 注册功能

新用户填写注册表单完成注册。

### 2.1 参数

- username: 字符串
- email: 邮件地址
- password: 密码（长度 ≥ 8）

### 2.2 验证

系统会校验邮箱是否已被注册，若重复返回 409。
"""


def test_returns_list_of_tuples():
    result = chunk_text_hierarchical(MD_SMALL_SECTIONS, max_chars=500, overlap=50)
    assert isinstance(result, list)
    assert len(result) > 0
    for item in result:
        assert len(item) == 3, "每项必须是 (parent_content, parent_meta, children) 三元组"
        parent_content, parent_meta, children = item
        assert isinstance(parent_content, str) and parent_content.strip()
        assert isinstance(parent_meta, dict)
        assert isinstance(children, list)


def test_small_sections_are_merged_into_parents():
    """三级标题（### 小节）内容短，应被合并为较大的父块；父块数量 < 总节数。"""
    result = chunk_text_hierarchical(
        MD_SMALL_SECTIONS,
        max_chars=500,
        overlap=50,
        min_parent_chars=150,
        max_parent_chars=1200,
    )
    # 文档共 2 个二级标题，父块数应 ≤ 原始小节数
    total_sections_approx = MD_SMALL_SECTIONS.count("\n###") + MD_SMALL_SECTIONS.count("\n##")
    parent_count = len(result)
    assert parent_count < total_sections_approx, (
        f"小节应被合并为较少的父块，得到 {parent_count} 个父块，原始节数约 {total_sections_approx}"
    )


def test_parent_content_contains_child_text():
    """父块内容应包含所有子块的原始文本（未被压缩）。"""
    result = chunk_text_hierarchical(
        MD_SMALL_SECTIONS,
        max_chars=300,
        overlap=30,
        min_parent_chars=100,
        max_parent_chars=800,
    )
    for parent_content, _, children in result:
        for child_content, _ in children:
            # 子块内容应出现在父块中（子块是从父块节中切出的）
            # 注：子块可能含续块前缀，取前 40 个字符检测即可
            key = child_content.strip()[:40].strip()
            if key and not key.startswith("[节："):  # 跳过续块前缀虚拟内容
                assert key in parent_content or any(
                    key in seg for seg in parent_content.split("\n\n")
                ), f"子块片段 {key!r} 未出现在父块中"


def test_single_large_section_splits_into_children():
    """若某节内容超过 max_chars，切出的子块数 > 1，父块完整保留该节文本。"""
    long_md = "## 大节\n\n" + ("这是一段很长的内容。" * 60)
    result = chunk_text_hierarchical(
        long_md,
        max_chars=200,
        overlap=20,
        min_parent_chars=100,
        max_parent_chars=2000,
    )
    assert len(result) >= 1
    parent_content, _, children = result[0]
    assert "大节" in parent_content
    assert len(children) > 1, "大节内容应被切成多个子块"


def test_non_markdown_degrades_gracefully():
    """纯文字（非 Markdown）应退化为普通切块，每块 children=[]。"""
    plain = "这是普通文本，没有任何 Markdown 标题。" * 20
    result = chunk_text_hierarchical(plain, max_chars=200, overlap=20)
    for _, _, children in result:
        assert children == [], "非 Markdown 退化后不应有子块"


def test_parent_meta_has_section_heading():
    """父块 meta 中应包含 section_heading。"""
    result = chunk_text_hierarchical(
        MD_SMALL_SECTIONS,
        max_chars=500,
        overlap=50,
        min_parent_chars=100,
        max_parent_chars=1200,
    )
    headings = [m.get("section_heading") for _, m, _ in result]
    assert any(h for h in headings), "至少一个父块应有 section_heading"


def test_child_meta_has_section_heading():
    """子块 meta 中应包含 section_heading。"""
    result = chunk_text_hierarchical(
        MD_SMALL_SECTIONS,
        max_chars=300,
        overlap=30,
        min_parent_chars=50,
        max_parent_chars=600,
    )
    children = _all_children(result)
    if children:
        headings = [m.get("section_heading") for _, m in children]
        assert any(h for h in headings), "至少一个子块应有 section_heading"


def test_empty_text_returns_empty():
    result = chunk_text_hierarchical("", max_chars=500, overlap=50)
    assert result == []


# ── API 文档三级嵌套测试（接口文档.md 类型） ──────────────────────────────────────

MD_API_DOC = """\
### 1.登录注册和验证码发送

#### 1.1注册

##### 1.1.1基本信息

请求路径: `/register`
请求方式: `POST`
接口描述: 该接口用于用户注册

##### 1.1.2请求参数

参数格式: `application/json`

参数说明:

| 名称 | 类型 | 是否必须 | 备注 |
|------|------|----------|------|
| email | string | 是 | 邮箱 |
| verification | string | 是 | 验证码 |

##### 1.1.3响应数据

参数格式: `application/json`

参数说明:

| 参数名 | 类型 | 是否必须 | 备注 |
|--------|------|----------|------|
| code | number | 必须 | 响应码，1代表成功 |
| msg | string | 非必须 | 提示信息 |
| data | object | 非必须 | 返回数据 |

#### 1.2登录

##### 1.2.1基本信息

请求路径: `/login`
请求方式: `POST`
接口描述: 使用用户名/密码和验证码登录

##### 1.2.2请求参数

| 名称 | 类型 | 是否必须 |
|------|------|----------|
| username | string | 是 |
| password | string | 是 |
"""


def test_api_doc_groups_subsections_into_parent():
    """API 文档三级嵌套（###/####/#####）：所有 ##### 子节应合并进对应 #### 父块。

    期望：每个 #### 标题（1.1注册、1.2登录）各自成为一个父块，
    包含其下所有 ##### 子节内容；不会因某个 ##### > min_parent_chars 就断开。
    """
    result = chunk_text_hierarchical(
        MD_API_DOC,
        max_chars=720,
        overlap=90,
        min_parent_chars=200,
        max_parent_chars=1500,
    )
    # 提取有子块的父块
    parents_with_children = [(pc, pm, ch) for pc, pm, ch in result if ch]
    assert len(parents_with_children) >= 2, (
        f"期望 1.1注册 和 1.2登录 各自成为有子块的父块，实际父块数: {len(parents_with_children)}"
    )

    # 1.1注册 父块应包含 1.1.1 / 1.1.2 / 1.1.3 的内容
    reg_parent = next(
        (pc for pc, _, _ in parents_with_children if "1.1注册" in pc or "1.1.1" in pc),
        None,
    )
    assert reg_parent is not None, "未找到包含 1.1注册 内容的父块"
    assert "1.1.2请求参数" in reg_parent, f"父块应包含 1.1.2请求参数，实际内容: {reg_parent[:200]}"
    assert "1.1.3响应数据" in reg_parent, f"父块应包含 1.1.3响应数据，实际内容: {reg_parent[:200]}"

    # 1.2登录 父块应包含 1.2.1 / 1.2.2 的内容
    login_parent = next(
        (pc for pc, _, _ in parents_with_children if "1.2登录" in pc or "1.2.1" in pc),
        None,
    )
    assert login_parent is not None, "未找到包含 1.2登录 内容的父块"
    assert "1.2.2请求参数" in login_parent, f"父块应包含 1.2.2请求参数，实际内容: {login_parent[:200]}"


def test_api_doc_children_are_fine_grained():
    """子块应是比父块更细粒度的 ##### 小节。"""
    result = chunk_text_hierarchical(
        MD_API_DOC,
        max_chars=720,
        overlap=90,
        min_parent_chars=200,
        max_parent_chars=1500,
    )
    children = _all_children(result)
    # 应存在子块
    assert len(children) > 0, "应有检索子块"
    # 每个子块都比对应的父块小
    for pc, _, ch in result:
        for child_content, _ in ch:
            assert len(child_content) <= len(pc), (
                f"子块应 ≤ 父块大小：子块 {len(child_content)} 字符 > 父块 {len(pc)} 字符"
            )
