# Maya MCP Server — v1.0.0 真机验证窗执行手册 (Runbook)

> **文档标识**：docs/reference/real-maya-runbook.md（原工作副本 `.scratch/t32/runbook.md`，rev 1.1 返工修订版；`.scratch/` 已不入仓）
> **修订日期**：2026-09-29
> **审计返工依据**：2026-09-29 T-32a 独立审计报告（纠正 D-146 渠道与单次核销语义、跑序上下游依赖、框2 版本矩阵拆行、Scope 固定行 build/stdio 补齐、GUI 前置脏场景守卫、AC-01 逃生通道恢复 live GUI 降级退路、度量口径订正）
> **权威决策依据**：退役决策账本 D-148（R32 主题面裁决）、D-149（真机验证窗执行契约）、D-150（发版序与前向立法）、D-109（凭证入仓纪律）、D-131（用户末行裁决权）、D-049（环境披露档）、D-145β / D-146（发布收录与核销）、GLOSSARY.md（脏场景守卫）；账本原文恢复：`git show '1c5befe^:docs/decision-ledger.md'`
> **性质声明**：本件原为操作类非耐久运行手册（原留存 `.scratch/`），现作为历史归档收入 `docs/reference/`；窗收尾时的 AC 卡骨架与逐框结果表作为正式裁决产物随成果合入 `docs/evidence/`。

---

## 一、Readiness 节（前置就绪与客户端预检）

在开启 Maya 真机窗前，必须核验以下工具链与环境前置：

### 1.1 宿主与运行时基线
- **操作系统**：Windows 11 x64 (Host)
- **Maya 安装**：Autodesk Maya 2024 已就绪于 `D:\maya2024\Maya2024\bin\maya.exe`（Build: 202302170737 / 24.0.0.4640，mayapy Python 3.10.8）
  - *注记 (D-149②)*：本机未安装 Maya 2025/2026；版本矩阵严格按 D-049 披露档与条件判位分列执行，禁合并勾选。
- **端口准备**：
  - `:7001`：Maya native commandPort (Python sourceType)
  - `:7002`：Maya Qt framed channel (PySide2 server)
  - 端口状态核验命令：`netstat -ano | findstr "7001 7002"`

### 1.2 客户端工具预检（框5 专项依赖）
- [ ] **MCP Inspector**：`npx @modelcontextprotocol/inspector --version` 可用
- [ ] **Claude Code CLI**：`claude --version` 可用
- [ ] **Codex / Antigravity IDE**：会话支持 FastMCP stdio 通道连接与多 Block 图像/文本渲染

---

## 二、三车道执行架构 (Three-Lane Architecture)

真机验证窗采用**三车道一窗**模式（D-149①），避免多子窗调度碎片化与人工负担：

```
┌────────────────────────────────────────────────────────────────────────┐
│                        单一真机验证窗 (Single Window)                   │
├────────────────────┬────────────────────┬──────────────────────────────┤
│    1. mayapy 道    │    2. 桌面道       │         3. GUI 道            │
│   (mayapy Lane)    │  (Desktop Lane)    │        (GUI Lane)            │
├────────────────────┼────────────────────┼──────────────────────────────┤
│ 框1: Tier-2 suite  │ 框7: cmds 签名审计 │ 框2: GUI Tier-3 (视口/相机)  │
│ 框4: headless 注入 │ 框8: 回灌 diff     │ 框3: PySide2 framed Qt 通道  │
│ 框8: 签名采集      │      (下游比对)    │ 框5: MCP Inspector+客户端    │
│ 框10: commandPort  │                    │ 框6: Windows Qt RST/FIN      │
│      (standalone)  │                    │ 框9: modelPanel 相机切换     │
└────────────────────┴────────────────────┴──────────────────────────────┘
```

