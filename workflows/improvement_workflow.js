// =============================================================================
// 电商图谱改进工作流
// 基于 IMPROVEMENT_PLAN.md，分阶段实施 P1-P4 优先级改进
// =============================================================================

export const meta = {
  name: 'ecommerce-graph-improvement',
  description: '实施电商图谱项目的响应速度优化、查询稳定性增强和语音输入功能',
  phases: [
    { title: 'Phase 1: Code Fixes', detail: '修复代码缺陷、移除重复方法、动态Schema、GPU加速' },
    { title: 'Phase 2: Speed - Streaming', detail: '实现SSE流式响应、合并LLM调用' },
    { title: 'Phase 3: Speed - Cache & Template', detail: 'Cypher模板匹配、查询缓存层' },
    { title: 'Phase 4: Stability', detail: 'Few-shot Prompt、Cypher校验、重试机制' },
    { title: 'Phase 5: Voice Input', detail: '前端Web Speech API、后端语音端点' },
    { title: 'Phase 6: Integration & Verify', detail: '集成所有改动、验证功能' },
  ],
};

// =============================================================================
// Phase 1: 代码修复 — 并行修复 service.py 和 configuration.py
// =============================================================================
phase('Phase 1: Code Fixes');

// Agent 1.1: 修复 service.py — 移除重复方法、动态Schema、GPU检测
const serviceFixResult = await agent(
  `You are fixing E:\\agentProject\\graph\\src\\web\\service.py. Read the file first, then make these specific changes:

1. REMOVE the first duplicate _generate_cypher method (lines 92-119). Keep only the second one (lines 140-195) which is more robust with JSON extraction.

2. REPLACE the hardcoded schema (lines 41-44):
   OLD:
   if not self.graph.schema:
       self.graph.schema = """
   节点类型：SPU (商品), BaseTrademark (品牌), Category1 (一级分类), Category2 (二级分类), Category3 (三级分类)。
   关系：BELONG (属于), HAVE (拥有)。
   """
   NEW: call self.graph.refresh_schema() to dynamically load the actual Neo4j schema.

3. Add GPU auto-detection for embeddings (line 53):
   OLD: model_kwargs={"device": "cpu"}
   NEW: model_kwargs={"device": "cuda" if torch.cuda.is_available() else "cpu"}
   Also add `import torch` at the top of the file.

4. Add a Cypher validation method _validate_cypher() that uses EXPLAIN to check syntax before execution.

5. Add a retry mechanism in _generate_cypher: if JSON parsing fails, retry once with a stronger prompt.

Write the complete fixed service.py file to E:\\agentProject\\graph\\src\\web\\service.py.`,
  {
    label: 'fix-service-py',
    phase: 'Phase 1: Code Fixes',
  }
);

// Agent 1.2: 检查 configuration.py 是否有泄露的密钥需要处理
const configAuditResult = await agent(
  `Read E:\\agentProject\\graph\\configuration.py. The file contains hardcoded API keys and passwords.
  DO NOT modify the file - just report: what secrets are exposed, and recommend how to move them to environment variables.
  Write your findings to E:\\agentProject\\graph\\SECURITY_NOTE.md.`,
  {
    label: 'audit-config-security',
    phase: 'Phase 1: Code Fixes',
  }
);

log(`Phase 1 完成: service.py 修复 = ${serviceFixResult ? 'done' : 'pending'}, 安全审计 = ${configAuditResult ? 'done' : 'pending'}`);

// =============================================================================
// Phase 2: 流式响应 — 并行改造后端和前端
// =============================================================================
phase('Phase 2: Speed - Streaming');

// Agent 2.1: 改造 app.py 支持 SSE 流式响应
const streamingBackendResult = await agent(
  `Read E:\\agentProject\\graph\\src\\web\\app.py and E:\\agentProject\\graph\\src\\web\\service.py.

Modify app.py to add a NEW streaming endpoint /api/chat/stream that:
1. Uses StreamingResponse from fastapi.responses
2. Calls a new service method chat_stream() that yields SSE events
3. Keeps the original /api/chat endpoint for backward compatibility

Then modify service.py to add the chat_stream() method:
1. Use self.llm.stream() instead of self.llm.invoke() in the answer generation step
2. Yield each token as an SSE event: "data: {token}\\n\\n"
3. Send a "[DONE]" event when complete
4. Send error events on failure

Write the modified files back.`,
  {
    label: 'streaming-backend',
    phase: 'Phase 2: Speed - Streaming',
  }
);

