# configuration.py
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件（如果存在），不会覆盖已有的环境变量
load_dotenv()


class Config:
    # 项目根目录
    BASE_DIR = Path(__file__).parent

    # 数据目录
    DATA_DIR = BASE_DIR / "data"

    # 模型检查点目录
    CHECKPOINT_DIR = BASE_DIR / "checkpoints"

    # 日志目录
    LOG_DIR = BASE_DIR / "logs"

    # Web静态文件目录
    WEB_STATIC_DIR = BASE_DIR / "src" / "web" / "static"

    # MySQL 配置（从环境变量读取，硬编码值仅为本地开发兜底）
    MYSQL_CONFIG = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", "123456"),
        "database": os.getenv("MYSQL_DATABASE", "gmall"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
    }

    # NER 预训练模型名称
    NER_MODEL = "E:/agentProject/graph/models/bert-base-chinese"

    # 嵌入模型路径（请根据你的实际路径修改）
    EMBEDDING_MODEL_PATH = "E:/agentProject/graph/models/bge-small-zh-v1.5"

    # 标签列表（BIO）
    LABELS = ["B", "I", "O"]

    # DeepSeek API Key（从环境变量读取）
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

    # Neo4j 配置（从环境变量读取，默认值指向本地社区版）
    NEO4J_CONFIG = {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "12345678"),
        "database": os.getenv("NEO4J_DATABASE", "neo4j"),
    }


# 创建 config 实例，供其他模块导入
config = Config()
