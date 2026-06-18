import sys
import tempfile
import os as _os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import uvicorn
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles
try:
    import configuration
    print("configuration imported successfully")
except ImportError as e:
    print("Import error:", e)

from schemas import Question, Answer
from service import ChatService

# ---------------------------------------------------------------------------
# 加载 Whisper 模型（离线语音识别）
# ---------------------------------------------------------------------------
whisper_model = None
try:
    import whisper
    # base 模型 ~142MB，中文识别质量不错，CPU 上约 1-2 秒/秒音频
    whisper_model = whisper.load_model("base")
    print("[OK] Whisper base 模型加载完成（离线语音识别就绪）")
except Exception as e:
    print(f"[WARN] Whisper 模型加载失败，语音识别不可用: {e}")


app = FastAPI()
STATIC_DIR = Path(__file__).parent / "static"
print("STATIC_DIR:", STATIC_DIR)
print("Files in dir:", list(Path(STATIC_DIR).iterdir()))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
service = ChatService()

@app.get("/")
def read_root():
    return RedirectResponse("/static/index.html")

@app.post("/api/chat")
def read_item(question: Question, mode: str = "full", nocache: bool = False) -> Answer:
    print(f"Received question [{mode}]:", question.message)
    if nocache:
        answer = service.chat_nocache(question.message, eval_mode=mode)
    else:
        answer = service.chat(question.message, eval_mode=mode)
    print("Answer:", answer[:100] if answer else "(empty)")
    return Answer(message=answer)


@app.post("/api/chat/stream")
def read_item_stream(question: Question):
    print("Received streaming question:", question.message)

    def event_generator():
        try:
            for token in service.chat_stream(question.message):
                yield f"data: {token}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: [ERROR] {e}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/cache/stats")
def cache_stats():
    return {
        "hits": service.cache_hits,
        "misses": service.cache_misses,
        "size": len(service.cache),
        "max_size": service.cache_max_size,
    }


# ---------------------------------------------------------------------------
# POST /api/voice  -  离线语音识别（Whisper）
# ---------------------------------------------------------------------------
@app.post("/api/voice")
async def voice_endpoint(file: UploadFile = File(...)):
    """
    接收前端上传的音频文件（webm/wav/mp3），使用 Whisper 转文字。
    """
    if whisper_model is None:
        return {
            "text": "",
            "success": False,
            "error": "Whisper 模型未加载，语音识别不可用",
        }

    # 保存上传的音频到临时文件
    suffix = Path(file.filename).suffix if file.filename else ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # Whisper 转文字（language="zh" 提升中文识别准确率）
        result = whisper_model.transcribe(tmp_path, language="zh", fp16=False)
        text = result["text"].strip()
        print(f"🎤 语音识别结果: {text}")
        return {"text": text, "success": True, "error": ""}
    except Exception as e:
        print(f"语音识别失败: {e}")
        return {"text": "", "success": False, "error": f"语音识别失败: {str(e)}"}
    finally:
        # 清理临时文件
        try:
            _os.unlink(tmp_path)
        except Exception:
            pass


if __name__ == "__main__":
    print("启动 uvicorn 服务器...")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)