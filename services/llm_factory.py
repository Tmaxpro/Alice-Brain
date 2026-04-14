"""
ALICE Brain — LLM Factory (services/llm_factory.py)
────────────────────────────────────────────────────
Module centralisé d'instanciation du LLM.

Priorité 1 : MiniMax M2.7 via NVIDIA NIM (langchain-nvidia-ai-endpoints)
Priorité 2 : Claude claude-sonnet-4-5 via Anthropic (fallback, si clé présente)

AUCUN agent n'instancie le LLM directement.
Tous importent :  from services.llm_factory import llm
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_nvidia_ai_endpoints import ChatNVIDIA

from config import settings

logger = logging.getLogger(__name__)


def build_llm() -> BaseChatModel:
    """
    Construit la chaîne LLM avec fallback automatique.
    Retourne un objet compatible BaseChatModel.
    """
    # ── LLM principal : MiniMax M2.7 via NVIDIA NIM ──
    primary = ChatNVIDIA(
        model="minimaxai/minimax-m2.7",
        api_key=settings.NVIDIA_API_KEY,
        temperature=1,
        top_p=0.95,
        max_tokens=4096,
    )
    logger.info("LLM principal initialisé : MiniMax M2.7 (NVIDIA NIM)")

    # ── LLM fallback : Claude (optionnel) ──
    if settings.ANTHROPIC_API_KEY:
        try:
            from langchain_anthropic import ChatAnthropic

            fallback = ChatAnthropic(
                model="claude-sonnet-4-5",
                api_key=settings.ANTHROPIC_API_KEY,
                temperature=0.3,
                max_tokens=4096,
            )
            logger.info("LLM fallback initialisé : Claude claude-sonnet-4-5 (Anthropic)")
            return primary.with_fallbacks(
                [fallback],
                exceptions_to_handle=(Exception,),
            )
        except ImportError:
            logger.warning(
                "langchain-anthropic non installé — fallback Claude désactivé."
            )
    else:
        logger.info("ANTHROPIC_API_KEY absente — fallback Claude désactivé.")

    return primary


# ── Singleton importé par tous les agents ──
llm: BaseChatModel = build_llm()
