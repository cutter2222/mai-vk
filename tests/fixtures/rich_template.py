"""Собственные синтетические шаблоны, построенные кодом, чтобы фикстуры не зависели от
бинарных файлов. `build_rich_template` — для анализа и планирования (титул, карточки с
иконками в группах, показатели, слайд-инструкция, каталог иконок, скрытый слайд; логотип на
всех слайдах); его состав закреплён записями replay модели, поэтому новые образцы живут в
`build_scheme_template` — для вёрстки (титул с декором макета справа, нумерованные этапы,
схема из блоков со стрелками)."""

from __future__ import annotations

import io
import pathlib

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Emu, Pt


def _png(color: tuple[int, int, int], size: int = 64, alpha: bool = False) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new(
        "RGBA" if alpha else "RGB", (size, size), (0, 0, 0, 0) if alpha else (255, 255, 255)
    )
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, size - 8, size - 8), fill=(*color, 255) if alpha else color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _add_layout_decoration(
    slide: object, layout: object, png: bytes, x: int, y: int, w: int, h: int
) -> None:
    """Картинка на макете: python-pptx не добавляет фигуры в макет, поэтому она создаётся
    на слайде, а её XML переносится в дерево макета со своей связью на медиа-часть."""
    import copy

    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    pic = slide.shapes.add_picture(io.BytesIO(png), Emu(x), Emu(y), Emu(w), Emu(h))  # type: ignore[attr-defined]
    blip = pic._element.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip")
    image_part = slide.part.related_part(
        blip.get(  # type: ignore[attr-defined]
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
        )
    )
    rid = layout.part.relate_to(image_part, RT.IMAGE)  # type: ignore[attr-defined]
    element = copy.deepcopy(pic._element)
    element.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip").set(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed", rid
    )
    layout.shapes._spTree.append(element)  # type: ignore[attr-defined]
    slide.shapes._spTree.remove(pic._element)  # type: ignore[attr-defined]


