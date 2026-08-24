"""Finance query handlers. NUMBER/CALC use XBRL only; ANALYSIS gets FTS5 + facts."""

from __future__ import annotations

import logging

from fastapi.concurrency import run_in_threadpool

from app.schemas import FinanceFactOut, FinanceQueryIn, FinanceQueryOut, FinanceSourceOut
from app.services.agent_enrichment import infer_with_metrics
from app.services.dart_db import get_fact, list_facts, search_paragraphs
from app.services.dart_router import QueryRoute, route_finance_question
from app.services.finance_db import lookup_ticker
from app.services.llm_quality import persist_quality_row, score_rag

log = logging.getLogger(__name__)

DEFAULT_YEAR = 2024
MAX_PARAS = 3
MAX_PARA_CHARS = 400


def resolve_ticker(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        hit = lookup_ticker(text)
    except Exception:
        log.exception("ticker lookup failed")
        hit = None
    if hit is not None:
        return hit.ticker
    if text.isdigit():
        return text.zfill(6)
    return None


def _fact_out(fact) -> FinanceFactOut:
    return FinanceFactOut(
        ticker=fact.ticker,
        year=fact.year,
        quarter=fact.quarter,
        account=fact.account,
        account_name=fact.account_name,
        value=fact.value,
    )


def _number_answer(ticker: str, account: str, year: int) -> FinanceQueryOut:
    fact = get_fact(ticker, account, year)
    if fact is None:
        return FinanceQueryOut(
            ok=False,
            route="NUMBER",
            answer=f"{year}년 {account} XBRL 수치가 없습니다.",
        )
    return FinanceQueryOut(
        ok=True,
        route="NUMBER",
        answer=f"{year}년 {fact.account_name}은 {fact.value:,}원입니다.",
        facts=[_fact_out(fact)],
    )


def _yoy_change(current: int, previous: int) -> float:
    if previous == 0:
        raise ZeroDivisionError("previous is zero")
    return (current - previous) / previous * 100.0


def _margin(operating_profit: int, revenue: int) -> float:
    if revenue == 0:
        raise ZeroDivisionError("revenue is zero")
    return operating_profit / revenue * 100.0


def _calc_answer(ticker: str, account: str, year: int, calc: str) -> FinanceQueryOut:
    try:
        if calc == "margin":
            op = get_fact(ticker, "operating_profit", year)
            rev = get_fact(ticker, "revenue", year)
            if op is None or rev is None:
                return FinanceQueryOut(ok=False, route="CALCULATION", answer="마진 계산에 필요한 XBRL이 없습니다.")
            pct = _margin(op.value, rev.value)
            return FinanceQueryOut(
                ok=True,
                route="CALCULATION",
                answer=f"{year}년 영업이익률은 {pct:.1f}%입니다.",
                facts=[_fact_out(op), _fact_out(rev)],
            )
        current = get_fact(ticker, account, year)
        previous = get_fact(ticker, account, year - 1)
        if current is None or previous is None:
            return FinanceQueryOut(ok=False, route="CALCULATION", answer="전년 대비 계산에 필요한 XBRL이 없습니다.")
        pct = _yoy_change(current.value, previous.value)
        return FinanceQueryOut(
            ok=True,
            route="CALCULATION",
            answer=f"{previous.year}→{current.year} {current.account_name} 증가율은 {pct:.1f}%입니다.",
            facts=[_fact_out(previous), _fact_out(current)],
        )
    except ZeroDivisionError:
        return FinanceQueryOut(ok=False, route="CALCULATION", answer="분모가 0이라 계산할 수 없습니다.")
    except Exception:
        log.exception("calculation failed")
        return FinanceQueryOut(ok=False, route="CALCULATION", answer="계산에 실패했습니다.")


def _gather_analysis(ticker: str, question: str, year: int) -> tuple[list, list]:
    facts = list_facts(ticker, year=year) or list_facts(ticker)
    paras = search_paragraphs(ticker, question, limit=MAX_PARAS)
    return facts, paras


def build_analysis_prompt(question: str, facts: list, paras: list) -> str:
    fact_lines = [
            f"- {f.year} {f.account_name}({f.account}) = {f.value:,}" for f in facts[:8]
    ] or ["- (no XBRL)"]
    para_lines = []
    for hit in paras[:MAX_PARAS]:
        text = (hit.paragraph_text or "")[:MAX_PARA_CHARS]
        para_lines.append(f"[p.{hit.page} {hit.doc_id}] {text}")
    excerpts = "\n".join(para_lines) or "(no related paragraphs)"
    return (
        "Answer in Korean, briefly. Use XBRL numbers only. Do not cite numbers from PDF paragraphs.\n"
        f"Question: {question}\n"
        "XBRL:\n"
        f"{chr(10).join(fact_lines)}\n"
        "Report excerpts (background only):\n"
        f"{excerpts}\n"
    )


async def answer_finance_query(payload: FinanceQueryIn, *, user_id: int | None = None) -> FinanceQueryOut:
    try:
        routed = route_finance_question(payload.question)
        ticker = await run_in_threadpool(resolve_ticker, payload.ticker)
        if ticker is None:
            return FinanceQueryOut(
                ok=False,
                route=routed.route.value,
                answer="종목을 찾지 못했습니다.",
            )
        year = payload.year or routed.year or DEFAULT_YEAR
        if routed.route == QueryRoute.NUMBER:
            return await run_in_threadpool(_number_answer, ticker, routed.account, year)
        if routed.route == QueryRoute.CALCULATION:
            return await run_in_threadpool(_calc_answer, ticker, routed.account, year, routed.calc)
        facts, paras = await run_in_threadpool(_gather_analysis, ticker, payload.question, year)
        prompt = build_analysis_prompt(payload.question, facts, paras)
        sources = [FinanceSourceOut(doc_id=h.doc_id, page=h.page) for h in paras]
        fact_out = [_fact_out(f) for f in facts]
        try:
            metrics = await infer_with_metrics(prompt)
            text = (metrics.text or "").strip()
        except Exception as exc:
            log.warning("analysis llm skipped: %s", exc)
            numbers = "; ".join(f"{f.account_name} {f.value:,}" for f in facts[:4]) or "없음"
            return FinanceQueryOut(
                ok=True,
                route="ANALYSIS",
                answer=f"분석 모델을 쓰지 못했습니다. XBRL 수치: {numbers}",
                facts=fact_out,
                sources=sources,
            )
        if user_id:
            try:
                from app.db import SessionLocal
                from app.config import get_settings as _settings

                rag = score_rag(
                    question=payload.question,
                    answer=text,
                    contexts=[h.paragraph_text for h in paras],
                    grounded_values=[str(f.value) for f in facts],
                )
                qdb = SessionLocal()
                try:
                    persist_quality_row(
                        qdb,
                        user_id=int(user_id),
                        purpose="dart_analysis",
                        provider="ollama",
                        model=_settings().resolved_llm_local_model,
                        prompt_tokens=0,
                        completion_tokens=metrics.completion_tokens,
                        success=bool(text),
                        error_message="" if text else "empty_response",
                        timing=metrics.timing,
                        rag=rag,
                    )
                finally:
                    qdb.close()
            except Exception:
                log.warning("dart analysis quality persist skipped", exc_info=True)
        return FinanceQueryOut(
            ok=True,
            route="ANALYSIS",
            answer=text or "분석 결과가 비었습니다.",
            facts=fact_out,
            sources=sources,
        )
    except Exception:
        log.exception("finance query failed")
        return FinanceQueryOut(ok=False, route="ANALYSIS", answer="재무 조회에 실패했습니다.")
