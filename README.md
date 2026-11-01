# Knowledge Sync

## 2026-10-16：writer readiness 诊断纠正（本地未发布）

生产 adapter 构造或运行 capabilities 探针抛异常时，状态返回固定 `WRITER_READINESS_UNAVAILABLE`；不再将该失败一律归因为缺少索引证明。没有生产 adapter，或 adapter 的 `production_ready=false` 时，仍返回 `INDEX_ATTESTATION_REQUIRED`。两者均保持 `writerReady/ready=false` 和 `freshness=blocked`，不输出原始异常、凭据、URL 或响应正文，也不新增请求或重试。

这项诊断修正不证明服务可连、认证成功或真实写入完成。满足批准、启用和进程凭据前置条件时，status 仍可能执行现有的认证 GET capabilities；不能将其作为保证离线的命令。未重置任何运行数据，历史 UNKNOWN 仍必须查询原执行。


## 2026-10-11 14:06 UTC：匿名页面shell已确认，Owner认证仍未确证（当前状态）

Owner再次报告“成功、访问不用登录”，仍未发生可确认的新密码提交。停止重复无痕/密码实验，不退出当前会话。14:04:47UTC本机8081确由原guarded PID23820提供，EXE SHA与批准版本一致，只有127.0.0.1:8081监听；14:06:42UTC匿名health=200/ok。

不带cookie或认证的GET /login与/platform/knowledge-bases都返回200、4736字节、同一SHA92296211812e015cf15f47e72231f53ed84900ae86cfc57ec2019d51764ad065，与启动器指定web-reader-recovery-v0.8.2/index.html完全一致，未设置cookie。Go先服务静态SPA shell再处理认证；页面200、界面可打开不能证明密码验证成功。代理没有执行网页JS，未读用户浏览器存储或凭据，也未读取/展示config.js或inline script内容。

实际main-fYAmUhLu.js与部署清单SHA一致。它是Vue启动和/auth/me会话恢复代码；未找到dev/mock登录证据。Lite AutoSetup分支确实存在，但需要Wails桌面bridge的GetAutoSetupToken及对应请求头，后端也要求已注入的非空内存值；原启动器运行cmd/server，未在这条链上注入桌面bridge/setter。这是源码与产物绑定的推断，不能证明Owner当前浏览器实际走了哪个分支。前端API base为当前origin加编译相对前缀，Owner所用页面origin/应用仍未观察。

脚本auth-login的401/AUTH_REJECTED仍未解决，tenant1 Owner和新密码登录均未确认；不将“进入网页”改写为密码成功。旧UNKNOWN恢复记录保留；本轮不改功能代码、DB/state/凭据/权限，不发认证请求、不登出或清cookie。

最小非敏感补充只看当前窗口，不再登录：Owner仅回报窗口属于普通浏览器/Codex内置浏览器/WeKnora桌面应用，地址栏主机+端口+路径（去掉query、fragment、userinfo），以及页面是登录表单还是已有真实知识库条目的列表（不要列库名）。不需要截图、邮箱、密码、cookie/token/Bearer或原始API响应。

证据：[anonymous-ui-evidence.json](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner_anonymous_ui_followup_20261011/anonymous-ui-evidence.json)。以下为历史记录，以本段当前状态为准。

## 2026-10-11：网页已有会话，尚未验证新密码登录（当前状态）

Owner报告打开网页后直接进入，未输入密码。这说明已有浏览器会话可继续使用，不证明本次密码认证成功；脚本auth-login的HTTP401/AUTH_REJECTED仍未解决。保留现有窗口和会话，代理没有读取浏览器存储、cookie/token或真实账号值，没有登出、清缓存、发新认证请求或改账号/密码/权限。

只读源码核对：网页从既有存储态恢复并经/auth/me确认当前会话；该接口返回active tenant、user及memberships。脚本仍要求active tenant id=1、active user且tenant1 membership role=owner；此检查在登录成功后，当前login401未到该步骤，不能说tenant1 Owner验证已通过。网页与脚本均使用POST /api/v1/auth/login及email/password字段。UI和服务没有显式trim/lowercase邮箱，repository按email等值查找；这只是可能影响匹配的条件，不是本次失败根因。密码不能自动trim或改写。

