"""Сопоставление фраз с действиями навыков.

Алгоритмы перенесены из старой версии (они себя оправдали) и почищены:

* нормализация регистра, ``ё``, Unicode и пунктуации;
* поиск точных фраз по границам слов (``пароль`` не совпадёт с ``беспарольный``);
* отказ при неоднозначности: если лучшая и вторая команда близки — не угадываем;
* обрезанная фраза («перезагрузи» без объекта) не срабатывает;
* отрицания («не выключай») не исполняются локально — их разбирает LLM.

Главное правило: распознанный текст только ВЫБИРАЕТ действие, он никогда не
становится частью команды.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import deque
from typing import Iterable, Sequence

from .types import Action, Intent, Skill

#: Слова-обращения и вежливые вставки: они не несут смысла для выбора команды.
FILLERS = {
    "джарвис", "jarvis", "пожалуйста", "ну", "а", "и", "потом", "затем",
    "покажи", "дай", "хочу", "можно", "можешь", "надо", "скажи", "будь", "мне",
}
NEGATIONS = {"не", "нет", "нельзя", "отмена", "отмени", "стоп"}

_SPLIT_RE = re.compile(r"\b(?:и затем|и потом|а потом|а затем|затем|потом|и)\b")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold().replace("ё", "е")
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def strip_fillers(text: str) -> str:
    words = normalize_text(text).split()
    while words and words[0] in FILLERS:
        words.pop(0)
    while words and words[-1] in FILLERS:
        words.pop()
    return " ".join(words)


def phrase_forms(phrase: str) -> list[str]:
    """Все написания фразы, которые считаем одинаковыми.

    Пользователь говорит «скажи который час», а в навыке записано «который час»;
    и наоборот — в навыке может стоять фраза с вежливым словом, а человек его
    опустит. Поэтому фразу и реплику приводим к одному виду: без обращения и
    вводных слов по краям.
    """
    forms: list[str] = []
    for form in (normalize_text(phrase), strip_fillers(phrase)):
        if form and form not in forms:
            forms.append(form)
    return forms


def has_negation(text: str) -> bool:
    return bool(set(normalize_text(text).split()) & NEGATIONS)


def phrase_spans(text: str, phrase: str):
    return ((match.start(), match.end()) for match in re.finditer(
        r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text))


def capture_tail(text: str, phrase: str) -> str | None:
    """Хвост исходной фразы после произнесённого триггера.

    Нормализация выбрасывает точки и цифры, а для аргументов они важны:
    «найди новости за 2026 год», «открой сайт example.com». Поэтому аргумент
    берём из исходного текста, сопоставляя слова по нормализованным формам.
    """
    raw = re.findall(r"\S+", text or "")
    normalized = [normalize_text(token) for token in raw]
    for form in phrase_forms(phrase):
        wanted = form.split()
        if not wanted:
            continue
        for start in range(len(normalized)):
            index, matched = start, 0
            while index < len(normalized) and matched < len(wanted):
                if normalized[index] == wanted[matched]:
                    index += 1
                    matched += 1
                elif normalized[index] in FILLERS:
                    index += 1
                else:
                    break
            if matched == len(wanted):
                tail_tokens = list(raw[index:])
                while tail_tokens and normalize_text(tail_tokens[0]) in FILLERS:
                    tail_tokens.pop(0)  # «найди мне погоду» → «погоду»
                tail = " ".join(tail_tokens).strip().strip(" .,!?;:")
                return tail or None
    return None


def _iter_actions(skills: Iterable[Skill]):
    for skill in skills:
        if not skill.enabled:
            continue
        for action in skill.actions:
            yield skill, action


def platform_allows(action: Action, platform: str) -> bool:
    return not action.platforms or platform in action.platforms


def find_exact(
    text: str,
    skills: Sequence[Skill],
    platform: str,
    *,
    max_extra_words: int = 6,
    allow_dangerous: bool = True,
) -> list[Intent]:
    """Все действия, чьи фразы целиком прозвучали в тексте, в порядке произнесения.

    Опасные действия находятся и здесь, но подтверждение запрашивает ядро
    (``core.executor``) — без него они не выполняются. Нечёткий поиск
    опасные действия игнорирует полностью.
    """
    normalized = strip_fillers(text)
    if not normalized:
        return []
    # «не» в самой команде запрещает выполнение, но «не» внутри текста сообщения —
    # это уже данные: «отправь Маме: не забудь про хлеб». Поэтому сначала ищем
    # совпадения, а решение об отрицании принимаем ниже, зная границы фраз.
    negated = has_negation(normalized)

    candidates: list[tuple[int, int, int, Skill, Action, str]] = []
    for skill, action in _iter_actions(skills):
        if not platform_allows(action, platform):
            continue
        for phrase in action.phrases:
            for phrase_norm in phrase_forms(phrase):
                for start, end in phrase_spans(normalized, phrase_norm):
                    candidates.append((start, end, len(phrase_norm), skill, action, phrase))
    if negated:
        # оставляем только фразы-захвата, у которых каждое отрицание стоит ПОСЛЕ
        # фразы, то есть внутри того, что мы примем как аргумент («текст письма»).
        # Смещения здесь символьные — phrase_spans отдаёт символы, не слова.
        pattern = r"(?<!\w)(?:" + "|".join(re.escape(word) for word in NEGATIONS) + r")(?!\w)"
        negation_positions = [match.start() for match in re.finditer(pattern, normalized)]
        candidates = [item for item in candidates
                      if item[4].capture and all(pos >= item[1] for pos in negation_positions)]
    if not candidates:
        return []

    # одна и та же фраза у разных действий — это неоднозначность, а не порядок в файле
    ownership: dict[tuple[int, int], set[str]] = {}
    for start, end, _, skill, action, _phrase in candidates:
        ownership.setdefault((start, end), set()).add(f"{skill.id}.{action.id}")
    if any(len(ids) > 1 for ids in ownership.values()):
        return []

    candidates.sort(key=lambda item: (-item[2], item[0]))
    chosen: list[tuple[int, int, Skill, Action, str]] = []
    for start, end, _, skill, action, phrase in candidates:
        if any(not (end <= c_start or start >= c_end) for c_start, c_end, _, _, _ in chosen):
            continue
        if not allow_dangerous and (action.is_dangerous or action.confirm):
            continue
        chosen.append((start, end, skill, action, phrase))

    if not chosen:
        return []

    # «отправь в телеграм Артёму: привет» — всё после фразы-захвата это текст
    # сообщения, поэтому совпавшие там слова не считаем отдельными командами
    # (иначе «привет» уводило бы фразу в приветствие). Подтверждение всё равно
    # показывает, что именно уйдёт, а опасные совпадения не отбрасываем.
    captures = [item for item in chosen if item[3].capture]
    if len(chosen) > 1 and len(captures) == 1:
        head = captures[0]
        tail = [item for item in chosen if item is not head and item[0] >= head[1]
                and not (item[3].is_dangerous or item[3].confirm)]
        if head[0] == min(item[0] for item in chosen) and len(tail) == len(chosen) - 1:
            chosen = [head]

    words_total = len(normalized.split())
    matched_words = sum(len(normalized[start:end].split()) for start, end, _, _, _ in chosen)
    if words_total - matched_words > max_extra_words:
        return []

    remainder = list(normalized)
    for start, end, _, _, _ in chosen:
        remainder[start:end] = " " * (end - start)
    leftover_text = " ".join("".join(remainder).split())
    leftovers = set(leftover_text.split()) - FILLERS

    capture: dict[str, str] = {}
    if leftovers or len(chosen) > 4:
        # Действие с capture принимает хвост фразы как аргумент: «найди погоду в Алматы»
        if len(chosen) == 1 and chosen[0][3].capture and leftovers:
            action, phrase = chosen[0][3], chosen[0][4]
            capture[action.id] = capture_tail(text, phrase) or leftover_text
        else:
            return []

    seen: set[str] = set()
    intents: list[Intent] = []
    for start, end, skill, action, _phrase in sorted(chosen, key=lambda item: item[0]):
        full_id = f"{skill.id}.{action.id}"
        if full_id in seen:
            continue
        seen.add(full_id)
        args = {}
        if action.id in capture:
            args[action.capture_field] = capture[action.id]
        intents.append(Intent(action=action, skill=skill, score=1.0, source="exact", text=text, args=args))
    return intents


def find_fuzzy(
    text: str,
    skills: Sequence[Skill],
    platform: str,
    *,
    threshold: float = 0.72,
    ambiguity_margin: float = 0.06,
) -> Intent | None:
    """Одно нечёткое совпадение — строго, с отказом при неоднозначности."""
    normalized = strip_fillers(text)
    if not normalized or has_negation(normalized):
        return None

    ranked: list[tuple[float, Skill, Action, str]] = []
    for skill, action in _iter_actions(skills):
        if not platform_allows(action, platform):
            continue
        if action.confirm or action.is_dangerous:
            continue  # опасное — только точной фразой и с подтверждением
        best = 0.0
        capture_text = ""
        for phrase in action.phrases:
            for phrase_norm in phrase_forms(phrase):
                if len(normalized.split()) < len(phrase_norm.split()):
                    continue
                if action.capture and (normalized == phrase_norm or normalized.startswith(phrase_norm + " ")):
                    best = 1.0
                    capture_text = normalized[len(phrase_norm):].strip()
                    break
                best = max(best, difflib.SequenceMatcher(None, normalized, phrase_norm, autojunk=False).ratio())
            if best == 1.0:
                break
        ranked.append((best, skill, action, capture_text))

    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    score, skill, action, capture_text = ranked[0]
    runner_up = next((value for value, other_skill, other, _ in ranked[1:]
                      if f"{other_skill.id}.{other.id}" != f"{skill.id}.{action.id}"), 0.0)
    limit = action.min_score if action.min_score is not None else threshold
    if score < limit or score - runner_up < ambiguity_margin:
        return None
    args = {action.capture_field: capture_text} if (action.capture and capture_text) else {}
    return Intent(action=action, skill=skill, score=score, source="fuzzy", text=text, args=args)


def plan_fuzzy_multi(
    text: str,
    skills: Sequence[Skill],
    platform: str,
    *,
    threshold: float = 0.72,
    ambiguity_margin: float = 0.06,
    max_clauses: int = 4,
) -> list[Intent]:
    """Несколько неточно распознанных команд, соединённых «и/потом/затем»."""
    if has_negation(text):
        return []
    clauses = _SPLIT_RE.split(normalize_text(text))
    if len(clauses) > max_clauses or any(not clause.strip() for clause in clauses):
        return []
    plan: list[Intent] = []
    seen: set[str] = set()
    for clause in clauses:
        # в одной реплике может быть и точная фраза, и неточная: «громче и патом тише»
        candidates = find_exact(clause, skills, platform)
        if not candidates:
            fuzzy = find_fuzzy(clause, skills, platform, threshold=threshold,
                               ambiguity_margin=ambiguity_margin)
            if fuzzy is None:
                return []
            candidates = [fuzzy]
        for intent in candidates:
            if intent.full_id not in seen:
                seen.add(intent.full_id)
                plan.append(intent)
    return plan


class SpeechBuffer:
    """Кадры VAD: требует устойчивой речи и сохраняет начало фразы (pre-roll)."""

    def __init__(self, start_frames: int = 3, min_frames: int = 6,
                 silence_frames: int = 30, preroll_frames: int = 10):
        self.start_frames = max(1, start_frames)
        self.min_frames = max(self.start_frames, min_frames)
        self.silence_frames = max(1, silence_frames)
        self.preroll: deque[bytes] = deque(maxlen=max(preroll_frames, self.start_frames))
        self.started = False
        self.frames: list[bytes] = []
        self.speech_frames = 0
        self.silence_run = 0

    def feed(self, frame: bytes, is_speech: bool) -> bool:
        """Возвращает True, когда запись закончена."""
        if not self.started:
            self.preroll.append(frame)
            if is_speech:
                self.speech_frames += 1
                if self.speech_frames >= self.start_frames:
                    self.started = True
                    self.frames = list(self.preroll)
                    self.silence_run = 0
            else:
                self.speech_frames = 0
            return False

        self.frames.append(frame)
        if is_speech:
            self.speech_frames += 1
            self.silence_run = 0
        else:
            self.silence_run += 1
        return self.silence_run >= self.silence_frames

    def valid(self) -> bool:
        return self.started and len(self.frames) >= self.min_frames
