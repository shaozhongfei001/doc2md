"""生成用于「PDF 富文本 → AI 可用文本」验证的样例 PDF。

刻意包含四类结构：标题 / 中文正文（含数字与百分号）/ 带框线表格 / 位图插图。

要点：必须使用**真实 CJK 字体文件**（默认取系统 AR PL UMing）。
若改用 PyMuPDF 内置 ``china-s``，文本层会被写成逐字符定位，任何解析器读出来都会变成
``2 0 2 6 …``——那是造 PDF 的字体问题，不是解析器缺陷。

用法::

    /home/szf/anaconda3/bin/python make_demo_pdf.py /tmp/demo/rich_demo2.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

FONT_PATH = "/usr/share/fonts/truetype/arphic/uming.ttc"
PARAGRAPHS = [
    "本季度核心企业客户营收同比增长 12.4%，日均存款余额 8.95 亿元，较上季度提升 6.1%。",
    "用电量同比上升 9.8%，纳税开票金额同比上升 11.2%，两项交叉验证支持营收增长的真实性。",
    "供应链上下游集中度由 62% 降至 54%，但单一最大供应商占比仍达 23%，需持续监测替代可行性。",
]
TABLE_ROWS = [
    ("指标", "本期", "上期", "同比"),
    ("营业收入", "12480", "11104", "+12.4%"),
    ("日均存款", "89500", "84356", "+6.1%"),
    ("用电量(kWh)", "2310000", "2103900", "+9.8%"),
]
COL_WIDTHS = [140, 110, 110, 100]
ROW_HEIGHT = 24


def make_chart(path: Path) -> None:
    """生成一张折线图位图，作为 PDF 内的插图资产。"""
    image = Image.new("RGB", (640, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 620, 200], outline="black", width=2)
    draw.line([60, 180, 180, 120, 300, 150, 420, 70, 560, 90], fill="black", width=4)
    draw.text((24, 4), "Chart: quarterly revenue trend", fill="black")
    image.save(path)


def build(target: Path) -> Path:
    chart = target.with_name("chart.png")
    chart.parent.mkdir(parents=True, exist_ok=True)
    make_chart(chart)

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_font(fontname="cjk", fontfile=FONT_PATH)

    y = 60.0
    page.insert_text((60, y), "2026 Q3 供应链季度复核报告", fontsize=18, fontname="cjk")
    y += 30

    for paragraph in PARAGRAPHS:
        page.insert_text((60, y), paragraph, fontsize=11, fontname="cjk")
        y += 20

    y += 12
    page.insert_text((60, y), "表 1  主要经营指标（单位：万元）", fontsize=12, fontname="cjk")
    y += 10

    x0 = 60.0
    for row_index, row in enumerate(TABLE_ROWS):
        top = y + row_index * ROW_HEIGHT
        page.draw_line((x0, top), (x0 + sum(COL_WIDTHS), top), width=0.8)
        cursor = x0
        for column_index, cell in enumerate(row):
            page.insert_text((cursor + 5, top + 16), cell, fontsize=10, fontname="cjk")
            page.draw_line((cursor, top), (cursor, top + ROW_HEIGHT), width=0.8)
            cursor += COL_WIDTHS[column_index]
    bottom = y + len(TABLE_ROWS) * ROW_HEIGHT
    page.draw_line((x0, bottom), (x0 + sum(COL_WIDTHS), bottom), width=0.8)
    y = bottom + 24

    page.insert_text((60, y), "图 1  季度营收趋势", fontsize=12, fontname="cjk")
    page.insert_image(fitz.Rect(60, y + 10, 460, y + 170), filename=str(chart))

    doc.save(str(target))
    doc.close()
    return target


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "rich_demo2.pdf").resolve()
    build(out)
    print(f"已生成 {out}")
