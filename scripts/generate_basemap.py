#!/usr/bin/env python3
"""
Generator Basemap Dunia EPSG:3857 (Web Mercator) 4096x4096 piksel
Tema: Abu-abu Monokrom Minimalis
Sumber data: Natural Earth 50m GeoJSON (domain publik)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw

# Batas lintang maksimum proyeksi Web Mercator (EPSG:3857 / Slippy Map XYZ)
MAX_LAT = 85.05112877980659

NATURAL_EARTH_BASE = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
)
DATASETS = {
    "countries": f"{NATURAL_EARTH_BASE}/ne_50m_admin_0_countries.geojson",
    "coastline": f"{NATURAL_EARTH_BASE}/ne_50m_coastline.geojson",
    "lakes": f"{NATURAL_EARTH_BASE}/ne_50m_lakes.geojson",
    "rivers": f"{NATURAL_EARTH_BASE}/ne_50m_rivers_lake_centerlines.geojson",
}

# Palet Abu-abu Monokrom Minimalis (netral, presisi kartografi)
PALETTE = {
    "ocean": "#cfd4d9",
    "graticule": "#c0c6cc",
    "graticule_major": "#adb4bc",
    "coast_halo": "#b4bac2",
    "land_tones": ("#f3f4f5", "#eceeef", "#e6e8eb"),
    "border": "#9aa1a9",
    "coastline": "#4e555e",
    "river": "#b8bfc7",
    "lake_fill": "#cfd4d9",
    "lake_stroke": "#7b838c",
}


def project_mercator(lon: float, lat: float, size: int) -> tuple[float, float]:
    """Proyeksikan koordinat geografis (lon, lat) derajat ke piksel EPSG:3857."""
    x = (lon + 180.0) / 360.0 * size
    lat_clamped = max(-MAX_LAT, min(MAX_LAT, lat))
    lat_rad = math.radians(lat_clamped)
    y = (
        (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
        / 2.0
        * size
    )
    return (x, y)


def download_dataset(name: str, url: str, cache_dir: Path, required: bool = True) -> Path | None:
    """Unduh dataset GeoJSON Natural Earth 50m dan simpan di folder cache lokal."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / Path(url).name
    if target.exists() and target.stat().st_size > 0:
        print(f"[cache] Menggunakan {target.name}")
        return target

    print(f"[unduh] Mengambil {name}: {url}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SIG-Static-XYZ-Tile-Generator/1.0 (NaturalEarth50m)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        target.write_bytes(data)
        print(f"[ok]    Tersimpan {target.name} ({len(data) / 1024:.1f} KB)")
        return target
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if required:
            raise RuntimeError(f"Gagal mengunduh dataset wajib '{name}': {exc}") from exc
        print(f"[lewat] Dataset opsional '{name}' gagal diunduh: {exc}", file=sys.stderr)
        return None


def iter_polygons(geometry: dict) -> Iterable[list[list[list[float]]]]:
    """Kembalikan iterable daftar ring untuk Polygon dan MultiPolygon."""
    if not geometry:
        return
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        yield coords
    elif gtype == "MultiPolygon":
        for poly in coords:
            yield poly


def iter_lines(geometry: dict) -> Iterable[list[list[float]]]:
    """Kembalikan iterable daftar koordinat untuk LineString dan MultiLineString."""
    if not geometry:
        return
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "LineString":
        yield coords
    elif gtype == "MultiLineString":
        for line in coords:
            yield line


def pick_land_tone(properties: dict) -> str:
    """Pilih gradasi abu-abu monokrom deterministik untuk membedakan negara bertetangga."""
    tones = PALETTE["land_tones"]
    mapcolor = properties.get("MAPCOLOR7")
    if isinstance(mapcolor, int) and mapcolor > 0:
        return tones[(mapcolor - 1) % len(tones)]
    key = str(properties.get("ADM0_A3") or properties.get("NAME") or "default")
    digest = hashlib.md5(key.encode("utf-8")).digest()[0]
    return tones[digest % len(tones)]


def draw_polygon_with_holes(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    rings: list[list[list[float]]],
    size: int,
    fill_color: str,
) -> None:
    """Gambar poligon GeoJSON (termasuk lubang/interior ring jika ada) secara akurat."""
    if not rings or len(rings[0]) < 3:
        return

    ext_pts = [project_mercator(pt[0], pt[1], size) for pt in rings[0]]

    if len(rings) == 1:
        draw.polygon(ext_pts, fill=fill_color)
        return

    # Poligon memiliki lubang (enklave/danau): gunakan mask lokal sesuai bounding box
    xs = [p[0] for p in ext_pts]
    ys = [p[1] for p in ext_pts]
    x0 = max(0, int(math.floor(min(xs))))
    y0 = max(0, int(math.floor(min(ys))))
    x1 = min(size, int(math.ceil(max(xs))) + 1)
    y1 = min(size, int(math.ceil(max(ys))) + 1)
    bw = x1 - x0
    bh = y1 - y0
    if bw <= 0 or bh <= 0:
        return

    mask = Image.new("L", (bw, bh), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.polygon([(p[0] - x0, p[1] - y0) for p in ext_pts], fill=255)
    for hole in rings[1:]:
        if len(hole) >= 3:
            hole_pts = [project_mercator(pt[0], pt[1], size) for pt in hole]
            mdraw.polygon([(p[0] - x0, p[1] - y0) for p in hole_pts], fill=0)

    canvas.paste(fill_color, (x0, y0), mask)


def draw_graticule(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    """Gambar garis lintang & bujur (graticule) setiap 30 derajat pada lautan."""
    thin = max(1, scale)
    major = max(2, int(round(1.4 * scale)))

    # Garis bujur (meridian) -150..150
    for lon in range(-150, 180, 30):
        x, _ = project_mercator(lon, 0, size)
        color = PALETTE["graticule_major"] if lon == 0 else PALETTE["graticule"]
        width = major if lon == 0 else thin
        draw.line([(x, 0), (x, size)], fill=color, width=width)

    # Garis lintang (paralel) -75..75
    for lat in (-75, -60, -30, 0, 30, 60, 75):
        _, y = project_mercator(0, lat, size)
        color = PALETTE["graticule_major"] if lat == 0 else PALETTE["graticule"]
        width = major if lat == 0 else thin
        draw.line([(0, y), (size, y)], fill=color, width=width)


def generate_basemap(
    output_path: Path,
    cache_dir: Path,
    size: int = 4096,
    supersample: int = 2,
) -> Path:
    """Render basemap Web Mercator EPSG:3857 ukuran `size x size` dengan tema abu-abu monokrom."""
    countries_path = download_dataset("countries", DATASETS["countries"], cache_dir, required=True)
    coastline_path = download_dataset("coastline", DATASETS["coastline"], cache_dir, required=False)
    lakes_path = download_dataset("lakes", DATASETS["lakes"], cache_dir, required=False)
    rivers_path = download_dataset("rivers", DATASETS["rivers"], cache_dir, required=False)

    assert countries_path is not None
    countries_data = json.loads(countries_path.read_text(encoding="utf-8"))
    countries_features = countries_data.get("features", [])

    render_size = size * supersample
    print(
        f"[render] Membuat kanvas {render_size}x{render_size} "
        f"(supersample {supersample}x -> target {size}x{size})..."
    )
    canvas = Image.new("RGB", (render_size, render_size), PALETTE["ocean"])
    draw = ImageDraw.Draw(canvas)

    # 1. Graticule pada perairan
    draw_graticule(draw, render_size, supersample)

    # 2. Halo tipis di sepanjang batas luar daratan (kedalaman kartografi monokrom)
    halo_width = max(2, int(round(2.4 * supersample)))
    for feature in countries_features:
        for rings in iter_polygons(feature.get("geometry")):
            if rings and len(rings[0]) >= 3:
                pts = [project_mercator(pt[0], pt[1], render_size) for pt in rings[0]]
                draw.line(pts, fill=PALETTE["coast_halo"], width=halo_width, joint="curve")

    # 3. Isi daratan (poligon negara dengan tonal abu-abu monokrom minimalis)
    for feature in countries_features:
        fill_color = pick_land_tone(feature.get("properties") or {})
        for rings in iter_polygons(feature.get("geometry")):
            draw_polygon_with_holes(canvas, draw, rings, render_size, fill_color)

    # 4. Sungai utama (opsional)
    if rivers_path and rivers_path.exists():
        rivers_data = json.loads(rivers_path.read_text(encoding="utf-8"))
        river_width = max(1, int(round(1.0 * supersample)))
        for feature in rivers_data.get("features", []):
            for line in iter_lines(feature.get("geometry")):
                if len(line) >= 2:
                    pts = [project_mercator(pt[0], pt[1], render_size) for pt in line]
                    draw.line(pts, fill=PALETTE["river"], width=river_width, joint="curve")

    # 5. Danau utama (opsional)
    if lakes_path and lakes_path.exists():
        lakes_data = json.loads(lakes_path.read_text(encoding="utf-8"))
        lake_stroke = max(1, int(round(1.0 * supersample)))
        for feature in lakes_data.get("features", []):
            for rings in iter_polygons(feature.get("geometry")):
                draw_polygon_with_holes(canvas, draw, rings, render_size, PALETTE["lake_fill"])
                for ring in rings:
                    if len(ring) >= 2:
                        pts = [project_mercator(pt[0], pt[1], render_size) for pt in ring]
                        draw.line(pts, fill=PALETTE["lake_stroke"], width=lake_stroke, joint="curve")

    # 6. Batas administratif negara
    border_width = max(1, int(round(1.0 * supersample)))
    for feature in countries_features:
        for rings in iter_polygons(feature.get("geometry")):
            for ring in rings:
                if len(ring) >= 2:
                    pts = [project_mercator(pt[0], pt[1], render_size) for pt in ring]
                    draw.line(pts, fill=PALETTE["border"], width=border_width, joint="curve")

    # 7. Garis pantai tegas (lebih gelap dari batas darat untuk keterbacaan pulau)
    coast_width = max(1, int(round(1.35 * supersample)))
    if coastline_path and coastline_path.exists():
        coast_data = json.loads(coastline_path.read_text(encoding="utf-8"))
        for feature in coast_data.get("features", []):
            for line in iter_lines(feature.get("geometry")):
                if len(line) >= 2:
                    pts = [project_mercator(pt[0], pt[1], render_size) for pt in line]
                    draw.line(pts, fill=PALETTE["coastline"], width=coast_width, joint="curve")

    # 8. Downsample dengan Lanczos untuk anti-aliasing halus
    if supersample > 1:
        print(f"[resize] Downsampling LANCZOS ke {size}x{size}...")
        final_img = canvas.resize((size, size), Image.Resampling.LANCZOS)
    else:
        final_img = canvas

    output_path.parent.mkdir(parents=True, exist_ok=True)
    final_img.save(output_path, format="PNG", optimize=True)
    size_kb = output_path.stat().st_size / 1024
    print(f"[selesai] Basemap tersimpan di {output_path} ({size}x{size}, {size_kb:.1f} KB)")
    return output_path


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Generate basemap dunia Web Mercator (EPSG:3857) 4096x4096 dari Natural Earth 50m."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / ".cache" / "basemap_4096.png",
        help="Path file output PNG basemap (default: .cache/basemap_4096.png)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=project_root / ".cache" / "natural_earth",
        help="Folder cache untuk file GeoJSON Natural Earth 50m",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=4096,
        help="Ukuran sisi gambar basemap dalam piksel (default: 4096)",
    )
    parser.add_argument(
        "--supersample",
        type=int,
        default=2,
        choices=(1, 2),
        help="Faktor supersampling anti-aliasing (1 atau 2, default: 2)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    generate_basemap(
        output_path=args.output,
        cache_dir=args.cache_dir,
        size=args.size,
        supersample=args.supersample,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
