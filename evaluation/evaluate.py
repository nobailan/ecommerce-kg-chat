"""
评估脚本 — 对电商智能客服进行批量测试与量化评估。

用法：
  python evaluation/evaluate.py                  # 交互模式（逐条人工标注）
  python evaluation/evaluate.py --auto           # 自动模式（仅记录指标，不标注）
  python evaluation/evaluate.py --mode compare   # 三模式对比评估

依赖：服务需先启动（python src/web/app.py）
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
TEST_SET_PATH = Path(__file__).parent / "test_set.json"
RESULTS_DIR = Path(__file__).parent / "results"
TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "60"))

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def load_test_set(path: Path) -> list:
    """加载测试集。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("test_cases", [])


def send_question(question: str, use_stream: bool = False, mode: str = "full") -> dict:
    """
    发送问题到 /api/chat（非流式），返回 {answer, elapsed, tokens_approx}。
    mode: full | no_script | llm_only
    """
    endpoint = f"{BASE_URL}/api/chat?mode={mode}&nocache=true"
    t0 = time.time()
    try:
        resp = requests.post(
            endpoint,
            json={"message": question},
            timeout=TIMEOUT,
        )
        elapsed = time.time() - t0
        if resp.status_code == 200:
            data = resp.json()
            answer = data.get("message", "")
            # 估算 token 消耗（中文约 1.5 字符/token）
            tokens_approx = len(answer) // 1.5 + len(question) // 1.5
            return {
                "success": True,
                "answer": answer,
                "elapsed": elapsed,
                "tokens_approx": int(tokens_approx),
                "error": "",
            }
        else:
            return {
                "success": False,
                "answer": "",
                "elapsed": elapsed,
                "tokens_approx": 0,
                "error": f"HTTP {resp.status_code}",
            }
    except requests.exceptions.ConnectionError:
        elapsed = time.time() - t0
        return {
            "success": False,
            "answer": "",
            "elapsed": elapsed,
            "tokens_approx": 0,
            "error": "连接失败 — 服务是否启动？",
        }
    except Exception as e:
        elapsed = time.time() - t0
        return {
            "success": False,
            "answer": "",
            "elapsed": elapsed,
            "tokens_approx": 0,
            "error": str(e),
        }


def print_separator(char: str = "─", width: int = 70):
    print(char * width)


def human_annotation(test_case: dict, result: dict, index: int, total: int) -> str:
    """
    交互式人工标注，返回 "correct" / "fallback" / "incorrect"。
    correct: 真正回答了问题，给出了具体信息
    fallback: 没答出具体内容，但给了合理的引导/兜底
    incorrect: 完全答错或无用回复
    """
    print_separator()
    print(f"[{index}/{total}] 类别: {test_case['category']}")
    print(f"问题: {test_case['question']}")
    print(f"期望: {test_case['expected_answer']}")
    print_separator("-")
    print(f"回答: {result['answer'][:500]}")
    print(f"耗时: {result['elapsed']:.2f}s | 估算 Token: {result['tokens_approx']}")
    if result["error"]:
        print(f"错误: {result['error']}")
    print_separator("-")

    while True:
        choice = input("标注 [c=正确 / f=兜底 / i=错误 / s=跳过]: ").strip().lower()
        if choice in ("c", "correct"):
            return "correct"
        elif choice in ("f", "fallback"):
            return "fallback"
        elif choice in ("i", "incorrect"):
            return "incorrect"
        elif choice in ("s", "skip"):
            return "skipped"
        else:
            print("  请输入 c / p / i / s")


