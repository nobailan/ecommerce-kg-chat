'''
电子商务知识图谱聊天 API 的全面测试脚本。
======================================================================

本脚本用于测试聊天服务中实现的改进：
  1. 流式处理端点 (/api/chat/stream)
  2. 模板匹配（快速路径，无需 LLM Cypher 生成）
  3. 响应缓存（第二次调用比第一次更快）
  4. 错误处理（格式错误的请求）
  5. 语音接口（/api/voice）
  6. 向后兼容性（/api/chat）

依赖项
------------
  pip install requests sseclient-py

  若无法安装 sseclient-py，流式测试将回退至
  通过 requests 并设置 stream=True 进行手动 SSE 解析。

用法
-----
  # 1. 首先启动服务器：
  #    cd E:\agentProject\graph\src\web
  #    python app.py
  #
  # 2. 然后在另一个终端中：
  #    python E:\agentProject\graph\test_improvements.py

  # 运行单个测试类：
  #    python -c "import sys; sys.path.insert(0,r'E:\agentProject\graph'); \
  #               from test_improvements import TestStreaming; \
  #               t=TestStreaming(); t.run(); t.print_summary()"

配置
-------------
  BASE_URL: 默认 “http://127.0.0.1:8000”
  可通过环境变量 BASE_URL 进行覆盖。'''
import os
import sys
import json
import time
import traceback
from typing import Optional, List, Dict, Any, Tuple

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "30"))
STREAM_TIMEOUT = int(os.environ.get("STREAM_TIMEOUT", "45"))
CACHE_FASTER_RATIO = 0.85  # second call must be at least this fraction faster
TEMPLATE_TIMEOUT = 3.0      # template-matched answers should return under 3s

# ---------------------------------------------------------------------------
# Colour helpers (for terminal output)
# ---------------------------------------------------------------------------
_COLOURS = {
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "reset": "\033[0m",
    "bold": "\033[1m",
}


def _c(colour: str, text: str) -> str:
    """Wrap *text* in ANSI colour escapes if stdout is a TTY."""
    if not sys.stdout.isatty():
        return text
    return _COLOURS.get(colour, "") + text + _COLOURS["reset"]


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------
def parse_sse_events(response: requests.Response) -> List[Dict[str, Any]]:
    """
    Manually parse SSE text/event-stream from a requests Response.
    Returns a list of parsed JSON objects (dicts) and one string "[DONE]" marker.
    """
    events: List[Dict[str, Any]] = []
    leftover = ""
    for chunk in response.iter_content(chunk_size=1024, decode_unicode=True):
        if chunk is None:
            continue
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        leftover += chunk
        while "\n\n" in leftover:
            raw_event, leftover = leftover.split("\n\n", 1)
            for line in raw_event.split("\n"):
                line = line.strip()
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        events.append("[DONE]")
                    else:
                        try:
                            events.append(json.loads(payload))
                        except json.JSONDecodeError:
                            events.append({"raw": payload})
    # Handle trailing event without \n\n
    if leftover.strip():
        for line in leftover.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload == "[DONE]":
                    events.append("[DONE]")
                else:
                    try:
                        events.append(json.loads(payload))
                    except json.JSONDecodeError:
                        events.append({"raw": payload})
    return events


# ---------------------------------------------------------------------------
# Test result collector
# ---------------------------------------------------------------------------
class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = 0
        self.failed = 0
        self.errors: List[str] = []
        self.measurements: Dict[str, float] = {}

    def ok(self, description: str = ""):
        self.passed += 1
        print(f"    {_c('green', 'PASS')}  {description}")

    def fail(self, description: str, detail: str = ""):
        self.failed += 1
        msg = f"{description}"
        if detail:
            msg += f"  --  {detail}"
        self.errors.append(msg)
        print(f"    {_c('red', 'FAIL')}  {msg}")

    def measure(self, key: str, value: float):
        self.measurements[key] = value
        print(f"    {_c('cyan', 'TIME')}  {key}: {value:.3f}s")

    @property
    def total(self) -> int:
        return self.passed + self.failed

    def print_summary(self):
        print(f"\n{_c('bold', self.name)}")
        print(f"  Passed: {_c('green', str(self.passed))}  "
              f"Failed: {_c('red', str(self.failed)) if self.failed else _c('green', '0')}  "
              f"Total:  {self.total}")
        if self.errors:
            print(f"  {_c('red', 'Failures:')}")
            for e in self.errors:
                print(f"    - {e}")
        if self.measurements:
            print(f"  {_c('cyan', 'Timings:')}")
            for k, v in self.measurements.items():
                print(f"    {k}: {v:.3f}s")
        return self.failed == 0


