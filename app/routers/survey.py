"""설문 페이지: 임시 사용자 발급, 설문 문항, 응답 저장 (4.10, 4.12)."""
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.db import get_db
from app.deps import get_user
from app.reco import repository as repo
from app.reco import survey_map

router = APIRouter(prefix="/api", tags=["설문"])

QuestionId = Annotated[str, StringConstraints(pattern=r"^q[0-9]{1,2}(_[0-9]{1,2})?$")]
OptionValue = Annotated[str, StringConstraints(min_length=1, max_length=60)]


class AnswersIn(BaseModel):
    """문항 id → 고른 보기 값 목록. 예: {"q1": ["android"], "q3": ["genre:RPG", "subgenre:턴제RPG"]}
    건너뛴 문항은 빼고 보낸다."""
    model_config = ConfigDict(extra="forbid")
    answers: dict[QuestionId, Annotated[list[OptionValue], Field(max_length=60)]] = Field(max_length=30)


def _questions_or_503(conn: sqlite3.Connection) -> list[dict]:
    questions = survey_map.resolved_questions(conn)
    if questions is None:
        raise HTTPException(503, "설문 문항 준비 중입니다.")
    return questions


@router.post("/users")
def ensure_user(user=Depends(get_user)):
    """임시 사용자 발급(쿠키). 이미 있으면 그대로. ID와 실험 그룹은 응답에 넣지 않는다."""
    return {"nickname": user["nickname"], "survey_done": bool(user["survey_done"])}


@router.get("/survey/questions")
def survey_questions(conn: sqlite3.Connection = Depends(get_db)):
    """문항과 보기 (태그·게임 보기는 DB에서 채움). show_if가 있는 문항은 그 조건일 때만 보여 준다."""
    return {"questions": [survey_map.public(q) for q in _questions_or_503(conn)]}


@router.post("/survey/answers")
def save_answers(body: AnswersIn, user=Depends(get_user), conn: sqlite3.Connection = Depends(get_db)):
    """응답 검사(보기·개수·필수·조건부) → 원본 저장 → 태그 점수 재계산 (설정의 '설문 다시 하기'도 같은 API)"""
    problems = survey_map.validate(_questions_or_503(conn), body.answers)
    if problems:
        raise HTTPException(422, " / ".join(problems))
    repo.save_answers(conn, user["id"], body.answers)
    return {"survey_done": True}
