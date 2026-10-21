# OpenAI 协议与 auto-X 文章生成参数说明

记录日期：2026-10-21。本文结合 auto-X 功能提交 `006e4ca` 的实现，说明模型参数的含义、指令与素材的区别，以及 Skills 如何参与文章生成。

同日分类改造补充：管理页面现区分 GitHub 导入的“Skills”和手动维护的“提示词”，共用 `ai_skills` 表及有序关联。原自定义 Skills 迁移为提示词，既有内容、ID 和快照保持。两类内容仍按现有逻辑拼入 `instructions`；本文后续沿用旧版代码中的 Skill/`skill_snapshot` 名称时，也包括这类自定义提示词。这次分类改造没有新增单次写作要求的 `user_prompt` 参数。

本文主要讨论 **OpenAI Responses API**。文中的“OpenAI 协议”指接口格式及其参数约定；不同第三方兼容服务和模型对参数的支持程度可能不同。

## 1. 写作方法、写作要求与素材分别是什么

一次文章生成通常需要三部分信息：

| 信息 | 解决的问题 | 示例 |
| --- | --- | --- |
| 写作方法与通用规则 | 应该怎样写 | 采用小红书笔记风格，开头突出痛点，分段清晰，不编造事实 |
| 本次写作要求 | 这次具体要写什么 | 根据这条推文，写给普通上班族，约 800 字，重点说明实际用途 |
| 素材与上下文 | 依据什么来写 | 当前推文、作者画像、近期动态、用户提供的资料 |

Skill 主要提供可复用的写作方法与规则。用户每次与 ChatGPT 对话时输入的“请帮我……”，通常属于本次任务要求；其中也可能同时包含素材，需要在组装请求时明确区分。

auto-X 当前能够正常生成文章，模型调用所需参数已经具备。不过，产品目前没有独立的“本次／本监听任务写作要求”输入字段，具体差距见第 7 节。

## 2. Responses API 的主要参数

| 参数 | 含义 | 侧重点 | auto-X 当前用法 |
| --- | --- | --- | --- |
| `model` | 选择处理请求的模型 | 能力、速度、成本、支持的功能 | 使用当前 AI 数据源配置的模型 |
| `instructions` | 提供系统／开发者层的指令 | 角色、规则、方法、限制 | 合并基础规则、功能点目标、Skills、全局默认提示词、语言与语气等 |
| `input` | 本次请求的消息或其他输入项 | 具体任务、素材、上下文 | 当前发送一条 `role=user` 的文本消息 |
| `reasoning.effort` | 控制受支持模型的推理投入程度 | 分析投入、延迟与成本 | 从生成配置读取并发送 |
| `max_output_tokens` | 限制输出的 Token 预算 | 输出上限 | 使用全局设置或监听任务覆盖值 |
| `text.format` | 指定输出格式 | 结果结构及可解析性 | 使用严格 JSON Schema 输出文章草稿 |
| `store` | 控制是否存储响应以供后续 API 检索等用途 | 响应存储 | 当前固定发送 `false` |

模型能力会影响可用的推理强度、输出预算上限及结构化输出支持。应用界面允许填写某个值，不等于所有模型或兼容服务都接受该值。

### 2.1 `instructions`：告诉模型应遵守什么规则

适合放置以下内容：

- 写作者角色，例如科技作者、编辑。
- 通用质量要求，例如事实准确、保留不确定性、不编造引用。
- 写作方法，例如先提炼观点，再解释影响。
- Skills 的正文指令。
- 长期适用的风格、语言和语气要求。

`instructions` 不是某种程序代码，模型会将它作为指令文本理解。它也不负责下载安装 Skill、执行脚本或调用外部工具。

### 2.2 `input`：告诉模型本次要完成什么任务

Responses API 的 `input` 可以接受文本，也可以接受结构化输入项；auto-X 当前使用消息数组：

```json
{
  "input": [
    {
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "本次任务要求和明确标识的素材"
        }
      ]
    }
  ]
}
```

这里几层字段的含义不同：

| 字段 | 含义 |
| --- | --- |
| `input` | 本次请求的输入项集合 |
| `role: user` | 这是一条用户角色的消息 |
| `content` | 消息中的内容项集合 |
| `type: input_text` | 当前内容项是输入文本 |
| `text` | 实际交给模型阅读的文字 |

所以，聊天时输入的一段话可以放在 **`input[0].content[0].text`** 中。这个位置也能同时包含任务要求和素材，但应该用清晰的段落或结构将二者分开。

