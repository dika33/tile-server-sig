#!/usr/bin/env python3
"""
Pemotong Tile XYZ Statis ({z}/{x}/{y}.png) untuk Static Map Tile Server.
Membaca basemap proyeksi Web Mercator (default: .cache/basemap_4096.png atau gambar custom via --input),
memotongnya menjadi tile 256x256 piksel dari Zoom 0 sampai Zoom 4 (total 341 tile PNG),
serta menghasilkan file tiles/manifest.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


def slice_tiles(
    input_path: Path,
    output_dir: Path,
    min_zoom: int = 0,
    max_zoom: int = 4,
    tile_size: int = 256,
    clean: bool = True,
) -> dict:
    """Potong gambar sumber menjadi piramida tile XYZ dan tulis manifest.json."""
    if not input_path.exists():
        raise FileNotFoundError(
            f"Gambar input tidak ditemukan: {input_path}. "
            "Jalankan scripts/generate_basemap.py terlebih dahulu atau tentukan --input."
        )

    if min_zoom < 0 or max_zoom < min_zoom:
        raise ValueError(f"Rentang zoom tidak valid: min={min_zoom}, max={max_zoom}")

    target_max_px = tile_size * (2 ** max_zoom)
    print(f"[baca]  Membuka gambar sumber: {input_path}")
    with Image.open(input_path) as raw:
        source_size = raw.size
        src = raw.convert("RGB")

    if src.size != (target_max_px, target_max_px):
        print(
            f"[skala] Menyesuaikan gambar {src.size[0]}x{src.size[1]} "
            f"-> {target_max_px}x{target_max_px} (LANCZOS)"
        )
        src = src.resize((target_max_px, target_max_px), Image.Resampling.LANCZOS)

    output_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        for z in range(min_zoom, max_zoom + 1):
            z_dir = output_dir / str(z)
            if z_dir.exists():
                shutil.rmtree(z_dir)

    levels_summary: list[dict] = []
    total_tiles = 0
    total_bytes = 0

    for z in range(min_zoom, max_zoom + 1):
        grid = 2 ** z
        world_px = tile_size * grid
        level_img = (
            src
            if world_px == src.size[0]
            else src.resize((world_px, world_px), Image.Resampling.LANCZOS)
        )

        level_tiles = 0
        level_bytes = 0

        for x in range(grid):
            col_dir = output_dir / str(z) / str(x)
            col_dir.mkdir(parents=True, exist_ok=True)
            left = x * tile_size
            right = left + tile_size

            for y in range(grid):
                top = y * tile_size
                bottom = top + tile_size
                tile = level_img.crop((left, top, right, bottom))
                tile_path = col_dir / f"{y}.png"
                tile.save(tile_path, format="PNG", optimize=True)

                file_size = tile_path.stat().st_size
                level_tiles += 1
                level_bytes += file_size

        total_tiles += level_tiles
        total_bytes += level_bytes
        levels_summary.append(
            {
                "z": z,
                "grid": f"{grid}x{grid}",
                "columns": grid,
                "rows": grid,
                "tile_count": level_tiles,
                "world_px": world_px,
                "bytes": level_bytes,
            }
        )
        print(
            f"[z={z}]   Grid {grid:2d}x{grid:<2d} -> {level_tiles:3d} tile PNG "
            f"({level_bytes / 1024:.1f} KB)"
        )

    manifest = {
        "name": "Static XYZ Map Tile Server - SIG",
        "theme": "abu-abu-monokrom-minimalis",
        "projection": "EPSG:3857",
        "scheme": "xyz",
        "format": "png",
        "tile_size": tile_size,
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
        "url_template": "./tiles/{z}/{x}/{y}.png",
        "source_image": input_path.name,
        "source_dimensions": [source_size[0], source_size[1]],
        "total_tiles": total_tiles,
        "total_bytes": total_bytes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "levels": levels_summary,
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"[selesai] Total {total_tiles} tile PNG ({total_bytes / 1024:.1f} KB) "
        f"dan manifest ditulis ke {manifest_path}"
    )
    return manifest


def verify_tiles(output_dir: Path, min_zoom: int = 0, max_zoom: int = 4, tile_size: int = 256) -> int:
    """Verifikasi integritas seluruh tile PNG (eksistensi, ukuran 256x256, tidak korup)."""
    checked = 0
    errors: list[str] = []

    for z in range(min_zoom, max_zoom + 1):
        grid = 2 ** z
        for x in range(grid):
            for y in range(grid):
                tile_path = output_dir / str(z) / str(x) / f"{y}.png"
                if not tile_path.exists():
                    errors.append(f"Hilang: {tile_path}")
                    continue
                if tile_path.stat().st_size == 0:
                    errors.append(f"Kosong (0 byte): {tile_path}")
                    continue
                try:
                    with Image.open(tile_path) as img:
                        img.verify()
                    with Image.open(tile_path) as img:
                        if img.size != (tile_size, tile_size):
                            errors.append(
                                f"Dimensi salah {img.size} (harus {tile_size}x{tile_size}): {tile_path}"
                            )
                        if img.format != "PNG":
                            errors.append(f"Format bukan PNG ({img.format}): {tile_path}")
                except Exception as exc:
                    errors.append(f"Korup ({exc}): {tile_path}")
                checked += 1

    if errors:
        print(f"[GAGAL] Ditemukan {len(errors)} masalah integritas tile:")
        for msg in errors[:20]:
            print(f"  - {msg}")
        raise SystemExit(1)

    print(f"[valid] Seluruh {checked} tile PNG terverifikasi utuh ({tile_size}x{tile_size} PNG).")
    return checked


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Potong basemap menjadi tile XYZ 256x256 (Zoom 0..4) dan buat tiles/manifest.json."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=project_root / ".cache" / "basemap_4096.png",
        help="Path gambar basemap input (default: .cache/basemap_4096.png)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "tiles",
        help="Folder output tile XYZ (default: tiles/)",
    )
    parser.add_argument(
        "--min-zoom",
        type=int,
        default=0,
        help="Level zoom minimum (default: 0)",
    )
    parser.add_argument(
        "--max-zoom",
        type=int,
        default=4,
        help="Level zoom maksimum (default: 4)",
    )
    parser.add_argument(
        "--tile-size",
        type=int,
        default=256,
        help="Ukuran sisi tile dalam piksel (default: 256)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Lewati tahap verifikasi integritas PNG setelah pemotongan",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    slice_tiles(
        input_path=args.input,
        output_dir=args.output,
        min_zoom=args.min_zoom,
        max_zoom=args.max_zoom,
        tile_size=args.tile_size,
    )
    if not args.no_verify:
        verify_tiles(
            output_dir=args.output,
            min_zoom=args.min_zoom,
            max_zoom=args.max_zoom,
            tile_size=args.tile_size,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
