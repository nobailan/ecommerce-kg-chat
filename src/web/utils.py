# graph/src/web/utils.py
from configuration import config
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_neo4j import Neo4jGraph

def create_full_text_index(graph, index, label, property):
    cypher = f"""
    CREATE FULLTEXT INDEX {index}
    FOR (n:{label})
    ON EACH [n.{property}]
    """
    graph.query(cypher, {'index': index, 'label': label, 'property': property})

def create_embedding_index(
        graph,
        index,
        label,
        property,
        embedding_property,
        embedding_model,
        embedding_dim,
        batch_size=100
):
    # 1. 查询需要生成 embedding 的节点
    query = f"""
    MATCH (n:{label})
    WHERE n.{property} IS NOT NULL
    RETURN id(n) as node_id, n.{property} as text
    """
    nodes = graph.query(query)

    # 2. 批量生成 embedding 并更新节点
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i:i + batch_size]
        texts = [record['text'] for record in batch]
        embeddings = embedding_model.embed_documents(texts)  # HuggingFace 批量生成 embedding

        for record, emb in zip(batch, embeddings):
            update_query = f"""
            MATCH (n)
            WHERE id(n) = $node_id
            SET n.{embedding_property} = $embedding
            """
            graph.query(update_query, {'node_id': record['node_id'], 'embedding': emb})
    print(f"已为 {len(nodes)} 个节点生成 embedding。")

    # 3. 创建向量索引
    cypher_index = f"""
    CREATE VECTOR INDEX {index}
    FOR (n:{label})
    ON n.{embedding_property}
    OPTIONS {{indexConfig:{{
        `vector.dimensions`: {embedding_dim},
        `vector.similarity_function`: 'cosine'
        }}
    }}
    """
    graph.query(cypher_index)
    print(f"向量索引 '{index}' 已创建。")

def drop_all_indexes(graph):
    indexes = graph.query("show indexes where type in ['VECTOR','FULLTEXT']")
    indexes = [index['name'] for index in indexes]
    for index in indexes:
        graph.query(f"drop index {index}")

if __name__ == '__main__':
    # 创建 Neo4j 连接，使用配置文件中的参数
    graph = Neo4jGraph(
        url=config.NEO4J_CONFIG["uri"],
        username=config.NEO4J_CONFIG["user"],
        password=config.NEO4J_CONFIG["password"],
        database=config.NEO4J_CONFIG["database"],
    )

    create_full_text_index(graph, "spu_full_text_index", "SPU", "name")
    create_full_text_index(graph, "trademark_full_text_index", "BaseTrademark", "name")
    create_full_text_index(graph, "category3_full_text_index", "Category3", "name")
    create_full_text_index(graph, "category2_full_text_index", "Category2", "name")
    create_full_text_index(graph, "category1_full_text_index", "Category1", "name")

    model_name = "BAAI/bge-small-zh-v1.5"
    model_kwargs = {"device": "cpu"}
    encode_kwargs = {"normalize_embeddings": True}
    embedding_model = HuggingFaceEmbeddings(
        model_name=model_name, model_kwargs=model_kwargs, encode_kwargs=encode_kwargs
    )

    create_embedding_index(graph, "spu_embedding_index", "SPU", "name", "embedding", embedding_model, 512)
    create_embedding_index(graph, "trademark_embedding_index", "BaseTrademark", "name", "embedding", embedding_model,
                           512)
    create_embedding_index(graph, "category3_embedding_index", "Category3", "name", "embedding", embedding_model, 512)
    create_embedding_index(graph, "category2_embedding_index", "Category2", "name", "embedding", embedding_model, 512)
    create_embedding_index(graph, "category1_embedding_index", "Category1", "name", "embedding", embedding_model, 512)