### 2.3 指令优先级不等于内容重要程度

`instructions` 所承载的系统／开发者层指令优先于用户层要求。正常情况下，两者配合完成任务：前者提供规则，后者确定本次目标。

这不能简化成“所有 `input` 都比 `instructions` 低”。`input` 本身也支持不同角色的消息，优先级需要根据实际角色判断；本文的项目对照指的是当前 `role=user` 的用法。

例如，通用规则要求“不编造数据”，本次用户要求“说明对上班族的影响”，二者可以同时满足。若用户要求“缺少的数据直接编造”，就与更高层规则冲突。

有两点容易混淆：

- 用户消息不是不重要。文章的主题、读者、重点通常依赖用户消息确定。
- 多个 Skill 与全局提示词都被拼入同一个 `instructions` 后，并不会自动获得不同的指令权限。拼接顺序也不保证某一段必然覆盖另一段；需要尽量消除规则冲突。

原帖里出现“忽略前面的要求”等文字时，它属于被引用的素材，不应变成本次生成任务的命令。auto-X 的基础规则和素材边界标记都表达了这一约束。

### 2.4 `reasoning.effort`：推理投入，不是文章长度

它用于受支持的推理模型，表达希望模型投入多少推理计算。更高的投入可能增加耗时和成本，但不保证每个任务的结果都更好，也不意味着正文一定更长。

可用取值依赖具体模型与服务端实现，应以对应模型文档为准。该参数也不会自动赋予模型联网、执行脚本或使用工具的能力。

### 2.5 `max_output_tokens`：预算上限，不是目标字数

Token 是模型处理文本的单位，不等同于中文字数或英文单词数。因此 `max_output_tokens=2500` 不表示“写 2500 个中文字”。对于推理模型，输出预算还可能包含推理 Token，留给可见正文的预算会更少。

若希望文章约 800 字，应在写作要求中说明目标字数，并设置足够的 Token 预算。过小的上限可能使输出不完整或无法满足完整 JSON 结构。

### 2.6 `text.format`：约束结果结构

auto-X 使用以下配置指定结构化输出：

```json
{
  "text": {
    "format": {
      "type": "json_schema",
      "name": "x_sentinel_draft",
      "strict": true,
      "schema": {}
    }
  }
}
```

上例的空 `schema` 仅用于标明参数位置；生产请求实际传入完整的 `DRAFT_OUTPUT_SCHEMA`，不是空对象。

当前草稿结构包括 `title`、`content`、`excerpt`、`metadata`、`author_profile` 等字段，服务端收到结果后还会进行结构校验。

JSON Schema 解决的是字段、类型和格式约束，不能保证事实准确、观点合理或文章达到预期风格。模型拒绝回答、输出预算不足、服务不兼容等情况，也需要由应用处理。

## 3. auto-X 当前实际发送的请求

当前文章生成使用：

```text
POST {base_url去掉末尾斜杠}/responses
Authorization: Bearer <AI数据源API Key>
```

例如，当 `base_url` 为 `https://api.openai.com/v1` 时，最终地址为 `https://api.openai.com/v1/responses`。

下方是便于阅读的结构示意，其中提示词和 Schema 已简化，不应直接当作完整生产报文复制：

```json
{
  "model": "<AI数据源配置的模型ID>",
  "instructions": "基础规则 + 功能点目标 + Skills正文 + 全局默认提示词 + 语言语气",
  "input": [
    {
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "固定创作要求 + 当前推文及作者上下文JSON"
        }
      ]
    }
  ],
  "text": {
    "format": {
      "type": "json_schema",
      "name": "x_sentinel_draft",
      "strict": true,
      "schema": {}
    }
  },
  "reasoning": { "effort": "medium" },
  "max_output_tokens": 2500,
  "store": false
}
```

这里的 `medium` 和 `2500` 是示例值，实际从配置读取。语言和语气目前以文本形式写入 `instructions`，不是单独发送名为 `language` 或 `tone` 的标准模型参数。

`API Key` 放在 HTTP 鉴权头中；请求超时通过 HTTP 客户端设置。它们不是交给模型理解的写作内容。失败重试次数同样属于 auto-X 的任务执行控制。

`store=false` 不会取消 auto-X 自己保存的任务快照、调用审计和生成草稿；这些是应用自身的数据。

## 4. Skills 在当前请求中如何生效

标准 Responses API 没有专门名为 `skills` 的参数。auto-X 中的 `skill_ids`、`skill_snapshot` 都是应用自己的业务字段。

当前处理链路为：

