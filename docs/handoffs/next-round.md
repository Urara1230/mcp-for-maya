---
generated: 2026-10-08
from_round: R48（发布债收口裁决——收录面义务行立法+CHANGELOG 字段闸+同根打包排序，覆盖 D-228..230）
ledger_head: canonical=D-230（D-228..230 已随本批迁入；.scratch/r48-grill/decision-ledger.md 过程件留档勿重迁）
branch: R48 docs 批（ADR-0023 增补+ADR-0029 §5 判据+surfaces+CONTEXT+canonical 迁+本任务书）待 push/PR/merge；上游 main=68f7429
---

# 下轮任务书（rev57）

## 状态总览（R48 收口）

- **R48 三裁决全落账**（D-228..230，canonical 已迁）：全包排序（同根病灶=义务的机器消费者不存在）/收录面义务行三段式（server.json 落仓根+三元清单断言+preflight 第 10 行+双链）/CHANGELOG 字段闸即日 blocking+观察期判据+g 归位先例。
- **立法批本批已落盘**：ADR-0023 R48 增补块（第 10 行）+ADR-0029 §5 观察期判据行+distribution-surfaces 核销触发链+实况注记（v0.6.0 重 publish 挂账中如实披露）+关联链接+CONTEXT 两新词（收录面义务行/观察期判据）+canonical 迁 3 行+本任务书。
- **v0.6.0 发布态**：tag=68f7429（annotated）+PyPI+GH Release 全链已兑现（R47 收尾）；PR #73/#74 已合。
- **实况待办**：①registry 匿名 API 直查返 404=记录存在性未证实（须带认证复证）；②金丝雀双窗已兑现待落账（dispatch 37273572077@10-05T06:40Z 首次无人自触发成功；schedule 37331229023@15:14Z 迟到~8.6h 且随发版 push 补发）；③gate-registry 仍 ::warning:: 观察期，翻转前置=R7/R8 落地；④Glama staleness issue 已解锁（tag 在）。
- **工作区残渣如实**：gitbutler/workspace HEAD=53b6ddc，g0 栈挂 7 个上游已合空壳 commit（部分 {conflicted} 标）+~24 stat-dirty 幻影件（git diff 为空）——R46 lane squash 上游后的残影；清理须用户确认。
- **N1 维持结案**（D-219）：四窗阴性；reopener=下次真实 but commit 顺手 git diff --cached。

---

## T-R48-00：registry 复证+重 publish（第一优先——核销窗时钟；D-228①/D-229③）

1. **先证伪**：mcp-publisher registry status / 带认证 API 复证 io.github.Xxx91n/mcp-for-maya 记录存在性（匿名 404 未证实缺席）；
2. **存在**→按 T-30h 动作序重 publish：server.json version=0.6.0（须先随 T-R48-02 落仓）→mcp-publisher validate 离线→人工门 login github+publish→curl+registry status 查 active→落账 publish 日期=**D-146 30 天核销窗新起算点**→surfaces 行更新；
3. **缺席**→按 D-144 三态落「未收录」事实行+走首次 publish 流程（mcp-name 标记行硬前提在案=D-143）；
4. 依赖序：须等 PyPI 上 0.6.0 可被 registry 验证（MS Learn 时序先例）——PyPI 已在故可即跑。

## T-R48-01：金丝雀双窗销账行（D-228②）

账本事件行落账——dispatch run 37273572077@2026-10-05T06:40:02Z=外部载体首次无人自触发成功；schedule run 37331229023@15:14:41Z=当窗到账但迟到~8.6h 且在 15:07 发版 push 后 7 分钟补发。**措辞红线**：只写「schedule 事件链当窗到账、迟到 ~8.6h」，**禁写「已验证准时」**（D-129 归因纪律+GitHub schedule best-effort 官方文档在案）；附 cron 错峰弱注记。

## T-R48-02：server.json 落仓+三元版本断言（D-229①②④）

1. .scratch/t30/server.json 迁仓根（version 改 0.6.0，此后随 bump commit 同步）；位置注记循 D-150② 工具默认先例；
2. check_version_consistency.py 扩三元清单断言（pyproject==__init__==server.json）——只断清单相等，禁断 registry active/PyPI 有版（D-164β）；
3. gate-registry 登记断言面扩列（bound object 关系更新）+钉配齐（反事实=server.json 版本错位 fixture→红；live 对照=真文件→绿）；
4. surfaces↔ADR-0023 双向链接文本本批已落（ADR-0023 R48 增补+surfaces 核销触发链行）。

## T-R48-03：收录面义务行 registry 登记（D-229③）

bound_class=manual_preflight 新条目（gate_id=surfaces_obligation_line 之类命名）；钉形态若纯人工行无落点→exemption{reason, expires=next release-preflight 事件锚}（ADR-0030 §4 事件锚默认值+D-227② 到期处置菜单适用）。

## T-R48-04：check_changelog_fixed.py+g 归位（D-230）

