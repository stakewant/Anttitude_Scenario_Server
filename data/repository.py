"""
데이터 조회 창구.
- JSON 파일에서 질문 은행·시나리오·기준표를 읽어온다.
- 채점/진행 코드는 이 함수들로만 데이터에 접근한다.
- 나중에 파일 → DB로 바꿔도 이 파일 안만 고치면 된다(호출부 불변).
"""
import json
from pathlib import Path

# 이 파일(repository.py)이 있는 폴더 = data/
DATA_DIR = Path(__file__).parent
SCENARIOS_DIR = DATA_DIR / "scenarios"


def _load_json(path: Path) -> dict:
    """JSON 파일 하나를 읽어 dict로. 없으면 에러."""
    if not path.exists():
        raise FileNotFoundError(f"파일 없음: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_questions() -> dict:
    """질문 은행(공통) 전체를 반환."""
    return _load_json(DATA_DIR / "questions.json")


def get_scenario(scenario_id: str) -> dict:
    """시나리오 메타(제목·학습포인트·턴 골격) 반환."""
    return _load_json(SCENARIOS_DIR / scenario_id / "scenario.json")


def get_rubric(scenario_id: str, turn_no: int) -> dict:
    """특정 시나리오·턴의 채점 기준표(정답지) 반환."""
    return _load_json(SCENARIOS_DIR / scenario_id / f"rubric_turn{turn_no}.json")


def get_turn_questions(scenario_id: str, turn_no: int) -> list[dict]:
    """
    이 턴에 실제로 쓸 질문들을 (은행에서 뽑아) 반환.
    rubric의 questions_used 목록 → 질문 은행에서 해당 질문 꺼내기.
    """
    rubric = get_rubric(scenario_id, turn_no)
    bank = get_questions()["questions"]
    used_ids = rubric.get("questions_used", [])
    result = []
    for qid in used_ids:
        if qid in bank:
            q = dict(bank[qid])   # 복사
            q["question_id"] = qid
            result.append(q)
    return result