def is_fallback_answer(answer: str) -> bool:
    """
    检测回答是否为兜底/引导性回复。
    关键区分：长回答（>200字）通常是详细话术，即使含"建议"等词也不算兜底；
    短回答含兜底关键词的才是真正的"找不到数据"。
    """
    # 长回复 = 详细话术脚本，不是兜底
    if len(answer) > 200:
        return False

    # 短回复才检查兜底关键词
    fallback_markers = [
        "数据库", "暂未找到", "换个关键词", "换个词",
        "暂无", "未找到相关信息", "无法直接查询", "暂时无法",
        "知识库中暂时没有", "未能找到",
    ]
    return any(kw in answer for kw in fallback_markers)


def is_ai_exposed(answer: str) -> bool:
    """
    检测回复是否暴露了AI身份。
    真人客服不会说'请找客服''转人工'这种话。
    """
    exposure_markers = [
        "联系客服", "找客服", "转人工", "联系在线客服",
        "联系人工客服", "咨询客服", "客服热线", "投诉专线",
        "投诉邮箱", "点击页面右下角",
    ]
    return any(kw in answer for kw in exposure_markers)


def auto_label(test_case: dict, result: dict) -> str:
    """
    自动标注（严格模式）：
    - correct: 真正给出了具体信息（品牌名、价格、产品名等）
    - fallback: 没给出具体信息，但给了合理的引导兜底
    - incorrect: 完全错误的回复
    """
    criteria = test_case.get("evaluation_criteria", "")
    answer = result.get("answer", "")

    if not result["success"]:
        return "incorrect"

    # 最高优先级：检测AI身份暴露（"请联系客服"等）
    if is_ai_exposed(answer):
        return "incorrect"

    # 判断是否为兜底回复
    fallback = is_fallback_answer(answer)

    if criteria == "graceful_fallback":
        return "fallback" if fallback else ("correct" if len(answer) > 20 else "incorrect")

    if criteria == "after_sales_policy":
        if fallback:
            return "fallback"
        # 真正回答售后问题的话，应该有具体信息（流程/时间/条件等）
        has_detail = any(kw in answer for kw in ["天", "工作日", "流程", "步骤", "申请", "审核", "退款", "寄回", "换新"])
        return "correct" if has_detail else "fallback"

    if criteria == "contains_brand_list":
        if fallback:
            return "fallback"
        keywords = ["品牌", "苹果", "华为", "三星", "小米", "OPPO", "VIVO", "荣耀", "品牌包括", "主要有"]
        return "correct" if any(k in answer for k in keywords) else "incorrect"

    if criteria == "contains_product_list":
        if fallback:
            return "fallback"
        return "correct" if len(answer) > 30 else "incorrect"

    if criteria == "contains_category_list":
        if fallback:
            return "fallback"
        keywords = ["分类", "图书", "手机", "电脑", "家电", "家居", "一级", "二级"]
        return "correct" if any(k in answer for k in keywords) else "incorrect"

    if criteria == "contains_count":
        if fallback:
            return "fallback"
        import re
        return "correct" if re.search(r'\d+', answer) else "incorrect"

    if criteria == "contains_price":
        if fallback:
            return "fallback"
        import re
        return "correct" if (re.search(r'\d+', answer) or "元" in answer) else "incorrect"

    if criteria == "contains_sku_info":
        if fallback:
            return "fallback"
        return "correct" if len(answer) > 30 else "incorrect"

    if criteria == "contains_color_info":
        if fallback:
            return "fallback"
        keywords = ["颜色", "色", "黑", "白", "红", "蓝", "金"]
        return "correct" if any(k in answer for k in keywords) else "incorrect"

    if criteria == "contains_size_info":
        if fallback:
            return "fallback"
        keywords = ["尺码", "尺寸", "大小", "英寸", "cm", "mm"]
        return "correct" if any(k in answer for k in keywords) else "incorrect"

    if criteria == "order_card":
        if fallback:
            return "fallback"
        keywords = ["订单", "确认", "¥", "价格", "购买", "商品"]
        return "correct" if any(k in answer for k in keywords) else "incorrect"

    if criteria == "order_intent":
        if fallback:
            return "fallback"
        return "correct" if len(answer) > 15 else "incorrect"

    if criteria == "comparison_response":
        if fallback:
            return "fallback"
        return "correct" if len(answer) > 20 else "incorrect"

    if criteria == "clarification_or_helpful":
        if fallback:
            return "fallback"
        return "correct" if len(answer) > 15 else "incorrect"

    if criteria == "category_hierarchy":
        if fallback:
            return "fallback"
        keywords = ["分类", "属于", "层级", "一级", "二级", "三级"]
        return "correct" if any(k in answer for k in keywords) else "incorrect"

    if criteria == "correct_brand":
        if fallback:
            return "fallback"
        keywords = ["品牌", "属于", "Apple", "苹果"]
        return "correct" if any(k in answer for k in keywords) else "incorrect"

    # 默认
    if fallback:
        return "fallback"
    return "correct" if len(answer) > 15 else "incorrect"