新增2项定向离线测试（未重跑原29项）：6组假密码覆盖引号、美元符号、反引号、反斜杠、中文/emoji、组合字符、前后空格与内部tab。直接提取现有BSTR/JSON桥接和UTF-8编码设置，经假SecureString、Python PipeUI与假HTTP opener验证原样传输；全部通过。测试未覆盖真实键盘/Read-Host输入，不能排除粘贴、输入法或本人确认行为。没有发现新的生产代码缺陷，本轮不改客户端功能代码。

官方网页的401不走token refresh，展示后端错误message。后端可返回“Account is disabled”“Invalid email or password”或通用“Login failed”；前者可提供账号停用类别，但“Invalid email or password”仍可能覆盖账户查找错误/不存在/密码校验失败，仅凭401不能细分。字段/JSON绑定错误在handler映射400，也不能用旧会话进入来证明密码。客户端继续保留固定AUTH_REJECTED和数字状态，不读取敏感错误body作猜测。

Owner最小本人验证：不退出当前网页；另开独立隐私/无痕窗口访问http://127.0.0.1:8081/login。若出现邮箱/密码表单，按本人在原账号资料中确认的邮箱原值及密码只尝试一次。只反馈“出现表单且新登录成功”“账号或密码无效”“账号禁用”“其他失败状态”，不要发邮箱、密码、token/Bearer/cookie、截图或原始响应。若隐私窗口仍直接进入，只反馈“仍未出现密码表单”，仍不算新密码认证。暂不自动重跑OwnerOperate或反复试密码，也不重置账号。

证据：[session-contract-evidence.json](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner_session_followup_20261011/session-contract-evidence.json)、[bridge-test-evidence.json](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner_session_followup_20261011/bridge-test-evidence.json)。以下为历史阶段，以本段当前状态为准。

## 2026-10-11：登录401已确认为AUTH_REJECTED（当前状态）

Owner本人再次执行后返回auth-login、HTTP401、HTTP_STATUS、ownerStep=password。服务已返回明确的登录失败响应；不能继续称为连接或HTTP结果未知。请求契约已只读核对：POST /api/v1/auth/login，application/json，UTF-8，字段仅email/password；PowerShell到Python的隐藏输入管道也为UTF-8，值不做trim或改写。官方LoginRequest、路由及响应契约匹配，normal-reader overlay未替换认证路径。登录不接收tenant字段；成功后由/auth/me检查tenant1且为Owner。

客户端诊断仅将auth-login的401改为固定AUTH_REJECTED，仍输出安全的stage/transport/httpStatus；不读取错误体、密码、token/hash或账户数据。其他HTTP结果与KB/writer创建的UNKNOWN恢复逻辑保留，状态格式、过期时间与保护保存流程不变。29项相关无socket合成回归通过，prompt-emulated reviewer未发现新增P0/P1/P2；这不代表真实认证或canary验收成功。

13:39:49UTC只读投影：两canary库与两writer均0，journal不存在，auth_tokens仍32。代理没有新增登录请求或自动重跑OwnerOperate，没有改生产DB、受保护状态或凭据。此前无HTTP状态的UNKNOWN仍是历史未确证尝试，不被这次401追溯改写。现有OCR入口此前报目标“not a git repository”，本次未重复安装或改变Git/权限，不声称OCR通过。

Owner最小下一步：本人打开http://127.0.0.1:8081/login，用WeKnora原Owner邮箱与密码验证网页登录；此账号不是ChatGPT或Windows账号。只反馈“网页登录成功”或登录失败的状态/提示类别，不提供邮箱值、密码、token、cookie、Bearer、截图或原始响应。若失败先停止重复尝试；若网页成功也先反馈结果，随后再继续固定交接入口。401不能区分账号不存在、账户停用、账户查询或token生成错误等原因，不能据此认定Owner输错。当前不重置账号、不枚举用户或爆破密码。

证据：[auth-contract-fix-evidence.json](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner_auth_rejected_20261011/auth-contract-fix-evidence.json)。以下记录为历史阶段，以本段当前状态为准。

## 2026-10-11 13:27 UTC：原服务已恢复（当前状态）

原guarded服务已由既有启动器恢复，PID23820，仅监听127.0.0.1:8081；匿名GET /health返回200、ok。启动前没有实例或监听，启动器和EXE/DLL哈希匹配。旧知识、FTS与任务完整指纹在启动前后均为506bf696f6cde92b9a2f3bb8a0eca7026843c4839482267017fd6c53d7360e79，与一致备份相同；1647文档、1493历史待处理项未变。housekeeping与自动migration/dirty recovery继续关闭，Redis环境清空，持久Wiki任务、数据源及IM等触发表为空，MCP保持禁用。

