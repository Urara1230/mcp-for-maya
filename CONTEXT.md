# Maya MCP Server — Domain Context

让 LLM Agent（Codex/Claude 类）通过 MCP 工具直接操控 Autodesk Maya 的项目；核心抽象是给 Agent 空间感知 + 工程级验证回路，而非通用遥控器。

## Language

### 工作流

**ICEV**:
Inspect → Compute → Execute → Verify 四步强制工作流：任何场景修改必须先快照感知、再计算、执行、然后验证。
_Avoid_: 盲改

**视觉闭环 (Visual Loop)**:
把视口/渲染图像回传给多模态 Agent 的验证手段；两个工具：scene_viewport_snapshot（高频便宜）与 scene_render_preview（低分辨按需）。headless 会话无视口，工具须返回显式能力错误。
_Avoid_: 截图工具（过泛）

### 场景认知

**场景快照 (scene_snapshot)**:
一次批量查询返回的全场景空间状态（名字/类型/位置/BBox/父子），是 Agent 场景心智模型的来源。
_Avoid_: 场景信息 dump

**CoS (Chain-of-Symbol)**:
场景数据的紧凑记号化输出格式，用于压缩 token。

**Zone**:
按命名规则映射的功能分区，内置九组：GRP_shell / GRP_entrance / GRP_display / GRP_ip_core / GRP_furniture / GRP_lighting / GRP_path / GRP_decor / GRP_service。
_Avoid_: 区域、层

**命名约定**:
生产命名前缀——GRP_ 组、GEO_ 几何、MAT_ 材质、CAM_ 相机、LGT_ 灯（带角色 key/fill/rim/accent）、LOC_ 定位器；禁用 Maya 默认名（pCube1、group1 等）。

### 验证与事务

**审核 (scene_review)**:
确定性场景审计；每个检查是自注册 validator（携带 name/severity/family/order 元数据），单条异常记 check_error 不中断整轮。
_Avoid_: 评分脚本

**自定义 validator**:
经注册 API（register(name, fn, severity, family)）注入的自定义检查；口径=受信 agent 的扩展机制，非插件生态。
_Avoid_: 插件、沙箱

**checkpoint（真快照）**:
对导出时刻内存态的自包含序列化快照（exportAll，references 默认展平），落 checkpoints/ 目录；不含 undo 历史。
_Avoid_: 文件备份、存档

**rollback**:
打开快照文件并显式重建场景身份；不变式——任何覆盖路径名操作之前，被覆盖内容必须已有内存态快照；返回值显式携带场景身份信息。
_Avoid_: 恢复

**ad-hoc 快照**:
untitled（未保存）场景经显式 name 参数产出的标记性快照；不参与 S2 回滚语义——rollback 打开后停留快照路径（S1），返回 scene_rebound_to=null、original_file_status="no_original_file"。untitled 场景不带 name 的 checkpoint 调用默认报错。 快照落 <Maya workspace>/checkpoints/。
_Avoid_: 无名场景快照

**脏场景守卫 (dirty-scene guard)**:
对用户运行中会话执行场景变更类操作前的只读前置探针——先查 file modified 标志与场景名，命中未保存改动即停手并把处置权交回用户（存盘/授权丢弃/中止三选一），干净才放行新建场景。存在理由：脏场景上不带 force 会弹模态保存框挂死 UI 线程，带 force 则静默丢弃用户工作——两条路都致命，故唯一合宜姿势是"探针先行、人决脏场景"。
_Avoid_: 无条件 file(new,force)、在用户当前场景直跑会留残的 fixture、把场景状态当可信前提

### 连接与注入

**引导通道 (bootstrap channel)**:
commandPort（默认 :7001），仅用于发现会话与注入 helper，不承载工作流量。
_Avoid_: 主通道

**工作通道 (working channel)**:
bootstrap 后创建的专用通道；默认=自研 Qt TCP server（多客户端、长度前缀分帧）；headless（mayapy 无 Qt 事件循环）回退到 native commandPort。

**temp-file 注入**:
大模块落盘临时文件再由 Maya 读取的注入方式；GUI 会话上随分帧协议移除，headless 回退保留。

**薄集成 (thin integration)**:
资产生态接入策略——只接低成本外部源（首片 Poly Haven，免费 CC0 API），挂到 scene_plan 做 zone 语义 + bbox 尺寸推荐；不做自建资产库/爬虫。
_Avoid_: 资产市场


**asset_descriptor**:
宿主侧资产下载与 Maya 侧导入之间的受验证交接契约——下载完成返回的结构化对象（本地首选文件路径+源 URL+CC0 license+依赖路径清单），参照 dcc-asset-polyhaven 形态；发现/下载（宿主、有网）与导入（Maya、本地文件）职责分界即以此对象为缝。
_Avoid_: 把 URL 直接递给 Maya 侧下载（Maya 零网络面）、下载与导入揉成单步无交接物

**注入模块准入判据**:
新 Maya 侧注入模块的立项门槛——三判据全满足才允许新建独立模块：①独立注入时机（D-095 语义读法：被需要的会话形态独立，非仅触发器机械独立）②独立 Maya 侧 API 面（cmds/omui 调用面与既有模块正交）③独立失败域（异常/降级语义不与他模块共享）；④目标模块健康度附则——判据不过须进既有模块时，若目标是挂号巨石则优先同注入单元独立文件而非追加本体（ADR-0027 含 D-095 修订）。域边界=注入边界=失败域粒度；文件粒度在同注入单元内可细分。
_Avoid_: 凭语义就近塞巨石（债上加债）、为表面相似拆模块（碎片化）

**同注入单元独立文件 (same-unit separate file)**:
B' 落位形态——能力归既有注入域（同一注入模块名/触发时机/失败域），但源码驻独立宿主侧文件经装配进入该注入单元；典型用于「准入判据不过但目标是挂号巨石」场景（ADR-0027 判据④，D-095 introspection 首案）。
_Avoid_: 独立文件≠独立注入模块（注入单元与失败域仍唯一）；不得借此绕开准入判据为新能力开小灶

### 安全姿态

**受信方**:
威胁模型中信任的对象 = MCP client/agent；防线不承诺遏制恶意 client。
_Avoid_: 沙箱内执行

**安全网 (safety net)**:
pattern 扫描/限流/校验等防误操作机制；与安全边界严格区分——文档禁用 sandbox/secure 字样描述它们。
_Avoid_: 沙箱、安全边界

**审计日志 (audit log)**:
每次工具调用一行结构化 JSON 的独立记录流（JSONL 文件），与应用日志分流；outcome 含 rejected——被管线拒绝的调用是探测攻击的第一信号。审计是观测面不是边界：审计写失败不阻断工具执行。
_Avoid_: 流水日志

**标记块 (marker block)**:
向共享的用户启动文件（userSetup.py）做幂等合并写入的单元——`# >>> mcp-for-maya >>>` … `# <<< mcp-for-maya <<<` 包裹；不存在则建、有块则原位替换、不可解析则拒绝；不提供整文件覆写参数，写入前留时间戳备份。
_Avoid_: 覆写安装


**模块卸载协议 (module teardown protocol)**:
create_module 虚拟模块被 overwrite 替换前的生命周期钩子——替换 sys.modules 条目之前 getattr(旧模块,'_mcp_teardown',None) 判空调用，模块自带资源（listen socket/Qt server/线程）由归属方自释放；纪律=钩子幂等+逐项 try/except+Qt 对象 deleteLater+断信号连接非仅关 socket。importlib 语义实锤根因：sys.modules 替换不触发任何清理。
_Avoid_: 直接顶掉模块对象、调用方两步点修当长期方案、状态过继旧引用

