# graph/src/models/ner/train.py

import evaluate
from datasets import load_from_disk
from transformers import AutoModelForTokenClassification, Trainer, TrainingArguments, EvalPrediction, \
    DataCollatorForTokenClassification, AutoTokenizer

from configuration import config

import os
os.environ["HF_DATASETS_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"


# label映射关系
id2label = {id: label for id, label in enumerate(config.LABELS)}
label2id = {label: id for id, label in enumerate(config.LABELS)}

# 模型
model = AutoModelForTokenClassification.from_pretrained(
    config.NER_MODEL,
    num_labels=len(id2label),
    id2label=id2label,
    label2id=label2id,
    ignore_mismatched_sizes=True,  # 就是添加这一行，避免架构不完全匹配导致的警告错误
)

# 数据集
train_dataset = load_from_disk(config.DATA_DIR / 'ner' / 'processed' / 'train')
valid_dataset = load_from_disk(config.DATA_DIR / 'ner' / 'processed' / 'valid')

# ================== 调试 evaluate.load ==================
try:
    # 方法1：检查是否能正常从网络加载
    print("尝试从网络加载 'seqeval'...")
    seqeval = evaluate.load('./metrics/seqeval.py')
    print("从网络加载成功！")
except Exception as e:
    print(f"从网络加载失败: {e}")
    try:
        # 方法2：如果网络不行，尝试离线加载
        # 你也可以从 Hugging Face 官网手动下载指标后，指向本地路径
        # 本地加载示例: evaluate.load('./metrics/seqeval')
        print("尝试离线加载 'seqeval'...")
        seqeval = evaluate.load('./metrics/seqeval')
        print("离线加载成功！")
    except Exception as e:
        print(f"离线加载也失败了: {e}")
        print("请检查网络环境或本地文件。")
        # 你原来的评估函数 compute_metrics 可以先注释掉，之后再修复
        # seqeval = evaluate.load('seqeval')
# ==================================================

# 评价函数
def compute_metrics(prediction: EvalPrediction) -> dict:
    logits = prediction.predictions  # [batch_size, seq_len, num_labels]
    preds = logits.argmax(axis=-1)  # [batch_size, seq_len]
    labels = prediction.label_ids  # [batch_size, seq_len]

    # 转换为标签名称
    true_predictions = [
        [id2label[p] for (p, l) in zip(pred, label) if l != -100]
        for pred, label in zip(preds, labels)
    ]
    true_labels = [
        [id2label[l] for (p, l) in zip(pred, label) if l != -100]
        for pred, label in zip(preds, labels)
    ]

    return seqeval.compute(predictions=true_predictions, references=true_labels)

# 分词器
tokenizer = AutoTokenizer.from_pretrained(config.NER_MODEL)
# 数据整理器
data_collator = DataCollatorForTokenClassification(tokenizer=tokenizer, padding=True, return_tensors='pt')

# 训练参数
training_args = TrainingArguments(output_dir=str(config.CHECKPOINT_DIR / 'ner'),  # 模型保存目录
                                  logging_dir=str(config.LOG_DIR / 'ner'),  # 日志目录
                                  per_device_train_batch_size=2,  # 训练批次大小
                                  logging_steps=20,  # 训练日志间隔
                                  num_train_epochs=10,  # 训练轮数
                                  save_steps=20,  # 模型保存间隔
                                  save_total_limit=3,  # 最多保存模型数量
                                  eval_strategy='steps',  # 评估策略
                                  eval_steps=20,  # 评估间隔
                                  load_best_model_at_end=True,  # 训练结束加载最优模型
                                  metric_for_best_model='eval_overall_f1',  # 最优模型评估指标
                                  greater_is_better=True)
trainer = Trainer(model=model,
                  args=training_args,
                  data_collator=data_collator,
                  train_dataset=train_dataset,
                  eval_dataset=valid_dataset,
                  compute_metrics=compute_metrics)

# 训练
trainer.train()
# 保存模型
trainer.save_model(config.CHECKPOINT_DIR / 'ner' / 'best_model')