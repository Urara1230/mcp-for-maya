# ADR-0023 — T-16f 真机窗口执行面与 0.1.2 发布链激活（T-18 spec 簇）

状态：accepted（grill 定稿）
日期：2026-09-22
决策来源：D-066（本轮范围）、D-067（T-16f 执行面）、D-068（发布激活序列）、D-069（#2/#7 发帖机制）

## 背景

T-17 已实施并独立审计 PASS-WITH-NITS：`origin/main` 顶=b3f4133（T-16 spec×2+impl×6+T-17×6+dependabot mypy 合并），CI 全绿；幻影簿记自清（zz 无变更、porcelain=0）；上游六 issue 评论数实测 0（人工门零越界）；PyPI 仍只有 0.1.0/0.1.1 未 yank。审计新增登记债 N7/N8/O1/O2。**关键状态位移：Maya 2024(24.0.0.4640) 已开机**——PID 18236，userSetup.py 开 `:7001 sourceType=python` LISTENING——T-16f 真机验证窗的硬阻塞解除，D-049 门从"无限期挂账"变为"本窗可满足"。

## D-066：窗口开启合并轮

Maya 开机激活整条阻塞链，本轮同裁：T-16f 执行面 + 通过后的发布链激活序列 + N7/N8/O1/O2 归置 + 0.1.0 yank 补裁 + #2/#7 发帖机制。依据=同一棵决策树：验证一过，发布/yank/回应依次激活，逐项临场裁会在窗口里卡死。

## D-067：T-16f 执行面（atomcode 调研修正版）

1. **场景处置=只读探针先行**：窗口第一步 `file(q=True,modified=True)`+`file(q=True,sceneName=True)`——场景脏则**停手报用户三选一**（存盘后跑/授权丢弃/中止窗口），干净才 `file(new=True,force=True)`。VP2 批走"干净场景+临时 namespace"组合（namespace 不替代新场景——undo 栈/selection/shader 绑定仍污染——但适合 currentTime 净零这类纯读微注入）；run 开头幂等扫残（临时对象记 namespace 全名）。**实证依据**：dcc-mcp-maya #240/#255 dirty-scene guard 先例——脏场景上不 force 会弹模态保存对话框**永久挂死 UI 线程**（等价 server 死亡）；`file(new,force)` 于脏场景=静默丢未保存工作，社区公认危险操作；直接跑用户场景零信源支持（"测试运行者的场景是不可信状态"为业界公理）。
2. **MEL 对照端口=全生命周期管理**：execute_code 先 `commandPort(":7002", query=True)` 探占用（被占换高位端口）→开 `sourceType="mel"`→验证完 try/finally 关闭+下次幂等先 close 再 open。**仅用 `:port` localhost 形式，禁用 `IP:port`**（官方原文：INET socket 无鉴权、命令以 Maya 用户权限执行含 `system()`；安全通告 adsk-sa-2025-0008："不需要就禁用"→用后即关、窗口越短越好）。预期 Maya 2022+ 首次外部连接弹 "Allow" 模态安全对话框——用户在场授权，属批次已知项。
3. **执行者=agent 自主+分级守卫**：pytest -m gui 全批+MEL eval 探针+teardown 活证由 agent 执行；唯二用户前置点=①脏场景命中时的处置三选一②断连窗口知情（报告显式标注）。"生成命令清单交用户手跑"零信源支持，否决。

## D-068：发布激活序列（atomcode 调研修正版）

**八步序定稿**（T-16f PASS 后触发）：

1. upstream-issue-status.md 验证列更新（stub 绿→real-Maya 实证）+ 备稿去 pending 限定措辞
2. N7+N8+O2 同窗修掉+门禁复跑（真机窗覆盖 N7 路径）
3. O1：CHANGELOG `[Unreleased]` 并入 `[0.1.2]` 订正真实日期，顶部留空 Unreleased 段（Keep-a-Changelog 惯例）
4. 备发布包：tag 说明/release notes（取 CHANGELOG 段原文——pyhf 教训：先拷贝再发防被覆盖）/yank 命令与 reason 文案
5. **人工门**：push tag v0.1.2 → release workflow → **盯 publish CI 到绿+验 PyPI 页面**（本项目 invalid-publisher 前科，此步不可省）
6. **逐版核实后 yank**（详见下）
7. 上游评论收尾（链接已稳定）
8. 可选：GitHub Release v0.1.1 加 "yanked on PyPI" 标注两边对齐