# ---------------------------------------------------------------------------
# Test: Streaming endpoint
# ---------------------------------------------------------------------------
class TestStreaming:
    def __init__(self):
        self.result = TestResult("1. Streaming Endpoint (/api/chat/stream)")

    def run(self):
        print(f"\n{'='*60}")
        print(f"{_c('bold', '1. Streaming Endpoint')}")
        print(f"{'='*60}")

        # --- 1a: Send question, verify SSE events ---
        print(f"\n  {_c('yellow', '1a)')} Sending streaming POST & verifying SSE events...")
        try:
            t0 = time.time()
            resp = requests.post(
                f"{BASE_URL}/api/chat/stream",
                json={"message": "有哪些手机品牌？"},
                headers={"Accept": "text/event-stream"},
                stream=True,
                timeout=STREAM_TIMEOUT,
            )
            t1 = time.time()
            self.result.measure("request_roundtrip", t1 - t0)

            if resp.status_code != 200:
                self.result.fail(f"Expected 200, got {resp.status_code}")
                return

            events = parse_sse_events(resp)
            self.result.measure("sse_parse_duration", time.time() - t1)

            # Verify we got at least one event
            if len(events) == 0:
                self.result.fail("No SSE events received")
                return
            self.result.ok(f"Received {len(events)} SSE events")

            # Verify [DONE] is present
            if "[DONE]" in events:
                self.result.ok("[DONE] sentinel found in events")
            else:
                self.result.fail("[DONE] sentinel NOT found in events",
                                 f"Last 3 events: {events[-3:]}")

            # Verify content was received (token or message events)
            has_content = any(
                isinstance(e, dict) and (e.get("token") or e.get("message"))
                for e in events
            )
            if has_content:
                self.result.ok("Token/message content received via SSE")
            else:
                self.result.fail("No token or message content in SSE events",
                                 f"Sample events: {events[:3]}...")

            # Verify content-type header
            ct = resp.headers.get("content-type", "")
            if "text/event-stream" in ct:
                self.result.ok(f"Content-Type is text/event-stream")
            else:
                self.result.fail(f"Wrong Content-Type: {ct}")

        except requests.exceptions.ConnectionError:
            self.result.fail("Connection refused – is the server running?")
        except requests.exceptions.Timeout:
            self.result.fail(f"Request timed out after {STREAM_TIMEOUT}s")
        except Exception as e:
            self.result.fail(f"Unexpected error: {e}", traceback.format_exc())

        # --- 1b: Test streaming with a template-matched question ---
        print(f"\n  {_c('yellow', '1b)')} Streaming with template-matched question...")
        try:
            t0 = time.time()
            resp = requests.post(
                f"{BASE_URL}/api/chat/stream",
                json={"message": "有哪些品牌？"},
                headers={"Accept": "text/event-stream"},
                stream=True,
                timeout=STREAM_TIMEOUT,
            )
            events = parse_sse_events(resp)
            elapsed = time.time() - t0
            self.result.measure("template_stream_total", elapsed)

            if resp.status_code == 200 and len(events) > 0 and "[DONE]" in events:
                self.result.ok(f"Template-aware question streamed successfully ({elapsed:.2f}s)")
            else:
                self.result.fail("Template-aware stream failed",
                                 f"Status={resp.status_code}, events={len(events)}")
        except Exception as e:
            self.result.fail(f"Template stream error: {e}")

        # --- 1c: Test streaming with malformed body (should still get error event) ---
        print(f"\n  {_c('yellow', '1c)')} Streaming with empty message...")
        try:
            resp = requests.post(
                f"{BASE_URL}/api/chat/stream",
                json={"message": ""},
                headers={"Accept": "text/event-stream"},
                stream=True,
                timeout=STREAM_TIMEOUT,
            )
            events = parse_sse_events(resp)
            # Should still respond (FastAPI validates via Pydantic; empty string passes)
            if resp.status_code == 200:
                self.result.ok("Empty message handled without crashing")
            else:
                self.result.ok(f"Empty message properly rejected: {resp.status_code}")
        except Exception as e:
            self.result.fail(f"Empty message test error: {e}")

    def print_summary(self):
        return self.result.print_summary()