13:27:41UTC只读核两canary库、两writer均0，journal不存在，auth_tokens仍32。Owner此前认证UNKNOWN仍未确证；恢复服务不代表认证成功。代理本轮只做匿名GET和非秘密只读状态验收，没有代认证或执行OwnerOperate，没有读取token/hash。受保护reader查询未重测。

无需再让Owner重复ConnectionCheck。下一步由Owner本人通过已交付固定入口确认认证、完成逐个创建最终提交与隐藏输入/CurrentUser-DPAPI保存；候选仍未安装，真实canary未开始，writer/scheduler/系统自启动未开启。凭据就绪后再继续已授权的一致备份、短重启部署和双轨真实验收。以下旧认证排障段落为历史阶段，以本段当前状态为准。

恢复证据：[service-recovery-receipt-20261011.json](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/service-recovery-receipt-20261011.json)。

**当前版本：`v0.1.0.1`**

## 2026-10-11 Owner 本机交接入口（代码已实现，真实待本人操作）

新增 [Start-OwnerCanary.ps1](Start-OwnerCanary.ps1) 与 [Owner 操作/恢复说明](service-candidate/Owner-Canary-Entry.md)。默认只离线预览，-SyntheticCheck 为无socket合成服务验收；真实 -OwnerOperate 仅本人交互控制台。原官方/login+/me验证tenant1 Owner，隐藏email/password，每个创建前显式CONFIRM；固定两空库keyword-only零模型、两独立单库ingest+retrieve writer、实际最终提交起7天，进程内CurrentUser DPAPI保护保存，输出只有非秘密回执。单实例锁、实际只读文档/chunk/FTS/持久任务空计数及UNKNOWN恢复均已覆盖；不读取旧key列表，不扩大reader身份权限，不建立秘密HTTP接收服务。

真实认证、KB/writer创建/保存、canary安装及双轨真实验收尚未执行。原服务查询恢复/完整旧指纹保护及当前已核父提交默认建议见[主交接](service-candidate/Owner-Canary-Handoff.md)。本轮仅可回滚代码/文档及合成测试，候选服务ZIP未改变，无真实API写入、调度、自启、push/tag/父pin。

## 2026-10-09 原生 keyword 双轨 canary 候选（历史）


本轮完成隔离 Windows 服务候选、native keyword 事务写入/持久异步任务/实际 FTS inventory 收据，以及 Python 的 first-add 单库适配。真实 SQLite/FTS 与 Python outbox 的无 socket 合成端到端通过：两个通道共四次写入，丢失首次 POST 回复可查询恢复且无重复提交，整批 completedSHA 关闭重载一致。两个已批准根文档的固定 Git blob 与 Go Unicode15 全表分词核对通过。旧 25 个 guard overlay 已继承；canary 模式关闭历史后台维护/队列恢复、旧 IM 和外部启动连接，使用无迁移 SQLite 检索器。正常模式保留既有行为。

当前真实运行验收未完成：支持的 8081 health / reader metadata / 原服务 Git 入口被权限拒绝，均已停用该检查路径，未绕过。没有创建真实 KB/凭据、访问运行库、部署/备份/短重启、真实导入或调用模型。实际 sources.json 尚不存在，writer/scheduler/系统自启动仍关闭。原 1647 文档/1493 pending 和禁用旧端点保持保护边界。

已批准源为 committed 根 AGENTS.md / AI_CONTEXT.md，local HEAD 与缓存官方 ref 均读到 1b6fd01462bd0ff32a5e05499824a9b72e037d48，official 远端最新仍未知。两个新 KB 的实际 ID 和 Owner 本机配置的独立单库 writer 是剩余外部条件；不再重复要求批准同一范围。首轮只有四个 frozen add、禁止 DELETE/stock write/模型处理/调度。native 试验协议不支持直接启用 schedule 或自动跟踪新提交。

交付见 [Owner 本机操作与验收](service-candidate/Owner-Canary-Handoff.md)、[构建脚本](service-candidate/Build-Canary.ps1)、[Companion 协议](protocol-v1.json) 和 tests/native-canary-evidence-20261009.json。ready=writerReady（实时能力探测），queued=queuedCount>0（只数 queued），indexed 要求完整目标 checkpoint、pending=0、非空活跃映射每条有非合成有效收据；合成 receipts 不计入 indexedDocuments。字段为 v1 的兼容增量。

