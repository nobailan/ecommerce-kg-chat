# graph/src/models/ner/process.py

from datasets import load_dataset
from transformers import AutoTokenizer

from configuration import config

def process():
    # 加载数据
    dataset = load_dataset("json", data_files=str(config.DATA_DIR / 'ner' / 'raw' / 'data.json'))['train']
    dataset = dataset.remove_columns(["id", "annotator", "annotation_id", "created_at", "updated_at", "lead_time"])

    dataset_dict = dataset.train_test_split(train_size=0.8)
    dataset_dict['test'], dataset_dict['valid'] = dataset_dict['test'].train_test_split(test_size=0.5).values()

    # 分词器
    tokenizer = AutoTokenizer.from_pretrained("E:/agentProject/graph/models/bert-base-chinese")

    # label映射关系
    id2label = ['B', 'I', 'O']
    label2id = {label: id for id, label in enumerate(id2label)}

    # 数据转换
    def map_func(example):
        tokens = list(example['text'])
        inputs = tokenizer(tokens, truncation=True, is_split_into_words=True)
        labels = [label2id['O']] * len(tokens)
        for entity in example['label']:
            start = entity['start']
            end = entity['end']
            labels[start:end] = [label2id['B']] + [label2id['I']] * (end - start - 1)
        labels = [-100] + labels + [-100]
        inputs['labels'] = labels
        return inputs

    dataset_dict = dataset_dict.map(map_func, batched=False, remove_columns=['text', 'label'])

    dataset_dict.save_to_disk(config.DATA_DIR / 'ner' / 'processed')

if __name__ == '__main__':
    process()