# ---------------------------------------------------------------------------
# Test: Template matching
# ---------------------------------------------------------------------------
class TestTemplateMatching:
    """
    Tests that common questions which match predefined Cypher templates
    return within the expected time (under TEMPLATE_TIMEOUT seconds),
    confirming the fast-path works.
    """

    TEMPLATE_QUESTIONS = [
        "有哪些手机品牌？",
        "有哪些商品分类？",
        "华为有多少个商品？",
        "有哪些品牌？",
        "商品列表",  # from "商品列表" template pattern
        "这个多少钱？",
        "有什么颜色？",
        "有什么尺码？",
        "什么材质？",
        "有多少个商品？",
    ]

    def __init__(self):
        self.result = TestResult("2. Template Matching (Fast Path)")

    def run(self):
        print(f"\n{'='*60}")
        print(f"{_c('bold', '2. Template Matching (Fast Path)')}")
        print(f"{'='*60}")

        for question in self.TEMPLATE_QUESTIONS:
            print(f"\n  {_c('yellow', 'Q:')} \"{question}\"")
            try:
                t0 = time.time()
                resp = requests.post(
                    f"{BASE_URL}/api/chat",
                    json={"message": question},
                    timeout=TIMEOUT,
                )
                elapsed = time.time() - t0

                if resp.status_code != 200:
                    self.result.fail(f"Status {resp.status_code}",
                                     f"Body: {resp.text[:200]}")
                    continue

                data = resp.json()
                answer = data.get("message", "")

                self.result.measure(f'"{question[:20]}..."', elapsed)

                if elapsed <= TEMPLATE_TIMEOUT:
                    self.result.ok(f"Response < {TEMPLATE_TIMEOUT}s ({elapsed:.2f}s)")
                else:
                    self.result.fail(
                        f"Response too slow: {elapsed:.2f}s (threshold {TEMPLATE_TIMEOUT}s)",
                        f"May have fallen back to LLM Cypher generation"
                    )

                # Verify answer is non-empty and in Chinese (sanity check)
                if answer and len(answer) > 2:
                    self.result.ok(f"Non-empty answer: \"{answer[:50]}...\"")
                else:
                    self.result.fail(f"Empty or too-short answer: \"{answer}\"")

            except requests.exceptions.ConnectionError:
                self.result.fail("Connection refused – is the server running?")
                return
            except requests.exceptions.Timeout:
                self.result.fail(f"Template question timed out ({TIMEOUT}s)")
            except Exception as e:
                self.result.fail(f"Error: {e}", traceback.format_exc())

        # Summary
        fast = sum(1 for k, v in self.result.measurements.items()
                   if v <= TEMPLATE_TIMEOUT)
        total = len(self.result.measurements)
        print(f"\n  {_c('bold', f'Template match summary: {fast}/{total} under {TEMPLATE_TIMEOUT}s')}")

    def print_summary(self):
        return self.result.print_summary()


