# cc-switch 3.20.1「本地路由接管 Codex」源码研究报告

> 分析对象：`cc-switch-v3.20.1/cc-switch-3.20.1/`（本地完整源码树，版本 3.20.1，与用户机器版本一致）
> 源码根：`cc-switch-v3.20.1/cc-switch-3.20.1/src-tauri/src/`（后端 Rust）与 `src/`（前端 TS）
> 所有行号均引自上述本地源码树文件，可直接 grep/read 复核。

---

## 0. 结论速览（TL;DR）

1. **cc-switch 3.20.1 不检测 Codex CLI 版本**。写 `~/.codex/config.toml` 采用「扁平格式」还是「新版结构」，完全取决于**该 provider 数据库行 `settings_config.config` 里的 TOML 文本本身有没有顶层 `model_provider` 键**：
   - config 文本带顶层 `model_provider = "custom"`（+ `[model_providers.custom]` 表）→ 走新版结构，`base_url`/`wire_api`/`experimental_bearer_token` 全部写进 `[model_providers.custom]`；
   - config 文本**没有** `model_provider` 键 → 全部字段**兜底写成顶层字段**（顶层 `base_url`、`wire_api`、`experimental_bearer_token`）—— 这正是用户机器上看到的扁平格式，也是 401 的根源。
2. **路由代理（`codex.rs`）读上游 base_url 的优先级**：`settings_config.base_url` → `settings_config.baseURL` → `settings_config.config`（对象 `.base_url` 或 TOML 字符串内 `base_url = "..."` 文本查找）。不读 `website_url` 列、不读 `meta`、不读 `settings_config.env`。
   「缺少 base_url 配置」= `src-tauri/src/proxy/providers/codex.rs:907-909` 抛 `ProxyError::ConfigError`，判断条件：非官方/非 xAI OAuth 且上述链路全部取不到 base_url。
3. **API key 取自 `settings_config`**：`env.OPENAI_API_KEY` → `auth.OPENAI_API_KEY`（`auth` 对象，`extract_codex_auth_api_key`）→ `apiKey`/`api_key` → `config` 内 `api_key`/`experimental_bearer_token`。`meta.apiKeyField` **只决定请求头策略**（`ANTHROPIC_API_KEY` → `x-api-key`；否则 `Authorization: Bearer`），不决定 key 值来源。前端始终把 key 写入 `auth.OPENAI_API_KEY`。
4. `meta.apiFormat = "anthropic"` 的 Codex provider 走 `transform_codex_anthropic::responses_request_to_anthropic`（请求转换）+ `streaming_codex_anthropic`（SSE 回流）。这两个模块**不读 settings_config**，纯协议转换；真正需要的是 codex.rs 适配器能取到 base_url 和 key，以及可选的 `meta.max_output_tokens` / `meta.impersonate_claude_code`。
5. **justwoker(codex) 行的修复**：`settings_config` 里补 `base_url`（顶层字段或 config TOML 内均可，路由优先读顶层 `settings_config.base_url`），`auth` 里放 `OPENAI_API_KEY`（真实 key）。正确的新版 `config.toml` 见 §5。

---

## 1. 写 config.toml：扁平格式 vs 新版结构的条件（问题 1）

### 1.1 写入入口链路

接管（本地路由接管）激活时：

- `services/proxy.rs:742-758` `sync_codex_live_from_provider_while_proxy_active`：
  - `apply_codex_takeover_fields_for_provider(&mut effective_settings, &proxy_codex_base_url, provider)`（`:746`）
  - 最后 `write_codex_takeover_live_for_provider(&effective_settings, Some(provider))`（`:758`）
- `services/proxy.rs:3468-3487` `apply_codex_takeover_fields_for_provider`：
  - `apply_codex_takeover_auth_placeholder`（`:3473`，非官方 provider 把 `auth.OPENAI_API_KEY` 置为 `"PROXY_MANAGED"` 占位，见 `:3453-3466`）
  - `apply_codex_proxy_toml_config_for_provider(&config_text, proxy_base_url, Some(provider))`（`:3479`）→ 把代 base_url + `wire_api="responses"` + 上游模型写进 config 文本
  - `attach_codex_model_catalog_from_provider`（`:3485`）