def run_evaluation(test_cases: list, interactive: bool = True, mode: str = "full") -> dict:
    """
    主评估流程：逐条测试 + 标注 + 统计。
    mode: full | no_script | llm_only
    """
    results = []
    correct = fallback = incorrect = skipped = 0
    total_elapsed = 0.0
    total_tokens = 0
    success_count = 0

    total = len(test_cases)
    for i, tc in enumerate(test_cases, 1):
        print(f"\n⏳ [{i}/{total}] [{mode}] 测试: {tc['question'][:50]}...")

        result = send_question(tc["question"], use_stream=False, mode=mode)
        result["id"] = tc["id"]
        result["category"] = tc["category"]
        result["question"] = tc["question"]
        result["expected_answer"] = tc["expected_answer"]

        if interactive:
            label = human_annotation(tc, result, i, total)
        else:
            label = auto_label(tc, result)

        result["label"] = label

        if label == "correct":
            correct += 1
        elif label == "fallback" or label == "partial":
            fallback += 1
        elif label == "incorrect":
            incorrect += 1
        elif label == "skipped":
            skipped += 1

        if result["success"]:
            success_count += 1
            total_elapsed += result["elapsed"]
            total_tokens += result["tokens_approx"]

        results.append(result)

    n = len(results) - skipped
    # 严格正确率：排除兜底回复，只看真正给出了具体信息的
    strict_acc = round(correct / n, 4) if n > 0 else 0
    # 可用率：正确 + 兜底（给用户的回复都是合理可用的）
    usable_acc = round((correct + fallback) / n, 4) if n > 0 else 0
    return {
        "timestamp": datetime.now().isoformat(),
        "total": len(results),
        "tested": n,
        "skipped": skipped,
        "correct": correct,
        "fallback": fallback,
        "incorrect": incorrect,
        "strict_accuracy": strict_acc,
        "usable_accuracy": usable_acc,
        "avg_elapsed": round(total_elapsed / success_count, 2) if success_count > 0 else 0,
        "avg_tokens": round(total_tokens / success_count, 0) if success_count > 0 else 0,
        "success_rate": round(success_count / len(results), 4),
        "results": results,
    }