新功能日志已同步；VERSION 保持 Owner 已批准 0.1.0.1。原34个 staged内容保持，子仓库仍未出生 HEAD；正常 hook 提交曾因系统权限失败，本轮未重试、绕过、push/tag 或改父 pin。候选 ZIP 与 SHA256 receipt 是文件检查点，不能称作已提交 Git 检查点。

以下日期同日的旧切片记录为历史证据，当前 native 边界及阻塞以本节/最新 handoff 为准。

## 2026-10-09 SQLite keyword 可执行离线切片

新增 `sqlite_keyword_observer.SQLiteKeywordProvider`：显式提供数据库路径，以 `mode=ro`、`query_only` 和单一事务读取；不建表、初始化 retriever 或访问默认服务。对照固定官方源码构造标题 + ContextHeader + 正文，独立重建真正 FTS5 `unicode61` postings，逐行核对正文及四个元数据列的 term/column/offset、完整 source/chunk 集合、文档版本、chunk revision 和历代 rowid。持久 generation/operation/task 回执与实际索引必须同时成立；删除还需精确任务成功以及元数据、chunk、FTS 和登记 rowid 全部消失。`IndexVerifier` 将可选 `backendInventoryHash` 写入其完整性哈希保护的收据。

隔离合成库已验证真实 contentless FTS、索引缺失/错词/孤儿、标题/header/revision 或版本变化、跨 scope、事务回滚、精确删除任务、逐项完成与整批 checkpoint 持久重开。该 provider 可产生合成 READY，仍固定 `production_ready=false`，不会配置生产 writer。执行：`python -X utf8 -B -m unittest discover -s tests -p test_sqlite_keyword_observer.py -v`。首个 canary 分词范围明确限定 ASCII、U+4E00–U+9FFF Han 与代码列出的常用标点；其他 Unicode 阻断，不估算 Go Script 属性。合成库只在项目 `tests` 临时目录创建，避免当前沙箱默认临时目录的 SQLite 打开拒绝。

最终25/25通过，无失败或跳过（完整记录115.672秒）；18个Python文件AST通过，测试期间4个切片功能/fixture文件字节不变。新增实际SQL错误回滚、旧收据兼容、metadata-only basis、客户端重开不重复提交/删除验收。`/root/code_mapper`与`/root/pin_index_review`仅按真实TOML职责执行普通提示词角色，静态复审无具体P1/P2；不声称原生preset、指定模型或硬只读隔离已生效。OCR Delegation规则按7个明确路径解析，宿主逐项核读；未运行preview或Git检查点重试，不外发源码/模型审查。

`tests/sqlite_keyword_receipts.sql` 是已在合成库执行的原生迁移候选；现有真实服务尚无该回执表或 vocab 投影。后续服务变更的精确最小边界见 [实施方案](implementation-plan.md)：keyword 写删错误传播/事务、operation/generation 关联、成功回执及受限只读输出，需独立部署批准。本轮不修改 `D:/services/weknora`、运行DB/凭据/权限，不连接 loopback，不重试 Git、不改变原34文件暂存检查点与ZIP。实际 source、writer、scheduler 继续关闭。新证据见 `tests/sqlite-keyword-evidence-20261009.json`；不将既有92通过/1跳过替代本切片或生产验收。

## 2026-10-09 官方API观察与版本初始化

新增 `official_api_observer.OfficialAPIProvider`，仅注入transport读取已核源码的文档与分页text chunks两个GET接口。独立核对页号/总数/完整分页、来源/tenant/KB、chunk重复、文档前后版本和调用者内容集合；parse与chunk状态只提供处理中/失败佐证。官方API未暴露实际keyword/vector索引inventory或知识删除task终态，因此该观察器始终 `production_ready=false`，绝不制造READY或推进checkpoint。本轮完整93项中92项通过，1项因Hook启动权限拒绝而跳过（179.905秒）；16个Python文件AST通过。实际源码证据、合同测试与本轮完整结果见 `tests/api-observer-evidence-20261009.json`；既有77项开发回执保持不变。

Owner已批准初始版本 `0.1.0.1`。本仓 `scripts/git_version.py` / `.githooks` 遵循P0002四段版本和Agent署名合同，适配首次提交与所有本地ref的第四段计数。真实Hook集成测试在当前沙箱因MSYS `NtCreateDirectoryObject 0xC0000022`拒绝而明确跳过，脚本/首次index逻辑单独验证；不绕过Hook、换Git入口或修改权限。仅本任务的正常本地检查点结果另附回执，不push/tag/改父pin。版本授权不等于生产授权。[Owner选项](owner-options.md)已列精确候选repo/ref/两文件白名单、双库、处理、身份/删阈值/周期与观察证明缺口。