1. **mayapy 道 (mayapy Lane)**：通过 `mayapy.exe` 批处理与 `maya.standalone` 运行，负责基线 suite 回归与动态签名采集。
2. **桌面道 (Desktop Lane)**：仅依赖官方文档与仓内静态代码，承接框7 静态扫描与框8 下游 diff 比对，不占 Maya 运行进程。
3. **GUI 道 (GUI Lane)**：必须在真实的 Maya 2024 图形界面运行环境下验证视口渲染、Qt 事件循环与双通道通信。

---

## 三、标准执行跑序 (Standard Execution Sequencing)

执行顺序严格遵循 **观测先行、依赖闭合、Andon 保护**（D-148①、D-149① 修正版）：

```mermaid
flowchart TD
    S0[Step 0: 环境快照采录] --> S1[Step 1: 观测窗只读先行 (10-05双金丝雀)]
    S1 --> S2[Step 2: D-115 存量 probe 工件补采]
    S2 --> S3[Step 3: mayapy 道实跑 (框1, 4, 8动态签名采集, 10)]
    S3 --> S4[Step 4: 桌面道静态审计与下游比对 (框7 + 框8下游 diff)]
    S4 --> S5[Step 5: GUI 前置脏场景守卫检查]
    S5 --> S6[Step 6: GUI 道真机验证 (框2, 3, 5, 6, 9)]
    S6 --> S7[Step 7: Andon 收尾与裁决入仓]
```

1. **Step 0 — 环境快照**：记录 OS、Maya build（202302170737 / 24.0.0.4640）、GPU 驱动、Python/mayapy 详细版本。
2. **Step 1 — 观测窗只读先行**：执行 2026-10-05 双金丝雀观测（D-127/D-137）与 drift notify 状态检查。**观测步必先于变更步，严禁混装**。
3. **Step 2 — D-115 补采**：将 `.scratch/` 中滞留的 live-probe（如 shader_probe、mcp_stdio_probe）按 D-109 规范核验元数据后归档。
4. **Step 3 — mayapy 道实跑 (先行)**：运行 headless 注入回退、独立 commandPort 探针，并**优先完成框8 mayapy 动态签名采集**，产出实际签名数据。
5. **Step 4 — 桌面道执行 (后置比对)**：提取 `visual_module.py` 与 `maya_scene_module.py` 中的 `cmds.*` 调用完成静态签名审计（框7）；利用 Step 3 采集的签名实物与 `tests/maya_stub/` 进行下游 diff 回灌对比（框8 下游）。
6. **Step 5 — GUI 前置脏场景守卫 (Dirty-Scene Guard)**：
   - 依赖规范：GLOSSARY.md 的「脏场景守卫」词条 (D-067①)。
   - 探针动作：进入 GUI 交互前，第一步必须执行只读探测：
     `cmds.file(q=True, modified=True)` 与 `cmds.file(q=True, sceneName=True)`。
   - 守卫准则：**若场景处于 modified 脏状态，绝对禁止强制执行 `file(new=True, force=True)`**，必须立即停手呈报用户三选一（保存/放弃/中止）；仅当场景完全干净时方可重置新场景。
7. **Step 6 — GUI 道真机验证**：在守卫通过后，顺序执行视口、Qt 通道、多客户端渲染、WinError 64 探针与相机切换。
8. **Step 7 — Andon 收尾**：汇总十框结果；若遇红灯触发现场保护与失败三选一流程。

---

## 四、逐框 AC 卡 (Per-Box Acceptance Criteria Cards)

> **结构规范**：按框编号（框1..框10），车道归入 `Scope` 字段；每卡包含 Scope 固定行（Maya 版本/Build/通道/端口态/车道）、Preconditions、Action、Expected Readback、Artifact Destination、Escape Hatch 六要素。

---

### [AC-01] mayapy Tier-2 suite 回归与引用编辑验证

- **Scope**：
  - 车道：mayapy 道
  - Maya 版本：Maya 2024
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：Headless standalone (mayapy 3.10.8)
  - 端口状态：N/A (进程内执行)
