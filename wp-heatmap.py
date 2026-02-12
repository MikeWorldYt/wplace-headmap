#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import random
from collections import defaultdict
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Cookie": "_cfuvid=6ZtIVEk1rfvIqE50VL8t4YY6rzq1ADjkTAjWfbq7Ezo-1770920159.0875788-1.0.1.1-.FG_XDpXu69mv9RPxtOn_uzg5dTEq8dGjSSSlOIyRBU"
}

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
CHUNKS_DIR = os.path.join(OUTPUT_DIR, "chunks")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CHUNKS_DIR, exist_ok=True)

RECT_IMAGE_PATH = os.path.join(OUTPUT_DIR, "rect.png")
DATA_JSON_PATH = os.path.join(OUTPUT_DIR, "data.json")
HTML_PATH = os.path.join(OUTPUT_DIR, "index.html")

BLOCK_SIZE = 10
MAX_WORKERS = 8
MIN_WORKERS = 1

# ==========================
# COORDENADAS
# ==========================

def world_coords(tlx, tly, pxx, pxy):
    return tlx * 1000 + pxx, tly * 1000 + pxy


def rect_bounds(start, end):
    wx0, wy0 = world_coords(start["tlx"], start["tly"], start["pxx"], start["pxy"])
    wx1, wy1 = world_coords(end["tlx"], end["tly"], end["pxx"], end["pxy"])
    return wx0, wy0, wx1, wy1


# ==========================
# FETCH
# ==========================

def fetch_pixel(tlx, tly, pxx, pxy):
    url = BASE_PIXEL_URL.format(tlx=tlx, tly=tly, px=pxx, py=pxy)
    time.sleep(0.15 + random.random() * 0.25)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)

        if r.status_code == 404:
            print(f"[404] {tlx},{tly} px {pxx},{pxy}")
            return "404"
        if r.status_code == 429:
            print(f"[429] RATE LIMIT en {tlx},{tly} px {pxx},{pxy}")
            return "RATE_LIMIT"
        if r.status_code >= 500:
            print(f"[{r.status_code}] SERVER ERROR en {tlx},{tly} px {pxx},{pxy}")
            return "SERVER_ERROR"
        r.raise_for_status()
        print(f"[OK] {tlx},{tly} px {pxx},{pxy}")
        return r.json()

    except requests.exceptions.Timeout:
        print(f"[TIMEOUT] {tlx},{tly} px {pxx},{pxy}")
        return "TIMEOUT"

    except Exception as e:
        print(f"[ERROR] {tlx},{tly} px {pxx},{pxy} -> {e}")
        return "ERROR"

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
# BLOQUES / CHUNKS
# ==========================

def chunk_filename(bx, by):
    return os.path.join(CHUNKS_DIR, f"chunk_{bx}_{by}.json")


def partial_filename(bx, by):
    return os.path.join(CHUNKS_DIR, f"chunk_{bx}_{by}.partial.json")


def block_already_done(bx, by):
    return os.path.exists(chunk_filename(bx, by))