// Agent 2.2: 改造前端 index.html 支持 SSE 流式渲染
const streamingFrontendResult = await agent(
  `Read E:\\agentProject\\graph\\src\\web\\static\\index.html.

Modify the sendMessage() function to support streaming responses:
1. Add a toggle/flag to use streaming endpoint (/api/chat/stream)
2. Use fetch + ReadableStream to process SSE events
3. Render each token as it arrives (append to the bot message bubble in real-time)
4. Add a typing indicator that shows during streaming
5. Keep the existing non-streaming behavior as fallback
6. Handle errors gracefully during streaming

Write the modified index.html back.`,
  {
    label: 'streaming-frontend',
    phase: 'Phase 2: Speed - Streaming',
  }
);

log(`Phase 2 完成: 后端流式 = ${streamingBackendResult ? 'done' : 'pending'}, 前端流式 = ${streamingFrontendResult ? 'done' : 'pending'}`);

// =============================================================================
// Phase 3: 缓存与模板匹配
// =============================================================================
phase('Phase 3: Speed - Cache & Template');

// Agent 3.1: 实现 Cypher 模板匹配系统
const templateResult = await agent(
  `Read E:\\agentProject\\graph\\src\\web\\service.py.

Add a Cypher template matching system to ChatService. Create a new method _match_template(question: str) that:

1. Defines 10-15 common query templates as a list of dicts:
   [
     {
       "patterns": ["有哪些.*品牌", "什么品牌", "品牌列表", "brands"],
       "cypher": "MATCH (t:BaseTrademark) RETURN t.name AS name LIMIT 20",
       "answer_template": "平台有以下品牌：{names}"
     },
     {
       "patterns": [".*分类", "category", "categories"],
       "cypher": "MATCH (c:Category1) RETURN c.name AS name LIMIT 20",
       "answer_template": "一级分类包括：{names}"
     },
     {
       "patterns": ["(.*)品牌.*商品", "(.*)有什么", "(.*)的产品"],
       "cypher": "MATCH (s:SPU)-[:Belong]->(t:BaseTrademark {{name: $brand}}) RETURN s.name AS name LIMIT 20",
       "params": {"brand": "$1"},
       "answer_template": "{brand}品牌有以下商品：{names}"
     },
     {
       "patterns": [".*属于.*分类", ".*分类.*商品", ".*分类下.*"],
       "cypher": "MATCH (s:SPU)-[:Belong]->(c:Category3 {{name: $category}}) RETURN s.name AS name LIMIT 20",
       "params": {"category": "$1"}
     },
     // ... add more templates covering: price queries, SKU queries, attribute queries, etc.
   ]

2. For each template, use the existing self.embeddings to compute similarity between question and pattern list
3. Return the best matching template if similarity > threshold (0.7), else return None

3. Modify the chat() method: try _match_template first. If matched, use the template's cypher directly (skip LLM Cypher generation). If not matched, fall back to LLM-based Cypher generation.

Use Edit tool to add these changes to service.py.`,
  {
    label: 'cypher-template-matching',
    phase: 'Phase 3: Speed - Cache & Template',
  }
);

// Agent 3.2: 实现查询缓存层
const cacheResult = await agent(
  `Read E:\\agentProject\\graph\\src\\web\\service.py.

Add an in-memory LRU cache layer to ChatService:

1. Add a cache import and init in __init__():
   from collections import OrderedDict
   self.cache = OrderedDict()
   self.cache_max_size = 100

2. Add _cache_key(question: str) method that normalizes the question (strip spaces, lowercase) to use as cache key

3. Add _cache_get(key: str) and _cache_set(key: str, value: str) methods with LRU eviction

4. Modify chat() method:
   - Check cache before processing
   - Cache the result after successful generation
   - Skip cache for error responses

5. Add cache statistics tracking (hits, misses) and a /api/cache/stats endpoint in app.py

Use Edit tool to add these changes to service.py and app.py.`,
  {
    label: 'query-cache',
    phase: 'Phase 3: Speed - Cache & Template',
  }
);

log(`Phase 3 完成: 模板匹配 = ${templateResult ? 'done' : 'pending'}, 缓存层 = ${cacheResult ? 'done' : 'pending'}`);

// =============================================================================
// Phase 4: 稳定性增强
// =============================================================================
phase('Phase 4: Stability');