- **Preconditions**：`D:\maya2024\Maya2024\bin\mayapy.exe` 可执行，repo 根目录处于就绪状态。
- **Action**：
  ```bash
  D:\maya2024\Maya2024\bin\mayapy.exe -m pytest tests/test_mayapy_smoke.py -m mayapy -v
  ```
- **Expected Readback**：
  - checkpoint / rollback 包含 reference-edit 用例全部 PASS。
  - 控制台输出明确包含 `test_checkpoint_rollback_with_reference PASSED` 信号，退出码 0。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box01-mayapy-suite.json`
  - `meta`: `{ "env": "mayapy 3.10.8 (Maya 2024, Build 202302170737)", "box": 1, "claim_boundary": "Proves checkpoint/rollback and reference-edit integrity under mayapy." }`
- **Escape Hatch (合法降级退路，D-138⑦)**：
  - 依据 `docs/testing.md:53-60` 与退役账本 D-138⑦（恢复：`git show '1c5befe^:docs/decision-ledger.md'`）：若宿主 mayapy 环境存在缺陷导致 `maya.standalone` 报 `0xC0000005` 崩溃（Windows access violation），**明确允许合法降级至 live GUI 会话通道（`pytest -m gui`）完成 Tier-2 语义验证**。记录真实探针结果并附 crash 日志，不强行阻断。

---

### [AC-02] GUI Tier-3 视口快照、渲染预览与版本矩阵

- **Scope**：
  - 车道：GUI 道
  - Maya 版本：Maya 2024 主测 (Maya 2025/2026 条件性披露)
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：Native commandPort / Framed Qt
  - 端口状态：7001 (commandPort), 7002 (Qt channel)
- **Preconditions**：
  - Maya 2024 GUI 打开，已加载默认视口（modelPanel4）。
  - **强制通过脏场景守卫**：`cmds.file(q=True, modified=True) == False`。
- **Action**：
  1. 调用 `viewport_snapshot` 工具抓取当前活动视口。
  2. 调用 `render_preview` 工具对测试几何体进行相机存复测试。
  3. 核验图像垂直翻转（`verticalFlip`）方向性。
  4. 检查 `modelPanel -camera` 参数在 2024 视口切换时的响应。
- **Expected Readback**：
  - 产出有效 PNG 图像（非纯黑、非全透明、尺寸非 0）。
  - `scene_assert` 读回当前视口相机在预览前后位姿一致（存复无漂移）。
- **版本矩阵声明规范 (D-149②)**：
  - **Maya 2024**：实测通过后在结果表打勾 (`[x]`)。
  - **Maya 2025 / 2026**：因本机环境未安装，**表格显式分列记录为 `not verified on this machine`（非勾选状态，不设选择框）**，进入 D-049 披露档并保留获得对应宿主后的条件性判位。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box02-gui-viewport.json`
  - 快照对比样本：`docs/evidence/probes/box02-viewport-sample.png`
  - `meta`: `{ "env": "Maya 2024 GUI Build 202302170737 PID... :7001", "box": 2, "claim_boundary": "Proves viewport grab and camera state restoration on Maya 2024. 2025/2026 unverified." }`
- **Escape Hatch**：若 OpenGL/DirectX 视口硬件加速受限导致全黑，切换 Maya 视口驱动为 VP2 (DirectX11/OpenGL Core) 重测。

---

### [AC-03] PySide2 分帧 Qt 通道实会话兼容性

- **Scope**：
  - 车道：GUI 道
  - Maya 版本：Maya 2024 GUI (PySide2 内置环境)
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：Qt Framed Codec Channel
  - 端口状态：7002 (LISTEN)
- **Preconditions**：Maya 2024 已启动且 `visual_module` / Qt framed listener 已正常注入监听。
- **Action**：
  - 通过 `ClientChannel` 发送超长代码块（>15,000 字符，例如注入完整的 `maya_scene_module.py`）。
  - 执行多次交替的 `execute_code` 请求，验证 16 MiB 分帧封包与解包。