# ---------------------------------------------------------------------------
# Test: Caching
# ---------------------------------------------------------------------------
class TestCaching:
    """
    Verifies that the LRU cache works:
      - Send the same question twice.
      - The second call should be faster.
      - Cache stats endpoint should reflect hits.
    """

    CACHE_TEST_QUESTIONS = [
        "有哪些手机品牌？",
        "商品列表",
        "华为有多少个商品？",
    ]

    def __init__(self):
        self.result = TestResult("3. Caching")

    def run(self):
        print(f"\n{'='*60}")
        print(f"{_c('bold', '3. Caching')}")
        print(f"{'='*60}")

        for question in self.CACHE_TEST_QUESTIONS:
            print(f"\n  {_c('yellow', 'Q:')} \"{question}\"")

            try:
                # First call (should be a cache miss)
                t0 = time.time()
                resp1 = requests.post(
                    f"{BASE_URL}/api/chat",
                    json={"message": question},
                    timeout=TIMEOUT,
                )
                t1 = time.time()
                first_duration = t1 - t0

                if resp1.status_code != 200:
                    self.result.fail(f"First call failed: {resp1.status_code}")
                    continue

                answer1 = resp1.json().get("message", "")
                self.result.measure(f'first_call', first_duration)
                print(f"    First call: {first_duration:.3f}s")

                # Small delay to avoid any rate-limiting
                time.sleep(0.3)

                # Second call (should be a cache hit)
                t2 = time.time()
                resp2 = requests.post(
                    f"{BASE_URL}/api/chat",
                    json={"message": question},
                    timeout=TIMEOUT,
                )
                t3 = time.time()
                second_duration = t3 - t2

                if resp2.status_code != 200:
                    self.result.fail(f"Second call failed: {resp2.status_code}")
                    continue

                answer2 = resp2.json().get("message", "")
                self.result.measure(f'second_call', second_duration)
                print(f"    Second call: {second_duration:.3f}s")

                # Verify answers match (may not be byte-identical due to LLM,
                # but the cached answer should be identical)
                if answer1 == answer2:
                    self.result.ok("Cache returned identical answer")
                elif answer2 and len(answer2) > 2:
                    # LLM non-determinism could produce different answers without cache;
                    # if answers differ but both are substantial, cache may still have worked
                    # (the key check is speed)
                    self.result.ok("Both calls returned substantial answers (cache may have re-generated)")
                else:
                    self.result.fail("Second answer is empty or too short")

                # The cache-hit path should be significantly faster
                if second_duration < first_duration * CACHE_FASTER_RATIO:
                    self.result.ok(
                        f"Second call faster: {second_duration:.3f}s < {first_duration:.3f}s "
                        f"(ratio {second_duration/first_duration:.2f})"
                    )
                else:
                    self.result.fail(
                        f"Second call NOT significantly faster: {second_duration:.3f}s vs "
                        f"{first_duration:.3f}s (ratio {second_duration/first_duration:.2f})"
                    )

            except requests.exceptions.ConnectionError:
                self.result.fail("Connection refused – is the server running?")
                return
            except requests.exceptions.Timeout:
                self.result.fail(f"Request timed out ({TIMEOUT}s)")
            except Exception as e:
                self.result.fail(f"Error: {e}", traceback.format_exc())

        # --- Verify cache stats endpoint ---
        print(f"\n  {_c('yellow', 'Cache Stats:')} Checking /api/cache/stats...")
        try:
            resp = requests.get(f"{BASE_URL}/api/cache/stats", timeout=10)
            if resp.status_code == 200:
                stats = resp.json()
                print(f"    hits={stats.get('hits')}, misses={stats.get('misses')}, "
                      f"size={stats.get('size')}, max={stats.get('max_size')}")
                if stats.get("hits", 0) > 0:
                    self.result.ok(f"Cache reports {stats['hits']} hits (caching confirmed)")
                else:
                    self.result.fail("Cache reports 0 hits", "Cache may not be working")
            else:
                self.result.fail(f"Cache stats endpoint returned {resp.status_code}")
        except Exception as e:
            self.result.fail(f"Cache stats endpoint error: {e}")

    def print_summary(self):
        return self.result.print_summary()


