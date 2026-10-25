<!-- D-144/D-145α 执行载体：收录面三态登记表（reference 页型——每个 recurring question 恰一处 canonical 查询面；
     同构先例=threat-model.md / visual-callform-matrix.md / upstream issue 对照表）。
     本表=查询面兼权威载体：原账本 D-144/D-145/D-146/D-147 立法内容已折叠于本表；账本本体已于 2026-10-14 治理庭裁定退役删除（D 行原文见 git 历史 docs/decision-ledger.md）。
     四维判据列源=R31-Q1 atomcode 调研四维矩阵（结构匹配性/受众质量/进入成本与凭据面/宣称纪律约束）。
     立法后渠道处置不再逐轮重复议事（D-144）；本表=维护者治理面——README 不挂指针、AGENTS.md 联动表不挂（R31-Q3 核验：纯文档不触模块联动）。
-->

# 收录面三态登记表（distribution surfaces）

**staleness 纪律（修订协议）**：修改渠道处置态须**先落 ADR 裁决或维护者明令再改表**（upstream-issue-status.md 同型存活纪律——任何一格失真即破坏存在理由）；渠道侧事实变化（更名/下架/规则变更）→直接在表中注记修订（原「账本注记→同步改表」两跳随账本退役改一跳）。

**宣称纪律**：凡涉本仓对外宣称的表述一律走 D-132 校准句式（4-Beta+能力+证据指针，禁 stable 语气）；收录轨价值上限=可发现性兜底，非拉新主力（MCP 目录 Maya 用户密度低，禁夸大目录流量）；禁收录徽章堆砌、禁荣誉墙。

互锚：GLOSSARY「收录/叙事双轨」「收录面三态判置」词条 · 发布后段核销行（Registry publish 后 30 天一次性核销）· 原账本 D-141/143/144/145/146/147 行（账本已退役，见 git 历史）。

## 可行动（actionable）