- **Expected Readback**：
  - 客户端正常接收 JSON 响应，无长连接截断（EOF）或封包解析失败。
  - 服务端日志与 JSONL 审计记录无解码错。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box03-qt-channel.json`
  - `meta`: `{ "env": "Maya 2024 GUI PySide2 Build 202302170737 :7002", "box": 3, "claim_boundary": "Proves framed Qt channel transports >15KB payloads without truncation on PySide2." }`
- **Escape Hatch**：若 Qt 事件循环死锁，检查 `QtCore.QCoreApplication.processEvents()` 是否被长任务占满，降级为 native 临时文件通道对比。

---

### [AC-04] 真 mayapy 下 headless 临时文件注入回退

- **Scope**：
  - 车道：mayapy 道
  - Maya 版本：Maya 2024 mayapy (无 GUI)
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：Native commandPort (无 Qt channel)
  - 端口状态：7001 (LISTEN)
- **Preconditions**：通过 mayapy 启动独立 commandPort 会话，禁用 Qt 通道。
- **Action**：
  - 客户端配置 `client_type="native"`，调用 `write_module` 注入大型模块源码（超长阈值）。
  - 验证回退逻辑自动生成系统临时 `.py` 文件并执行 `import`。
- **Expected Readback**：
  - 注入成功，返回 `{"status": "ok", "module": "_mcp_scene"}`。
  - 验证模块在 Maya 内可调用且临时文件在完成或异常时被干净清理。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box04-headless-fallback.json`
  - `meta`: `{ "env": "mayapy 3.10.8 headless Build 202302170737 :7001", "box": 4, "claim_boundary": "Proves temp-file injection fallback activates and succeeds when Qt channel is unavailable." }`
- **Escape Hatch**：若 Windows 权限拦截 temp 目录写入，配置 `MAYA_MCP_TEMP_DIR` 指定本地可写沙箱目录。

---

### [AC-05] MCP Inspector + Claude Code + Codex 客户端渲染验真

- **Scope**：
  - 车道：GUI 道
  - Maya 版本：Maya 2024 GUI
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：FastMCP stdio (Host 端) + Native/Qt 通道 (Maya 端)
  - 端口状态：N/A (客户端与 Server 通过 stdio 通信，不占额外 TCP 端口)
- **Preconditions**：Maya GUI 运行中，Maya MCP Server 通过 stdio 启动并连接各客户端。
- **Action**：
  - 分别在 MCP Inspector、Claude Code、Codex 中调用 `viewport_snapshot` 与 `render_preview`。
- **Expected Readback**：
  - 各客户端界面中均能正常解析 MCP ToolResult 中的 Multi-block 内容（同时显示文本描述与嵌入图像预览）。
  - 无 JSON 序列化崩溃，无 Base64 编码破损。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box05-client-multiblock.json`
  - 截图证物：`docs/evidence/probes/box05-inspector-render.png`、`box05-claude-render.png`
  - `meta`: `{ "env": "Host Windows 11 + Inspector/Claude/Codex", "box": 5, "claim_boundary": "Verifies multi-block text/image MCP payloads render across all 3 client frontends." }`
- **Escape Hatch**：若某一第三方 CLI 不支持图片渲染（如纯文本终端），记录为客户端支持限制，只要 MCP Inspector 和 Codex 原生渲染正常即不阻断。

---

### [AC-06] Windows Qt 通道 RST/FIN 语义实测 (WinError 64)

- **Scope**：
  - 车道：GUI 道 (Windows 面)
  - Maya 版本：Maya 2024 GUI
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：Qt Framed Channel
  - 端口状态：7002 (LISTEN)
- **Preconditions**：Qt channel 保持连接并处理通信。
- **Action**：
  - 模拟客户端异常断开（突发 kill socket / RST 封包）以及正常单向 FIN 关闭。
  - 触发 `test_qt_channel` 中针对 `WinError 64` (指定的网络名不再可用) 的防护用例。
- **Expected Readback**：
  - 服务端平稳捕获 socket 异常，清理会话句柄，不导致 Maya 主进程崩溃或 GUI 挂起。
  - 异常被安全转为连接断开日志。
- **分拆与登记债条款 (D-149①)**：
  - Windows 面直接在真机窗完成实测。
  - **Linux CI 复核面**：另立登记债，由 GitHub Actions weekly CI 矩阵承接复核，不占用本真机人工窗。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box06-win-rst-fin.json`
  - `meta`: `{ "env": "Windows 11 + Maya 2024 GUI Qt Build 202302170737 :7002", "box": 6, "claim_boundary": "Proves WinError 64 / RST handling preserves Maya stability. Linux surface deferred to CI." }`