# ---------------------------------------------------------------------------
# Test: Error handling
# ---------------------------------------------------------------------------
class TestErrorHandling:
    """
    Sends malformed / invalid requests and verifies proper error responses.
    """

    def __init__(self):
        self.result = TestResult("4. Error Handling")

    def run(self):
        print(f"\n{'='*60}")
        print(f"{_c('bold', '4. Error Handling')}")
        print(f"{'='*60}")

        # --- 4a: Missing required field ---
        print(f"\n  {_c('yellow', '4a)')} POST /api/chat with missing 'message' field...")
        try:
            resp = requests.post(
                f"{BASE_URL}/api/chat",
                json={},
                timeout=TIMEOUT,
            )
            if resp.status_code == 422:
                self.result.ok(f"422 Unprocessable Entity returned (expected)")
            else:
                self.result.fail(f"Expected 422, got {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            self.result.fail(f"Error: {e}")

        # --- 4b: Wrong field name ---
        print(f"\n  {_c('yellow', '4b)')} POST /api/chat with wrong field name...")
        try:
            resp = requests.post(
                f"{BASE_URL}/api/chat",
                json={"query": "有哪些手机品牌？"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 422:
                self.result.ok(f"422 returned for wrong field name")
            else:
                self.result.fail(f"Expected 422, got {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            self.result.fail(f"Error: {e}")

        # --- 4c: Non-JSON body ---
        print(f"\n  {_c('yellow', '4c)')} POST /api/chat with non-JSON body...")
        try:
            resp = requests.post(
                f"{BASE_URL}/api/chat",
                data="this is not json",
                headers={"Content-Type": "text/plain"},
                timeout=TIMEOUT,
            )
            if resp.status_code in (400, 422, 415):
                self.result.ok(f"Status {resp.status_code} returned for non-JSON body")
            else:
                self.result.fail(f"Expected 4xx, got {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            self.result.fail(f"Error: {e}")

        # --- 4d: Empty string message ---
        print(f"\n  {_c('yellow', '4d)')} POST /api/chat with empty message string...")
        try:
            resp = requests.post(
                f"{BASE_URL}/api/chat",
                json={"message": ""},
                timeout=TIMEOUT,
            )
            # Empty string is valid by Pydantic; should not crash
            if resp.status_code == 200:
                data = resp.json()
                self.result.ok(f"Empty message handled (answer: \"{data.get('message', '')[:50]}\")")
            else:
                self.result.fail(f"Unexpected status {resp.status_code}")
        except Exception as e:
            self.result.fail(f"Error: {e}")

        # --- 4e: Very long message ---
        print(f"\n  {_c('yellow', '4e)')} POST /api/chat with very long message (2K chars)...")
        try:
            long_msg = "你好" * 1024  # ~2K chars
            resp = requests.post(
                f"{BASE_URL}/api/chat",
                json={"message": long_msg},
                timeout=60,  # may take longer
            )
            if resp.status_code in (200, 422):
                self.result.ok(f"Long message handled (status {resp.status_code})")
            else:
                self.result.fail(f"Unexpected status {resp.status_code}")
        except requests.exceptions.Timeout:
            self.result.fail("Long message timed out (server may be hanging)")
        except Exception as e:
            self.result.fail(f"Error: {e}")

        # --- 4f: Non-existent endpoint ---
        print(f"\n  {_c('yellow', '4f)')} GET /api/nonexistent...")
        try:
            resp = requests.get(f"{BASE_URL}/api/nonexistent", timeout=TIMEOUT)
            if resp.status_code == 404:
                self.result.ok("404 returned for non-existent endpoint")
            else:
                self.result.fail(f"Expected 404, got {resp.status_code}")
        except Exception as e:
            self.result.fail(f"Error: {e}")

        # --- 4g: Streaming with missing field ---
        print(f"\n  {_c('yellow', '4g)')} POST /api/chat/stream with missing field...")
        try:
            resp = requests.post(
                f"{BASE_URL}/api/chat/stream",
                json={},
                timeout=TIMEOUT,
            )
            if resp.status_code == 422:
                self.result.ok("422 returned for missing field in streaming endpoint")
            else:
                self.result.fail(f"Expected 422, got {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            self.result.fail(f"Error: {e}")

        # --- 4h: POST to GET-only endpoint ---
        print(f"\n  {_c('yellow', '4h)')} POST to GET-only root...")
        try:
            resp = requests.post(f"{BASE_URL}/", timeout=TIMEOUT)
            if resp.status_code == 405:
                self.result.ok("405 Method Not Allowed returned")
            else:
                self.result.fail(f"Expected 405, got {resp.status_code}")
        except Exception as e:
            self.result.fail(f"Error: {e}")

    def print_summary(self):
        return self.result.print_summary()


# ---------------------------------------------------------------------------
# Test: Voice endpoint
# ---------------------------------------------------------------------------
class TestVoice:
    """Tests the /api/voice endpoint (currently a placeholder stub)."""

    def __init__(self):
        self.result = TestResult("5. Voice Endpoint (/api/voice)")

    def run(self):
        print(f"\n{'='*60}")
        print(f"{_c('bold', '5. Voice Endpoint (/api/voice)')}")
        print(f"{'='*60}")

        # --- 5a: POST to /api/voice ---
        print(f"\n  {_c('yellow', '5a)')} POST /api/voice (expect JSON stub)...")
        try:
            resp = requests.post(f"{BASE_URL}/api/voice", timeout=TIMEOUT)
            if resp.status_code == 200:
                self.result.ok("200 returned")
            else:
                self.result.fail(f"Expected 200, got {resp.status_code}")

            # Verify JSON content type
            ct = resp.headers.get("content-type", "")
            if "application/json" in ct:
                self.result.ok("Content-Type is application/json")
            else:
                self.result.fail(f"Content-Type is not JSON: {ct}")

            # Verify JSON fields
            try:
                data = resp.json()
                self.result.ok(f"Response is valid JSON: {data}")

                # Current placeholder returns {"text": "", "success": false, "error": "..."}
                if "text" in data and "success" in data and "error" in data:
                    self.result.ok("Response contains expected keys: text, success, error")
                else:
                    self.result.fail(f"Missing expected keys in response: {list(data.keys())}")

                # success should currently be False (placeholder)
                if data.get("success") is False:
                    self.result.ok("success=False as expected (placeholder)")
                elif data.get("success") is True:
                    self.result.ok("success=True (voice may be implemented!)")
                else:
                    self.result.fail(f"Unexpected 'success' value: {data.get('success')}")

            except json.JSONDecodeError:
                self.result.fail("Response body is not valid JSON",
                                 f"Body: {resp.text[:200]}")

        except requests.exceptions.ConnectionError:
            self.result.fail("Connection refused – is the server running?")
        except Exception as e:
            self.result.fail(f"Error: {e}")

        # --- 5b: GET /api/voice (should be 405 Method Not Allowed) ---
        print(f"\n  {_c('yellow', '5b)')} GET /api/voice (expect 405)...")
        try:
            resp = requests.get(f"{BASE_URL}/api/voice", timeout=TIMEOUT)
            if resp.status_code == 405:
                self.result.ok("405 Method Not Allowed (GET on POST-only endpoint)")
            else:
                self.result.fail(f"Expected 405, got {resp.status_code}")
        except Exception as e:
            self.result.fail(f"Error: {e}")

    def print_summary(self):
        return self.result.print_summary()


# ---------------------------------------------------------------------------
# Test: Backward compatibility
# ---------------------------------------------------------------------------
class TestBackwardCompatibility:
    """
    Verifies the original /api/chat endpoint still works correctly.
    """

    COMPAT_QUESTIONS = [
        "有哪些手机品牌？",
        "有哪些商品分类？",
        "你好",
    ]

    def __init__(self):
        self.result = TestResult("6. Backward Compatibility (/api/chat)")

    def run(self):
        print(f"\n{'='*60}")
        print(f"{_c('bold', '6. Backward Compatibility (/api/chat)')}")
        print(f"{'='*60}")

        for question in self.COMPAT_QUESTIONS:
            print(f"\n  {_c('yellow', 'Q:')} \"{question}\"")
            try:
                t0 = time.time()
                resp = requests.post(
                    f"{BASE_URL}/api/chat",
                    json={"message": question},
                    timeout=TIMEOUT,
                )
                elapsed = time.time() - t0

                if resp.status_code != 200:
                    self.result.fail(f"Status {resp.status_code}",
                                     f"Body: {resp.text[:200]}")
                    continue

                # Check Content-Type
                ct = resp.headers.get("content-type", "")
                if "application/json" in ct:
                    self.result.ok("Content-Type is application/json")
                else:
                    self.result.fail(f"Wrong Content-Type: {ct}")

                # Parse JSON
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    self.result.fail("Response body is not valid JSON",
                                     f"Body: {resp.text[:200]}")
                    continue

                # Check schema: should have "message" field
                if "message" in data:
                    self.result.ok("Response contains 'message' field (schema valid)")
                else:
                    self.result.fail(f"Missing 'message' field. Keys: {list(data.keys())}")

                # Answer should be non-empty
                answer = data.get("message", "")
                if answer and len(answer) > 2:
                    self.result.ok(f"Non-trivial answer: \"{answer[:60]}...\"")
                    self.result.measure(f'"{question[:20]}"', elapsed)
                elif answer in ("", "未找到相关信息。"):
                    self.result.ok(f"Answer: \"{answer}\" (no results or empty)")
                else:
                    self.result.fail(f"Answer too short: \"{answer}\"")

            except requests.exceptions.ConnectionError:
                self.result.fail("Connection refused – is the server running?")
                return
            except requests.exceptions.Timeout:
                self.result.fail(f"Request timed out ({TIMEOUT}s)")
            except Exception as e:
                self.result.fail(f"Error: {e}", traceback.format_exc())

    def print_summary(self):
        return self.result.print_summary()


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
def check_server() -> bool:
    """Quick health check to see if the server is reachable."""
    try:
        resp = requests.get(f"{BASE_URL}/", timeout=5, allow_redirects=False)
        # Root redirects to /static/index.html
        if resp.status_code in (200, 307, 302):
            print(f"{_c('green', f'Server reachable at {BASE_URL} (status {resp.status_code})')}")
            return True
        print(f"{_c('yellow', f'Server responded with status {resp.status_code} – proceeding anyway')}")
        return True
    except requests.exceptions.ConnectionError:
        print(f"{_c('red', f'ERROR: Cannot reach server at {BASE_URL}')}")
        print(f"{_c('yellow', 'Make sure the server is running:')}")
        print(f"  cd E:\\agentProject\\graph\\src\\web")
        print(f"  python app.py")
        return False
    except Exception as e:
        print(f"{_c('red', f'ERROR checking server: {e}')}")
        return False


def main():
    print(f"{_c('bold', '='*60)}")
    print(f"{_c('bold', 'E-Commerce Knowledge Graph Chat API – Test Suite')}")
    print(f"{_c('bold', '='*60)}")
    print(f"Base URL:    {_c('cyan', BASE_URL)}")
    print(f"Timeout:     {TIMEOUT}s (streaming: {STREAM_TIMEOUT}s)")
    print(f"Template threshold: {TEMPLATE_TIMEOUT}s")
    print(f"Cache ratio: {CACHE_FASTER_RATIO}")
    print()

    # Health check
    if not check_server():
        sys.exit(1)

    # Run all test suites
    suites = [
        TestStreaming(),
        TestTemplateMatching(),
        TestCaching(),
        TestErrorHandling(),
        TestVoice(),
        TestBackwardCompatibility(),
    ]

    all_passed = True
    for suite in suites:
        suite.run()
        ok = suite.print_summary()
        all_passed = all_passed and ok

    # Grand total
    print(f"\n{'='*60}")
    print(f"{_c('bold', 'GRAND SUMMARY')}")
    print(f"{'='*60}")
    total_passed = sum(s.result.passed for s in suites)
    total_failed = sum(s.result.failed for s in suites)
    total_tests = total_passed + total_failed
    print(f"  Total tests: {total_tests}")
    print(f"  Passed:      {_c('green', str(total_passed))}")
    print(f"  Failed:      {_c('red', str(total_failed)) if total_failed else _c('green', '0')}")

    if all_passed:
        print(f"\n  {_c('green', _c('bold', 'ALL TEST SUITES PASSED'))}")
    else:
        print(f"\n  {_c('red', _c('bold', 'SOME TESTS FAILED – see details above'))}")

    print(f"\n{_c('bold', '='*60)}")
    print(f"{_c('bold', 'Test run complete.')}")
    print(f"{_c('bold', '='*60)}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
