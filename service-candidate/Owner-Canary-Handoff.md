# 双轨 keyword canary：Owner 本机交接

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

状态：候选已构建、离线验收通过；尚未部署或完成真实验收。2026-10-09 的授权涵盖两个新试验库、仅已提交的根 `AGENTS.md` / `AI_CONTEXT.md`、本地 keyword、单库 ingest+retrieve writer、首轮禁止删除、一次手动同步，以及备份后短重启。凭据由 Owner 本机配置；本轮不启用调度或系统自启动。

## 2026-10-11 当前检查点（客户端核验已补充）

12:54:31UTC认证UNKNOWN最新落点：Owner本人CONFIRM并隐藏输入email/password后返回旧HTTP_RESULT_UNKNOWN。只读journal不存在、两固定canary库/writer均0，未产生canary提交意图/ID；auth_tokens总量仍32且本轮时间窗口无新增，但不能据此断言登录未成功（源码对token记录持久化错误不阻止成功）。严格定位为认证login/auth-me阶段，先不重跑。代理无认证health被10013拒绝、CIM拒绝均停止该路线，未取得Owner的实际HTTP状态；Get-Process没找到原weknora也不能替代HTTP结果。已补固定stage/transport/数字httpStatus及Owner无账号ConnectionCheck，28项相关Owner模块合成回归通过，原journal/DB未改；[最小下一步](Owner-Canary-Entry.md)只运行匿名健康探测，暂不再次认证或创建。没有读取密码/token/hash/原始敏感响应、代认证、重放提交、清状态或生产库修改。

11:07:55UTC 只读新增观测：两个固定canary库/两个writer均0，恢复state不存在，不读取hash/token。Owner首提示前的笼统安全停止已定位为启动器把本机两个python.exe路径拼接成不存在的FileName；已修复为只取第一个Application，停止输出固定code/stage/白名单ownerStep。新增无Owner后端/DB/state/auth的-LauncherCheck，本次4项相关合成回归通过。Owner用[操作说明](Owner-Canary-Entry.md)中已核PowerShell7绝对路径命令重新启动；未来有部分成功仍按原journal恢复，不重置/盲重发。真实凭据步骤仍由Owner本人，本轮没有认证/创建/保护保存或服务部署。

现有原知识库查询已恢复：先前02:19UTC实测127.0.0.1:8081 health=ok、PID22384，原guarded EXE未变，受保护reader实际P8000 keyword查询3条。旧1647文档/1493 pending、10项后台触发表和禁用MCP guard保持；旧知识/FTS/任务指纹506bf696f6cde92b9a2f3bb8a0eca7026843c4839482267017fd6c53d7360e79与10/09一致备份相同。本次仅离线客户端检查，没有重启或安装candidate，健康状态沿用该时点证据。

真实canary尚未开始：此前只读两新库/两受限writer不存在，sources.json/两ledger未登记，双轨status/dry-run为POLICY_NOT_APPROVED。没有sync-once、实际索引收据或completedSHA。现有reader查询和合成客户端预演不能算同步成功。

客户端现在有一条已实机验证的无凭据预演入口：[Start-OwnerClientReview.ps1](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner-client-check-20261011/Start-OwnerClientReview.ps1)。安装的官方CLI raw JSON入口四次--dry-run均原样保留method/path/body，隔离空凭据配置、合成UUID、合成目标收到请求0次；没有联系真实8081或调用实际写接口。证据：[cli-offline-receipt.json](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner-client-check-20261011/cli-offline-receipt.json)。

