#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
from collections import defaultdict
from io import BytesIO

import requests
from PIL import Image

# ==========================
# CONFIGURACIÓN
# ==========================

START = {
    "tlx": 622,
    "tly": 1225,
    "pxx": 74,
    "pxy": 977,
}

END = {
    "tlx": 622,
    "tly": 1226,
    "pxx": 601,
    "pxy": 138,
}

BASE_PIXEL_URL = "https://backend.wplace.live/s0/pixel/{tlx}/{tly}?x={px}&y={py}"
BASE_TILE_URL = "https://backend.wplace.live/files/s0/tiles/{tlx}/{tly}.png"

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

RECT_IMAGE_PATH = os.path.join(OUTPUT_DIR, "rect.png")
DATA_JSON_PATH = os.path.join(OUTPUT_DIR, "data.json")
HTML_PATH = os.path.join(OUTPUT_DIR, "index.html")


# ==========================
# COORDENADAS
# ==========================

def world_coords(tlx, tly, pxx, pxy):
    return tlx * 1000 + pxx, tly * 1000 + pxy


def rect_bounds(start, end):
    wx0, wy0 = world_coords(start["tlx"], start["tly"], start["pxx"], start["pxy"])
    wx1, wy1 = world_coords(end["tlx"], end["tly"], end["pxx"], end["pxy"])
    return wx0, wy0, wx1, wy1


def pixel_iterator(start, end):
    wx0, wy0, wx1, wy1 = rect_bounds(start, end)

    for wy in range(wy0, wy1 + 1):
        tly = wy // 1000
        pxy = wy % 1000

        for wx in range(wx0, wx1 + 1):
            tlx = wx // 1000
            pxx = wx % 1000

            yield tlx, tly, pxx, pxy, wx, wy


# ==========================
# FETCH
# ==========================

def fetch_pixel(tlx, tly, pxx, pxy):
    url = BASE_PIXEL_URL.format(tlx=tlx, tly=tly, px=pxx, py=pxy)
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_tile(tlx, tly):
    url = BASE_TILE_URL.format(tlx=tlx, tly=tly)
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")


# ==========================
# IMAGEN DEL RECTÁNGULO
# ==========================

def build_rect_image(start, end, save_path):
    wx0, wy0, wx1, wy1 = rect_bounds(start, end)
    width = wx1 - wx0 + 1
    height = wy1 - wy0 + 1

    canvas = Image.new("RGBA", (width, height))

    tlx_min = wx0 // 1000
    tlx_max = wx1 // 1000
    tly_min = wy0 // 1000
    tly_max = wy1 // 1000

    for tly in range(tly_min, tly_max + 1):
        for tlx in range(tlx_min, tlx_max + 1):

            try:
                tile = fetch_tile(tlx, tly)
            except:
                continue

            tile_wx0 = tlx * 1000
            tile_wy0 = tly * 1000

            inter_x0 = max(wx0, tile_wx0)
            inter_y0 = max(wy0, tile_wy0)
            inter_x1 = min(wx1, tile_wx0 + 999)
            inter_y1 = min(wy1, tile_wy0 + 999)

            if inter_x0 > inter_x1 or inter_y0 > inter_y1:
                continue

            crop_x0 = inter_x0 - tile_wx0
            crop_y0 = inter_y0 - tile_wy0
            crop_x1 = inter_x1 - tile_wx0 + 1
            crop_y1 = inter_y1 - tile_wy0 + 1

            tile_crop = tile.crop((crop_x0, crop_y0, crop_x1, crop_y1))

            paste_x = inter_x0 - wx0
            paste_y = inter_y0 - wy0

            canvas.paste(tile_crop, (paste_x, paste_y))

    canvas.save(save_path)
    print("[OK] rect.png generado")


# ==========================
# RECOLECCIÓN DE DATOS
# ==========================

