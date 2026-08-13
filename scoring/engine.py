"""
채점 엔진: 규칙(M1~M3) + 행동 방향(M4) + LLM(M5) + PORTFOLIO를 합쳐 Scorecard로.

- turn_score는 코드가 계산한다.
- 빈 입력과 기준표 누락을 방어한다.
- M5 채점 모델이 모두 실패하면 M5를 총점에서 제외한다.
"""
import json

from data.models import (
    UserDecision,
    Scorecard,
    CardStatus,
    MetricResult,
    MetricId,
    TrapResult,
)
from scoring import (
    rule_scorer,
    llm_scorer,
    feedback_generator,
    action_scorer,
)


DEFAULT_WEIGHTS = {
    "M1": 0.20,
    "M2": 0.18,
    "M3": 0.18,
    "M4": 0.17,
    "M5": 0.12,
    "PORTFOLIO": 0.15,
}


def score_turn(decision: UserDecision, rubric: dict) -> Scorecard:
    sid = decision.scenario_id
    tno = decision.turn_no

    # 1. 빈 입력 방어
    if decision.is_empty():
        return Scorecard(
            scenario_id=sid,
            turn_no=tno,
            status=CardStatus.EMPTY_INPUT,
            feedback="판단이 비어 있어 채점할 수 없습니다.",
        )

    # 2. 기준표 누락 방어
    if not rubric or not rubric.get("answer_rules"):
        return Scorecard(
            scenario_id=sid,
            turn_no=tno,
            status=CardStatus.MISSING_RUBRIC,
            feedback="채점 기준표가 없습니다.",
        )

    answers = [
        {
            "question_id": answer.question_id,
            "selected": answer.selected,
            "text": answer.text,
        }
        for answer in decision.answers
    ]

    # 3. 객관식 규칙 채점
    qscores = rule_scorer.score_objective(answers, rubric)
    metric_map = rule_scorer.aggregate_by_metric(qscores)

    # 4. 자유서술 M5 채점
    free_answer = next(
        (
            answer
            for answer in answers
            if rubric.get("answer_rules", {})
            .get(answer["question_id"], {})
            .get("type") == "free"
        ),
        None,
    )

    turn_context = rubric.get("turn_context", "")
    free_text = free_answer.get("text", "") if free_answer else ""
    has_free_text = bool(free_text.strip())

    m5 = llm_scorer.score_freetext(
        free_text,
        answers,
        turn_context,
    )

    metric_map["M5"] = m5.score

    m5_unavailable = any(
        penalty.cause == "LLM_UNAVAILABLE"
        for penalty in m5.penalties
    )

    # 5. 행동과 포트폴리오 채점
    action_rule = rubric.get("action_rule", {})

    q36_answer = next(
        (
            answer["selected"]
            for answer in answers
            if answer["question_id"] == "Q36"
        ),
        [],
    )

    m4_action = None
    portfolio = None

    if action_rule:
        m4_action, portfolio = action_scorer.score_actions(
            decision.holdings,
            decision.cash_pct,
            q36_answer,
            action_rule,
        )

        metric_map["M4"] = m4_action.score
        metric_map["PORTFOLIO"] = portfolio.score

    # 6. 최종 MetricResult 목록 조립
    metrics = []

    for metric_id in ["M1", "M2", "M3"]:
        if metric_id in metric_map:
            metrics.append(
                MetricResult(
                    metric=MetricId(metric_id),
                    score=metric_map[metric_id],
                )
            )

    if m4_action is not None:
        metrics.append(m4_action)
    elif "M4" in metric_map:
        metrics.append(
            MetricResult(
                metric=MetricId.M4,
                score=metric_map["M4"],
            )
        )

    metrics.append(m5)

    if portfolio is not None:
        metrics.append(portfolio)

    # 7. 가중 평균 계산
    weights = rubric.get("metric_weights") or DEFAULT_WEIGHTS

    weighted_score = 0.0
    applied_weight = 0.0

    for metric in metrics:
        # M5 모델이 모두 실패했을 때 표시용으로 넣은 3점은
        # 실제 사용자 총점에 반영하지 않는다.
        is_unavailable = any(
            penalty.cause == "LLM_UNAVAILABLE"
            for penalty in metric.penalties
        )

        if is_unavailable:
            continue

        weight = weights.get(metric.metric.value, 0.0)

        weighted_score += metric.score * weight
        applied_weight += weight

    if applied_weight > 0:
        turn_score = round(weighted_score / applied_weight, 2)
    else:
        turn_score = 0.0

    # 8. 피드백 재료 수집
    material = _collect_material(
        qscores,
        rubric,
        m5,
    )

    # 행동 및 포트폴리오 감점 근거도 피드백에 포함한다.
    if m4_action is not None:
        for penalty in m4_action.penalties:
            material["missed_points"].append(penalty.evidence)

    if portfolio is not None:
        for penalty in portfolio.penalties:
            material["missed_points"].append(penalty.evidence)

    material["missed_points"] = material["missed_points"][:6]

    # Q39가 비어 있으면 채점과 피드백 모두 LLM을 호출하지 않는다.
    # M5 채점 모델이 전부 실패한 경우에도 추가 피드백 호출을 하지 않는다.
    feedback = feedback_generator.generate_feedback(
        material,
        turn_context,
        use_llm=has_free_text and not m5_unavailable,
    )

    # 9. 함정 결과 조립
    traps = [
        TrapResult(
            trap_id=trap_id,
            triggered=True,
            explanation=explanation,
        )
        for trap_id, explanation
        in material.get("triggered_traps", [])
    ]

    return Scorecard(
        scenario_id=sid,
        turn_no=tno,
        status=CardStatus.SCORED,
        metrics=metrics,
        traps=traps,
        turn_score=turn_score,
        feedback=json.dumps(
            feedback,
            ensure_ascii=False,
        ),
    )


def _collect_material(
    qscores,
    rubric: dict,
    m5: MetricResult,
) -> dict:
    """채점 결과에서 잘 본 점, 놓친 점, 함정을 수집한다."""
    good_points = []
    missed_points = []
    triggered_traps = []

    answer_rules = rubric.get("answer_rules", {})

    for question_score in qscores:
        rule = answer_rules.get(
            question_score.question_id,
            {},
        )
        note = rule.get("note", "")

        if question_score.good_hit and not question_score.trap_hit:
            good_points.append(
                note
                or f"{question_score.question_id}에서 핵심을 잘 짚음"
            )

        if question_score.trap_hit:
            trap_text = ", ".join(question_score.trap_hit)

            missed_points.append(
                f"{note} (함정: {trap_text}에 주의)"
            )
            triggered_traps.append(
                (
                    f"TRAP_{question_score.question_id}",
                    note,
                )
            )

        elif not question_score.picked:
            missed_points.append(
                note
                or f"{question_score.question_id}를 고려하지 못함"
            )

    # 객관식과 자유서술이 직접 충돌한 경우만 추가한다.
    for penalty in m5.penalties:
        if penalty.cause == "INCONSISTENT_REASON":
            missed_points.append(penalty.evidence)

    objective_summary = ", ".join(
        f"{question_score.metric} {question_score.score}점"
        for question_score in qscores
    )

    if objective_summary:
        score_summary = (
            f"{objective_summary}, M5 {m5.score}점"
        )
    else:
        score_summary = f"M5 {m5.score}점"

    return {
        "good_points": good_points[:4],
        "missed_points": missed_points[:4],
        "ai_baseline": rubric.get(
            "ai_baseline",
            {},
        ).get("rationale", ""),
        "score_summary": score_summary,
        "triggered_traps": triggered_traps,
    }