**双语判别探针 (bilingual discriminating probe)**:
commandPort 端口类型探测的零副作用载荷形态——eval("1/2") 在 MEL（标准命令、int 除法→0）与 Python3（builtin→0.5）两侧皆合法且返回值可区分，替代会刷 syntax error 的裸 Python 探针 1+1；约束=eval 内保 int/int 操作数，MEL 侧回传形状须真机定型。commandPort 只回传返回值不回传 stdout（print 载荷无效），// 在 Python 非注释是 SyntaxError（死案留档）。
_Avoid_: print() 载荷、// 伪注释载荷、MEL python() 反向包裹（报错转嫁 Python 端口）

**端口豁免集 (port exemption set)**:
判别为非 Python 的 commandPort/第三方端口入会话级永久豁免集（端口从 LISTEN 消失才解禁）——probe minimization+指纹缓存范式，终结对同一已识别端口的周期重探；与失败冷却不同层：冷却管“疑似可成但失败”，豁免集管“已判明非我族类”。
_Avoid_: 对非 Python 端口周期重探、豁免集跨进程持久（端口复用语义不符）

### 工程词汇

**绞杀者 (strangler)**:
大模块改造方式——先建 stub 测试脚手架，修复带回归测试落地，拆分随修复触及区域增量进行。
_Avoid_: 大爆炸重构

**stub 层**:
CI 上替代 maya.cmds/OpenMaya 的自建假实现；数学语义必须正确（尤其 8 角点世界 bbox），否则假绿。定位=契约测试层——验证代码与 maya.cmds 契约的往返逻辑（edit 真改状态、query 读回同值），像素/行为正确性归 mayapy 档；GUI 面（panel/modelPanel/playblast/OpenMayaUI）以 stateful-fake 扩展，禁像素断言；lookThru 仅作 stub 保真保留（生产已于 T-11/D-039 退场）。

**mayapy 档**:
可选的真机 Maya 测试层，本地手动跑，文档化，不卡 CI。与 stub 层不同 pytest 调用（禁混跑）。

**净零副作用 (net-zero side effect)**:
工具执行中可瞬时改变宿主状态（视口相机/时间线），但必须 try/finally 恢复，使成功与失败路径的终态==入前快照；readOnlyHint:true 的声称以此为成立前提，瞬时改变须在工具描述中披露。
_Avoid_: 就地改动

**文档传播矩阵 (propagation matrix)**:
文档同步边界规则——commit 只修"本次变更使其失真"的文档行（触碰的工具数/清单/instructions/架构表）；与变更无关的既有错误不顺手修（sweep 反模式），但须在 PR 描述中列出交文档任务。

**双层错误契约**:
工具错误的两个正交通道——宿主侧失败（校验/限流/连接/管线拒绝）经 MCP isError 上报；Maya 侧域结果经统一 `{error:{code,message,suggestion?}}` 对象返回。区分“调用失败”与“调用成功但域内未过”。
_Avoid_: 全量信封

**三层命名**:
Python 包的三个独立命名面——dist 名（PyPI 货架标签，本项目=mcp-for-maya）、import 名（代码地名，=maya_mcp_server 不动）、script 名（uvx 命令入口，=mcp-for-maya，旧名仅作兼容 alias）。dist 与 import 分叉是 PyPA 认可的惯例，fork 是其常见成因。
_Avoid_: 包名（歧义）

**人工确认门**:
一次性高副作用公开动作的人工批准边界——repo 改名、push main、建 tag、建 Release、PyPI 发布、改 secret 须经用户执行或逐条批准；agent 仅备命令清单与影响面列表。低风险可逆动作（文件/分支/issues/PR）agent 自主。
_Avoid_: 自动发布

**事件门授权 (event-gate authorization)**:
无人值守执行窗内 agent 的授权形态——不逐段确认也不 blanket approval，授权钉在预定义事件门上（验收卡红灯即停/安全红线不可授权越过/其余全自主）；停手权在 agent、处置权在用户。机制细则见 ADR-0023。
_Avoid_: 逐段一停（往返成本+无新增判断点可守）、blanket 授权越过红线、人工门动作插在裁决悬停段

**冻结预算 (frozen budget)**:

存量 lint/类型错误以计数预算文件入库冻结、只降不升的 ratchet 纪律——CI 计数>预算即 fail；预算下调须经 PR 同 commit 改文件并写明理由，CI 内禁止自动写预算；粒度按目录分段（src/tests），per-rule 为完整质量门升级项。
_Avoid_: 全绿门槛、noqa 打标基线

**休眠代码 (dormant code)**:
有明确排期复用决策而保留、但当前零生产引用的代码；必须机器可见标注（AGENTS.md 联动表+预算/配置说明），docstring 装饰不算数。区别于死代码（无复用计划应删除，git 历史即期权）与负债代码（在被引用的生产路径上）。休眠代码的测试计为 dormant 覆盖，不得虚增“已验证”叙事。（首例 T-06 aesthetic_engine.py 已按 D-124 删除收束——休眠的终点可以是删除。）
_Avoid_: 仅口头休眠、无锚点标注、把 dormant 测试算进生产覆盖率叙事

**棘爪 (ratchet pawl)**:
冻结预算之外的防侵蚀单向机制——预算只降不升之外还须堵“抑制项只增不减”的侧门：mypy 落地为 warn_unused_ignores+warn_unused_configs（不再必要的 ignore/override 立即显形）、lint 落地为 noqa 审计与预算下调须同 commit 说明；无棘爪的 ratchet 会因抑制面静默膨胀而失效。失效模式另例=预算值长期高于现值不收紧、闸只挡不收退化为高水位线（旧 cap 默许继续膨胀至上限）——达标即应 PR 下调并写明理由。棘轮件的物理位置遵工具约定：`mypy-baseline.txt` 在仓根=该工具默认 CWD 读取位置，与 `.github/` 自研兄弟件（ruff-baseline.json/monolith-budget.json）的不对称系工具约束非漂移（D-150②）。
_Avoid_: 只设预算不设棘爪

**human_verify 骨架测试**:
人眼验收项的版本化形态——pytest 中 test 函数存在、标 human_verify marker、body 只打印待人工确认步骤而不写 assert；使 GUI 真机清单（截图方向/渲染正确性等脆弱断言面）进版本库可复跑，又不制造假红假绿。与 gui marker（机器可断项真断言）同属 manual-tier 层，按 RC 节奏跑不卡 CI。
_Avoid_: 把脆弱 GUI 断言硬写成 assert、清单散落仓库外

**presence 基线 (presence baseline)**:
GUI 真机会话内 inspect.signature 尝试+dir() 采集项目实际 cmds.* 调用面产出的 JSON 清单+allowlist——cmds builtins 无 inspectable signature，契约实为 presence+方法名表，故名 presence-baseline.json（D-056①）；与 stub 声明自动 diff 作合约护栏，对齐 mypy stubtest 先例的 stub↔runtime 漂移检测，属冻结预算同构的 ratchet 资产——Maya 版本升级时重采集比对。presence 只覆盖存在性，时序/副作用/返回值语义归 call-form 清单补（证据层=docs/visual-callform-matrix.md）。
_Avoid_: 全人工逐条对文档、把 presence 等价当行为等价