def collect_data(start, end):
    painter_counts = defaultdict(int)
    pixel_map = defaultdict(set)

    wx0, wy0, wx1, wy1 = rect_bounds(start, end)
    total = (wx1 - wx0 + 1) * (wy1 - wy0 + 1)
    processed = 0

    for tlx, tly, pxx, pxy, wx, wy in pixel_iterator(start, end):
        processed += 1
        if processed % 1000 == 0:
            print(f"[INFO] {processed}/{total}")

        try:
            data = fetch_pixel(tlx, tly, pxx, pxy)
        except:
            continue

        pb = data.get("paintedBy", {})
        name = pb.get("name")
        pid = pb.get("id")

        if not name or not pid:
            continue

        key = f"{name}#{pid}"
        painter_counts[key] += 1

        rx = wx - wx0
        ry = wy - wy0
        pixel_map[(rx, ry)].add(key)

    return painter_counts, pixel_map


# ==========================
# EXPORT JSON
# ==========================

def export_json(start, end, painter_counts, pixel_map, path):
    wx0, wy0, wx1, wy1 = rect_bounds(start, end)

    data = {
        "rect": {
            "start": start,
            "end": end,
            "world": {"wx0": wx0, "wy0": wy0, "wx1": wx1, "wy1": wy1},
            "width": wx1 - wx0 + 1,
            "height": wy1 - wy0 + 1,
        },
        "painterCounts": [
            {"key": k, "count": c}
            for k, c in sorted(painter_counts.items(), key=lambda x: x[1], reverse=True)
        ],
        "pixels": [
            {"x": x, "y": y, "painters": list(p)}
            for (x, y), p in pixel_map.items()
        ],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    print("[OK] data.json generado")


# ==========================
# EXPORT HTML
# ==========================

def export_html(path):
    html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Heatmap</title>
<style>
body { font-family: sans-serif; display: flex; gap: 20px; }
#container { position: relative; }
#overlay { position: absolute; top:0; left:0; pointer-events:none; }
#painter-list { max-height: 90vh; overflow-y:auto; list-style:none; padding:0; }
.painter-item { cursor:pointer; padding:3px; }
.painter-item.selected { background:#007acc; color:white; }
</style>
</head>
<body>

<div id="container">
  <img id="base" src="rect.png">
  <canvas id="overlay"></canvas>
</div>

<ul id="painter-list"></ul>

<script>
let data = null;
let selected = new Set();
let ctx = null;

fetch("data.json").then(r=>r.json()).then(d=>{
    data = d;
    init();
});

function init(){
    const img = document.getElementById("base");
    const canvas = document.getElementById("overlay");
    ctx = canvas.getContext("2d");

    img.onload = ()=>{
        canvas.width = img.width;
        canvas.height = img.height;
        renderList();
        draw();
    };
}

function renderList(){
    const ul = document.getElementById("painter-list");
    data.painterCounts.forEach(p=>{
        const li = document.createElement("li");
        li.textContent = p.key + " (" + p.count + ")";
        li.className = "painter-item";
        li.onclick = ()=>{
            if(selected.has(p.key)){
                selected.delete(p.key);
                li.classList.remove("selected");
            } else {
                selected.add(p.key);
                li.classList.add("selected");
            }
            draw();
        };
        ul.appendChild(li);
    });
}

function draw(){
    ctx.clearRect(0,0,ctx.canvas.width,ctx.canvas.height);

    if(selected.size === 0) return;

    ctx.fillStyle = "rgba(0,0,0,0.5)";
    ctx.fillRect(0,0,ctx.canvas.width,ctx.canvas.height);

    ctx.fillStyle = "rgba(255,0,0,0.9)";
    data.pixels.forEach(p=>{
        for(const k of p.painters){
            if(selected.has(k)){
                ctx.fillRect(p.x, p.y, 1, 1);
                break;
            }
        }
    });
}
</script>

</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    print("[OK] index.html generado")


# ==========================
# MAIN
# ==========================

def main():
    print("[1] Generando imagen del rectángulo…")
    build_rect_image(START, END, RECT_IMAGE_PATH)

    print("[2] Recolectando datos…")
    painter_counts, pixel_map = collect_data(START, END)

    print("[3] Exportando JSON…")
    export_json(START, END, painter_counts, pixel_map, DATA_JSON_PATH)

    print("[4] Exportando HTML…")
    export_html(HTML_PATH)

    print("\nListo. Abre output/index.html en tu navegador.")


if __name__ == "__main__":
    main()