## 2026-10-09 离线父 pin 与索引证明开发

按主线程继续开发要求，新增显式获批离线cache的递归gitlink正文读取、父→子来源链与内容重验，以及纯逻辑IndexVerifier/provider接口。仅读取每层父树给出的固定SHA，不查子仓HEAD、不fetch；`child/docs`范围也会发现祖先mount。缺cache/commit拒绝，未登记相关pin阻断正式计划。`source_commit`现在指真实叶子仓固定SHA，`parent_commit`保存根父SHA；来源主键和双轨目标隔离保持。

正文不变但父SHA/子pin/处理策略改变会建立出处操作。索引收据、mapping、done与整批completedSHA在同一账本事务提交；账本schema为3，旧schema拒绝，不自动迁移。UNKNOWN索引证明立即blocked，不重发写请求。标题或处理策略需要重新publish时，恢复与索引证明均要求版本高于上一持久收据；同正文/策略的metadata-only操作使用绑定当前来源的新收据，明确引用旧索引收据。

新增27项合成测试，完整77/77通过（165.224秒），12个Python文件AST检查通过。端到端覆盖定时双轨精确pin、缓存缺失不误删、逐项异步收据/整批检查点、持久恢复、UNKNOWN、索引集合/版本/计数/新鲜度、来源metadata、删除任务，以及publish应用前/后丢回复。最终结果见 [开发验收回执](tests/development-evidence-20261009.json)和[原始日志](tests/development-tests-20261009.log)。[Companion协议](protocol-v1.json)新增只读 `indexReceipts` 摘要，既有状态字段含义保持。真实source、生产观察provider、writer与scheduler仍未配置/启用；未读秘密、导入真实资料或改WeKnora数据库，未操作Companion/游戏。

原19文件的可恢复ZIP与哈希保持完整。Owner后续已批准初始版本 `0.1.0.1`，本仓版本门与本地检查点正在落实；该批准不扩大生产权限。真实启用的最小选项见 [Owner选项](owner-options.md)。

## 2026-10-09 开发前执行环境基线

本轮重新执行完整合成套件，50/50 通过（68.271 秒），10 个 Python 文件 AST 检查通过；测试前后这 10 个文件字节一致，8 个功能源文件仍匹配既有验收哈希。实际 `status` / `scheduler-status` 均返回 `sources=[]`、`pending=0`、`targetSHA/completedSHA=null`，writer 与 scheduler 关闭。新回执与原始测试日志见 [复核证据](tests/verification-20261009.json) 和 [测试日志](tests/verification-20261009.log)，不覆盖历史回执。

当前 Git 为独立 `main`，尚无 HEAD、VERSION、remote 或父仓 gitlink；未暂存或提交。官方 CLI 文件哈希仍匹配交接脚本的已验收值，受保护 reader 文件存在且未读取。当前沙箱访问 8081/32180 的 socket 返回 10013，生产数据库只读打开返回 SQLITE_CANTOPEN；因此本轮无法重新确认实际服务、真实 reader 检索、1647 文档/1493 历史 pending 或禁用端点状态。未重启服务、恢复历史任务、改权限或调用模型。

协议中的 `writerReady` 只表示获准生产 writer 的前置条件；`sync-once` 只返回持久化入队的 `jobId/jobState`；`completedSHA` 只在整批索引/删除确认完成后推进。v1 没有独立 `ready/queued/indexed` 布尔字段，视图标签须按 [状态语义](protocol-v1.json) 映射。开发前 gitlink 仅记录精确父树 SHA，未读取子仓固定版本正文。最新实现和新增验收见本页顶部；生产观察provider仍未接入。50项是开发前基线，不能代替新增验收或生产定时同步完成。实际协议和新增字段已交给 Companion worker，未修改其工程。

## 2026-10-09 reader 恢复与同步状态回执

原 `SYSTEM_AES_KEY` 已无法取得，因此旧加密 reader 的明文不能恢复；原 ID1/ID2 元数据与密文保留。按 Owner 授权创建替代 ID3 `shidai3-cli-pilot-retrieve-recovery-20261008`：tenant1、仅 `shidai3-pilot`（`da1051a5-3fff-4198-ba55-017ea79df9c3`）、retrieve-only、full access 关闭，至 `2026-11-08T01:25:46.685460Z` 到期。新密钥只以 hash 写入服务库，并在本机由当前 Windows 用户和机器保护的 DPAPI 文件保存。

