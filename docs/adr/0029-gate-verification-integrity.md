# ADR-0029 — 机械核验完整性通则：期望值外部派生、反事实钉义务、人编登记表守护与裸计数宣称退役

状态：accepted（grill 定稿）
日期：2026-10-03
决策来源：D-211..D-222（R46 立法轮十一裁决）
证据面：R45 三连翻形态归因（A1=CHANGELOG 计数错致审计翻案 / N1=闸可被证据面操纵而非真对象 / D1=虚构身份自证假绿——同形=「闸存在但断言的是自己而非世界」）+ atomcode 六轮调研（oracle anchoring arXiv 2608.17214 / ESLint RuleTester / OPA --fail-on-empty / CODEOWNERS / Codecov target:auto / 12-Factor config litmus / GitLab dual-report / SRE error-budget 分层政策 / Datadog flaky 30d TTL / TestFiesta 续期上限 / 航空 NFF-CND-NTF 标签文化 / NTSB Probable-Cause-Undetermined / Stride cannot-reproduce / clockify check-docs-counts / ADR org 不可变惯例）+ 本仓惯例盘点（evidence-anchor-forms/monolith-budget/ruff-baseline/0028-elements 分散单源制 / D-183 防双真源 / ADR-0020 presence-baseline）

## 背景

三次审计翻案共形：判据面存在但断言对象错位——期望值从被闸对象自身流动（oracle anchoring），闸变红与否与对象腐坏与否解耦。本通则把「闸活着」从信仰变为义务：每个握有红/绿否决权的守卫必须证明自己会红。

## 决策

### 1. 通则本体

对一切机检判定面立法三条义务：
- **期望值外部派生**：闸的期望值必须来自被闸对象之外的真源（spec-anchored 人编文件或 object-derived 运行时派生），禁止闸内嵌治理常量自证；
- **反事实钉**：每个红/绿判定闸必须配一枚「故意改坏被闸对象→闸变红」的钉测试，且该钉必须被 CI 实际执行；
- **覆盖率守护**：钉覆盖率由人编登记表+一致性闸守护，豁免条目带理由+到期/绑 issue，过期即红。

### 2. 适用面分级（D-212——否决权决定义务级）

| 类 | 外部派生 | 钉/实验 | 备注 |
|---|---|---|---|
| check_* 判定闸 | 全额 | 全额（注入变异→闸红，CI 断言） | 钉腐化=闸回永绿，为登记表面守的对象 |
| CI workflow 断言点 | 经闸间接口径 | 钉必须被 CI 矩阵实际执行 | 「钉存在但 CI 不跑」即回永绿 |
| 探针/仪器 | 全额（治理常量迁出脚本） | 降级=敏感度实验（注入已知故障→产出非零 verdict） | 裁决权漂移条款：verdict 被下游当硬门消费→升格全责或显式标 non-gating |
| 人工清单行 | 证据指针义务 | 豁免（定义上不可自动化） | 四眼原则为唯一现实缓解 |
| pytest 套件 | 豁免 | 豁免——测试本身就是①②③的钉 | 可选范围化变异测试作健康指标 |

### 3. 钉形态法典化（D-213，类别语义经 ADR-0030 修订）

> **supersession note（2026-10-05, D-225）**：本节「钉=单一形态 test_*_fails_the_gate」被 ADR-0030 三类法典取代（反事实钉/live 对照钉/性质断言——单类法典对混装登记面结构性不可执行，R46 审计 M1 实证）。本节进程内 pytest 形态、断言力条款、fixture 例外、递归终止三件套、mutmut 定位仍有效，类别义务以 ADR-0030 为准。

- 唯一默认形态=**进程内 pytest 钉**：monkeypatch 闸模块目标常量→tmp_path 腐坏副本→断言 main()==1；与 live-green 阳性对照（真闸 subprocess rc==0）成对；
- **钉断言力条款**：钉必须携带非空红断言（ESLint RuleTester「invalid cases must have at least one error」同构），命名 test_*_fails_the_gate+counterfactual docstring；
- fixture 书面例外：仅大体积逐字节敏感整档需 snapshot 比对时准红绿成对提交式 fixture；默认禁 fixture 文件库（无执行不证伪）；
- **递归终止三件套**：人编登记表（禁由任何闸生成）+fail-on-empty 零钉防护+登记表内容由 code review 守护——终止于人类层，登记表闸自身钉=喂腐坏登记表→红，此为承认的终止层，不加第四层机器闸；
- mutmut 类变异框架=可选季度抽样深度审计，非钉形态、不进门禁。

### 4. 期望值外部派生双轨（D-214）