**dogfood 素材**:
门面/文档证据素材由产品自身产出的纪律——README 截图与演示 GIF 用本项目视觉工具（scene_viewport_snapshot/camera_orbit 序列）真实捕获，而非生成图或手工 mock；是 show-don't-tell 的最强形态，素材即能力证明。采集走 repo HEAD 服务实例，属门面资产入 git（非 .scratch 过程件）。
_Avoid_: 生成图冒充实拍、手工摆拍截屏

**文本极轻共享资产 (text-light shared assets)**:
双语 README（zh/en）共用同一套视觉资产的纪律——logo 字标用英文名、hero 等资产文字压到最少且统一英文，两版 README 零资产分叉；动因=SVG 内嵌中文在 GitHub 渲染链有缺字风险+维护成本翻倍。
_Avoid_: 双语双变体资产、SVG 内嵌中文文案

**非对称纯色断言 (asymmetric pure-color assertion)**:
视口读回正确性的地面真值锁定法——渲染已知非对称纯色布局（如左上红块其余黑）截屏断言，同时锁定垂直朝向与 RGB/BGR 通道序；非对称是硬约束（满屏纯色锁不了"翻没翻"），色管用容差带非精确等值（OCIO 可偏移纯色）。与 stub 自验证同构：先验证测量仪器，再用仪器测东西。
_Avoid_: 版本号分支猜朝向、universal swizzle、满屏纯色 fixture、精确等值断言

**双环境包 (dual-runtime package)**:
宿主进程与注入目标解释器是两个独立运行时的包形态——requires-python 只声明宿主依赖图（Py>=3.10），注入端（Maya 内嵌 Python）须独立声明支持 floor（Maya>=2023 即 Py>=3.9）并在注入代码内做双层守卫：版本检测在前报可操作消息、能力检测（hasattr）在后防 fork/patched 解释器。声明即契约：不为已声明不支持的环境写兼容 shim。
_Avoid_: 宿主 requires-python 当注入端契约、守卫缺位让用户吃 traceback、为 EOL 运行时写兼容层

**技术+署名回应 (technical response with attribution)**:
休眠上游 issue 区的合宜发言形态——根因分析+修复路径+fork 实现指针+血缘披露+回哺意愿句，第一功能是给卡住的用户留路标、引流仅为副产品；区别于引流型（promotion-first，spam 邻域）与纯技术型（对 stranded 用户无导航价值）。纪律：一 issue 一评不追评、事实陈述非广告、发布归人工门。
_Avoid_: 宣传置顶措辞、连环追评、把回应当广告位

**耐久陈述 (durable claim)**:
对外文案中不随 yank/后续发布而失效的版本陈述写法——以「≥X.Y.Z 已含」锚定引入版本而非「最新版已修」锚定当前位；前者在旧版被 yank、新版接续后依然为真，后者随发布节奏变质为误导。回应/对照表/release notes 的版本指针一律走耐久形态。
_Avoid_: 「最新版修复」式瞬态措辞、指向即将 yank 版本的硬推

**upstream issue 对照表 (upstream issue tracking table)**:
docs/ 下公开登记上游 open issue 与本仓处置映射的表（issue×处置×版本号×验证状态）——对外回应的固定锚点+透明度叙事资产；须与决策账本/CHANGELOG 同源，任何一格失真即破坏其存在理由。
_Avoid_: 与账本漂移的二手表、缺验证状态列的过度宣称

**fix-forward（同窗修复）**:
发布冻结窗口（tag/RC 签发前）内新发现低风险高收益缺陷的处置姿势——修进待发版本而非带病发布记 known-issue；判据=修复量小、可与待发版本同过一次验证窗（机会成本为零）、且"修复版遗留同族缺陷"的披露负担大于修复成本。tag 一旦签发则切换为"只加不减"的事后语义。
_Avoid_: 无 RC 锚点却按 post-RC 严苛门槛拒修、为赶发布把已知同族缺陷带病上架

**逐版核实 (per-release verification)**:
发布面决策（yank/安全公告/弃用声明）的判定单位是单个 release 而非缺陷家族——同一缺陷在不同版本间可能被其间修复改变存在性，须对每个候选版本做实物核验（如 git show 对应 tag）再定处置，reason 写该版的具体故障模式。
_Avoid_: 凭"同族"推定批量处置、在未确认后继版本可用前先行 yank

**便利性镜像 (convenience mirror)**:
第二语言 README 的权威定位——英文 README.md 为唯一 source-of-truth，README.zh-CN.md 为镜像：zh 文件头 HTML 注释锚定英文版 commit hash 作新鲜度审计，CI 挂标题骨架校验防漂移；镜像可滞后、不许分叉。
_Avoid_: 双文件对等维护（对等必漂移）、留 README_en.md stub（双英文文件=混乱源）

**prompt→结果例表**:
README 叙事单元——| Prompt | Result | 表把用户指令与真实实拍结果配对（4-6 行甜区，每行内嵌图）；每行天然是 dogfood 证明；构图分工：建模/导入行单图、审计行 before/after 双帧读作"证据"非"作品"；门面资产保持纯美感，差异化叙事由例表承担。
_Avoid_: 无图凑数行、外链视频当主图（PyPI 不渲染）、把审计行也做成纯美图丢失差异化

**冻结采集会话 (frozen capture session)**:
门面素材的采集纪律——一个冻结环境（同 Maya 会话/同 VP2 设置/同灯光/同 HUD 状态）一个 session 一次出齐全部素材（hero/shots/orbit/social/例表图）；跨 session 补拍会产生视口风格漂移（SSAO/阴影/背景不一致），补拍仅作缺陷返工路径非常规分批；Maya 采集窗以 MAYA_UI_LANGUAGE=en_US 英文 UI 启动（系统环境变量，禁写 Maya.env）。
_Avoid_: 分批零散补拍、中文 UI 配英文默认门面、会话间设置漂移

**精确性替代艺术性 (precision-over-artistry)**:
脚本驱动 DCC 演示题材的选型准则——纯代码建模（execute_code）只在几何逻辑清晰的题材上可达作品级：钟表机芯/渐开线齿轮/模块建筑/阵列装配，精度本身就是卖点；有机角色/雕塑类必翻车（拉伸球拼装是工具约束非 prompt 问题，HN/3daistudio/BigGo/Reddit 四源收敛）。有机题材的诚实出路=Poly Haven 导入（归管线展示非建模展示）。
_Avoid_: 用脚本硬做角色生物、把密度当难度、假齿轮不自啮合

**视觉模块预算 (visual module budget)**:
README 可视区的图片密度红线——≤4 个视觉模块（合成横幅+例表实拍+orbit 通栏+可选架构图）、单图 <500KB、orbit GIF <10MB（GitHub 限制线）；依据=Utrecht「don not overdo it」官方教程+巨型 GIF 拖垮整页双源翻车实证（import-http#7/GitLab 论坛）+awesome-readme 收录案例零多图堆砌正面案例。与冻结采集会话互补：一个管拍、一个管放。
_Avoid_: 截图散落正文各处、GIF 锁进表格窄列、图片堆砌显自信


