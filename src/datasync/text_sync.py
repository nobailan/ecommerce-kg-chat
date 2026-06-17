# graph/src/datasync/text_sync.py

import sys
from pathlib import Path
# 将项目根目录添加到 Python 路径
sys.path.append(str(Path(__file__).parent.parent.parent))


import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

from configuration import config
from utils import Neo4jWriter, MysqlReader
from src.models.ner.predict import Predictor

class TextSynchronizer:
    def __init__(self):
        self.neo4j_writer = Neo4jWriter()
        self.mysql_reader = MysqlReader()
        self.extractor = self._init_extractor()

    def _init_extractor(self):
        model = AutoModelForTokenClassification.from_pretrained(str(config.CHECKPOINT_DIR / 'ner' / 'best_model'))
        tokenizer = AutoTokenizer.from_pretrained(str(config.CHECKPOINT_DIR / 'ner' / 'best_model'))
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        return Predictor(model, tokenizer, device)

    def sync_spu_desc(self):
        sql = """
              select id, description
              from spu_info
              """
        spu_desc = self.mysql_reader.read_data(sql)
        spu_ids = [spu['id'] for spu in spu_desc]
        descs = [spu['description'] for spu in spu_desc]
        spu_entities = self.extractor.extract(descs)

        nodes = []
        relationships = []
        for id, entities in zip(spu_ids, spu_entities):
            for index, entity in enumerate(entities):
                node = {
                    "id": '-'.join([str(id), str(index)]),
                    "name": entity
                }
                nodes.append(node)

                relationship = {
                    "start_id": id,
                    "end_id": '-'.join([str(id), str(index)])
                }
                relationships.append(relationship)
        self.neo4j_writer.write_nodes('Tag', nodes)
        self.neo4j_writer.write_relationships('SPU', 'Tag', relationships, 'Have')

if __name__ == '__main__':
    synchronizer = TextSynchronizer()
    synchronizer.sync_spu_desc()