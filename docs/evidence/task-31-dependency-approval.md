# Task 31 依赖审批材料（pypdf / defusedxml）

**状态：待批准（PENDING_APPROVAL）。需要知识负责人与仓库负责人共同批准。**
**背景**：Task 31（文档解析/知识库摄取）引入两个第三方依赖，按仓库依赖策略需审批后才能进入锁文件。官方来源已由实现会话核录于 `task-31-progress.md`。

## 一、依赖清单

| 依赖 | 版本（锁定） | 用途 | 官方来源 |
|---|---|---|---|
| `pypdf` | `6.21.2` | PDF 文本提取（知识库摄取主路径） | [PyPI 发布页](https://pypi.org/project/pypdf/6.21.2/)（含安全公告链接） |
| `defusedxml` | `0.7.1` | XML 安全解析（防 XXE 注入，配合文档解析） | [PyPI 页](https://pypi.org/project/defusedxml/0.7.1/) · [上游 releases](https://github.com/tiran/defusedxml/releases) |

## 二、安全与供应链要点（供审批依据）

- **pypdf**：纯 Python 实现（无原生二进制供应链面）；PyPI 页面维护安全公告。已知风险类别与其历史 CVE 集中在恶意构造的 PDF 触发资源耗尽/解析异常——`defusedxml` 的引入正是对 XML 类输入的同类加固。
- **defusedxml**：安全社区标准库（Tiran/Kirsten 维护），专为禁用 XML 实体扩展等攻击面设计，是被广泛接受的防御性依赖。
- 两者均为按版本精确锁定的传递依赖最小集合；`uv lock --check` 已通过（实现会话记录）。

## 三、需要负责人决定的事项

1. **仓库负责人**：批准 `pypdf==6.21.2`、`defusedxml==0.7.1` 进入锁文件与安装包（PyInstaller spec 的依赖收集随之生效）。
2. **知识负责人**：确认知识库摄取管道解析用户上传的 PDF/XML 文档属预期用途，且解析在 Sidecar 沙箱内进行。
3. 确认后续升级策略：跟随上游安全公告升级，重大版本变更需重新审批。

**批准方式**：对上述三项明确批准或提出条件；确认后由记录者标注批准人与日期。
