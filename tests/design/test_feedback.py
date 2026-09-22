"""Правка плана по фактам собранного файла.

Факты берутся из `ComposedDeck` — описания того, что вёрстка действительно
поставила на слайд. Поэтому вёрстка в тестах не нужна: её заменяет функция,
которая раскладывает блоки плана по слотам. Проверяется не она, а выводы,
которые слой делает из результата.
"""

from __future__ import annotations

import json

from presentation_designer import design
from presentation_designer.design import feedback


def _slot(slot_id, kind, w, h, size_pt=12.0, sample=None):
    slot = {
        "slot_id": slot_id,
        "kind": kind,
        "bbox": {"x": 0.1, "y": 0.1, "width": w, "height": h},
        "font": {"size_pt": size_pt},
    }
    if sample:
        slot["sample_text"] = sample
    return slot


PATTERN = {
    "pattern_id": "cards_3",
    "role": "cards",
    "slots": [
        _slot("title_1", "title", 0.74, 0.14, 24.0),
        _slot("body_1", "body", 0.27, 0.16),
        _slot("body_2", "body", 0.27, 0.16),
        _slot("body_3", "body", 0.27, 0.16),
        _slot("caption_1", "caption", 0.02, 0.01, 7.0),  # точка на схеме
    ],
}
PROFILE = {
    "patterns": [PATTERN],
    "slide_size": {"width_emu": 9144000, "height_emu": 5143500},
    "placeholder_markers": ["Заголовок"],
}
SLOTS = {s["slot_id"]: s for s in PATTERN["slots"]}


def _plan(blocks):
    return {
        "slides": [
            {
                "slide_id": "s7",
                "pattern_id": "cards_3",
                "role": "cards",
                "title": "Итоги пилота",
                "blocks": [
                    {"slot_id": sid, "kind": SLOTS[sid]["kind"], "text": text}
                    for sid, text in blocks
                ],
            }
        ]
    }


def _deck(plan, *, autofit="none"):
    """Вёрстка-двойник: ставит на слайд ровно то, что дал план."""
    slide = plan["slides"][0]
    objects = []
    for index, block in enumerate(slide["blocks"], start=1):
        slot = SLOTS[block["slot_id"]]
        objects.append(
            {
                "object_id": str(index),
                "kind": "text",
                "slot_id": block["slot_id"],
                "bbox": dict(slot["bbox"]),
                "text": {
                    "plain": block["text"],
                    "autofit": autofit,
                    "computed_style": {"font": dict(slot["font"])},
                },
            }
        )
    return {
        "slide_size": PROFILE["slide_size"],
        "slides": [{"slide_id": "s7", "pattern_id": "cards_3", "objects": objects}],
    }


# -- чтение фактов -----------------------------------------------------------


def test_дозапрос_по_сборке_сохраняет_ссылки_на_факты_тезиса():
    plan = _plan([("title_1", "Итоги пилота")])
    plan["slides"][0]["thesis_refs"] = ["t1"]
    story = {
        "theses": [
            {
                "thesis_id": "t1",
                "statement": "Простой снижен на {fact:f1}",
                "source_refs": ["block_1"],
                "fact_refs": ["f1"],
            }
        ]
    }
    captured = []

    def ask(_system, user):
        captured.extend(json.loads(user)["slides"])
        return "{}"

    facts = feedback.read(_deck(plan), plan, PROFILE)
    feedback.ask_slots(plan, facts, PROFILE, story, ask, rounds=1)
    assert captured[0]["fact_refs"] == ["f1"]
    assert captured[0]["material"] == ["Простой снижен на {fact:f1}"]


def test_ряд_без_единой_подписи_считается_дырой():
    """Три пустые карточки из трёх — грубый дефект, а не низкая плотность."""
    plan = _plan([("title_1", "Итоги пилота")])
    facts = feedback.read(_deck(plan), plan, PROFILE)

    assert [f.kind for f in facts.items] == ["hole"] * 3
    assert facts.score() == 9.0


def test_пустая_карточка_среди_заполненных_остаётся_дырой():
    """Ряд одинаковых карточек обязан быть полным: пустая читается недоделкой."""
    plan = _plan(
        [("title_1", "Итоги"), ("body_1", "Простой снижен"), ("body_2", "Выручка выросла")]
    )
    facts = feedback.read(_deck(plan), plan, PROFILE)

    assert [f.kind for f in facts.items] == ["hole"]


