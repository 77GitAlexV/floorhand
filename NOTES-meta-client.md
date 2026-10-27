# Meta client / Клиент Meta

Built `floorhand/meta/` with structured text extraction, image understanding,
file transcription, SAM image segmentation and an owner's Ask tool loop.
All network traffic goes through `floorhand/models.py`; no packages were installed.
No feature, frontend, schema or domain files were changed. No commit was created.

Создан пакет `floorhand/meta/`: JSON, распознавание снимков, расшифровка WAV,
сегментация SAM и цикл инструментов для вопросов владельца. Сеть и бюджет проходят
через `floorhand/models.py`. Пакеты не устанавливались, коммит не создавался.

## Calling the clients

```python
from floorhand.meta import BudgetStop, text, vision, voice, sam, tools

schema = {
    "type": "object",
    "properties": {"qty": {"type": "integer", "minimum": 1}},
    "required": ["qty"],
    "additionalProperties": False,
}

try:
    proposal = text.respond_json(conn, "intake-json", "Extract only stated facts.",
                                 "Invented sample: two chairs.", schema)
    fields = vision.understand(conn, "order-photo", image_bytes, "image/png",
                                "Read the invented order form.", schema)
    description = vision.understand(conn, "sheet-photo", image_bytes, "image/png",
                                     "Describe the fictional sheet.")
    transcript = voice.transcribe(conn, audio_bytes, "audio/wav", "en")
    regions = sam.segment(conn, image_bytes, "image/png", "upholstered armrest")
    reply = tools.run(conn, "owner-ask", "Use only the supplied shop facts.",
                      "Which fictional units are waiting?", {
        "waiting": tools.Tool(
            function=lambda: {"unit_labels": []},
            parameters={"type": "object", "properties": {},
                        "additionalProperties": False},
            description="Read fictional waiting units; no writes.",
        ),
    })
except BudgetStop:
    pass  # The feature must return its offline answer or manual-entry path here.
```

`vision.understand` returns text without a schema, a dict with one.
`voice.transcribe` returns the complete API dict (`transcript`, `audioDurationMs`,
`turns`, etc.). `sam.segment` returns `objects`, `boxes`, encoded `masks`, `count`,
`basis="ESTIMATE"`, and a bilingual review message. `tools.run` returns `answer`
and `trace` with call IDs, names, parsed arguments, success flags and results/errors.
Tools are trusted read-only Python callables with object parameter schemas. Tool
closures must use the supplied connection. `PRAGMA query_only` prevents accidental
writes on that connection during execution and is restored afterward; this is
not a sandbox for arbitrary Python, other connections, filesystem or network I/O.
Register only role-filtered lookup tools. Human-confirmed proposal application
stays in feature/domain code, outside this loop.

По умолчанию API выключен. При `BudgetStop` функция интерфейса должна показать
офлайн-ответ или ручной ввод. Ответы модели не применяются к данным мастерской.
В инструменты передавайте только проверенные функции чтения с фильтрацией по роли.

## Verified API contracts

Verified against the official documentation during this task, on 2026-10-27;
these are documentation checks, **not live paid API tests**. Shape-specific
builders/parsers are in `meta/_wire.py`; HTTP, SSE and usage accounting are in
`models.py`. No undocumented endpoint is used.