- `services/proxy.rs:3426-3451` `apply_codex_proxy_toml_config_for_provider`：
  - 对非官方 provider 调用 `codex_config::update_codex_toml_field(toml_str, "base_url", proxy_url)`（`:3436`）与 `update_codex_toml_field(..., "wire_api", "responses")`（`:3439`），以及可选 `update_codex_toml_field(..., "model", upstream_model)`（`:3446`）

之后 token 注入：

- `services/proxy.rs:3663-3668`：`prepare_codex_provider_live_config(auth, &prepared_config)` → `write_codex_live_config_atomic`
- `codex_config.rs:3780-3805` `prepare_codex_provider_live_config`：取 token（`auth.OPENAI_API_KEY` 或 config 内 `experimental_bearer_token`）→ `set_codex_experimental_bearer_token(config_text, &token)`（`:3804`）

### 1.2 关键分流逻辑

`codex_config.rs:3109-3153` `set_codex_experimental_bearer_token`（token 写哪个层级）：

```rust
3122:    let Some(provider_id) = active_codex_model_provider_id(&doc) else {
3123:        doc["experimental_bearer_token"] = toml_edit::value(token);   // ← 无 model_provider → 顶层（扁平）
3124:        return Ok(doc.to_string());
3125:    };
3127:    if !is_custom_codex_model_provider_id(&provider_id) {
3130:        doc["experimental_bearer_token"] = toml_edit::value(token);   // ← 保留 id（openai/ollama/lmstudio）→ 顶层
3131:        return Ok(doc.to_string());
3132:    }
3138:    if let Some(provider_table) = doc.get_mut("model_providers")
3139:        .and_then(|item| item.as_table_like_mut())
3140:        .and_then(|table| table.get_mut(provider_id.as_str()))
3141:        .and_then(|item| item.as_table_like_mut()) {
3144:        if codex_provider_table_declares_auth(&*provider_table) { return Ok(config_text.to_string()); }
3147:        provider_table.insert("experimental_bearer_token", toml_edit::value(token));  // ← 自定义 model_provider → 表内（新版）
3148:        return Ok(doc.to_string());
3149:    }
3151:    doc["experimental_bearer_token"] = toml_edit::value(token);       // ← 表不存在 → 顶层兜底
```

`codex_config.rs:3870-3996` `update_codex_toml_field`（base_url / wire_api 写哪个层级，行号从 3930 段起）：

```rust
        // "base_url" | "wire_api" 分支：
        if active provider id 存在且 is_custom_codex_model_provider_id(id) {
            // 写入 [model_providers.<id>].<field>（表缺失时自动创建）     ← 新版结构
        }
        // 顶部注释（codex_config.rs:3891-3903）：
        //   内置 openai 的改址走它的正统机制——顶层 openai_base_url
        //   这两个保留 id（ollama/lmstudio）没有等价的顶层旋钮：建表=生成 Codex 拒绝
3978:            // Fallback: no model_provider or structure mismatch → top-level field
3982:                doc[field] = toml_edit::value(trimmed);                  // ← 无 model_provider → 顶层（扁平）
```

**结论**：扁平格式 ⇔ config TOML 文本顶层没有 `model_provider` 键（或它是保留 id）。用户机器 `config.toml` 出现 `base_url/wire_api/experimental_bearer_token` 三个顶层字段，正是其 provider 行 `settings_config.config` = `model = "claude-opus-5-thinking"...`（无 `model_provider`、无 base_url）直接导致的。

### 1.3 版本检测

**cc-switch 3.20.1 不检测 Codex CLI 版本来决定写入格式。**
- 仓库中 `--version` 相关代码全在诊断/工具枚举（`commands/misc.rs:2003 scan_cli_version`、`:2315` 带超时探测），与 config.toml 写入无关。
- 代码注释里的版本号（`0.148`、`0.149`、`0.144.5`）全部是**静态注释/常量**（如 `codex_config.rs:2705-2707` CODEX_STALE_RESERVED_TABLE_IDS 注释「present since 0.148」；`resources/codex_deepseek_catalog_template.json:50 minimal_client_version "0.144.0"`），不是运行时分支条件。
- 写入结构由 config 文本形状决定（见 1.2），检测失败/缺失时不存在「默认输出新版」——`set_codex_experimental_bearer_token` 对空文本直接报错（`codex_config.rs:3110-3115`），对无 `model_provider` 的文本一律写顶层。