- **Escape Hatch**：若出现未捕获的 SocketError 警告，补全 `QT_SOCKET_DISCONNECT_ERRORS` 异常捕获元组。

---

### [AC-07] cmds.* 静态签名审计对齐官方文档

- **Scope**：
  - 车道：桌面道 (Desktop Lane)
  - Maya 版本：Maya 2024 (官方参考基准)
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：N/A (静态分析)
  - 端口状态：N/A
- **Preconditions**：本地源码 + Autodesk Maya 2024 官方 Python 命令文档可读。
- **Action**：
  - 提取 `src/maya_mcp_server/visual_module.py`（约 10 处 cmds 调用）及 `maya_scene_module.py` 全 surface 中的 `cmds.*` 调用。
  - 对比官方文档中参数名、参数类型、query/edit 标志位。
- **Expected Readback**：
  - 产出形式化审计报告，记录 0 个非法 flag 调用，0 个弃用未兼容语法。
  - 历史修复点（如 lookThru 替换为 modelPanel camera 切换）状态闭环。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box07-cmds-audit.json`
  - `meta`: `{ "env": "Static AST audit vs Maya 2024 documentation", "box": 7, "claim_boundary": "Verifies static signature compatibility for all cmds.* invocations across injected modules." }`
- **Escape Hatch**：发现存在歧义的未文档化 flag 时，在 AC-08 中增加动态查询断言。

---

### [AC-08] mayapy 签名采集回灌 stub 契约护栏

- **Scope**：
  - 车道：mayapy 道 (采集) + 桌面道 (比对)
  - Maya 版本：Maya 2024 mayapy
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：Subprocess 批处理
  - 端口状态：N/A
- **Preconditions**：`D:\maya2024\Maya2024\bin\mayapy.exe` 可执行。
- **Action**：
  - Step 3 先行：使用 mayapy 提取真实 `maya.cmds` 中相关函数的参数签名与 docstring。
  - Step 4 后置：对比 `tests/maya_stub/` 中的 fake cmds 实现，生成 diff 报告。
  - 确认 stub 契约未落后于真实宿主。
- **Expected Readback**：
  - 回灌 diff 产出并在 allowlist 范围内收敛，无新增未记录的假方法或假签名。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box08-signature-diff.json`
  - `meta`: `{ "env": "mayapy 3.10.8 -> tests/maya_stub", "box": 8, "claim_boundary": "Validates stub contract fidelity against real Maya 2024 command signatures." }`
- **Escape Hatch**：发现真实 Maya 返回非标准类型时，在 stub 中追加对应分支并附 ADR 注记。

---

### [AC-09] modelPanel 相机切换 call-form 冒烟

- **Scope**：
  - 车道：GUI 道
  - Maya 版本：Maya 2024 GUI (含视口 UI)
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：Native/Qt 通道
  - 端口状态：7001 / 7002
- **Preconditions**：
  - Maya GUI 存在多面板视口。
  - **强制通过脏场景守卫**：`cmds.file(q=True, modified=True) == False`。
- **Action**：
  - 针对 D-039 替代 `lookThru` 的调用形式进行调用：
    `cmds.modelPanel(panel, edit=True, camera=cam_transform)`
  - 验证多相机间交替切换并触发 UI 刷新。
