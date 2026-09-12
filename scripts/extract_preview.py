# -*- coding: utf-8 -*-
"""探查：把相关 PDF 页文本 dump 到 data/_preview/ 便于定位内容位置。"""
import os
import pymupdf
import sys

BASE = r"c:\Users\123\Desktop\BeeEnglish"
OUT = os.path.join(BASE, "data", "_preview")
os.makedirs(OUT, exist_ok=True)

def dump(pdf, name, p0=0, p1=None, wmax=140):
    doc = pymupdf.open(os.path.join(BASE, pdf))
    end = len(doc) if p1 is None else min(p1, len(doc))
    out = []
    for i in range(p0, end):
        page = doc[i]
        text = page.get_text("text")
        out.append(f"\n===== {pdf}  page {i+1}/{len(doc)}  page_index={i} =====\n{text}")
    with open(os.path.join(OUT, name + ".txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"[ok] {name}: pages {len(doc)} -> txt {len(''.join(out))} chars")

if __name__ == "__main__":
    # 全书课本：先看 Unit1 可能在前面几页
    dump("Foundations SB.pdf", "sb_all")
    # 视频字幕 PDF
    dump("视频1字幕all_units_video_scripts.pdf", "video_scripts")
    # 词汇表 by unit
    dump("REx 3e Level F - Vocab List by Unit.pdf", "vocab_by_unit")