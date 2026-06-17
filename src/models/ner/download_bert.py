import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'   # 国内镜像加速

from huggingface_hub import snapshot_download

snapshot_download(
    repo_id='bert-base-chinese',
    local_dir='E:/agentProject/graph/models/bert-base-chinese',
    local_dir_use_symlinks=False,
    resume_download=True,
    ignore_patterns=['*.h5', '*.ot', '*.msgpack']  # 忽略不必要的大文件
)