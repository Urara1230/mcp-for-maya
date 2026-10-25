# GLOSSARY — Maya MCP Server

Domain vocabulary. Each entry is a term and a definition of at most two sentences.

## 工作流

**ICEV**: Inspect → Compute → Execute → Verify 四步强制工作流；任何场景修改必须先快照感知、再计算、执行、验证。

**视觉闭环 (Visual Loop)**: 把视口/渲染图像回传给多模态 Agent 的验证手段；scene_viewport_snapshot 高频便宜，scene_render_preview 低分辨按需，headless 会话返回显式能力错误。

## 场景认知

**场景快照 (scene_snapshot)**: 一次批量查询返回的全场景空间状态（名字/类型/位置/BBox/父子），是 Agent 场景心智模型的来源。

**CoS (Chain-of-Symbol)**: 场景数据的紧凑记号化输出格式，用于压缩 token。

**Zone**: 按命名规则映射的功能分区，内置九组 GRP_shell / entrance / display / ip_core / furniture / lighting / path / decor / service。

**命名约定**: 生产命名前缀 GRP_/GEO_/MAT_/CAM_/LGT_（key/fill/rim/accent）/LOC_；禁用 pCube1、group1 等 Maya 默认名。

## 验证与事务

**scene_review**: 确定性场景审计；每个检查是自注册 validator，单条异常记 check_error 不中断整轮。

**自定义 validator**: 经 register(name, fn, severity, family) 注入的受信扩展机制，不是插件生态；当前尚无公开注册 API（见 issue #79）。

**checkpoint（真快照）**: 对导出时刻内存态的自包含序列化快照（exportAll，references 默认展平），落 checkpoints/，不含 undo 历史。

**rollback**: 打开快照并显式重建场景身份；任何覆盖路径名操作前，被覆盖内容必须已有内存态快照。

**ad-hoc 快照**: untitled 场景经显式 name 产出的标记性快照；不参与 S2 回滚语义，rollback 后停留快照路径并返回 scene_rebound_to=null。

**脏场景守卫 (dirty-scene guard)**: 场景变更前的只读探针；发现未保存改动即停手交回用户三选一，因为 force 会挂死 UI、不 force 会丢工作。

## 连接与注入

**引导通道 (bootstrap channel)**: commandPort（默认 :7001），只用于发现会话与注入 helper，不承载工作流量。

**工作通道 (working channel)**: bootstrap 后创建的专用通道；默认是自研 Qt TCP server，headless 回退到 native commandPort。

**temp-file 注入**: 大模块落盘临时文件再由 Maya 读取；GUI 会话已由分帧协议取代，仅 headless 回退保留。

**薄集成 (thin integration)**: 只接低成本外部源（首片 Poly Haven，CC0 API），不做自建资产库或爬虫。

**asset_descriptor**: 宿主下载与 Maya 导入之间的受验证交接对象（本地路径+源 URL+license+依赖清单）；URL 不直接递给 Maya 侧。

**注入模块准入判据**: 新建 Maya 侧注入模块须同时满足独立注入时机、独立 API 面、独立失败域；否则进既有模块，目标为巨石时用同注入单元独立文件。

**同注入单元独立文件**: 能力归既有注入域，但源码驻独立宿主侧文件经装配进入该单元；不得借此绕开准入判据。

## 安全姿态

**受信方**: 威胁模型信任 MCP client/agent；防线不承诺遏制恶意 client。

**安全网 (safety net)**: pattern 扫描/限流/校验等防误操作机制，与安全边界严格区分。

**审计日志 (audit log)**: 每次工具调用一行 JSONL 的独立记录流；outcome 含 rejected，审计写失败不阻断执行。

**标记块 (marker block)**: userSetup.py 中 `# >>> mcp-for-maya >>>` … `# <<< mcp-for-maya <<<` 的幂等合并单元；不提供整文件覆写，写入前留时间戳备份。

**模块卸载协议**: create_module 被 overwrite 前调用旧模块的 `_mcp_teardown`，由归属方释放 socket/Qt server/线程；钩子须幂等。

**双语判别探针**: 用 `eval("1/2")` 区分 MEL（0）与 Python3（0.5）的零副作用端口探测载荷。

**端口豁免集**: 已判定非 Python 的端口入会话级永久豁免，端口从 LISTEN 消失才解禁，避免周期重探。

## 工程词汇

**绞杀者 (strangler)**: 先建 stub 测试脚手架，修复带回归测试落地，拆分随修复触及区域增量进行。

**stub 层**: CI 上替代 maya.cmds/OpenMaya 的自建假实现，数学语义必须正确；定位为契约测试层，像素/行为正确性归 mayapy 档。

**mayapy 档**: 可选的真机 Maya 测试层，本地手动跑、文档化、不卡 CI，禁与 stub 层混跑。

**净零副作用 (net-zero side effect)**: 工具可瞬时改变宿主状态，但必须 try/finally 恢复到入前快照；readOnlyHint:true 以此为前提。

**双层错误契约**: 宿主侧失败经 MCP isError 上报；Maya 域结果经 `{error:{code,message,suggestion?}}` 返回。

**三层命名**: dist 名=mcp-for-maya、import 名=maya_mcp_server、script 名=mcp-for-maya；dist 与 import 分叉是 PyPA 惯例。

**人工确认门**: repo 改名、push main、建 tag/Release、PyPI 发布、改 secret 须用户执行或逐条批准；低风险可逆动作 agent 自主。

**冻结预算 (frozen budget)**: lint/类型错误计数预算入库冻结、只降不升。

**棘爪 (ratchet pawl)**: 冻结预算之外的防侵蚀机制，堵住「抑制项只增不减」的侧门，例如 warn_unused_ignores 与 noqa 审计。

**休眠代码 (dormant code)**: 有明确复用排期但当前零生产引用的代码；须机器可见标注，其测试不得算进生产覆盖率。

**human_verify 骨架测试**: 人眼验收项的版本化形态，test 标 human_verify、只打印步骤不写 assert，进版本库但不制造假红假绿。

**presence 基线**: 真机会话内采集 cmds.* 实际调用面生成的 JSON 清单+allowlist，用于 stub↔runtime 漂移检测；只覆盖存在性。

**双环境包 (dual-runtime package)**: 宿主 requires-python 与 Maya 内嵌解释器支持 floor 分开声明，注入代码做版本+能力双层守卫。

**便利性镜像 (convenience mirror)**: 英文 README.md 是唯一 source-of-truth，README.zh-CN.md 是可滞后但不许分叉的镜像。

**TDQS**: Glama 对工具定义质量的第三方评分；本仓以字母档 A、逐件 min ≥3.0、零 contradiction 旗标为验收，不以分数驱动 API breaking。

**Boundary: 行**: 每件工具描述内嵌一句单向 `Boundary:`，点名 1~3 个兄弟分工，用于消歧。

**只加不删资产目录**: `.github/assets/` 被绝对 URL 引用，条目只增不删不改名，否则历史 PyPI 页面永久裂图。
