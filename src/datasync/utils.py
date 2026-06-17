import pymysql
from neo4j import GraphDatabase
from pymysql.cursors import DictCursor
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from configuration import config

class MysqlReader:
    def __init__(self):
        self.connection = pymysql.connect(**config.MYSQL_CONFIG)
        self.cursor = self.connection.cursor(cursor=DictCursor)

    def read_data(self, sql):
        self.cursor.execute(sql)
        return self.cursor.fetchall()

    def close(self):
        self.cursor.close()
        self.connection.close()

class Neo4jWriter:
    def __init__(self):
        self.neo4j_driver = GraphDatabase.driver(uri=config.NEO4J_CONFIG['uri'],
                                                 auth=(config.NEO4J_CONFIG['user'], config.NEO4J_CONFIG['password']))

    def write_nodes(self, label, batch_data, batch_size=20):
        for i in range(0, len(batch_data), batch_size):
            batch = batch_data[i:i + batch_size]
            properties = {
                "batch": batch
            }
            cypher = f"""
            UNWIND $batch AS row
            MERGE (n:{label} {{id: row.id, name: row.name}})
            """
            print(cypher)
            # 使用execute_query方法执行查询
            self.neo4j_driver.execute_query(cypher, parameters_=properties)

    def write_relationships(self, start_node_label, end_node_label, relationships, relationship_type, batch_size=20):
        for i in range(0, len(relationships), batch_size):
            batch = relationships[i:i + batch_size]
            properties = {
                "batch": batch
            }
            cypher = f"""
            UNWIND $batch AS row
            MATCH (start:{start_node_label} {{id: row.start_id}}),
                   (end:{end_node_label} {{id: row.end_id}})
            MERGE (start)-[:{relationship_type}]->(end)
            """
            self.neo4j_driver.execute_query(cypher, parameters_=properties)