**a) N7=fix-forward 修进 0.1.2**：Go release cycle 准则——RC 签发前的 freeze 窗口内按 "low risk and high reward" 收放；本项目无 tag 无 RC 正处该窗口，~3 行+1 测试可与 0.1.2 同过真机窗（机会成本为零）。"缺陷修复版遗留同族缺陷"的 known-issue 披露负担大于修复成本；ship-with-known-issue 次优解否决。纪律=修完全套 CI+真机覆盖该路径。

**b) yank 改逐版核实制**（对我方"同带病"表述的修正）：PyPI 官方判定单位=**单个 release 非缺陷家族**——0.1.0/0.1.1 之间隔着 T-11b 等修复，须 `git show v0.1.0/v0.1.1` 逐版实证各带什么病，确认带病者才 yank、reason 写具体故障模式（attrs 先例：一句话说清故障模式）。PEP 592 语义钉死：yank 只挡非 pin 解析（`==0.1.1` 仍可装+warning）、可 unyank 回滚；**yank 只在 0.1.2 确认 PyPI 可用后执行**（防全项目无可用版本空窗）。

**R26 增补（D-107④ / D-108②③，2026-09-27 兑现）**：

- 发布清单硬核对项：`[X.Y.Z]` 节内容 == tag diff —— 节内每条叙事条目可归因到 `git diff <prev-tag>..<tag-commit>` 的文件集；对不上即不可发。
- GH Release 判别钉死：minor 版执行、patch 版依 D-084 跳过（release.yml 注释同锚；D-068 原序列文本在此修订）。
- tag 前软前置冒烟：本批新 cmds 面（T-24 = attr_meta / plug_connections / listAttr / attributeQuery）——Maya 开机则跑 `pytest -m mayapy` 冒烟；未开机则 release notes 按 D-049 档披 known-unverified。不设硬 CI 门。

**R27 增补（D-123①，2026-09-27）**：

- 发布清单硬项：v1.0.0 门前须存在 **≥1 条 drift-canary 人为复核记录**（运行结果+归因写账本，**红绿均可**——防「等红灯才复核」死角致清单项永远无法兑现）。红→人知通道=ci.yml `drift-notify` 独立 trailing job（去重 issue/续败评论/恢复自动关）；复核义务登记为 D-127。

**R28 增补（D-131/D-132，2026-09-28）——v1.0.0 门前清单**：

清单唯一真源=账本 D-131 规范化列；本块为抄写+格式化载体，清单项增删须回账本裁决（冻结语义）。

- **硬项**（全绿方可 tag v1.0.0）：
  - issue #7 十框真机验证清单全绿（D-040）
  - D-115 探针工件补采同窗（下个真机窗按 D-109 形态入 docs/evidence/）
  - D-127 drift-canary 复核记录 ≥1 条——已兑现（run 36334876189，workflow_dispatch 首燃全绿）；完成条件另含 cron 对照核验（schedule 事件链首验）
  - 「`[X.Y.Z]` 节内容 == tag diff」硬核对（D-107④）
  - 版本阶梯履行：0.4.0 升 `4 - Beta` → 1.0.0 与 `5 - Production/Stable` 同 commit（D-032 阶梯履行非修改；0.4.0 发布计划调整已单独拍板）
  - GitHub Release 依 D-084/D-108 判别（minor 建、patch 跳过）
  - milestone 归置（D-130 执行面，已执行：v1.0.0←#7、v1.x←#31/#6/#4/#3）
  - public-API 冻结声明（README Versioning 节一段：public API=MCP 工具名+IO 形状+双层错误契约+annotations 语义；additive→minor、breaking→major；不立 snapshot 新机件）
  - D-036⑤ main 分支保护：入硬项执行（gh api PUT，外部动作须用户确认）或显式豁免写理由
- **软前置**：T-24 mayapy 冒烟；未跑则 release notes 按 D-049 档披 known-unverified
- **一次性过堂行**（发布面卫生核验，结果落账本行或 docs/evidence/）：
  - 断链全扫 / 陈旧宣称清点 / 实验态措辞核对
  - CVE-2026-66004 对本仓 asset_import 路径适用性核验（引用前必核）
  - twine check（D-080⑤）
- **观测项**（非 blocker，显式入账防静默收窄）：
  - drift schedule 事件链首验——2026-09-28 06:37 UTC 后 `gh run list --event schedule` 非空+结果归因入账（静默未触发→升级方案=外部 cron 调 dispatch API）
  - notify 红路径真实验证（自然真红免费闭环；禁制造红）
