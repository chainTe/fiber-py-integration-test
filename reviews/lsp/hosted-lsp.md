# Hosted LSP 集成测试设计（G1 人工评审）

本轮是一份 Hosted LSP 对外行为评审文档，覆盖注册到结算的连贯生命周期。复选框只表示映射存在性，不表示执行通过。用户已对本任务明确免除逐项确认的程序门禁；仍保留独立审查、映射和实际运行证据。

## 固定输入

- 产品：[chenyukang/fiber lsp-support](https://github.com/chenyukang/fiber/tree/8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8)，head `8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8`。
- 参考 base tip / merge-base：`23a597c5e81d06f5c49a3744dffe244e73530a60`（同仓库 develop）。分支差异很大，本次只分析 LSP 相关行为，不表示全 diff 已评审。
- 集成仓库 main：`4be658da51967421be9d58543fe0d5c2888e324b`。
- ai-test-agent：`6768441f75ced58132d32f0d0914638b403b4edd`，先完成 G1 设计及独立 B 审查；本任务的人工逐项确认由用户明确豁免，随后实现并验证。
- 来源行号均基于上述产品 head；源码路径若无前缀均相对于产品仓库。
- 用户要求：使用 ai-test-agent 做 LSP 测试，并向 nervosnetwork/fiber-py-integration-test 提交 open、非 draft PR；测试目标为上述分支。仅隔离 devnet，不访问生产/testnet 资金。

## 变更说明

原集成仓库已有 CKB/Fiber pytest 生命周期、RPC 和通道/付款工具，但没有 LSP 目录或评审；GitHub LSP PR 搜索未发现结果。拟添加真正进程级 pytest 集成测试、最小 SDK 测试适配器、固定版本构建/运行说明和 TEST-MAP。不会把已有 Rust mock/in-process 测试冒充 Python 外部集成运行结果。

当前分支已具備 RootSigner 凭证刷新、租户 open_tenant_channel 和 SDK Auto 策略。tenant allowlist 不包含 open_channel/cancel_invoice/parse_invoice；settle_invoice 合法。旧说明的接口或缺陷结论不直接沿用。

观察得到的行为标为 observation，候选安全/活性期望标为 inference，由本任务明确授权代理判断；不声称用户逐行批准。两个疑似缺陷先保留明确目标预期，不用 skip/xfail 或手工旁路把失败变成成功。

## Spec

### SPEC-01：RootSigner 注册、凭证更新与持久身份
Condition: 独立 devnet LSP，operator token 与两个钱包持有的 RootSigner
Expected: 仅最新一次有效签名 challenge 可注册/刷新；刷新不重建身份
Observable: 注册返回、租户列表/计数、通道绑定和 token 可用性
Basis: crates/fiber-types/src/hosted_lsp.rs:82-146; crates/fiber-lib/src/lsp/registry.rs:166-239; crates/fiber-lib/src/lsp/service.rs:349-395
Source: observation
Testing: required

### SPEC-02：Biscuit 授权及租户/公共节点隔离
Condition: operator、租户 A/B token、无 token 与损坏 token；A/B 各自拥有状态
Expected: 权限来自已认证的 authority tenant fact；普通方法解析到正确命名空间
Observable: HTTP/JSON-RPC 拒绝、双方 list/get 与状态快照
Basis: crates/fiber-lib/src/rpc/tenant.rs:16-92; crates/fiber-lib/src/rpc/biscuit.rs; crates/fiber-lib/src/lsp/tests/integration.rs:1421-1781
Source: observation
Testing: required

### SPEC-03：运行时生命周期和容量
Condition: 已注册租户，可配置 max_active_tenants，使用 operator 观测冷租户
Expected: ensure 幂等；显式空闲驱逐保留身份；容量及忙碌保护生效
Observable: lsp_get_status/lsp_list_tenants、通道 online、支付终态
Basis: crates/fiber-lib/src/lsp/runtime.rs:320-359; crates/fiber-lib/src/lsp/service.rs:396-438; crates/fiber-lib/src/lsp/tests/integration.rs:1196-1420
Source: observation
Testing: required

### SPEC-04：租户专用开通、外部资金与重试
Condition: 真实 CKB devnet、独立 SDK 签名端与租户 token
Expected: open_tenant_channel 限制 peer/private/external signer；开通过程耐重试且验证资金交易
Observable: opening result、双方 ChannelReady、链上 funding output 与唯一通道计数
Basis: crates/fiber-lib/src/rpc/channel.rs:345-376,1109-1125; crates/fiber-lib/src/fiber/network.rs:3679-3792; crates/fiber-lsp-sdk/src/opening.rs
Source: observation
Testing: required

### SPEC-05：发票约束和跨层原子性
Condition: 活跃租户有私有通道，显式 payment_hash、金额与有限 expiry
Expected: 有效发票路由提示/身份正确；不支持的 inbound MPP 在写入之前拒绝
Observable: tenant get_invoice、普通付款者 parse_invoice、operator 交付查询
Basis: crates/fiber-lib/src/rpc/invoice.rs:184-217,391-413; crates/fiber-lib/src/lsp/invoice.rs:181-222; crates/fiber-lib/src/lsp/tests/integration.rs:2056-2138
Source: observation
Testing: required

### SPEC-06：离线缓冲、期限与额度
Condition: 普通付款者→Public T→Hosted tenant，operator 开通内部 signer 私有通道作为路由基线；离线窗口仅用 operator API 观测
Expected: 离线付款可延后并准确恢复；截止与配额限制不导致双付或锁死
Observable: payer payment、delivery 状态/截止/错误、receiver invoice、两端 TLC/余额
Basis: crates/fiber-lib/src/lsp/delivery.rs; crates/fiber-lib/src/lsp/service.rs; crates/fiber-lib/src/lsp/tests/integration.rs:1784-2053,2144-2293
Source: observation
Testing: required

### SPEC-07：出站付款、路由与 MPP
Condition: 使用 operator Public T→tenant 开通的内部 signer 私有通道，租户经 Public T 向普通节点或另一 LSP 租户付款；外部 signer 出站另由 LSP-033 检验
Expected: 支持的出站路由成功；不支持的入站 MPP 不被误推断为出站限制
Observable: get_payment/list_payments、接收方发票、各通道 TLC/余额
Basis: crates/fiber-lib/src/lsp/tests/integration.rs:2296-2450; crates/fiber-lib/src/rpc/payment.rs
Source: observation
Testing: required

### SPEC-08：必须正面暴露的候选缺陷
Condition: 固定该分支，用真实 RPC 与独立签名端，禁止预像旁路及 xfail 掩盖
Expected: 以下是要求确认的目标不变量；若分支不满足应红灯并附证据，不修改产品代码
Observable: 完整分层终态、RPC 可见预像与失败后残留
Basis: https://github.com/nervosnetwork/fiber/issues/1695; crates/fiber-lib/src/fiber/payment.rs:636-643,1937-1948; crates/fiber-lsp-sdk/src/commitment.rs:280-299,504-523; crates/fiber-lib/src/rpc/invoice.rs:196-217,409; crates/fiber-lib/src/lsp/invoice.rs:193-195
Source: inference
Testing: required

### SPEC-09：外部通道签名协议与恢复
Condition: 真实 pending signing request；可暂停 SDK 自动签名，独立保存有效 submission
Expected: 拒绝错误提交不丢请求；即时精确重试幂等；完整 SDK store 与节点持久状态可恢复
Observable: get_channel_signing_status、Applied/AlreadyApplied、独立 signer snapshot、最终付款/通道状态
Basis: crates/fiber-lib/src/lsp/tests/signer_isolation.rs:418-451,606-807; crates/fiber-types/src/channel_signer.rs:493-559; crates/fiber-lib/src/fiber/tests/external_signer_restart.rs:998-1337,1562-1796
Source: observation
Testing: required

### SPEC-10：SDK 的独立意图、验证与预像释放
Condition: 使用生产 SDK 的测试适配器；篡改仅在本地响应代理，服务端仍为真实进程
Expected: 强制校验先于策略确认；未授权不得签名或向服务端释放预像
Observable: adapter 调用记录、实际 RPC 流量与完整 signer snapshot
Basis: crates/fiber-lsp-sdk/src/policy.rs:123-135; crates/fiber-lsp-sdk/src/commitment_tests.rs:816-903,1177-1203,1332-1401; tests/fiber-lsp-sdk-agent/src/agent.rs:280-315,527-568,1412-1505
Source: observation
Testing: required

### SPEC-11：协作关闭与托管 Watchtower 链上安全
Condition: 真实 devnet 确认交易，独立 SDK chain verifier，明确钱包收款地址和费用上限
Expected: 只对验证过的链上上下文签名；关闭/惩罚路径正确支付且可重试恢复
Observable: 确认的 commitment/spend 交易、live cell、SDK key purpose/accepted request、输出与费用
Basis: crates/fiber-lib/src/rpc/watchtower.rs:200-255,379-470; crates/fiber-lib/src/watchtower/signer.rs:128-145; tests/bruno/e2e/lsp-remote-signer*/; tests/fiber-lsp-sdk-agent/src/agent.rs:629-785; crates/fiber-lsp-sdk/src/commitment.rs:849-942
Source: observation
Testing: required

## 测试树

<!-- TEST-TREE-BEGIN -->
- Hosted LSP 对外生命周期
  - RootSigner 注册、凭证更新与持久身份
    - LSP-001 [primary] -> SPEC-01：有效 RootSigner 获取 challenge 并签名注册，然后以新 token 调用租户 RPC
    - LSP-002 [primary] -> SPEC-01：同一 RootSigner 先后获取两个 nonce，依次提交旧 nonce 和最新 nonce 的有效证明
    - LSP-003 [primary] -> SPEC-01：分别提交错误 RootSigner、其他 LSP 域签名、损坏签名及错误 nonce，随后提交正确证明
    - LSP-004 [primary] -> SPEC-01：已存在有通道/发票的租户使用新的有效 RootSigner 证明刷新凭证
    - LSP-005 [primary] -> SPEC-01：获取 nonce 后重启 LSP 再注册；注册后重启再重放已消费证明
  - Biscuit 授权及租户/公共节点隔离
    - LSP-006 [primary] -> SPEC-02：无 token、损坏 token 调用受保护 RPC；租户 token 调用 lsp_*、node_info、open_channel、cancel_invoice、parse_invoice
    - LSP-007 [primary] -> SPEC-02：A/B 各自创建发票、通道和付款；A 用 B 的 hash/channel_id 查询及修改
    - LSP-008 [primary] -> SPEC-02：公共节点和租户分别产生状态，以 operator 和 A/B token 调用同名标准 RPC
  - 运行时生命周期和容量
    - LSP-009 [primary] -> SPEC-03：并发/重复 ensure 同一租户，再重复驱逐空闲租户并重新 ensure
    - LSP-010 [primary] -> SPEC-03：将 max_active_tenants 设为 1，激活 A 后尝试 B，驱逐空闲 A 后再激活 B
    - LSP-011 [primary] -> SPEC-03：租户分别有待完成 delivery 或活动 hold TLC 时请求驱逐；不将 unsigned funding 已返回后的待签 opening 当作 busy
    - LSP-012 [primary] -> SPEC-03：cold 租户先仅由 operator 查询，再调用 tenant list_channels/get_invoice
  - 租户专用开通、外部资金与重试
    - LSP-013 [primary] -> SPEC-04：钱包通过 SDK 记录开通意图并 open_tenant_channel，签署和提交资金交易
    - LSP-014 [primary] -> SPEC-04：参数化使用错误 peer、public 为 true/缺失、缺少 external_channel_signer，operator 调用租户开通接口
    - LSP-015 [primary] -> SPEC-04：首个开通仍未提交资金时，同步重发相同请求并模拟调用方断开后重试
    - LSP-016 [primary] -> SPEC-04：已有未完成开通时改变资金金额/脚本等条款重试；ChannelReady 后再开第二条
    - LSP-017 [primary] -> SPEC-04：unsigned funding 返回后重启 LSP，使用完全相同请求恢复并完成签名提交
    - LSP-018 [primary] -> SPEC-04：提交篡改 funding outputs/amount/lock/input/cell_deps 的已签交易，随后提交合法交易
  - 发票约束和跨层原子性
    - LSP-019 [primary] -> SPEC-05：租户 new_invoice 创建普通及 hold 发票，用普通付款者解析，再通过 tenant/operator 查询
    - LSP-020 [primary] -> SPEC-05：tenant new_invoice 开启 allow_mpp 后检查 hash，再以相同 hash 创建合法单路径发票
  - 离线缓冲、期限与额度
    - LSP-021 [primary] -> SPEC-06：在线租户接收一笔付款，分别观察 payer、delivery、receiver 和两侧通道
    - LSP-022 [primary] -> SPEC-06：先创建发票后驱逐租户，付款并确认 deferred/cold，再显式激活
    - LSP-023 [primary] -> SPEC-06：离线延后期间重启 LSP，恢复后在原截止前激活租户
    - LSP-024 [primary] -> SPEC-06：保持租户离线直到较短 buffer deadline 过去，再激活
    - LSP-025 [primary] -> SPEC-06：分别设置 invoice expiry 与 TLC safety 为最早截止；先证明实际 Deferred，再从真实 upstream TLC expiry 和已配置转发 delta 推导 min 截止
    - LSP-026 [primary] -> SPEC-06：交付进入 in_flight 后维持尚未 fulfill 的 hold invoice，跨过原 buffer deadline 再合法 settle
    - LSP-027 [primary] -> SPEC-06：两个普通付款者并发支付同一 hosted invoice，保持第一笔 hold 尚未完成
    - LSP-028 [primary] -> SPEC-06：配置较低全局/每租户 pending limit；分别占满 A 配额及全局额度后加入 A/B 请求，再完成旧请求
  - 出站付款、路由与 MPP
    - LSP-029 [primary] -> SPEC-07：hosted tenant 经 Public T 向普通节点单路径付款，并用 operator scoped payment 查询对照
    - LSP-030 [primary] -> SPEC-07：公共节点到普通接收者有两条各自容量不足、合计足够的通道，hosted tenant 发送支持 MPP 的付款
    - LSP-031 [primary] -> SPEC-07：tenant A 经两个真实 LSP 向 tenant B 付款，B 使用单路径 hosted 发票
    - LSP-032 [primary] -> SPEC-07：租户余额/路径容量不足时付款，随后补足正常条件并发送新 hash 的付款
  - 必须正面暴露的候选缺陷
    - LSP-033 [primary] -> SPEC-08：外部签名 hosted payer 支付 hosted receiver，双方仅使用自己 RPC 可得信息驱动 SDK；不把接收钱包预像人工传给付款钱包
    - LSP-034 [primary] -> SPEC-08：new_invoice 提交有 payment_hash 但缺少有限 expiry 的 hosted 发票，检查原 hash 后修正 expiry 重试
  - 外部通道签名协议与恢复
    - LSP-035 [primary] -> SPEC-09：真实待签请求依次提交错 request_id、无效签名、缺失/不完整/冲突 next_material 和跨用途相同 nonce，最后提交正确 payload
    - LSP-036 [primary] -> SPEC-09：有效签名 Applied 后立即原样重试，再修改 signature/material 重试同一 ID
    - LSP-037 [primary] -> SPEC-09：在 unsigned funding 已返回、无活动 TLC/付款的 opening 待签检查点暂停并 evict；tenant 查询 signing status 后提交合法签名
    - LSP-038 [primary] -> SPEC-09：接收入站 hold invoice，在观察到承诺/撤销请求后分别重启 LSP 和完整 SDK signer store，再由收款钱包合法释放自有预像并恢复
  - SDK 的独立意图、验证与预像释放
    - LSP-039 [primary] -> SPEC-10：相同已验证非开通入站 commitment 用 Manual/Auto 处理，并分别测试开通和出站变更
    - LSP-040 [primary] -> SPEC-10：代理篡改余额/TLC key 或方向、settlement、nonce、peer partial，另测试缺少本地授权与过期 prepared approval
    - LSP-041 [primary] -> SPEC-10：hold invoice 尚不可链上恢复时请求释放预像，再提交错误预像，最后在恢复证据齐全时正确释放
  - 协作关闭与托管 Watchtower 链上安全
    - LSP-042 [primary] -> SPEC-11：完成开通后重启 signer，以钱包明确脚本/费用授权协作关闭并发起 shutdown_channel
    - LSP-043 [primary] -> SPEC-11：显式授权 Watchtower 后暂停 signer，由 Public T force-close，确认 commitment 并等待成熟后恢复 signer
    - LSP-044 [primary] -> SPEC-11：在真实 watchtower pending request 上提交未知 ID、错误长度和错误 key，再合法提交及精确/改写重试
    - LSP-045 [primary] -> SPEC-11：另一租户查询/提交 owner 的实际 watchtower 请求，并尝试 local_settlement_key 注册
    - LSP-046 [primary] -> SPEC-11：空闲租户 evict 成 cold 后 Public T force-close，保持 host/watchtower 与 SDK 可用
    - LSP-047 [primary] -> SPEC-11：已有 SDK 验证可恢复 hold TLC，确认 funding cell 仍 live 后安全释放预像到 Watchtower，再 force-close 并确认 commitment
    - LSP-048 [primary] -> SPEC-11：付款推进后 SDK 持有旧 peer commitment 的完整撤销证据，由 operator/dev RPC 广播旧版本
  - 新增独立安全边界
    - LSP-049 [primary] -> SPEC-02：Biscuit attenuation 不改变 authority 身份
    - LSP-050 [primary] -> SPEC-04：SDK 拒绝恶意 opening response
    - LSP-051 [primary] -> SPEC-11：SDK 拒绝恶意或失效链上证据
  - [pending] 精确崩溃点/毫秒级截止竞态 — 黑盒无法保证在持久提交与 continuation 之间击杀；只承诺可观测 pending 状态进程重启，内部钩子测试另行评估。
  - [pending] UDT 外部 signer 链上完整结算 — 当前 checked agent 链适配器仅支持 plain CKB；本轮外部签名资金路径不宣称 UDT 覆盖。
  - [unanalysed] 全分支其余 524-file diff、CCH/Lightning、WASM、迁移兼容和生产规模压测 — 超出本份 Hosted LSP devnet 行为设计。
  - [not_applicable] hosted public announcement 成功 — 租户通道强制私有。
<!-- TEST-TREE-END -->

## 本批完整用例

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `LSP-001` | - [x] 有效 RootSigner 获取 challenge 并签名注册，然后以新 token 调用租户 RPC | 生成由 RootSigner 派生的 tenant_id；首次注册为 cold；租户 RPC 可激活且仅访问自己的命名空间 | 合法注册失败或 token 无法使用 | P0 |
| `LSP-002` | - [x] 同一 RootSigner 先后获取两个 nonce，依次提交旧 nonce 和最新 nonce 的有效证明 | 旧证明失败且租户计数不变；最新证明仅注册一次；再次重放失败 | challenge 替换/消费不生效 | P0 |
| `LSP-003` | - [x] 分别提交错误 RootSigner、其他 LSP 域签名、损坏签名及错误 nonce，随后提交正确证明 | 非法请求失败，不新增租户、不消费有效 challenge；正确证明仍可成功 | 冒名注册或失败请求烧毁 nonce | P0 |
| `LSP-004` | - [x] 已存在有通道/发票的租户使用新的有效 RootSigner 证明刷新凭证 | 返回可用新 token；tenant_id、invoice_pubkey、created_at、通道绑定及发票保持；注册计数不增加 | 刷新导致身份和资金状态被替换 | P0 |
| `LSP-005` | - [x] 获取 nonce 后重启 LSP 再注册；注册后重启再重放已消费证明 | 未消费 challenge 在重启后可用；已消费证明仍失败，身份与计数保持 | 重启导致一次性证明复活或注册丢失 | P1 |
| `LSP-006` | - [x] 无 token、损坏 token 调用受保护 RPC；租户 token 调用 lsp_*、node_info、open_channel、cancel_invoice、parse_invoice | 全部拒绝；注册数、通道及发票状态不变；合法 tenant 方法仍可用 | 管理接口泄漏或错误使用不存在的租户 API | P0 |
| `LSP-007` | - [x] A/B 各自创建发票、通道和付款；A 用 B 的 hash/channel_id 查询及修改 | 跨租户 get/update/shutdown/settle/signature 操作拒绝或只返回本租户结果；B 状态不变 | 横向读写其他租户资金状态 | P0 |
| `LSP-008` | - [x] 公共节点和租户分别产生状态，以 operator 和 A/B token 调用同名标准 RPC | operator 观察公共节点，A/B 只观察各自通道/发票/付款；列表无串租户条目 | 共享 RPC 路由错到公共节点 | P0 |
| `LSP-009` | - [x] 并发/重复 ensure 同一租户，再重复驱逐空闲租户并重新 ensure | active_tenants 只增加一次；驱逐为 cold；重新激活同一身份和通道，无重复余额 | 重复运行时或驱逐破坏持久数据 | P0 |
| `LSP-010` | - [x] 将 max_active_tenants 设为 1，激活 A 后尝试 B，驱逐空闲 A 后再激活 B | 容量满时 B 激活失败，不隐式驱逐 A；释放槽位后 B 成功 | 容量绕过或隐式驱逐活跃租户 | P1 |
| `LSP-011` | - [x] 租户分别有待完成 delivery 或活动 hold TLC 时请求驱逐；不将 unsigned funding 已返回后的待签 opening 当作 busy | 拒绝驱逐且工作继续；完成后才可驱逐；分别设置各类忙碌状态 | 驱逐中断未完成资金操作 | P0 |
| `LSP-012` | - [x] cold 租户先仅由 operator 查询，再调用 tenant list_channels/get_invoice | operator 查询不激活；tenant RPC 按当前实现激活；观测计数明确区分 | 离线测试被自身查询意外唤醒而假通过 | P1 |
| `LSP-013` | - [x] 钱包通过 SDK 记录开通意图并 open_tenant_channel，签署和提交资金交易 | SDK 验证返回条款/链上输入；双方进入 ChannelReady，绑定唯一 private_channel_id，资金输出匹配约定 | 端到端开通不可用或资金流向错误 | P0 |
| `LSP-014` | - [x] 参数化使用错误 peer、public 为 true/缺失、缺少 external_channel_signer，operator 调用租户开通接口 | 均失败，不产生通道/链上资金交易；同一钱包正确请求随后可成功 | 绕过私有/签名边界或错误角色开通 | P0 |
| `LSP-015` | - [x] 首个开通仍未提交资金时，同步重发相同请求并模拟调用方断开后重试 | 返回同一 channel_id 与冻结的 unsigned funding transaction；只有一个开通和资金输出 | 重试创建双通道或双花资金 | P0 |
| `LSP-016` | - [x] 已有未完成开通时改变资金金额/脚本等条款重试；ChannelReady 后再开第二条 | 拒绝不同开通及第二通道；原条款和资金状态不变 | 重试替换已批准条款或绕过单通道限制 | P0 |
| `LSP-017` | - [x] unsigned funding 返回后重启 LSP，使用完全相同请求恢复并完成签名提交 | 恢复同一通道及资金交易，不生成新意图；可进入 ChannelReady | 开通中断丢失或重复筹资 | P1 |
| `LSP-018` | - [x] 提交篡改 funding outputs/amount/lock/input/cell_deps 的已签交易，随后提交合法交易 | 坏交易拒绝且没有错误链上输出；合法原交易仍可完成开通 | 未验证外部资金交易或拒绝后无法恢复 | P0 |
| `LSP-019` | - [x] 租户 new_invoice 创建普通及 hold 发票，用普通付款者解析，再通过 tenant/operator 查询 | 发票属于 tenant invoice_pubkey、提示 public LSP；金额/expiry/hash 保持；operator/tenant 查询一致 | 发票身份和 trampoline hint 错误 | P0 |
| `LSP-020` | - [x] tenant new_invoice 开启 allow_mpp 后检查 hash，再以相同 hash 创建合法单路径发票 | MPP 请求拒绝且无发票残留；合法重试成功 | 把 inbound MPP 错当支持或失败后残留 | P1 |
| `LSP-021` | - [x] 在线租户接收一笔付款，分别观察 payer、delivery、receiver 和两侧通道 | payer Success、delivery succeeded、invoice Paid；金额/费用与余额变化对应一次，无残留 TLC | 单侧成功或跨层状态不一致 | P0 |
| `LSP-022` | - [x] 先创建发票后驱逐租户，付款并确认 deferred/cold，再显式激活 | 激活前付款未成功且状态 durable deferred；激活后 payer Success、delivery succeeded、invoice Paid，仅结算一次 | 离线付款丢失或查询误唤醒造成假通过 | P0 |
| `LSP-023` | - [x] 离线延后期间重启 LSP，恢复后在原截止前激活租户 | delivery 身份/原截止保持，不重新延长缓冲；最终双方成功且仅结算一次 | 重启遗失交付或重置截止时间 | P0 |
| `LSP-024` | - [x] 保持租户离线直到较短 buffer deadline 过去，再激活 | delivery failed，payer 终态失败、无 receiver Paid、无待处理 TLC；激活不复活已失败交付 | 过期款无限挂起或迟到成功 | P0 |
| `LSP-025` | - [x] 分别设置 invoice expiry 与 TLC safety 为最早截止；先证明实际 Deferred，再从真实 upstream TLC expiry 和已配置转发 delta 推导 min 截止 | buffer_deadline 等于 min(created_at+cap,invoice截止,upstream_expiry−incoming_delta−downstream_delta−30000ms)；证明目标项严格最小；短 invoice 变体实等失败，长 safety 变体验证截止后立即安全解除，不伪称等待数小时 | 仅按 buffer cap 等待导致资金风险 | P1 |
| `LSP-026` | - [x] 交付进入 in_flight 后维持尚未 fulfill 的 hold invoice，跨过原 buffer deadline 再合法 settle | 不因离线缓冲期限已过而错误终结已派发支付；合法 settle 后完整成功 | 将缓冲截止误用于已启动的付款 | P1 |
| `LSP-027` | - [x] 两个普通付款者并发支付同一 hosted invoice，保持第一笔 hold 尚未完成 | 第二笔失败且不抢占/结算第一笔；释放合法预像后第一笔独立成功且收款仅一次 | 重复付款覆盖 delivery 或双重收款 | P0 |
| `LSP-028` | - [x] 配置较低全局/每租户 pending limit；分别占满 A 配额及全局额度后加入 A/B 请求，再完成旧请求 | 超额请求失败且不污染已有交付；未触及全局时 B 不受 A 配额影响；终态释放额度后可再付款 | 无界排队或跨租户饥饿/额度泄漏 | P1 |
| `LSP-029` | - [x] hosted tenant 经 Public T 向普通节点单路径付款，并用 operator scoped payment 查询对照 | payer Success、receiver Paid；payment hash/preimage 与金额匹配；仅 A 可查询该付款 | 出站路由/命名空间错配 | P0 |
| `LSP-030` | - [x] 公共节点到普通接收者有两条各自容量不足、合计足够的通道，hosted tenant 发送支持 MPP 的付款 | 观察多个实际 attempt/通道分片共同成功、接收一次总额；不是仅设置 allow_mpp 而走单路径 | 伪 MPP 覆盖或分片账务错误 | P0 |
| `LSP-031` | - [x] tenant A 经两个真实 LSP 向 tenant B 付款，B 使用单路径 hosted 发票 | 两服务各自租户/交付命名空间独立，最终 payer Success/receiver Paid，资金变化一致 | 单 LSP 假设破坏跨 LSP 付款 | P1 |
| `LSP-032` | - [x] 租户余额/路径容量不足时付款，随后补足正常条件并发送新 hash 的付款 | 首笔最终失败、无收款/残留 TLC、余额不错误扣除；后续合法付款成功 | 失败付款锁住额度或污染后续会话 | P1 |
| `LSP-033` | - [x] 外部签名 hosted payer 支付 hosted receiver，双方仅使用自己 RPC 可得信息驱动 SDK；不把接收钱包预像人工传给付款钱包 | 代理选择的目标不变量：应在有界时间内 payer Success、receiver Paid、RPC 返回匹配预像、无待签请求/TLC，余额一致；receiver Paid/payer Inflight 必须失败 | 掩盖 #1695 类预像循环依赖死锁 | P0 |
| `LSP-034` | - [x] new_invoice 提交有 payment_hash 但缺少有限 expiry 的 hosted 发票，检查原 hash 后修正 expiry 重试 | 代理选择的目标不变量：失败请求不留下 invoice/registry 残留，合法同 hash 重试可成功；当前源码可能先写 invoice 后拒绝，必须如实报告 | 拒绝请求留下不可重试的半成品发票 | P1 |
| `LSP-035` | - [x] 真实待签请求依次提交错 request_id、无效签名、缺失/不完整/冲突 next_material 和跨用途相同 nonce，最后提交正确 payload | 每次拒绝保持 pending request；正确提交 Applied；开通/付款最终可继续 | 错误签名污染状态或拒绝后卡死 | P0 |
| `LSP-036` | - [x] 有效签名 Applied 后立即原样重试，再修改 signature/material 重试同一 ID | 精确重试 AlreadyApplied，不重复状态推进；改写 receipt 拒绝；不要求任意历史签名永久可重放 | 网络重试导致重复应用或接受变造回执 | P0 |
| `LSP-037` | - [x] 在 unsigned funding 已返回、无活动 TLC/付款的 opening 待签检查点暂停并 evict；tenant 查询 signing status 后提交合法签名 | 冷查询保留完整请求且不激活；提交时恢复执行；最终通道/付款完成 | 读取签名状态误激活或冷状态无法续签 | P1 |
| `LSP-038` | - [x] 接收入站 hold invoice，在观察到承诺/撤销请求后分别重启 LSP 和完整 SDK signer store，再由收款钱包合法释放自有预像并恢复 | 每种重启保留对应请求与身份/绑定；双方付款终态和余额一致；不把 node 与 signer 重启混为一个证据 | 持久化边界丢请求、回滚 nonce 或重复结算 | P0 |
| `LSP-039` | - [x] 相同已验证非开通入站 commitment 用 Manual/Auto 处理，并分别测试开通和出站变更 | Manual 需确认；Auto 只在 version>1 且无出站变更时允许；开通/出站仍需确认；签名必须对应预先钱包意图 | 把测试代理自动确认误当 SDK 无条件授权 | P1 |
| `LSP-040` | - [x] 代理篡改余额/TLC key 或方向、settlement、nonce、peer partial，另测试缺少本地授权与过期 prepared approval | Manual/Auto 均拒绝强制验证失败；无提交、signer snapshot 不变；恢复原始合法材料后可继续 | 服务端自报数据绕过钱包校验 | P0 |
| `LSP-041` | - [x] hold invoice 尚不可链上恢复时请求释放预像，再提交错误预像，最后在恢复证据齐全时正确释放 | 前两次 SDK 拒绝且代理确认无 settle_invoice/create_preimage 传输；合法释放后完整 Success/Paid | 提前泄漏预像或仅凭未完成状态假证实安全 | P0 |
| `LSP-042` | - [x] 完成开通后重启 signer，以钱包明确脚本/费用授权协作关闭并发起 shutdown_channel | 关闭实际上链；输出地址、余额和费用匹配授权；Closed 状态，无未解决 TLC | 关闭错误支付或 signer 重启后失能 | P0 |
| `LSP-043` | - [x] 显式授权 Watchtower 后暂停 signer，由 Public T force-close，确认 commitment 并等待成熟后恢复 signer | 签名前 commitment output 仍 live 且有 Settlement 待签请求；恢复后有 SDK 接受签名记录及确认 spend，正确 payout | 仅看输出消失却未验证外部签名/目的地 | P0 |
| `LSP-044` | - [x] 在真实 watchtower pending request 上提交未知 ID、错误长度和错误 key，再合法提交及精确/改写重试 | 错误不改 pending/store；合法 Applied、精确 AlreadyApplied、改写拒绝；多轮扫描/重启后仍能链上完成 | watchtower 签名错误状态持久化或非幂等 | P0 |
| `LSP-045` | - [x] 另一租户查询/提交 owner 的实际 watchtower 请求，并尝试 local_settlement_key 注册 | 跨租户请求拒绝，owner 请求保持；tenant secret-key 注册拒绝，public-key-only 路径可用 | watchtower 命名空间泄漏或服务端代持私钥 | P0 |
| `LSP-046` | - [x] 空闲租户 evict 成 cold 后 Public T force-close，保持 host/watchtower 与 SDK 可用 | tenant 冷状态及 channel binding 可观测；watchtower 仍生成/应用签名并正确结算 | 租户不驻留时丢失链上保护 | P0 |
| `LSP-047` | - [x] 已有 SDK 验证可恢复 hold TLC，确认 funding cell 仍 live 后安全释放预像到 Watchtower，再 force-close 并确认 commitment | 必须出现 key_purpose=Tlc 的 SDK 提交；确认的 commitment/TLC spend 与合法收款匹配 | 只覆盖 Settlement key 而遗漏 TLC 资金 | P0 |
| `LSP-048` | - [x] 付款推进后 SDK 持有旧 peer commitment 的完整撤销证据，由 operator/dev RPC 广播旧版本 | 先确认旧 commitment，再验证 revocation spend 消耗输出且支付正确惩罚地址；缺少证据不得盲签 | 旧状态惩罚证据不全或处罚漏执行 | P0 |
| `LSP-049` | - [x] 对真实租户 token 追加有效 attenuation 的 tenant(B)/node(B或local) facts；另对 operator token 追加 tenant fact | 请求仍依 authority 块路由；A 可以正常读 A 但不能读改 B/host watchtower；operator 仍访问公共节点 | 信任可追加事实造成身份提升 | P0 |
| `LSP-050` | - [x] 代理篡改 genuine opening response 的钱包约定金额/费用/延迟，以及 channel_id/资金输出，然后恢复真实响应重试 | SDK 拒绝且无 funding 签名提交/verified binding；保留 opening intent，真实同意图重试可完成同一通道 | 仅检查服务端拒绝坏交易却遗漏恶意开通提案 | P0 |
| `LSP-051` | - [x] 对真实 Watchtower 请求篡改 payout/fee 或额外 sponsor input；另在 prepare 后用合法链交易花费输入再 confirm | SDK 拒绝恶意/过时证据且无签名提交；拒绝不改 signer snapshot；恢复合法材料的独立变体可结算，已花费输入不要求复活 | 提现目的地劫持或签名前不重新检查链状态 | P0 |
<!-- TEST-CASES-END -->

## 实现和执行方案（确认后进行）

1. 新增 test_cases/fiber/devnet/lsp/，沿用 pytest 与现有 RPC/节点工具；仅在缺少 LSP 配置/认证 header 的位置做最小扩展。独立临时目录及端口，局部进程句柄清理，不使用全局 pkill，不改其他项目或用户电脑。
2. 运行真实 CKB、Public T/FNN、普通付款者/接收者和独立 SDK signer 进程；状态不由 mock 替代。既有 FiberTest 依赖旧 data.1117 快照和硬编码合约，不能未经核对直接宣称兼容。LSP 专用 fixture 使用目标分支固定 dev-chain/contract 输入，验证合约 hash 和创世依赖后生成配置；不盲执行会清理目录或无限重启的 upstream start.sh。
3. 签名 happy-path 可复用固定版本 fiber-lsp-sdk-agent。它使用 Manual 并显式确认通过强制校验的请求；没有 outbound authorization 控制接口。新增最小测试适配器调用生产 SDK 来提供暂停/准备/提交、出站授权、Auto/Manual 和安全的局部响应篡改能力；适配器只在本测试库，不修改 Fiber 产品代码。不得从服务端待签内容反推出钱包授权，不得复制接收者预像给付款者绕开 #1695。
4. 使用真实 nonce/签名/链上交易；fixture 钱包仅 devnet。冷租户窗口只允许 lsp_list_tenants/lsp_get_payment_delivery 与 payer RPC，lsp_get_invoice/lsp_get_payment 也会激活租户，不能用于冷状态断言。get_channel_signing_status 是已验证的冷读例外。
5. 先静态设计/映射检查与 pytest collection，再按注册→开通→支付→签名→链上关闭逐类实跑，最后跑本 LSP 套件；每类有有界等待、诊断快照及 teardown。记录命令、版本、退出码、Junit 和脱敏日志；收集成功不是执行通过。
6. Makefile 显式目录列表需增加 lsp 或提供独立 lsp_test 入口；确认现有 CI 路径并提供固定 product SHA 的 LSP job。只发布测试 PR，不触发合并或部署。通过 chainTe/fiber-py-integration-test fork 发布，upstream 无直接写权限。
7. 在 PR 中分别列已实现映射、B 语义审查、真实执行结果和未覆盖/失败项。若上述候选缺陷导致红灯，保留真实失败及证据，不以 mock/skip/xfail 冒充通过，也不改产品来迎合测试。

## 依赖与具体执行权限

目前环境有 Python/uv、node、gcc/make/pkg-config；没有 cargo/rustc、CKB/ckb-cli、Docker、clang/cmake。无需 Docker。尚未下载执行运行时，也未运行外部仓库脚本。

建议本次一并确认以下限定操作，仅作用于 dot 云端独立工作目录：

- 从 Rust 官方来源安装 Rust 1.95.0 minimal toolchain（实际 rust-toolchain.toml 为 1.95.0，AGENTS 的 1.93.0 已过时），把 CARGO_HOME/RUSTUP_HOME 放在工作目录。Cargo 只使用固定仓库和 Cargo.lock。
- 从 Nervos 官方 CKB v0.202.0 release 获取 Linux portable ckb/ckb-cli，记录下载来源和 SHA256，再运行本地 devnet。预定 URL：https://github.com/nervosnetwork/ckb/releases/download/v0.202.0/ckb_v0.202.0_x86_64-unknown-linux-gnu-portable.tar.gz 。本地计算 SHA256 只是追踪，若发行方无独立签名/校验文件，不宣称供应链认证。
- 通过系统官方包仓库准备 clang/libclang-dev/cmake/libssl-dev 等 Cargo 实际需要的构建依赖（只在确实缺少时）；Python 在独立 venv 安装本仓库固定 requirements.txt 和 black，额外测试适配依赖固定并记录。
- 编译并运行用户指定的 chenyukang/fiber 固定提交：cargo build --locked -p fiber-bin -p fiber-lsp-sdk-agent；使用 checkout 内 tests/deploy/contracts 的固定 devnet 合约。构建和合约执行具有运行仓库代码的风险，来源是指定 fork；不执行 tools/install、任意下载脚本或 testnet/mainnet 流程。
- 运行经阅读的 ai-test-agent 固定提交中 scripts/check_test_design.py / check_test_map.py / pr_workflow.py，以及上述 pytest/SDK adapter。它们在本测试工作目录生成评审/报告和运行已确认的隔离测试；不会把脚本中的状态字段视为独立人类授权凭证。

具体验证命令（最终路径随 PR 中真实文件落实）：

- python3 ../ai-test-agent/scripts/check_test_design.py --root . --review reviews/lsp/hosted-lsp.md --json
- python3 ../ai-test-agent/scripts/check_test_map.py --root . --review reviews/lsp/hosted-lsp.md --require-complete --json
- .venv/bin/python -m pytest -o addopts= --collect-only test_cases/fiber/devnet/lsp
- .venv/bin/python -m pytest -o addopts= -vv -s --tb=short --junitxml=reports/hosted-lsp/junit.xml test_cases/fiber/devnet/lsp
- .venv/bin/python -m black --check 对应新增/修改 Python 文件

当前未运行这些命令；没有测试通过的结论。

## G1 程序门禁的任务专属处理

用户最新明确指示：“不用找我确认，全你自己负责，我只想看到最终测试结果”。据此，本任务不再请求逐行确认，代理负责采纳 B 修正、选择目标不变量并实现/测试。此记录是程序门禁免除，不是用户已看过或已批准 LSP-001 至 LSP-051 的声明，不扩展到其他任务或安全权限。

LSP-033/034 保留应成立的活性/原子性目标，若产品不满足按真实失败呈现，不弱化期望。plain CKB 范围与 pending/unanalysed 项保持公开。CLI pr_workflow 不支持 waiver 状态，因此不伪造 confirm；使用固定输入指纹、完整 B acknowledgements、逐项 A responses、结构/映射检查和真实执行报告维持可审查证据。
