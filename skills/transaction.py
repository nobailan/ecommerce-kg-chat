"""
下单意图识别与模拟执行 Agent（TransactionAgent）。

流程：
  1. 识别下单意图 → 确认商品 → 生成订单卡片 → 等待确认 → 模拟下单
"""

import re
import json
import time
import random
from datetime import datetime
from typing import Optional, Dict, Any, List


class TransactionAgent:
    """
    下单执行 Agent：
    - 识别“帮我下单”“我要买这个”等意图
    - 从 Neo4j 检索商品信息生成订单卡片
    - 处理用户确认 → 模拟下单闭环
    """

    # 下单意图触发词
    ORDER_INTENT_KEYWORDS = [
        "帮我下单", "我要买", "就它了", "就这个", "下单",
        "帮我买", "买它", "帮我订", "我想买", "我要下单",
        "加入购物车", "立即购买", "现在买",
    ]

    # 确认触发词
    CONFIRM_KEYWORDS = ["确认", "确定", "好的", "可以", "行", "下单吧", "买", "ok", "OK", "是的", "没错"]

    def __init__(self, graph=None):
        self.graph = graph
        self.pending_order: Optional[Dict[str, Any]] = None

    def detect_intent(self, message: str) -> bool:
        """检测是否包含下单意图。"""
        for kw in self.ORDER_INTENT_KEYWORDS:
            if kw in message:
                return True
        return False

    def is_confirmation(self, message: str) -> bool:
        """检测是否为确认操作。"""
        msg = message.strip().lower()
        for kw in self.CONFIRM_KEYWORDS:
            if kw == msg or kw in msg:
                return True
        return False

    def search_product(self, product_name: str) -> Optional[Dict[str, Any]]:
        """在 Neo4j 中搜索商品信息。"""
        if not self.graph:
            return None

        try:
            # 搜索 SPU
            results = self.graph.query(
                "MATCH (s:SPU) WHERE s.name CONTAINS $name "
                "OPTIONAL MATCH (s)-[:Belong]->(t:BaseTrademark) "
                "RETURN s.name AS product, s.price AS price, t.name AS brand "
                "LIMIT 1",
                params={"name": product_name},
            )
            if results:
                result = results[0]
                return {
                    "product": result.get("product", product_name),
                    "price": result.get("price", "价格待查询"),
                    "brand": result.get("brand", "未知品牌"),
                }
        except Exception as e:
            print(f"   [TransactionAgent] 商品搜索失败: {e}")
        return None

    def extract_product_name(self, message: str) -> Optional[str]:
        """从下单消息中提取商品名称。"""
        # 移除下单意图关键词
        cleaned = message
        for kw in self.ORDER_INTENT_KEYWORDS:
            cleaned = cleaned.replace(kw, "")

        # 移除常见修饰词
        for word in ["一个", "一台", "一件", "一部", "这个", "那个", "给我", "来"]:
            cleaned = cleaned.replace(word, "")

        cleaned = cleaned.strip()
        return cleaned if len(cleaned) >= 1 else None

    def generate_order_card(self, product_info: Dict[str, Any], quantity: int = 1) -> str:
        """生成订单确认卡片（Markdown 格式）。"""
        order_id = f"ORD{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(10, 99)}"
        price = product_info.get("price", "待确认")
        if isinstance(price, (int, float)):
            total = price * quantity
            price_str = f"¥{price:.2f}"
            total_str = f"¥{total:.2f}"
        else:
            price_str = str(price)
            total_str = "待确认"

        self.pending_order = {
            "order_id": order_id,
            "product": product_info.get("product"),
            "brand": product_info.get("brand"),
            "price": price_str,
            "quantity": quantity,
            "total": total_str,
            "created_at": datetime.now().isoformat(),
        }

        brand_line = f"\n| **品牌** | {product_info.get('brand', '未知')} |" if product_info.get("brand") else ""

        return f"""---

## 📋 订单确认单

| 项目 | 详情 |
|------|------|
| **订单编号** | {order_id} |
| **商品名称** | {product_info.get('product', '未知商品')} |{brand_line}
| **单价** | {price_str} |
| **数量** | {quantity} |
| **合计** | **{total_str}** |

---

> ⚠️ 以上为模拟订单，确认后不会产生实际扣款。
>
> 请回复 **「确认」** 下单，回复其他内容取消。

---
"""

    def simulate_order(self) -> Dict[str, Any]:
        """模拟下单，返回订单结果。"""
        if not self.pending_order:
            return {"success": False, "message": "没有待处理的订单"}

        order = self.pending_order
        # 模拟下单延迟
        time.sleep(0.5)

        result = {
            "success": True,
            "order_id": order["order_id"],
            "message": (
                f"✅ 下单成功！\n\n"
                f"订单编号：**{order['order_id']}**\n"
                f"商品：{order['product']}\n"
                f"金额：{order['total']}\n"
                f"下单时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"> 📝 这是模拟下单，实际环境中会调用真实支付接口。\n"
                f"> 如需查询订单状态，请查看「我的订单」。"
            ),
            "order": order,
        }

        # 清空待处理订单
        self.pending_order = None
        return result

    def process(self, message: str) -> Optional[str]:
        """
        处理下单相关消息。返回：
        - 订单卡片（首次识别下单意图）
        - 下单结果（用户确认后）
        - None（不处理，交由后续流程）
        """
        # 1. 如果用户说"确认"且有 pending order，执行下单
        if self.pending_order and self.is_confirmation(message):
            result = self.simulate_order()
            return result["message"]

        # 2. 检测下单意图
        if not self.detect_intent(message):
            return None

        # 3. 提取商品名称
        product_name = self.extract_product_name(message)
        if not product_name:
            return "请问您想购买哪款商品呢？告诉我商品名称我帮您下单～"

        # 4. 搜索商品
        product_info = self.search_product(product_name)
        if product_info and product_info.get("price"):
            return self.generate_order_card(product_info)
        else:
            # 商品未在 KG 中找到，返回引导信息
            self.pending_order = {
                "order_id": f"ORD{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "product": product_name,
                "price": "待确认",
                "quantity": 1,
                "total": "待确认",
                "brand": "",
                "created_at": datetime.now().isoformat(),
            }
            return f"""---

## 📋 订单确认单（模拟）

| 项目 | 详情 |
|------|------|
| **商品名称** | {product_name} |
| **单价** | 待确认（模拟数据） |
| **数量** | 1 |

---

> ⚠️ 该商品暂未在数据库中查到精确价格，以上为模拟订单。
>
> 请回复 **「确认」** 下单，回复其他内容取消。

---
"""
