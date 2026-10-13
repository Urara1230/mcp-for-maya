# ADR-0026 — 第三轮锐评对账裁决与 T-21 修复批（R21 spec 簇）

状态：accepted（grill 定稿）
日期：2026-09-24
决策来源：D-082（第三轮锐评逐条裁决+修复范围圈定）
审计对象：Xxx91n/mcp-for-maya @ b2640f1（审计原文存档 docs/reference/audit-r3.txt；真机探针证据 docs/evidence/probes/probe-displacement.json（由 .scratch/t21 迁入 per D-109）

## 背景

第三轮外部锐评确认第二轮账目十条全兑现（含其正式撤回 ADR-0011 权重表指控——系其上轮脑补），新指控 2×P1 + 3×P2 + 6×P3。本轮对每条指控做了代码面/真机面实证裁决：8 项中 7 成立、1 驳回（P1-B）。对称史：上轮其驳对我方 1 条，本轮我方驳倒其 1 条——逐条实证反审计闭环维持双向有效。

## 裁决表（实证后）

| 项 | 裁决 | 关键证据 |
|---|---|---|
| P1-A tmp 固定名注入 | 成立，定性降级=卫生缺陷 | asset_tools.py:58 / scene_tools.py:80 固定名+无 O_EXCL/0600+不删；同仓 visual_module.py:311 已有 mkstemp 正解。威胁模型已宣 multi-user 出界（threat-model §8）+Windows %TEMP% per-user——非边界破口 |
| P1-B displacement 接线必败 | **驳回——真机证伪** | 活 Maya 2024 探针复刻生产路径（asUtility+force connect）：disp.displacement→sg.displacementShader 连接成立、listConnections 实见 displacementShader1 为源；残余真项=该路径零测试覆盖+失败路径孤儿节点 |
| P2-C manifest 措辞 | 成立 | manifest.json 全 src 无读取方（write-only 审计产物），CHANGELOG:49 "manifest-based" 失实——CHANGELOG:33 同型病灶第二例 |
| P2-D VP2 翻转常量 | 成立 | visual_module.py:244 单机钉死+注释自曝；D-056⑤ 有跨 GPU 方向差异前科。编译期常量仲裁运行时变量=结构性弱点 |
| P2-E blinn 死路 | 成立+修复有坑 | 真机属性面：blinn 无 roughness/specularRoughness/metalness（roughness 接线永 False）；metalness→reflectivity 接错语义。**坑**：standardSurface 无 diffuse/reflectivity，换材质须同步重订属性映射（AO 改 base 侧） |
| P3 六连 | 6/6 成立 | 裸 int()×3 逃出 AssetError 域（polyhaven.py:172/443/449）；/assets 索引零 TTL 缓存；duration_ms=0.0 假数（asset_tools.py:122）；相机默认名 orbit_cam/shot_cam 撞自家 CAM_ 审计（maya_scene_module.py:1530/1631 vs :3316/3567）；main 钉下旧资产退役→旧 PyPI 页永久裂（assets 只加不删纪律）；巨石 4583 行（mypy 基线 183/237 行住它） |

## 处置（D-082 → T-21 任务书）

- **T-21a** P1-A：两处注入口改 mkstemp+0600+finally-unlink（~10 行）
- **T-21b** P1-B 残余：test_asset_module 补 displacement 正/负例+失败路径孤儿节点回收；stub 槽位类型表记债；渲染语义层归 gui/human_verify 档
- **T-21c** P2-C：CHANGELOG:49 措辞归真（fresh-metadata revalidation + per-file size+md5 复验；manifest=只写审计产物）
- **T-21d** P2-D：首次视觉调用跑非对称纯色探针定 per-session 方向；净零副作用铁约束（探针还原场景态含脏标志），落位被证不安全则退文档化逃生开关
- **T-21e** P2-E：_new_material blinn→standardSurface+属性映射同步重订
- **T-21f** P3 六项打包修
- **T-21g** meta 机制：CHANGELOG bullet 挂 file:line/测试 ID 证据指针写入 AGENTS.md 联动规范（0.2.1 条目起执行）
- **T-21h** 门禁收口+0.2.1 patch 备料（docs-only 前提被 0ccf0a1 作废；PyPI 裂图随此版顺带修=D-081 既定路线兑现）
- 巨石 T-06/T-07 与其余顺延债维持挂账（锐评自评「下一个 minor 兑现一次减行」）

## 诚实注记

- P1-A 对外口径=卫生缺陷修复，不得宣称「安全边界破口」——威胁模型已宣 multi-user 出界，且 Windows %TEMP% 为 per-user
- P1-B 驳回以真机探针为凭；其「stub 槽位类型表=下一 frontier」观察仍有效，已记入顺延债
- 探针实现细节：execute_code 包装作用域不存活全局变量，结果经 os.environ 进程内信道传出（已知怪癖复现）；探针净零——节点全删、场景原脏标志未动
- 回应通道：裁决记录本体（本 ADR+账本+修复批）即回应；不向 .codex_tmp 另放回执（默认，用户可另行要求）