def print_report(stats: dict):
    """打印评估报告。"""
    correct = stats["correct"]
    fallback = stats["fallback"]
    incorrect = stats["incorrect"]
    total = stats["tested"]

    print("\n" + "=" * 60)
    print("📊 评估报告")
    print("=" * 60)
    print(f"  测试时间: {stats['timestamp']}")
    print(f"  总测试数: {stats['total']} | 跳过: {stats['skipped']}")
    print(f"  API 成功率: {stats['success_rate']:.1%}")
    print("-" * 60)
    print(f"  ✅ 正确 (给出具体信息):  {correct:3d}  ({stats['strict_accuracy']:.1%})")
    print(f"  ⏸️  兜底 (合理引导回复):  {fallback:3d}  ({fallback/total:.1%})" if total > 0 else "")
    print(f"  ❌ 错误 (答非所问):      {incorrect:3d}")
    print(f"  📊 可用率 (正确+兜底):    {stats['usable_accuracy']:.1%}")
    print("-" * 60)
    print(f"  平均响应时间: {stats['avg_elapsed']}s")
    print(f"  平均 Token:   {stats['avg_tokens']}")
    print("=" * 60)

    # 按分类统计
    print("\n📂 按类别统计（严格正确率）:")
    by_cat = {}
    for r in stats["results"]:
        cat = r["category"]
        if cat not in by_cat:
            by_cat[cat] = {"total": 0, "correct": 0, "fallback": 0}
        by_cat[cat]["total"] += 1
        if r["label"] == "correct":
            by_cat[cat]["correct"] += 1
        elif r["label"] in ("fallback", "partial"):
            by_cat[cat]["fallback"] += 1

    cat_names = {
        "product_inquiry": "商品咨询",
        "order_query": "订单查询",
        "recommendation": "推荐导购",
        "after_sales": "售后问题",
    }
    for cat, data in by_cat.items():
        acc = data["correct"] / data["total"] if data["total"] > 0 else 0
        total_acc = (data["correct"] + data["fallback"]) / data["total"] if data["total"] > 0 else 0
        bar_strict = "█" * int(acc * 20)
        bar_fb = "▓" * int((total_acc - acc) * 20)
        bar_empty = "░" * (20 - len(bar_strict) - len(bar_fb))
        print(f"  {cat_names.get(cat, cat):8s}  {bar_strict}{bar_fb}{bar_empty}  "
              f"严格{acc:.0%}  可用{total_acc:.0%}  ({data['correct']}+{data['fallback']}/{data['total']})")


def run_baseline(interactive: bool = False):
    """运行基线评估。"""
    print("🚀 开始基线评估...")
    print(f"   API: {BASE_URL}")
    print(f"   测试集: {TEST_SET_PATH}")

    test_cases = load_test_set(TEST_SET_PATH)
    if not test_cases:
        print("❌ 测试集为空！")
        return

    print(f"   共 {len(test_cases)} 条测试用例\n")

    stats = run_evaluation(test_cases, interactive=interactive)
    print_report(stats)

    # 保存结果
    RESULTS_DIR.mkdir(exist_ok=True)
    result_path = RESULTS_DIR / f"baseline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"\n📁 详细结果已保存到: {result_path}")

    # 生成 baseline.md
    baseline_md = RESULTS_DIR / "baseline.md"
    with open(baseline_md, "w", encoding="utf-8") as f:
        f.write(f"# 基线评估报告\n\n")
        f.write(f"> 评估时间：{stats['timestamp']}\n")
        f.write(f"> 测试集：{stats['total']} 条\n\n")
        f.write(f"## 总体指标\n\n")
        f.write(f"| 指标 | 值 |\n")
        f.write(f"|------|-----|\n")
        f.write(f"| 准确率 | {stats['accuracy']:.1%} |\n")
        f.write(f"| 部分正确率 | {stats['partial_rate']:.1%} |\n")
        f.write(f"| 平均响应时间 | {stats['avg_elapsed']}s |\n")
        f.write(f"| 平均 Token 消耗 | {stats['avg_tokens']} |\n")
        f.write(f"| API 成功率 | {stats['success_rate']:.1%} |\n")
    print(f"📁 基线报告已保存到: {baseline_md}")

    return stats