**实证反审计 (evidence-based counter-audit)**:
对外部审计指控的回应姿势——每条指控先对当前树/真机取证再表态：成立则修、证伪则驳回并留探针证据（裁决型工件归 docs/evidence/ 见「裁决型证据」条；过程 transcript 仍 .scratch/<slug>/）；对称纪律=对方驳对的指控要认（D-042 撤回先例），我方驳倒的要有机器证据不许只有口头。
_Avoid_: 凭记忆反驳、把对方指控照单全收（辩证=逐条实证）

**证据指针 (evidence pointer)**:
CHANGELOG/发布说明每条 Added/Fixed bullet 必须挂的可机检锚点——可接受形态白名单单源=.github/evidence-anchor-forms.yaml（pytest node ID＞path::symbol AST 验证＞sha:path:line＞裸 path:line 版本化文档 warn 升级提示、.scratch 合法过渡形态；D-183 scoped revised 细化 D-082⑦「file:line 或测试 ID」表述，机制本体不变）；lint 可查形态，防「决策→文案」管线跑在「代码→对账」前面（CHANGELOG:33、manifest-based 两例病灶）。
_Avoid_: 无锚点承诺文案、先写机制名后补实现

**裁决型证据 (decision-grade evidence / evidence binary)**:
证据二分归置纪律（D-109，修订 D-048）——支撑公开裁决/对外声明的工件（探针 JSON、对账报告）脱敏后入 `docs/evidence/` 版本化=外部可复核锚，并附 claim boundary 字段写清该证据支持到哪一层；过程 transcript/reports/草稿仍留 `.scratch/` 不入 git。工件 append-only：被反驳不原位篡改，走「新工件+新裁决+旧件标 superseded」。前向规则：一切 verified-live/live 级声明须挂仓内工件或点名 mayapy/human_verify 档记录，裸声称=违 D-017⑥。
尺寸纪律（D-153②）：≤512KB 的 PNG 截图件与 JSON 同址入 probes/；超限件（录屏类）不入仓，走 sha256+本地路径+再生成脚本路径的三联指针=仓内描述件+仓外实物，出现时按再裁流程立裁。issue 载体二分（D-153③）：tracker 类 issue 的 body=压缩终态一次性更新、过程进展走评论 append-only——窗内逐框改写 body 会让弱断言脚本（check_release_appendix.py）中途语义抖动，且半成品态对外可读违披露纪律。
_Avoid_: 裁决证据躺 .scratch 外部不可达、证据原位编辑冒充最新、裸声称无工件无档名、MB 级二进制裸入仓、逐框改写 tracker issue body

**只加不删资产目录 (append-only asset dir)**:
.github/assets/ 等被绝对 URL（main 钉）引用的发布资产目录纪律——条目只可新增不可删除/改名；旧 PyPI 页面 description 永久指向 main 路径且不可回改，退役删除=全部历史页面永久裂图。CI 守卫=.github/scripts/check_assets_append_only.py（D-113：纯 add 放行、D/M/R/T 拦；逃生口=维护者知情合入+PR 声明+账本注记）。
_Avoid_: 素材换代顺手删旧文件、改名复用槽位

**触发条件债**:
顺延债的强化形态——登记时必须附显式重开触发条件（如「下个 minor 窗口探 fastmcp 3.x 兼容面」）；ignore/defer 不带触发条件=永久失明而非债管理。登记时须当场廉价核验触发当前态并写入债文（廉价=查已有 --durations/版本数据或跑单项，非登记时跑全量），触发态三档：否 / 是-可即兑（不入债、直接兑现）/ 是-不可即兑（债文标注已触发+写明排程理由）。「带病出生」判例=D-116（登记日实测已触发而未核验，R27-Q2 兑现）。
_Avoid_: ignore 规则裸挂不记债、债条目无触发条件成永久搁置、登记时不核验触发当前态

**证据型上界 (evidence-based cap)**:
依赖版本 cap 的合法形态——仅当存在已知不兼容证据或上游官方要求时才设上界（PyPA/iscinumpy 立场：cap 是例外非默认）；cap 必须挂证据指针（CI 红记录/上游迁移指南），探通收窄时在同文件注释引证据。
_Avoid_: 无证据预防性 cap、cap 悬空不引证据、上游已明令兼容范围仍放任

**探索窗 (exploration window / spike)**:
触发条件债的执行体——time-boxed 探测单元：探针清单→可弃分支→结构化报告→裁决点，产出=信息非代码（分支可弃不合并、不写迁移 PR 进主线）；成熟先例=XP spike/Renovate 按序探针。姊妹件关系：债=排程（何时探），窗=执行（探什么怎么判）。
_Avoid_: 探测分支夹带修复、报告落 docs/ 越过程件归处、无验收判据的开放式探测

**解析漂移金丝雀 (resolution-drift canary)**:
weekly scheduled 浮动依赖解析 job（D-112 双轨裁决）——uv.lock 落地把 PR 矩阵锁成确定性面后，该 job 专职跑 fresh resolve 保「库测 PyPI 区间=用户实装面」的浮动探测不失传；红=上游 dep release 首次漂移信号（D-100④ 触发债自动成就），同时承接 fresh-resolution 类探针义务（D-099 的载体移交）。红→人知通道（D-123 制度化对价）=独立 trailing notify job：job 级 issues:write 按需提权，issue 按 workflow/branch 去重——首开新建、续败评论、恢复自动关，notify 自身失败不遮蔽原红（continue-on-error）；人肉复核义务登记 D-127（v1.0.0 清单硬项，红绿均可——D-129 已兑现于触发前，dispatch 首燃全绿）。事件链独立（D-129）：workflow_dispatch 与 schedule 是两条独立事件链（GitHub community #206369 实证私仓 schedule 可全静默无记录），一链验证不担保另一链——归因禁写「weekly schedule 已验证」，除非 schedule 事件真验过。静默实证（2026-09-28）：schedule 首窗 06:37 UTC 后 61min 三次核验恒空、全量 run 无记录、workflow=active——结构性静默失效（#206369/runner#4210 形态），周驱动须由外部调度源调 workflow_dispatch 承载（D-129/D-135③）。姊妹件关系：触发条件债=何时查，金丝雀=持续在看。
_Avoid_: 全矩阵上锁无浮动哨兵、金丝雀红不成可检索工件、把金丝雀红灯误当 CI 绿态必达标
**发布门前清单 (release gate checklist)**:
发布前裁决汇编的单载体清单（D-128/D-131）——分层结构=硬项（全绿方可 tag 的定义性前提）/软前置（未达须显式披露的减损项）/一次性过堂行（发布面卫生核验：断链/陈旧宣称/实验态措辞/CVE 适用性/twine check）/观测项（非 blocker 的后续验证登记，显式入账防静默收窄）/裁出项（触发态否的存量债显式除名）/go-no-go 末行（逐项挂证据指针+单一决策人记录在案）；载体=发布链 ADR 增补块单真源，issue body/milestone description 仅作指针或操作副本；milestone=release payload（名=tag 名），后推池用 rolling-container 自封措辞（1.x 线规划时拆为精确版本 milestone）；blocker 定义在 release day 前写死（blast radius×severity×reversibility 三问）。
_Avoid_: 清单双载体漂移、issue body 当权威、观测项冒充硬项或反之、go/no-go 无证据指针、发布后补定义 blocker