def test_недописанный_пункт_списка_стоит_дешевле():
    """Список из двух пунктов в макете на три — не дефект, а короткий список."""
    agenda = {**PATTERN, "pattern_id": "agenda_3", "role": "agenda"}
    profile = {**PROFILE, "patterns": [agenda]}
    plan = _plan([("title_1", "Содержание"), ("body_1", "Итоги пилота"), ("body_2", "Масштаб")])
    plan["slides"][0]["pattern_id"] = "agenda_3"
    deck = _deck(plan)
    deck["slides"][0]["pattern_id"] = "agenda_3"
    facts = feedback.read(deck, plan, profile)

    assert [f.kind for f in facts.items] == ["gap"]
    assert facts.score() == 1.0


def test_ряд_узнаётся_по_одинаковому_размеру_слотов():
    """Подписи разного размера на схеме рядом не являются."""
    assert {"body_1", "body_2", "body_3"} in feedback.rows(PATTERN)
    scattered = {
        "pattern_id": "map",
        "role": "cards",
        "slots": [
            _slot("body_1", "body", 0.14, 0.03),
            _slot("body_2", "body", 0.22, 0.05),
            _slot("body_3", "body", 0.06, 0.02),
        ],
    }
    assert feedback.rows(scattered) == []


def test_заголовки_и_описания_карточек_считаются_разными_рядами():
    """В «Карточках × 2» восемь слотов `body`: четыре заголовка и четыре описания."""
    cards = {
        "pattern_id": "cards_2",
        "role": "cards",
        "slots": [
            _slot("body_1", "body", 0.27, 0.05),
            _slot("body_2", "body", 0.27, 0.05),
            _slot("body_3", "body", 0.27, 0.16),
            _slot("body_4", "body", 0.27, 0.16),
        ],
    }
    assert sorted(len(r) for r in feedback.rows(cards)) == [2, 2]


def test_слот_под_оформление_пустым_не_считается():
    """В подпись на два знака осмысленного текста не поставить."""
    plan = _plan([("title_1", "Итоги")])
    facts = feedback.read(_deck(plan), plan, PROFILE)

    assert "caption_1" not in {f.slot_id for f in facts.items}


def test_выход_за_рамку_в_пустоту_дефектом_не_считается():
    """Рамка объявлена в три строки, а места под ней — на десять: это приём."""
    long_text = "Платформа обеспечивает рост доходов участников логистической цепи " * 3
    plan = _plan([("title_1", "Итоги"), ("body_1", long_text)])
    facts = feedback.read(_deck(plan, autofit="none"), plan, PROFILE)

    assert {f.kind for f in facts.items if f.slot_id == "body_1"} == {"spill"}


def test_наезд_на_соседа_считается_переполнением():
    """Тот же текст, но под ним стоит другой: это уже видимый дефект."""
    long_text = "Платформа обеспечивает рост доходов участников логистической цепи " * 3
    plan = _plan([("title_1", "Итоги"), ("body_1", long_text)])
    deck = _deck(plan, autofit="none")
    box = deck["slides"][0]["objects"][1]["bbox"]
    deck["slides"][0]["objects"].append(
        {
            "object_id": "99",
            "kind": "text",
            "slot_id": "body_2",
            "role": "content",
            "bbox": {"x": box["x"], "y": box["y"] + box["height"], "width": 0.27, "height": 0.16},
            "text": {"plain": "Соседняя карточка", "computed_style": {"font": {"size_pt": 12.0}}},
        }
    )
    facts = feedback.read(deck, plan, PROFILE)

    assert "overflow" in {f.kind for f in facts.items if f.slot_id == "body_1"}


def test_счёт_различает_дыру_и_недобор():
    assert feedback.WEIGHTS["hole"] > feedback.WEIGHTS["gap"]


# -- подписи из фактов и сокращение -----------------------------------------


def test_подпись_собирается_из_факта_пакета():
    labels = feedback.fact_labels(
        {"facts": [{"raw": "3400", "context": {"metric": "рейсов", "period": "в месяц"}}]}
    )
    assert "3400 рейсов" in labels


def test_сокращение_берёт_кусок_с_числом_и_без_предлога_по_краям():
    """«на 34%» и «дней до 1 дня» — обрубки, такие подписи не годятся."""
    slot = _slot("body_1", "body", 0.14, 0.03, 13.2, sample="Заголовок")
    short = feedback.condense("Простой снижен на 34% за полгода пилота", slot)

    assert short is not None
    assert not short.lower().startswith(("на ", "до ", "с "))
    assert feedback.condense("", slot) is None


