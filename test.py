import requests
resp = requests.post("http://127.0.0.1:8000/api/chat", json={"message": "有哪些手机品牌？"})
print(resp.text)  # 应该显示正常中文