```text
页面明确选择 skill_ids
  → 通过 Nacos 发现 skill-worker，并通过 HTTP 解析、校验
  → 入队时冻结有序 Skill 内容和版本
  → ai-worker 领取时通过 skill-worker 校验并读取冻结材料
  → build_provider_material() 拼装提示词
  → Skills 正文成为 instructions 的一部分
  → 发给模型生成草稿
```

每个 Skill 会被组装成包含名称、版本、正文指令和输出偏好的文本段落。多个 Skills 按选择顺序排列，但这种排列不等于程序按步骤执行多个 Skill。

自定义 Skill 使用数据库中的指令；GitHub Skill 使用已经解析编译的指令与参考文本。模型不会因为看到 Skill 名称，就自动读取服务器磁盘上的整个目录。

当前 GitHub Skill 的完整资源保存在服务器上，但文章生成阶段只使用写作指令和参考文本；不执行其中的脚本、不安装依赖、不自动调用外部工具。请求也没有配置供模型使用的工具列表。

Skill 中的 `output_schema` 在当前拼装中以 `OUTPUT PREFERENCES` 文本传入。真正约束最终草稿结构的是 `text.format.schema` 中的应用统一 Schema。

## 5. 当前 `instructions` 的组成

`build_provider_material()` 按以下顺序组装同一个指令字符串：

1. `PROMPT_GUARD`：素材边界、事实要求、创作与作者画像更新规则。
2. 功能点名称和 `feature_snapshot.base_prompt`：当前生成能力的基础目标。
3. 有序的 `skill_snapshot`：每个 Skill 的名称、版本、正文和输出偏好。
4. 存在 GitHub Skill 时的执行范围说明：只生成文章 JSON，不执行脚本或外部工具。
5. `prompt_template`：界面“默认提示词”，放在 `ADMIN EDITORIAL PROMPT` 段。
6. `language` 与 `tone`：语言和语气。
7. 若监听任务自动发布到小红书，再追加对应标题与正文长度要求。

这些段落应当互相协调。例如，Skill 要求固定输出 Markdown、应用要求输出 JSON 时，需要以应用草稿结构为目标适配写作内容，不能假设两种格式能够同时原样输出。

“默认提示词”是全局创作配置，并不是每次生成弹框中的用户消息。它可以补充共同的编辑要求，但不能独立表达两个监听任务各自不同的写作目标。

## 6. 当前 `input` 的组成与会话边界

当前 `input` 的 `user` 消息由系统自动构造，包括：

- 固定任务话术：理解作者、关联近期动态、识别当前关注点、生成草稿并保守更新画像。
- `current_post`：推文 ID、作者 ID、语言、发布时间和正文等。
- `author_context`：作者身份、持久化画像、近期动态及相关信息。

这些素材被封装成 JSON，并使用 `BEGIN_UNTRUSTED_SOURCE`、`BEGIN_UNTRUSTED_AUTHOR_CONTEXT` 等边界标记，提示模型将其中内容当作引用材料处理。

因此，消息的 `role=user` 不表示其中每句话都由用户在界面亲自输入。当前这条消息实际由程序组装。

每次生成都会发送当前作业的指令与素材，当前请求没有传入 `previous_response_id`、会话 ID 或多轮聊天历史。作者画像与近期动态是应用显式提供的上下文，不等于模型自动继承了之前所有聊天内容。

ChatGPT 网页中的历史对话、工具和产品级上下文，也不会因为使用 GPT 模型就自动出现在 auto-X 的 API 请求中。

## 7. 当前缺少的“本次／任务级写作要求”

当前两个入口的情况如下：

| 入口 | 现有配置 | 当前缺口 |
| --- | --- | --- |
| `/tweets` 手动 AI 生成 | `feature_code`、`skill_ids`、`idempotency_key` | 没有每次自由填写的写作要求 |
| `/ai-writing` 监听任务 | Skills、监听范围、语言、语气、输出预算等 | 没有每个任务独立保存的写作要求 |
| `/ai-writing` 默认提示词 | 全局 `prompt_template` | 作用于共同配置，不能替代单次或任务专属要求 |

监听任务的名称用于标识与审计，目前不会自动作为提示词发送给模型。仅将任务命名为“写给程序员的文章”，不会自动让模型收到这个写作要求。

例如，下面这段话应当属于单独的写作要求：

> 请围绕这条消息分析对普通开发者的影响，写约 1000 字，先解释发生了什么，再分析机会和限制。避免逐句翻译，不要补造素材中不存在的数据。

