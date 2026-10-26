"""Local, flattened PDF watermarks. Run with Python 3 or the bundled launcher."""
from __future__ import annotations

import argparse
from datetime import datetime
from io import BytesIO
import json
import math
import os
from pathlib import Path
import sys
import tempfile

try:
    import fitz
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:
    raise SystemExit(
        "缺少依赖。请双击【双击运行.bat】，或运行：\n"
        'py -3 -m pip install -r "' + str(Path(__file__).with_name("requirements.txt")) + '"'
    ) from exc


HERE = Path(__file__).resolve().parent
MAX_PAGE_PIXELS = 60_000_000
DEFAULTS = {
    "primary_line": "资料来源：@橙子同学",
    "secondary_line": "仅供个人学习，请勿转售。侵权必究。",
    "opacity": 0.12,
    "angle": 28,
    "color": "#536D91",
    "raster_dpi": 300,
    "font_path": "",
}


def load_settings(path: Path) -> dict:
    config = DEFAULTS.copy()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("配置文件必须是 JSON 对象。")
        unknown = set(data) - set(DEFAULTS)
        if unknown:
            raise ValueError("不认识的配置项：" + ", ".join(sorted(unknown)))
        config.update(data)
    for key in ("primary_line", "secondary_line"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f"{key} 必须是非空文字。")
        if "\n" in config[key] or "\r" in config[key]:
            raise ValueError(f"{key} 请只填写一行文字。")
    for key, low, high in (("opacity", 0.01, 1), ("angle", -89, 89), ("raster_dpi", 72, 600)):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"{key} 必须是 {low} 到 {high} 之间的数字。")
    color = config["color"]
    if not isinstance(color, str) or len(color) != 7 or not color.startswith("#"):
        raise ValueError("color 请填写 #536D91 这样的六位颜色代码。")
    try:
        tuple(int(color[n:n + 2], 16) for n in (1, 3, 5))
    except ValueError as exc:
        raise ValueError("color 不是有效的十六进制颜色。") from exc
    if not isinstance(config["font_path"], str):
        raise ValueError("font_path 必须是字体文件路径，或空字符串。")
    return config


def find_font(config: dict) -> Path:
    if config["font_path"]:
        path = Path(config["font_path"]).expanduser()
        if not path.is_absolute():
            path = HERE / path
        if not path.is_file():
            raise ValueError(f"找不到配置的字体：{path}")
        return path
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyh.ttf", "simhei.ttf", "simsun.ttc"):
        path = fonts / name
        if path.is_file():
            return path
    raise ValueError("找不到中文字体。请在配置文件的 font_path 中填写中文字体的完整路径。")


def select_inputs() -> list[Path]:
    import tkinter as tk
    from tkinter import filedialog

    selected: list[Path] = []
    root = tk.Tk()
    root.title("橙子同学 · PDF 水印工具")
    root.geometry("500x205")
    root.resizable(False, False)
    root.eval("tk::PlaceWindow . center")
    tk.Label(root, text="给 PDF 添加浅色中央水印", font=("Microsoft YaHei", 15)).pack(pady=(22, 8))
    tk.Label(root, text="结果另存到新文件夹，保留原文件。", font=("Microsoft YaHei", 10)).pack()
    row = tk.Frame(root)
    row.pack(pady=20)

    def choose(folder: bool) -> None:
        root.withdraw()
        if folder:
            value = filedialog.askdirectory(title="选择包含 PDF 的文件夹", parent=root)
            paths = [value] if value else []
        else:
            paths = filedialog.askopenfilenames(
                title="选择 PDF，可按住 Ctrl 多选", parent=root,
                filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
            )
        if paths:
            selected.extend(Path(p) for p in paths)
            root.destroy()
        else:
            root.deiconify()

    tk.Button(row, text="选择 PDF（可多选）", width=20, command=lambda: choose(False)).pack(side="left", padx=8)
    tk.Button(row, text="选择整个文件夹", width=20, command=lambda: choose(True)).pack(side="left", padx=8)
    root.mainloop()
    return selected