- **裁出**（触发态均否，2026-09-27 核验）：D-102 / D-104 / D-118
- **时序**：0.4.0 先落地（4-Beta）；1.0.0=0.4.0 之后下一 release，不插 0.5.x（真机窗出 feature 级 blocker 除外）；gate 核对时点=0.4.0 发布后
- **go/no-go**：核对产出=账本行逐项挂证据指针，用户拍板

**R30 增补（D-142β，2026-09-28）**：

- 发布窗 preflight 第 6 行：release body 披露附录断言——`python .github/scripts/check_release_appendix.py <tag>`（弱断言=附录节存在+非空+引用 #7；#7 open⇒义务生效、closed⇒义务解除，不数 `- [ ]`；fail-loud 三显式报错：gh 不可用/HTTP 非 200/rate-limit 403）。人工执行项，不进 push CI；断言脚本随 pytest 回归（tests/test_check_release_appendix.py）。

**R31 增补（D-145β/D-146，2026-09-29）——发布后段（一次性核销）**：

- 唯一真源=账本 D-146 行，本块为抄写+格式化载体（对齐 R28 块措辞纪律）；渠道处置查询面=docs/distribution-surfaces.md（D-145α）。
- Registry publish 动作完成后 30 天，一次性核销 mcp.so/PulseMCP/VS Code gallery 收录态（核验「Registry 同步已发生/未发生」并按 docs/distribution-surfaces.md 三态落账）；D-147 Cline 信号核验并入同窗顺带执行（共用观测窗零额外成本）。
- 起算点=Registry publish 日期落账日，非发版日；与 v1.0.0 硬项区隔——硬项 gate 核对在 tag 前，本行核对在 publish 后，时点不同互不阻塞。
- 逾期未核销=债文标已触发未兑现；非 CI 项无探针步（一次性核销≠持续监控，D-127/D-129/D-135③/D-137 立法史同源）。

**R32 增补（D-150，2026-09-29）**：

- D-146 起算锚实际化=**0.4.1-publish**（承接 D-150① 发版序裁决：下次发版=0.4.1 patch 先行、走完整 D-084 patch 纪律跳 GH Release；唯一真源仍为账本 D-146 行，本块为操作提醒载体）。
- tag/Release 前向立法（D-150③）：自下一 tag 起统一 **annotated**——人工 `git tag -a` 建锚、GH Release 挂已存在 tag 不反向生成（git manpage「annotated=release」+GitHub REST tags API 只收 annotated；v0.2.0+ lightweight 系 GH Release 功能自身工具成因非纪律疏漏）；Release 标题=裸 `vX.Y.Z`；存量 tag/标题不回填（D-080/D-108① 冻结同源）。
- 0.4.1 发版窗 preflight 两行：PyPI 双车道复核见 0.4.1（pip 车道+探针车道各带时间戳，T-30i/D-142γ 同型防 CDN 分裂致 D-146 锚悬空）；0.4.1 wheel 的 README 须含 `<!-- mcp-name: -->` 标记行（Registry publish 硬前提，D-143）。

**R34 增补（D-163，2026-09-30）——0.5.x 插入与 v1.0.0 门三态化**：

- **0.5.0 插入依据**：非援引本 ADR R28「真机窗 feature 级 blocker 除外」例外——框2 VP2 红经 Chromium 判定矩阵（severity 中×prevalence 站特异，playblast workaround 存在）判非 feature-blocker。新依据=真机窗成果兑现义务+轻叙事轨解锁+D-143α 收录动作绑发版窗。
- **v1.0.0 门判定形态修订**：#7 十框「字面全绿」（D-040）→ 三态制「Pass 集全绿 + Waived 集逐行 reason+owner+expiry（fail-closed，到期未消解转 Blocked）+ Blocked 集=空」；机检 waiver 清单=docs/evidence/gate-waiver-list-1.0.0.json，每次 release-preflight 重读。D-040 标 revised 保留原记录。
- **gate review 事件锚点**：= 本增补落地后首次 gate 核对执行时点（非编排轮开启时点）。

**R36 增补（D-167~171，2026-09-30）——v1.0.0 门两事件制与豁免生命周期立法**：