上述客户端缺口现已补齐为本机 Owner 交接入口，可启动并已做合成服务验收；真实Owner认证、两库创建、writer提交和保存仍未执行，真实canary仍未开始。入口：[Start-OwnerCanary.ps1](../Start-OwnerCanary.ps1)，步骤/恢复：[Owner-Canary-Entry.md](Owner-Canary-Entry.md)。默认只离线预览；-SyntheticCheck使用无socket官方合同合成服务；-OwnerOperate仅在Owner本机交互控制台可用，输入/输出重定向在登录前拒绝。Owner隐藏输入原账号email/password，经原官方/login和/me验证tenant1 Owner。每个KB/writer POST前本人CONFIRM；writer提示明确CREATE+CurrentUser-DPAPI SAVE，返回token仅在同一进程保护保存，不打印、不调用key列表、不复制Owner Bearer，不新增HTTP秘密接收服务或改ACL/身份/权限。

入口固定两库/单库最小writer和实际最终提交+7天期限，独立只读核验空库/FTS与持久任务，token同进程绑定新writer ID+范围+hash并GET自身KB验证认证，保持既有reader-identity的retrieve-only门。单实例锁贯穿全流程；取消/部分成功/UNKNOWN先核对同一journal与对象，不盲重POST。原token若首次未收到/保护落盘且Owner也没有它，则仍受阻，本入口不会创建第二把key。DPAPI同现有PowerShell格式，合成互读通过，无新安全设置。真实配置/密文路径及限制以新Owner入口说明为准。


可见步骤与精确页面限制：[Owner-Client-Visible-Steps.txt](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner-client-check-20261011/Owner-Client-Visible-Steps.txt)。真实页面为/platform/knowledge-bases及/platform/settings→发布集成→API→API Keys；stock库表单把keyword/vector联动并补默认模型，stock key创建无expiry输入，不能直接按默认项最终提交。精确非秘密KB/writer body保留于上一阶段frozen-alignment-20261011；后端支持expires_at_unix，此次只做公开源码与合成dry-run核验。

源输入默认建议已明确：采用local=93ab0923b5f086f96f7755545f876e7634e8a05b及official缓存origin/main=19144d8ded15b286f658e657267f5e42b8d8bb6b。2026-10-11T02:46:49.891918+00:00再次核验两获准路径、blob和内容字节与原批准冻结1b6fd01462bd0ff32a5e05499824a9b72e037d48完全一致，ref前后稳定。无需Owner再理解或选择Git SHA。未fetch，official远端新鲜度unknown_cached_ref；工作树有未提交修改但同步源仅使用已提交Git对象，工作区规则AGENTS另行读取。内容若变则停止报告，不能盲用。

新默认候选：[candidate-plan-default.json](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner-client-check-20261011/candidate-plan-default.json)，规范化SHA256=d1f59281fce5fbe28e4740bd6727fdd93e278452522e25b0d97b2ee26f0c0bf9；[source-default-recheck.json](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner-client-check-20261011/source-default-recheck.json)。旧9f4810候选保留作历史。新计划仍executable/activePolicy/approvedForDeployment=false，真实UUID/runtime operation/scope为空；默认建议不等于真实运行policy，没有写库或ledger。

凭据最终提交、安全接收、隐藏输入及保存仍只由Owner本人完成。Owner本人运行新入口完成后，只需返回非秘密tenant1/两KB UUID/两writer创建与保存就绪状态及实际expiresAt，可用[Owner-Reply.template.json](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/owner-client-check-20261011/Owner-Reply.template.json)，不返回完整响应或token。到期为实际创建+7天，不能沿用旧样例。

后续本任务按已批准范围继续：真实空库和单库权限/到期metadata核验、真实冻结policy、届时一致备份复核/刷新、已核candidate短重启、一次手动双轨与真实索引/重载/CLI/旧fingerprint验收。不开定时/自启，不碰游戏或Cocos-MCP。只读mapper/reviewer为prompt-emulated，无原生agent_type或硬隔离证明。

当前集中交接：[Owner-Current-Handoff.txt](C:/Users/33272/Documents/Codex/2026-10-09/task/canary-current-20261011/Owner-Current-Handoff.txt)。下方10/09候选阶段的旧阻塞保留为历史；本次没有自动审批拒绝、ACL/身份/网络变更。