def test_вместимость_не_меньше_образца_шаблона():
    """Рамка, куда дизайнер поставил слово, вмещает как минимум слово."""
    from presentation_designer.design.measure import canvas_of, max_chars

    canvas = canvas_of(PROFILE)
    tight = _slot("body_1", "body", 0.14, 0.03, 13.2)
    assert max_chars(tight, canvas) in (None, 0)
    assert (max_chars({**tight, "sample_text": "Заголовок"}, canvas) or 0) >= 8


# -- ряд целиком -------------------------------------------------------------


def test_неполный_ряд_снимается_целиком():
    """Две подписанные карточки из трёх выглядят недоделкой."""
    plan = _plan([("title_1", "Итоги")])
    facts = feedback.read(_deck(plan), plan, PROFILE)
    slide = plan["slides"][0]
    slide["blocks"].append(
        {"slot_id": "body_1", "kind": "body", "text": "Простой снижен", "source": "design.refill"}
    )

    assert feedback.drop_partial_rows(plan, facts) == 1
    assert [b["slot_id"] for b in slide["blocks"]] == ["title_1"]


def test_полный_ряд_остаётся():
    plan = _plan([("title_1", "Итоги")])
    facts = feedback.read(_deck(plan), plan, PROFILE)
    slide = plan["slides"][0]
    row = (("body_1", "Простой −34%"), ("body_2", "Выручка +18%"), ("body_3", "3400 рейсов"))
    for slot_id, text in row:
        slide["blocks"].append(
            {"slot_id": slot_id, "kind": "body", "text": text, "source": "design.refill"}
        )

    assert feedback.drop_partial_rows(plan, facts) == 0
    assert len(slide["blocks"]) == 4


# -- проход целиком ----------------------------------------------------------


def test_правка_принимается_только_когда_дефектов_стало_меньше():
    """Проверка той же меркой, которой найден дефект."""
    plan = _plan([("title_1", "Итоги пилота")])
    package = {
        "facts": [
            {"raw": "34%", "context": {"metric": "Простой"}},
            {"raw": "18%", "context": {"metric": "Выручка"}},
            {"raw": "3400", "context": {"metric": "рейсов"}},
        ]
    }

    def ask(_system, user):
        return (
            '{"s7": {"body_1": "Простой −34%", "body_2": "Выручка +18%", "body_3": "3400 рейсов"}}'
        )

    fixed, report = design.polish(
        plan, PROFILE, {}, compose=_deck, variant="balanced", ask=ask, package=package
    )

    assert report["verdict"] == "правка принята"
    assert report["after"]["score"] < report["before"]["score"]
    assert len(fixed["slides"][0]["blocks"]) == 4
    assert plan["slides"][0]["blocks"] == [
        {"slot_id": "title_1", "kind": "title", "text": "Итоги пилота"}
    ]  # исходный план не тронут


def test_бесполезная_правка_откатывается():
    """Модель ответила пустотой — план остаётся прежним."""
    plan = _plan([("title_1", "Итоги пилота")])

    def ask(_system, user):
        return '{"s7": {"body_1": "", "body_2": "", "body_3": ""}}'

    fixed, report = design.polish(plan, PROFILE, {}, compose=_deck, ask=ask)

    assert fixed == plan
    assert report["verdict"] in ("чинить нечем", "правка отклонена: лучше не стало")


def test_ошибка_модели_не_роняет_проход():
    plan = _plan([("title_1", "Итоги пилота")])

    def ask(_system, user):
        raise RuntimeError("шлюз недоступен")

    fixed, report = design.polish(plan, PROFILE, {}, compose=_deck, ask=ask)

    assert fixed["slides"][0]["blocks"][0]["text"] == "Итоги пилота"
    assert report["before"]["by_kind"]["hole"] == 3


def test_заголовок_сокращается_по_придаточной_части():
    """Одно предложение всё равно можно укоротить: по запятой или предлогу."""
    slot = _slot("title_1", "title", 0.56, 0.15, 24.0)
    long_title = (
        "Платформа демонстрирует высокую эффективность в оптимизации логистики для среднего бизнеса"
    )
    short = feedback._fit_text(long_title, slot, None)

    assert short is not None and len(short) < len(long_title)
    assert short.startswith("Платформа демонстрирует")


def test_одинокое_прилагательное_не_принимается_как_подпись():
    """«Высокая» в карточке не сообщает ничего: это обрывок фразы."""
    from presentation_designer.design.refill import _dangling

    assert _dangling("Высокая")
    assert not _dangling("Согласование")
    assert not _dangling("Простой −34%")