- **两事件制**：门检（gate-check）=release-preflight 窗内对 waiver 清单重读+逐行三态重算的幂等测量（可多次、每次落账、不预判只落真实读数）；门审（gate review=γ-B 终审）=烧债完成度触发的 go/no-go 判定事件。D-163 γ-B 锚经 D-168 修订指向门审（原行保留 scoped revised 指针；waiver JSON 两类 expiry 分层早于措辞合并存在=清单立法本意即两事件）。
- **到期行三车道**：消解→pass（挂证物）/未消解→Blocked/仍需豁免→显式续期。续期五要件：owner 之外门权者重签+新 expiry 事件锚（下次门检/门审/env 可得三选一，禁日历悬挂）+每行上限 2 次（第 2 次须用户拍板，第 3 次不存在）+reason 重验（不重验无效）+门审后豁免车道关闭；清单行 renewals 字段机检（0/1/2）。
- **排程**：首次门检=下一执行窗开场即跑（框1/4/2b/2c 四行到期当场处分=预判续期 renewals:1，reason 重验+新事件锚 expiry+user 重签）；复跑=债消解信号对号触发（限 expiry 写明该锚的行，同窗幂等去重）+每 release-preflight 强制重读；防惰性兜底=持续不开执行窗则下次 preflight 窗内强制补跑。
- **召集序列**：agent 检出「Blocked 空+Waived 全有效」快照→以快照工件为召集凭据呈报→user 召集终审（检测归 agent、召集与裁决归 user）。
- **判负三轨**（D-171）：当期不发布+案卷落账；re-review 凭新健康快照重召集（凭据=清解证据非日历非原判负件）；descope=门本体修订走 revise 呈报+披露——豁免车道与缩门车道永久隔离，任何时点禁静默缩门。
- **#7 关闭判据**≡v1.0.0 三态门判定通过（等价关系入本清单防双轨漂移，D-167ε）；本轮不裁 #7 关闭动作本身。
- **披露**：续期上限 2 次=GRCOPILOT 惯例级非 ISO/SOC2 规范级；「门审由烧债完成度触发」=NASA 分级链+D-167β 同构推导非直接工业同名先例。
- **R38 注记（D-176）**：本块及 D-168~171 中「owner 之外门权者（user）重签」之 user 义=gate_authority（门权者=human user 或具名授权代理，代签须录 renewed_by 三元组）；waiver 行 owner 字段=debt_owner（欠账责任方，数据语义不变仅获术语名）；D-168④ 措辞位已标 scoped revised，词条见 CONTEXT.md。

**R41 增补（D-187/D-189/D-191/D-192，2026-10-01）——0.6.0 车选、expiry 复核规程、门测量口径与门审前置议题登记**：

- **0.6.0 minor 车选（D-187）**：下一发版=0.6.0 minor——gr→P-A→P-B 三独立 PR（D-186）全合+CI 绿+本 ADR preflight 清单全过+人工确认门点头后切 `v0.6.0`；annotated tag+GH Release 页义务位循 R32/D-084 惯例。定性=两个新 CI 机件（TDQS 棘爪+evidence-anchors）+披露批+修复批的 minor 功能窗非文本专列（D-175③ 标的=pure-text 专列不涉本批）；兼作「Glama 重扫源=PyPI-pull」假说的决定性自然实验（D-182③ 忠实执行）。
- **release-preflight 第 7 行（D-189）——evidence-anchor expiry 复核**：每次 release-preflight 窗内跑 `check_evidence_anchors.py` 并按锚点形态分桶——裸 `path:line` 类=设计内 advisory 永久 warn 不计误报分母（存量约 40 条登记升级债逐步消化，触发债纪律 D-122）；可硬化类（`path::symbol` unresolvable/pytest node ID/`sha:path:line` not-exist-at-HEAD）逐条人工核验分真假（`_symbol_exists` 只认顶层 def/class+一层方法，`__all__`/动态名/深层嵌套=AST 盲区→记 FP）；FP 率=FP/(TP+FP) 仅对可硬化类计；判据=零/低误报→该形态升 hard、>30%→退人工抽查+修 checker 债（D-183④ 原文）；结果=账本事件行（时间戳+逐类计数+FP率+判定）+分类工件归档 docs/evidence/。人工执行项不进 push CI——复核=measurement 非 gate（循第 6 行先例）。
- **门测量口径（D-191）**：diff 行数 canonical=numstat（--stat vs numstat 差=口径非错误）；TDQS 要素/工具计数以 `0028-elements.yaml` 解析值为唯一真值（运行快照计数差=时点差非缺陷，报告须注明快照时点）；极性零误拒语料分母=真实校验面为主测（工具↔其要素断言面）+全量 docstrings 笛卡尔积作压测面，两轨口径禁混淆；mypy `new:0` 语义=raw 过滤 baseline 后无新增（现行 raw 184 vs baseline 199，增量判新细则见 AGENTS.md mypy 实践行）。
- **gate review 前置议题登记块（D-192）**：v1.0.0 门审前置独立议题（非门清单行，D-171 隔离条款）=**P3 工具面合并复议**。判据=D-179② 原文（同构族测试=3+ 操作共享大部分参数+合并前 LLM 驱动实测选错率）；触发债三条件=D-179③ 原文摘录（Disambiguation 仍 3/5 且真实客户端实证选错 / Glama 调分带收益归零自动作废 / 工具面越 ~35 件转可用性题）；证据指针占位=Glama 批后维度读数快照+客户端实测报告+工具面计数；**骨架禁含判决倾向**——材料于门审召集窗按当刻快照重算（陈旧快照判案=门审 _Avoid_）。