**校准宣称 (calibrated claim)**:
对外宣称的强度校准纪律（D-132）——宣称句式=「能力+证据指针」：semver 1.0.0 规范语义=public API freeze 承诺（非质量认证），成熟范式=稳定宣称+freeze 承诺+证据指针+显式排除面（Temporal 1.0/React Compiler 1.0 一手公告同构）；安全宣称须指到 enforcement 代码或测试节点；竞品漏洞引用分层=threat-model 可作攻击类存在性论据、README 营销面不点名（CTA 伦理：不得将竞品漏洞用于商业利益；引用未公开同行漏洞须先知会竞方）。姊妹件：耐久陈述管宣称的时间寿命，校准宣称管宣称的强度-证据配比。
_Avoid_: 全称质量宣称超载、「we take security seriously」类惰性语句、横幅摘除与验证证据脱节、README 点名竞品 CVE

**发布编排纪律 (release choreography)**:
发布执行窗的操作纪律集（D-133/D-134/D-135）——①三段中止线：tag 后 CI 红→中止不升 PyPI（Debug to Revert; Not to Fix，窗内不修），PyPI 推送=不可回改点其后一切瑕疵走下个 patch 且 waiver 必带修复 expiry（禁无 expiry 挂账），每次中止=账本 incident 行（归因+回退+重发时点）；②go-signal 分层批准：批准门只设在不可逆邻域（uniform approval 失效），批准面=逐项核验证据非走读；③preflight 核对单=每行须绑定一个 CI 不覆盖的决策（cargo-cult 判据「这行会改变什么决策」），单子自挂 expiry 防沉淀为仪式；④谁发布谁守窗：所有权离散转移，观测闭环（健康确认+归因入账）前不算完成（false completion 禁忌）；⑤CONDITIONAL GO：非阻塞观测项不满足=带条件放行+独立跟踪路径，不拖停发布；⑥静默即升级：调度依赖静默实锤后立即走预案不等下一窗；⑦tag/Release 前向形态（D-150③）：tag 统一 annotated（人工 `git tag -a` 建锚、GH Release 挂已存在 tag 不反向生成）、Release 标题=裸 `vX.Y.Z`、存量不回填（tag 签发后冻结同源 D-080）。
_Avoid_: 窗内修车、waiver 无 expiry 裸挂账、核对单含与既有门禁重复行、发布即散场、观测项当 blocker、静默等下周、lightweight tag 充发布锚、Release 标题自由发挥、回填改写已发布 tag

**披露附录 (known-unverified appendix / disclosure appendix)**:
GH Release body 尾部的 per-release 已知未验证披露块（D-049 义务载体，D-142 机制定型）——语义=装船时点现状快照（release-scoped effectively frozen，Wakelog known-issues 双层惯例之第①层），frozen 后 stale=特性非缺陷（修复时在 fix 条目点名+旧条目加「Fixed in X→」指针，禁静默删）；内容纪律=只放声明性指针（issue 链接/债 ID 引用/实测结论），计数型易腐内容禁入；措辞必过校准宣称句式；机验=弱断言脚本（附录存在+非空+引用锚 issue+issue open，fail-loud），断言条件=「未勾项>0 或存在未兑现声明⇒附录恒非空」不写死计数；载体=Release body 层（可编辑非不可逆），不进 CHANGELOG。
_Avoid_: 全债表 dump（tracker-dump 反模式）、措辞抄未校准旧文、断言锁 body 尾部位置、fail-open 静默通过、改动 CHANGELOG 代补 body

**收录/叙事双轨 (listing/narrative dual-track)**:
对外扩张渠道的两层分类（D-141/D-143）——声明式低承诺轨=目录收录面（官方 MCP Registry/Glama/awesome-list），随时可上可撤、版本不可变但可重发，宣称走校准句式（beta 级禁 stable 语气）；叙事型高承诺轨=Show HN/blog/社区帖，一次性消费且有「working demo」证据门（证据不足的 launch 会被社区反噬），弹药=校准宣称+真机证据链，时点=v1.0.0+#7 全绿后。Registry 机制注：PyPI 所有权验证读已发布版本 README 的 mcp-name 标记行→registry publish 天然绑发版时点；聚合器同步逐渠道核销（D-144）：仅 Glama 官方明文 superset 分钟级同步精确成立；mcp.so/PulseMCP 未证实自动同步（mcp.so=GitHub issue 人工提交面）；渠道三态处置查询面=docs/distribution-surfaces.md（D-145α）。
_Avoid_: 叙事帖在证据未固化时抢发、聚合器逐个手工直投、收录徽章堆砌成荣誉墙、给 Smithery 类托管面配无法健康检查的容器面


**收录面三态判置 (listing three-state disposition)**:
逐渠道收录处置的三态分类——可行动 / 被动观察带触发条件 / 结构性否决永续，升级单向=被动→触发→可行动。登记载体=docs/distribution-surfaces.md（D-144/D-145α）。
-_Avoid_: 逐渠道周期性重开议事、未过结构匹配性判据即顺手提交、把被动观察误读为可行动、把结构性否决改写成临时推迟

**发布门三态判定 (release-gate three-state disposition)**:
v1.0.0 发布门对验收项的三态处置——Pass / Waived（reason+owner+expiry 三要素缺一不立，到期未消解自动转 Blocked）/ Blocked；机检清单=docs/evidence/gate-waiver-list-1.0.0.json。机制细则见 ADR-0023（D-163）。
_Avoid_: waived 行当 pass 读、无 expiry 豁免（退化永久豁免）、把站特异红直接拔为 feature-blocker、叙事面把 waived 项描绿

**安装面标注 (install-surface badge)**:
第三方列表/收录面里 OS emoji、平台徽标、安装形态标记的语义域（D-165）——语义=「此平台可安装/可运行」的**安装信号**，不是「该 OS 上全能力已验证」的审计信号；punkpeye Legend 原文=「For macOS/Windows/Linux」即「for」非「verified」。由此 D-149②「声明集=已验证集」的管辖边界显式化为：**宣称文本**（README/claim/描述句/叙事面）受其约束，**安装面标注**按宿主软件实际可运行平台标子集（桌面宿主 MCP server 标 OS 子集是列表正规形态，如 ableton-mind 🍎🪟）；剔除「已部分实证 OS」的 emoji 反而制造虚假信息（Linux 用户误判不可用）。
_Avoid_: 把 emoji 当审计声明逐字代入 D-149② 裁切、在列表一行内塞验证态限定词（生态惯例=一句话只讲功能+安装面）、宣称文本反而用 emoji 语义当借口放宽

**门检 (gate-check)**:
v1.0.0 门的幂等测量动作——preflight 窗内对 waiver 清单重读+逐行三态重算，可多次、每次留痕、不预判只落当时读数。机制细则见 ADR-0023（D-168..D-170）。
_Avoid_: 把门检当终审事件、等条件齐了才跑（probe 不等容器健康才跑）、日历周期排程、登记信号不重测致快照陈旧

**门审 (gate review / γ-B 终审)**:
v1.0.0 门的 go/no-go 一次性判定事件——召集与裁决归 user，判负后豁免车道关闭。机制细则见 ADR-0023（D-168/170/171）。
_Avoid_: 检测者自召集自裁决（违 D-152γ 分权）、拿陈旧快照判案、判负后走后门补豁免、把缩门混进豁免车道

**豁免续期 (waiver renewal)**:
waiver 行到期未消解的显式续签车道——门权者重签具名+新 expiry 事件锚+每行上限 2 次+reason 重验；门审后车道关闭。机制细则见 ADR-0023（D-168④）。
_Avoid_: 静默续期、日历悬空 expiry、无限续期、续期不重验根因、γ-B 后补豁免