### 建议的后续设计，尚未实现

可以增加业务字段 `user_prompt` 或 `writing_requirement`，页面名称使用“写作要求”：

- 手动生成：每次提交时填写。
- 监听任务：按任务单独保存，允许同一账号的不同任务使用不同要求。
- 入队：连同 Skill 内容及任务配置一起冻结，避免后续编辑改写已有队列的意图。
- 模型请求：放在 `input` 的用户指令部分，与推文、画像等引用素材分开。

推荐的职责分配是：

```text
instructions：通用规则 + Skills写作方法 + 允许本次需求变化的范围

input / user：
  本次写作要求：主题、读者、重点、目标字数等
  引用素材：当前推文、作者画像、近期动态等
```

不要把新增的写作要求直接混入当前 `source.text`，因为该字段明确属于被引用的素材。也应避免全局规则把风格和长度完全写死，导致本次合法要求与高层规则冲突。

以上是建议，不代表当前已经存在这些字段或界面功能。

## 8. 任务快照与运行时配置的区别

任务快照用于保留当时选择的 Skills、功能点目标、素材、作者上下文和生成配置。其中，全局 `prompt_template` 会在入队时写入请求快照；监听任务的语言、语气和输出预算覆盖值也会反映到作业配置中。

不过，不能将其理解为“所有调用参数永远冻结”。当前 ai-worker 执行时会读取当前 AI 数据源，并更新本次调用使用的协议、模型、服务地址、数据源名称与版本；API Key 同样来自执行时的数据源配置。

因此，应分别理解“作业保留的创作内容与意图”和“实际执行时使用哪个模型服务”。

## 9. Responses 与 Chat Completions 的格式区别

OpenAI 有不同的 API 形式，不能将参数名直接混用：

| 用途 | Responses API | Chat Completions API |
| --- | --- | --- |
| 请求地址 | `/v1/responses` | `/v1/chat/completions` |
| 系统／开发者规则 | 常通过 `instructions` 提供，也支持相应角色的输入消息 | 通过 `messages` 中的 `system`／`developer` 消息，取决于模型支持 |
| 用户任务 | `input` 中的 `user` 消息 | `messages` 中的 `user` 消息 |
| 结构化输出 | `text.format` | `response_format`，具体支持取决于模型 |

auto-X 当前的数据源配置限定为 `openai_responses`，因此实际文章生成按 Responses 格式发送。配置某个第三方服务前，需要确认它支持 `/responses`、相关推理参数和严格 JSON Schema；仅支持 `/chat/completions` 并不足以直接兼容当前链路。

代码中另有旧 `codex_bridge` 适配分支，其请求体包含业务字段 `skills`、`source` 等。这是应用自定义桥接格式，不能把它当成标准 OpenAI API 参数说明。

## 10. 代码对应与参考

以下链接固定到本文核对的功能提交，便于对照实现：

| 代码 | 对应内容 |
| --- | --- |
| [ai_provider.py](https://github.com/StanXu-symple/auto-x/blob/006e4caedb7cce594be71cb7e436318486af0406/backend/app/services/ai_provider.py) | `ProviderRequest`、提示词组装、Responses 请求体与结果校验 |
| [ai_jobs.py](https://github.com/StanXu-symple/auto-x/blob/006e4caedb7cce594be71cb7e436318486af0406/backend/app/services/ai_jobs.py) | Skills、素材、作者上下文和配置的入队快照 |
| [ai_worker.py](https://github.com/StanXu-symple/auto-x/blob/006e4caedb7cce594be71cb7e436318486af0406/backend/app/ai_worker.py) | Skill 校验、执行时数据源读取与 `ProviderRequest` 构造 |
| [ai_defaults.py](https://github.com/StanXu-symple/auto-x/blob/006e4caedb7cce594be71cb7e436318486af0406/backend/app/services/ai_defaults.py) | 基础规则、默认功能点和 `DRAFT_OUTPUT_SCHEMA` |
| [ai.py](https://github.com/StanXu-symple/auto-x/blob/006e4caedb7cce594be71cb7e436318486af0406/backend/app/schemas/ai.py) | 手动生成、监听任务、全局设置等业务请求字段 |

OpenAI 官方参考入口：[Responses API](https://platform.openai.com/docs/api-reference/responses)、[推理模型说明](https://platform.openai.com/docs/guides/reasoning)、[结构化输出说明](https://platform.openai.com/docs/guides/structured-outputs)。本文的项目行为以所列代码为准，模型具体可用参数以对应服务端文档为准。