- **R44 增补（D-197，2026-10-02）——0.6.0 时序与 Glama 评分链路解耦认知**：0.6.0 走串行单跳——tag 挂「R44 描述修复批（scene_measure 重写）落地」为前置，但理由=发布语义连贯性（pip 用户拿到好描述+Unreleased 不半空）非评分必需；Glama methodology 原文实证其扫描管线吃 git 源码（push 分钟级同步、commit 驱动全量重扫、inputHash 按定义变更重评分）——**评分与发版/PyPI 解耦**，camera_orbit C2.9 定性修正为扫描时滞伪影（随下次重扫自愈，不赖发版车）。排序显式化=scene_measure 重写列下一窗第一优先、落地即 preflight→人工门→tag、其他 minor 内容不插队单发；熔断=重写超 2 窗未落地则放弃等批切 0.6.0，scene_measure 随下一个含包内实质变更版本顺带发出，绝不单发纯文本 patch（0.6.1 纯 docstring patch 撞文本专列限制 D-175③）。附加行动项=核 Glama last-scanned 时间戳，合入超合理时滞仍 C2.9 则向 glama-ai 提 issue（inputHash 重扫异常可审计）。

- **R45 增补（D-204/D-208/D-209，2026-10-02）——preflight 两行与范式 pitch 登记块扩列**：
  - **release-preflight 第 8 行（D-204）——TDQS 豁免到期阻塞**：每次 release-preflight 窗内跑 `check_tdqs_disclosure.py`——存在 `current>=due` 到期豁免且未消解且未按 D-168 waiver 续期纪律显式延期→no-go。人工执行项不进 push CI（循第 6/7 行先例）。
  - **release-preflight 第 9 行（D-208）——pitch/observe 触发条件呈报义务**：每次 release-preflight 窗内人工盘点下块中非机检触发条件的达成状态（pitch③④⑤⑥+遥测 observe 项），结果落账本观察行；如实披露「无自动探针」缺口——不假装有在看。
  - **范式 pitch 登记块扩列（D-208，observe 态）**：六件竞品范式（对标本机 clone dcc-mcp-maya v0.9.33）登记为候选 pitch——本轮未 bet，每条只放可 bet 性路标（动机+不-bet 理由+re-shape 触发条件+证据指针占位），完整论证属触发成就后的 shaping 工作禁入本块；「登记面与门同寿」语义扩为「**与登记所锚事件同寿**」——各条显式标事件锚；条目措辞统一 observe 态「本轮未 bet，re-shape 触发条件=X」禁拒绝/否决字样：
    - **pitch-1 skill 包制**——动机=域级打包按需装载；不-bet=25 件包装开销>收益且无域分化；事件锚=0.7+ shape 轮；触发=工具面超 ~35 件（tool-count CI 闸可检）∨域分化实证。
    - **pitch-2 minimal/渐进加载**——动机=default_active 只读组+显式升级省上下文；不-bet=D-207① strict 旗标已取走安全默认切片、25 件全量成本有限；事件锚=**v1.0.0 门审，与 P3 议题同窗竞裁**（P3=合并减件 vs 本 pitch=分组藏件，同一病灶 Disambiguation 3/5 的两反向解，不分别处置）；触发=D-179③ 条件①原文。
    - **pitch-3 Rust sidecar 网关**——动机=进程监督+机器级注册；不-bet=其动机链全指向我方不存在的架构前提（网关/多实例注册表/LAN 面）；事件锚=0.7+ shape 轮；触发=多实例部署需求实证（须附 SessionManager 不可满足的具体场景——跨机/进程级隔离/独立失败域，D-209② 加固）。
    - **pitch-4 Streamable HTTP**——动机=MCP 官方远程传输轨；不-bet=stdio=本地一等公民无迁移压力、迁移驱动全为远程/共享部署语义；事件锚=0.7+ shape 轮；触发=远程会话/render-farm/CI 远操实证。
    - **pitch-5 dcc-mcp-cli 控制面**——pitch-3 的伴生面（无网关即无语义），**禁独立 re-shape**。
    - **pitch-6 core 共享多适配器**——动机=多 DCC 摊销；不-bet=单 DCC 仓抽 core=抽象税零收益；事件锚=第二 DCC 立项（fail-loud 事件，立项即呈报非持续观测）。
    - 遥测 observe 项（D-209③，非 pitch 不入上列）：本地审计聚合首选形态注记+触发=0.6.0 后≥1 季度真实使用窗口+「改进依据不足」具体痛点；采集面落地前须过 threat-model 披露面扩展裁决（D-030 zero-telemetry 宣称修订前提）；不引入任何当前行为变更。

