#!/usr/bin/env python3
"""
Batch extract wrong math questions from photos and export to Markdown.

Usage:
  python extract_mistakes.py \
    --input-dir photos \
    --output-md output/mistakes.md \
    --assets-dir output/assets
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "wrong_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_no": {"type": "string"},
                    "question_text": {"type": "string"},
                    "student_answer": {"type": "string"},
                    "correct_answer": {"type": "string"},
                    "error_reason": {"type": "string"},
                    "has_figure": {"type": "boolean"},
                    "question_bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "figure_bboxes": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                    },
                },
                "required": [
                    "question_no",
                    "question_text",
                    "student_answer",
                    "correct_answer",
                    "error_reason",
                    "has_figure",
                    "question_bbox",
                    "figure_bboxes",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["wrong_questions"],
    "additionalProperties": False,
}


PROMPT = """你是一位小学数学错题整理助手。请识别照片中的“做错的题”，并只输出 JSON。

严格要求：
1) 只提取明确可见的错题（如有老师红叉、扣分、错号，或作答与题意明显不符）。
2) 不要编造看不清的内容；看不清就填空字符串 ""。
3) 输出字段必须与给定 schema 一致。
4) bbox 使用归一化坐标 [x1, y1, x2, y2]，范围 0~1，基于“原图”。
5) question_bbox 是整道题区域；figure_bboxes 只放图形/示意图区域（可多个）。
6) 如果题目没有图形，has_figure=false 且 figure_bboxes=[]。
7) question_no 可填题号（如 "5"），看不清可用 ""。
"""


ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass
class WrongQuestion:
    question_no: str
    question_text: str
    student_answer: str
    correct_answer: str
    error_reason: str
    has_figure: bool
    question_bbox: list[float]
    figure_bboxes: list[list[float]]


def env_first(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return default


def resolve_api_key() -> str:
    return env_first("OPENAI_API_KEY")


def resolve_base_url() -> str:
    return env_first("OPENAI_BASE_URL", "BASE_URL", "base_url")


def resolve_model(default: str = "gpt-4.1-mini") -> str:
    return env_first("MODEL", "model", "MISTAKE_MODEL", default=default)


def create_openai_client() -> OpenAI:
    api_key = resolve_api_key()
    base_url = resolve_base_url()
    kwargs: dict[str, str] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract wrong questions to Markdown.")
    parser.add_argument("--input-dir", default="photos", help="Folder with input images.")
    parser.add_argument("--output-md", default="output/mistakes.md", help="Output markdown file.")
    parser.add_argument("--assets-dir", default="output/assets", help="Directory for extracted figure crops.")
    parser.add_argument("--model", default=resolve_model("gpt-4.1-mini"), help="Vision-capable model name.")
    parser.add_argument(
        "--detail",
        default="high",
        choices=["low", "high", "auto"],
        help="Image detail level for input_image.",
    )
    parser.add_argument("--max-images", type=int, default=0, help="Only process first N images; 0 means all.")
    return parser.parse_args()


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    sys.exit(1)


def encode_image_as_data_url(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "image/jpeg"
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        snippet = text[left : right + 1]
        return json.loads(snippet)
    raise ValueError("Model output is not valid JSON.")


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def sanitize_bbox(bbox: list[float]) -> list[float]:
    if len(bbox) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    x1, y1, x2, y2 = [clamp01(float(v)) for v in bbox]
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return [x1, y1, x2, y2]


def crop_by_norm_bbox(image_path: Path, norm_bbox: list[float], out_path: Path) -> bool:
    if Image is None:
        return False
    bbox = sanitize_bbox(norm_bbox)
    x1, y1, x2, y2 = bbox
    with Image.open(image_path) as img:
        width, height = img.size
        left = int(x1 * width)
        top = int(y1 * height)
        right = int(x2 * width)
        bottom = int(y2 * height)
        if right - left < 8 or bottom - top < 8:
            return False
        cropped = img.crop((left, top, right, bottom))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(out_path)
        return True


def to_wrong_questions(data: dict[str, Any]) -> list[WrongQuestion]:
    rows = data.get("wrong_questions", [])
    out: list[WrongQuestion] = []
    for row in rows:
        out.append(
            WrongQuestion(
                question_no=str(row.get("question_no", "")).strip(),
                question_text=str(row.get("question_text", "")).strip(),
                student_answer=str(row.get("student_answer", "")).strip(),
                correct_answer=str(row.get("correct_answer", "")).strip(),
                error_reason=str(row.get("error_reason", "")).strip(),
                has_figure=bool(row.get("has_figure", False)),
                question_bbox=sanitize_bbox(row.get("question_bbox", [0, 0, 0, 0])),
                figure_bboxes=[sanitize_bbox(b) for b in row.get("figure_bboxes", [])],
            )
        )
    return out


def analyze_one_image(client: OpenAI, image_path: Path, model: str, detail: str) -> list[WrongQuestion]:
    data_url = encode_image_as_data_url(image_path)
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": PROMPT},
                        {"type": "input_image", "image_url": data_url, "detail": detail},
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "wrong_questions_extract",
                    "strict": True,
                    "schema": SCHEMA,
                }
            },
        )
        payload = extract_json(response.output_text or "")
        return to_wrong_questions(payload)
    except Exception as resp_exc:
        fallback_prompt = (
            PROMPT
            + "\n请严格只输出 JSON 对象，顶层键名必须是 wrong_questions，"
            + "并且字段名与要求完全一致。"
        )
        message = [
            {"type": "text", "text": fallback_prompt},
            {"type": "image_url", "image_url": {"url": data_url, "detail": detail}},
        ]

        try:
            chat = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": message}],
                response_format={"type": "json_object"},
                temperature=0,
            )
        except Exception:
            chat = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": message}],
                temperature=0,
            )

        content = chat.choices[0].message.content or ""
        payload = extract_json(content)
        try:
            return to_wrong_questions(payload)
        except Exception as chat_exc:  # pragma: no cover
            raise RuntimeError(
                f"responses+chat_completions parse failed: {resp_exc}; {chat_exc}"
            ) from chat_exc


def relative_path(from_path: Path, target: Path) -> str:
    return os.path.relpath(target, from_path.parent).replace("\\", "/")


def build_markdown(
    input_dir: Path,
    output_md: Path,
    image_to_questions: dict[Path, list[WrongQuestion]],
    figure_paths: dict[tuple[Path, int], list[Path]],
) -> str:
    lines: list[str] = []
    lines.append("# 数学错题整理")
    lines.append("")
    lines.append(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 图片目录: `{input_dir}`")
    lines.append("")

    total = sum(len(v) for v in image_to_questions.values())
    lines.append(f"共识别到 **{total}** 道错题。")
    lines.append("")

    for image_path, questions in image_to_questions.items():
        lines.append(f"## 图片 `{image_path.name}`")
        lines.append("")
        if not questions:
            lines.append("- 未识别到明确错题。")
            lines.append("")
            continue

        for idx, q in enumerate(questions, start=1):
            title_no = f"（题号: {q.question_no}）" if q.question_no else ""
            lines.append(f"### 错题 {idx}{title_no}")
            lines.append("")
            lines.append(f"- 题干: {q.question_text or '（未识别清楚）'}")
            lines.append(f"- 孩子作答: {q.student_answer or '（未识别清楚）'}")
            lines.append(f"- 参考正确答案: {q.correct_answer or '（无法判断）'}")
            lines.append(f"- 错因简述: {q.error_reason or '（未给出）'}")

            figs = figure_paths.get((image_path, idx), [])
            if figs:
                lines.append("- 题目图形:")
                for p in figs:
                    rel = relative_path(output_md, p)
                    lines.append(f"  - ![]({rel})")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def list_images(input_dir: Path) -> list[Path]:
    paths = []
    for p in sorted(input_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES:
            paths.append(p)
    return paths


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_md = Path(args.output_md)
    assets_dir = Path(args.assets_dir)

    if not input_dir.exists() or not input_dir.is_dir():
        fail(f"input dir not found: {input_dir}")
    if not resolve_api_key():
        fail("OPENAI_API_KEY is not set.")
    if OpenAI is None:
        fail("openai package is required. Please install dependencies: pip install -r requirements.txt")
    if Image is None:
        fail("Pillow is required. Please install dependencies: pip install -r requirements.txt")

    images = list_images(input_dir)
    if not images:
        fail(f"no image files found in: {input_dir}")
    if args.max_images > 0:
        images = images[: args.max_images]

    output_md.parent.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    client = create_openai_client()
    image_to_questions: dict[Path, list[WrongQuestion]] = {}
    figure_paths: dict[tuple[Path, int], list[Path]] = {}

    for image_path in images:
        print(f"[INFO] analyzing {image_path.name}")
        try:
            questions = analyze_one_image(client, image_path, args.model, args.detail)
        except Exception as exc:  # pragma: no cover
            print(f"[WARN] failed on {image_path.name}: {exc}", file=sys.stderr)
            questions = []
        image_to_questions[image_path] = questions

        for idx, q in enumerate(questions, start=1):
            out_paths: list[Path] = []
            for fig_idx, bbox in enumerate(q.figure_bboxes, start=1):
                crop_name = f"{image_path.stem}_q{idx}_fig{fig_idx}.png"
                crop_path = assets_dir / crop_name
                ok = crop_by_norm_bbox(image_path, bbox, crop_path)
                if ok:
                    out_paths.append(crop_path)
            figure_paths[(image_path, idx)] = out_paths

    md = build_markdown(input_dir, output_md, image_to_questions, figure_paths)
    output_md.write_text(md, encoding="utf-8")
    print(f"[OK] markdown saved to: {output_md}")
    print(f"[OK] figure assets saved to: {assets_dir}")


if __name__ == "__main__":
    main()