## 10/09 候选阶段记录（历史）

- Windows amd64 服务候选构建成功，完整 native canary 测试、handler/container 边界测试通过。服务内原有 25 个 reader/keyword/recovery 修复文件作为基底继承；其中 repository.go 本轮另加无迁移构造路径，其余24个最终字节保持，见 `tests/native-canary-inherited-guards-20261009.json` 和最终文件 manifest。
- Go 原生 SQLite FTS 与 Python 持久 outbox 的合成端到端完成四次写入、双轨检查点关闭重载、实际 FTS 命中；首次 POST 回复丢失时按持久 operation 查询恢复，没有重复提交。合成收据不会显示真实 indexed。
- 本执行环境匿名 8081 `/health` 返回 WinError10013；现有 `ReaderMetadataGuard.py --metadata-only` 返回 READER_METADATA_UNAVAILABLE；原服务 Git 访问 Permission denied。这三条入口已停止，没有改 ACL、换凭据或绕路。可见进程为空不能证明实际服务停机。
- 尚未访问运行库、创建试验库/密钥、安装 sources.json、备份运行库、替换服务、短重启、真实导入或调用模型。历史 1647 文档 / 1493 pending 是保护基线线索，需 Owner 本机重新量测；不得自动恢复旧队列。旧禁用端点 `8f9d7d6c-cec1-4017-b4a2-ed648f372182` 保持禁用。

## 1. 本机基线和备份

Owner 在已有授权入口检查实际运行版本、进程、启动脚本、SQLite 路径及健康。先运行现有 metadata-only guard，再使用现有 `Enter-PilotCli.ps1 -UseProtectedRecovery` 验证 reader；只保存状态码、计数、ID、哈希，不输出 DPAPI/密钥/正文。若同一支持入口仍拒绝，停止该入口，报告拒绝；权限扩大另行批准。

安装前在既有受保护本机备份位置保存当前二进制、依赖 DLL、公开启动参数，以及一致 SQLite 快照和文件存储。使用 SQLite backup API 或受控停机快照，不能只复制活动 `.db` 漏掉 WAL。启动配置若含秘密，只能留在 Owner 本机受保护位置，不能进入本候选 ZIP、项目或回执。记录备份 SHA256、数据库 quick_check、非 canary 文档/chunk/FTS/旧任务分状态计数及稳定行指纹；这些数字在短重启和试验后必须一致。此处是安装前基线和一致备份复核要求；10/09已完成一致备份，继续安装前须确认仍覆盖届时状态，必要时刷新。

## 2. 两个全新空库和本地 writer

在现有 Owner 登录上下文创建 `shidai3-dev-canary` 与 `shidai3-official-canary`，使用原生 UI/API 的显式配置。不要使用裸 `weknora-cli kb create`，该 CLI 的创建参数不能表达完整 keyword-only 策略。

API 请求配置模板为 `Create-Canary-KB.template.json`；每库替换 name，保留全部关闭项。核实响应的实际 tenant/UUID/name/type、空文档/chunk/索引，以及 keyword=true、vector/wiki/graph=false、embedding/summary/VLM/ASR model ID 为空、profile_config=null、question/extract/autotag 关闭。服务会再次验证并拒绝非空库或不匹配配置。旧 pilot `da1051a5-3fff-4198-ba55-017ea79df9c3` 禁止作为目标。

每库仅一个新 writer，名称必须分别为 `shidai3-dev-canary-writer` / `shidai3-official-canary-writer`，full_access=false、非 platform admin、knowledge_base_ids 只含对应 UUID、capabilities 仅 ingest+retrieve、明确过期时间。最终密钥仅 Owner 本机放入进程环境 `KNOWLEDGE_SYNC_LOCAL_CANARY_KEY` / `KNOWLEDGE_SYNC_OFFICIAL_CANARY_KEY`；不能使用旧 retrieve reader 或 Owner 全权 key 替代。不得将密钥置于 argv、JSON、Git、日志或对话。