替代 reader 已通过官方 CLI 真实身份匹配及 keyword `--no-vector --limit 3` 检索（返回3条）；API key 管理与外部 tenant 知识库请求均返回403。Edge 原 Owner 登录也能加载 API key 列表：旧 ID1 标记不可用并禁用复制，新 ID3 标记为本机交接并禁用网页复制，旧 ID2 仍可读但未复制。部署和 CLI metadata-only 回执位于 `D:/services/weknora/pilot-access/reader-recovery-20261008/`、`D:/services/weknora/cli/evidence/pilot-readonly-smoke-metadata.json` 与 `D:/services/weknora/cli/evidence/reader-boundary-metadata.json`。最终部署后 guard 于 `2026-10-09T01:48:05Z` 完成：quick_check=ok、schema dirty=0，documents/chunks/FTS 为 1647/59428/2269；auth_tokens=30、users=2、tenants=2；completed/finalizing/processing 为 154/356/1137，知识状态指纹与基线一致；全部触发器为空、临时 KB 为0、精确 legacy MCP 端点仍禁用。旧 ID1/ID2 非敏感元数据与原值一致；新 ID3 为双 scope BLOB、明文 `api_key` 列为空。

本同步工具功能源文件未改，50项完整合成测试结果复用；没有登记真实来源、入队/导入资料、读取 writer 凭据或启动调度。`sources=[]`、writer/scheduler 关闭，生产索引验证器缺失；独立仓仍无提交、版本和 remote。Companion 官方 client status 确认 `32180` daemon 正在运行，3个 ready Creator 连接及其进程保留；未操作 Creator。知识库 UI 显示官方 CLI 可用、认证未配置，真实关键词查询待执行。当前阻塞在部署产物核对：269项部署文件中有45项与上一轮验收清单不同（涉及 daemon、coordination、HTTP、lifecycle、bridge 与 Web），其归属及匹配验证未知；未停 daemon、未覆盖 Companion 产物/源码、未将 reader 传入 Companion。3个未应用原型已移至 `stage evidence/companion-unapplied`，暂不部署。此前批准的短暂停用授权仍有效，当前阻塞是产物不匹配；不得据 daemon 运行或 reader CLI 验收宣称 Companion 检索或同步已完成。

## 2026-10-08 重装后现场核验

本目录仍为独立 main Git 仓，无提交、版本、remote 或父仓 gitlink。本轮保护全部原有未跟踪代码，未修改功能实现，也未登记真实来源。50项完整模块合成测试通过；只读 Companion bridge status 返回 sources=[]、writerReady=false、schedulerEnabled=false，无网络写入或常驻调度。

Companion v0.3.121（69c99cccf1b597b3c173a9046486e445d3100024）已在 Owner 批准窗口部署；两个 Creator 进程和连接保留。Edge 知识库卡片实测 CLI可用、认证未配置、暂无来源、写库与定时关闭；不能据此称真实检索/同步已用。8项WeKnora恢复guard、7项reader metadata、7项官方CLI合成、54项Companion知识库测试及隔离build/typecheck通过。

8081当前未运行，数据库一致性/快照/历史状态已核对。现有单库retrieve reader没有到期时间，期限修正待Owner批准；官方v0.8.2未提供输入key的非敏感身份自省，CLI helper保持拒绝，未输入key或真实检索。生产index verifier、真实源/双轨目标库、writer权限、处理/外发范围、删除策略/周期，以及本仓版本前三段/remote仍待明确，writer/scheduler不启用。

Companion完整测试初次结果为445通过、8失败、1跳过。隔离Git archive的Collector换行导致7项失败，恢复清单要求的LF字节后10项Profiler复测通过，当前部署文件也通过清单哈希加载。余下1项CLI参考文档断言在未修改的原仓复现：README为CRLF，归一化后内容完全匹配。未重复完整套件，也不宣称完整测试全部通过；证据见companion-final-contracts.json。

证据、原文档备份、源码/构建哈希及回滚包：D:/services/weknora/pilot-access/recovery-20261008-current/。本轮未提交、推送、发布标签或改父仓gitlink。


状态: Draft

Owner: shidai3 工作区 Owner

维护责任: 后续正式交付由 Owner 指定；本目录当前提供已模拟验证的同步工具代码，尚未启用真实写入。

