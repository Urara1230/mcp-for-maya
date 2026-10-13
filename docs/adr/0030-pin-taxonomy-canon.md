# ADR-0030 — 钉类别法典：反事实钉 / live 对照钉 / 性质断言的三类分立与形态机检

状态：accepted（grill 定稿）
日期：2026-10-05
决策来源：D-223..D-227（R47 立法轮五裁决）
证据面：R46 审计 M1（ADR-0029 钉形态条款立法后首批 6 个新钉无一遵守=「规则只写不查」第四次复发）+登记表机器复算（17 条目 45 pin_node_ids 实分三类混装、仅 4/45 合规范式）+ atomcode 三轮调研（ESLint RuleTester valid/invalid 义务分立 RFC-2021-stricter+#18960 / codeowners-validator 一表一验证器可扩展 checks / knip 双解析器盲区反例 / Notion eslint-seatbelt 棘轮豁免燃烧 / ESLint bulk suppressions 与 #21007 baseline 模式 / Stryker 变异测试定位+Just et al. FSE 2014 / Khorikov 命名法典只在有机器消费者时强制 / nzakas warning-console-noise 判词+typescript-eslint v6 整体翻转 / HashiCorp Sentinel policy-set 级 enforcement / AWS OPS07-BP03 runbook 版本控制+mattpocock/skills #596+#272 交接载体同形事故 / AWS ADR supersede 惯例 / idvorkin 收敛协议+SEBoK exit criteria+Checklist Manifesto abnormal 三件套）

## 背景

ADR-0029 §3 立法「钉=单一形态 test_*_fails_the_gate」，但登记表实证 pin_node_ids 混装三类义务完全不同的测试：注入故障预期变红的反事实钉、验证真仓库当前绿的 live 对照、规格陈述的性质断言。单一法典套三类对象=结构性不可执行，首轮落地即复发。本 ADR 把「pin」的类别语义立法，并把形态纪律从文档条款升为机器判红。ADR-0029 §3 的进程内 pytest 形态、断言力条款、递归终止三件套仍为本法典基底；单类→三类系语义变更按不可变纪律新开篇（AWS ADR supersede 惯例同构），ADR-0029 仅改指针。

## 决策

### 1. 三类法典（D-225①）

| 类 | 命名法典 | docstring | 断言义务 | 登记义务 |
|---|---|---|---|---|
| **反事实钉** | test_*_fails_the_gate | Counterfactual 声明（故障注入描述+Non-empty red 注记） | 非空红断言≥1（AST: assert/pytest.raises） | pin_node_ids 主力——bound_class=gate 须≥1 枚此类 |
| **live 对照钉** | 描述名（建议 *_is_green|_passes|_holds|_consistent 收敛面）**禁 fails_the_gate 后缀** | 无强制 | 真仓库断言绿 | 可入 pin_node_ids 作阳性对照 |
| **性质断言** | 描述名 | 无强制 | 规格断言 | **不入 pin_node_ids**——归普通测试，登记语义纯化为「pin=能判红者」 |

命名承载类别（ESLint RuleTester valid/invalid 双轨同构：invalid 轨强制 errors≥1、valid 轨禁带 errors 属性）；类别从名派生禁声明字段（pin_kind=第二真源违 D-214⑥）；instrument 敏感度轨不并入本法典，仅守全局下限「禁钉无断言」（给无否决权对象立红/绿义务=D-212 负向①明禁形态错配）。

### 2. 形态机检（D-225②）

判红面扩展 .github/scripts/check_gate_registry.py（codeowners-validator 一表一验证器+checks 可扩展先例；独立 check_pin_form.py 否决=双解析器漂移，D-216 同题已裁）：