- **Expected Readback**：
  - 调用返回成功，当前活动的视口确实与目标相机绑定，无运行时 Warning 或报错。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box09-modelpanel-camera.json`
  - `meta`: `{ "env": "Maya 2024 GUI Build 202302170737 modelPanel4", "box": 9, "claim_boundary": "Verifies modelPanel(camera=...) call form functions correctly in live Maya UI." }`
- **Escape Hatch**：若指定 panel 不存在，增加 `cmds.getPanel(type='modelPanel')` 动态回退逻辑。

---

### [AC-10] 无尾换行命令与 500ms 读取真实 commandPort 语义验证

- **Scope**：
  - 车道：mayapy 道 (standalone 实测) 或 GUI 道 (升级退路)
  - Maya 版本：Maya 2024
  - Build：202302170737 (24.0.0.4640)
  - 通道类型：Native commandPort
  - 端口状态：7001 (LISTEN)
- **Preconditions**：commandPort 在 `:7001` 开启。
- **Action**：
  - 向 commandPort 发送**不带末尾换行符 `\n`** 的命令字节。
  - 保持连接 500ms 读取缓冲区，观察服务端是否处理或阻塞等待终止符。
  - 紧接着发送带 `\n` 的常规命令，观察队列是否受损。
- **Expected Readback**：
  - 确定性记录 2024 版本下 commandPort 对缺少换行符的语义行为（确认或否认 Qt 内部重写后的换行终结符契约，D-041）。
- **Escape Hatch / 升级路径 (D-149①)**：
  - 默认于 `maya.standalone` 中完成实证；若 standalone 与 GUI 态表现存疑（或 standalone 环境损坏），立即**升级至 GUI 道**进行交叉比对。
- **Artifact Destination**：
  - `docs/evidence/probes/probe-box10-commandport-newline.json`
  - `meta`: `{ "env": "Maya 2024 commandPort Build 202302170737 :7001 (standalone/GUI cross-checked)", "box": 10, "claim_boundary": "Proves newline termination requirements on Maya 2024 commandPort." }`

---

## 五、失败框处置与裁决机制 (Failure Adjudication & Triage)

当真机窗执行中任意 AC 卡未达预期时，严格依照 **D-131 用户最终裁决权** 采用三选一裁决：

```
┌─────────────────────────────────────────────────────────────────┐
│                    AC 卡执行未通过 (Red Light)                  │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                 ┌───────────────┼───────────────┐
                 ▼               ▼               ▼
          【Approved】     【Conditional】    【Rejected】
           (豁免/接受)     (条件性带责通过)      (坚决否决)
                 │               │               │
                 ▼               ▼               ▼
           进披露附录      定 Owner + 期限    阻断 v1.0.0 发版
         (D-049/D-142α)   (开专项 Issue 跟踪)   (现场保护排查)