**缩门 (descope / gate-definition revision)**:
把验收帧从门定义中移除的处置车道——本质=门定义修订须走 revise 呈报+披露，与豁免车道永久隔离。机制细则见 ADR-0023（D-171③）。
_Avoid_: 压力下顺手删行、把 descope 伪装成 waiver 续期、跳过披露义务

**TDQS (Tool Definition Quality Score)**:
Glama 对 MCP server 工具定义质量的第三方评分体系（ADR-0028，D-172~175 立法）——逐工具六维加权（Purpose25/Usage20/Behavior20/Params15/Conciseness10/Completeness10），服务器总分=0.7×描述质量（0.6×mean+0.4×min——最差工具被刻意放大：a single garbage definition degrades selection across the whole set）+0.3×四维 Coherence（disambiguation/naming/toolCount/completeness，tool-count 带 5分=3~15/3分=16~25）；描述↔annotations 矛盾=该维自动 1 分+公开旗标（校准纪律的自动化审计器同构：披露不足扣分、虚假披露定罪）；重评=merge to main 即触发（GitHub HEAD build，inputHash 增量继承）。本仓复合验收判据=Glama 页面字母档 A+min 逐件 ≥3.0+零 annotation contradiction 旗标（三要件全锚 TDQS spec 官方成文条款——字母档 A=≥3.5、「tier-B passing bar」逐件 ≥3.0；D-182 重校准，stretch=3.8 系本仓自设推断非官方阈值）。
_Avoid_: 为分数做 API breaking（P3 合并须独立立项+semver major 裁决（D-179 注记：本轮推迟真理由=D-094 异构合并禁令+在飞批撞车+gate 三态清账带宽，非 semver 成本——0.x breaking 合法））、虚假披露、关键词堆砌、always-call-first 强制排序句式、本地复现分入 CI（生产 LLM 型号/温度未公开）

**Boundary: 行**:
工具描述内嵌的单向消歧惯例（D-174，扩用 introspect_tools scene_nodes 既有先例）——每件工具一句 `Boundary:` 点名 1~3 个兄弟分工（when/when-not 语义，TDQS Usage 维 5 分锚点）；单向点名=新工具自写边界句零改动旧件；逐件消歧矩阵=docs/adr/0028-elements.yaml 的 boundary_targets 单源；集中路由表对 TDQS 评审零贡献（Appendix B 评审输入只看 name+description），全局 instructions 仅跨模块分工用。
_Avoid_: 双向点名（O(n²) 维护税）、强制排序句式（"A boundary is describable; a priority is not"）、把消歧职责堆进集中文档不进描述本体

**要素存在性棘爪 (element-presence ratchet)**:
描述披露要素的 CI 防回归断言形态（D-175）——断言「结构化要素存在」非「关键词字面」（Boundary: 行等自造结构分隔符=合法断言对象，措辞自由度在行内容）；要素清单单源=0028-elements.yaml（ADR 立法与 CI 断言同源，防「ADR 改了 CI 静默漂移」）；hard gate 入 lint job（确定性检查无 LLM，与 check 家族并列）；失败信息须指明缺失要素 id；清单变更须 PR 显式 review。
_Avoid_: snapshot 式全文断言（合法措辞迭代误报）、词表双源、warn 级软化（Notion 实证 warnings 被无视）、改措辞顺手删断言、为存量豁免造 baseline 文件（要素全齐后无此需求）

**debt_owner（欠账责任方）**:
waiver 行 owner 字段的精确语义（D-176 拆词立法——原与「门权者」共用 user 一词致 D-168④ 条款字面自相矛盾，标 scoped revised）——=欠账责任承担方（可=user 或 env 责任面），与续期签署方权责分离（GRC「债务人不得自批豁免」普世条款：NHS 原文连最高执行官不得自批 waiver）。
_Avoid_: 与 gate_authority 混用同一词、owner 字段被读成「批准人」、续期行 owner 写成签署人

**gate_authority（门权者）**:
waiver 续期重签的裁决位（D-176）——=human user 或其具名授权代理；代理代签合法性条款=renewed_by 录三元组「代理账号+authorized by=委托人+日期」，授权链具名可追溯即担责等价（UCC §3-402 同构：intra vires 行为责任归 principal；ISC2：审批人与委托人共担）；约束精确化=debt_owner≠签署方（防自查自批），非「owner 之外的 user」。
_Avoid_: 欠账人自签、无名授权链、「authorized by human」当无凭据口头语、门审后仍走代理补豁免（车道已关）

**极性感知断言 (polarity-aware assertion)**:
mutation 类披露的存在性断言必须区分肯定/否定极性的立法原则（D-180——S-03 病灶实证：autofix_mutation 断言被否定句「without auto_fix mutations」反向满足=CI 假绿，且现行要素词表已含 5 个否定式 token，朴素守卫落地即自伤）——断言对象=肯定性披露，否定句式不充数；机制形态经 D-190 立法=子句级窗+positive_exemptions 豁免表+方向限定条款（切分符/豁免短语清单细则仍归执行窗产物）；与「要素存在性棘爪」分工=棘爪管结构化要素存在，极性感知管 mutation 类断言不得被否定句满足。
_Avoid_: 裸关键词断言管 mutation 面、否定 token 一刀切误杀自带否定的正确披露（"cannot be undone"/"does not modify"）、把未实测正则形态钉进立法、个案修复不留原则致下一件 mutation 断言复刻盲视

**锚点形态白名单 (anchor-form whitelist)**:
证据指针可接受形态的优先级序（D-183——P-03b 行号漂移一般化：裸 path:line 每次编辑即失效属结构性缺陷）——pytest node ID＞path::symbol（AST 符号存在性抗漂移）＞sha:path:line（commit 钉死断面）＞裸 path:line；裸 path:line 在版本化文档=warn 提示升级的合法临时态，.scratch 过程报告中合法形态；白名单为单源机读件，机检脚本与文档模板双消费防双真源；「可执行证据指针」词条的形态细则由本单源细化（D-082⑦ scoped revised 随机件批落地）。
_Avoid_: 裸行号当永久锚、模板与脚本各执一套形态表（双真源）、无 expiry 锚长期挂 warn（warnings 被无视反模式）、对 .scratch 面装机检（CI checkout 不可达 gitignored 文件）


**验收门/stretch 双轨 (committed gate vs aspirational stretch)**:
验收判据的双轨表述纪律（D-182）——验收门锚官方成文语义（Glama 字母档 A=≥3.5 spec 正文+min 逐件≥3.0 tier-B passing bar+零 contradiction 旗标），stretch=本仓自设推断目标（3.8=乐观推演值）如实标注自设非官方；判据修订走重基线惯例（原判据保留在 ADR 被否选项/修订注记不删=多基线供审计，GM-22-001 同构）；stretch 值禁入对外宣称文本（D-149 校准宣称）。
_Avoid_: 推断值立为验收门（已知不可达的门逼出未来口径游戏）、门与 stretch 混写不分轨、对外宣称引 stretch 值充门、为达标缩门静默改写（须走 revise 呈报+披露义务）

**判负决策树 (fail-branch decision tree)**:
验收门判负处置的预注册分流表——按可观测信号分四支定向处置，立法须先于判负时刻。机制细则见 ADR-0028（D-188）。
_Avoid_: 判负当刻现编归因、把结构性判负包装成执行质量返修、绕过决策树直接触发 P3 解冻（判定权归门审）、拿决策树当缩门工具