def run_comparison():
    """三模式对比评估。"""
    print("🚀 三模式对比评估\n")

    test_cases = load_test_set(TEST_SET_PATH)
    if not test_cases:
        print("❌ 测试集为空！")
        return

    modes = {
        "llm_only": "纯 LLM 生成（无检索/无模板/无话术）",
        "no_script": "Naive RAG（模板匹配 + LLM，无话术路由）",
        "full": "完整方案（话术路由 + 模板 + Neo4j + 缓存）",
    }

    all_stats = {}
    for mode_key, mode_desc in modes.items():
        print(f"\n{'='*60}")
        print(f"📊 {mode_desc}")
        print(f"{'='*60}")
        stats = run_evaluation(test_cases, interactive=False, mode=mode_key)
        print_report(stats)
        all_stats[mode_key] = stats

    # 保存对比报告
    RESULTS_DIR.mkdir(exist_ok=True)
    report_path = RESULTS_DIR / "comparison_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 三模式对比评估报告\n\n")
        f.write(f"> 评估时间：{datetime.now().isoformat()}\n")
        f.write(f"> 测试集：{len(test_cases)} 条\n\n")
        f.write(f"## 总体对比\n\n")
        f.write(f"| 模式 | 准确率 | 平均响应时间 | 平均 Token |\n")
        f.write(f"|------|--------|-------------|----------|\n")

        mode_names = {
            "llm_only": "纯 LLM",
            "no_script": "Naive RAG",
            "full": "完整方案",
        }
        f.write(f"| 模式 | 严格正确率 | 可用率 | 响应时间 | Token |\n")
        f.write(f"|------|----------|--------|---------|-------|\n")
        for mode_key, stats in all_stats.items():
            f.write(f"| {mode_names[mode_key]} | {stats['strict_accuracy']:.1%} | "
                    f"{stats['usable_accuracy']:.1%} | {stats['avg_elapsed']}s | {stats['avg_tokens']} |\n")

        llm_strict = all_stats["llm_only"]["strict_accuracy"]
        full_strict = all_stats["full"]["strict_accuracy"]
        improvement = (full_strict - llm_strict) / llm_strict * 100 if llm_strict > 0 else 0

        f.write(f"\n## 分析\n\n")
        f.write(f"- 完整方案 vs 纯 LLM 严格正确率提升：**{improvement:.0f}%**\n")
        f.write(f"- 完整方案严格正确率：{full_strict:.1%}\n")
        f.write(f"- 完整方案响应时间：{all_stats['full']['avg_elapsed']}s（纯 LLM 的 {all_stats['full']['avg_elapsed']/all_stats['llm_only']['avg_elapsed']:.0%}）\n")

    print(f"\n📁 对比报告已保存到: {report_path}")
    print(f"\n{'='*60}")
    print(f"📊 三模式对比总结")
    print(f"{'='*60}")
    for mk in ["llm_only", "no_script", "full"]:
        s = all_stats[mk]
        name = {"llm_only": "纯 LLM", "no_script": "Naive RAG", "full": "完整方案"}[mk]
        print(f"  {name:10s}  严格正确率 {s['strict_accuracy']:.1%} | "
              f"可用率 {s['usable_accuracy']:.1%} | "
              f"响应 {s['avg_elapsed']}s | Token {s['avg_tokens']}")
    print(f"\n  严格正确率提升: {improvement:.0f}%")
    print(f"  响应时间: 完整方案 ({all_stats['full']['avg_elapsed']}s) vs 纯 LLM ({all_stats['llm_only']['avg_elapsed']}s)")

    return all_stats


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="电商智能客服评估工具")
    parser.add_argument("--auto", action="store_true", help="自动标注模式（不人工确认）")
    parser.add_argument("--mode", choices=["baseline", "compare"], default="baseline",
                        help="评估模式：baseline=基线评估, compare=三模式对比")
    parser.add_argument("--test-file", type=str, default=None,
                        help="自定义测试集路径")
    args = parser.parse_args()

    if args.test_file:
        TEST_SET_PATH = Path(args.test_file)

    if args.mode == "compare":
        run_comparison()
    else:
        interactive = not args.auto
        if interactive:
            print("💡 交互模式：每条问题需要手动标注。")
            print("   使用 --auto 可跳过人工标注。\n")
        else:
            print("⚡ 自动标注模式\n")
        run_baseline(interactive=interactive)