def test_ответ_модели_приводится_в_порядок():
    """Удвоенная единица и обрыв на предлоге — то, что видно на снимке."""
    from presentation_designer.design.refill import tidy

    facts = {"f1": {"raw": "34%", "unit": "%"}, "f3": {"raw": "120", "unit": ""}}
    assert tidy("на {fact:f1}%", facts) == "на {fact:f1}"
    assert tidy("Самый быстрорастущий сегмент бизнеса в с", facts) == (
        "Самый быстрорастущий сегмент бизнеса"
    )
    assert tidy("{fact:f3} перевозчиков", facts) == "{fact:f3} перевозчиков"


def test_не_поместившийся_текст_переписывается_короче():
    """Замена принимается, только если числа сохранены и строка влезла."""
    from presentation_designer.design.refill import apply_answer

    slot = _slot("body_1", "body", 0.27, 0.05)
    pattern = {"pattern_id": "p", "role": "cards", "slots": [slot]}
    plan = {
        "slides": [
            {
                "slide_id": "s1",
                "pattern_id": "p",
                "blocks": [
                    {
                        "slot_id": "body_1",
                        "kind": "body",
                        "text": "Согласование сократилось с 5 дней до 1 дня по всем маршрутам",
                    }
                ],
            }
        ]
    }
    replace = {("s1", "body_1")}

    lost = json.loads('{"s1": {"body_1": "Согласование быстрее"}}')
    assert apply_answer(plan, {"p": pattern}, lost, set(), None, None, replace) == []

    kept = json.loads('{"s1": {"body_1": "Согласование: 5 дней → 1"}}')
    assert apply_answer(plan, {"p": pattern}, kept, set(), None, None, replace) == [
        ("s1", "body_1")
    ]
    assert plan["slides"][0]["blocks"][0]["text"] == "Согласование: 5 дней → 1"


def test_сокращение_не_имеет_права_выесть_смысл():
    """«Итоги пилота логистической платформы» → «Итоги» — это не сокращение."""
    from presentation_designer.design.refill import _keeps_meaning

    full = "Итоги пилота логистической платформы"
    assert not _keeps_meaning(full, "Итоги")
    assert _keeps_meaning(full, "Итоги пилота")
    assert not _keeps_meaning("Согласование сократилось с 5 дней до 1", "Быстрее стало")


def test_заголовок_обложки_укорачивается_по_словам_а_не_до_одного():
    slot = _slot("title_1", "title", 0.47, 0.15, 48.0)
    short = feedback._fit_text("Итоги пилота логистической платформы", slot, None)

    assert short is None or short.startswith("Итоги пилота")


def test_запертый_слайд_полировка_не_трогает():
    """Слайд с ручными правками редактора (этап 22): его дыры не читаются, сам слайд
    возвращается из исходного плана, соседний по-прежнему чинится."""
    plan = _plan([("title_1", "Итоги пилота")])
    plan["slides"][0]["overrides"] = [{"op": "text", "target": {"object_id": "1"}, "text": "x"}]
    second = json.loads(json.dumps(plan["slides"][0]))
    second["slide_id"] = "s8"
    second.pop("overrides")
    plan["slides"].append(second)
    package = {
        "facts": [
            {"raw": "34%", "context": {"metric": "Простой"}},
            {"raw": "18%", "context": {"metric": "Выручка"}},
            {"raw": "3400", "context": {"metric": "рейсов"}},
        ]
    }

    def compose(doc):
        decks = [_deck({"slides": [s]}) for s in doc["slides"]]
        return {
            "slide_size": PROFILE["slide_size"],
            "slides": [
                {**d["slides"][0], "slide_id": s["slide_id"]}
                for d, s in zip(decks, doc["slides"], strict=True)
            ],
        }

    def ask(_system, user):
        return (
            '{"s7": {"body_1": "Простой −34%", "body_2": "Выручка +18%", "body_3": "3400 рейсов"},'
            ' "s8": {"body_1": "Простой −34%", "body_2": "Выручка +18%", "body_3": "3400 рейсов"}}'
        )

    fixed, report = design.polish(
        plan,
        PROFILE,
        {},
        compose=compose,
        variant="balanced",
        ask=ask,
        package=package,
        locked_slide_ids={"s7"},
    )

    assert report["locked_slide_ids"] == ["s7"]
    assert report["verdict"] == "правка принята"
    by_id = {s["slide_id"]: s for s in fixed["slides"]}
    assert by_id["s7"] == plan["slides"][0], "запертый слайд вернулся нетронутым"
    assert len(by_id["s8"]["blocks"]) == 4, "соседний слайд починен"
