# Owner 本机交接入口

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

状态：入口已交付并完成合成验收；Owner本人登录接口返回401，认证未通过；两库创建和 writer 保存尚未执行。Owner 是本机当前用户。仅知识库/同步工具，Python 3.12 + PowerShell 7；不涉及游戏、Companion 或 Cocos。

当前Owner已经本人尝试认证并收到HTTP_RESULT_UNKNOWN，认证成功与否未确证。暂不运行下方一般用法中的OwnerOperate；先按“HTTP_RESULT_UNKNOWN 后先定位”一节仅运行ConnectionCheck，不再次输入账号。库/writer创建与保护保存仍未执行。

先看入口（完全离线，不读取任何已有配置或秘密）：

    & 'C:/Users/33272/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe' -NoProfile -File 'D:/code/shidai3/P8000/knowledge-sync/Start-OwnerCanary.ps1'

运行无 socket 的合成服务验收，所有凭据和数据库都是测试 fixture：

    & 'C:/Users/33272/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe' -NoProfile -File 'D:/code/shidai3/P8000/knowledge-sync/Start-OwnerCanary.ps1' -SyntheticCheck

Owner 本人在另开的本机交互控制台操作：

    & 'C:/Users/33272/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe' -NoProfile -File 'D:/code/shidai3/P8000/knowledge-sync/Start-OwnerCanary.ps1' -OwnerOperate

本机 PowerShell 7.6.5 位于上述 Codex 运行目录，不在普通用户 PATH；无需安装或修改 PATH/执行策略。Python 3.12.9 为已有 miniconda3/python.exe，用户 PATH 已包含；启动器只取命令解析的第一个 Application，不把多个 python.exe 路径拼接。真实分支使用 .NET ArgumentList 与 ConvertFrom-Json -AsHashtable，不用 Windows PowerShell 5.1 替代。

仅核对子进程启动及协议可加 -LauncherCheck：运行固定 Python 合成程序，不调用 Owner 后端、不读取 state/DB、不认证或创建。它不能与 -OwnerOperate/-SyntheticCheck 同用。停止提示现含固定 code、stage、已白名单 ownerStep；可以回传这三项及非秘密回执，不要回传原始异常、stderr、密码或token。CHILD_NO_PROTOCOL 表示后端没有返回协议，此时保留状态并核对已有对象，不推断没创建、不自动重试。

2026-10-11 11:07:55UTC 只读固定名称 metadata 检查：两个 canary KB=0、writer=0，owner-canary-handoff-state.json 不存在。本次 Owner 首次提示前停止已只读复现为旧启动器把两条 Python 路径拼接，发生在 process-start 之前；没有读取 token/hash。计数只对应该观测时间，未来停止仍须先核对保留状态。

此入口没有 token、password、host、tenant、KB 或权限的命令行参数；管道/重定向模式在登录前拒绝。代理本轮不运行真实模式。无需 Owner 编程、复制 Bearer 或手工拼请求。单实例文件锁贯穿整个操作，第二控制台在任何认证前拒绝；不改ACL/身份/权限。

## HTTP_RESULT_UNKNOWN 后先定位，不重跑认证

2026-10-11 12:54:31UTC 只读固定名称元数据检查：两个canary KB=0、writer=0、恢复journal不存在，未提交canary创建意图/ID。auth_tokens总量32，与先前基线相同；本轮时间窗口无新增记录，但服务源码忽略token记录持久化错误，因此不能据此断言登录没成功。只有Owner的登录CONFIRM和隐藏email/password提示，旧异常码又未记录路由，所以严格定位为认证阶段（login或auth/me），没有证据显示进入canary创建。旧日志文件为0字节，没有可供进一步定位的安全HTTP状态。

当前代理的无认证health读返回访问拒绝，CIM入口也不可用，均停止该路线；Get-Process未找到原weknora进程，但这不等于已读到Owner实际HTTP结果。先由Owner本人只运行这个**不需账号、不读state/DB、不创建对象**的探测：

    & 'C:/Users/33272/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe' -NoProfile -File 'D:/code/shidai3/P8000/knowledge-sync/Start-OwnerCanary.ps1' -ConnectionCheck

只回传输出的ok/code/stage/transport/httpStatus。health=ok之前不要再输入账号、重跑OwnerOperate；探测失败保留现有state/锁文件，不删除或重置、不启动迁移/生产修复。连接检查只是GET127.0.0.1:8081/health，关闭代理/跳转/重试，3秒超时，不读取密码、token、hash、请求正文或原始响应到日志。

修订后的OwnerOperate也先匿名health成功才显示登录确认。HTTP异常将带固定阶段service-health/auth-login/auth-identity/kb-create/writer-create/kb-detail，transport为固定分类、httpStatus仅数字。它们区别安全连接失败/HTTP401/403/5xx/JSON无效，不打印服务错误message/body/header。创建阶段的UNKNOWN仍保留pending，只核对原对象和ID，不重发POST。健康通过后是否再次认证，以本任务下一次准确核验为准，不假设前次失败或清理会话。

## 本人可见步骤

