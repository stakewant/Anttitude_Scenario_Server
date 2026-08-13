import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# M5 채점: 품질 우선
SCORING_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

# 피드백 문장 생성: 속도·비용 우선
FEEDBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
]

LLM_TEMPERATURE = 0
LLM_TIMEOUT_SECONDS = 15