### 1.4 新版结构的「标准模板」（前端如何生成）

`src/config/codexProviderPresets.ts:60-77` `generateThirdPartyConfig`（第三方 provider 预设统一生成）：

```ts
export function generateThirdPartyConfig(providerName, baseUrl, modelName = "gpt-5.6-sol") {
  return `model_provider = "custom"
model = ${tomlString(modelName)}
model_reasoning_effort = "high"
disable_response_storage = true

[model_providers.custom]
name = ${tomlString(providerName)}
base_url = ${tomlString(baseUrl)}
wire_api = "responses"
requires_openai_auth = true`;
}
```

注意末尾有 `requires_openai_auth = true`——说明 Codex 0.149+ 第三方配置需要该字段显式声明（否则回退 auth.json 登录态，见 `codex_config.rs:3690` 的错误文案）。

---

## 2. 路由代理读 base_url（问题 2）

### 2.1 读取优先级（`providers/codex.rs:852-910` `CodexAdapter::extract_base_url`）

```rust
852:    fn extract_base_url(&self, provider: &Provider) -> Result<String, ProxyError> {
853:        if is_codex_official_provider(provider) {
              // → CHATGPT_CODEX_BASE_URL（providers/mod.rs:47）
            }
            // xAI OAuth → XAI_API_BASE_URL（codex.rs:860 附近）
861:        // 1. 尝试直接获取 base_url 字段
863:            .get("base_url")                                // settings_config.base_url
869:        // 2. 尝试 baseURL 字段
            .get("baseURL")                                  // settings_config.baseURL
876:        // 3. 尝试从 config 对象/字符串中获取
882:            if let Some(url) = config.get("base_url")...    // settings_config.config 对象 .base_url
886:                if let Some(url) = crate::grok_config::extract_base_url(config_str) {  // TOML 字符串解析
892:                if let Some(start) = config_str.find("base_url = \"") { ... }          // 文本查找
898:                if let Some(start) = config_str.find("base_url = '") { ... }           // 文本查找
907:        Err(ProxyError::ConfigError(
908:            "Codex Provider 缺少 base_url 配置".to_string(),
909:        ))
```

注意第 3 步 `grok_config::extract_base_url`（`grok_config.rs:236-238` → `extract_model_config` `:176-208`）解析的是 **Grok Build 形态**（`models.default` + `[model."<id>"]`），对 Codex 形态 config 通常返回 None；真正对 Codex config 生效的是 `:892-903` 的**纯文本行查找**（不区分是否在 `[model_providers.custom]` 表内，只要有 `base_url = "..."` 行即可）。

### 2.2 「缺少 base_url 配置」错误抛出点

- 抛出：`providers/codex.rs:907-909`（`ProxyError::ConfigError("Codex Provider 缺少 base_url 配置")`）
- 触发条件：非官方、非 xAI OAuth，且 `settings_config` 无 `base_url`/`baseURL`/`config.base_url`、config TOML 文本中也没有可查找的 `base_url = ...` 行。
- 调用点：`proxy/forwarder.rs:1175` `let mut base_url = adapter.extract_base_url(provider)?;`（每次转发最先执行）
- 用户看到的错误文案包装：`proxy/handlers.rs:1958`：

```rust
"CC Switch local proxy failed while handling Codex endpoint {endpoint}. Provider: {provider_name}; model: {request_model}...; cause: {cause}"
```

- 用户 DB 行：`settings_config = {"auth":{},"config":"model = \"claude-opus-5-thinking\"...","modelCatalog":{...}}` —— 无 `base_url`/`baseURL`，`config` 文本也无 base_url 行 → 必然命中 907。

**明确**：`website_url` 列、`meta`、`settings_config.env.*`（包括 `ANTHROPIC_BASE_URL`）**全部不参与** Codex 路由 base_url 解析。（`ANTHROPIC_BASE_URL` 只在 Claude 应用形态的 `Provider::claude_base_url_contains` 等地方用，见 `provider.rs:105-111`。）

---

## 3. API key 取值（问题 3）

`providers/codex.rs:781-838` `CodexAdapter::extract_key`（优先级顺序）：