| 渠道 | 结构匹配性 | 受众质量 | 进入成本/凭据面 | 宣称纪律 | 动作锚 |
|---|---|---|---|---|---|
| 官方 MCP Registry | ✅ stdio 一等支持（D-143β 已核） | 权威上游，全聚合器消费（Glama 官方明文 superset 全量摄入；下游镜像有实证同步缺口——GitHub 镜像 discussion #203757——故下游收录态逐一核销勿假设） | mcp-publisher CLI + GitHub device flow=用户人工门；server.json 草稿见任务书 T-30h | server.json 描述走 D-132 句式 | **已收录（2026-09-30 首录 v0.5.0，v0.4.1→v0.5.0 isLatest 翻转已核；2026-10-25 17:29 UTC 重 publish v0.6.0，status=active・isLatest=true，version 与 PyPI 0.6.0 精确一致，核验见小节实况）**；发新版须重 publish（操作契约见下「Registry publish 小节」） |
| Glama | ✅ 已收录+已认领（2026-09-30，由 Xxx91n 完成 OAuth Claim；自动索引页 HTTP 200）；构建面依赖 Dockerfile——**无 Dockerfile 构建面=不分发常态，禁主动启 Docker 构建/健康检查面**（Maya GUI 宿主构建恒失败→健康检查红反损 listing） | 中流量，生态搜索 SEO 好，Maya 密度低 | Add MCP Server 提交 repo URL→GitHub OAuth claim（个人账号直连免 glama.json） | claim 文案走 D-132 句式（已用 D-165γ 终稿） | **已完成（2026-09-30，T-30f 闭环，D-166 记录）**；预期=占位+元数据所有权，非分发流量（D-144③）。**R38 TDQS 提分窄焦批 PR #58 已合并（4d01b44，覆盖 D-172..D-175）；金丝雀观察：当前页面评估基线仍为 2026-09-30 06:25 UTC 的 B 3.4/5.0，待后台更新或随下一发版实测生效（D-175②③）**。**备注（遗漏面 #7）：claim 后改 repo 描述/logo 须重走 Claim ownership 流程触发同步** |
| awesome-mcp-servers | ✅ 纯列表，无运行时匹配要求 | 87-94K★，生态最权威列表，长期 SEO 背链 | fork+PR（格式=owner/repo+语言 emoji+字母序），🤖🤖🤖 fast-track；行稿备稿见任务书 T-30g | 一句话描述走 D-132 beta 语气 | **PR 已发出（2026-09-30，[#15382](https://github.com/punkpeye/awesome-mcp-servers/pull/15382) 待合并；已在 Art & Culture 分类按字母序插行）**，覆盖 D-164α/D-165αβ，D-166 记录 |

## 被动观察带触发条件（passive observation with trigger）

| 渠道 | 结构匹配性 | 受众质量 | 理由/进入面 | 触发条件与核销 |
|---|---|---|---|---|
| mcp.so | ✅ 无结构障碍 | 流量大但质量参差，社区有 spam 反感史 | GitHub issue 人工提交。**否决结论维持但理由换锚（D-144 注记/遗漏面 #4）：否决依据=issue 人工提交面+受众质量低，非「聚合器自动同步」假设**——对 mcp.so 自动同步未证实（D-108② 先例注记，非 revised） | D-146 核销窗：Registry publish 后 30 天一次性核销收录态；若提交，描述走 D-132 |
| PulseMCP | ✅（D-141 未深挖定性维持） | 21800+ 条目自称每日更新；403 程序化访问，无稳定机读接口 | 无稳定核销 API；零信号对象不做全维度调研（D-128 同款否决逻辑） | D-146 核销窗同上 |
| VS Code MCP gallery | ⚠️ manifest 驱动，无明文第三方提交路径；实为 GitHub MCP Registry 一键安装面（github.blog 2025-10-24 一手，官方明文下游自动传播） | 巨大装机量，Maya 密度低 | 无独立提交动作可做；用户侧可 .vscode/mcp.json 直配 | D-146 核销窗同上；**核验形态=人工检索，无 API（遗漏面 #2）** |
| Cline marketplace | ✅ stdio/pip 完全兼容 | 中流量，Maya 密度低；one-click install 承诺与 Maya 宿主前提冲突（装完连不上=差第一印象） | 开 issue（repo URL+400×400 logo+收录理由+Cline 内实测装成确认） | D-147 登记债：触发=Registry publish 后其生态出现 DCC/Maya 类收录信号；信号未现前不投入；核验动作并入 D-146 同窗（共用观测窗零额外成本）；若评估提交，描述走 D-132 |
| Smithery | ❌ stdio+Maya 宿主与其托管/扫描环境结构性不匹配（D-141 降级维持） | — | 无动作面；本仓结构性否决的**判据参照物**（D-144 同构判据先例） | 无触发条件——维持被动观察，不重议 |

## 结构性否决（structural rejection，永续）

否决判据=**结构匹配性一票否决**：本地 stdio 传输+Maya GUI 宿主前提可否被该渠道的提交面/健康检查面承载（与 Smithery 判定同构，D-141 先例）。永续=除非账本 revise，不在轮次中重议（D-144：登记表立法后渠道处置不再逐轮重复议事）。

| 渠道 | 否决理由（一手原文级） |
|---|---|
| Claude Connectors Directory | 官方提交文档明文停收本地服务器：「the directory no longer accepts local servers packaged as MCP Bundles (MCPB)」，仅收 remote HTTPS；巨大流量但零结构入口 |
| Docker MCP Catalog/Toolkit | 服务器必须是容器镜像（PR 到 docker/mcp-registry+建+推镜像）；Maya GUI 宿主无法容器化 |
| Cursor directory | 官方明文只收代码编辑工作流服务器（「Only relevant if your MCP server is useful for code editing workflows」）；Maya 场景操控不属代码编辑工作流 |
| Windsurf / Zed | 无第三方 MCP 服务器提交面（官方文档无独立入口） |

## 兜底条款（catch-all default，D-145γ）

- 未列出渠道（OpenTools/MCP Market/mcpservers.org/lobehub 等长尾）默认=**被动观察**（fail-closed：不投入=零成本零风险；显出高价值信号走单向升级）。
- 新渠道进入登记表（任何态）须先过**结构匹配性判据**（见上）；不过=结构性否决（一票否决闸，防顺手提交滑梯——与注入模块「独立时机/独立 API 面/独立失败域」准入判据同构）。
- 升级单向：被动观察→触发条件→可行动；降级/否决/revise 须回账本（先落账本再改表）。
- **叙事修边**：兜底默认态不豁免**叙事轨**判据（D-143γ）——任何渠道作叙事型投放位（Show HN/blog/社区帖）仍走 v1.0.0+#7 证据门；叙事位判定独立于收录态（收录态归本表，叙事用途归双轨词条，两轴正交）。
- **收录后宣称面同步（遗漏面 #8，按 D-145γ 并入兜底消化，不立常设义务）**：repo 宣称面实质变更（定位/档位/能力面）时检查已收录渠道描述是否失真；失真按本表修订协议更正（awesome=新 PR 改行；Glama=重走 claim flow；Registry=重 publish），防收录页成为第二个陈旧宣称源（D-082⑦ 反向面）。

## Registry publish 小节（可行动第一行的操作契约，遗漏面 #5）

- server.json 每次 publish 生成新版本记录；**发新版须重 publish，且 version 字段与 PyPI 精确一致**（T-30h 动作序：version 改届时 PyPI 精确版本→mcp-publisher validate 离线→人工门 login github+publish→登录态 Registry API GET /v0.1/servers/{serverName}/versions/{version} 查 status/isLatest（serverName 需 URL-encode；端点公开可读，登录态用于排除匿名歧义））。
- 下架/更名走 publish 侧 status 变更（active/deprecated/deleted，Registry API 原生字段），非删记录——原生可控退路。
- **publish 完成即刻于本表落 publish 日期=D-146 30 天核销窗起算点（非发版日）**。
- **核销触发链（D-229）**：本小节义务由 release-preflight **第 10 行「收录面义务行」**逐行核销（执行附证据/延缓附理由+复审日）——本表↔runbook 双向链接就此落地，v0.6.0 当窗漏执的根因（义务无消费者）由此封闭。
- **实况（2026-10-08 挂账 → 2026-10-25 结清；裁决 #86，执行 #88）**：v0.6.0 发布后重 publish 曾**未执行**（R48 挂账 T-R48-00）。2026-10-25 先以 mcp-publisher 1.8.4 离线 `validate`（exit 0），维护者执行 `mcp-publisher login github` 与 `mcp-publisher publish`（exit 0，published 0.6.0）；1.8.4 的 `status` 子命令只写不读，故以登录态 Registry API 复证：`GET /v0.1/servers/io.github.Xxx91n%2Fmcp-for-maya/versions/0.6.0` → HTTP 200，`version=0.6.0`、`status=active`、`isLatest=true`、`publishedAt=2026-10-25T17:29:14Z`，与 PyPI 0.6.0 精确一致。30 天核销窗起算=2026-10-25。匿名 404 仍不作为缺席证据。
- 核销产出=每渠道「Registry 同步已发生/未发生」事实，**按本表三态重新判置并落账**（遗漏面 #1：判置变化→先落账本再改表；未收录且无信号→维持被动观察或注销债；已收录→观察态转静默，无需动作）。

## 关联

- release-preflight 义务行：发布清单第 10 行「收录面义务行」（D-229——本表可行动区的人工核销触发器）
- 原账本 D 行（已退役，git 历史可查）：D-132（校准宣称句式）/D-141（渠道分层）/D-143（平台扩张执行）/D-144（三态立法）/D-145（执行契约）/D-146/D-147（触发债）/D-082⑦（宣称对账反向面）
- 发布后段核销行（D-145β：Registry publish 后 30 天一次性核销）· 注入模块准入判据（独立注入时机 / 独立 API 面 / 独立失败域）
- GLOSSARY.md：「收录/叙事双轨」「收录面三态判置」词条（本表=其查询面落地）
- 开放任务索引：GitHub issues + milestone（轮次任务书载体已随账本退役）