## 3. 冻结批准的四条操作

原批准冻结两轨为 1b6fd01462bd0ff32a5e05499824a9b72e037d48；现默认建议采用10/11检查点已核当前父提交，两获准路径/blob/内容完全相同。只读committed blob，official远端新鲜度仍未知；真实执行前再次核验，ref或内容改变时报告，不盲用。下列命令供部署者在真实UUID和到期就绪后使用，不是Owner需理解的Git选择步骤。

在知识同步目录运行以下命令；三个占位参数必须替换为实际值，输出目录必须不存在：

```powershell
python -X utf8 -B prepare_native_canary.py --tenant-id <实际tenant> --local-kb-id <新dev-UUID> --official-kb-id <新official-UUID> --expected-head 93ab0923b5f086f96f7755545f876e7634e8a05b --expected-official 19144d8ded15b286f658e657267f5e42b8d8bb6b --output-dir <全新本机目录>
```

生成 candidate-sources.json、service-canary-policy.json、frozen-plans.json。该步骤只读 Git 对象、使用内存规划，不读取密钥、不建真实 ledger、不访问服务。只允许根两个文件、两个通道共四条确定 operation，七天 scope 过期、first-add only、maxDeletes=0、无 gitlink、全部五个 processing 开关 false、scheduleEnabled=false。冻结计划中的 operation_id、内容 hash、完整 desired 绑定必须保留，不能重新生成 instance 覆盖已采用的运行库。

## 4. 已批准维护窗口安装

核对交付包 `native-canary-package-receipt.json`、文件 manifest 和二进制 SHA256；候选由历史 upstream-v0.8.2 文件副本、25 个既有 overlay 与本轮 canary 修改组合，原服务 Git 在此环境未重新验证，不能将 ldflags CommitID 当成新的 Git 核验。

只在备份与基线成功后，用现有启动机制替换候选二进制和随包四个 DLL，保留现有公开/秘密配置、端口与绑定。设置服务进程环境 `WEKNORA_SYNC_CANARY_POLICY=<生成policy绝对路径>`、`DB_DRIVER=sqlite`、`RETRIEVE_DRIVER=sqlite`、`AUTO_MIGRATE=false`、`REDIS_ADDR` 严格为空（空白也拒绝）。未满足这些条件服务会在初始化前拒绝启动。不要通过普通无 policy 重启规避试验模式：该模式跳过旧 datasource、wiki recovery、housekeeping、审计保留、skill/fork reaper、临时附件清理这七个入口，旧任务保持暂停。普通 dispatcher 保留；仅 canary 自有持久操作允许 Resume。

试验检索器使用只读现有 schema 的构造路径：不运行旧 lite_embeddings AutoMigrate/FTS DROP+重建/历史 dimension vec 建表；缺失或不兼容 schema 会停止。历史 DB VectorStore 不加载，也不允许惰性加载；Langfuse、旧 IM 频道、docreader 连接、Neo4j 认证、DuckDB 扩展下载安装在本试验模式禁用。正常无 policy 模式保留旧行为。该隔离是候选代码与合成证据，不是实际运行的观测。

本机确认两库仍为空且配置匹配后安装生成 registry 为本项目 sources.json；先保留原 registry（如有）。通过现有授权启动脚本短重启，验证原 reader/关键词 guard、旧端点仍禁用、上述旧行计数/指纹未变。试验自有 schema/vocab 初始化仅启用 policy 时进行；不跑全局 AUTO_MIGRATE 或旧 repair 脚本。

## 5. 一次手动双轨验收

先调用 `companion_api.py --operation status` 的每通道请求，要求 ready/writerReady=true、completedSHA=null、indexed=false；缺凭据、scope/策略/服务错误必须 blocked。每通道用 stdin 输入 `{"sourceId":"shidai3-canary","channel":"local"}` 或 official：