```rust
781:    fn extract_key(&self, provider: &Provider) -> Option<String> {
783:        // 1. env
784:        if let Some(env) = provider.settings_config.get("env") {
785:            if let Some(key) = env.get("OPENAI_API_KEY")... { return Some(...); }
795:        // 2. auth（Codex CLI auth.json 形态）
796:        if let Some(auth) = provider.settings_config.get("auth") {
797:            if let Some(key) = crate::codex_config::extract_codex_auth_api_key(auth) {...}
802:        // 3. 直接字段 apiKey / api_key
804:            .get("apiKey").or_else(|| provider.settings_config.get("api_key"))...
814:        // 4. config 内
815:        if let Some(config) = provider.settings_config.get("config") {
              // 对象 .api_key/.apiKey
826:            if let Some((_, key)) = crate::grok_config::extract_credentials(config_str) {...}
829:            if let Some(key) = crate::codex_config::extract_codex_experimental_bearer_token(config_str) {...}
```

- `extract_codex_auth_api_key` = `codex_config.rs:1088-1094`：只读 `auth.OPENAI_API_KEY`。
- `extract_codex_experimental_bearer_token`（`codex_config.rs:2523-2544`）：优先读 active `[model_providers.<id>].experimental_bearer_token`，无 active provider 时读顶层。
- **`meta.apiKeyField` 只影响请求头**（`codex.rs:912-944` `extract_auth`）：`api_format=anthropic` 且 `api_key_field=ANTHROPIC_API_KEY` → 发 `x-api-key` 头；否则 `Authorization: Bearer <key>`。key **值**永远来自 `extract_key`（即上述 settings_config 链路）。
- 前端写入位置：`components/providers/forms/hooks/useCodexConfigState.ts:236`：

```ts
const auth = JSON.parse(codexAuth || "{}");
auth.OPENAI_API_KEY = trimmed;      // ← API Key 输入统一写 auth.OPENAI_API_KEY
setCodexAuth(JSON.stringify(auth, null, 2));
```

**用户场景**：justwoker(codex) 行 `auth = {}`（空）且 config 无 token → `extract_key` 返回 None；即便 `meta.apiKeyField="ANTHROPIC_API_KEY"`，也只是让 header 长成 `x-api-key: <空>`——key 根本没有来源。base_url 报错会**先于** key 报错（`forwarder.rs:1175` 先 `extract_base_url`）。

---

## 4. apiFormat=anthropic 的转换路径（问题 4）

### 4.1 判定与路由

- `providers/codex.rs:168-196` `codex_provider_uses_anthropic`：优先读 `meta.api_format`（`:170-172`）→ 命中 `"anthropic"` → `is_anthropic_wire_api`（`:708-713`，接受 `anthropic/anthropic_messages/anthropic-messages/claude/messages`）→ true。也可从 `settings_config.api_format`/`apiFormat` 或 config TOML 的 `wire_api` 判定。
- `providers/codex.rs:198-207` `should_convert_codex_responses_to_anthropic`：endpoint ∈ `/responses`、`/v1/responses`、`/responses/compact`、`/v1/responses/compact` 且 `codex_provider_uses_anthropic` → true。
- `proxy/forwarder.rs:1198-1199` 计算 `codex_responses_to_anthropic`；`:1548-1603` 执行：

```rust
1548:        } else if codex_responses_to_anthropic {
1550:            super::providers::apply_codex_upstream_model(provider, &mut mapped_body);
1559:            if let Some(max_out) = provider.meta...max_output_tokens.filter(|v| *v > 0) {
1565:                mapped_body["max_output_tokens"] = Value::from(max_out);   // meta.maxOutputTokens 注入
1574:            let mut anthropic_body = super::providers::transform_codex_anthropic::responses_request_to_anthropic(
1575:                mapped_body, DEFAULT_CODEX_ANTHROPIC_MAX_TOKENS /* 8192 */);
1584-1590:        // 处理 [1m] 后缀剥除
1591:            if codex_impersonate_claude_code { prepend_claude_code_system_prompt(&mut anthropic_body); }
1599:            super::cache_injector::inject(&mut anthropic_body, ...);
```

- 响应流：`providers/streaming_codex_anthropic.rs:587` `responses_sse_events_from_anthropic_message`、`:676` `create_responses_sse_stream_from_anthropic`（forwarder `:2398` 分支调用）。
- 请求/响应路径改写：`forwarder.rs:3193-3197` `rewrite_responses_endpoint_to_messages`（`/responses` → `/v1/messages`）；base_url 已带 `/v1/messages` 时不再拼接（`forwarder.rs:1480-1482`、`base_url_is_full_endpoint` `:3010`）。