1. 确认原账号登录，然后隐藏输入 email、password。使用与官方客户端相同的 POST /api/v1/auth/login；JWT 和 refresh response 不进入配置/日志。GET /api/v1/auth/me 必须证明 active tenant1、用户 active、tenant1 membership 为 Owner，否则停止。不会切换 tenant、改密码/签名或创建账号。
2. 每库提交前明确显示操作、通道，只有本人键入精确 CONFIRM 才创建。固定 shidai3-dev-canary、shidai3-official-canary，永久 document、keyword=true，vector/wiki/graph=false，模型 ID 空，profile/vector_store=null，VLM/ASR/extract/question/autotag 均关闭。GET 详情检查配置，并独立按新UUID只读查询实际空库/索引/处理中计数；查询失败硬拒绝。两 UUID 必须不同且不能是旧 pilot。
3. 每把 writer 的最终提示明确包含 CREATE + DPAPI SAVE；本人 CONFIRM 同时批准此次创建和本机保护保存。名称固定为 shidai3-dev-canary-writer、shidai3-official-canary-writer，tenant scope、full_access=false，只绑定对应一个新库，capabilities 仅 ingest+retrieve。到期按此次最终提交的 UTC Unix 秒 +604800；核验服务 createdAt 与提交相差0–60秒，因此实际有效期不超过7天，真实 expiresAt 交给后续 policy 使用。
4. token 不显示、不经过官方 CLI raw POST stdout。在同一 Owner 进程内立即用 Windows CurrentUser DPAPI 封装为现有 ConvertFrom-SecureString 兼容格式，独立新路径 CreateNew + fsync + Windows 原子不覆盖 rename。密码/返回 token 不写 journal；PowerShell 隐藏输入 BSTR 用完清零。Python 不承诺不可变字符串的物理内存清零，仅清除引用且不持久化明文。
5. 只输出非秘密回执：tenant1、两 KB UUID、writer ID/名称、真实到期时间、两个变量名称及 protectedSaveReady。不要发送 API 完整响应或任何 token。本交接入口不导入文档、不启动 worker、不自动加载 writer 环境变量、不生成运行 sources/policy；部署任务随后继续已批准的备份/短重启和真实双轨验收。

## 现有保护机制与身份绑定

复用 D:/services/weknora/cli/Enter-PilotCli.ps1 / Issue-RecoveryReader.ps1 的 CurrentUser DPAPI + SecureString hex 格式；已用合成值在 Python 与 PowerShell 间互读。无需新 ACL、网络设置或权限。需要在原 Windows 用户/机器上操作，无法把 DPAPI 文件当作跨机器明文配置使用；秘密备份仍由 Owner 保管。

只使用已存在的运行目录，不建立新私密目录或 HTTP 秘密接收服务：

- 独立保护文件：D:/services/weknora/cli/private/shidai3-dev-canary-writer.dpapi、shidai3-official-canary-writer.dpapi。
- 非秘密恢复状态：D:/services/weknora/pilot-access/owner-canary-handoff-state.json。
- 单实例锁：上述state路径后加 .owner.lock，Windows byte-range lock，退出/终止后自动释放，不删除状态或已有对象。
- 只读 DB：D:/knowledge-data/weknora/pilot-access/weknora.db，mode=ro + query_only，永不迁移/修复。

现有 /auth/reader-identity 限制 retrieve-only，明确拒绝 writer；本实现保留该门，不扩大服务权限。也不会调用 API key 列表，因为列表可能返回旧 key 明文。未来 Owner 进程只查固定两个新名称的非秘密授权元数据，并对新 writer 精确 ID+tenant1+name+tenant scope+非全权+未撤销记录，读取 key_hash 在内存中与 token 的 SHA256 常数时间比较；hash 不出进程/回执，再用此 writer GET 自己的新 KB 验证实际认证，复读授权确认未变。旧 token / api_key 列从不读取。

空库证明另用同一个只读事务，按新UUID计数 knowledges/chunks/lite_embeddings 与 FTS MATCH（包括软删除/orphan行）、durable task_pending_ops、pending/running processing spans；存在原生canary操作表时也数该新库操作。缺表/查询失败直接停止。这只证明数据库内这些记录和持久任务为空，不宣称所有内存/Redis后台队列为空；不连接Redis、不触发恢复。与 canary store.go 原有 adoption preflight 使用同样的前三个表及 FTS边界。此入口只用于首次空库凭据交接，实际导入后不再用它重新发行/更新凭据。

## 取消、结果不明和恢复

每次创建前先持久化 pending 意图；取消不会自动执行下一次提交。重开同一入口会保留已完成通道、同一 ID 和原 expiresAt，不刷新期限或重建。

KB 响应丢失时只读查询精确新名称，必须匹配该实例 description 标记、tenant、配置和实际空计数，再由本人确认采用；没有匹配行则停在 UNKNOWN，不重发 POST。

writer 响应丢失时只查精确新名称的 metadata。若创建结果未找到、重复名称、权限/过期不符，停止，不重发。如果保护文件已落盘但 journal 尚未完成，核验同一 writer 后恢复。完整密文停在 .dpapi.pending 时，验证后完成原子 rename。若首次 token 没有成功收到/落盘，只允许 Owner 把自己仍保有的原 token 隐藏输入并确认保护保存，必须绑定同一 ID/授权；不能取得原 token 时仍受阻，需要 Owner 后续另行决定撤销/重新发行，本入口不会代做或盲目生成第二把 key。损坏的保护文件也会停止，不覆盖。

API transport 固定127.0.0.1:8081，只允许上述登录、两种创建、auth/me和单KB GET；关闭代理/跳转/自动重试，15秒超时。异常、HTTP body、密码、Bearer/token 不进入 stdout/log。正常控制台仅固定提示和经过白名单构造的非秘密回执。

回滚代码只撤回此次两个新增根源码文件、测试及本文/README条目；不要删除恢复状态、密文、真实KB/writer或覆盖数据库。当前候选服务包未改、未安装，调度/自启保持关闭。源输入默认采用已核当前父提交，两根获准文档路径/blob/内容与原冻结一致，不要求 Owner 理解或选择 Git。