```

1. **Approved (豁免/接受)**：
   - 适用于第三方/上游宿主固有行为（如 Windows 平台特有 socket 错误码、某特定命令不支持某些边界输入）。
   - 处置：将已知现象与约束列入 D-049 披露档与 Release 附录（D-142α），不阻断 v1.0.0 航程。
2. **Conditional (条件性通过)**：
   - 适用于局部边缘功能（如某辅助诊断工具在特定布局下告警），且不危及数据完整性与主流程。
   - 处置：必须明确指定 **Owner** 与 **修复期限**（如 v1.1.0 前），立独立 Issue 挂账。
3. **Rejected (否决)**：
   - 凡涉及场景数据损坏、内存泄露、无告警崩溃、权限越界、安全沙箱击穿等核心缺陷，一律否决。
   - 处置：立即触发 Andon 停机，保护现场日志，阻断发版。

---

## 六、D-146 预签页 (Pre-Sign Page for Registry Verification Window)

> **预签条款声明**：本节严格逐句引用退役账本 D-145β（原文 docs/decision-ledger.md:155；恢复：`git show '1c5befe^:docs/decision-ledger.md'`）、发布后段核销行（Registry publish 后 30 天一次性核销）与 D-150① 裁决，无新增语义。

1. **起算锚前移至 0.4.1-publish (D-150①)**：
   - MCP Registry publish 窗口与下一次发版窗（0.4.1 patch 版）强绑定。
   - **D-146 30 天核销窗的起算时间锚确定为 `0.4.1-publish` 落账日**，而非 v1.0.0 发版日。
2. **核销目标渠道与一次性核销规则 (D-145β: Registry publish 后 30 天一次性核销)**：
   - **核心核销渠道**：严格核验 `mcp.so`、`PulseMCP`、`VS Code gallery` 以及 Cline 客户端发现信号（D-147）。
   - **三态落账**：按收录登记表三态（收录正常 / 有条件收录 / 未收录或异常）如实落账。
   - **一次性核销原则**：非 CI 监控项，**30 天窗口到期时执行一次性核销，绝非 30 天内的持续轮询监控（一次性核销≠持续监控）**。
   - **逾期后果**：到期若因不可抗力或渠道未响应未完成核销，**逾期未核销=债标已触发未兑现**，需在账本中明确记录挂账状态。
3. **真机窗与收录轨解耦 (D-150①)**：
   - 0.4.1 patch 版发版与 Registry publish 独立推进，无需等待真机验证窗排期，收录轨与真机验证轨形成平行墙钟。

---

## 七、逐框结果汇总表模板 (Summary Result Template)

真机验证窗执行完毕后，执行人应回填下表并随成果归档至 `docs/evidence/` 与账本：

| 框号 | 检查项简述 | 归属车道 | 验证环境 | 状态 ([x] / 声明) | 裁决/证据工件指针 |
|:---:|:---|:---:|:---:|:---:|:---|
| 框 1 | mayapy Tier-2 suite | mayapy 道 | Maya 2024 | [ ] | docs/evidence/probes/probe-box01-mayapy-suite.json (支持 live GUI 降级退路) |
| 框 2a| GUI Tier-3 (视口/相机) — Maya 2024 | GUI 道 | Maya 2024 | [ ] | docs/evidence/probes/probe-box02-gui-viewport.json |
| 框 2b| GUI Tier-3 — Maya 2025 | GUI 道 | Maya 2025 | not verified on this machine | D-049 披露档（条件判位，非勾选） |
| 框 2c| GUI Tier-3 — Maya 2026 | GUI 道 | Maya 2026 | not verified on this machine | D-049 披露档（条件判位，非勾选） |
| 框 3 | PySide2 framed Qt 通道 | GUI 道 | Maya 2024 | [ ] | docs/evidence/probes/probe-box03-qt-channel.json |
| 框 4 | headless 临时文件注入回退 | mayapy 道 | Maya 2024 | [ ] | docs/evidence/probes/probe-box04-headless-fallback.json |
| 框 5 | MCP Inspector + 多客户端渲染 | GUI 道 | Win11 + 客户端 | [ ] | docs/evidence/probes/probe-box05-client-multiblock.json |
| 框 6 | Windows Qt RST/FIN (WinError 64) | GUI 道 | Maya 2024 | [ ] | docs/evidence/probes/probe-box06-win-rst-fin.json (Linux 拆入 CI 债) |
| 框 7 | cmds.* 静态签名审计 | 桌面道 | 源码 + 官方文档 | [ ] | docs/evidence/probes/probe-box07-cmds-audit.json |
| 框 8 | mayapy 签名采集回灌 stub | mayapy/桌面道 | Maya 2024 -> stub | [ ] | docs/evidence/probes/probe-box08-signature-diff.json |
| 框 9 | modelPanel 相机切换 call-form | GUI 道 | Maya 2024 | [ ] | docs/evidence/probes/probe-box09-modelpanel-camera.json |
| 框 10| 无尾换行 + 500ms commandPort | mayapy/GUI 道 | Maya 2024 | [ ] | docs/evidence/probes/probe-box10-commandport-newline.json |