### 4.2 转换模块期望的 settings_config 字段

`transform_codex_anthropic.rs` 与 `streaming_codex_anthropic.rs` **完全不读取 settings_config**（grep 无任何 `settings_config`/`env_key`/`ANTHROPIC_API_KEY` 引用，文件头注释 `:1-11` 说明只是 Responses ⇄ Anthropic 协议转换）。真正驱动它工作的是适配器层（codex.rs）+ forwarder 需满足：

| 需要 | 来源 | 缺了会怎样 |
|---|---|---|
| 上游 base_url | settings_config.base_url / baseURL / config 内 base_url | `codex.rs:907`「缺少 base_url 配置」（用户当前错误） |
| API key | settings_config.auth.OPENAI_API_KEY（标准）等 | 无 Authorization 头 → 上游 401「Missing bearer」 |
| apiFormat=anthropic（已有） | meta.api_format | 走原生 Responses 透传，不转换 |
| apiKeyField（可选，已有 ANTHROPIC_API_KEY） | meta.api_key_field | 决定 x-api-key vs Bearer |
| maxOutputTokens（可选，已有 32768） | meta.max_output_tokens | 默认 8192 截断长输出 |
| impersonateClaudeCode（可选） | meta.impersonate_claude_code | 不加 Claude Code 伪装 |
| 默认模型 | config TOML `model` | 用 Codex 默认模型 |

即：`meta` 里已有的东西基本够用，**缺的就是 settings_config 里的 base_url 与 key**。

---

## 5. 结论：justwoker(codex) 行的预期形态（问题 5）

### 5.1 providers 表 justwoker(codex) 行 settings_config（预期 JSON）

保持现有 `modelCatalog` 与 `meta` 不动，只需补 `base_url`（路由优先读该顶层字段）并把 key 放入 `auth`：

```json
{
  "auth": {
    "OPENAI_API_KEY": "<真实 API key，与 claude 行 env.ANTHROPIC_API_KEY 同源的 JustWoker key>"
  },
  "config": "model_provider = \"custom\"\nmodel = \"claude-opus-5-thinking\"\n\n[model_providers.custom]\nname = \"JustWoker\"\nbase_url = \"https://api.justwoker.icu\"\nwire_api = \"responses\"\nrequires_openai_auth = true\n",
  "modelCatalog": { /* 原样保留现有内容 */ }
}
```

meta 保持现有（已满足 anthropic 路径）：

```json
{
  "apiFormat": "anthropic",
  "apiKeyField": "ANTHROPIC_API_KEY",
  "maxOutputTokens": 32768,
  "customUserAgent": "curl/8.5.0"
}
```

要点：
- **base_url 至少二选一**：顶层 `"base_url": "https://api.justwoker.icu"`（路由 864-867 命中，最简单可靠），或写进 config TOML 的 `[model_providers.custom]`（路由通过 `:892-903` 文本查找也能命中；但顶层字段优先级更高，且与 UI 表单的「Base URL 输入框」行为一致——`handleCodexBaseUrlChange` 会写回 TOML 对应层级）。
- **key 必须放 `auth.OPENAI_API_KEY`**（前端保存逻辑与后端 `extract_key` 第 2 步一致；也可用 `apiKey` 顶层字段或 config 内 `experimental_bearer_token`，但 UI 写入的就是 `auth.OPENAI_API_KEY`）。⚠️ 不要只放在 `settings_config.env.ANTHROPIC_API_KEY`（那是 Claude 行的形态，Codex 路由的 `extract_key` 第 1 步只认 `env.OPENAI_API_KEY`）。
- 不要依赖 `website_url`（路由不读）。

### 5.2 接管期间 `~/.codex/config.toml` 的正确内容（新版结构）

接管写入后（base_url 被替换为本地代理地址、auth 占位符被注入 token）：

```toml
model_provider = "custom"
model = "claude-opus-5-thinking"

[model_providers.custom]
name = "JustWoker"
base_url = "http://127.0.0.1:15721/v1"
wire_api = "responses"
requires_openai_auth = true
experimental_bearer_token = "PROXY_MANAGED"
```