将获准 Git 仓库的已提交文档变化同步到本地 WeKnora，覆盖人类和 Agent 的提交、修改、重命名、删除与回滚。源仓库继续是事实源，知识库是可重建的检索副本。

Owner 于 2026-10-06 批准本目录的 dry-run，随后批准开发自动写库与时间调度代码及合成/mock验证。采用两个独立通道：`local` 只读本地 HEAD 提交树，供开发使用；`official` 只读明确指定的已缓存远端正式分支，供正式知识库使用。未提交文件不进入任一通道，二者的来源身份与目标库分别隔离。真实来源、目标库、写入能力、删除阈值、周期、模型处理/外发范围和新写入凭据仍须一次精确确认。

本目录已初始化独立本地 Git 仓库，尚无提交、remote、版本号或父仓 gitlink。默认没有登记来源，writer、network 与 scheduler 关闭，生产索引验证器未接入；不得将模拟 adapter 当作生产就绪。没有抓取远端、安装 Hook/系统任务、启动常驻 worker、读取真实凭据、写 WeKnora、调用模型或扫描真实资料。dry-run 与 Companion 桥接只输出来源元数据，不输出正文。

## 使用

依赖为已安装的 Python 3.12 标准库和 Git CLI。路径必须是已获准源仓根目录；`--include` 逐项指定 Git 相对目录或文档，支持重复。默认仅接纳 UTF-8 Accepted Markdown；无状态的知识条目须额外明确使用 `--allow-unmarked`，Deprecated、archive、凭据及会话等路径仍排除。

```powershell
Set-Location D:/code/shidai3/P8000/knowledge-sync
python -X utf8 -B knowledge_sync.py --repo D:/approved-repo --repo-id example --channel local --include approved-docs
python -X utf8 -B knowledge_sync.py --repo D:/approved-repo --repo-id example --channel official --ref refs/remotes/origin/main --include approved-docs
python -X utf8 -B -m unittest discover -s tests -p 'test_*.py' -v
```

当前 Windows 的 `python` 已核实为 `C:\Users\33272\miniconda3\python.exe`（3.12.9）；也可将命令前缀替换为 `& 'C:/Users/33272/miniconda3/python.exe'`。`-X utf8` 保证重定向时中文及其他 Unicode 标题的 JSON 使用 UTF-8。`python3` 是 WindowsApps 别名，不作为本原型入口。

命令中的源仓和范围是用法示例，实际清单仍由 Owner 指定。输出为 stdout JSON。`--baseline <完整 commit SHA>` 可生成新增、修改、删除建议及内容完全相同的重命名候选；删除建议表示合格文档集合减少，状态、编码或大小排除也可能触发，不能直接认定文件已删除。服务层另核对固定提交树的真实缺失、映射来源所有权和删除阈值；范围排除或降为 Draft 的文档会要求保留审查。内容相同且无歧义的改名保留文档 ID；改名并编辑列为新增与删除建议。

Companion 固定调用 `companion_api.py --operation status|dry-run|sync-once|scheduler-status`。stdin 仅接受已登记的 `sourceId` 与 `channel`，全局 status 可用 `{}`；不接收路径、命令、ref、目标库或批准开关。`sync-once` 只持久化入队并返回 `jobId`，不会启动或执行写入。创建 draft、写来源 metadata、publish 和索引确认分阶段恢复；未知 POST/publish 结果先核对，不能盲重发。所有权检查和恢复重试期间取消也会读取最新取消状态。

真实部署使用本机 `sources.json`（Git 忽略）登记精确来源与审批指纹。`credentialEnv` 可登记固定 `KNOWLEDGE_SYNC_API_KEY` 名称，status 仅检查名称存在，不读值；不能用一个全权限 key 合并开发/正式库权限。每次生产请求重读当前审批，常驻 worker 每轮重读登记；撤回、过期、scope 变更或关闭开关均阻止后续请求。内部 scheduler 默认关闭，系统自动启动未注册。生产索引验证器仍需接入并验收，`HTTP 200`、parse `completed` 或 chunk 列表均不能单独推进完成检查点。

正式通道不会刷新缓存，明确报告其新鲜度未知。Git 替换对象通过固定环境与 `--no-replace-objects` 禁用，防止相同 commit/blob 身份被本地 replace 规则替换。登记后的gitlink递归读取精确固定SHA正文，缺对象离线拒绝，不用子仓HEAD代替；未登记pin的纯dry-run只给metadata，服务计划拒绝入队。本轮仅运行合成仓测试，未扫描、导入真实业务或个人资料。