// Agent 4.1: Few-shot Prompt 优化
const fewshotResult = await agent(
  `Read E:\\agentProject\\graph\\src\\web\\service.py.

Enhance the _generate_cypher method's prompt template with Few-shot examples. Add 6-8 examples covering:

1. 品牌查询: "有哪些手机品牌？" → MATCH (t:BaseTrademark) RETURN t.name
2. 分类查询: "有哪些商品分类？" → MATCH (c:Category1) RETURN c.name
3. 品牌+分类组合: "华为手机有哪些？" → MATCH (s:SPU)-[:Belong]->(t:BaseTrademark), (s)-[:Belong]->(c:Category3) WHERE t.name CONTAINS '华为' AND c.name CONTAINS '手机' RETURN s.name
4. 属性查询: "有哪些颜色的衣服？" → MATCH (s:SPU)-[:Have]->(a:SaleAttr)-[:Have]->(v:SaleAttrValue) WHERE v.name CONTAINS '颜色' RETURN s.name
5. 关系查询: "XX商品属于哪个品牌？" → MATCH (s:SPU)-[:Belong]->(t:BaseTrademark) WHERE s.name CONTAINS 'XX' RETURN t.name
6. SKU查询: "XX商品有哪些规格？" → MATCH (sku:SKU)-[:Belong]->(s:SPU) WHERE s.name CONTAINS 'XX' RETURN sku.name

Each example should show: User Question → Expected JSON output with cypher_query and entities_to_align.

Also add these rules to the prompt:
- Always use CONTAINS for fuzzy name matching (not =)
- Always limit results with LIMIT 20 unless user asks for more
- For aggregate questions (count, sum, avg), use appropriate aggregation functions
- Never use Cartesian product (avoid MATCH (a), (b) without relationship)

Write the modified service.py back.`,
  {
    label: 'fewshot-prompt',
    phase: 'Phase 4: Stability',
  }
);

// Agent 4.2: 多层校验 + 重试 + 实体对齐增强
const validationResult = await agent(
  `Read E:\\agentProject\\graph\\src\\web\\service.py.

Implement the following stability enhancements:

1. _validate_cypher(cypher_query: str) method:
   - Use EXPLAIN to check syntax: "EXPLAIN " + cypher_query
   - Catch Neo4j errors and return error message
   - Return (is_valid: bool, error_msg: str)

2. Retry mechanism in chat():
   - If _validate_cypher fails, send error back to LLM with the error message
   - Allow max 2 retries
   - After 2 failures, use fallback templates

3. _validate_result(query_result, question) method:
   - If result is empty list, return a flag so answer generation can say "未找到相关信息"
   - If result has >100 rows, truncate to first 50 for the LLM answer generation

4. Enhanced _entity_align():
   - After vector similarity, if no result found (results is empty), try fuzzy matching using difflib.SequenceMatcher against all entity names in that label's index
   - Add a similarity threshold of 0.6 for fuzzy matching
   - Log alignment failures with details for debugging

Use Edit tool to make these changes. Import difflib at the top of the file.`,
  {
    label: 'validation-retry',
    phase: 'Phase 4: Stability',
  }
);

log(`Phase 4 完成: Few-shot = ${fewshotResult ? 'done' : 'pending'}, 校验重试 = ${validationResult ? 'done' : 'pending'}`);

// =============================================================================
// Phase 5: 语音输入
// =============================================================================
phase('Phase 5: Voice Input');

// Agent 5.1: 前端语音输入 (Web Speech API)
const voiceFrontendResult = await agent(
  `Read E:\\agentProject\\graph\\src\\web\\static\\index.html.

Add voice input support using the Web Speech API:

1. Add a microphone button next to the send button:
   - Style: circular button with mic icon (🎤)
   - States: idle (gray), listening (red with pulse animation), processing (spinner)

2. Implement voice recognition:
   - Use webkitSpeechRecognition / SpeechRecognition API
   - Set language to 'zh-CN'
   - interimResults: true (show partial results in input box)
   - On final result: auto-fill input box and optionally auto-send

3. Add graceful fallback:
   - Check for API availability: if (!('SpeechRecognition' in window) && !('webkitSpeechRecognition' in window))
   - If not available, hide the mic button or show "浏览器不支持语音输入" tooltip

4. Handle errors:
   - 'not-allowed': show "请允许麦克风权限"
   - 'no-speech': show "未检测到语音"
   - 'network': show "网络错误，请重试"

Write the modified index.html back.`,
  {
    label: 'voice-frontend',
    phase: 'Phase 5: Voice Input',
  }
);