```powershell
'{"sourceId":"shidai3-canary","channel":"local"}' | python -X utf8 -B companion_api.py --operation dry-run
'{"sourceId":"shidai3-canary","channel":"official"}' | python -X utf8 -B companion_api.py --operation dry-run
'{"sourceId":"shidai3-canary","channel":"local"}' | python -X utf8 -B companion_api.py --operation sync-once
'{"sourceId":"shidai3-canary","channel":"official"}' | python -X utf8 -B companion_api.py --operation sync-once
python -X utf8 -B sync_worker.py --once
```

每通道只入队一次。入队回执 queuedOnly=true/writePerformed=false，仅表示持久队列；重复执行 `sync_worker.py --once` 可以推进同一批次，禁止循环重跑 sync-once。每次 tick 检查状态，不用无限循环；新操作正常异步 queued/running/done，首次 POST UNKNOWN 先按 operationId 查询，不能盲重提。失败/过期/矛盾证明停止并保留 ledger 与任务回执。

成功时每通道两条非 synthetic receipt、indexedDocuments=2、pending=0、queuedCount=0、indexed=true，targetSHA/completedSHA 等于对应冻结根提交。退出并重载 ledger 后重新读取同一 completedSHA；核对每条 taskId/documentVersion/content hash、完整真实 FTS inventory 的 backendInventoryHash。重载 checkpoint、HTTP200、parse completed 与检索命中分别记录，不能互相替代。

在 Owner 受控本机进程将对应单库 writer 临时作为 WEKNORA_API_KEY（不打印值），WEKNORA_HOST=http://127.0.0.1:8081、WEKNORA_LOG_LEVEL=error，运行官方 CLI：

```powershell
& D:/services/weknora/cli/weknora-cli.exe search chunks 'shidai3' --kb <对应新UUID> --no-vector --keyword-threshold 0 --limit 3 --format json
```

只记录 ok、命中数、文档 ID/path/source metadata；原始结果含正文，不回传模型/日志。验证对应两个 approved path 和 parent_commit/source_commit 的绑定；同时对 foreign KB/tenant 和 key management 只读请求预期403。DELETE、stock manual/batch/publish、默认 vector 请求预期403，不实际执行删除。CLI search docs 只是元数据，不是索引验收。最后核对原非 canary 计数/指纹不变；差异即失败。

## 6. 失败与回滚

停止 canary worker，保留 outbox、原生任务和失败收据，不删除目标、不清库、不重建 ledger、不重新生成 operation。先本机撤销两个新 writer/清理进程密钥，再停 canary 服务并按维护窗口恢复之前的二进制/配置；不能在旧二进制上遗留可写 canary key。policy 撤下但仍运行本候选时，保留名称的 writer 仍阻止 stock 写入。不能将旧 DB 快照覆盖 Owner 后续无关写入；数据库恢复需核对时间和全库变化，必要时单独决定。保留已经采用的 policy instance 以便在同一候选下只恢复自有 queued/running。

## Companion 字段与定时验收

精确 contract 见 ../protocol-v1.json：ready=writerReady，仅表示本通道批准/凭据/实时能力探测可用；queued=queuedCount>0（只数 queued 操作）；indexed 要求目标等于整批 completedSHA、pending=0、活跃映射非空且每条有非合成有效收据。indexedDocuments 不计 synthetic。三个布尔值互不替代；全局结果需看 data.sources 每通道。

当前 scheduler 禁用/过期/双轨/恢复测试是合成验收；真实定时与驻留/重启/备份恢复未执行。后续启用需新的调度授权并扩展 first-add frozen canary 协议；本轮 native_canary_adapter 明确拒绝 scheduleEnabled=true，不能直接把 interval 改成300启用。现有34个 staged文件、未出生HEAD与VERSION0.1.0.1保持，提交被系统hook权限拒绝后未重试、未绕过；未push/tag/父pin。候选ZIP是精确可恢复文件检查点，不是Git提交。