def build_rich_template(path: pathlib.Path) -> pathlib.Path:
    """Шаблон 16:9 из шести слайдов: титул, карточки с иконками в группах, показатели,
    слайд-инструкция, каталог иконок, скрытый слайд; логотип повторяется на всех слайдах."""
    prs = Presentation()
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    blank = prs.slide_layouts[6]
    logo = _png((0, 119, 255), 48)
    icon_blue = _png((0, 119, 255), 40, alpha=True)
    icon_white = _png((250, 250, 250), 40, alpha=True)

    def add_logo(slide: object) -> None:
        slide.shapes.add_picture(
            io.BytesIO(logo), Emu(11200000), Emu(6200000), Emu(500000), Emu(500000)
        )  # type: ignore[attr-defined]

    def textbox(
        slide: object, text: str, x: int, y: int, w: int, h: int, size: int, bold: bool = False
    ) -> object:
        tb = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))  # type: ignore[attr-defined]
        tb.text_frame.text = text
        run = tb.text_frame.paragraphs[0].runs[0]
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor(0x20, 0x20, 0x20)
        return tb

    # 1. титул
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Название презентации"
    s.placeholders[1].text = "Имя Фамилия, должность"
    add_logo(s)
    # 2. три карточки в группах: иконка + заголовок + текст
    s = prs.slides.add_slide(blank)
    textbox(s, "Заголовок в две или одну строчку", 600000, 400000, 11000000, 900000, 32, True)
    for i in range(3):
        grp = s.shapes.add_group_shape()
        x = 600000 + i * 3700000
        grp.shapes.add_picture(
            io.BytesIO(icon_blue), Emu(x), Emu(1700000), Emu(400000), Emu(400000)
        )
        t = grp.shapes.add_textbox(Emu(x), Emu(2200000), Emu(3300000), Emu(600000))
        t.text_frame.text = "Заголовок"
        t.text_frame.paragraphs[0].runs[0].font.size = Pt(20)
        b = grp.shapes.add_textbox(Emu(x), Emu(2900000), Emu(3300000), Emu(1800000))
        b.text_frame.text = "Текст описания карточки в несколько строк"
        b.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
    add_logo(s)
    # 3. показатели
    s = prs.slides.add_slide(blank)
    textbox(s, "Заголовок", 600000, 400000, 11000000, 900000, 32, True)
    for i in range(3):
        x = 600000 + i * 3700000
        textbox(s, "ххх%", x, 2000000, 3300000, 1200000, 54, True)
        textbox(s, "Описание показателя", x, 3300000, 3300000, 800000, 14)
    add_logo(s)
    # 4. инструкция по оформлению
    s = prs.slides.add_slide(blank)
    textbox(
        s,
        "Шрифт для заголовков — Play, кегль не менее 24. Используйте только цвета палитры: "
        "основной синий #0077FF, акцентный бирюзовый. Не используйте тени и линии сетки "
        "у диаграмм.",
        600000,
        800000,
        11000000,
        3000000,
        18,
    )
    add_logo(s)
    # 5. каталог иконок
    s = prs.slides.add_slide(blank)
    textbox(s, "Иконки", 600000, 300000, 5000000, 600000, 24, True)
    for i in range(16):
        s.shapes.add_picture(
            io.BytesIO(icon_white if i % 2 else _png((i * 15, 119, 255 - i * 10), 40, alpha=True)),
            Emu(600000 + (i % 8) * 1300000),
            Emu(1200000 + (i // 8) * 1300000),
            Emu(500000),
            Emu(500000),
        )
    add_logo(s)
    # 6. скрытый слайд
    s = prs.slides.add_slide(blank)
    textbox(s, "Черновик", 600000, 400000, 6000000, 800000, 20)
    s._element.set("show", "0")
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(path)
    return path


def build_scheme_template(path: pathlib.Path) -> pathlib.Path:
    """Шаблон 16:9 из четырёх слайдов: титул с декоративной картинкой на макете справа, четыре
    нумерованных этапа на общей линии, схема из трёх блоков со стрелками-соединителями и
    свободной линией, три тезиса под отдельными иконками (иконки и тексты — разные группы);
    логотип повторяется на всех слайдах."""
    prs = Presentation()
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    blank = prs.slide_layouts[6]
    logo = _png((0, 119, 255), 48)

    def add_logo(slide: object) -> None:
        slide.shapes.add_picture(
            io.BytesIO(logo), Emu(11200000), Emu(6200000), Emu(500000), Emu(500000)
        )  # type: ignore[attr-defined]

    def textbox(
        slide: object, text: str, x: int, y: int, w: int, h: int, size: int, bold: bool = False
    ) -> object:
        tb = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))  # type: ignore[attr-defined]
        tb.text_frame.text = text
        run = tb.text_frame.paragraphs[0].runs[0]
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor(0x20, 0x20, 0x20)
        return tb

    # 1. титул: заголовок во всю ширину, справа на макете большая декоративная картинка,
    #    как в фирменных шаблонах (декор макета, а не объект образца)
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Название презентации"
    s.placeholders[1].text = "Имя Фамилия, должность"
    _add_layout_decoration(
        s, prs.slide_layouts[0], _png((0, 119, 255), 512), 6400000, 600000, 5400000, 5400000
    )
    add_logo(s)
    # 2. нумерованные этапы: кружок с номером и пояснение ×4 на общей линии
    s = prs.slides.add_slide(blank)
    textbox(s, "Нумерация", 600000, 400000, 11000000, 900000, 32, True)
    s.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Emu(0), Emu(3600000), Emu(12192000), Emu(3600000)
    )
    for i in range(4):
        x = 600000 + i * 2850000
        circle = s.shapes.add_shape(MSO_SHAPE.OVAL, Emu(x), Emu(3200000), Emu(800000), Emu(800000))
        circle.text_frame.text = str(i + 1)
        circle.text_frame.paragraphs[0].runs[0].font.size = Pt(24)
        textbox(s, "Здесь можно добавить пояснение к этапу", x, 4200000, 2500000, 700000, 14)
    add_logo(s)
    # 3. схема: три блока в ряд, стрелки между ними привязаны к блокам, слева отдельная линия
    s = prs.slides.add_slide(blank)
    textbox(s, "Схема процесса", 600000, 400000, 11000000, 900000, 32, True)
    boxes = []
    for i in range(3):
        box = s.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Emu(900000 + i * 3800000),
            Emu(2600000),
            Emu(2400000),
            Emu(1200000),
        )
        box.text_frame.text = "Текстовый блок"
        box.text_frame.paragraphs[0].runs[0].font.size = Pt(16)
        boxes.append(box)
    for left, right in ((boxes[0], boxes[1]), (boxes[1], boxes[2])):
        arrow = s.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            left.left + left.width,
            left.top + left.height // 2,
            right.left,
            right.top + right.height // 2,
        )
        arrow.begin_connect(left, 3)
        arrow.end_connect(right, 1)
    # Свободная декоративная линия под заголовком: ни к чему не привязана, остаётся всегда.
    s.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Emu(600000), Emu(1500000), Emu(11600000), Emu(1500000)
    )
    add_logo(s)
    # 4. три тезиса под иконками: иконки и тексты не сгруппированы, тексты разной высоты
    s = prs.slides.add_slide(blank)
    textbox(s, "Заголовок — ключевая мысль слайда", 600000, 400000, 11000000, 900000, 32, True)
    for i in range(3):
        x = 600000 + i * 3700000
        s.shapes.add_picture(
            io.BytesIO(_png((0, 119, 255), 40, alpha=True)),
            Emu(x),
            Emu(2200000),
            Emu(450000),
            Emu(450000),
        )
        textbox(
            s,
            "Тезис под тематической иконкой (например, об идее проекта)",
            x,
            2900000,
            2900000,
            1200000 + (400000 if i == 2 else 0),
            14,
        )
    add_logo(s)

    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(path)
    return path