def collect_pdfs(inputs: list[Path]) -> list[Path]:
    result = []
    seen = set()
    for value in inputs:
        value = value.expanduser().resolve()
        if value.is_dir():
            candidates = sorted(p for p in value.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        elif value.is_file() and value.suffix.lower() == ".pdf":
            candidates = [value]
        else:
            raise ValueError(f"不是有效的 PDF 文件或文件夹：{value}")
        for candidate in candidates:
            key = os.path.normcase(str(candidate.resolve()))
            if key not in seen:
                seen.add(key)
                result.append(candidate)
    return result


def make_output_dir(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    stem = "已加水印_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    for index in range(10000):
        path = parent / (stem if not index else f"{stem}_{index}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            continue
    raise ValueError("无法创建独立输出文件夹。")


def destination_for(source: Path, folder: Path) -> Path:
    stem = source.stem + "_已加水印"
    path = folder / (stem + ".pdf")
    index = 2
    while path.exists():
        path = folder / f"{stem}_{index}.pdf"
        index += 1
    return path


def page_image(page, dpi: float) -> tuple[Image.Image, str]:
    """Extract simple full-page images at native size; render complex pages."""
    images = page.get_image_info(xrefs=True)
    if (len(images) == 1 and page.rotation == 0
            and not page.get_text("text").strip() and not page.get_drawings()
            and page.first_annot is None and page.first_widget is None):
        info = images[0]
        bbox = fitz.Rect(info["bbox"])
        transform = info["transform"]
        full_page = max(abs(a - b) for a, b in zip(bbox, page.rect)) < 0.5
        upright = transform[0] > 0 and transform[3] > 0 and abs(transform[1]) < 0.001 and abs(transform[2]) < 0.001
        if info["xref"] > 0 and full_page and upright:
            raw = page.parent.extract_image(info["xref"])
            if raw and not raw.get("smask"):
                if raw["width"] * raw["height"] > MAX_PAGE_PIXELS:
                    raise ValueError("原图超过 6000 万像素，无法安全处理这一页。")
                with Image.open(BytesIO(raw["image"])) as im:
                    # CMYK conversion is best handled by the PDF renderer.
                    if im.mode in ("RGB", "L"):
                        return im.convert("RGB"), "保留原图像素"
    scale = dpi / 72
    pixels = math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
    if pixels > MAX_PAGE_PIXELS:
        raise ValueError("这一页按当前分辨率渲染超过 6000 万像素；请调低 raster_dpi 后重试。")
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False, annots=True)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), f"{dpi:g} PPI 渲染"


def apply_watermark(image: Image.Image, config: dict, font_path: Path) -> Image.Image:
    width, height = image.size
    ss = 3
    lines = (config["primary_line"], config["secondary_line"])
    fonts = [ImageFont.truetype(str(font_path), max(1, round(width * size * ss))) for size in (0.034, 0.023)]
    stamp_width = math.ceil(max(font.getlength(line) for font, line in zip(fonts, lines))) + 48 * ss
    stamp = Image.new("RGBA", (stamp_width, max(110 * ss, round(width * 0.105 * ss))), (0, 0, 0, 0))
    draw = ImageDraw.Draw(stamp)
    rgb = tuple(int(config["color"][n:n + 2], 16) for n in (1, 3, 5))
    color = (*rgb, round(config["opacity"] * 255))
    # Position lines proportionally to page width, matching the approved 1A sample.
    for line, font, y in zip(lines, fonts, (width * 12 / 1055, width * 57 / 1055)):
        draw.text((stamp.width / 2, y * ss), line, font=font, fill=color, anchor="mt")
    stamp = stamp.crop(stamp.getbbox()).rotate(config["angle"], resample=Image.Resampling.BICUBIC, expand=True)
    stamp = stamp.resize((max(1, round(stamp.width / ss)), max(1, round(stamp.height / ss))), Image.Resampling.LANCZOS)
    stamp = stamp.crop(stamp.getbbox())
    stamp.thumbnail((max(1, round(width * 0.9)), max(1, round(height * 0.9))), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    layer.alpha_composite(stamp, ((width - stamp.width) // 2, (height - stamp.height) // 2))
    return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")


def process_pdf(source: Path, destination: Path, config: dict, font: Path) -> dict:
    if source.resolve() == destination.resolve() or destination.exists():
        raise ValueError("拒绝覆盖已有文件。")
    temporary = None
    pages = []
    try:
        with fitz.open(source) as original, fitz.open() as output:
            if original.needs_pass:
                raise ValueError("文件有打开密码，请先解密副本后再处理。")
            if not original.page_count:
                raise ValueError("PDF 没有页面。")
            for number, page in enumerate(original, 1):
                image, method = page_image(page, config["raster_dpi"])
                image = apply_watermark(image, config, font)
                data = BytesIO()
                image.save(data, format="PNG", optimize=True)
                target = output.new_page(width=page.rect.width, height=page.rect.height)
                target.insert_image(target.rect, stream=data.getvalue(), keep_proportion=False)
                pages.append({"page": number, "pixels": list(image.size), "method": method})
                print(f"    第 {number}/{original.page_count} 页 · {method} · {image.width}×{image.height}", flush=True)
                image.close()
            metadata = {key: value for key, value in original.metadata.items() if key in {
                "title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate", "trapped"
            }}
            metadata["title"] = (metadata.get("title") or source.stem) + "（已加水印）"
            output.set_metadata(metadata)
            toc = original.get_toc()
            if toc:
                output.set_toc(toc)
            handle, name = tempfile.mkstemp(prefix=".watermark-", suffix=".pdf", dir=destination.parent)
            os.close(handle)
            temporary = Path(name)
            output.save(temporary, garbage=4, deflate=True)
        with fitz.open(temporary) as verified:
            if verified.page_count != len(pages):
                raise ValueError("输出页数验证失败。")
        if destination.exists():
            raise ValueError("输出路径已存在，拒绝覆盖。")
        temporary.rename(destination)
        temporary = None
        return {"input": str(source), "output": str(destination), "status": "ok", "pages": pages}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_batch(sources: list[Path], parent: Path, config: dict, font: Path) -> tuple[Path, list[dict]]:
    folder = make_output_dir(parent)
    records = []
    for index, source in enumerate(sources, 1):
        print(f"\n[{index}/{len(sources)}] {source.name}", flush=True)
        try:
            record = process_pdf(source, destination_for(source, folder), config, font)
            print("  完成。", flush=True)
        except Exception as exc:
            record = {"input": str(source), "status": "error", "error": str(exc)}
            print(f"  失败：{exc}", flush=True)
        records.append(record)
        # Keep a report even if a later PDF fails.
        (folder / "处理记录.json").write_text(
            json.dumps({"settings": config, "files": records}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return folder, records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给 PDF 添加一个浅色中央水印；不覆盖原文件。无参数运行时弹出选择窗口。")
    parser.add_argument("inputs", nargs="*", type=Path, help="一个或多个 PDF 路径，或文件夹路径（只处理当前层级）")
    parser.add_argument("--output", type=Path, help="输出父文件夹；每次都会在其中新建独立结果文件夹")
    parser.add_argument("--config", type=Path, default=HERE / "watermark_config.json", help="水印配置 JSON 路径")
    args = parser.parse_args(argv)
    try:
        config = load_settings(args.config)
        font = find_font(config)
        inputs = args.inputs or select_inputs()
        if not inputs:
            print("已取消，没有处理文件。")
            return 0
        sources = collect_pdfs(inputs)
        if not sources:
            raise ValueError("所选文件夹当前层级没有 PDF。")
        parent = (args.output or sources[0].parent).expanduser().resolve()
        print("水印：" + config["primary_line"] + "｜" + config["secondary_line"])
        print(f"不透明度：{config['opacity']:.0%}；倾斜：{config['angle']}°；每页中央一个。")
        print("原文件保留。请勿把已经加水印的 PDF 再次加入处理。", flush=True)
        folder, records = run_batch(sources, parent, config, font)
        successes = sum(r["status"] == "ok" for r in records)
        print(f"\n完成：成功 {successes} 份，失败 {len(records) - successes} 份。")
        print(f"输出文件夹：{folder}")
        return 0 if successes == len(records) else 1
    except Exception as exc:
        print(f"\n无法处理：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