**前置议题登记 (pre-agenda registration)**:
终审事件前置议题在立法面注册的骨架形态——议题槽+判据引用+触发条件+证据占位，零结论禁预判决。机制细则见 ADR-0023（D-192）。
_Avoid_: 骨架内预写结论或判决倾向、拿占位符当证据本身、议题放瞬态载体随批遗失、召集窗不重算材料直接拿登记时快照判案

**发版车 (release train)**:
发布动作的「有车即搭」心智模型（D-187——semver §7 批量取最高跳档+changelog 即发版触发器：Unreleased 节有实质条目即装货完毕该发车）——发版位=既有纪律的人工确认门非编排裁决面；发版密度受 PyPI append-only 对单次发版的质量门约束而非频率上限（D-093）；文本专列=无实质内容的为文本单发版，被 D-175③ 禁止，与「实质批搭车」合法形态区隔。
_Avoid_: 为文本单发版空驶、实质批攒批不发致门状态悬空、把发版车等当审批门（判据仍是 preflight+人工确认门）

**枚举完整性断言 (enumeration-completeness assertion)**:
注册面与断言面逐键对齐的覆盖率检查（D-195——Sonar coverage-on-new-code 分档同构）——注册表（TOOL_ANNOTATIONS）每个键必须出现在要素登记（0028-elements.yaml）中：挂要素或显式 exempt+理由字段，缺席即 CI 红；与质量类断言分工=完整性断言只管「在不在册」不管「写得好不好」。_Avoid_: 豁免当免责出口随手用（缺席=疏忽非决定）、注册面换载体后断言锚点不同步、把质量维度检查塞进完整性断言

**推导最低集 (derivable minimum set)**:
按签名可机检事实推导的必挂要素下限（D-195）——带 session_key→session_prerequisite 必挂、有功能兄弟面→boundary_line+boundary_targets_named 必挂；映射表落代码内常量+测试走 PR 评审不落版本化文档（D-191① 缓解）；增量立即生效、存量限期收敛只收紧不放松。_Avoid_: 映射表写成新立法文档（撞裁决面禁令）、存量无限期豁免（Sonar new-code 盲区教训）、覆盖率层引入极性盲断言（mutation 工具沿用极性感知）

**判据集 (reference corpus)**:
三轨语料中唯一合法的 FN 分母轨（D-198——ConText/eds-nlp 人工标注判据集同构）——手工合成标注+每条一句可独立辩护理由；轨①真实语料管 FP/赦免面、轨③程序化变异集只报绝对数与构式覆盖永不作分母（变异样本人工确认真否定后可晋升轨②）。三轨分报、数字禁同现一个比值。_Avoid_: 拿生成规则自证的变异集当分母（分母污染）、三轨合并成单一比值、判据集条目无可辩护理由

**设计已知限制 (known design limitation)**:
按设计有意漏判的跨子句否定类样本的计数归宿（D-198）——与真 FN 分桶单列，不阻断 hard 化但须在判定输出显式区分；是「机制按立法语义忠实执行的结果」非缺陷。_Avoid_: 混进 FN 分母（三轨下最隐蔽的污染路径）、记成缺陷逼机制越立法面

**开火 fixture (firing fixture)**:
每个 polarity_aware 要素随附的否定语境样本+断言守卫产出 negated_only 的测试件（D-199——ESLint post-run 运行时断言/RuleTester 负向断言同构）——「能写出开火 fixture」与「守卫可挂载」逻辑等价，误挂靠在 fixture 构造期即不可表达=构造性排除；缺 fixture warn→error、不开火即 error；fixture⊆LEGISLATED_GUARDED 交叉断言双锚；原料复用判据集同分布语料防假绿。_Avoid_: 手工造句恰好绕过正命中路径的假绿 fixture、人工 review 升格回主强制面（已实证漏过一次）、拿正则纯度 lint 替代行为断言

**扫描时滞伪影 (scan-staleness artifact)**:
外部目录评分滞后于源码真相的时点差现象（D-197——Glama methodology 实证：扫 git 源码 push 分钟级同步、commit 驱动全量重扫、inputHash 按定义变更重评分）——评分与发版/PyPI 解耦；camera_orbit C2.9=该伪影实例（v0.5.0 文本被评、main 已修复、随下次重扫自愈）；与发版滞后归因分工：时滞在扫描管线不在发布车。_Avoid_: 把时滞读数当源码缺陷再修一遍、拿发版当重评触发器（解耦）、超合理时滞不动仍不查 last-scanned/issue

**缺口披露节 (gap disclosure section)**:
ADR 内常设的 known-gap 注册表宿主章节（D-200——ADR-0028 缺口披露节为首例，「找家≠新建房产」）——每条记「缺口+影响面+兜底」三件套，账本债行作指针；不新建独立注册表文件（D-191① 不新增版本化裁决面）。_Avoid_: 另建注册表文件制造第二真源、把披露节写成运维手册、缺口结案后删原文（留结案注记保审计链）

**判决承载标签 (adjudication-bearing label)**:
判定强度地板（MIN_ADJUDICABLE）的合法计数基（D-203）——只数能产生「标签 vs 守卫」分歧的语料标签子集（true_negative+forgiven）；known_limitation 类按设计永不进判决故从地板剔除（KL 充数=稀释判决强度=同函数内分母排 KL 而地板含 KL 的内部不一致病灶）。_Avoid_: 地板口径与判定口径不一致、拿不计判决的标签凑样本量达标

**候选 pitch 登记 (pitch registration / observe state)**:
范式级方案「本轮不 bet 但保留回归路径」的登记形态（D-208——Shape Up fat-marker 心智模型）——载体=ADR-0023 前置议题登记块紧凑条目（≤5 行：动机+不-bet 理由+re-shape 触发条件+证据指针占位）；措辞=observe 态「本轮未 bet」禁拒绝/否决字样；登记面寿命=与登记所锚事件同寿（各自标事件锚：v1.0.0 门审/0.7+ shape 轮/fail-loud 立项事件，非一律与门同寿）。_Avoid_: 登记块写完整论证（属触发成就后 shaping）、observe 条目被误读为永久关闭清单、非机检触发条件不挂 preflight 呈报义务行（不假装有在看）

**机制 vs 范式分离 (mechanism-vs-paradigm separation)**:
竞品/外来方案采纳裁决的第一过滤判据（D-207/208/209）——先问「这个东西在其原生架构里为什么存在」，答案若指向我方不存在的架构前提（网关/机器级注册表/渐进加载面）则机制即 reject、仅架构无关的范式可议；reject 交付物与 pitch 登记范式回归路径可共存不矛盾（宾语不同层：机制层 vs 需求层）。_Avoid_: 绑定不存在前提的机制照搬、不带触发条件的裸 reject（永久失明）、把伴生面当独立候选捞起

**结案判定词 (closeout verdict)**:
审计/评审发现项的结案标签三态（D-210——ISO 9001 APG/SOC2 evidence-vs-documentation/PMBOK assumption log 判据面对号）——verified-fixed=纠正+原因分析+防再发三证据齐；no-actionable=决策级层已 canonical 无须新增面（触发性未来需求以触发条件债形态登记非口头备注）；open-assumption=未验证推断登记+触发式验证协议+显式出口锚（两窗未复现→「维持未验证」显式关闭不无限挂）。_Avoid_: 判定词混用（open-assumption 写成 wontfix）、结案不留证据指针、非缺陷类发现新增裁决面（D-004/065/122 先例=登记级归置）