- **R7 形态判红**（仅 bound_class=gate 条目）：反事实钉验三要件（命名后缀+Counterfactual docstring+AST 非空断言）；对照钉验反向（命中 fails_the_gate 后缀=形式撒谎判红）；
- **R8 反向判红**：bound 对象对应测试文件内的 test_*_fails_the_gate 未注册→红；**只扫 bound 对象测试文件不扫全 tests/**（codeowners notowned 克制——普通业务测试内同名者非本闸义务面）；
- 本闸自身配反事实钉（喂空断言/错误命名/未注册钉→红，落 R8 覆盖范围）。

### 3. 存量迁移（D-225③——D-221 分类模式）

- ~24 反事实钉（*_fails* 描述名）改名 fails_the_gate+补 docstring+registry 同步=单机械批；
- ~8 性质断言迁出 pin_node_ids=registry 编辑随批；
- 迁出后归零条目新写反事实钉；当期无法配齐者走豁免燃烧清单——exemption 条目必带 expires|issue（ADR-0029 §5 既有条款），**expires 默认值=事件锚定**（next release-preflight 触发到期重读，与 D-208 事件锚同寿+preflight waiver 重读纪律同构），per-entry override 允许；
- 新钉立法即全额合规。

### 4. 豁免到期处置（D-227②——菜单立法）

过期闸 finding 由执行窗 agent 提呈处置建议、批准权归用户（单维护者现实不虚构评审委员会）：

1. **补钉回注册**——豁免自然消亡；
2. **续期**——新 expires+新 reason+复审签认（D-168 纪律沿用，禁无到期续期）；
3. **退役 bound 对象**——闸降级即 registry 条目随 PR 删除（控制映射不到需求=候选删除，AWS coverage 惯例）。

### 5. 翻转时序（D-226②）

R7/R8 随 checker 单翻（typescript-eslint v6 整体翻转+Sentinel policy-set 级 enforcement 先例；per-rule tier 否决=新增状态面）：翻转条件维持 D-222⑤（#72 后首个正常 PR 周期走完+覆盖自查+小 commit）；**前提=R7/R8 落地 PR 内配齐自身反事实钉**，未配齐则回退独立观察一周期；豁免条目机制为假阳性兜底。

### 6. 交接载体（D-226①）

一切交接文档（grill/audit/exec/handoff）必须落 .scratch/{slug}/handoffs/，%TEMP% 及仓外路径禁作交接载体；skill 默认（%TEMP%/opencode）对本仓显式无效——工具默认与仓内惯例冲突时本仓惯例优先（AWS OPS07-BP03 runbook 版本控制+mattpocock/skills #596 同形事故先例）；跨轮决策内容按 fold-then-delete 惯例折叠进 ADR/CONTEXT.md 后交接文件允许不版本化（.scratch 不版本化=跨机器存活换 commit 降噪的明示取舍）。

### 7. 残余盲披露（D-225⑥/D-227④）

- **形态≠效力**：AST 验的是形态纪律（命名/docstring/断言存在），不证明注入真会让闸红——断言力归变异测试季度抽样域（Stryker 定位，ADR-0029 §3 已立）；变异测试自身有 crash-kill 残余（ISSTA 2023）；
- **孤儿钉盲点**：R8 只扫 bound 对象测试文件——落在无 bound 对象文件内的 fails_the_gate 钉机器不可见；刻意残余盲非缺口（补闸=过度机检违克制条款），如实披露；
- **ADR-0029 负向清单仍活**——本 ADR 引用不复述（D-183 防双真源）：禁钉无断言/禁豁免无 expires|issue/禁 warning 永久化等条款继续有效。

## 负向清单（本 ADR 汇总）

禁 pin_kind 声明字段（第二真源）；禁两分类并入（边界含混）；禁独立 check_pin_form.py（双解析器漂移）；禁永久豁免（Graveyard 反模式）；禁 R8 扫全 tests/（过度机检）；禁 per-rule severity tier（新增状态面）；禁纯日期默认 expires 与纯 issue 绑定（churn/变相永久）；禁交接文档落 %TEMP%/仓外路径；禁 ADR-0030 复述 ADR-0029 负向条款（双真源）。

## 证据薄弱处（诚实标注）

- 钉形态一致性闸无直接公开先例——ESLint RuleTester 义务分立为最强同构先例，本仓原创外推；
- 豁免到期处置菜单+owner 立法无逐字先例——Spacelift expires 模板只管书写不管处置，本仓自设；
- 「立法批次大小」无定量先例——LSC sharding/jacobian RFC 批评/AWS blast-radius 分层系同构外推；
- 交接载体「agent 交接文件」无逐字官方规范——AWS runbook 条款与 mattpocock 案例为同构先例；.scratch 版本化取舍社区两派并存本仓自裁。
