# graph/src/web/service.py
import json
import re
import difflib
import numpy as np
import torch
from collections import OrderedDict
from typing import Dict, List, Any

from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_deepseek import ChatDeepSeek
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_neo4j import Neo4jVector, Neo4jGraph
from neo4j_graphrag.types import SearchType

from configuration import config

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

class ChatService:
    def __init__(self):
        print("0. 开始初始化 ChatService...")
        self.cache = OrderedDict()
        self.cache_max_size = 100
        self.cache_hits = 0
        self.cache_misses = 0
        try:
            print("1. 初始化 LLM...")
            self.llm = ChatDeepSeek(model="deepseek-v4-pro", temperature=0, api_key=config.DEEPSEEK_API_KEY)
            print("   LLM 初始化成功")

            print("2. 初始化 Neo4j 图连接...")
            self.graph = Neo4jGraph(
                url=config.NEO4J_CONFIG["uri"],
                username=config.NEO4J_CONFIG["user"],
                password=config.NEO4J_CONFIG["password"],
                database=config.NEO4J_CONFIG.get("database", "neo4j"),
                refresh_schema=False
            )
            print("   Neo4j 图连接成功")

            # Dynamically load the actual Neo4j schema from the database
            try:
                self.graph.refresh_schema()
            except Exception as schema_e:
                print(f"   refresh_schema() failed (APOC not available?): {schema_e}")
                print(f"   使用手动 schema 构建...")
                self._build_manual_schema()
            print(f"   Neo4j schema loaded: {self.graph.schema[:200] if self.graph.schema else 'No schema available'}...")

            print("3. 初始化嵌入模型...")
            # 使用本地嵌入模型路径
            model_path = config.EMBEDDING_MODEL_PATH
            print(f"   模型路径: {model_path}")
            self.embeddings = HuggingFaceEmbeddings(
                model_name=model_path,
                model_kwargs={"device": "cuda" if torch.cuda.is_available() else "cpu"},
                encode_kwargs={"normalize_embeddings": True}
            )
            print("   嵌入模型加载完成")

            print("4. 初始化向量存储...")
            self.vector_stores = {}
            # 定义需要加载的索引列表
            stores = ['SPU', 'BaseTrademark', 'Category3', 'Category2', 'Category1']
            for label in stores:
                print(f"   加载 {label} 向量索引...")
                store = self._init_vector_store(label)
                self.vector_stores[label] = store
                if store is not None:
                    print(f"      {label} 向量索引就绪")
                else:
                    print(f"      警告: {label} 向量索引不可用（无数据或创建失败）")
            print("   向量存储初始化完成")

            self.json_parser = JsonOutputParser()
            self.str_parser = StrOutputParser()
            print("5. 解析器初始化完成")
            print("ChatService 初始化全部成功")
        except Exception as e:
            print(f"!!! ChatService 初始化失败: {e}")
            import traceback
            traceback.print_exc()
            raise

    def _init_vector_store(self, label: str):
        """
        Initialize a Neo4jVector hybrid search index for the given node label.
        1. Try to load an existing index via from_existing_index().
        2. If the index doesn't exist, use from_texts() to create it from node names.
        3. If there are no nodes of this label, return None.
        """
        index_name = f"{label.lower()}_embedding_index"
        keyword_index_name = f"{label.lower()}_full_text_index"
        neo4j_kwargs = {
            "url": config.NEO4J_CONFIG['uri'],
            "username": config.NEO4J_CONFIG['user'],
            "password": config.NEO4J_CONFIG['password'],
            "database": config.NEO4J_CONFIG.get("database", "neo4j"),
        }

        # Step 1: try existing index
        try:
            store = Neo4jVector.from_existing_index(
                embedding=self.embeddings,
                index_name=index_name,
                keyword_index_name=keyword_index_name,
                search_type=SearchType.HYBRID,
                **neo4j_kwargs,
            )
            print(f"      已存在 {label} 向量索引")
            return store
        except Exception as e:
            print(f"      未找到 {label} 已有索引 ({e})，尝试自动创建...")

        # Step 2: check whether nodes of this label exist and collect names
        try:
            rows = self.graph.query(f"MATCH (n:{label}) RETURN n.name AS name LIMIT 5000")
            # Flatten result: query returns list of dicts like {'name': 'xxx'}
            names = []
            for row in rows:
                for v in row.values():
                    if v and isinstance(v, str) and v.strip():
                        names.append(v.strip())
        except Exception as e:
            print(f"      查询 {label} 节点失败: {e}")
            return None

        if not names:
            print(f"      标签 {label} 下没有节点，跳过向量索引创建")
            return None

        # Deduplicate
        names = list(set(names))
        print(f"      为 {label} 创建向量索引 (节点数={len(names)})...")

        # Step 3: use from_texts() to create the index AND populate embeddings
        # This creates Chunk nodes internally, which is the standard langchain_neo4j pattern
        try:
            store = Neo4jVector.from_texts(
                texts=names,
                embedding=self.embeddings,
                index_name=index_name,
                keyword_index_name=keyword_index_name,
                search_type=SearchType.HYBRID,
                **neo4j_kwargs,
            )
            print(f"      {label} 向量索引自动创建完成 (from_texts)")
            return store
        except Exception as e:
            print(f"      自动创建 {label} 索引失败: {e}")
            # Last resort: try creating JUST the vector index via Cypher
            # (Neo4j 5.15+ Community supports CREATE VECTOR INDEX natively)
            try:
                test_embedding = self.embeddings.embed_query("test")
                dim = len(test_embedding)
                self.graph.query(f"""
                    CREATE VECTOR INDEX {index_name} IF NOT EXISTS
                    FOR (n:{label}) ON (n.embedding)
                    OPTIONS {{indexConfig: {{
                        `vector.dimensions`: {dim},
                        `vector.similarity_function`: 'cosine'
                    }}}}
                """)
                # Populate embeddings via SET (batch)
                batch_size = 50
                for i in range(0, len(names), batch_size):
                    batch = names[i:i + batch_size]
                    batch_embs = self.embeddings.embed_documents(batch)
                    for name, emb in zip(batch, batch_embs):
                        self.graph.query(
                            f"MATCH (n:{label} {{name: $name}}) SET n.embedding = $embedding",
                            params={"name": name, "embedding": emb},
                        )
                    print(f"      嵌入填充 {i//batch_size + 1}/{(len(names)-1)//batch_size + 1}")
                # Try from_existing_index again
                store = Neo4jVector.from_existing_index(
                    embedding=self.embeddings,
                    index_name=index_name,
                    keyword_index_name=keyword_index_name,
                    search_type=SearchType.HYBRID,
                    **neo4j_kwargs,
                )
                print(f"      {label} 向量索引创建完成 (Cypher fallback)")
                return store
            except Exception as e2:
                print(f"      Cypher fallback 也失败了: {e2}")
                return None

    def _build_manual_schema(self):
        """
        Build a schema description string by directly querying Neo4j
        (fallback when APOC is not available in Community Edition).
        """
        schema_parts = []
        try:
            # Get node labels and their counts
            labels_result = self.graph.query("""
                MATCH (n)
                UNWIND labels(n) AS label
                RETURN label, count(*) AS cnt
                ORDER BY cnt DESC
            """)
            schema_parts.append("节点类型：")
            for row in labels_result:
                label = row.get("label", "")
                cnt = row.get("cnt", 0)
                # Get sample properties for this label
                try:
                    props = self.graph.query(f"""
                        MATCH (n:{label}) RETURN keys(n) AS props LIMIT 1
                    """)
                    prop_str = ""
                    if props:
                        keys = props[0].get("props", [])
                        prop_str = ", ".join(keys[:5])
                    schema_parts.append(f"  - {label} (count={cnt}, properties: [{prop_str}])")
                except Exception:
                    schema_parts.append(f"  - {label} (count={cnt})")
        except Exception as e:
            schema_parts.append(f"  (无法获取节点标签: {e})")

        schema_parts.append("")
        schema_parts.append("关系类型：")
        try:
            rels_result = self.graph.query("""
                MATCH ()-[r]->()
                RETURN DISTINCT type(r) AS rel_type, count(*) AS cnt
                ORDER BY cnt DESC
                LIMIT 20
            """)
            for row in rels_result:
                rel_type = row.get("rel_type", "")
                cnt = row.get("cnt", 0)
                schema_parts.append(f"  - {rel_type} (count={cnt})")
        except Exception as e:
            schema_parts.append(f"  (无法获取关系类型: {e})")

        self.graph.schema = "\n".join(schema_parts)
        print(f"   手动 schema 构建完成 ({len(schema_parts)} 行)")

    def _cache_key(self, question: str) -> str:
        """Normalize the question to use as a cache key."""
        return question.strip().lower()

    def _cache_get(self, key: str) -> str | None:
        """Retrieve a cached answer. Returns None on miss."""
        if key in self.cache:
            self.cache.move_to_end(key)
            self.cache_hits += 1
            return self.cache[key]
        self.cache_misses += 1
        return None

    def _cache_set(self, key: str, value: str) -> None:
        """Store an answer in the cache with LRU eviction."""
        if key in self.cache:
            self.cache.move_to_end(key)
            self.cache[key] = value
        else:
            if len(self.cache) >= self.cache_max_size:
                self.cache.popitem(last=False)
            self.cache[key] = value

    def _entity_align(self, entities_to_align):
        for node in entities_to_align:
            label = node['label']
            entity_name = node['entity']
            if label in self.vector_stores and self.vector_stores[label] is not None:
                try:
                    results = self.vector_stores[label].similarity_search(entity_name, k=1)
                    if results:
                        node['entity'] = results[0].page_content
                    else:
                        # Vector similarity found no result; fall back to fuzzy matching
                        print(f"   向量搜索未找到 '{label}' 的实体 '{entity_name}'，尝试模糊匹配...")
                        try:
                            graph_results = self.graph.query(
                                f"MATCH (n:{label}) RETURN n.name LIMIT 1000"
                            )
                            candidates = []
                            for row in graph_results:
                                for val in row.values():
                                    if val and isinstance(val, str):
                                        candidates.append(val)

                            if candidates:
                                best_match = None
                                best_score = 0.0
                                for candidate in candidates:
                                    score = difflib.SequenceMatcher(
                                        None, entity_name.lower(), candidate.lower()
                                    ).ratio()
                                    if score > best_score:
                                        best_score = score
                                        best_match = candidate

                                if best_score >= 0.6 and best_match:
                                    print(f"   模糊匹配成功: '{entity_name}' -> '{best_match}' (相似度: {best_score:.3f})")
                                    node['entity'] = best_match
                                else:
                                    print(f"   [ALIGN_FAIL] 模糊匹配未达阈值: '{entity_name}', label={label}, best='{best_match}', score={best_score:.3f}")
                            else:
                                print(f"   [ALIGN_FAIL] 标签 '{label}' 中没有找到候选实体名称")
                        except Exception as fuzzy_e:
                            print(f"   [ALIGN_FAIL] 模糊匹配出错 label={label}, entity='{entity_name}': {fuzzy_e}")
                except Exception as e:
                    print(f"   [ALIGN_FAIL] 实体对齐出错 label={label}, entity='{entity_name}': {e}")
            else:
                print(f"跳过实体对齐，未找到 {label} 的向量存储")
        return entities_to_align

    def _validate_cypher(self, cypher_query: str):
        """
        Use Cypher EXPLAIN to validate the syntax of a generated Cypher query
        before execution. Returns (is_valid: bool, error_msg: str).
        """
        try:
            explain_query = f"EXPLAIN {cypher_query}"
            self.graph.query(explain_query)
            print("   [OK] Cypher syntax validated successfully")
            return True, ""
        except Exception as e:
            error_msg = str(e)
            print(f"   [FAIL] Cypher validation failed: {error_msg}")
            return False, error_msg

    def _execute_cypher(self, cypher: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        """Execute a Cypher query and return results."""
        results = self.graph.query(cypher, params=params)
        return results

    def _validate_result(self, query_result, question):
        """
        Validate and post-process query results.
        Returns (processed_result: List[Dict], is_empty: bool).
        - If result is empty, return flag so answer generation can indicate no results.
        - If result has >100 rows, truncate to first 50.
        """
        if not query_result:
            print(f"   查询结果为空")
            return query_result, True

        row_count = len(query_result)
        if row_count > 100:
            print(f"   查询结果行数 ({row_count}) 超过100，截断至前50行")
            return query_result[:50], False

        return query_result, False

    def _generate_cypher(self, question: str, schema_info: str):
        """
        Generate a Cypher query statement, handling cases where the LLM
        output is not valid JSON. Includes a retry mechanism: if JSON
        parsing fails on the first attempt, retries once with a stronger,
        more explicit prompt.
        """
        # 1. Construct the primary prompt with strong constraints,
        #    few-shot examples, and critical rules
        prompt = PromptTemplate(
            input_variables=["question", "schema_info"],
            template="""
    你是一个严格的 Cypher 生成器。你的唯一任务是根据用户问题输出一个 JSON 对象，不要输出任何其他文字、解释或标记。

    === 重要规则 ===
    1. 名称模糊匹配必须使用 CONTAINS，不要使用 = 进行精确匹配。例如：WHERE t.name CONTAINS '华为'，而不是 WHERE t.name = '华为'
    2. 除非用户明确要求更多（如"全部"、"所有"、"每个"），否则始终使用 LIMIT 20 限制结果数量
    3. 对于计数、求和、平均值等聚合问题，使用相应的聚合函数（count, sum, avg, max, min），并用 AS 给结果列起别名
    4. 严禁使用笛卡尔积。不要在 MATCH 中用逗号分隔无直接关系的节点，如 MATCH (a), (b) 是错误的；必须通过关系连接，如 MATCH (a)-[r]->(b)

    === 知识图谱结构 ===
    节点类型：
    - BaseTrademark: 品牌节点，属性包括 name
    - Category1 / Category2 / Category3: 三级分类节点，每级属性包括 name
    - SPU: 标准商品节点，属性包括 name, price 等
    - SKU: 商品规格节点，属性包括 name
    - SaleAttr: 销售属性节点，属性包括 name
    - SaleAttrValue: 销售属性值节点，属性包括 name

    关系类型：
    - (SPU)-[:Belong]->(BaseTrademark): 商品属于某个品牌
    - (SPU)-[:Belong]->(Category3): 商品属于某个三级分类
    - (Category1)-[:Has]->(Category2)-[:Has]->(Category3): 分类层级关系
    - (SPU)-[:Have]->(SaleAttr)-[:Have]->(SaleAttrValue): 商品具有某个销售属性及其属性值
    - (SKU)-[:Belong]->(SPU): SKU 属于某个商品

    当前数据库 schema: {schema_info}

    === Few-shot 示例（严格按照示例格式输出 JSON） ===

    示例1:
    用户问题: 有哪些手机品牌？
    输出: {{
      "cypher_query": "MATCH (t:BaseTrademark) RETURN t.name LIMIT 20",
      "entities_to_align": []
    }}

    示例2:
    用户问题: 有哪些商品分类？
    输出: {{
      "cypher_query": "MATCH (c:Category1) RETURN c.name LIMIT 20",
      "entities_to_align": []
    }}

    示例3:
    用户问题: 华为手机有哪些？
    输出: {{
      "cypher_query": "MATCH (s:SPU)-[:Belong]->(t:BaseTrademark), (s)-[:Belong]->(c:Category3) WHERE t.name CONTAINS '华为' AND c.name CONTAINS '手机' RETURN s.name LIMIT 20",
      "entities_to_align": []
    }}

    示例4:
    用户问题: 有哪些颜色的衣服？
    输出: {{
      "cypher_query": "MATCH (s:SPU)-[:Have]->(a:SaleAttr)-[:Have]->(v:SaleAttrValue) WHERE v.name CONTAINS '颜色' RETURN DISTINCT v.name LIMIT 20",
      "entities_to_align": []
    }}

    示例5:
    用户问题: 华为P60属于哪个品牌？
    输出: {{
      "cypher_query": "MATCH (s:SPU)-[:Belong]->(t:BaseTrademark) WHERE s.name CONTAINS '华为P60' RETURN t.name LIMIT 20",
      "entities_to_align": []
    }}

    示例6:
    用户问题: 华为P60有哪些规格？
    输出: {{
      "cypher_query": "MATCH (sku:SKU)-[:Belong]->(s:SPU) WHERE s.name CONTAINS '华为P60' RETURN sku.name LIMIT 20",
      "entities_to_align": []
    }}

    示例7:
    用户问题: 华为有多少个商品？
    输出: {{
      "cypher_query": "MATCH (s:SPU)-[:Belong]->(t:BaseTrademark) WHERE t.name CONTAINS '华为' RETURN count(s) AS count",
      "entities_to_align": []
    }}

    示例8:
    用户问题: 手机属于哪个分类？
    输出: {{
      "cypher_query": "MATCH (c1:Category1)-[:Has]->(c2:Category2)-[:Has]->(c3:Category3) WHERE c3.name CONTAINS '手机' RETURN c1.name, c2.name, c3.name LIMIT 20",
      "entities_to_align": []
    }}

    ---

    用户问题：{question}

    请只输出 JSON，不要有其他内容：
    """
        ).format(question=question, schema_info=schema_info)

        # 2. Call LLM and get raw output
        print("=== Prompt to LLM ===")
        print(prompt)
        raw_response = self.llm.invoke(prompt)
        raw_text = raw_response.content.strip()
        print("=== Raw LLM Response ===")
        print(raw_text)

        # 3. Attempt to parse and return on first try
        result = self._try_parse_cypher_response(raw_text, question)
        if result is not None:
            return result

        # 4. Retry once with a stronger, more explicit prompt
        print("[WARN] First attempt failed to parse JSON. Retrying with a stronger prompt...")
        retry_prompt = PromptTemplate(
            input_variables=["question", "schema_info"],
            template="""
    OUTPUT ONLY VALID JSON. NO MARKDOWN. NO EXPLANATION. NO CODE BLOCKS.

    You MUST respond with exactly this JSON structure and nothing else:
    {{"cypher_query": "<valid Cypher statement>", "entities_to_align": []}}

    CRITICAL RULES for the Cypher statement:
    - Use CONTAINS for fuzzy name matching, never =
    - Always add LIMIT 20 unless the user asks for more
    - Use count/sum/avg for aggregate questions
    - Never use Cartesian product: always connect nodes via relationships

    Schema: {schema_info}
    Question: {question}

    Your response must start with {{ and end with }}. No leading or trailing text.
    """
        ).format(question=question, schema_info=schema_info)

        print("=== Retry Prompt to LLM ===")
        print(retry_prompt)
        raw_response = self.llm.invoke(retry_prompt)
        raw_text = raw_response.content.strip()
        print("=== Retry Raw LLM Response ===")
        print(raw_text)

        result = self._try_parse_cypher_response(raw_text, question)
        if result is not None:
            return result

        # 5. Both attempts failed, use default fallback
        print("[ERROR] Both attempts failed to produce valid JSON. Using default Cypher.")
        return self._get_default_cypher(question)

    def _try_parse_cypher_response(self, raw_text: str, question: str):
        """
        Attempt to extract and parse JSON from LLM output.
        Returns the parsed dict on success, None on failure.
        """
        # Extract JSON (handle markdown code blocks, extra text)
        json_str = self._extract_json_from_text(raw_text)
        if not json_str:
            print("[WARN] No valid JSON found in output")
            return None

        # Parse JSON
        try:
            cypher_obj = json.loads(json_str)
            if "cypher_query" not in cypher_obj:
                print("[WARN] Missing 'cypher_query' key in parsed JSON")
                return None
            print("=== Parsed JSON ===")
            print(cypher_obj)
            return cypher_obj
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[ERROR] JSON parse error: {e}")
            return None

    def _extract_json_from_text(self, text: str) -> str:
        """
        Extract JSON string from LLM output (handles markdown code blocks
        and surrounding text).
        """
        # Try to match ```json ... ``` or ``` ... ```
        code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block_match:
            return code_block_match.group(1).strip()
        # Fallback: find content between the first { and the last }
        first_brace = text.find('{')
        last_brace = text.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            return text[first_brace:last_brace + 1].strip()
        return ""

    def _get_default_cypher(self, question: str) -> dict:
        """
        Fallback: predefined simple Cypher when LLM output is invalid.
        """
        if "手机品牌" in question or "品牌" in question:
            cypher = "MATCH (t:BaseTrademark) RETURN t.name LIMIT 10"
        elif "分类" in question:
            cypher = "MATCH (c:Category1) RETURN c.name LIMIT 10"
        else:
            cypher = "MATCH (n) RETURN labels(n) LIMIT 5"
        return {
            "cypher_query": cypher,
            "entities_to_align": []
        }

    def _match_template(self, question: str):
        """
        Match user question against predefined Cypher templates using embedding similarity.
        Returns the best matching template (dict with patterns, cypher, answer_template)
        if similarity > 0.7, otherwise returns None.
        """
        TEMPLATES = [
            {
                "patterns": ["有哪些品牌", "什么品牌", "品牌列表", "所有品牌", "有什么品牌", "品牌有哪些", "查看品牌"],
                "cypher": "MATCH (t:BaseTrademark) RETURN t.name AS name LIMIT 20",
                "answer_template": "以下是平台的主要品牌：{results}"
            },
            {
                "patterns": ["分类", "category", "有哪些分类", "分类列表", "商品分类", "分类有哪些", "品类"],
                "cypher": "MATCH (c:Category1) RETURN c.name AS name LIMIT 20",
                "answer_template": "以下是商品的分类信息：{results}"
            },
            {
                "patterns": ["多少钱", "价格", "售价", "什么价格", "怎么卖", "价钱", "定价"],
                "cypher": "MATCH (s:SPU) RETURN s.name AS name, s.price AS price LIMIT 10",
                "answer_template": "以下是相关商品的价格信息：{results}"
            },
            {
                "patterns": ["规格", "SKU", "sku", "有哪些规格", "规格参数", "型号"],
                "cypher": "MATCH (s:SPU) RETURN s.name AS name, s.spec AS spec LIMIT 10",
                "answer_template": "以下是商品的规格信息：{results}"
            },
            {
                "patterns": ["颜色", "有什么颜色", "颜色选择", "可选颜色", "颜色有哪些"],
                "cypher": "MATCH (s:SPU) WHERE s.color IS NOT NULL RETURN DISTINCT s.color AS color LIMIT 20",
                "answer_template": "以下是可用的颜色选项：{results}"
            },
            {
                "patterns": ["尺码", "尺寸", "大小", "什么尺码", "尺码表", "多大"],
                "cypher": "MATCH (s:SPU) WHERE s.size IS NOT NULL RETURN DISTINCT s.size AS size LIMIT 20",
                "answer_template": "以下是可用的尺码选项：{results}"
            },
            {
                "patterns": ["材质", "什么材质", "材料", "面料", "什么料子", "成分"],
                "cypher": "MATCH (s:SPU) WHERE s.material IS NOT NULL RETURN DISTINCT s.material AS material LIMIT 20",
                "answer_template": "以下是商品的材质信息：{results}"
            },
            {
                "patterns": ["有多少", "多少个", "一共多少", "总共有多少", "数量", "总数", "多少个商品"],
                "cypher": "MATCH (s:SPU) RETURN count(s) AS total_count",
                "answer_template": "根据查询，平台共有 {results} 件商品。"
            },
            {
                "patterns": ["属于哪个品牌", "是哪个品牌", "什么牌子", "哪个品牌", "品牌是什么"],
                "cypher": "MATCH (s:SPU)-[:Belong]->(t:BaseTrademark) RETURN s.name AS product, t.name AS brand LIMIT 20",
                "answer_template": "以下是商品对应的品牌信息：{results}"
            },
            {
                "patterns": ["属于哪个分类", "什么分类", "哪个类别", "分类是什么", "属于什么类"],
                "cypher": "MATCH (s:SPU)-[:Belong]->(c:Category3) RETURN s.name AS product, c.name AS category LIMIT 20",
                "answer_template": "以下是商品对应的分类信息：{results}"
            },
            {
                "patterns": ["分类下有什么", "分类下的商品", "分类下", "这个分类有什么"],
                "cypher": "MATCH (c:Category3)<-[:Belong]-(s:SPU) RETURN c.name AS category, collect(s.name) AS products LIMIT 10",
                "answer_template": "以下是该分类下的商品：{results}"
            },
            {
                "patterns": ["品牌有什么", "品牌的产品", "品牌下有什么", "品牌下", "品牌有什么产品", "什么手机", "手机有哪些"],
                "cypher": "MATCH (t:BaseTrademark)<-[:Belong]-(s:SPU) RETURN t.name AS brand, collect(s.name) AS products LIMIT 10",
                "answer_template": "以下是该品牌下的商品：{results}"
            },
            {
                "patterns": ["商品列表", "所有商品", "全部商品", "有什么商品", "有哪些商品", "产品列表"],
                "cypher": "MATCH (s:SPU) RETURN s.name AS name LIMIT 20",
                "answer_template": "以下是平台的商品列表：{results}"
            },
            {
                "patterns": ["推荐", "热门", "精选", "有什么推荐", "买什么好"],
                "cypher": "MATCH (s:SPU) RETURN s.name AS name, s.price AS price LIMIT 10",
                "answer_template": "以下是为您推荐的商品：{results}"
            },
        ]

        question_embedding = self.embeddings.embed_query(question)
        question_vec = np.array(question_embedding)

        best_template = None
        best_score = 0.0

        for template in TEMPLATES:
            pattern_embeddings = self.embeddings.embed_documents(template["patterns"])
            pattern_matrix = np.array(pattern_embeddings)
            # Cosine similarity via dot product (embeddings are already L2-normalized)
            similarities = np.dot(pattern_matrix, question_vec)
            max_sim = float(np.max(similarities))
            if max_sim > best_score:
                best_score = max_sim
                best_template = template

        THRESHOLD = 0.7
        if best_score > THRESHOLD:
            print(f"   [OK] Template matched: \"{best_template['patterns'][0]}\" (score: {best_score:.3f})")
            return best_template

        print(f"   No template matched (best score: {best_score:.3f}, threshold: {THRESHOLD})")
        return None

    def _generate_final_answer(self, question: str, query_result: List[Dict[str, Any]]) -> str:
        """
        Convert Cypher query results into a natural language answer.
        """
        answer_prompt = PromptTemplate(
            input_variables=["question", "query_result"],
            template="""
    你是一个电商智能客服。根据用户问题，以及从知识图谱查询到的结果，生成一段简洁、准确的自然语言回答。
    如果查询结果为空，请告知用户未找到相关信息。

    用户问题: {question}
    查询结果: {query_result}

    回答:
    """
        ).format(question=question, query_result=query_result)
        response = self.llm.invoke(answer_prompt)
        return self.str_parser.invoke(response)

    def chat(self, question: str):
        # Check cache first
        cache_key = self._cache_key(question)
        cached = self._cache_get(cache_key)
        if cached is not None:
            print(f"   Cache hit: \"{question}\"")
            return cached

        print(f"   Cache miss: \"{question}\"")
        try:
            # 1. Try template matching first (fast path, no LLM for Cypher)
            matched = self._match_template(question)
            if matched:
                cypher_query = matched["cypher"]
                print(f"   Using template cypher: {cypher_query}")

                is_valid, error_msg = self._validate_cypher(cypher_query)
                if is_valid:
                    query_result = self._execute_cypher(cypher_query, {})
                    query_result, is_empty = self._validate_result(query_result, question)
                    if is_empty:
                        answer = "未找到相关信息。"
                    else:
                        answer = self._generate_final_answer(question, query_result)
                    self._cache_set(cache_key, answer)
                    return answer
                else:
                    print(f"   Template cypher validation failed: {error_msg}, falling through to LLM generation")

            # 2. LLM-based Cypher generation
            print("   Generating Cypher via LLM...")
            cypher = self._generate_cypher(question, self.graph.schema)
            cypher_query = cypher['cypher_query']
            entities_to_align = cypher['entities_to_align']

            # 3. Validate Cypher syntax with retry mechanism
            is_valid, error_msg = self._validate_cypher(cypher_query)
            retry_count = 0
            max_retries = 2

            while not is_valid and retry_count < max_retries:
                retry_count += 1
                print(f"   Cypher validation failed (retry {retry_count}/{max_retries}), sending error to LLM for correction...")

                fix_prompt = PromptTemplate(
                    input_variables=["question", "schema_info", "cypher_query", "error_msg"],
                    template="""
You generated an invalid Cypher query. Fix the syntax error based on the error message below.
Output ONLY a valid JSON object with the fixed cypher_query. NO markdown, NO explanation.

Original question: {question}
Schema: {schema_info}
Invalid Cypher: {cypher_query}
Error message: {error_msg}

Output format: {{"cypher_query": "<fixed cypher>", "entities_to_align": []}}
"""
                ).format(
                    question=question,
                    schema_info=self.graph.schema,
                    cypher_query=cypher_query,
                    error_msg=error_msg
                )

                fix_response = self.llm.invoke(fix_prompt)
                fix_text = fix_response.content.strip()
                print("=== LLM Fix Response ===")
                print(fix_text)

                fix_json = self._try_parse_cypher_response(fix_text, question)
                if fix_json:
                    cypher_query = fix_json.get("cypher_query", cypher_query)
                    entities_to_align = fix_json.get("entities_to_align", entities_to_align)
                    is_valid, error_msg = self._validate_cypher(cypher_query)
                else:
                    print("   Failed to parse LLM fix response, stopping retries")
                    break

            if not is_valid:
                print(f"   Cypher validation still failing after {retry_count} retries, using fallback templates")
                default_cypher = self._get_default_cypher(question)
                cypher_query = default_cypher["cypher_query"]
                entities_to_align = default_cypher["entities_to_align"]
                is_valid, error_msg = self._validate_cypher(cypher_query)
                if not is_valid:
                    print(f"   Even fallback template failed: {error_msg}")
                    return "抱歉，生成的查询语句有语法错误，请重新描述您的问题。"

            # 4. Execute and generate answer
            entities = self._entity_align(entities_to_align)
            params = {entity['param_name']: entity['entity'] for entity in entities}
            print(cypher_query, params)
            query_result = self._execute_cypher(cypher_query, params)

            query_result, is_empty = self._validate_result(query_result, question)
            if is_empty:
                answer = "未找到相关信息。"
            else:
                answer = self._generate_final_answer(question, query_result)
            self._cache_set(cache_key, answer)
            return answer
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"抱歉，处理您的问题时出现错误: {e}"

    def chat_stream(self, question: str):
        """
        Streaming version of chat() with full feature parity.
        1. Checks cache (fast path).
        2. Tries template matching first, then LLM Cypher generation.
        3. Validates Cypher with retry + fallback.
        4. Streams the final answer token-by-token as JSON SSE events.
        Each yielded string is either a JSON object ({{"token": "..."}}, {{"message": "..."}})
        or the sentinel "[DONE]".
        """
        try:
            # Check cache first
            cache_key = self._cache_key(question)
            cached = self._cache_get(cache_key)
            if cached is not None:
                print(f"   Stream cache hit: \"{question}\"")
                yield json.dumps({"message": cached}, ensure_ascii=False)
                yield "[DONE]"
                return

            print(f"   Stream cache miss: \"{question}\"")
            cypher_query = None
            entities_to_align = []

            # 1. Try template matching first (fast path, no LLM for Cypher)
            matched = self._match_template(question)
            if matched:
                cypher_query = matched["cypher"]
                print(f"   Stream using template cypher: {cypher_query}")
                is_valid, error_msg = self._validate_cypher(cypher_query)
                if not is_valid:
                    print(f"   Stream template cypher validation failed: {error_msg}, falling through to LLM")
                    cypher_query = None  # clear so we fall through

            # 2. LLM-based Cypher generation (if template didn't work)
            if cypher_query is None:
                print("   Stream generating Cypher via LLM...")
                cypher = self._generate_cypher(question, self.graph.schema)
                cypher_query = cypher['cypher_query']
                entities_to_align = cypher['entities_to_align']

            # 3. Validate Cypher syntax with retry mechanism
            is_valid, error_msg = self._validate_cypher(cypher_query)
            retry_count = 0
            max_retries = 2

            while not is_valid and retry_count < max_retries:
                retry_count += 1
                print(f"   Stream cypher validation failed (retry {retry_count}/{max_retries}), sending error to LLM for correction...")

                fix_prompt = PromptTemplate(
                    input_variables=["question", "schema_info", "cypher_query", "error_msg"],
                    template="""
You generated an invalid Cypher query. Fix the syntax error based on the error message below.
Output ONLY a valid JSON object with the fixed cypher_query. NO markdown, NO explanation.

Original question: {question}
Schema: {schema_info}
Invalid Cypher: {cypher_query}
Error message: {error_msg}

Output format: {{"cypher_query": "<fixed cypher>", "entities_to_align": []}}
"""
                ).format(
                    question=question,
                    schema_info=self.graph.schema,
                    cypher_query=cypher_query,
                    error_msg=error_msg
                )

                fix_response = self.llm.invoke(fix_prompt)
                fix_text = fix_response.content.strip()
                print("=== Stream LLM Fix Response ===")
                print(fix_text)

                fix_json = self._try_parse_cypher_response(fix_text, question)
                if fix_json:
                    cypher_query = fix_json.get("cypher_query", cypher_query)
                    entities_to_align = fix_json.get("entities_to_align", entities_to_align)
                    is_valid, error_msg = self._validate_cypher(cypher_query)
                else:
                    print("   Stream failed to parse LLM fix response, stopping retries")
                    break

            if not is_valid:
                print(f"   Stream cypher validation still failing after {retry_count} retries, using fallback templates")
                default_cypher = self._get_default_cypher(question)
                cypher_query = default_cypher["cypher_query"]
                entities_to_align = default_cypher["entities_to_align"]
                is_valid, error_msg = self._validate_cypher(cypher_query)
                if not is_valid:
                    print(f"   Stream even fallback template failed: {error_msg}")
                    yield json.dumps({"message": "抱歉，生成的查询语句有语法错误，请重新描述您的问题。"}, ensure_ascii=False)
                    yield "[DONE]"
                    return

            # 4. Execute and generate answer
            entities = self._entity_align(entities_to_align)
            params = {entity['param_name']: entity['entity'] for entity in entities}
            print(cypher_query, params)
            query_result = self._execute_cypher(cypher_query, params)

            query_result, is_empty = self._validate_result(query_result, question)
            if is_empty:
                answer = "未找到相关信息。"
                self._cache_set(cache_key, answer)
                yield json.dumps({"message": answer}, ensure_ascii=False)
                yield "[DONE]"
                return

            # Build the answer prompt (same template as _generate_final_answer)
            answer_prompt = PromptTemplate(
                input_variables=["question", "query_result"],
                template="""
    你是一个电商智能客服。根据用户问题，以及从知识图谱查询到的结果，生成一段简洁、准确的自然语言回答。
    如果查询结果为空，请告知用户未找到相关信息。

    用户问题: {question}
    查询结果: {query_result}

    回答:
    """
            ).format(question=question, query_result=query_result)

            # Stream tokens from LLM and collect full answer for caching
            full_answer_parts = []
            for chunk in self.llm.stream(answer_prompt):
                token = self.str_parser.invoke(chunk)
                if token:
                    full_answer_parts.append(token)
                    yield json.dumps({"token": token}, ensure_ascii=False)

            # Cache the full answer for future non-streaming lookups
            full_answer = "".join(full_answer_parts)
            if full_answer:
                self._cache_set(cache_key, full_answer)

            yield "[DONE]"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield json.dumps({"message": f"抱歉，处理您的问题时出现错误: {e}"}, ensure_ascii=False)
            yield "[DONE]"