- [实施方案](implementation-plan.md)：数据流、权限、增量同步、恢复、验收和实施前的决策。
- [本地验证证据](validation-evidence.json)：最终源文件哈希、完整模块合成测试、OCR Delegation 及范围限制。
- [Companion 协议](protocol-v1.json)与[阶段交接](phase-handoff.json)：稳定接口、实际代码验证状态和未发布/未启用边界。
- [P8000 工具集](../README.md)：工作区工具定位。
- [项目目录规范](../../P0002/09-governance/engineering-standards/project-layout.md)：目录与独立工程边界。
- [Git 合同](../../P0002/09-governance/engineering-standards/git.md)：未来独立代码仓和父仓 gitlink 的交付要求。

WeKnora 服务与原生 MCP 继续复用 `D:\services\weknora`。本工具只负责同步，不另建 MCP 服务。`P7000` 保持知识整理、跨项目经验和个人知识的来源角色，不承载同步程序。

## 更新记录

- 2026-10-11（认证阶段UNKNOWN诊断）：固定投影HTTP路由阶段、transport及数字状态，不读取原始敏感HTTP异常/响应内容；新增Owner匿名ConnectionCheck，真实入口在隐藏输入前先核health。只读确认两个canary库/writer=0、journal不存在，认证成功与否仍未断言；保留所有恢复/UNKNOWN边界，不代认证/重放/删除/修生产库。28项本次Owner模块合成回归通过，未运行实际网络探测新入口（原health读取受权限拒绝已停该路线）。

- 2026-10-11（Owner启动器修复）：只读定位首提示前停止为多条python.exe来源被拼入FileName；改为只取第一个Application并校验文件，增加固定code/stage/白名单ownerStep及无认证LauncherCheck，保留真实操作本人确认与恢复状态。4项本次相关入口合成回归通过；未登录、创建库/key、读取秘密或改PATH/执行策略，命令改用已核PowerShell7绝对路径。

- 2026-10-11（本机Owner交接代码、未执行真实操作）：增加固定双库/最小writer的受控交互入口，原登录认证、本人隐藏输入及最终确认、CurrentUser DPAPI安全捕获/保存、精确新writer内存hash绑定、原子状态/单实例锁、只读实际空库/FTS证明以及取消/未知/部分成功恢复。默认离线预览，合成验收使用无socket服务和测试DB/测试凭据；保持旧reader-identity权限、旧资料和服务候选包，不自动认证/创建、启用同步或调度。真实结果待Owner本人操作后再核验。


- 2026-10-09（离线开发、未发布）：增加显式递归父pin正文/来源链、同blob出处操作、schema3持久索引收据和纯逻辑IndexVerifier；UNKNOWN阻断，需重新publish的恢复及证明要求版本推进。新增定时双轨、索引完整性、恢复和publish未知结果合成验收；继续实现两个真实GET接口的离线观察合同，缺实际索引inventory/task终态始终阻断。Owner批准0.1.0.1，初始化四段版本/Agent署名门；正常本地检查点仍须记录权限结果。生产provider/source/凭据/运行启用尚未完成。

- 2026-10-06（代码验收阶段、未发布）：新增独立 Companion JSON 桥接、登记源双通道、SQLite outbox/映射/租约、分阶段 draft→来源标记→publish→索引确认 REST 适配、内部时间调度和显式 worker 入口。`sync-once` 只持久化入队，不执行网络写入；默认 sources 为空、writer/scheduler/network 关闭，生产索引验证器未接入。37 条同步定向合成/mock测试通过；取消竞态、未应用结果恢复后的取消终态和常驻审批缓存均修补并通过只读复审。完整模块验收及限制见验证证据，不声明生产可用。未导入真实资料、未读取写入凭据、未启用任何调度、未提交或推送。精确接口见 [protocol-v1.json](protocol-v1.json)。

- 2026-10-06，初始 dry-run 原型、未正式交付：建立 Git → WeKnora 方案与双通道 dry-run CLI；13 个合成用例验证提交树隔离、正式引用不随未推送提交推进、正式父仓固定 gitlink、跳过 Hook 后的全树差异补查、排除规则、rename/delete/revert、路径边界、commit/blob replace 保护、缺失 promisor 对象时离线拒绝（合成本地远端请求数为零），以及 CLI 的 UTF-8 元数据输出。该初始阶段未实现 writer；后续代码状态见上一条。