- **对象可派生的期望值一律运行时派生**（禁 spec 文件镜像对象实况=第二真源）；
- **纯治理常量迁分散 per-domain spec 文件**（Prettier 禁全局配置先例；禁统一 mega-registry）；
- **判别条款**（12-Factor litmus 变体+reversal test）：会因治理决策变化而变化的值=治理常量须外置；只影响闸如何检测=机制常量豁免；治理常量错了须 PR 审计回滚，机制常量错了随 code review 直接改；
- **baseline 语义条款**：baseline 文件=「上一帧真源的存档」每次重生成，禁手写维护数字；
- 迁出清单（按严重度序）：polarity_corpus_probe 的 MIN_ADJUDICABLE/MIN_TN_PER_ELEMENT→check_llms_txt 的 LINE_CAP→check_assets_append_only 的 GUARDED→check_release_appendix 的 ISSUE=7→liveness_probe 的 PROTOCOL；
- pin 登记表=索引视图，expected_source 引用 spec 文件（path::symbol 形态）不吸收其内容。

### 5. 钉登记表与覆盖率（D-215）

- .github/gate-registry.yaml **人编**：gate_id/bound_class/pin_node_ids/expected_source/exemption{reason,expires|issue}；bound 对象全集从机器源枚举（check_*.py glob+仪器清单）一致性闸只做差集；
- **五判红**：①bound 对象无登记 ②登记 pin node 不存在 ③stale 登记（对象已删）④新闸未登记 ⑤豁免过期；
- **过渡姿态**：::warning:: 一观察周期后转硬红——翻转条件=L1 合入后首个正常 PR 周期走完（diagnostic-first 先例+D-183 落地路径），禁 warning 永久化；
- **观察期判据（D-230）**：::warning:: 观察期唯一正当理由=新闸**存量违例>0** 需分期消化；存量违例=0 的新闸可直接 blocking，但落地 PR/registry 条目须显式声明偏离理由（「存量违例=0 故跳过观察期」）防静默破例——Tyler Hawkins（warn 唯一例外=存量违例清不完）/Notion eslint-seatbelt/CodeQL baseline/Prettier 四源收敛；首例=check_changelog_fixed。
- **频率下限**：钉轨每 PR；敏感度实验轨周跑+豁免抽样；
- **残余盲如实披露**：登记表机检的是**登记纪律**非钉的有效性（空壳钉可骗过登记）——有效性靠 pytest strict 语义+实验轨+code review 三层缓解，不假装机检全能；
- 覆盖率分两轨统计：判定闸钉轨/仪器敏感度轨。

### 6. 裸计数宣称退役（D-216）

- **四问判别**（规范性宣称 vs 时点记录）：读者此刻依赖它为真决策？/代码变更使其失效？/描述「当时」状态随版本冻结？/未来有人以它为据复述为现在事实？
- 规范性宣称→须机检载体；时点记录→豁免但须时点戳防复述；转引豁免（引用已机检文档的宣称免机检，单源原则）；灰区宣称→改写为规范性表述（≥N 或指派生页）；
- 载体=统一 check_count_claims.py 入口+per-family deriver 内分发+denylist 层（历史漂移旧串进 denylist 防回归）；禁声明式 claims-registry（第二真源+mega-file，clockify 源码自证其坑）；
- 存量迁移：11 checks/5 dimensions/8 shot types 三族上机检；CHANGELOG 类时点记录豁免+时点戳；
- 守护面含 docs/guide/*.md（公开面规范性宣称同责）；
- **写时判别纪律**：每新增一处数字宣称须同时指定 canonical 与核验方式，否则写作时拒收（「宁可少一个当前宣称，不可多一个无家宣称」）。

### 7. 载体分工与不可变立场（D-217）

- 本通则载体=本 ADR 专篇；AGENTS.md 收指针行；CONTEXT.md 收新术语；decision-ledger 收裁决记录（注：账本已于 2026-10-14 治理庭退役删除，裁决记录改落 ADR/CONTEXT.md，原文见 git 历史）——四载体各单一职责指针化防双真源；
- **不可变立场注记**：ADR GitHub org 自承「immutability 理想 vs mutability 实战更好」的社区分歧存在——本仓按不可变纪律运作：已接受 ADR 不原地改实质决策，方向变化新开 ADR 并标 revised 双向链接；增补仅限澄清/新信息/扩范围（amend），不得借 amend 嫁接异域决策。

## 负向清单（本通则汇总）

禁闸内嵌治理常量自证；禁 spec 文件镜像对象实况；禁统一 mega-registry 收期望值+pin；禁 fixture 文件库为默认形态；禁钉无断言；禁登记表由闸/脚本生成；禁机检「pin 真腐坏了 source」（成本爆炸+假红，属实验轨）；禁 warning 永久化；禁机检历史快照宣称；禁声明式 claims-registry；禁 amend 嫁接异域决策；禁为覆盖率指标稀释分母计入 pytest；禁豁免条目无 expires/issue。

## 证据薄弱处（诚实标注）

- 「钉覆盖率作正式 CI 指标」无直接公开先例——本仓原创立法，五判红集系 CODEOWNERS/fail-on-empty/Codecov ratchet 惯例的组合外推；
- oracle anchoring 论文为单作者单系统测量——分类学可决策、数值不采信；
- 「治理/机制常量」此精确措辞系本仓首创（12-Factor litmus 为同构先例非逐字）；
- 横切 vs 专项 ADR 载体分工系多惯例推论组合，无单一权威条文；
- 人工清单防腐与豁免时效机制无定量先例，自设。