- `base_url`/`wire_api` 由 `update_codex_toml_field` 写进 `[model_providers.custom]`（因为 config 文本有顶层 `model_provider = "custom"`）；`experimental_bearer_token` 由 `set_codex_experimental_bearer_token`（codex.rs 逻辑 3138-3148）注入表内——**Codex CLI 只认 provider-scoped token**（`codex_config.rs:2520-2522` 注释「Mobile compat：第三方 provider 把 API key 放在 `[model_providers.<id>].experimental_bearer_token`，顶层 token 在 0.149 无此字段会被忽略」，及 `:3681-3682`「Codex 不识别顶层 experimental_bearer_token」）。
- 路由模式下不会把真实 key 写进 config.toml：`apply_codex_takeover_auth_placeholder`（services/proxy.rs:3453-3466）把 auth 占位为 `PROXY_MANAGED`，`prepare_codex_provider_live_config`（codex_config.rs:3784-3785）优先取 auth 里的 key（此处为占位）→ 所以写入的是占位符；真实 key 留在 DB，由本地代理在转发时从 `extract_key` 取并注入 `x-api-key`/`Authorization` 头。
- 顶层模型/其他字段原样保留；若想与预设一致可保留 `model_reasoning_effort = "high"`、`disable_response_storage = true` 等自定义行。

### 5.3 手动修复最小动作（不改 UI）

1. 在 DB 改 justwoker(codex) 行：`settings_config.auth.OPENAI_API_KEY` = 真实 key；`settings_config` 加顶层 `"base_url": "https://api.justwoker.icu"`（并把 config TOML 改为含 `model_provider = "custom"` + `[model_providers.custom]` 的新版结构）。
2. 重新触发 Codex 接管（或手动把 live `~/.codex/config.toml` 写成 §5.2 新版结构）。
3. 代理 15721 转发时即能：`extract_base_url` 命中顶层字段 → `codex_provider_uses_anthropic`（meta.apiFormat）→ `/responses` 转 `/v1/messages` → `extract_key` 从 `auth.OPENAI_API_KEY` 取值 → 按 `apiKeyField` 发 `x-api-key: <key>` 到 `https://api.justwoker.icu/v1/messages`。

---

## 6. 附：关键文件行号索引

| 主题 | 文件:行 |
|---|---|
| PROXY_MANAGED 占位符 | `src-tauri/src/codex_config.rs:25`；`services/proxy.rs:25`；`proxy/forwarder.rs:40` |
| CC_SWITCH_CODEX_MODEL_PROVIDER_ID="custom" | `src-tauri/src/codex_config.rs:18` |
| config.toml 顶层字段写入兜底 | `codex_config.rs:3978-3983`（update_codex_toml_field）|
| token 写顶层 vs 表内 | `codex_config.rs:3109-3153`（set_codex_experimental_bearer_token）|
| 新版结构生成模板（前端） | `src/config/codexProviderPresets.ts:60-77` |
| 接管时字段改写 | `services/proxy.rs:3426-3451, 3468-3487` |
| 接管时 auth 占位 | `services/proxy.rs:3453-3466` |
| 路由 base_url 来源 | `providers/codex.rs:852-910` |
| 「缺少 base_url 配置」抛错 | `providers/codex.rs:907-909` → 包装 `proxy/handlers.rs:1958` |
| 路由 API key 来源 | `providers/codex.rs:781-838`；`codex_config.rs:1088-1094` |
| apiKeyField → 头策略 | `providers/codex.rs:912-944` |
| anthropic 判定 | `providers/codex.rs:168-207`；wire_api 判定 `:696-720, 732-749` |
| anthropic 转换入口 | `proxy/forwarder.rs:1548-1603`；`transform_codex_anthropic.rs`（纯协议）；`streaming_codex_anthropic.rs:587,676-682` |
| Provider 结构 | `provider.rs:11-44`；ProviderMeta `:434-555` |
| DB 读取 providers 行 | `database/dao/providers.rs:20-109` |
| 前端 key 写入 auth.OPENAI_API_KEY | `components/providers/forms/hooks/useCodexConfigState.ts:230-246` |
| 前端 base_url 编辑 | `src/utils/providerConfigUtils.ts:1252-1336`（setCodexBaseUrl：有 model_provider 写表内，否则写顶层）|