// Agent 5.2: 后端语音端点 (可选，基于 Whisper)
const voiceBackendResult = await agent(
  `Read E:\\agentProject\\graph\\src\\web\\app.py.

Add an optional backend voice endpoint:

1. Add a POST /api/voice endpoint that:
   - Accepts multipart/form-data with an audio file (wav/mp3/webm)
   - Returns JSON: {"text": "识别结果", "success": true/false, "error": "..."}

2. Implementation options (use option A for simplicity):
   Option A (recommended): Use requests to call a free STT API
   Option B: Use openai-whisper library with the "base" model locally

3. For now, implement as a placeholder that returns:
   {"text": "", "success": false, "error": "请使用浏览器内置语音功能"}

   This way the endpoint exists for future Whisper integration.

Add a note comment explaining how to enable real Whisper integration later.

Modify app.py only.`,
  {
    label: 'voice-backend',
    phase: 'Phase 5: Voice Input',
  }
);

log(`Phase 5 完成: 前端语音 = ${voiceFrontendResult ? 'done' : 'pending'}, 后端语音端点 = ${voiceBackendResult ? 'done' : 'pending'}`);

// =============================================================================
// Phase 6: 集成验证
// =============================================================================
phase('Phase 6: Integration & Verify');

// Agent 6.1: 全量代码审查 — 检查所有改动的一致性和正确性
const integrationAudit = await agent(
  `Review all the modified files in this project for consistency:

1. Read E:\\agentProject\\graph\\src\\web\\service.py — verify:
   - No duplicate methods
   - All new methods are properly integrated into chat()
   - Imports are correct and complete
   - No undefined variables

2. Read E:\\agentProject\\graph\\src\\web\\app.py — verify:
   - New endpoints are properly defined
   - Imports include StreamingResponse if used
   - Routes don't conflict

3. Read E:\\agentProject\\graph\\src\\web\\static\\index.html — verify:
   - Voice button integrates with existing UI
   - Streaming and non-streaming paths both work
   - No JavaScript errors (check for undefined variables, missing closing braces)

4. Cross-file consistency check:
   - service.py methods called from app.py exist
   - API endpoints called from index.html exist
   - All configuration references are correct

Report any issues found and fix them if possible. Write a summary to E:\\agentProject\\graph\\INTEGRATION_REPORT.md.`,
  {
    label: 'integration-verify',
    phase: 'Phase 6: Integration & Verify',
  }
);

// Agent 6.2: 创建测试脚本
const testScriptResult = await agent(
  `Create a comprehensive test script at E:\\agentProject\\graph\\test_improvements.py that:

1. Tests the streaming endpoint:
   - Send a POST to /api/chat/stream with {"message": "有哪些手机品牌？"}
   - Verify SSE events are received
   - Verify [DONE] event is sent

2. Tests the template matching:
   - Send common questions that should match templates
   - Verify response time < 3 seconds (template path skips LLM Cypher generation)

3. Tests caching:
   - Send same question twice
   - Verify second response is faster (cache hit)

4. Tests error handling:
   - Send malformed requests
   - Verify proper error responses

5. Tests voice endpoint (if implemented):
   - Send POST to /api/voice
   - Verify JSON response

6. Tests backward compatibility:
   - Send POST to /api/chat (original endpoint)
   - Verify it still works

Include instructions for running the tests.`,
  {
    label: 'test-script',
    phase: 'Phase 6: Integration & Verify',
  }
);

log(`Phase 6 完成: 集成审查 = ${integrationAudit ? 'done' : 'pending'}, 测试脚本 = ${testScriptResult ? 'done' : 'pending'}`);

// =============================================================================
// 最终汇总
// =============================================================================
log(`
========================================
改进工作流执行完毕
========================================

修改文件清单：
- src/web/service.py    (核心改造：模板匹配、缓存、流式、Few-shot、校验重试)
- src/web/app.py         (新端点：/api/chat/stream、/api/voice、/api/cache/stats)
- src/web/static/index.html (前端：流式渲染、语音输入)
- test_improvements.py   (新增：综合测试脚本)
- INTEGRATION_REPORT.md  (新增：集成审查报告)
- SECURITY_NOTE.md       (新增：安全建议)

预期效果：
- 常见问题响应时间：< 2s  (模板匹配 + 缓存)
- 复杂问题首字延迟：2~3s  (流式响应)
- 查询稳定性提升：Cypher校验 + Few-shot + 重试
- 新功能：浏览器语音输入 (Chrome/Edge)
`);