def process_block(bx, by, wx0, wy0, wx1, wy1, throttle):
    final_file = chunk_filename(bx, by)
    partial_file = partial_filename(bx, by)
    time.sleep(0.5 + random.random() * 1.0)

    if os.path.exists(final_file):
        print(f"[SKIP] bloque {bx},{by} ya completado")
        return final_file

    # Cargar progreso parcial
    partial_data = []
    if os.path.exists(partial_file):
        try:
            with open(partial_file, "r", encoding="utf-8") as f:
                partial_data = json.load(f)
            print(f"[RESUME] bloque {bx},{by} retomando desde fila {len(partial_data)}")
        except:
            partial_data = []

    rows_done = len(partial_data)
    retry_count = 0

    for row_index in range(rows_done, BLOCK_SIZE):
        wy = by + row_index
        if wy > wy1:
            break

        row_pixels = []

        print(f"[ROW] bloque {bx},{by} procesando fila {row_index}")

        for col in range(BLOCK_SIZE):
            wx = bx + col
            if wx > wx1:
                break

            tlx = wx // 1000
            tly = wy // 1000
            pxx = wx % 1000
            pxy = wy % 1000

            data = fetch_pixel(tlx, tly, pxx, pxy)

            if data in ("RATE_LIMIT", "SERVER_ERROR", "TIMEOUT", "ERROR"):
                print(f"[RETRY] bloque {bx},{by} fila {row_index} por {data}")
                retry_count += 1
                if retry_count >= 5:
                    print(f"[FAILED] bloque {bx},{by} marcado como fallido")
                    return "FAILED"
                time.sleep(2)
                return "RETRY"

            if data == "404":
                continue

            pb = data.get("paintedBy") or {}
            name = pb.get("name")
            pid = pb.get("id")

            if name and pid:
                key = f"{name}#{pid}"
                rx = wx - wx0
                ry = wy - wy0
                row_pixels.append({"x": rx, "y": ry, "painters": [key]})

        partial_data.append(row_pixels)
        with open(partial_file, "w", encoding="utf-8") as f:
            json.dump(partial_data, f, ensure_ascii=False)

        print(f"[SAVE] bloque {bx},{by} fila {row_index} guardada")

    # Convertir a final
    painter_counts = defaultdict(int)
    pixel_map = []

    for row in partial_data:
        for p in row:
            for key in p["painters"]:
                painter_counts[key] += 1
            pixel_map.append(p)

    final_data = {
        "painterCounts": dict(painter_counts),
        "pixels": pixel_map,
    }

    with open(final_file, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False)

    if os.path.exists(partial_file):
        os.remove(partial_file)

    print(f"[DONE] bloque {bx},{by} completado")
    return final_file

def collect_data_parallel(start, end):
    wx0, wy0, wx1, wy1 = rect_bounds(start, end)

    blocks = []
    for by in range(wy0, wy1 + 1, BLOCK_SIZE):
        for bx in range(wx0, wx1 + 1, BLOCK_SIZE):
            blocks.append((bx, by))

    print(f"[INFO] Bloques totales: {len(blocks)}")

    throttle = {"requests": 0, "errors": 0, "error_rate": 0.0}

    for bx, by in blocks:
        print(f"[INFO] Procesando bloque {bx},{by}")

        while True:
            result = process_block(bx, by, wx0, wy0, wx1, wy1, throttle)
            if result != "RETRY":
                break

    # Fusionar chunks
    painter_counts = defaultdict(int)
    pixel_map = defaultdict(set)

    for fname in os.listdir(CHUNKS_DIR):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(CHUNKS_DIR, fname), "r", encoding="utf-8") as f:
            cdata = json.load(f)

        for k, c in cdata["painterCounts"].items():
            painter_counts[k] += c

        for p in cdata["pixels"]:
            pixel_map[(p["x"], p["y"])].add(p["painters"][0])

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
#overlay { position: absolute; top:0; left:0; pointer-events:none; image-rendering: pixelated; }
#base { image-rendering: pixelated; }
#painter-list { max-height: 90vh; overflow-y:auto; list-style:none; padding:0; margin:0; }
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
let canvas = null;

fetch("data.json").then(r=>r.json()).then(d=>{
    data = d;
    init();
});

function init(){
    const img = document.getElementById("base");
    canvas = document.getElementById("overlay");
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
    ctx.clearRect(0,0,canvas.width,canvas.height);
    if(selected.size === 0) return;

    ctx.fillStyle = "rgba(0,0,0,0.5)";
    ctx.fillRect(0,0,canvas.width,canvas.height);

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

    print("[2] Recolectando datos en bloques con auto‑throttle…")
    painter_counts, pixel_map = collect_data_parallel(START, END)

    print("[3] Exportando JSON…")
    export_json(START, END, painter_counts, pixel_map, DATA_JSON_PATH)

    print("[4] Exportando HTML…")
    export_html(HTML_PATH)

    print("\nListo. Abre output/index.html en tu navegador.")


if __name__ == "__main__":
    main()