**oracle 锚定 (oracle anchoring)**:
判定面期望值与测量值同源流动的失效形态（D-212——arXiv 2608.17214「specification-anchored vs state-anchored」分类学）——期望值若从被测系统自身传递性流动，故障同时移动两侧使比较精确抵消、闸结构性不可能红；立法对治=期望值必须由被闸对象之外的真源锚定。_Avoid_: 闸内嵌治理常量自证、spec 文件镜像对象实况造第二真源、把「期望值有出处」当充分条件（出处须独立于对象）

**反事实钉 (counterfactual pin)**:
判定闸的腐坏实验件（D-213——ESLint RuleTester 断言力条款/OPA --fail-on-empty 同构）——注入变异使被闸对象故意变坏、断言闸返回红（main()==1），与 live-green 阳性对照成对；唯一默认形态=进程内 pytest 钉（monkeypatch 常量→tmp_path 腐坏副本），命名 test_*_fails_the_gate+counterfactual docstring 与非空红断言义务（此三要件经 ADR-0030 明确为**反事实钉专属**类别义务；D-225）。_Avoid_: 只跑不断言的空壳钉、钉存在但 CI 不跑（回永绿）、fixture 文件库默认化（无执行不证伪）、拿闸代码变异当钉（错层）

**治理常量 vs 机制常量 (governance vs mechanism constant)**:
外部派生义务的判别二分（D-214——12-Factor config litmus 变体+reversal test）——会因治理决策变化而变化的值=治理常量须迁出脚本入外部真源、其修改须 PR 审计回滚；只影响闸如何检测/不影响判定什么是对的=机制常量豁免迁出、随 code review 直接改。_Avoid_: 拿「常量都该外置」一刀切（机制参数外置=维护噪音）、治理常量藏脚本内自证、baseline 文件手写维护数字（其=上一帧真源存档每次重生成）

**规范性宣称 vs 时点记录 (normative claim vs point-in-time record)**:
文档计数/枚举宣称的机检判别四问（D-216——SpecWeave living-docs/ADR-supersede 分层同构）：读者此刻依赖它为真？代码变更使其失效？描述当时状态随版本冻结？未来有人以它为据复述为现在事实？——规范性宣称须机检载体、时点记录豁免但须时点戳防复述、转引已机检文档豁免、灰区改写规范表述（≥N 或指派生页）。_Avoid_: 机检历史快照（语义错配）、无家宣称入库（新宣称写时须指定 canonical+核验方式）、denylist 缺位（历史漂移旧串回流）

**钉登记表 (pin registry / gate registry)**:
钉覆盖率的守护载体（D-215——CODEOWNERS 人编登记/fail-on-empty 零钉防护先例）——.github/gate-registry.yaml 人编条目（gate_id/bound_class/pin_node_ids/expected_source/exemption{reason,expires|issue}）+一致性闸五判红（未登记/pin node 不存在/stale/新闸未登记/豁免过期）；登记的是纪律非有效性，空壳钉残余盲如实披露不假装机检全能。_Avoid_: 登记表由闸或脚本生成（自证）、豁免条目无 expires/issue（永久后门）、expected_source 做强语义校验（越界进实验轨职责）

**终止层 (termination layer)**:
「谁钉钉者」递归的承认性收束（D-213——Matryoshka 论证）——登记表一致性闸自身的钉=喂腐坏登记表→红，其内容正确性由普通 code review 守护、终止于人类层，不再加第四层机器闸；承认终止层比假装无限机检更诚实。_Avoid_: 给登记表闸再配机器闸（伪递归）、把终止层当缺陷隐藏而非立法披露

**live 对照钉 (live-green control)**:
反事实钉的阳性配对（ADR-0030——ESLint RuleTester valid 轨禁带 errors 属性同构）——在真仓库上跑闸断言当前绿；保留描述名（建议 *_is_green|_passes|_holds|_consistent 收敛面）且**禁 fails_the_gate 后缀**（套错后缀=形式撒谎，R46 审计 M1 实证）；可入 pin_node_ids 但不满足「≥1 反事实钉」义务。_Avoid_: 把对照钉注册成反事实钉（假覆盖）、给对照钉补 counterfactual docstring（文档撒谎）

**性质断言 (property assertion)**:
规格陈述型回归测试的类别——验证脚本文档化性质（幂等/格式/容差），非反事实钉也非 live 对照；不占 pin_node_ids。机制细则见 ADR-0030。
_Avoid_: 性质断言占 pin_node_ids 充钉数（登记表看起来有钉实则无可判红者）、因迁出而删测试（迁出≠退役）

**交接载体 (handoff carrier)**:
交接文档的法定落点条款——一切交接文档必须落 .scratch/{slug}/handoffs/，%TEMP% 及仓外路径禁用；跨轮决策内容折叠进 ADR/CONTEXT.md 后交接文件允许不版本化。机制细则见 ADR-0030 §6（D-226①）。
_Avoid_: 交接落 %TEMP%（跨会话失效已致一次真实丢失）、为交接文件开 .scratch 版本化（commit 噪音）、把交接当第二真源不复读（承载内容须折叠进 ADR/CONTEXT.md）

**孤儿钉 (orphan counterfactual pin)**:
fails_the_gate 钉落在无 bound 对象对应测试文件内的披露类残余盲——R8 只扫 bound 对象测试文件属刻意克制，如实披露不假装机检全能。机制细则见 ADR-0030（D-227④）。
_Avoid_: 把孤儿钉当机检能抓的缺陷报告（边界外）、为堵此洞放开 R8 全扫（误伤面大于捕获面）

**否决权判据 (veto-power criterion)**:
通则义务级的分级标尺——握红/绿否决权的守卫必须证明自己会红；无否决权的探针降级为敏感度实验。机制细则见 ADR-0029（D-212）。
_Avoid_: 给无否决权对象立红/绿义务（形态错配）、探针 verdict 隐性变门禁（漂移不立界）

**双层时间戳判据 (two-layer timestamp criterion)**:
异步外部评分的验收读数定义——聚合徽章 Scored>T 且该工具评估块时间戳>T 两层各自卡住。机制细则见 ADR-0028 §5（D-220）。
_Avoid_: 拿混合时点读数下结论、追全 A 对不透明评分器方差过拟合（Goodhart）、无 T+72h staleness 兜底使观察窗无限挂

**收录面义务行 (distribution-surface obligation line)**:
release-preflight 第 10 行人工对照 distribution-surfaces 可行动表逐行核销的义务行——结局二值=执行(附证据)/延缓(附理由+复审日)。机制细则见 ADR-0023（D-229）。
_Avoid_: 义务只写契约文档不挂清单行（v0.6.0 重 publish 漏执即此形态）、push-CI 断收录完成态（D-164β 空转）、延缓无复审日（变相永久豁免）

**观察期判据 (observation-window criterion)**:
新闸 ::warning::→blocking 观察期的启用判据——存量违例=0 可直接 blocking 但须显式声明偏离理由。机制细则见 ADR-0029 §5（D-230）。
_Avoid_: 无存量违例空跑观察期（无对象空转）、跳观察期不声明（静默破例腐蚀默认值权威）
