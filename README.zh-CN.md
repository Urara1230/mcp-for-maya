<!-- mcp-name: io.github.Xxx91n/mcp-for-maya -->
<!-- synced-with: README.md @ 8402e3b — 本文件为英文权威版的中文镜像：可滞后、不许分叉；英文版 commit 变更后需同步本文件 -->

[English](README.md) | **简体中文**

<p align="center">
  <img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/hero.svg" width="100%" alt="mcp-for-maya — Give AI agents eyes inside Autodesk Maya"/>
</p>

# mcp-for-maya

> 让 AI Agent 拥有 Maya 三维空间感知能力的 MCP 服务器

[![CI](https://github.com/Xxx91n/mcp-for-maya/actions/workflows/ci.yml/badge.svg)](https://github.com/Xxx91n/mcp-for-maya/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/mcp-for-maya?cacheSeconds=300)](https://pypi.org/project/mcp-for-maya/) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/Xxx91n/mcp-for-maya/blob/main/LICENSE) [![Python](https://img.shields.io/pypi/pyversions/mcp-for-maya)](https://pypi.org/project/mcp-for-maya/)

## 这是什么

`mcp-for-maya` 是一个 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 服务器，让大语言模型（Codex、Claude 等）直接操控 Autodesk Maya，进行三维建模、场景规划和工程级项目落地。

本项目 fork 自 [chadrik/maya-mcp-server](https://github.com/chadrik/maya-mcp-server)，在其连接层之上扩展了场景智能层：空间感知、工程审核、事务安全与视觉闭环。

**核心能力：** AI 不再是"闭眼写代码"，而是能随时感知 Maya 场景的空间状态、材质分布、物体关系，并基于工程规范进行确定性审核。

> [!WARNING]
> 本服务器把任意 Python 代码送进 Maya 执行——这是设计能力而非漏洞。内置的校验/限流/审计是**误操作与注入指令的安全网，不是抵御恶意客户端的边界**；接入的 Agent 是受信方。详见 [docs/threat-model.md](https://github.com/Xxx91n/mcp-for-maya/blob/main/docs/threat-model.md)。

<img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/section-live-demo.svg" width="100%" alt="Live Demo — captured by the product itself"/>

以下素材全部由本产品工具链真实捕获，非 mock：场景经 `execute_code` / `write_module` 程序化构建（另含一次真实 `asset_import`），帧画面经 `scene_render_preview` 渲染，audit 双帧经 `scene_review` + `scene_viewport_snapshot` 采集，环绕序列由 `playblast` 逐帧采集（Maya 2024 GUI 会话）。

| 指令 | 结果 |
|---|---|
| `write_module` → `t20_movement.build()`——"参数化渐开线钟表机芯：真渐开线齿形、日内瓦纹夹板、蓝钢螺丝、红宝石轴承、螺旋游丝" | <img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/showcase-movement.png" width="440" alt="参数化钟表机芯：渐开线齿轮、日内瓦纹、摆轮与游丝"/> |
| `write_module` → `t20_bonsai.build()`——"low-poly L-system 盆景：递归分枝、切面叶团、盆+苔+石" | <img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/showcase-bonsai.png" width="440" alt="low-poly L-system 盆景：递归分枝、叶片团、盆与苔石"/> |
| `asset_import("vintage_pocket_watch")` + `asset_import("metal_tool_chest")` → `t20_workbench.build()`——"钟表匠工作台：贴图接线的导入资产+程序化渐开线散件" | <img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/showcase-workbench.png" width="440" alt="钟表匠工作台：导入怀表与工具箱、程序化齿轮散件"/> |
| `write_module` → `t20_street.build()`——"low-poly 街角街区：11 栋参数化楼体、山墙屋顶、雨棚、街道家具、停放车辆" | <img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/showcase-street.png" width="440" alt="low-poly 街区：11 栋参数化楼体、雨棚、街道家具"/> |
| `write_module` → `t20_teapot.build()`——"Utah 茶壶复刻：旋转轮廓壶身、贝塞尔锥形壶嘴、耳形把手、棋盘地面" | <img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/showcase-teapot.png" width="440" alt="Utah 茶壶复刻：旋转壶身、贝塞尔锥形壶嘴、棋盘地面"/> |
| 在堆满乱件的台面上 `scene_review()` → `scene_plan(auto_fix=True)` + 清理 → 再 `scene_review()`——评分 **53.7 → 64.2** | <img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/audit-before.png" width="215" alt="scene_review 修复前：计量台面上散落默认名乱件"/><img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/audit-after.png" width="215" alt="scene_review 修复后：违规清除、台面复原"/> |

<p align="center"><img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/orbit.gif" width="100%" alt="机芯环绕 — 20 帧真实 playblast，120° 扫掠"/></p>

每一帧都可用 [`.github/assets-src/`](https://github.com/Xxx91n/mcp-for-maya/tree/main/.github/assets-src) 里的脚本复现。

## 与 blender-mcp 对比

对标 [ahujasid/mcp-for-blender](https://github.com/ahujasid/mcp-for-blender)（2026-09 实测），诚实三档：

| 档位 | 内容 |
|------|------|
| **本项目独有** | ICEV 强制工作流（写进服务端 instructions）、CoS 记号化输出、checkpoint/rollback 事务安全、scene_plan 场景规划（zone 语义+布局建议）、多会话管理、11 项确定性审核、带元数据的 playblast 渲染预览 |
| **blender-mcp 独有** | 资产生态链广度（Sketchfab/Hyper3D/Hunyuan3D）、一等对象 CRUD 工具面、AI 生成模型接入、社区规模 |
| **双方共有** | MCP 工具面、Poly Haven 资产源、视口截图回传、任意 Python 执行、本地 socket 连接 |

Poly Haven 模型搜索+导入已于 0.2.0 交付（issue #2 薄切片：FBX + 贴图接线；HDRI 与贴图包仍在路线图）。AI 生成与一等对象 CRUD 明确不做——后者 `execute_code` 已覆盖。

共有的资产下载链路在本仓是代码强制面而非惯例：下载仅发生在宿主侧——scheme+主机白名单、逐文件 md5、大小上限、文件名取 URL 末段并做字符白名单（`polyhaven.py`）——Maya 本体零网络面。执行细节见 [docs/threat-model.md](https://github.com/Xxx91n/mcp-for-maya/blob/main/docs/threat-model.md)。

<img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/section-capability-matrix.svg" width="100%" alt="Capability Matrix"/>

| 能力 | 工具 | 说明 |
|------|------|------|
| 🧊 **空间感知** | `scene_snapshot` `scene_inspect` `scene_measure` | 一次调用获取全场景空间模型，精确距离/重叠/间隙测量 |
| 🎨 **审美分析** | `scene_aesthetics` | 5 维分析：色彩理论(60-30-10)、空间构成(黄金比例/三分法)、比例尺度(人体工学)、光照质量(三点照明/填充比/色温/衰减)、视觉动线 |
| 🎬 **镜头规划** | `camera_create` `camera_orbit` | 8 种行业标准镜头 + 环绕动画 |
| 🛡️ **避灾回退** | `scene_checkpoint` `scene_rollback` `scene_checkpoint_list` | exportAll 内存态快照，回滚显式重绑原路径 |
| 🧠 **大局观统筹** | `scene_plan` | 组织健康检查、区域平衡、布局优化建议、冲突预防、自然语言规划 |
| 📋 **工程审核** | `scene_review` `scene_validate` `scene_assert` | 11 项确定性检查（0-100 分）+ 自定义约束验证 + 状态断言 |
| ⚡ **代码执行** | `execute_code` `write_module` | 在 Maya 中执行任意 Python 代码 / 注入可复用模块 |
| 👁️ **视觉闭环** | `scene_viewport_snapshot` `scene_render_preview` | 视口所见即所得捕获 + 单帧 playblast 预览（仅 GUI 会话） |
| 🔌 **会话管理** | `list_sessions` `add_session` `maya_setup_guide` | 多会话发现/接入 + 连接诊断/安装/回退引导 |
| 📦 **资产库** | `asset_search` `asset_import` | Poly Haven CC0 模型——宿主侧 HTTPS 下载（主机白名单、md5 校验、大小上限、platformdirs 缓存），Maya 侧 FBX 导入带贴图自动接线、面数/尺寸护栏、`GRP_asset_<id>` 去重 |
| 📤 **场景导出** | `scene_export` | FBX/OBJ/USD 导出——整场景或指定对象；格式按扩展名推断（冲突即报错不猜）、父目录自动创建、覆盖需显式 opt-in、导出后还原选择集 |
| 🔎 **场景图内省** | `scene_describe` `scene_nodes` | API 级节点自描述（精确类型、逐属性元数据 keyable/connectable/enum/取值范围、连接布线——响应有界，`limit` 最高放宽至 1000，截断经 `*_truncated` 披露）+ 有界枚举（含非 DAG 节点：材质/工具节点），`has_more`/`next_cursor` 诚实分页 |

共 25 个 MCP 工具。

<img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/section-quick-start.svg" width="100%" alt="Quick Start"/>

### 1. 安装

```bash
# PyPI 安装（推荐）
pip install mcp-for-maya

# 或 uvx 免安装直跑
uvx mcp-for-maya

# 备选：用 uv 直接从 git 安装
uv tool install git+https://github.com/Xxx91n/mcp-for-maya.git

# 或克隆源码安装
git clone https://github.com/Xxx91n/mcp-for-maya.git
cd mcp-for-maya
pip install -e .
```

> [!NOTE]
> **三层命名**：dist 名 `mcp-for-maya`（PyPI 货架名）→ 安装后 import 名为 `maya_mcp_server`（继承上游不改）；script 名 `mcp-for-maya`（旧名 `maya-mcp-server` 保留为兼容别名）。`uvx mcp-for-maya` 能命中正是因为命令名与包名一致。

### 2. 配置 Maya 连接

#### 方式一：自动配置（推荐）

启动 MCP 服务器后，AI Agent 会自动调用 `maya_setup_guide` 工具引导连接：

1. 确保 Maya 已启动
2. 在 Agent 中输入任意指令（如"查看 Maya 场景"）
3. 如果未连接，Agent 会自动运行诊断并可安装 `userSetup.py`（幂等标记块合并，写入前自动备份）
4. 重启 Maya 后，命令端口自动打开

#### 方式二：手动配置

在 Maya 的脚本编辑器中执行：

```python
import maya.cmds as cmds

cmds.commandPort(name=":7001", sourceType="python")
```

> **提示**: Script Editor 打开方式：Maya 菜单 → Windows → General Editors → Script Editor；确保语言选择器为 **Python**（不是 MEL）。

#### 方式三：永久自动连接

将以下内容保存为 `userSetup.py`，放入 Maya 的 scripts 目录：

| 平台 | 路径 |
|------|------|
| Windows | `%MAYA_APP_DIR%\<version>\scripts\` |
| Linux | `~/maya/<version>/scripts/` |
| macOS | `~/Library/Preferences/Autodesk/maya/<version>/scripts/` |

```python
import maya.cmds as cmds

cmds.evalDeferred('cmds.commandPort(name=":7001", sourceType="python")', lowestPriority=True)
```

#### 多实例并行

`commandPort` 是绑死 `host:port` 的单监听 socket——同端口第二个实例 bind 会失败，因此**每个 Maya 实例需要自己的端口**。典型拓扑：

| Maya 实例 | commandPort | 说明 |
|-----------|-------------|------|
| 实例 A | `:7001` python | 主工作实例 |
| 实例 B | `:7002` python | 第二实例 |
| 实例 A | `:7011` mel | MEL 端口（自动识别并豁免，不再反复刷错） |

在每个实例各自的脚本编辑器（或各自 `userSetup.py`）里执行 `cmds.commandPort(name=":<port>", sourceType="python")`。**自动扫描**是主路径——服务端周期枚举本机 Maya 监听端口并完成引导；扫不到时用 `add_session(host, port)` 手动兜底。若扫描误探本机其它 TCP 服务，可用 `MAYA_MCP_INCLUDE_PORTS=7001,7002`（逗号分隔，支持 `7005-7010` 区间）收敛探测范围，或用 `MAYA_MCP_EXCLUDE_PORTS=<port>` 排除特定端口。

注意：`commandPort` **不随会话持久**——Maya 重启即失效；持久化请写入 `userSetup.py`（见方式三）。

#### 故障排查

| 问题 | 解决方案 |
|------|----------|
| `list_sessions` 返回空 | 调用 `maya_setup_guide(action="diagnose")` |
| 端口被占用 | 关闭其他 Maya 实例，或换端口 |
| userSetup.py 不生效 | 确认文件在正确的 scripts 目录，重启 Maya |
| 防火墙拦截 | 确保 localhost:7001 可访问 |
| 第二个 Maya 实例不出现 | 每个实例需独立 `commandPort`（同端口 bind 冲突）；仍扫不到用 `add_session(host, port)` 手动添加 |
| Script Editor 反复刷语法错误 | 旧版探测 MEL 端口的症状：新版会自动豁免非 Python 端口；仍异常时用 `MAYA_MCP_EXCLUDE_PORTS` 排除该端口 |

### 3. 配置 MCP 客户端

Codex `~/.codex/config.toml`：

```toml
[mcp_servers.maya]
command = "uvx"
args = ["mcp-for-maya"]
tool_timeout_sec = 120
```

走 git 源则 `args = ["--from", "git+https://github.com/Xxx91n/mcp-for-maya.git", "mcp-for-maya"]`。源码安装改用 `command = "python"`、`args = ["-m", "maya_mcp_server"]`，并在 `env` 中把 `PYTHONPATH` 指到 `<repo>/src`。

### 4. 开始使用

在 Agent 中直接对话：

> "帮我看看 Maya 场景里有什么，然后在入口处创建一个展示架"

AI 会自动调用 `scene_snapshot()` → 理解场景 → 执行建模 → `scene_review()` 审核结果。

<img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/section-icev-workflow.svg" width="100%" alt="ICEV Workflow"/>

每次场景修改都遵循 **ICEV** 工作流（也内置为 Agent 流程卡，见 `skills/icev-workflow`）：

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ INSPECT  │ ──→ │ COMPUTE  │ ──→ │ EXECUTE  │ ──→ │ VERIFY   │
│ 场景快照  │     │ 计算规划  │     │ 执行修改  │     │ 审核验证  │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
```

1. **INSPECT**：`scene_snapshot()` 获取全场景空间数据
2. **COMPUTE**：基于空间数据计算位置、尺寸、间距
3. **EXECUTE**：`execute_code()` 执行 Maya Python 代码
4. **VERIFY**：`scene_assert()` + `scene_review()` 确认结果；GUI 会话可用视觉工具复核

## 工具详解

### 空间感知

```python
# 全场景快照（一次调用，返回所有物体的空间数据）
scene_snapshot(detail="compact", format="cos")

# 深度检查特定物体（含邻居分析）
scene_inspect(target="wall_entrance", include_neighbors=True)

# 精确测量（4 种模式）
scene_measure(obj_a="wall_north", obj_b="counter_A", mode="clearance")

# 验证场景状态
scene_assert(expectations='{"wall": {"exists": true, "position": [0,0,500]}}')
```

### 工程审核

```python
# 11 项确定性检查（返回 0-100 分）
scene_review()

# 空间约束验证
scene_validate(rules='[{"type": "min_clearance", "value": 180}]')
```

### 镜头规划

```python
# 创建镜头（8 种行业标准类型）
camera_create(target="product_display", shot_type="medium", azimuth=30, elevation=15)
# 类型：extreme_wide / wide / medium / close / extreme_close / bird_eye / low_angle / over_shoulder

# 环绕动画相机
camera_orbit(center=[0, 100, 0], radius=500, frames=120)
```

### 避灾回退

```python
# 保存检查点（快照=内存态 exportAll，不含 undo 历史）
scene_checkpoint(name="before_renovation")

# 列出所有检查点
scene_checkpoint_list()

# 回滚（先自动存安全快照，再把场景名重绑回原文件；回滚后请用 scene_snapshot 重建认知）
# 边界：快照自包含——references 默认展平不回写；假定单场景文件单会话
scene_rollback(filename="cp_before_renovation.ma")
```

### 资产（Poly Haven，宿主侧下载）

```python
asset_search(query="camera", asset_type="models", limit=20)  # Poly Haven 索引检索
asset_import(asset_id="Camera_01", resolution="1k")
# 经 HTTPS 下载到 platformdirs 缓存（逐文件 md5 校验 + sha256 审计），
# 然后 Maya 导入本地 FBX 并挂到 GRP_asset_<asset_id>（重复调用自动去重；
# force=True 强制重导）。贴图按文件名后缀自动接线
# （diff/rough/metal/nor_gl/ao/disp...），sRGB/Raw 色彩空间 + bump2d 法线。
# 单件资产使用裸贴图名（Diffuse/Rough/Metal...）——同样被识别并接到
# 该资产的唯一材质上。
# 护栏：默认 100k 面数上限（allow_high_polycount 可放宽）、尺寸合理性检查。
```

### 场景导出

```python
scene_export(path="D:/out/scene.fbx")  # 整场景导出；按 .fbx 扩展名推断格式
scene_export(path="/tmp/kit", format="obj")  # 缺扩展名按显式 format 追加 -> kit.obj
scene_export(path="D:/out/kit.usd", objects=["GEO_box"])  # exportSelected；导出后还原原选择集
# format ∈ {fbx,obj,usd}：扩展名与显式 format 冲突即报错，绝不猜。
# 目标已存在时拒绝，除非 overwrite=True。prompt=False 强制
# （模态对话框会挂死命令通道）；不触碰场景 modified 脏标志。
# 不支持 Alembic——AbcExport 不是 cmds.file 面。
```

### 视觉闭环（仅 GUI 会话）

```python
# 视口所见即所得截图（含 HUD/选中高亮——验证用户正看到什么）
scene_viewport_snapshot(max_size=800, format="jpeg")

# 单帧 playblast 预览（干净无 HUD；可指定相机，width/height 服务端向上取整 /4）
scene_render_preview(camera="CAM_hero", width=640, height=360)

# 两工具返回 [图片, JSON 元数据]；headless 会话返回 gui_session_required 错误
# wireframe/线稿审查建议 format="png"；实际尺寸以返回元数据为准
```

## CoS 符号化格式

默认输出使用 **Chain-of-Symbol** 记号化格式压缩场景数据。该格式论文报告在其演示场景上较 JSON 节省约 65% token（arXiv:2305.10276，-65.8%）——本项目实现该记号，此数字为论文测量值而非本项目基准测试。

```
SCENE[164obj, 5zones] UNIT=cm UP=y
shell (23obj) @(-11.8,178.8,145.7)
  GRP_floor[mesh]@(0,0,0) 1121.5x20x1530.5
  pasted__arch_wall[mesh]@(0,0,0) 100x300x10
entrance (6obj) @(157.3,162.6,-111.6)
  GRP_workshopFront[group]@(1162,-17,103) 227.4x200.9x193.3
```

## Agent Skills

仓库内置 2 张 **Experimental** 流程卡（`skills/` 目录）：

| Skill | 用途 |
|-------|------|
| `skills/icev-workflow` | ICEV 修改纪律：任何场景变更必走 Inspect→Compute→Execute→Verify |
| `skills/scene-review-playbook` | 审核手册：11 项检查的分值解读与 findings→actions 映射 |

> 两卡仅在 Claude Code 上评测过，未在 Codex/Gemini CLI/Cursor 验证；跨模型评测计划见 issue #3。

<img src="https://raw.githubusercontent.com/Xxx91n/mcp-for-maya/main/.github/assets/section-audit-trust.svg" width="100%" alt="Audit & Trust"/>

`scene_review()` 提供 11 项通用检查（0-100 分，按各项分值归一化）：

| 检查 | 分值 | 检查内容 |
|------|------|----------|
| spatial | 10 | 物体/相机/灯光计数 |
| overlaps | 10 | BBox 碰撞检测（排除父子） |
| conflicts | 10 | 空间穿透检测 |
| zones | 5 | 命名规则区域覆盖 |
| naming | 5 | 生产命名规范 |
| components | 10 | GRP_ 分组 + 嵌套深度 ≤4 |
| orphans | 5 | 空组/默认名检测 |
| aesthetics | 15 | 5 维审美（色彩/构成/比例/光照/动线） |
| lighting | 10 | 三点照明/填充比/衰减 |
| organization | 10 | 层级组织健康度 |
| constraints | 5 | 自定义约束违反 |

## 信任与隐私

- **零遥测**：zero telemetry, no phone-home——本项目不含任何遥测或主动外发上报代码，可源码核实。唯一外发流量是两个资产工具：仅在调用时经 HTTPS 访问 `api.polyhaven.com` / `dl.polyhaven.org|.com`（`polyhaven.py` 内主机白名单 + md5 校验 + 大小上限）。
- **本地单用户**：命令端口仅绑定 localhost；接入的 MCP client 是受信方。
- **安全网**：统一管线（`pipeline.py` + `security.py`）对全部 25 个工具做参数校验 + token-bucket 限流（读取类 ~100 次/60s、变更类 ~20 次/60s，按会话）+ pattern 扫描（默认 warn-only）+ 独立 JSONL 审计日志（覆盖全部 25 个工具）。它防误操作，不防恶意 client——完整模型见 [docs/threat-model.md](https://github.com/Xxx91n/mcp-for-maya/blob/main/docs/threat-model.md)。
- **事务安全**：`scene_checkpoint`/`scene_rollback` 提供内存态快照与显式回滚（快照不含 undo 历史，references 默认展平）。
- **严格策略模式（可选）**：设置 `MAYA_MCP_DISABLE_EXECUTE=1`、`MAYA_MCP_DISABLE_WRITE_MODULE=1` 或 `MAYA_MCP_DISABLE_ARBITRARY=1`（三者全禁），即可把逃生舱工具从 `tools/list` 中移除，并以 `[policy_disabled]` 拒绝调用；只读工具不受影响。它们是**误用护栏，不是恶意 agent 边界**——仍持有 `execute_code` 的 agent 本就在信任边界之内；它们买来的是 demo、误操作或 CI 沙箱场景下更小的爆炸半径。理由见 [docs/threat-model.md](https://github.com/Xxx91n/mcp-for-maya/blob/main/docs/threat-model.md)，逐码对照见 [docs/guide/error-codes.md](https://github.com/Xxx91n/mcp-for-maya/blob/main/docs/guide/error-codes.md)。
- 漏洞报告渠道见 [SECURITY.md](https://github.com/Xxx91n/mcp-for-maya/blob/main/SECURITY.md)。

## 版本策略

遵循 [Semantic Versioning](https://semver.org/)：

- **0.x（至 0.3.x，Alpha）**：工具面仍可能调整；minor bump 承载新功能，不承诺兼容冻结。
- **Beta（0.4.0）**：feature-complete 层级——自此开始外部测试（classifier `4 - Beta`）。
- **1.0.0**：公共 API 冻结——工具面与输出 schema 按 semver 保持稳定；breaking 变更须升 2.0.0。与 `5 - Production/Stable` classifier 同一提交晋升；由 [#7 真机验证清单](https://github.com/Xxx91n/mcp-for-maya/issues/7)把守（全绿方可 tag）。

**公共 API** 即 MCP 工具面：工具名、输入/输出形状、双层错误契约（宿主侧 `isError` 故障 vs `{error:{code,message,suggestion}}` 域结果）与工具 annotations 语义（见 [docs/threat-model.md §5](https://github.com/Xxx91n/mcp-for-maya/blob/main/docs/threat-model.md)）。additive 变更（新工具、新可选响应字段）随 minor 发布；破坏该面的变更须 major 升档。1.0.0 是对该面的冻结承诺而非质量认证——剩余真机验证面在 #7 显式跟踪，不默示抹去。

发布节奏为里程碑驱动，不承诺固定周期。路线图见 GitHub issues：#2 Poly Haven 集成（模型切片已随 0.2.0 交付并收口；scene_plan 推荐集成残余拆分为 #31）、#3 Skills 正式立项（v1.x）、#4 安全与权限模型（v1.x）、#5 场景导出与内省（scene_export 已随 0.3.0 交付：FBX/OBJ/USD；scene_describe/scene_nodes 内省同随 0.3.0 交付）、#6 更多资产源（exploratory）、#7 真机验证清单与 v1.0 反馈（pinned）。

## 环境要求

| 环境 | 要求 |
|------|------|
| 宿主（运行 MCP server） | Python **≥3.10**（`requires-python`）；Windows / Linux / macOS |
| Maya 侧注入（helper） | Maya **≥2023**（自带 Python ≥3.9，依赖 `ast.unparse`；低于此版本注入时会收到明确报错） |
| 实证版本 | Maya **2024** GUI |

视觉闭环两个工具需要 **GUI 会话**（headless/mayapy 返回结构化能力错误）。首次截图时服务端会跑一次 per-session VP2 回读方向探针（一次性搭景即拆，净零副作用）；个别驱动误报时可用 `MAYA_MCP_VP2_BOTTOM_UP=0|1` 强制指定。

## 开发

```bash
uv sync --frozen                  # 从 uv.lock 装锁定开发环境（备选 pip install -e . --group dev，需 pip>=25.1）
python -m pytest tests/ -q       # 测试
ruff check src tests             # lint
mypy src                         # 类型检查
python -m maya_mcp_server -vv    # DEBUG 日志运行（-v=INFO, -vv=DEBUG）
```

贡献流程见 [CONTRIBUTING.md](https://github.com/Xxx91n/mcp-for-maya/blob/main/CONTRIBUTING.md)；变更记录见 [CHANGELOG.md](https://github.com/Xxx91n/mcp-for-maya/blob/main/CHANGELOG.md)。

## 许可与商用

本项目以 [MIT 许可证](LICENSE) 发布，**允许商业使用**，包括在公司内部使用、修改、集成到商业产品中以及再分发，无需支付费用，也无需另行取得授权。唯一的条件是：在分发本软件或其衍生作品时，保留 `LICENSE` 中的版权声明和许可文本。

本仓库基于 [Xxx91n/mcp-for-maya](https://github.com/Xxx91n/mcp-for-maya)（上游为 [chadrik/maya-mcp-server](https://github.com/chadrik/maya-mcp-server)）。原作者的版权声明已完整保留；本仓库与原作者无隶属或背书关系。

- 本软件按"原样"提供，不附带任何担保（见 LICENSE）。
- 使用本软件需要合法授权的 Autodesk Maya；Maya 本身能否商用由 Autodesk 的许可条款决定（教育版/学生版不可商用）。
- 通过 `asset_import` 获取的 Poly Haven 素材为 CC0，可自由商用。

## 致谢

Fork 自 [chadrik/maya-mcp-server](https://github.com/chadrik/maya-mcp-server)——保留其 MIT 版权声明（见 LICENSE），在其连接层之上扩展场景智能层。

上游 open issue 在本仓的处置与修复版本对照见 [docs/upstream-issue-status.md](https://github.com/Xxx91n/mcp-for-maya/blob/main/docs/upstream-issue-status.md)。
