# 知识同步当前已批准试验与剩余交接

状态: Draft（候选已实现，真实运行待验收）

Owner: shidai3 工作区 Owner

2026-10-09 14:18 主线程记录的“准许”已授权 committed 根 AGENTS.md / AI_CONTEXT.md、两个全新 dev/official canary、零模型本地 keyword、独立单库 ingest+retrieve writer、首轮禁止删除、一次手动双轨同步、备份后短重启。无需重复申请该范围；凭据最终创建/配置由 Owner 本机进行。OS 安全权限扩大、外发资料、调度与系统自启动未授权。

| 已选择边界 | 当前实现/剩余条件 |
|---|---|
| local / official | 固定 committed HEAD / cached origin/main；均读到1b6fd01462bd0ff32a5e05499824a9b72e037d48；不读取 dirty、不 fetch，远端最新未知 |
| 源文件 | 只根 AGENTS.md / AI_CONTEXT.md；fixed desired+hash；本轮不扩展P0002或其他子仓库 |
| 目标 | shidai3-dev-canary / shidai3-official-canary 两个新空库；实际 tenant/UUID 尚未知；旧pilot禁止复用 |
| 索引 | native-keyword-canary-v1 已实现并离线验收；需实际运行schema/能力探测/非合成收据/CLI检索验收 |
| 凭据 | Owner 本机两独立单KB ingest+retrieve、full_access=false、明确到期；精确名称与进程环境见handoff；旧reader不可写 |
| 写入 | 仅首次四个 add；禁止DELETE/stock写入/vector默认查询/模型处理；历史1647/1493不动 |
| 维护 | 可交付Windows候选、继承旧guard、只读schema构造、旧维护与外部启动隔离；实际备份/短重启尚未发生 |
| 定时 | synthetic双轨/禁用/过期/恢复证据已有；当前native协议拒绝schedule=true，不得仅改interval启用；真实定时仍待后续授权与扩展 |
| Git | VERSION0.1.0.1；原34 staged、unborn HEAD保持；正常hook曾权限失败，未重试/绕过；候选ZIP是文件快照，未push/tag/父pin |

精确操作顺序、配置模板、一次手动验收和回滚均汇总于 [Owner-Canary-Handoff](service-candidate/Owner-Canary-Handoff.md)。当前环境已停止被拒绝的8081 health、ReaderMetadataGuard和原服务Git入口，未请求权限绕行。先完成Owner本机支持入口的基线、备份、两个新ID与本地密钥；若该入口仍被拒绝，准确报告拒绝，权限扩大需单独批准。实际配置/运行结果以验收回执为准，不沿用历史服务running或旧凭据可用的结论。