**R48 增补（D-229，2026-10-08）——收录面义务行（release-preflight 第 10 行）**：

- **release-preflight 第 10 行（D-229③）——收录面义务行**：每次 release-preflight 窗内人工对照 docs/distribution-surfaces.md 可行动表逐行核销——Registry 重 publish（server.json version 须与 PyPI 精确一致）/已提交渠道合并态/Glama 页面新鲜度/触发条件类渠道信号核查；结局二值=**执行**（附证据指针）或**延缓**（附理由+复审日——PolicyTrak exception register 形态，无登记例外会静默繁殖）；落账本事件行。人工执行项不进 push CI（循第 6/7/8/9 行先例——publish 须 OAuth 人工门+PyPI 可验证时序，恒属人工；tqsystems 80/20 自动化配比先例）。
- **server.json 落仓根（D-229①）**：`mcp-publisher publish --file` 默认读 `./server.json`——循 D-150②「工具默认路径」先例（与 mypy-baseline.txt 根目录不迁移裁定同构）；版本字段随 bump commit 同步改写。此前草稿躺 gitignored .scratch/ 无版控=v0.6.0 掉义务的直接诱因（义务无断言物）。
- **版本一致性断言扩三元（D-229②）**：check_version_consistency.py 断言面扩为 pyproject==__init__==server.json 清单相等——断言对象是仓内清单（push-CI 合法），非收录完成态（D-164β 空转禁令不破）。
- **D-146 核销窗重锚**：重 publish 完成落账日=新 30 天核销窗起算点；核销动作并入同窗顺带执行（D-153 同窗零额外成本）。
## D-069：#2/#7 发帖机制

本窗口即发（备稿就绪+"≥0.1.0 已含"耐久真话不依赖 0.1.2）；执行=**agent 贴终稿→用户过目→gh 以用户账号发出**。过目为授权前置条件，发出前用户有最终否决点；其余四条（#1/#3/#4/#5）仍等 0.1.2 发布后同流程；一 issue 一评不追评纪律不变；措辞改动须回草稿重过目。

## 否决与约束

- 脏场景未授权绝不 `file(new,force)`；不生成命令清单交用户手跑；临时端口不常开、不用 IP:port 形式
- yank 不早于 0.1.2 上线；tag 之前一切可改、tag 之后只加不减
- T-16f 翻车预案：#1 探针翻车→D-059 豁免集退路如实降级；其余翻车→fix-forward 准则外延（pre-RC 窗口修而不带病发）；不可速修→门如实守住呈报用户
- 人工门全保留：tag/release/yank/#1~#5 评论发出均用户执行或过目授权
- 调研缺口如实记：live GUI session 跑验证批无权威标准文档（多先例归纳）；孤儿监听器断言无公开范本，需以 commandPort -query/回调计数自证；adsk-sa-2025-0008 全文 403 未读（摘要级）

## 关联

- 前置：ADR-0018（D-049 真机门——本轮是满足它的路径非放宽）、ADR-0021（T-16 spec）、ADR-0022（回应策略+时序——本轮激活其执行面）
- 审计残留源：docs/reference/2026-09-22-audit.md（N7/N8/O1/O2 原始件）