- Base: `https://api.meta.ai/v1`; header `Authorization: Bearer $MODEL_API_KEY`.
  JSON requests use `Content-Type: application/json`.
  [Quickstart](https://dev.meta.ai/docs/quickstart),
  [API reference](https://dev.meta.ai/docs/api-reference),
  [Models](https://dev.meta.ai/docs/models).
- Text: `POST /v1/responses` with `model`, `input`, `max_output_tokens`,
  `store:false`, `text:{format:{type:"json_schema",name:"floorhand_result",schema,strict:false}}`.
  Input is system/user messages. `strict:false` accepts optional properties
  without changing the caller's schema. Extract text from message content
  `type:"output_text"`; also accept the SDK-style `output_text` shortcut.
  Defensive JSON parsing handles fences/prose and braces within strings, rejects
  duplicate keys and non-finite numbers, validates the object, then makes at most
  one separately budgeted repair request. It never substitutes an inner object
  from an invalid outer answer.
  [Structured output](https://dev.meta.ai/docs/structured-output),
  [Responses schemas](https://dev.meta.ai/docs/api-reference/responses/schemas).
- Vision: same Responses endpoint, a user message containing
  `{type:"input_text",text:prompt}` and
  `{type:"input_image",image_url:"data:<mime>;base64,<bytes>"}`.
  JSON output uses the same format as text. Supported image MIME types and the
  inline size limit follow the guide.
  [Image understanding](https://dev.meta.ai/docs/image-understanding).
- Voice: `POST /v1/asr/transcribe`, multipart parts `request` (JSON MIME type)
  and `audio` (WAV bytes). Settings are
  `{model:"muse-voice-transcribe-1.0",audioEncoding:"WAV",mode:"PUSH_TO_TALK"}`;
  optional `languageBias:["English"]` (names, not ISO codes). The client accepts
  `en` as an alias. Read `transcript` and `audioDurationMs`, preserving `turns`.
  Input must be mono signed PCM WAV, 16-bit, at 16 or 24 kHz; validate and measure
  the actual frames locally before sending. Truncated audio is refused.
  [Speech to text](https://dev.meta.ai/docs/speech-to-text).
- SAM: `POST /v1/responses`, `model:"sam-3.1"`, `stream:true`, text/image
  content as above, `Accept:text/event-stream`; no token output limit. Collect
  `response.output_text.delta` until `response.completed`. Empty completed text
  means no matches; interrupted streams are failures. Image output is `<0f>`
  followed by comma-separated object records, each with an integer ID, box token
  and mask token. Boxes have source-pixel corners and source `w,h`. Masks preserve
  their height, width, `~`/`!` encoding and base85 payload; confidence is optional.
  Count records, preserving sparse object IDs. Reject malformed geometry.
  [Overview](https://dev.meta.ai/docs/sam/overview),
  [Segmenting](https://dev.meta.ai/docs/sam/segmenting),
  [Reading segmentation](https://dev.meta.ai/docs/sam/reading-segmentation).
- Tools: Responses `tools:[{type:"function",name,description,parameters,strict:false}]`.
  Replay the whole `output`, including encrypted reasoning and message `phase`,
  followed by `{type:"function_call_output",call_id,output:<JSON string>}`.
  Each request includes `store:false`, `include:["reasoning.encrypted_content"]`.
  Only explicitly registered names execute, after argument validation; duplicate
  IDs stop. Defaults are bounded by model steps and total local calls. Failed
  tools return a generic bilingual error and a trace without exception secrets.
  [Tool calling](https://dev.meta.ai/docs/tool-calling),
  [Responses replay](https://dev.meta.ai/docs/protocols/responses).

Chat Completions and Files API are unnecessary for these clients: Responses
supports all text/vision/tool operations and images are inline. The generic
multipart transport can encode file uploads, but no upload lifecycle is exposed.

## Budget, units and retries

`FLOORHAND_META_LIVE=1` and `MODEL_API_KEY` are both required.
`FLOORHAND_BUDGET_USD` retains its existing default. Prices remain unchanged and
were checked against [official pricing](https://dev.meta.ai/docs/pricing-rate-limits).
Tokens use input/output counts with cached input discounted. Voice uses audio
seconds rounded **down** from `audioDurationMs`; absent duration uses locally
measured whole WAV seconds and marks the charge estimated. SAM uses
`usage.images_processed`; absent usage charges the single submitted image and
marks the charge estimated. Token usage never prices SAM or Voice.

Every attempt atomically reserves its maximum in `model_calls` before I/O, so
different connections cannot consume the same remaining balance. On success the
same row settles to reported usage. Missing/invalid usage retains the estimate.
Failed/ambiguous attempts remain charged conservatively, even an HTTP rejection
that might actually be free. A process crash retains the reservation; there is
no automatic refund. The ledger continues using explicit UTC timestamps.

Text reservations use the serialized request's UTF-8 byte count plus framing
allowance, including schemas, tools, instructions and replay. For opaque media
or replayed encrypted reasoning, reserve the entire available input context
instead of guessing image tokens. This deliberately can refuse a cheap image
request near the budget limit. Built-in hosted tools and server-managed history
are rejected because their extra billing/history would not be bounded here.

Retries: at most three attempts; recheck and reserve for each. Retry HTTP 429
and 5xx, honoring `Retry-After` seconds or HTTP dates, otherwise exponential
backoff plus jitter. Exception: do not replay HTTP 504, whose guide recommends
reducing work or changing request mode. Permanent 4xx and ambiguous transport
timeouts stop with `BudgetStop`. Transient SAM terminal stream errors retry the
whole request. [Error handling](https://dev.meta.ai/docs/error-handling).

## Limits / TODO / Что ещё нужно

- No endpoint shape is knowingly assumed. Live credentials were not used; a
  configured live smoke test remains for the reviewer, using invented inputs.
- TODO: authoritative `/responses/input_tokens` preflight could replace the
  conservative reservation heuristic. The byte/framing estimate is a local
  policy, not a documented tokenizer formula. Opaque inputs use the documented
  context ceiling. As with any client, actual billing relies on the provider
  honoring model context/output limits and reporting usage correctly.
- Dependency-free local JSON Schema validation supports objects, arrays, types,
  required/additional properties, local references/definitions, enum/const,
  anyOf/oneOf/allOf, numeric bounds, string length/pattern, array length/uniqueness
  and property counts. Unsupported keywords fail before spend; it is not a full
  JSON Schema implementation. No new validation dependency was added.
- TODO: SAM masks are encoded artifacts, not decoded pixel rasters. A documented
  compatible decoder is needed for raster overlays; no guessed RLE/base85 codec
  was introduced. Video segmentation is outside this image-only API.
- Voice rejects WebM/MP3/stereo/other rates. TODO: convert upstream, or provide a
  configured converter. No executable or package was installed. Russian is not
  in the documented Voice language list; an explicit Russian hint uses the
  offline boundary. English and Russian interface text is present throughout.
- Нет встроенного преобразования WebM/MP3 в WAV и декодирования масок SAM.
  Русский язык отсутствует в документированном списке Voice; используйте ручной
  ввод или офлайн-путь. Клиенты не подключены к параллельно изменяемым функциям.
- No frontend or feature integration is included; callers must catch BudgetStop,
  retain manual/offline behavior, validate business facts and require human
  confirmation before applying proposals. Numeric prose from Ask is prompted
  to carry FACT/CALC/ESTIMATE labels; model compliance is not independently proven.

## Tests / Проверка

FACT: **129 tests passed**, including the existing invariant/intake/budget suite,
with fake transports only. Coverage includes request shapes and HTTP encoding,
cached/token/image/whole-second costs, retries and header delays, interrupted
streams, zero SAM matches, malformed outputs, JSON repair and budget exhaustion,
tool schema/name/ID/step limits, write blocking and concurrent reservations.
One pre-existing Starlette/httpx deprecation warning remains.

Standard command from this worktree:

```powershell
& 'E:/Work/Floorhand/floorhand/.venv/Scripts/python' -m pytest -q
```

The supplied venv launcher could not find its base interpreter in this session.
An already installed matching Python ran the suite with the supplied packages;
no environment files were modified. Exact successful command:

```powershell
$env:PYTHONPATH='E:/Work/Floorhand/floorhand/.venv/Lib/site-packages'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe' -B -m pytest -q -p no:cacheprovider --basetemp=E:/Work/Floorhand/wt/cx-meta-client/.pytest-meta-tmp
```

ФАКТ: тесты прошли без сетевых запросов и без расходов на модели. Использован
имеющийся Python с готовыми зависимостями; окружение не изменялось.