1. 新脚本进 lint job **即日 blocking**：断言全部历史 ### Fixed 节每条目尾行 `Broken version:`+`fixed version:` 两字段存在且值非空——**仅存在性**，值域正则不做（语义层归评审）；
2. registry 登记 bound_class=gate+反事实钉（缺字段腐化 fixture→红）+live 对照（真 CHANGELOG→绿）；
3. **PR 显式声明**：「存量违例=0 故跳过 D-215④ 观察期」（观察期判据首例=D-230/ADR-0029 §5）；
4. g 归位同 PR：[Unreleased] 内 2 条 `fixed version: 0.6.0` 错位条目 retroactive edit 移入 [0.6.0] Fixed 节（commit message 引 Keep a Changelog FAQ「rewrite a changelog」条溯源；certbot#8272 先例=移动非追加勘误）；
5. fold-time 规则注记（runbook/CONTRIBUTING）：[Unreleased] 折入发布节时 `fixed version: [Unreleased]` 字面量同步改写为该版本号——闸不管=人工项。

## T-R48-05：checker 翻转 commit（D-226②/D-228③）

前置=T-R47-03（R7/R8 钉形态闸）合入=首个正常 PR 周期走完+落地 PR 已配齐自身反事实钉（否则回退独立观察一周期，D-226② 前提条款）；动作=ci.yml ::warning::→exit-code 翻转+**翻转条件实体化进 gate-registry**（修 R46 B1 phantom pointer 遗留）；独立小 commit（R46 纪律）。

## T-R48-06：Glama staleness issue（D-227④/D-220）

tag 已就绪可即发——rev56 T-R47-01 文案骨架沿用：camera_orbit C2.9+scene_measure B3.1 评语指控的缺陷在当前描述中不存在，附 A4.1 post-T 实测→C2.9 回归证据（索引回退非单纯时滞）+当前描述原文+glmax.txt 抓取时间戳；对象=glama-ai；观察日志落 docs/evidence/+surfaces 同步+账本事件行。

## T-R48-07：注记批（D-228⑤）

①mypy-baseline.txt 棘爪/baseline 族承接行补「位置=工具默认」弱注记（D-150② 兑现——禁删禁移）；②ci.yml:162 drift 注释「solo repo」改锚（二人化实证 68/89 commits 来自 Euiop1）。

## 承继项（自 rev56 更新）

- **T-R47-03**：R7/R8 钉形态闸实现 lane（D-225 全条款）——翻转前置，实施序见 rev56 该条；
- **T-R47-05**：B 扩面挂账锐化触发（D-223④+D-227③）；**T-R47-06**：baseline 强制再生 lane 槽（D-223⑤）；
- **T-R46-07**：S2 事后补验（D-221③）；
- **PV4 触发债**：对外宣称须挂 R44 工件结论→D-109 单件冻结入 docs/evidence/（触发态=否）；
- **pitch/observe 呈报**：preflight 第 9 行义务维持（D-208 六 pitch 全 observe-state）；
- **真机 soak**：T-R45-05 strict 回归套真机档仍 deferred（D-211 无外因变化）；
- **观察窗**：D-146 核销窗新锚=T-R48-00 publish 落账日；awesome PR #15382 合并跟踪；Glama issue 后页面翻动观察（T-R48-06 收口）；
- **N1 reopener**：下次真实 but commit 顺手 git diff --cached（阳性即复活）。

## 硬约束继承

- 交接载体=.scratch/{slug}/handoffs/ 强制，%TEMP%/仓外路径禁用（D-226①）；
- PyPI publish 人工门（D-206 负向①）；tag=annotated+挂 bump commit 版本一致树（D-224/D-150③）；
- registry 人编禁脚本生成（D-215）；豁免必带 expires|issue+到期处置菜单（ADR-0030 §4/D-227②）；
- 三类法典：fails_the_gate=反事实钉专属后缀；性质断言不入 pin_node_ids（ADR-0030）；
- **观察期判据**：存量违例=0 新闸可直接 blocking 但须显式声明偏离理由（D-230）；
- **收录面义务行**=preflight 第 10 行人工核销（D-229——执行附证据/延缓附理由+复审日）；
- 不可复现标未验证不猜因（D-205③）；非机器载体宣称不作数（D-205②）；
- 归因红线：schedule 金丝雀迟到写「当窗到账+迟到量」不写「已验证准时」（D-129）。

## suggested skills

- $implement / $tdd：T-R48-02/04 实现件（钉测试随行为同 commit；闸脚本先例=check_count_claims.py 形态）
- $gitbutler（but）：全部版本控制写操作（docs 批 push/PR/merge）
- $code-review：实现 lane 评审面（新闸+registry 扩列+迁移批）
- $atomcode-research：registry 缺席若证实时的处置先例、Glama issue 措辞佐证
- $handoff：收尾交接（载体=.scratch/{slug}/handoffs/）
- $domain-modeling / $neat-freak：结案批术语与文档面对齐
